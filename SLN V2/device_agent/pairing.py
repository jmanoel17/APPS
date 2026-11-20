#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""device_agent/pairing.py — pairing code (aleatório, uso único, TTL) + token individual."""
from __future__ import annotations

import secrets
import time

from . import errors
from .models import AgentRecord
from .registry import hash_token


class PairingManager:
    def __init__(self, store, ttl_s=300.0):
        self.store = store
        self.ttl_s = float(ttl_s)

    def open(self, intent="", ttl_s=None):
        code = secrets.token_urlsafe(9)
        now = time.time()
        self.store.put_pairing(hash_token(code), intent, now, now + float(ttl_s or self.ttl_s))
        return code

    def consume(self, code, agent_id, info=None):
        info = info or {}
        h = hash_token(code)
        p = self.store.get_pairing(h)
        if p is None:
            return {"ok": False, "error": errors.PAIRING_INVALID}
        if p.get("used"):
            return {"ok": False, "error": errors.PAIRING_USED}
        if time.time() > float(p.get("expires_at") or 0):
            return {"ok": False, "error": errors.PAIRING_EXPIRED}
        token = secrets.token_urlsafe(32)
        now = time.time()
        rec = AgentRecord(
            agent_id=agent_id, token_hash=hash_token(token), created_at=now, last_seen=now,
            android_id=str(info.get("android_id") or ""),
            build_fingerprint=str(info.get("build_fingerprint") or ""),
            manufacturer=str(info.get("manufacturer") or ""),
            model=str(info.get("model") or ""), sdk=int(info.get("sdk") or 0),
            agent_version=str(info.get("agent_version") or ""),
            capabilities=dict(info.get("capabilities") or {}),
            aliases={"intent": p.get("intent") or ""})
        others = [a for a in self.store.find_by_alias(rec.android_id, rec.build_fingerprint)
                  if a.agent_id != agent_id]
        rec.possible_reenrollment = bool(others)
        self.store.put_agent(rec)
        self.store.mark_pairing_used(h, agent_id)
        self.store.add_event(agent_id, "pair_ok",
                             "possible_reenrollment" if rec.possible_reenrollment else "")
        return {"ok": True, "token": token,
                "possible_reenrollment": rec.possible_reenrollment}
