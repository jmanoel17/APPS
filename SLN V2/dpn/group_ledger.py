#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DPN E2.6 - group ledger, egress epochs and route generations.

This module is an opt-in layer (`dpn_group_ledger_v2_enabled=false` by default).
It does not touch the bot, Fast Close, reward gate, FORMAT, Store/BACK, IA or accounting.
The coordinator is the only writer of the authoritative group ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid

from . import state as S
from .availability import (AVAILABLE, INCONCLUSIVE, UNAVAILABLE,
                           NullAvailabilityProbe)


# ---------------------------------------------------------------- states
DISABLED = "DISABLED"
STARTING = "STARTING"
RUNNING = "RUNNING"
LIMIT_REACHED = "LIMIT_REACHED"
DRAINING = "DRAINING"
DRAIN_TIMEOUT = "DRAIN_TIMEOUT"
ROTATION_PENDING = "ROTATION_PENDING"
ROTATING = "ROTATING"
VERIFYING_EGRESS = "VERIFYING_EGRESS"
NEW_EPOCH = "NEW_EPOCH"
REFRESHING_ROUTES = "REFRESHING_ROUTES"
WAIT_REFRESH = "WAIT_REFRESH"
DISCOVERING_ROUTES = "DISCOVERING_ROUTES"
ROUTE_GENERATION_EXHAUSTED = "ROUTE_GENERATION_EXHAUSTED"
DEGRADED = "DEGRADED"
PAUSED = "PAUSED"
ERROR = "ERROR"

BLOCK_STATES = frozenset((
    LIMIT_REACHED, DRAINING, DRAIN_TIMEOUT, ROTATION_PENDING, ROTATING,
    VERIFYING_EGRESS, REFRESHING_ROUTES, WAIT_REFRESH, DISCOVERING_ROUTES,
    ROUTE_GENERATION_EXHAUSTED, DEGRADED, PAUSED, ERROR,
))
CANDIDATE_BAD = frozenset(("REJECTED", "COOLDOWN", "EXHAUSTED", "UNAVAILABLE", "USED"))


def now_ms(now=None):
    return int((time.time() if now is None else float(now)) * 1000)


def event_id(host_id, instance_id, cycle_id, event_type="WORK_UNIT"):
    raw = "%s|%s|%s|%s" % (host_id or "", instance_id or "", cycle_id, event_type)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_work_unit_event(host_id, instance_id, device_id, cycle_id, terminal_result,
                         app_package="", route_group_id="", agent_id="", source="worker",
                         finished_at_ms=None):
    accounted = terminal_result in ("COMPLETED", "FAILED")
    return {
        "event_type": "WORK_UNIT",
        "event_id": event_id(host_id, instance_id, cycle_id),
        "host_id": host_id or "",
        "instance_id": instance_id or "",
        "device_id": device_id or "",
        "agent_id": agent_id or "",
        "cycle_id": str(cycle_id),
        "app_package": app_package or "",
        "terminal_result": terminal_result,
        "accounted": bool(accounted),
        "finished_at_ms": int(finished_at_ms or now_ms()),
        "route_group_id": route_group_id or "",
        "source": source or "worker",
    }


def route_groups_from_config(groups):
    """Normalize dpn_groups.json into RouteGroup records; old format stays compatible."""
    out = {}
    if not isinstance(groups, dict):
        return out
    for key, val in groups.items():
        if str(key).startswith("_") or not isinstance(val, dict):
            continue
        controller = val.get("controller") or {}
        gid = str(val.get("group_id") or key)
        rec = out.setdefault(gid, {
            "group_id": gid,
            "controller_id": str(val.get("controller_id") or key),
            "provider": str(val.get("provider") or controller.get("type") or "fake"),
            "tunnel_code": val.get("tunnel_code") or controller.get("tunnel_code") or "",
            "expected_dpn_id": val.get("expected_dpn_id") or controller.get("expected_dpn_id") or "",
            "members": [],
            "enabled": bool(val.get("enabled", True)),
            "controller": controller,
        })
        for d in (val.get("members") or val.get("devices") or []):
            if d not in rec["members"]:
                rec["members"].append(d)
    return out


# ---------------------------------------------------------------- persistence
class GroupLedgerStore:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        self.ledger_path = os.path.join(state_dir, "group_ledger.json")
        self.epochs_path = os.path.join(state_dir, "epochs.json")
        self.generations_path = os.path.join(state_dir, "route_generations.json")
        self.work_units_path = os.path.join(state_dir, "work_units.jsonl")
        self.inbox_path = os.path.join(state_dir, "work_unit_events.jsonl")
        self.lock_path = os.path.join(state_dir, ".group_ledger.lock")
        self.inbox_lock = os.path.join(state_dir, ".work_unit_inbox.lock")
        os.makedirs(state_dir, exist_ok=True)

    def load(self):
        data = S.read_json(self.ledger_path, None)
        if not isinstance(data, dict):
            data = {}
        data.setdefault("version", 2)
        data.setdefault("groups", {})
        data.setdefault("epochs", {})
        data.setdefault("generations", {})
        data.setdefault("seen_events", [])
        return data

    def save(self, ledger):
        S.atomic_write_json(self.ledger_path, ledger)
        S.atomic_write_json(self.epochs_path, {"version": 2, "epochs": ledger.get("epochs", {})})
        S.atomic_write_json(self.generations_path,
                            {"version": 2, "generations": ledger.get("generations", {})})

    def append_inbox(self, event):
        from .locks import InterProcessFileLock
        os.makedirs(self.state_dir, exist_ok=True)
        with InterProcessFileLock(self.inbox_lock, timeout=10.0):
            with open(self.inbox_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass

    def read_inbox(self):
        if not os.path.exists(self.inbox_path):
            return []
        out = []
        try:
            with open(self.inbox_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
        except Exception:
            return []
        return out

    def append_work_unit(self, event):
        with open(self.work_units_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass

    def snapshot(self, group_id):
        led = self.load()
        return led.get("groups", {}).get(group_id)


# ---------------------------------------------------------------- barrier
class GroupSignalBarrier:
    """SAFE_TO_ROTATE files scoped by route_group_id + drain_epoch."""

    def __init__(self, state_dir, group_id, members, now_fn=time.time,
                 alive_fn=None, stale_s=120.0):
        self.state_dir = state_dir
        self.group_id = group_id
        self.members = list(members)
        self.now_fn = now_fn
        self.alive_fn = alive_fn or (lambda d: True)
        self.stale_s = float(stale_s)

    def _epoch(self):
        led = GroupLedgerStore(self.state_dir).load()
        return int((led.get("groups", {}).get(self.group_id) or {}).get("drain_epoch", 0))

    def _path(self, device):
        return os.path.join(self.state_dir, "workers", S.safe_id(device) + ".json")

    def publish_safe(self, device, instance_id=None, cycle_id=None):
        epoch = self._epoch()
        p = self._path(device)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        S.atomic_write_json(p, {"device": device, "group_id": self.group_id,
                                "safe": True, "drain_epoch": epoch,
                                "instance_id": instance_id, "cycle_id": cycle_id,
                                "last_seen": self.now_fn()})

    def _resolved(self, device, epoch, now):
        sig = S.read_json(self._path(device), None)
        if isinstance(sig, dict) and sig.get("safe") and sig.get("group_id") == self.group_id \
                and int(sig.get("drain_epoch", -1)) == epoch \
                and (now - float(sig.get("last_seen", 0))) <= self.stale_s:
            return True
        try:
            return not bool(self.alive_fn(device))
        except Exception:
            return False

    def snapshot(self):
        epoch = self._epoch()
        now = self.now_fn()
        return {d: self._resolved(d, epoch, now) for d in self.members}

    def all_safe(self, timeout_s=0.0):
        end = self.now_fn() + max(0.0, float(timeout_s))
        while True:
            snap = self.snapshot()
            if all(snap.values()):
                return True
            if self.now_fn() >= end:
                return False
            time.sleep(0.05)


# ---------------------------------------------------------------- coordinator
class GroupLedgerCoordinator:
    def __init__(self, state_dir, group, provider, cfg=None, store=None, barrier=None,
                 probe=None, now_fn=time.time, sleep_fn=time.sleep, log_fn=None,
                 console_log_fn=None):
        self.state_dir = state_dir
        self.group = dict(group)
        self.group_id = str(group.get("group_id"))
        self.members = list(group.get("members") or [])
        self.cfg = cfg or {}
        self.provider = provider
        self.store = store or GroupLedgerStore(state_dir)
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn
        self.log = log_fn or (lambda *a, **k: None)
        self.console_log_fn = console_log_fn
        self._last_console_state = None
        self.threshold = max(1, int(self.cfg.get("dpn_work_units_per_ip", 20) or 20))
        self.cooldown_s = float(self.cfg.get("dpn_ip_reuse_cooldown_s", 3600) or 3600)
        self.drain_timeout_s = float(self.cfg.get("dpn_drain_timeout_s", 120) or 120)
        self.settle_s = max(0.0, float(self.cfg.get("dpn_route_refresh_settle_s", 5)))
        self.max_refreshes = int(self.cfg.get("dpn_max_route_refreshes", 2) or 2)
        self.refresh_backoff_s = max(0.0, float(self.cfg.get("dpn_route_refresh_backoff_s", 10) or 0))
        self.probe = probe or NullAvailabilityProbe()
        self.barrier = barrier or GroupSignalBarrier(
            state_dir, self.group_id, self.members, now_fn=now_fn)
        self._ensure_group()

    # ------------------------------------------------------------ state
    def _load(self):
        return self.store.load()

    def _console(self, event, **fields):
        if self.console_log_fn is None:
            return
        try:
            self.console_log_fn(self.group_id, self.members, event, **fields)
        except Exception:
            pass

    def _save(self, led):
        g = self._group(led)
        state = g.get("state")
        if state != self._last_console_state:
            self._last_console_state = state
            self._console("STATE", state=state, ip=g.get("current_public_ip"),
                          epoch=g.get("current_epoch_id"),
                          accepted=g.get("accepted_work_units"),
                          threshold=self.threshold,
                          generation=g.get("route_generation_id"),
                          refresh_attempt=g.get("refresh_attempt"))
        self.store.save(led)

    def _ensure_group(self):
        led = self._load()
        g = led["groups"].get(self.group_id)
        if not isinstance(g, dict):
            g = {
                "group_id": self.group_id,
                "controller_id": self.group.get("controller_id"),
                "members": self.members,
                "state": STARTING,
                "current_epoch_id": None,
                "current_public_ip": None,
                "accepted_work_units": 0,
                "threshold": self.threshold,
                "drain_overshoot": 0,
                "overshoot_count": 0,
                "overshoot_devices": [],
                "overshoot_cycle_ids": [],
                "drain_epoch": 0,
                "drain_started_at": None,
                "route_generation_id": None,
                "candidates_total": 0,
                "candidates_tested": 0,
                "candidates_rejected": 0,
                "refresh_attempt": 0,
                "last_rotation_at": None,
                "last_probe": None,
                "degraded_reason": None,
                "last_error": None,
            }
            led["groups"][self.group_id] = g
            self._save(led)
        return g

    def _group(self, led):
        return led["groups"][self.group_id]

    def _new_generation(self, led, candidates, refresh_index=0):
        g = self._group(led)
        gid = "%s:gen:%d" % (self.group_id, int(refresh_index) + 1)
        norm = []
        for c in candidates or []:
            ip = str(c.get("public_ip") or c.get("ip") or "")
            if not ip:
                continue
            norm.append({"public_ip": ip,
                         "tunnel_code": str(c.get("tunnel_code") or c.get("tunnelCode") or ""),
                         "status": c.get("status", "UNKNOWN"),
                         "first_seen_at": c.get("first_seen_at", now_ms(self.now_fn())),
                         "last_seen_at": now_ms(self.now_fn()),
                         "attempt_count": int(c.get("attempt_count", 0)),
                         "failure_reason": str(c.get("failure_reason") or "")})
        # stable unique by ip
        by_ip = {}
        for c in norm:
            by_ip[c["public_ip"]] = c
        norm = [by_ip[k] for k in sorted(by_ip)]
        fp = hashlib.sha256(json.dumps(
            sorted((c["tunnel_code"], c["public_ip"]) for c in norm)).encode()).hexdigest()
        gen = {"route_generation_id": gid, "route_group_id": self.group_id,
               "created_at_ms": now_ms(self.now_fn()), "refresh_index": int(refresh_index),
               "candidates_seen": len(norm), "candidates_rejected": 0,
               "fingerprint": fp, "state": "ACTIVE", "candidates": by_ip}
        led["generations"][gid] = gen
        g["route_generation_id"] = gid
        g["candidates_total"] = len(norm)
        g["candidates_tested"] = 0
        g["candidates_rejected"] = 0
        return gen

    def _candidates(self):
        try:
            if hasattr(self.provider, "get_candidates"):
                raw = self.provider.get_candidates()
            elif hasattr(self.provider, "fetch_tunnels"):
                raw = self.provider.fetch_tunnels() or []
            else:
                raw = []
        except Exception:
            raw = []
        out = []
        for c in raw or []:
            if isinstance(c, dict):
                out.append(c)
            else:
                out.append({"public_ip": str(c)})
        if not out:
            try:
                ip = self.provider.get_public_ip()
            except Exception:
                ip = None
            if ip:
                out = [{"public_ip": str(ip)}]
        return out

    def _create_epoch(self, led, ip, tunnel_code="", gen_id=""):
        g = self._group(led)
        eid = "%s:epoch:%d" % (self.group_id, len(led["epochs"]) + 1)
        epoch = {"epoch_id": eid, "route_group_id": self.group_id,
                 "route_generation_id": gen_id or g.get("route_generation_id"),
                 "public_ip": str(ip), "tunnel_code": tunnel_code or "",
                 "started_at_ms": now_ms(self.now_fn()), "ended_at_ms": None,
                 "accepted_work_units": 0, "drain_overshoot": 0, "state": RUNNING}
        led["epochs"][eid] = epoch
        g["current_epoch_id"] = eid
        g["current_public_ip"] = str(ip)
        g["accepted_work_units"] = 0
        g["drain_overshoot"] = 0
        g["overshoot_count"] = 0
        g["overshoot_devices"] = []
        g["overshoot_cycle_ids"] = []
        g["state"] = RUNNING
        g["degraded_reason"] = None
        c, _ = self._candidate_status(led, str(ip))
        if c is not None:
            c["status"] = "USED"
        S.record_ip_use(self.state_dir, str(ip), self.group_id, g.get("route_generation_id"),
                        now=self.now_fn())
        return epoch

    def _close_epoch(self, led, reason="rotated"):
        g = self._group(led)
        eid = g.get("current_epoch_id")
        ep = led["epochs"].get(eid)
        if ep is not None:
            ep["ended_at_ms"] = now_ms(self.now_fn())
            ep["state"] = "CLOSED"
            ep["close_reason"] = reason

    # ------------------------------------------------------------ events
    def ingest(self):
        led = self._load()
        g = self._group(led)
        seen = set(led.get("seen_events") or [])
        changed = False
        for ev in self.store.read_inbox():
            if ev.get("route_group_id") != self.group_id:
                continue
            eid = str(ev.get("event_id") or "")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            changed = True
            if ev.get("accounted") and g.get("current_epoch_id"):
                ep = led["epochs"].get(g["current_epoch_id"]) or {}
                units = int(g.get("accepted_work_units", 0))
                if g.get("state") in (RUNNING, STARTING) and units < self.threshold:
                    units += 1
                    g["accepted_work_units"] = units
                    ep["accepted_work_units"] = int(ep.get("accepted_work_units", 0)) + 1
                    ev["accepted_for_epoch_quota"] = True
                else:
                    g["drain_overshoot"] = int(g.get("drain_overshoot", 0)) + 1
                    g["overshoot_count"] = int(g.get("overshoot_count", 0)) + 1
                    ep["drain_overshoot"] = int(ep.get("drain_overshoot", 0)) + 1
                    ev["accepted_for_epoch_quota"] = False
                    dev = str(ev.get("device_id") or "")
                    cyc = str(ev.get("cycle_id") or "")
                    if dev and dev not in g["overshoot_devices"]:
                        g["overshoot_devices"].append(dev)
                    if cyc and cyc not in g["overshoot_cycle_ids"]:
                        g["overshoot_cycle_ids"].append(cyc)
                self.store.append_work_unit(ev)
        if changed:
            led["seen_events"] = list(seen)[-20000:]
            self._save(led)
        return changed

    # ------------------------------------------------------------ helpers
    def _barrier_safe(self):
        try:
            snap = self.barrier.snapshot()
            if isinstance(snap, dict) and snap and all(isinstance(v, bool) for v in snap.values()):
                return all(snap.values()), snap
        except Exception:
            pass
        try:
            return bool(self.barrier.all_safe(0)), {}
        except Exception:
            return True, {}

    def _candidate_status(self, led, ip):
        g = self._group(led)
        gen = led["generations"].get(g.get("route_generation_id")) or {}
        c = (gen.get("candidates") or {}).get(str(ip))
        return c, gen

    def _reject_candidate(self, led, ip, status, reason):
        g = self._group(led)
        c, gen = self._candidate_status(led, ip)
        if c is None:
            c = {"public_ip": str(ip), "tunnel_code": "", "first_seen_at": now_ms(self.now_fn()),
                 "last_seen_at": now_ms(self.now_fn()), "attempt_count": 0,
                 "status": "UNKNOWN", "failure_reason": ""}
            gen.setdefault("candidates", {})[str(ip)] = c
        c["status"] = status
        c["failure_reason"] = reason
        c["attempt_count"] = int(c.get("attempt_count", 0)) + 1
        c["last_seen_at"] = now_ms(self.now_fn())
        self._console("CANDIDATE_REJECTED", ip=ip, status=status, reason=reason,
                      generation=gen.get("route_generation_id"))
        g["candidates_tested"] = int(g.get("candidates_tested", 0)) + 1
        g["candidates_rejected"] = int(g.get("candidates_rejected", 0)) + 1
        gen["candidates_rejected"] = int(gen.get("candidates_rejected", 0)) + 1
        bad = [x for x in (gen.get("candidates") or {}).values()
               if x.get("status") in CANDIDATE_BAD]
        return len(bad) >= max(1, len(gen.get("candidates") or {}))

    def _probe(self, ip):
        if not bool(self.cfg.get("dpn_availability_probe_enabled", False)):
            return AVAILABLE, "probe_disabled"
        retries = max(0, int(self.cfg.get("dpn_availability_probe_retries", 2) or 0))
        interval = max(0.0, float(self.cfg.get("dpn_availability_probe_interval_s", 5) or 0))
        last = None
        for i in range(retries + 1):
            try:
                r = self.probe.probe(self.group_id, {"public_ip": ip}, 5.0)
                last = getattr(r, "status", str(r))
                if last == AVAILABLE:
                    return AVAILABLE, "available"
                if last == UNAVAILABLE:
                    return UNAVAILABLE, getattr(r, "reason", "unavailable")
            except Exception:
                last = INCONCLUSIVE
            if i < retries and interval:
                self.sleep_fn(interval)
        return (INCONCLUSIVE if last == INCONCLUSIVE else UNAVAILABLE), "probe_inconclusive"

    # ------------------------------------------------------------ steps
    def _start(self, led):
        g = self._group(led)
        try:
            ip = self.provider.get_public_ip()
            node = self.provider.get_node_id()
            healthy = self.provider.health()
        except Exception as e:
            g["state"] = ERROR
            g["last_error"] = "discover:%s" % type(e).__name__
            return "error"
        if not ip or not healthy:
            g["state"] = ERROR
            g["last_error"] = "no_public_ip_or_unhealthy"
            return "error"
        gen = self._new_generation(led, self._candidates())
        c, _ = self._candidate_status(led, ip)
        if c:
            c["status"] = "AVAILABLE"
        self._create_epoch(led, ip, str(node or ""), gen["route_generation_id"])
        g["state"] = RUNNING
        return "ready"

    def _verify_candidate(self, led):
        g = self._group(led)
        try:
            ip = self.provider.get_public_ip()
            healthy = self.provider.health()
        except Exception as e:
            g["state"] = ERROR
            g["last_error"] = "verify:%s" % type(e).__name__
            return "error"
        if not ip:
            return self._reject_or_refresh(led, "", "REJECTED", "no_public_ip")
        if str(ip) == str(g.get("current_public_ip")):
            return self._reject_or_refresh(led, ip, "REJECTED", "same_ip")
        st, info = S.ip_available(self.state_dir, ip, self.cooldown_s, now=self.now_fn())
        if st == "COOLDOWN":
            g["last_probe"] = {"ip": ip, "status": "COOLDOWN", "info": info}
            return self._reject_or_refresh(led, ip, "COOLDOWN", "cooldown")
        if not healthy:
            return self._reject_or_refresh(led, ip, "UNAVAILABLE", "unhealthy")
        pst, preason = self._probe(ip)
        g["last_probe"] = {"ip": ip, "status": pst, "reason": preason,
                           "at_ms": now_ms(self.now_fn())}
        if pst != AVAILABLE:
            return self._reject_or_refresh(led, ip, "UNAVAILABLE", preason)
        c, gen = self._candidate_status(led, ip)
        if c:
            c["status"] = "AVAILABLE"
        gen["state"] = "ACTIVE"
        self._create_epoch(led, ip, str(self.provider.get_node_id() or ""),
                           gen.get("route_generation_id"))
        return "ready"

    def _reject_or_refresh(self, led, ip, status, reason):
        exhausted = self._reject_candidate(led, ip, status, reason)
        g = self._group(led)
        g["state"] = ROUTE_GENERATION_EXHAUSTED if exhausted else ROTATING
        return "exhausted" if exhausted else "rotating"

    def tick(self):
        self.ingest()
        led = self._load()
        g = self._group(led)
        state = g.get("state")
        if state in (PAUSED, ERROR, DEGRADED, DISABLED):
            return state.lower()
        if state in (STARTING,):
            r = self._start(led)
            self._save(led)
            return r
        if state == RUNNING:
            if int(g.get("accepted_work_units", 0)) >= self.threshold:
                g["state"] = LIMIT_REACHED
                self._save(led)
                return "limit_reached"
            return "ready"
        if state == LIMIT_REACHED:
            g["state"] = DRAINING
            g["drain_epoch"] = int(g.get("drain_epoch", 0)) + 1
            g["drain_started_at"] = self.now_fn()
            self._save(led)
            return "draining"
        if state == DRAINING:
            ok, snap = self._barrier_safe()
            if ok:
                if g.get("current_epoch_id"):
                    self._close_epoch(led, "draining")
                g["state"] = ROTATING
                self._save(led)
                return "rotating"
            if self.now_fn() - float(g.get("drain_started_at") or self.now_fn()) >= self.drain_timeout_s:
                g["state"] = DRAIN_TIMEOUT
                g["last_error"] = "drain_timeout"
                self._save(led)
                return "drain_timeout"
            return "draining"
        if state == ROTATION_PENDING:
            g["state"] = ROTATING
            self._save(led)
            return "rotating"
        if state == ROTATING:
            try:
                self.provider.rotate_ip("REGULAR")
            except Exception as e:
                g["state"] = ERROR
                g["last_error"] = "rotate:%s" % type(e).__name__
                self._save(led)
                return "error"
            g["last_rotation_at"] = self.now_fn()
            g["state"] = VERIFYING_EGRESS
            self._save(led)
            return "verifying"
        if state == VERIFYING_EGRESS:
            r = self._verify_candidate(led)
            self._save(led)
            return r
        if state == ROUTE_GENERATION_EXHAUSTED:
            g["state"] = REFRESHING_ROUTES
            self._save(led)
            return "refreshing"
        if state == REFRESHING_ROUTES:
            g["refresh_attempt"] = int(g.get("refresh_attempt", 0)) + 1
            if g["refresh_attempt"] > self.max_refreshes:
                g["state"] = DEGRADED
                g["degraded_reason"] = "max_route_refreshes"
                self._save(led)
                return "degraded"
            try:
                if hasattr(self.provider, "refresh_route_set"):
                    self.provider.refresh_route_set()
            except Exception:
                pass
            g["state"] = WAIT_REFRESH
            g["refresh_due_at"] = self.now_fn() + self.settle_s + (
                self.refresh_backoff_s * max(0, int(g.get("refresh_attempt", 0)) - 1))
            self._save(led)
            return "refresh_wait"
        if state == WAIT_REFRESH:
            if self.now_fn() < float(g.get("refresh_due_at") or 0):
                return "refresh_wait"
            old_gen = led["generations"].get(g.get("route_generation_id")) or {}
            new = self._new_generation(led, self._candidates(), g.get("refresh_attempt", 0))
            if new.get("fingerprint") == old_gen.get("fingerprint"):
                if g["refresh_attempt"] >= self.max_refreshes:
                    g["state"] = DEGRADED
                    g["degraded_reason"] = "refresh_no_change"
                else:
                    g["state"] = REFRESHING_ROUTES
                    g["refresh_no_change"] = int(g.get("refresh_no_change", 0)) + 1
                self._save(led)
                return "refresh_no_change"
            g["state"] = DISCOVERING_ROUTES
            self._save(led)
            return "discovering"
        if state == DISCOVERING_ROUTES:
            if not (led["generations"].get(g.get("route_generation_id")) or {}).get("candidates"):
                g["state"] = DEGRADED
                g["degraded_reason"] = "no_candidates"
                self._save(led)
                return "degraded"
            g["state"] = ROTATING
            self._save(led)
            return "rotating"
        return "idle"

    # ------------------------------------------------------------ control
    def gate(self):
        g = self.store.load()["groups"].get(self.group_id) or {}
        return g.get("state") not in BLOCK_STATES and \
            int(g.get("accepted_work_units", 0)) < self.threshold

    def pause(self):
        led = self._load()
        self._group(led)["state"] = PAUSED
        self._save(led)

    def resume(self):
        led = self._load()
        g = self._group(led)
        if g.get("state") in (PAUSED, DEGRADED, DRAIN_TIMEOUT):
            g["state"] = RUNNING if g.get("current_epoch_id") else STARTING
        self._save(led)

    def rotate_now(self):
        led = self._load()
        g = self._group(led)
        if g.get("state") in (RUNNING, LIMIT_REACHED):
            g["state"] = DRAINING
            g["drain_epoch"] = int(g.get("drain_epoch", 0)) + 1
            g["drain_started_at"] = self.now_fn()
        self._save(led)

    def refresh_routes_now(self):
        led = self._load()
        g = self._group(led)
        g["state"] = REFRESHING_ROUTES
        self._save(led)

    def reset_node_now(self):
        # Kept for compatibility; never auto-called by the v2 state machine.
        led = self._load()
        g = self._group(led)
        g["state"] = ERROR
        g["last_error"] = "reset_node_now_not_automatic"
        self._save(led)

    def snapshot(self):
        led = self._load()
        g = self._group(led)
        gen = led["generations"].get(g.get("route_generation_id")) or {}
        try:
            safe_snap = self.barrier.snapshot()
            safe = sum(1 for v in safe_snap.values() if v)
        except Exception:
            safe = 0
        return {
            "group_id": self.group_id,
            "state": g.get("state"),
            "current_epoch": g.get("current_epoch_id"),
            "current_public_ip": g.get("current_public_ip"),
            "accepted_work_units": int(g.get("accepted_work_units", 0)),
            "threshold": self.threshold,
            "drain_overshoot": int(g.get("drain_overshoot", 0)),
            "overshoot_count": int(g.get("overshoot_count", 0)),
            "active_devices": len(self.members),
            "safe_devices": safe,
            "blocked_devices": max(0, len(self.members) - safe),
            "route_generation_id": g.get("route_generation_id"),
            "candidates_total": int(gen.get("candidates_seen", 0)),
            "candidates_tested": int(g.get("candidates_tested", 0)),
            "candidates_rejected": int(g.get("candidates_rejected", 0)),
            "refresh_attempt": int(g.get("refresh_attempt", 0)),
            "last_rotation_at": g.get("last_rotation_at"),
            "last_probe": g.get("last_probe"),
            "degraded_reason": g.get("degraded_reason"),
            "last_error": g.get("last_error"),
        }
