#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.8 telemetry: Agent/legacy accounting without changing bot decisions."""
from __future__ import annotations

import atexit
import inspect
import json
import os
import threading
import time

READ_OPS = frozenset((
    "PING", "GET_STATUS", "GET_DEVICE_INFO", "GET_NETWORK_INFO", "GET_FOREGROUND_APP",
    "GET_ACCESSIBILITY_STATE", "GET_UI_TREE", "CAPTURE_SCREEN",
    "GET_PRIVILEGED_BRIDGE_STATUS", "REQUEST_SHIZUKU_PERMISSION",
))
MUTATING_OPS = frozenset(("START_APP", "BACK", "HOME", "TAP", "SWIPE", "STOP_APP",
                          "CLEAR_APP_DATA"))


def _callsite():
    try:
        f = inspect.currentframe()
        for _ in range(3):
            f = f.f_back if f else None
        if f is None:
            return "unknown"
        return "%s:%s" % (os.path.basename(f.f_code.co_filename), f.f_lineno)
    except Exception:
        return "unknown"


def _small(value, limit=240):
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


class E38Telemetry:
    def __init__(self, path=None, log_fn=None):
        self.path = path
        self.log_fn = log_fn
        self.lock = threading.RLock()
        self.reset("cycle")

    def reset(self, phase="cycle"):
        with self.lock:
            self.started_at = time.time()
            self.data = {
                "phase": phase,
                "started_at": self.started_at,
                "agent_reads": 0,
                "agent_mutations": 0,
                "legacy_reads": 0,
                "legacy_mutations": 0,
                "adb_subprocesses": 0,
                "uncertain": 0,
                "reconnects": 0,
                "duplicate_commands": 0,
                "commands": [],
                "legacy_fallbacks": [],
                "agent_errors": [],
            }
            self._seen = set()
        self.save()

    def count(self, key, n=1):
        with self.lock:
            self.data[key] = int(self.data.get(key, 0)) + int(n)

    def log(self, msg):
        if self.log_fn:
            try:
                self.log_fn(msg)
            except Exception:
                pass

    def record_command(self, op, params, result, latency_ms):
        with self.lock:
            cid = str((result or {}).get("command_id") or "")
            status = str((result or {}).get("status") or "failure").lower()
            is_mut = op in MUTATING_OPS
            if is_mut:
                self.data["agent_mutations"] += 1
            elif op in READ_OPS:
                self.data["agent_reads"] += 1
            if status == "uncertain":
                self.data["uncertain"] += 1
            if cid and cid in self._seen:
                self.data["duplicate_commands"] += 1
            if cid:
                self._seen.add(cid)
            rec = {
                "command_id": cid,
                "op": op,
                "mutating": is_mut,
                "status": status,
                "latency_ms": round(float(latency_ms), 1),
                "error": str((result or {}).get("error") or ""),
                "params": _small(params, 200),
            }
            self.data["commands"].append(rec)
            if status not in ("success", "expired"):
                self.data["agent_errors"].append(rec)
                self.log("[E38][AGENT][%s] op=%s status=%s latency_ms=%.1f error=%s" % (
                    "UNCERTAIN" if status == "uncertain" else "ERROR", op, status,
                    latency_ms, rec["error"]))
            elif is_mut:
                self.log("[E38][AGENT][MUTATION] op=%s status=%s latency_ms=%.1f id=%s" % (
                    op, status, latency_ms, cid))
            need_save = (is_mut or status == "uncertain"
                         or (time.time() - self._last_save) >= 2.0)
        if need_save:
            self.save()

    def record_legacy(self, operation, reason, mutation=False, adb=False, adb_count=None):
        callsite = _callsite()
        with self.lock:
            self.data["legacy_mutations" if mutation else "legacy_reads"] += 1
            if adb:
                self.data["adb_subprocesses"] += int(adb_count or 1)
            rec = {"operation": str(operation), "callsite": callsite,
                   "reason": str(reason), "mutation": bool(mutation), "adb": bool(adb)}
            self.data["legacy_fallbacks"].append(rec)
        self.log("[E38][LEGACY_FALLBACK] operation=%s callsite=%s reason=%s" % (
            operation, callsite, reason))
        if mutation or (time.time() - self._last_save) >= 2.0:
            self.save()
        return rec

    def snapshot(self):
        with self.lock:
            data = json.loads(json.dumps(self.data, ensure_ascii=False, default=str))
        data["ended_at"] = time.time()
        data["duration_s"] = round(data["ended_at"] - data["started_at"], 2)
        return data

    def save(self):
        if not self.path:
            return
        try:
            with self.lock:
                payload = json.loads(json.dumps(self.data, ensure_ascii=False, default=str))
            payload["updated_at"] = time.time()
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._last_save = time.time()
        except Exception:
            pass


_TEL = None


def get_telemetry(path=None, log_fn=None):
    global _TEL
    if _TEL is None:
        _TEL = E38Telemetry(path=path, log_fn=log_fn)
        base = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "state", "device_agents", "e38_telemetry.json")
        if _TEL.path is None:
            _TEL.path = base
        atexit.register(_TEL.save)
    return _TEL


def reset(path=None, phase="cycle", log_fn=None):
    t = get_telemetry(path, log_fn)
    if path:
        t.path = path
    t.reset(phase)
    return t
