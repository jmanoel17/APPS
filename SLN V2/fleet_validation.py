#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Controlled fleet validation for the unified Device Agent DPN path.

The harness only observes and launches external bot processes. It does not change
Fast Close, accounting, the group ledger, the DPN state machine or the mutations.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections import defaultdict, deque

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE, "state", "dpn_fleet_validation")
REPORT_DIR = os.path.join(BASE, "state", "device_agents")
STATUS = os.path.join(STATE_DIR, "status.json")
AGENTS = os.path.join(STATE_DIR, "agents_status.json")
AUTO_GROUPS = os.path.join(BASE, "dpn_groups_fleet_auto.json")
CONFIG_OVERLAY = "config_fleet_validation.json"

try:
    import psutil  # optional host instrumentation
except Exception:
    psutil = None


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def port_open(port):
    # A wildcard bind is the actual controller binding. A localhost-only
    # listener (for example an ADB reverse endpoint) must not abort the test.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def safe_name(value):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(value))[:120]


class EventSink:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "a", encoding="utf-8")

    def emit(self, kind, **fields):
        row = {"ts": time.time(), "kind": kind}
        row.update(fields)
        self.f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.f.flush()
        return row

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


def controller_processes():
    if psutil is None:
        return []
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "dpn_agent_controller.py" in cmd:
                out.append(p.pid)
        except Exception:
            pass
    return out


class FleetMonitor:
    def __init__(self, sink, started_pid, duration_s, members_by_group):
        self.sink = sink
        self.started_pid = started_pid
        self.duration_s = duration_s
        self.members_by_group = members_by_group
        self.started = time.time()
        self.prev = {}
        self.group = {}
        self.device = {}
        self.metrics_prev = {}
        self.reconnect_times = deque()
        self.drain_since = {}
        self.uncertain = 0
        self.duplicate_switch = {}
        self.stop_reasons = []
        self.errors = []
        self.last_status_ts = 0.0

    def _once(self, key, value, kind, **extra):
        if self.prev.get(key) == value:
            return
        self.prev[key] = value
        self.sink.emit(kind, key=key, value=value, **extra)

    def check_stop(self, now):
        reasons = []
        st = read_json(STATUS, {}) or {}
        ag = read_json(AGENTS, {}) or {}
        server = ag.get("server") or {}
        metrics = ag.get("pool_metrics") or {}
        if not st:
            return []
        pid = st.get("controller_pid")
        if self.started_pid and pid and int(pid) != int(self.started_pid):
            reasons.append("TWO_CONTROLLERS")
        pids = controller_processes()
        if len(pids) > 1:
            reasons.append("TWO_CONTROLLERS")
        if server.get("metrics", {}).get("reconnects", 0) > 0:
            self.reconnect_times.append((now, server.get("metrics", {}).get("reconnects", 0)))
        while self.reconnect_times and now - self.reconnect_times[0][0] > 60:
            self.reconnect_times.popleft()
        if len(self.reconnect_times) >= 2:
            delta = self.reconnect_times[-1][1] - self.reconnect_times[0][1]
            if delta > 10:
                reasons.append("RECONNECT_STORM")
        prev_metrics = self.metrics_prev or {}
        auth_delta = int(metrics.get("auth_backoff", 0)) - int(prev_metrics.get("auth_backoff", 0))
        if auth_delta > 0:
            reasons.append("AUTH_STORM")
        unc_delta = int(metrics.get("mutation_uncertain", 0)) - int(prev_metrics.get("mutation_uncertain", 0))
        if unc_delta > 0:
            self.uncertain += unc_delta
            self.sink.emit("UNCERTAIN", delta=unc_delta,
                           total=int(metrics.get("mutation_uncertain", 0)))
        if self.uncertain >= 2:
            reasons.append("UNCERTAIN_REPEATED")
        self.metrics_prev = dict(metrics)
        for gid, g in (st.get("dpns") or {}).items():
            if not isinstance(g, dict):
                reasons.append("STATE_CORRUPTION")
                continue
            members = self.members_by_group.get(gid)
            if members is not None and int(g.get("active_devices") or 0) != len(members):
                reasons.append("MEMBERSHIP_MISMATCH")
            state = str(g.get("state") or "")
            if state == "DRAIN_TIMEOUT":
                self.drain_since.setdefault(gid, now)
                if now - self.drain_since[gid] > 30:
                    reasons.append("DRAIN_TIMEOUT_PERSISTENT")
            else:
                self.drain_since.pop(gid, None)
            rot = g.get("last_rotation_at")
            if rot:
                self.duplicate_switch.setdefault(gid, set()).add(rot)
                if len(self.duplicate_switch[gid]) > 1:
                    reasons.append("DUPLICATE_MUTATION")
        return sorted(set(reasons))

    def tick(self):
        now = time.time()
        st = read_json(STATUS, {}) or {}
        ag = read_json(AGENTS, {}) or {}
        if st:
            self.last_status_ts = now
            for gid, g in (st.get("dpns") or {}).items():
                if not isinstance(g, dict):
                    continue
                old = self.group.get(gid, {})
                for key in ("state", "current_public_ip", "current_epoch",
                            "accepted_work_units", "active_devices", "safe_devices",
                            "refresh_attempt", "last_error"):
                    val = g.get(key)
                    if old.get(key) != val:
                        self.sink.emit("GROUP_CHANGE", group=gid, field=key,
                                       old=old.get(key), new=val)
                if old.get("state") != g.get("state"):
                    self.sink.emit("GROUP_STATE", group=gid, old=old.get("state"),
                                   new=g.get("state"), ip=g.get("current_public_ip"),
                                   epoch=g.get("current_epoch"),
                                   accepted=g.get("accepted_work_units"))
                if old.get("current_public_ip") != g.get("current_public_ip"):
                    self.sink.emit("IP_CHANGE", group=gid,
                                   old=old.get("current_public_ip"),
                                   new=g.get("current_public_ip"),
                                   epoch=g.get("current_epoch"))
                self.group[gid] = dict(g)
            for row in (ag.get("agents") or []):
                aid = str(row.get("agent_id") or "")
                d = self.device.setdefault(aid, {})
                for key in ("status", "authenticated", "physical_dpn_id", "lan_ip"):
                    if d.get(key) != row.get(key):
                        self.sink.emit("AGENT_CHANGE", agent_id=aid, field=key,
                                       old=d.get(key), new=row.get(key))
                d.update(row)
        for reason in self.check_stop(now):
            if reason not in self.stop_reasons:
                self.stop_reasons.append(reason)
                self.sink.emit("STOP_CONDITION", reason=reason)
        return now - self.started >= self.duration_s

    def host(self, bot_pids):
        out = {"connections": (read_json(AGENTS, {}) or {}).get("server", {}).get("connections", 0),
               "coordinator_tick_age_s": round(time.time() - self.last_status_ts, 2)
               if self.last_status_ts else None,
               "leases_active": sum(1 for v in ((read_json(AGENTS, {}) or {}).get("leases") or {}).values()
                                    if v.get("state") in ("ACQUIRED", "BUSY", "UNCERTAIN")),
               "processes": []}
        if psutil is not None:
            for pid in [self.started_pid] + list(bot_pids):
                try:
                    proc = psutil.Process(pid)
                    out["processes"].append({"pid": pid, "cpu_pct": proc.cpu_percent(interval=None),
                                             "rss_mb": round(proc.memory_info().rss / 1048576, 1)})
                except Exception:
                    pass
        return out

    def _work_units_by_device(self):
        out = {}
        path = os.path.join(STATE_DIR, "work_units.jsonl")
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    dev = str(ev.get("device_id") or "")
                    if dev:
                        out[dev] = int(out.get(dev, 0)) + 1
        except Exception:
            pass
        return out

    def _safe_devices(self, members):
        out = {}
        for dev in members:
            try:
                from dpn import state as S
                p = os.path.join(STATE_DIR, "workers", S.safe_id(dev) + ".json")
                row = read_json(p, {}) or {}
                out[dev] = bool(row.get("safe"))
            except Exception:
                out[dev] = False
        return out

    def summary(self, bot_pids):
        now = time.time()
        groups = {}
        for gid, g in (read_json(STATUS, {}) or {}).get("dpns", {}).items():
            members = self.members_by_group.get(gid, [])
            groups[gid] = {
                "state": g.get("state"), "ip": g.get("current_public_ip"),
                "epoch": g.get("current_epoch"), "accepted_work_units": g.get("accepted_work_units"),
                "active_devices": g.get("active_devices"), "safe_devices": g.get("safe_devices"),
                "members": members, "refresh_attempt": g.get("refresh_attempt"),
                "last_rotation_at": g.get("last_rotation_at"),
                "last_error": g.get("last_error"),
                "pass": bool(g.get("state") == "RUNNING" and g.get("active_devices") == len(members)
                             and not g.get("last_error")),
            }
        work = self._work_units_by_device()
        membership = {}
        for gid, ms in self.members_by_group.items():
            for dev in ms:
                membership[dev] = gid
        devices = []
        for aid, row in self.device.items():
            serial = str(row.get("lan_ip") or "").split(":", 1)[0]
            serial = serial + ":5555" if serial else ""
            gid = membership.get(serial, "")
            safe = self._safe_devices([serial]).get(serial, False) if serial else False
            devices.append({
                "agent_id": aid, "device": serial, "group": gid,
                "status": row.get("status"), "authenticated": row.get("authenticated"),
                "physical_dpn_id": row.get("physical_dpn_id"),
                "member": bool(serial and serial in self.members_by_group.get(gid, [])),
                "safe": safe, "work_units": int(work.get(serial, 0)),
                "latency_ms": row.get("latency_ms", 0), "error": row.get("error", ""),
            })
        return {"started_at": self.started, "ended_at": now,
                "duration_s": round(now - self.started, 1),
                "stop_conditions": self.stop_reasons,
                "groups": groups,
                "devices": devices,
                "host": self.host(bot_pids),
                "pool_metrics": (read_json(AGENTS, {}) or {}).get("pool_metrics", {}),
                "dpn_metrics": (read_json(AGENTS, {}) or {}).get("dpn_metrics", {}),
                "agents": list(self.device.values())}


def load_members():
    groups = read_json(AUTO_GROUPS, {}) or {}
    out = {}
    for gid, g in groups.items():
        if isinstance(g, dict):
            out[gid] = [str(m) for m in (g.get("members") or []) if m]
    return out


def online_serials():
    status = read_json(AGENTS, {}) or {}
    out = set()
    for a in status.get("agents") or []:
        if str(a.get("status") or "").upper() == "ONLINE" and a.get("lan_ip"):
            out.add(str(a["lan_ip"]).split(":", 1)[0] + ":5555")
    return out


def launch_bots(members, app, seconds, log_dir):
    serials = []
    online = online_serials()
    for gid, ms in members.items():
        for d in ms:
            if d in online and d not in serials:
                serials.append(d)
    env = os.environ.copy()
    env["SLN_CONFIG_OVERLAY"] = CONFIG_OVERLAY
    env["SLN_TEST_MAX_RUNTIME_S"] = str(seconds)
    env["SLN_APP_PACKAGE"] = app
    os.makedirs(log_dir, exist_ok=True)
    procs = []
    for dev in serials:
        log = open(os.path.join(log_dir, "fleet_%s.log" % safe_name(dev)), "w", encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-u", "main_sln_v2.py", dev, "0", "0"],
                             cwd=BASE, env=env, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        procs.append((dev, p, log))
    return serials, procs


def stop_procs(procs):
    for dev, p, log in procs:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
    deadline = time.time() + 15
    for dev, p, log in procs:
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        try:
            log.close()
        except Exception:
            pass


def setup_controller(seconds, log_path):
    os.makedirs(STATE_DIR, exist_ok=True)
    src_presence = os.path.join(BASE, "state", "dpn", "presence.json")
    dst_presence = os.path.join(STATE_DIR, "presence.json")
    if os.path.isfile(src_presence):
        with open(src_presence, "r", encoding="utf-8") as f:
            data = f.read()
        with open(dst_presence, "w", encoding="utf-8") as f:
            f.write(data)
    env = os.environ.copy()
    env["SLN_CONFIG_OVERLAY"] = CONFIG_OVERLAY
    p = subprocess.Popen([sys.executable, "-u", "dpn_agent_controller.py",
                          "--duration", str(int(seconds + 120))],
                         cwd=BASE, env=env, stdout=open(log_path, "w", encoding="utf-8"),
                         stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    deadline = time.time() + min(120, max(30, seconds))
    while time.time() < deadline:
        groups = load_members()
        if groups and any(groups.values()) and read_json(STATUS, None):
            return p, groups
        time.sleep(1)
    return p, load_members()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Controlled fleet validation (30-60 min).")
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--start-bots", action="store_true",
                    help="launch one real bot process per online group member")
    ap.add_argument("--app", default="com.flixdigital.dramaclassic")
    ap.add_argument("--logs", default=os.path.join(BASE, "logs", "fleet_validation"))
    args = ap.parse_args(argv)
    if args.minutes < 30 or args.minutes > 60:
        print("ERROR: --minutes deve estar entre 30 e 60")
        return 2
    seconds = args.minutes * 60
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(args.logs, exist_ok=True)
    if port_open(8766):
        print("ERROR: porta 8766 já está em uso; pare o controller antes do teste")
        return 3
    for name in ("status.json", "agents_status.json", "group_ledger.json",
                 "epochs.json", "route_generations.json"):
        try:
            os.unlink(os.path.join(STATE_DIR, name))
        except Exception:
            pass
    try:
        os.unlink(AUTO_GROUPS)
    except Exception:
        pass
    sink = EventSink(os.path.join(REPORT_DIR, "fleet_validation_events.jsonl"))
    sink.emit("START", minutes=args.minutes, app=args.app, start_bots=args.start_bots)
    controller = None
    bots = []
    try:
        controller, members = setup_controller(seconds, os.path.join(args.logs, "controller.log"))
        sink.emit("CONTROLLER", pid=controller.pid, groups=len(members),
                  members=sum(len(v) for v in members.values()))
        if not members or not any(members.values()):
            raise RuntimeError("auto-register não produziu members")
        if args.start_bots:
            serials, bots = launch_bots(members, args.app, seconds, args.logs)
            sink.emit("BOTS", count=len(serials), devices=serials)
        mon = FleetMonitor(sink, controller.pid, seconds, members)
        while True:
            done = mon.tick()
            if mon.stop_reasons:
                sink.emit("STOP", reasons=mon.stop_reasons)
                break
            if done:
                break
            time.sleep(1.0)
        summary = mon.summary([p.pid for _, p, _ in bots])
        summary["app"] = args.app
        summary["bots_started"] = len(bots)
        summary["status"] = "FAIL" if mon.stop_reasons else (
            "PASS" if any(g.get("last_rotation_at") for g in summary["groups"].values())
            else "PARTIAL")
        write_json(os.path.join(REPORT_DIR, "fleet_validation_report.json"), summary)
        print(json.dumps({"status": summary["status"],
                          "groups": len(summary["groups"]),
                          "bots": len(bots),
                          "stop": summary["stop_conditions"]}, ensure_ascii=False))
        return 0 if summary["status"] == "PASS" else 1
    except Exception as e:
        sink.emit("ERROR", error="%s: %s" % (type(e).__name__, e))
        print("ERROR: %s: %s" % (type(e).__name__, e))
        return 2
    finally:
        stop_procs(bots)
        if controller is not None:
            try:
                controller.terminate()
                controller.wait(timeout=10)
            except Exception:
                try:
                    controller.kill()
                except Exception:
                    pass
        sink.close()


if __name__ == "__main__":
    sys.exit(main())
