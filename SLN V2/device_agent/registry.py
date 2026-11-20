#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""device_agent/registry.py — identidade/autenticação (token hash, revogação, epoch)."""
from __future__ import annotations

import hashlib
import hmac
import time

from .models import AgentRecord


def hash_token(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


class AgentRegistry:
    def __init__(self, store):
        self.store = store

    def get(self, agent_id):
        return self.store.get_agent(agent_id)

    def list(self):
        return self.store.list_agents()

    def authenticate(self, agent_id, token):
        rec = self.store.get_agent(agent_id)
        if rec is None or rec.revoked or not rec.token_hash:
            return False
        ok = hmac.compare_digest(rec.token_hash, hash_token(token))
        if ok:
            self.store.update_agent_meta(agent_id, last_seen=time.time())
        return ok

    def apply_hello(self, agent_id, capabilities=None, agent_version=None, sdk=None,
                    android_id=None, build_fingerprint=None, manufacturer=None, model=None,
                    display=None):
        self.store.update_agent_meta(
            agent_id, capabilities=capabilities, agent_version=agent_version, sdk=sdk,
            android_id=android_id, build_fingerprint=build_fingerprint,
            manufacturer=manufacturer, model=model, last_seen=time.time(), display=display)

    def revoke(self, agent_id):
        self.store.set_revoked(agent_id, True)
        self.store.add_event(agent_id, "revoked")

    def possible_reenrollment(self, agent_id):
        rec = self.get(agent_id)
        return bool(rec and rec.possible_reenrollment)
