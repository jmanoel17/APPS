#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-physical-DPN authentication serialization and backoff."""
from __future__ import annotations

import asyncio
import time


AUTH_CODES = frozenset(("DPN_UNAUTHORIZED", "DPN_AUTH_BACKOFF", "AUTH_ERROR",
                        "DPN_AUTH_FAILED"))


class AuthBackoff(Exception):
    code = "AUTH_BACKOFF"


class DPNAuthCoordinator:
    def __init__(self, backoff_s=300.0, now_fn=time.time):
        self.backoff_s = float(backoff_s)
        self.now_fn = now_fn
        self._locks = {}
        self._backoff = {}
        self._attempts = {}

    def lock(self, physical_dpn_id):
        key = str(physical_dpn_id or "GLOBAL")
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def blocked_until(self, physical_dpn_id):
        return float((self._backoff.get(str(physical_dpn_id)) or {}).get("backoff_until", 0))

    def is_blocked(self, physical_dpn_id):
        return self.now_fn() < self.blocked_until(physical_dpn_id)

    def note_failure(self, physical_dpn_id, reason="AUTH_BACKOFF"):
        key = str(physical_dpn_id or "GLOBAL")
        attempts = int(self._attempts.get(key, 0)) + 1
        self._attempts[key] = attempts
        self._backoff[key] = {
            "backoff_until": self.now_fn() + self.backoff_s,
            "reason": str(reason),
            "attempt_count": attempts,
        }
        return dict(self._backoff[key])

    def note_success(self, physical_dpn_id):
        key = str(physical_dpn_id or "GLOBAL")
        self._attempts[key] = 0
        self._backoff.pop(key, None)

    def snapshot(self):
        return {k: dict(v) for k, v in self._backoff.items()}
