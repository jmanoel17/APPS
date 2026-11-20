#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.6R - real-device read-only decision capture.

The Agent is only queried (GET_UI_TREE/CAPTURE_SCREEN/GET_FOREGROUND_APP).
QA actions that reach an app state are explicitly tagged [E36R][QA] and use raw
ADB only. No Agent TAP/BACK/HOME/CLOSE and no shadow decision is executed.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)

from device_agent.server import DeviceAgentServer
from device_agent.state import DeviceAgentStore
import decision_parity_replay as dp

DEV = "192.168.1.124:5555"
AGENT = "com.sln.deviceagent"
FLIX = "com.flixdigital.dramaclassic"
STORE = os.path.join(PROJ, "state", "device_agents", "e32b")
CERT = os.path.join(PROJ, "state", "device_agents", "tls", "device-controller-cert.pem")
KEY = os.path.join(PROJ, "state", "device_agents", "tls", "device-controller-key.pem")
REAL = os.path.join(PROJ, "tests", "fixtures", "e36_real")
BASE = os.path.join(PROJ, "tests", "fixtures", "e36")
OUT = os.path.join(PROJ, "state", "device_agents", "e36r_report.json")


def log(*a):
    print(" ".join(str(x) for x in a), flush=True)


async def adb(*a, timeout=30):
    p = await asyncio.create_subprocess_exec(
        "adb", *a, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
    return p.returncode, (out or b"").decode("utf-8", "replace")


def raw(*a, timeout=30):
    p = subprocess.run(["adb", "-s", DEV, *a], capture_output=True, timeout=timeout)
    return p.returncode, p.stdout


def adb_fg():
    rc, out = raw("shell", "dumpsys", "window")
    txt = out.decode("utf-8", "replace")
    for line in txt.splitlines():
        if "mCurrentFocus=" in line and "Window{" in line:
            m = re.search(r"u0 ([^/ ]+)/([^ }]+)", line)
            if m:
                return {"package": m.group(1), "activity": m.group(2),
                        "component": m.group(1) + "/" + m.group(2)}
    return {"package": "", "activity": "", "component": ""}


def raw_dump():
    raw("shell", "uiautomator", "dump", "/sdcard/e36r.xml", timeout=25)
    rc, xml = raw("exec-out", "cat", "/sdcard/e36r.xml", timeout=25)
    try:
        root = ET.fromstring(xml)
        nodes = [n.attrib for n in root.iter("node")]
    except Exception:
        nodes = []
    return xml, nodes


def adb_png():
    rc, b = raw("exec-out", "screencap", "-p", timeout=20)
    return b if rc == 0 else b""


def tree_hash(data):
    items = []
    for n in data.get("nodes") or []:
        items.append((n.get("class"), n.get("text"), n.get("content_desc"),
                      n.get("resource_id"), tuple(n.get("bounds") or []),
                      n.get("clickable"), n.get("visible")))
    return hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sha(b):
    return hashlib.sha256(b or b"").hexdigest()


def stats(v):
    if not v:
        return {"n": 0}
    v = sorted(v); n = len(v)
    def pct(p):
        return round(v[max(0, min(n - 1, int((p / 100.0) * n + 0.999) - 1))], 2)
    return {"n": n, "avg": round(sum(v) / n, 2), "p50": pct(50), "p95": pct(95),
            "p99": pct(99), "max": round(v[-1], 2)}


def classify(data, fg, meta, decision, previous_state):
    labels = set()
    state = decision.state
    nodes = data.get("nodes") or []
    classes = " ".join(str(n.get("class") or "") for n in nodes).lower()
    rids = " ".join(str(n.get("resource_id") or "") for n in nodes).lower()
    texts = " ".join(str(n.get("text") or "") for n in nodes).lower()
    if decision.candidate and decision.candidate.source == "consent":
        labels.add("consent")
    if state == "store":
        labels.add("store")
    if state == "main":
        labels.add("main")
        if decision.candidate and decision.candidate.source == "reward_button":
            labels.add("reward")
    if previous_state and previous_state != "main" and state == "main":
        labels.add("return_main")
    if state == "applovin":
        labels.add("applovin")
        if "webview" in classes:
            labels.add("webview")
        if any(x in rids for x in ("playable", "game-field", "game-controls")):
            labels.add("playable")
        if decision.endcard.get("confirmed"):
            labels.add("endcard")
        if decision.countdown.get("n", 0) > 0:
            labels.add("countdown")
        if decision.subad.get("found"):
            labels.add("sub_ad_close")
        if decision.candidate:
            src = decision.candidate.source
            if src == "endcard_structural":
                labels.add("structural_close")
            elif src == "anonymous_top_right":
                labels.add("anonymous_top_right")
            elif src in ("named", "legacy") or src.startswith("named_") or src.startswith("legacy_") or src.startswith("semi_close"):
                labels.add("close_candidate")
    return sorted(labels)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=600)
    args = ap.parse_args()
    os.makedirs(REAL, exist_ok=True)
    os.makedirs(os.path.join(REAL, "_screens"), exist_ok=True)
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 8766))
    except OSError:
        raise SystemExit("PORT_BUSY")
    finally:
        s.close()
    store = DeviceAgentStore(STORE)
    srv = DeviceAgentServer(store)
    await adb("connect", DEV)
    await srv.start_wss("127.0.0.1", 8766, CERT, KEY)
    await adb("-s", DEV, "reverse", "tcp:8766", "tcp:8766")
    await adb("-s", DEV, "shell", "am", "start", "-n", AGENT + "/.MainActivity")
    aid = None
    for _ in range(100):
        if srv.connections:
            aid = next(iter(srv.connections)); break
        await asyncio.sleep(.3)
    if not aid:
        raise SystemExit("AGENT_OFFLINE")
    await asyncio.sleep(1)
    async def cmd(op, params=None, timeout=20):
        return await srv.send_command(aid, op, params or {}, timeout=timeout)
    caps = (store.get_agent(aid) or type("X", (), {"capabilities": {}})()).capabilities
    acc = (await cmd("GET_ACCESSIBILITY_STATE", timeout=10)).get("data") or {}
    bridge = (await cmd("GET_PRIVILEGED_BRIDGE_STATUS", timeout=20)).get("data") or {}
    info = (await cmd("GET_DEVICE_INFO", timeout=10)).get("data") or {}
    status = (await cmd("GET_STATUS", timeout=10)).get("data") or {}
    display = info.get("display") or status.get("display") or {}
    preflight = {"agent_online": True, "agent_id": aid, "accessibility": acc.get("enabled"),
                 "tree_capability": caps.get("GET_UI_TREE"), "screenshot_capability": caps.get("CAPTURE_SCREEN"),
                 "shizuku_state": bridge.get("state"), "effective_uid": bridge.get("effective_uid"),
                 "display": display, "package": info.get("android_id"), "model": info.get("model")}
    log("[E36R][PREFLIGHT]", json.dumps(preflight, ensure_ascii=False))
    if not preflight["tree_capability"]:
        await adb("-s", DEV, "shell", 'settings put secure enabled_accessibility_services ""')
        await adb("-s", DEV, "shell", "settings put secure accessibility_enabled 0")
        await asyncio.sleep(1)
        await adb("-s", DEV, "shell", 'settings put secure enabled_accessibility_services "com.sln.deviceagent/com.sln.deviceagent.DeviceAccessibilityService"')
        await adb("-s", DEV, "shell", "settings put secure accessibility_enabled 1")
        await adb("-s", DEV, "shell", "am", "start", "-n", AGENT + "/.MainActivity")
        for _ in range(30):
            caps = (store.get_agent(aid) or type("X", (), {"capabilities": {}})()).capabilities
            if caps.get("GET_UI_TREE") and caps.get("CAPTURE_SCREEN"):
                break
            await asyncio.sleep(1)
        preflight["tree_capability"] = caps.get("GET_UI_TREE")
    if not preflight["tree_capability"] or not preflight["screenshot_capability"]:
        raise SystemExit("CAPABILITY_MISSING")
    if display.get("width_px") != 1080 or display.get("height_px") != 2280:
        log("[E36R][WARN] display differs", display)

    metrics = {"snapshots_total": 0, "trees_success": 0, "trees_failure": 0,
               "screenshots_total": 0, "shadow_decisions": 0,
               "shadow_actions_executed": 0, "qa_actions": 0,
               "fixtures_created": 0, "tree_ms": [], "decision_ms": [],
               "tree_bytes": [], "states": set(),
               "tree_hashes": 0, "foreground_transitions": [], "last_tree_hash": None,
               "last_fg": None, "last_state": None}
    lock = asyncio.Lock()
    seen_labels = set()
    start = time.time()
    last_shot = 0.0
    last_hash = None

    async def save_fixture(label, data, fg_agent, decision, elapsed, ctx=""):
        nonlocal last_shot
        safe = re.sub(r"[^a-z0-9_]+", "_", label.lower())
        d = os.path.join(REAL, safe)
        if os.path.isdir(d):
            return False
        async with lock:
            if os.path.isdir(d):
                return False
            os.makedirs(d, exist_ok=True)
            fg_adb = adb_fg()
            xml_bytes, adb_nodes = await asyncio.to_thread(raw_dump)
            apng = await asyncio.to_thread(adb_png)
            shot = await cmd("CAPTURE_SCREEN", {"max_payload_bytes": 3000000}, timeout=20)
            sd = shot.get("data") or {}
            try:
                import base64
                agent_png = base64.b64decode(sd["payload"]) if sd.get("payload") else b""
            except Exception:
                agent_png = b""
            meta = {"case": safe, "ad_type": "unknown", "armed": True,
                    "reward_completed": True, "elapsed": elapsed,
                    "foreground_adb": fg_adb, "foreground_agent": fg_agent,
                    "display": data.get("display") or display,
                    "agent_tree_ok": True, "agent_nodes": len(data.get("nodes") or []),
                    "agent_capture_ms": data.get("capture_ms"),
                    "source": "e36_real_capture", "qa_context": ctx}
            dec = dataclasses.asdict(decision)
            json.dump(data, open(os.path.join(d, "agent_tree.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            json.dump(data, open(os.path.join(d, "agent.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            open(os.path.join(d, "adb.xml"), "wb").write(xml_bytes)
            open(os.path.join(d, "agent.png"), "wb").write(agent_png)
            open(os.path.join(d, "agent_screen.png"), "wb").write(agent_png)
            open(os.path.join(d, "adb.png"), "wb").write(apng)
            json.dump(fg_adb, open(os.path.join(d, "foreground_adb.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            json.dump({"agent": fg_agent, "adb": fg_adb}, open(os.path.join(d, "foreground.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            # Reference decision from the ADB snapshot; no action is executed.
            try:
                adb_tree = dp.ADBTreeAdapter.load(os.path.join(d, "adb.xml"), meta)
                adb_dec = dataclasses.asdict(dp.decide(adb_tree, meta))
            except Exception as e:
                adb_dec = {"error": type(e).__name__}
            json.dump({"agent": dec, "adb_reference": adb_dec},
                      open(os.path.join(d, "decision.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            json.dump(meta, open(os.path.join(d, "metadata.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            metrics["fixtures_created"] += 1
            metrics["screenshots_total"] += 1
            log("[E36R][FIXTURE]", safe, "ctx=" + ctx, "nodes=" + str(meta["agent_nodes"]))
            last_shot = time.time()
            return True

    async def capture_loop():
        nonlocal last_shot, last_hash
        while time.time() - start < args.duration:
            t0 = time.perf_counter()
            r = await cmd("GET_UI_TREE", {"max_nodes": 3000, "max_payload_bytes": 2000000}, timeout=20)
            tree_ms = (time.perf_counter() - t0) * 1000
            data = r.get("data") or {}
            metrics["snapshots_total"] += 1
            if r.get("status") != "success":
                metrics["trees_failure"] += 1
                await asyncio.sleep(.5); continue
            metrics["trees_success"] += 1
            metrics["tree_ms"].append(round(tree_ms, 2))
            metrics["tree_bytes"].append(data.get("payload_bytes") or 0)
            fga = await cmd("GET_FOREGROUND_APP", timeout=10)
            fg = fga.get("data") or {}
            elapsed = round(time.time() - start, 1)
            meta = {"ad_type": "unknown", "armed": True, "reward_completed": True,
                    "elapsed": elapsed, "foreground_agent": fg,
                    "foreground_adb": adb_fg(), "display": data.get("display") or display}
            d0 = time.perf_counter()
            tree = dp.AgentTreeAdapter.from_data(data, meta)
            decision = dp.decide(tree, meta)
            metrics["decision_ms"].append(round((time.perf_counter() - d0) * 1000, 2))
            metrics["shadow_decisions"] += 1
            labels = classify(data, fg, meta, decision, metrics["last_state"])
            for label in labels:
                metrics["states"].add(label)
            fg_sig = (fg.get("package"), fg.get("activity"))
            if metrics["last_fg"] and fg_sig != metrics["last_fg"]:
                metrics["foreground_transitions"].append({"t": elapsed, "from": metrics["last_fg"], "to": fg_sig})
            metrics["last_fg"] = fg_sig
            h = tree_hash(data)
            changed = (h != last_hash or decision.state != metrics["last_state"])
            if changed:
                last_hash = h
                metrics["tree_hashes"] += 1
            # Screenshot only on significant change, capped by a 2s interval.
            if changed and time.time() - last_shot >= 2.0 and metrics["screenshots_total"] < 40:
                shot = await cmd("CAPTURE_SCREEN", {"max_payload_bytes": 3000000}, timeout=20)
                sd = shot.get("data") or {}
                metrics["screenshots_total"] += 1
                if sd.get("payload"):
                    try:
                        import base64
                        png = base64.b64decode(sd["payload"])
                        fn = os.path.join(REAL, "_screens", f"{int(time.time()*1000)}_state.png")
                        open(fn, "wb").write(png)
                    except Exception:
                        pass
                last_shot = time.time()
            # Save one fixture per newly observed semantic state.
            for label in labels:
                if label not in seen_labels:
                    if await save_fixture(label, data, fg, decision, elapsed, "observed"):
                        seen_labels.add(label)
                    else:
                        seen_labels.add(label)
            metrics["last_state"] = decision.state
            log(f"[E36R][SHADOW] state={decision.state} candidate={(decision.candidate.source if decision.candidate else '-')} "
                f"bounds={(decision.candidate.bounds if decision.candidate else '-')} authorized={decision.authorized} "
                f"would={decision.decision} tree_ms={tree_ms:.0f} decision_ms={metrics['decision_ms'][-1]:.0f}")
            await asyncio.sleep(max(0.05, 0.5 - (time.perf_counter() - t0)))

    async def qa_driver():
        # Raw ADB only. These are QA setup actions, never Agent/shadow actions.
        nonlocal last_shot
        await asyncio.sleep(3)
        for attempt in range(3):
            log(f"[E36R][QA] attempt={attempt+1} force-stop/relaunch {FLIX}")
            await adb("-s", DEV, "shell", "am", "force-stop", FLIX)
            await asyncio.sleep(1)
            await adb("-s", DEV, "shell", "monkey", "-p", FLIX, "-c", "android.intent.category.LAUNCHER", "1")
            await asyncio.sleep(3)
            rx = ry = None
            # Reward is tappable only after the SDK unlocks the button.
            # Never tap while it is disabled/not clickable/invisible.
            for _ in range(15):
                xml_bytes, nodes = await asyncio.to_thread(raw_dump)
                for n in nodes:
                    rid = n.get("resource-id") or ""
                    txt = (n.get("text") or "").upper()
                    if not (rid.endswith("btnRewardAds") or "ASSISTIR" in txt):
                        continue
                    if (n.get("clickable") == "true" and n.get("enabled", "true") != "false"
                            and n.get("visible-to-user", "true") != "false"):
                        b = list(map(int, re.findall(r"\d+", n.get("bounds") or "")))
                        if len(b) == 4:
                            rx, ry = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
                            break
                if rx is not None:
                    break
                await asyncio.sleep(1)
            if rx is None:
                log("[E36R][QA] reward not ready/clickable; not tapping")
                await asyncio.sleep(2); continue
            log(f"[E36R][QA] tap reward ready {rx},{ry}")
            await adb("-s", DEV, "shell", "input", "tap", str(rx), str(ry))
            metrics["qa_actions"] += 1
            await asyncio.sleep(5)
            # Let the capture loop observe video/close/countdown. If a Google Play CTA appears,
            # click it with ADB (QA) to reach Store; never click a close/X.
            for _ in range(4):
                await asyncio.sleep(4)
                f = adb_fg()
                if "vending" in f.get("package", "").lower() or "finsky" in f.get("package", "").lower():
                    break
                _, ns = await asyncio.to_thread(raw_dump)
                for n in ns:
                    txt = (n.get("text") or "").strip().lower()
                    if txt in ("google play", "install", "instalar", "download"):
                        b = list(map(int, re.findall(r"\d+", n.get("bounds") or "")))
                        if (len(b) == 4 and n.get("clickable") == "true"
                                and n.get("enabled", "true") != "false"
                                and n.get("visible-to-user", "true") != "false"):
                            px, py = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
                            log(f"[E36R][QA] tap store CTA {px},{py}")
                            await adb("-s", DEV, "shell", "input", "tap", str(px), str(py))
                            metrics["qa_actions"] += 1
                            await asyncio.sleep(4)
                            break
                break
            await adb("-s", DEV, "shell", "input", "keyevent", "3")
            await asyncio.sleep(2)
        log("[E36R][QA] done")

    try:
        await asyncio.gather(capture_loop(), qa_driver())
    finally:
        metrics["states"] = sorted(metrics["states"])
        metrics["tree_p50"] = stats(metrics["tree_ms"])
        metrics["decision_stats"] = stats(metrics["decision_ms"])
        metrics["payload_bytes"] = stats([x for x in metrics["tree_bytes"] if x])
        metrics["tree_hashes"] = metrics["tree_hashes"]
        metrics["duration_s"] = round(time.time() - start, 1)
        # Agent CPU/PSS via top (current PID) and meminfo.
        agent_pid = (await adb("-s", DEV, "shell", "pidof", AGENT))[1].strip()
        cpu = None
        try:
            if agent_pid:
                rc, out = raw("shell", "top", "-b", "-n", "1", "-p", agent_pid)
                for line in out.decode("utf-8", "replace").splitlines():
                    if line.strip().startswith(agent_pid + " "):
                        parts = line.split()
                        if len(parts) > 8:
                            cpu = float(parts[8]); break
        except Exception:
            pass
        pss = None
        try:
            rc, out = raw("shell", "dumpsys", "meminfo", AGENT)
            for line in out.decode("utf-8", "replace").splitlines():
                m = re.match(r"\s*TOTAL(?:\s+PSS:)?\s+(\d+)", line)
                if m:
                    pss = int(m.group(1)); break
        except Exception:
            pass
        metrics["agent_pid"] = agent_pid
        metrics["agent_cpu_pct"] = cpu
        metrics["agent_pss_kb"] = pss
        replay_out = os.path.join(PROJ, "state", "device_agents", "e36_parity.json")
        try:
            subprocess.run([sys.executable, os.path.join(PROJ, "decision_parity_replay.py"),
                            "--fixtures", BASE, "--fixtures", REAL, "--out", replay_out],
                           cwd=PROJ, timeout=180, check=False,
                           capture_output=True, text=True)
            replay = json.load(open(replay_out, encoding="utf-8"))
        except Exception as e:
            replay = {"error": type(e).__name__}
        report = {"phase": "E3.6R_REAL_PARITY", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "device": DEV, "preflight": preflight, "duration_s": metrics["duration_s"],
                  "metrics": metrics, "states_observed": metrics["states"],
                  "fixtures": sorted([d for d in os.listdir(REAL)
                                      if os.path.isdir(os.path.join(REAL, d)) and d != "_screens"]),
                  "shadow_actions_executed": 0, "qa_actions": metrics["qa_actions"],
                  "replay": {k: replay.get(k) for k in ("verdict", "metrics", "detectors",
                                                        "fixture_missing", "critical_fixture_missing",
                                                        "golden_cases")},
                  "coverage_pending": replay.get("fixture_missing", []),
                  "verdict": ("PARITY_PASS" if replay.get("verdict") == "PARITY_PASS"
                              and metrics["shadow_actions_executed"] == 0 else "PARITY_PARTIAL"),
                  "remaining_limitations": [
                      "Agent never executed a shadow action (counter 0).",
                      "Screenshots are captured only on significant changes/fixtures.",
                      "Absent real states are FIXTURE_NOT_OBSERVED, not FAIL.",
                      "uiautomator2 cannot coexist with Accessibility; raw ADB dump only."]}
        json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        log("[E36R][RESULT]", json.dumps({"verdict": report["verdict"],
                                          "fixtures": report["fixtures"],
                                          "states": report["states_observed"],
                                          "shadow_actions": 0,
                                          "replay_verdict": replay.get("verdict")}, ensure_ascii=False))
        await adb("-s", DEV, "shell", "am", "force-stop", AGENT)
        await adb("-s", DEV, "reverse", "--remove", "tcp:8766")
        await srv.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
