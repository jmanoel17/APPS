#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.6 - Decision Parity replay harness (ADB/uiautomator2 vs Device Agent).

Read-only: never clicks, never drives a device. Reuses the production pure
fast-close detectors from main_sln_v2.py for BOTH normalized snapshots.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import glob
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any

# main_sln_v2 requires argv[1] at import time but does not touch a device on import.
_argv = sys.argv
sys.argv = ["main_sln_v2.py", "e36-replay"]
import main_sln_v2 as bot  # noqa: E402
sys.argv = _argv

try:
    import numpy as np
    from PIL import Image
    import cv2
except Exception:  # pragma: no cover - optional for decision-only replay
    np = Image = cv2 = None


@dataclasses.dataclass
class NormalizedNode:
    node_id: str
    parent_id: str | None
    depth: int
    cls: str
    text: str
    content_desc: str
    resource_id: str
    bounds: tuple[int, int, int, int] | None
    clickable: bool
    enabled: bool
    visible: bool
    scrollable: bool


@dataclasses.dataclass
class NormalizedTree:
    source: str
    root: ET.Element
    nodes: list[NormalizedNode]
    width: int
    height: int
    package: str
    package_version: str = ""


def _bounds_tuple(s: Any):
    if isinstance(s, (list, tuple)) and len(s) == 4:
        try:
            b = tuple(int(v) for v in s)
        except Exception:
            return None
    else:
        nums = re.findall(r"-?\d+", str(s or ""))
        if len(nums) < 4:
            return None
        b = tuple(int(v) for v in nums[:4])
    if b[2] <= b[0] or b[3] <= b[1]:
        return None
    return b


def _bounds_str(b) -> str:
    if not b:
        return ""
    return f"[{b[0]},{b[1]}][{b[2]},{b[3]}]"


def _display_size(meta, data=None):
    d = (meta or {}).get("display") or (data or {}).get("display") or {}
    return int(d.get("width_px") or 1080), int(d.get("height_px") or 2280)


class ADBTreeAdapter:
    """uiautomator2 XML -> NormalizedTree (+ ElementTree accepted by main parsers)."""
    @staticmethod
    def load(xml_path: str, meta: dict | None = None) -> NormalizedTree:
        meta = meta or {}
        raw = open(xml_path, "rb").read()
        root = ET.fromstring(raw)
        pkg = ""
        nodes: list[NormalizedNode] = []

        def walk(node, parent_id, depth):
            nonlocal pkg
            a = node.attrib
            if a.get("package"):
                pkg = pkg or a.get("package")
            b = _bounds_tuple(a.get("bounds"))
            nid = f"adb-{len(nodes)}"
            nodes.append(NormalizedNode(
                nid, parent_id, depth, a.get("class", ""), a.get("text", ""),
                a.get("content-desc", ""), a.get("resource-id", ""), b,
                a.get("clickable", "") == "true", a.get("enabled", "true") != "false",
                a.get("visible-to-user", "true") != "false", a.get("scrollable", "") == "true"))
            for c in list(node):
                if c.tag == "node":
                    walk(c, nid, depth + 1)

        for n in list(root):
            if n.tag == "node":
                walk(n, None, 0)
        # Ensure package attrs used by the production parsers.
        if not pkg:
            pkg = (meta.get("foreground_adb") or meta.get("foreground_agent") or {}).get("package", "")
        for n in root.iter("node"):
            if not n.attrib.get("package"):
                n.attrib["package"] = pkg
        w, h = _display_size(meta)
        return NormalizedTree("adb", root, nodes, w, h, pkg)


class AgentTreeAdapter:
    """Device Agent GET_UI_TREE JSON -> NormalizedTree (+ ElementTree accepted by main parsers)."""
    @staticmethod
    def load(json_path: str, meta: dict | None = None) -> NormalizedTree:
        data = json.load(open(json_path, encoding="utf-8"))
        return AgentTreeAdapter.from_data(data, meta)

    @staticmethod
    def from_data(data: dict, meta: dict | None = None) -> NormalizedTree:
        meta = meta or {}
        pkg = str(data.get("package") or (meta.get("foreground_agent") or {}).get("package") or "")
        w, h = _display_size(meta, data)
        root = ET.Element("hierarchy")
        elems: dict[str, ET.Element] = {}
        nodes: list[NormalizedNode] = []
        for raw in data.get("nodes") or []:
            nid = str(raw.get("node_id") or f"agent-{len(nodes)}")
            pid = raw.get("parent_id")
            pid = None if pid is None else str(pid)
            cls = str(raw.get("class") or "")
            text = str(raw.get("text") or "")
            desc = str(raw.get("content_desc") or "")
            rid = str(raw.get("resource_id") or "")
            b = _bounds_tuple(raw.get("bounds"))
            node = NormalizedNode(
                nid, pid, int(raw.get("depth") or 0), cls, text, desc, rid, b,
                bool(raw.get("clickable")), bool(raw.get("enabled", True)),
                bool(raw.get("visible", True)), bool(raw.get("scrollable")))
            nodes.append(node)
            el = ET.Element("node", {
                "package": pkg, "class": cls, "text": text, "content-desc": desc,
                "resource-id": rid, "bounds": _bounds_str(b),
                "clickable": "true" if node.clickable else "false",
                "enabled": "true" if node.enabled else "false",
                "visible-to-user": "true" if node.visible else "false",
                "scrollable": "true" if node.scrollable else "false",
            })
            elems[nid] = el
        for node in nodes:
            el = elems[node.node_id]
            if node.parent_id and node.parent_id in elems:
                elems[node.parent_id].append(el)
            else:
                root.append(el)
        return NormalizedTree("agent", root, nodes, w, h, pkg,
                              str(data.get("agent_version") or ""))


@dataclasses.dataclass
class Candidate:
    source: str
    bounds: tuple[int, int, int, int]
    center: tuple[int, int]
    rid: str = ""
    text: str = ""
    desc: str = ""
    detail: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class DecisionResult:
    state: str
    candidate: Candidate | None
    counts: dict
    foreground: dict
    authorized: bool
    gate_reason: str
    decision: str
    reason: str
    endcard: dict
    subad: dict
    countdown: dict


def _fg(tree: NormalizedTree, meta: dict, side: str) -> dict:
    return ((meta or {}).get(f"foreground_{side}") or {}).copy()


def _find_reward(tree: NormalizedTree) -> Candidate | None:
    for n in tree.nodes:
        if n.resource_id.endswith("btnRewardAds") or "assistir an" in n.text.lower():
            if n.bounds:
                return Candidate("reward_button", n.bounds,
                                 ((n.bounds[0] + n.bounds[2]) // 2, (n.bounds[1] + n.bounds[3]) // 2),
                                 n.resource_id, n.text)
    return None


def _find_consent(tree: NormalizedTree) -> Candidate | None:
    for n in tree.nodes:
        low = (n.text + " " + n.content_desc).lower()
        if "yes, i agree" in low or "improve your ad experience" in low:
            if n.bounds:
                return Candidate("consent", n.bounds,
                                 ((n.bounds[0] + n.bounds[2]) // 2, (n.bounds[1] + n.bounds[3]) // 2),
                                 n.resource_id, n.text, n.content_desc)
    return None


def _extract_candidate(an, tree, w, h, endcard, subad):
    best = an.get("best")
    if best:
        b = _bounds_tuple(best.get("bounds"))
        if b:
            return Candidate("named_" + best.get("src", "?"), b,
                             (int(best.get("cx")), int(best.get("cy"))),
                             best.get("id", ""), best.get("text", ""), best.get("desc", ""), best)
    if endcard.get("confirmed"):
        ec = bot._fc_find_endcard_close_structural(an, w, h)
        if ec:
            b = ec["bounds"]
            return Candidate("endcard_structural", tuple(b), (int(ec["cx"]), int(ec["cy"])), detail=ec)
    if subad.get("found"):
        b = tuple(subad["bounds"])
        return Candidate("subad_close", b, tuple(subad["center"]), detail=subad)
    cb = bot._fc_find_close_btn(an, w, h)
    if cb and cb.get("onscreen"):
        b = tuple(cb["bounds"])
        return Candidate("semi_close_" + str(cb.get("rid")), b,
                         ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2), str(cb.get("rid") or ""),
                         str(cb.get("text") or ""), str(cb.get("desc") or ""), cb)
    old = bot._fc_listas_antigas(an, w, h)
    if old:
        b, src = old
        return Candidate("legacy_" + src, tuple(b), ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2), detail={"source": src})
    anon = bot._fc_recovery_anon_top_right(an, w, h)
    if anon:
        b = tuple(anon["bounds"])
        return Candidate("anonymous_top_right", b, (int(anon["cx"]), int(anon["cy"])), detail=anon)
    return None


def decide(tree: NormalizedTree, meta: dict) -> DecisionResult:
    meta = meta or {}
    fg = _fg(tree, meta, "adb" if tree.source == "adb" else "agent")
    try:
        an = bot._fc_analisar_dump(tree.root, tree.width, tree.height)
    except Exception as e:
        an = {"state": "error", "nodes": [], "cands": [], "best": None,
              "rejects": [], "error": type(e).__name__}
    state = an.get("state", "error")
    pkg = (fg.get("package") or tree.package or "").lower()
    act = (fg.get("activity") or "").lower()
    if any(k in pkg for k in ("vending", "finsky", "play.google")) or "vending" in act:
        state = "store"
    elif any(k in pkg for k in ("chrome", "browser")) or "launcher" in act:
        state = "external"

    endcard = bot._fc_detectar_endcard(an) if an.get("state") else {"confirmed": False, "signals": []}
    subad = bot._fc_detectar_subad_close(tree.root, tree.width, tree.height) if state == "applovin" else {"found": False}
    cand = _extract_candidate(an, tree, tree.width, tree.height, endcard, subad)
    reward = _find_reward(tree)
    consent = _find_consent(tree)
    countdown = bot._fc_detectar_countdown(an, tree.width, tree.height) if state == "applovin" else []

    ad_type = str(meta.get("ad_type") or "unknown").lower()
    elapsed = float(meta.get("elapsed") or 0)
    armed = bool(meta.get("armed", state == "applovin"))
    reward_done = bool(meta.get("reward_completed", True))
    min_watch = float(bot.cfg.get("applovin_rewarded_min_watch_seconds", 30))
    gate_reason = ""
    authorized = True
    if state == "applovin":
        if not armed:
            authorized, gate_reason = False, "not_armed"
        elif ad_type == "rewarded" and not reward_done and elapsed < min_watch:
            authorized, gate_reason = False, "min_watch_pending"
        elif (meta.get("reward_completion_required", False) and not reward_done
              and bool(bot.cfg.get("applovin_skip_to_endcard_enabled", False))):
            authorized, gate_reason = False, "reward_not_completed"
        else:
            gate_reason = "armed"

    decision, reason = "OBSERVE", "no_action"
    if consent:
        decision, reason = "CLICK", "consent"
        cand = consent
    elif state == "store" or state == "external":
        if bool(meta.get("return_allowed", True)):
            decision, reason = "BACK", "external_return"
        else:
            decision, reason = "OBSERVE", "external_return_blocked"
    elif state == "main":
        if bool(meta.get("session_after_ad", False)) or meta.get("stage") in ("after_ad", "closed"):
            decision, reason = "COMPLETE", "main_after_ad"
        elif reward:
            decision, reason = "CLICK", "reward_button"
            cand = reward
        else:
            decision, reason = "OBSERVE", "main_no_reward"
    elif state == "applovin":
        if cand:
            if authorized:
                decision, reason = "CLICK", "close_authorized"
            else:
                decision, reason = "OBSERVE", "close_gate_" + gate_reason
        elif countdown:
            decision, reason = "OBSERVE", "countdown_active"
        elif endcard.get("confirmed"):
            decision, reason = "OBSERVE", "endcard_no_candidate"
        else:
            decision, reason = "OBSERVE", "ad_no_candidate"
    elif meta.get("session_failed"):
        decision, reason = "FAIL", "session_failed"

    return DecisionResult(
        state=state, candidate=cand,
        counts={"nodes": len(tree.nodes), "clickable": sum(1 for n in tree.nodes if n.clickable),
                "candidates": len(an.get("cands") or [])},
        foreground=fg, authorized=authorized, gate_reason=gate_reason,
        decision=decision, reason=reason, endcard=endcard, subad=subad,
        countdown={"n": len(countdown), "values": [c.get("value") for c in countdown[:8]]})


def _candidate_match(a: Candidate | None, b: Candidate | None):
    if a is None and b is None:
        return True, True, None, None
    if a is None or b is None:
        return False, False, None, None
    iou = None
    if a.bounds and b.bounds:
        ax1, ay1, ax2, ay2 = a.bounds
        bx1, by1, bx2, by2 = b.bounds
        iw = max(0, min(ax2, bx2) - max(ax1, bx1))
        ih = max(0, min(ay2, by2) - max(ay1, by1))
        inter = iw * ih
        union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
        iou = round(inter / union, 3) if union > 0 else 0.0
    cd = round(((a.center[0] - b.center[0]) ** 2 + (a.center[1] - b.center[1]) ** 2) ** 0.5, 1)
    semantic = a.source == b.source
    near = cd <= 20 or (iou is not None and iou >= 0.75)
    return bool(semantic and near), semantic, cd, iou


def _blue_bar(png_path):
    if not png_path or cv2 is None or not os.path.exists(png_path):
        return None
    try:
        bgr = cv2.imread(png_path)
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([90, 70, 60]), np.array([145, 255, 255]))
        for y in range(int(h * 0.86), h - 20, 8):
            if int((mask[y:y + 14, :].mean(axis=0) > 40).sum()) / w > 0.15:
                return True
        return False
    except Exception:
        return None


def compare_screenshot(adb_png, agent_png):
    if not adb_png or not agent_png or Image is None:
        return None
    if not (os.path.exists(adb_png) and os.path.exists(agent_png)):
        return None
    try:
        im1 = Image.open(agent_png).convert("RGB")
        im2 = Image.open(adb_png).convert("RGB")
        a = np.array(im1)
        b = np.array(im2)
        out = {"dims_agent": list(im1.size), "dims_adb": list(im2.size),
               "dims_match": im1.size == im2.size, "diff": None,
               "blue_agent": _blue_bar(agent_png), "blue_adb": _blue_bar(adb_png)}
        if a.shape == b.shape:
            out["diff"] = round(float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))), 2)
        out["same_classification"] = (out["blue_agent"] == out["blue_adb"]
                                      if out["blue_agent"] is not None and out["blue_adb"] is not None else None)
        return out
    except Exception as e:
        return {"error": type(e).__name__}


def _pair_files(case_dir):
    if os.path.exists(os.path.join(case_dir, "adb.xml")):
        meta_path = os.path.join(case_dir, "meta.json")
        yield (os.path.join(case_dir, "adb.xml"), os.path.join(case_dir, "agent.json"),
               os.path.join(case_dir, "adb.png"), os.path.join(case_dir, "agent.png"), meta_path)
        return
    for adb in sorted(glob.glob(os.path.join(case_dir, "*_adb.xml"))):
        prefix = adb[:-8]
        yield (adb, prefix + "_agent.json", prefix + "_adb.png", prefix + "_agent.png",
               prefix + "_meta.json")


def replay(fixtures_root, out_json):
    roots = [fixtures_root] if isinstance(fixtures_root, str) else list(fixtures_root)
    cases = []
    for root in roots:
      for case_dir in sorted(d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d)):
        for adb_xml, agent_json, adb_png, agent_png, meta_path in _pair_files(case_dir):
            if not os.path.exists(agent_json):
                continue
            meta = json.load(open(meta_path, encoding="utf-8")) if os.path.exists(meta_path) else {}
            t0 = time.perf_counter()
            adb_tree = ADBTreeAdapter.load(adb_xml, meta)
            t1 = time.perf_counter()
            agent_tree = AgentTreeAdapter.load(agent_json, meta)
            t2 = time.perf_counter()
            a = decide(adb_tree, meta)
            t3 = time.perf_counter()
            b = decide(agent_tree, meta)
            t4 = time.perf_counter()
            cm, sem, cd, iou = _candidate_match(a.candidate, b.candidate)
            fg_a = a.foreground
            fg_b = b.foreground
            pkg_match = bool(fg_a.get("package") and fg_a.get("package") == fg_b.get("package"))
            act_match = bool(fg_a.get("activity") and fg_a.get("activity") == fg_b.get("activity"))
            comp_match = bool(fg_a.get("component") and fg_a.get("component") == fg_b.get("component"))
            state_match = a.state == b.state
            auth_match = a.authorized == b.authorized
            decision_match = a.decision == b.decision
            critical = ((a.decision == "OBSERVE" and b.decision == "CLICK") or
                        (a.candidate is not None and b.candidate is not None and not cm))
            shot = compare_screenshot(adb_png, agent_png)
            case = {
                "case": meta.get("case") or os.path.basename(case_dir),
                "dir": os.path.basename(case_dir),
                "meta": meta,
                "adb": dataclasses.asdict(a) | {"parse_ms": round((t1 - t0) * 1000, 2),
                                                 "decision_ms": round((t3 - t2) * 1000, 2)},
                "agent": dataclasses.asdict(b) | {"parse_ms": round((t2 - t1) * 1000, 2),
                                                   "decision_ms": round((t4 - t3) * 1000, 2)},
                "match": {"foreground_package": pkg_match, "foreground_activity": act_match,
                          "foreground_component": comp_match, "state": state_match,
                          "candidate": cm, "candidate_semantic": sem,
                          "bounds_iou": iou, "center_delta_px": cd,
                          "authorization": auth_match, "decision": decision_match,
                          "critical": critical},
                "screenshot": shot,
            }
            cases.append(case)

    def rate(key, pred):
        vals = [pred(c) for c in cases]
        return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None

    metrics = {
        "cases": len(cases),
        "foreground_package_match_rate": rate("fgp", lambda c: c["match"]["foreground_package"]),
        "foreground_activity_match_rate": rate("fga", lambda c: c["match"]["foreground_activity"]),
        "foreground_component_match_rate": rate("fgc", lambda c: c["match"]["foreground_component"]),
        "state_match_rate": rate("state", lambda c: c["match"]["state"]),
        "candidate_match_rate": rate("cand", lambda c: c["match"]["candidate"]),
        "authorization_match_rate": rate("auth", lambda c: c["match"]["authorization"]),
        "decision_match_rate": rate("dec", lambda c: c["match"]["decision"]),
        "critical_mismatches": sum(1 for c in cases if c["match"]["critical"]),
        "screenshot_cases": sum(1 for c in cases if c["screenshot"]),
    }
    # Detector coverage. A fixture is only evidence when the signal is actually present
    # in at least one of the two snapshots; absent signal is COVERAGE_PENDING.
    detectors = {
        "MAIN_DETECTION": {"requires": lambda c: c["case"].startswith("main")},
        "REWARD_BUTTON": {"requires": lambda c: c["case"].startswith("main")},
        "CONSENT": {"requires": lambda c: bool(c["adb"]["candidate"] and c["adb"]["candidate"]["source"] == "consent")
                    or bool(c["agent"]["candidate"] and c["agent"]["candidate"]["source"] == "consent")},
        "APPLOVIN_STATE": {"requires": lambda c: c["adb"]["state"] == "applovin" or c["agent"]["state"] == "applovin"},
        "CLOSE_CANDIDATE": {"requires": lambda c: (
            (c["adb"]["state"] == "applovin" or c["agent"]["state"] == "applovin") and
            any(x and x.get("source") not in ("reward_button", "consent")
                for x in (c["adb"]["candidate"], c["agent"]["candidate"])))},
        "SUB_AD_CLOSE": {"requires": lambda c: bool(c["adb"]["subad"].get("found") or c["agent"]["subad"].get("found"))},
        "COUNTDOWN": {"requires": lambda c: c["adb"]["countdown"]["n"] > 0 or c["agent"]["countdown"]["n"] > 0},
        "ENDCARD": {"requires": lambda c: (
            (c["adb"]["state"] == "applovin" or c["agent"]["state"] == "applovin") and
            bool(c["adb"]["endcard"].get("confirmed") or c["agent"]["endcard"].get("confirmed")))},
        "STORE_BACK": {"requires": lambda c: c["adb"]["state"] == "store" or c["agent"]["state"] == "store"},
        "SESSION": {"requires": lambda c: c["adb"]["state"] == "main" or c["agent"]["state"] == "main"},
    }
    summary = {}
    for name, spec in detectors.items():
        applicable = [c for c in cases if spec["requires"](c)]
        if not applicable:
            summary[name] = {"fixtures": 0, "status": "FIXTURE_MISSING",
                             "matches": 0, "mismatches": 0, "critical": 0}
            continue
        matches = sum(1 for c in applicable if c["match"]["decision"] and c["match"]["state"])
        mism = len(applicable) - matches
        crit = sum(1 for c in applicable if c["match"]["critical"])
        status = "PARITY_FAIL" if crit else ("PARITY_PASS" if mism == 0 else "PARITY_PARTIAL")
        if name == "CLOSE_CANDIDATE" and all(
                not (c["adb"]["candidate"] or c["agent"]["candidate"]) for c in applicable):
            status = "COVERAGE_PENDING"
        summary[name] = {"fixtures": len(applicable), "status": status,
                         "matches": matches, "mismatches": mism, "critical": crit}

    # Dedicated missing list for scenarios explicitly required by E3.6 but absent.
    missing = [k for k, v in summary.items() if v["fixtures"] == 0]
    critical_detectors = ("MAIN_DETECTION", "REWARD_BUTTON", "CLOSE_CANDIDATE",
                          "SUB_AD_CLOSE", "STORE_BACK")
    crit_missing = [k for k in critical_detectors if summary.get(k, {}).get("fixtures", 0) == 0]
    crit_fail = [k for k in critical_detectors if summary.get(k, {}).get("status") == "PARITY_FAIL"]
    safety = {"adb_observe_agent_click": sum(
        1 for c in cases if c["adb"]["decision"] == "OBSERVE" and c["agent"]["decision"] == "CLICK"),
        "different_candidate": sum(1 for c in cases
                                   if c["adb"]["candidate"] and c["agent"]["candidate"]
                                   and not c["match"]["candidate"]),
        "agent_actions_executed": 0}
    if metrics["critical_mismatches"] or safety["adb_observe_agent_click"] or crit_fail:
        verdict = "PARITY_FAIL"
    elif crit_missing:
        verdict = "PARITY_PARTIAL"
    elif missing:
        verdict = "PARITY_PASS_WITH_COVERAGE_PENDING"
    else:
        verdict = "PARITY_PASS"
    adb_parse = [c["adb"]["parse_ms"] for c in cases]
    agent_parse = [c["agent"]["parse_ms"] for c in cases]
    adb_dec = [c["adb"]["decision_ms"] for c in cases]
    agent_dec = [c["agent"]["decision_ms"] for c in cases]
    agent_capture = [c["meta"].get("agent_capture_ms") for c in cases
                     if c.get("meta", {}).get("agent_capture_ms") is not None]
    latency = {
        "adb_parse_ms_avg": round(sum(adb_parse) / len(adb_parse), 2) if adb_parse else None,
        "agent_parse_ms_avg": round(sum(agent_parse) / len(agent_parse), 2) if agent_parse else None,
        "adb_decision_ms_avg": round(sum(adb_dec) / len(adb_dec), 2) if adb_dec else None,
        "agent_decision_ms_avg": round(sum(agent_dec) / len(agent_dec), 2) if agent_dec else None,
        "agent_tree_capture_ms_avg": round(sum(agent_capture) / len(agent_capture), 2) if agent_capture else None,
        "note": "Live GET_UI_TREE/CAPTURE_SCREEN totals are in E3.5 report; replay measures parse+decision only.",
    }
    observed = sorted({c["dir"] for c in cases})
    golden_missing = []
    if summary.get("CONSENT", {}).get("fixtures", 0) == 0:
        golden_missing.append("CONSENT")
    if summary.get("COUNTDOWN", {}).get("fixtures", 0) == 0:
        golden_missing.append("COUNTDOWN")
    if summary.get("ENDCARD", {}).get("fixtures", 0) == 0:
        golden_missing.append("ENDCARD")
    if not any((c["adb"]["candidate"] and c["adb"]["candidate"]["source"] in
                ("endcard_structural", "anonymous_top_right")) or
               (c["agent"]["candidate"] and c["agent"]["candidate"]["source"] in
                ("endcard_structural", "anonymous_top_right")) for c in cases):
        golden_missing.append("STRUCTURAL/ANONYMOUS close")
    if not any(str((c.get("meta") or {}).get("ad_type", "")).lower() in ("interstitial", "rewarded")
               for c in cases):
        golden_missing.append("INTERSTITIAL/REWARDED label real")
    golden = {"observed": observed, "missing": golden_missing}
    report = {
        "phase": "E3.6_DECISION_PARITY",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "authority": "main_sln_v2 pure fast-close detectors",
        "mode": "replay_offline_read_only",
        "fixtures_root": fixtures_root,
        "cases": cases, "metrics": metrics, "detectors": summary,
        "fixture_missing": missing,
        "critical_fixture_missing": crit_missing,
        "verdict": verdict,
        "latency": latency,
        "golden_cases": golden,
        "safety": safety,
        "limitations": [
            "Fixtures are sequential real captures; uiautomator2 cannot run with Accessibility on this ROM.",
            "Countdown/consent/store/close-specific fixtures may be absent; absent is not PASS.",
            "IA classifier offline; screenshot classification uses the same OpenCV blue-bar detector for both frames.",
        ],
    }
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    json.dump(report, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return report


def main():
    ap = argparse.ArgumentParser()
    base = os.path.dirname(os.path.abspath(__file__))
    default_roots = [os.path.join(base, "tests", "fixtures", "e36"),
                     os.path.join(base, "tests", "fixtures", "e36_real")]
    ap.add_argument("--fixtures", action="append", default=None)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "state", "device_agents", "e36_parity.json"))
    args = ap.parse_args()
    report = replay(args.fixtures or default_roots, args.out)
    print(json.dumps({"cases": report["metrics"]["cases"],
                      "metrics": report["metrics"],
                      "detectors": report["detectors"],
                      "fixture_missing": report["fixture_missing"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
