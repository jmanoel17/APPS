#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.10B - DPN presence registry and read transport pool.

The pool is a controller-side index: physical_dpn_id -> Agent transports.
It never chooses or executes mutations; DPNMutationLease handles those separately.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from . import state as S


class ReadFailover(Exception):
    """Safe read failure: the operation was not dispatched and may run on another Agent."""


class TransportUncertain(Exception):
    """The read may have been dispatched; do not retry blindly."""


@dataclass
class TransportCandidate:
    agent_id: str
    device_id: str = ""
    physical_dpn_id: str = ""
    latency_ms: float = 0.0
    authenticated: bool = False
    capabilities: set = field(default_factory=set)
    connection_state: str = "OFFLINE"
    last_seen: float = 0.0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0

    def online(self, now, stale_s):
        return (self.connection_state.upper() == "ONLINE"
                and now - float(self.last_seen or 0) <= float(stale_s))


class PresenceRegistry:
    def __init__(self, state_dir=None, path=None):
        self.state_dir = state_dir
        self.path = path or (os.path.join(state_dir, "presence.json") if state_dir else None)
        self._candidates = {}
        if self.path and os.path.exists(self.path):
            data = S.read_json(self.path, {}) or {}
            for aid, d in (data.get("agents") or {}).items():
                c = TransportCandidate(
                    str(aid), str(d.get("device_id") or ""),
                    str(d.get("physical_dpn_id") or ""), float(d.get("latency_ms") or 0),
                    bool(d.get("authenticated")),
                    set(d.get("capabilities") or ()),
                    str(d.get("connection_state") or "OFFLINE"),
                    float(d.get("last_seen") or 0),
                    float(d.get("last_success_at") or 0),
                    float(d.get("last_failure_at") or 0))
                if c.physical_dpn_id:
                    self._candidates[c.agent_id] = c

    def _save(self):
        if not self.path:
            return
        data = {}
        for aid, c in self._candidates.items():
            d = dict(c.__dict__)
            d["capabilities"] = sorted(c.capabilities)
            data[aid] = d
        S.atomic_write_json(self.path, {"version": 1, "agents": data})

    def upsert(self, agent_id, device_id="", physical_dpn_id="", latency_ms=0.0,
               authenticated=False, capabilities=None, connection_state="ONLINE",
               last_seen=None):
        if not agent_id or not physical_dpn_id:
            return None
        c = self._candidates.get(str(agent_id))
        if c is None:
            c = TransportCandidate(str(agent_id))
            self._candidates[str(agent_id)] = c
        c.device_id = str(device_id or c.device_id or "")
        c.physical_dpn_id = str(physical_dpn_id)
        c.latency_ms = float(latency_ms or 0)
        c.authenticated = bool(authenticated)
        c.capabilities = set(capabilities or c.capabilities or ())
        c.connection_state = str(connection_state or "ONLINE").upper()
        c.last_seen = float(last_seen if last_seen is not None else time.time())
        self._save()
        return c

    def remove(self, agent_id):
        self._candidates.pop(str(agent_id), None)
        self._save()

    def get(self, agent_id):
        return self._candidates.get(str(agent_id))

    def all(self):
        return list(self._candidates.values())

    def by_dpn(self, physical_dpn_id):
        return [c for c in self._candidates.values()
                if c.physical_dpn_id == str(physical_dpn_id)]

    def touch(self, agent_id, last_seen=None, connection_state="ONLINE"):
        """Refresh a known Agent without changing its physical DPN identity."""
        c = self._candidates.get(str(agent_id))
        if c is None:
            return None
        c.last_seen = float(last_seen if last_seen is not None else time.time())
        c.connection_state = str(connection_state or "ONLINE").upper()
        self._save()
        return c

    def mark_failure(self, agent_id):
        c = self._candidates.get(str(agent_id))
        if c is not None:
            c.last_failure_at = time.time()
            self._save()


class DPNTransportPool:
    def __init__(self, registry=None, state_dir=None, stale_s=60.0, now_fn=time.time,
                 log_fn=None):
        self.registry = registry or PresenceRegistry(state_dir)
        self.stale_s = float(stale_s)
        self.now_fn = now_fn
        self.log = log_fn or (lambda *a, **k: None)
        self.failovers = []

    def get_candidates(self, physical_dpn_id, capability=None):
        now = self.now_fn()
        out = []
        for c in self.registry.by_dpn(physical_dpn_id):
            if not c.online(now, self.stale_s):
                continue
            if not c.authenticated:
                continue
            if capability and capability not in c.capabilities:
                continue
            out.append(c)
        out.sort(key=lambda c: (float(c.latency_ms), -float(c.last_success_at),
                                c.agent_id))
        return out

    def select_mutation_transport(self, physical_dpn_id, required_capability):
        candidates = self.get_candidates(physical_dpn_id, required_capability)
        return candidates[0] if candidates else None

    def select_read_transport(self, physical_dpn_id, capability=None):
        now = self.now_fn()
        candidates = []
        for c in self.registry.by_dpn(physical_dpn_id):
            if not c.online(now, self.stale_s):
                continue
            if not c.authenticated:
                continue
            if capability and capability not in c.capabilities:
                continue
            candidates.append(c)
        candidates.sort(key=lambda c: (float(c.latency_ms), -float(c.last_success_at),
                                       c.agent_id))
        return candidates[0] if candidates else None

    def read(self, physical_dpn_id, operation, call_fn, capability=None):
        candidates = []
        now = self.now_fn()
        for c in self.registry.by_dpn(physical_dpn_id):
            if c.online(now, self.stale_s) and c.authenticated and (
                    not capability or capability in c.capabilities):
                candidates.append(c)
        candidates.sort(key=lambda c: (float(c.latency_ms), -float(c.last_success_at),
                                       c.agent_id))
        last_exc = None
        for idx, c in enumerate(candidates):
            try:
                result = call_fn(c)
                c.last_success_at = self.now_fn()
                c.connection_state = "ONLINE"
                self.registry._save()
                return result, c
            except TransportUncertain:
                c.last_failure_at = self.now_fn()
                raise
            except Exception as e:
                c.last_failure_at = self.now_fn()
                last_exc = e
                nxt = candidates[idx + 1] if idx + 1 < len(candidates) else None
                self.failovers.append({"physical_dpn_id": physical_dpn_id,
                                       "operation": operation, "from_agent": c.agent_id,
                                       "to_agent": nxt.agent_id if nxt else None,
                                       "reason": type(e).__name__})
                if nxt is not None:
                    self.log("[DPN][FAILOVER] dpn=%s op=%s from=%s to=%s reason=%s" % (
                        physical_dpn_id, operation, c.agent_id, nxt.agent_id,
                        type(e).__name__))
                continue
        if last_exc is not None:
            raise last_exc
        raise ReadFailover("NO_READ_TRANSPORT")


    async def read_async(self, physical_dpn_id, operation, call_fn, capability=None):
        """Async read failover; call_fn is a coroutine function taking a Candidate.

        Safe failures (not dispatched) try the next Agent. TransportUncertain is
        propagated immediately and never retried.
        """
        candidates = []
        now = self.now_fn()
        for c in self.registry.by_dpn(physical_dpn_id):
            if c.online(now, self.stale_s) and c.authenticated and (
                    not capability or capability in c.capabilities):
                candidates.append(c)
        candidates.sort(key=lambda c: (float(c.latency_ms), -float(c.last_success_at),
                                       c.agent_id))
        last_exc = None
        for idx, c in enumerate(candidates):
            try:
                result = await call_fn(c)
                c.last_success_at = self.now_fn()
                c.connection_state = "ONLINE"
                self.registry._save()
                return result, c
            except TransportUncertain:
                c.last_failure_at = self.now_fn()
                raise
            except Exception as e:
                c.last_failure_at = self.now_fn()
                last_exc = e
                nxt = candidates[idx + 1] if idx + 1 < len(candidates) else None
                self.failovers.append({"physical_dpn_id": physical_dpn_id,
                                       "operation": operation, "from_agent": c.agent_id,
                                       "to_agent": nxt.agent_id if nxt else None,
                                       "reason": type(e).__name__})
                if nxt is not None:
                    self.log("[DPN][FAILOVER] dpn=%s op=%s from=%s to=%s reason=%s" % (
                        physical_dpn_id, operation, c.agent_id, nxt.agent_id,
                        type(e).__name__))
                continue
        if last_exc is not None:
            raise last_exc
        raise ReadFailover("NO_READ_TRANSPORT")

    def mark_failure(self, agent_id):
        c = self.registry.get(agent_id)
        if c:
            c.last_failure_at = self.now_fn()
            self.registry._save()
