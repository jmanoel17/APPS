#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.10B - presence registry and read transport pool."""
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn.transport_pool import (DPNTransportPool, PresenceRegistry, ReadFailover,
                                TransportUncertain)


class TestTransportPool(unittest.TestCase):
    def setUp(self):
        self.t = 1000.0
        self.reg = PresenceRegistry()
        self.pool = DPNTransportPool(self.reg, stale_s=60, now_fn=lambda: self.t)

    def add(self, aid, pdn="PDN1", latency=30, auth=True, caps=("DPN_TUNNELS_READ",),
            state="ONLINE", seen=None):
        return self.reg.upsert(aid, "device-" + aid, pdn, latency, auth,
                               capabilities=set(caps), connection_state=state,
                               last_seen=self.t if seen is None else seen)

    def test_lowest_latency_preferred(self):
        self.add("A", latency=31)
        self.add("B", latency=17)
        self.add("C", latency=24)
        self.assertEqual(self.pool.select_read_transport("PDN1").agent_id, "B")

    def test_offline_or_wrong_capability_skipped(self):
        self.add("A", latency=10, state="OFFLINE")
        self.add("B", latency=20, caps=("DPN_STATUS_READ",))
        self.add("C", latency=30)
        self.assertEqual(self.pool.select_read_transport(
            "PDN1", "DPN_TUNNELS_READ").agent_id, "C")
        self.assertIsNotNone(self.pool.select_read_transport("PDN1", "DPN_TUNNELS_READ"))
        self.assertIsNone(self.pool.select_read_transport("PDN1", "DPN_MISSING"))

    def test_stale_removed_from_selection(self):
        self.add("A", seen=self.t - 61)
        self.add("B", latency=99)
        self.assertEqual(self.pool.select_read_transport("PDN1").agent_id, "B")

    def test_safe_read_failover(self):
        self.add("A", latency=10)
        self.add("B", latency=20)
        calls = []

        def call(c):
            calls.append(c.agent_id)
            if c.agent_id == "A":
                raise TimeoutError("pre-dispatch")
            return "ok"

        result, chosen = self.pool.read("PDN1", "LIST_DPN_TUNNELS", call)
        self.assertEqual(result, "ok")
        self.assertEqual(chosen.agent_id, "B")
        self.assertEqual(calls, ["A", "B"])
        self.assertEqual(self.pool.failovers[0]["from_agent"], "A")
        self.assertEqual(self.pool.failovers[0]["to_agent"], "B")

    def test_uncertain_does_not_failover(self):
        self.add("A", latency=10)
        self.add("B", latency=20)

        def call(c):
            raise TransportUncertain("dispatched")

        with self.assertRaises(TransportUncertain):
            self.pool.read("PDN1", "LIST_DPN_TUNNELS", call)

    def test_invalid_physical_id_never_indexed(self):
        self.assertIsNone(self.reg.upsert("A", physical_dpn_id="", latency_ms=1))
        self.assertEqual(self.reg.all(), [])

    def test_registry_persists_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            reg = PresenceRegistry(d)
            reg.upsert("A", "dev", "PDN1", 12, True, {"DPN_TUNNELS_READ"})
            reg2 = PresenceRegistry(d)
            c = reg2.get("A")
            self.assertIsNotNone(c)
            self.assertEqual(c.physical_dpn_id, "PDN1")
            self.assertTrue(c.authenticated)


if __name__ == "__main__":
    unittest.main()
