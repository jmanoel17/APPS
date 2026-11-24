#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-writer DPN mutation leases (keyed by physical_dpn_id)."""
from __future__ import annotations

import os
import threading
import time
import uuid


class LeaseBusy(Exception):
    code = "LEASE_BUSY"


class DPNMutationLeaseManager:
    def __init__(self, state_dir=None, ttl_s=30.0, now_fn=time.time):
        self.state_dir = state_dir
        self.ttl_s = float(ttl_s)
        self.now_fn = now_fn
        self._lock = threading.RLock()
        self._leases = {}
        self._path = os.path.join(state_dir, "mutation_leases.json") if state_dir else None
        if self._path and os.path.exists(self._path):
            try:
                from . import state as S
                data = S.read_json(self._path, {}) or {}
                self._leases = dict(data.get("leases") or {})
                now = self.now_fn()
                for lease in self._leases.values():
                    if lease.get("state") in ("ACQUIRED", "BUSY"):
                        lease["state"] = "UNCERTAIN"
                        lease["expires_at"] = max(float(lease.get("expires_at") or 0),
                                                  now + self.ttl_s)
            except Exception:
                self._leases = {}

    def _save(self):
        if not self._path:
            return
        try:
            from . import state as S
            S.atomic_write_json(self._path, {"version": 1, "leases": self._leases})
        except Exception:
            pass

    def _expire_locked(self):
        now = self.now_fn()
        changed = False
        for key, lease in list(self._leases.items()):
            if lease.get("state") in ("ACQUIRED", "BUSY", "UNCERTAIN") and now >= float(lease.get("expires_at") or 0):
                lease["state"] = "EXPIRED"
                changed = True
        if changed:
            self._save()

    def acquire(self, physical_dpn_id, holder_agent_id, operation, command_id,
                ttl_s=None):
        key = str(physical_dpn_id or "")
        if not key or not holder_agent_id:
            raise LeaseBusy("physical_dpn_id/holder ausente")
        with self._lock:
            self._expire_locked()
            cur = self._leases.get(key)
            if cur and cur.get("state") in ("ACQUIRED", "BUSY", "UNCERTAIN"):
                raise LeaseBusy(cur.get("lease_id") or "BUSY")
            now = self.now_fn()
            lease = {
                "physical_dpn_id": key,
                "lease_id": uuid.uuid4().hex,
                "holder_agent_id": str(holder_agent_id),
                "generation": int((cur or {}).get("generation", 0)) + 1,
                "operation": str(operation),
                "command_id": str(command_id or ""),
                "issued_at": now,
                "expires_at": now + float(ttl_s if ttl_s is not None else self.ttl_s),
                "state": "ACQUIRED",
            }
            self._leases[key] = lease
            self._save()
            return dict(lease)

    def release(self, lease_id, state="RELEASED"):
        with self._lock:
            for key, lease in self._leases.items():
                if lease.get("lease_id") == lease_id:
                    lease["state"] = str(state)
                    lease["released_at"] = self.now_fn()
                    self._save()
                    return dict(lease)
            return None

    def get(self, physical_dpn_id):
        with self._lock:
            self._expire_locked()
            lease = self._leases.get(str(physical_dpn_id))
            return dict(lease) if lease else None

    def snapshot(self):
        with self._lock:
            self._expire_locked()
            return {k: dict(v) for k, v in self._leases.items()}
