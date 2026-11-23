#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.10A/B - DPN presence heartbeat feeds the controller transport pool."""
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from device_agent import protocol as P
from dpn.transport_pool import PresenceRegistry, DPNTransportPool


class TestAgentPresence(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.registry = PresenceRegistry()
        self.pool = DPNTransportPool(self.registry, stale_s=60)

    def tearDown(self):
        self._td.cleanup()

    def add(self, aid, latency, state="ONLINE"):
        return self.registry.upsert(
            aid, aid, "DPN_X", latency, True,
            capabilities=set(P.DPN_READ_OPS), connection_state=state)

    def test_same_physical_dpn_three_agents_preferred_by_latency(self):
        self.add("A", 31)
        self.add("B", 17)
        self.add("C", 24)
        self.assertEqual(self.pool.select_read_transport("DPN_X").agent_id, "B")
        self.assertEqual(len(self.registry.by_dpn("DPN_X")), 3)

    def test_read_failover_same_dpn(self):
        self.add("A", 10)
        self.add("B", 20)

        def call(c):
            if c.agent_id == "A":
                raise TimeoutError("read not dispatched")
            return {"ok": True}

        res, chosen = self.pool.read("DPN_X", "LIST_DPN_TUNNELS", call)
        self.assertTrue(res["ok"])
        self.assertEqual(chosen.agent_id, "B")
        self.assertEqual(self.pool.failovers[0]["to_agent"], "B")

    def test_offline_agent_not_selected(self):
        self.add("A", 1, state="OFFLINE")
        self.add("B", 10)
        self.assertEqual(self.pool.select_read_transport("DPN_X").agent_id, "B")

    def test_wrong_physical_id_not_indexed(self):
        self.assertIsNone(self.registry.upsert("A", "A", "", 10, True,
                                               capabilities=set(P.DPN_READ_OPS)))
        self.assertEqual(self.pool.registry.all(), [])


if __name__ == "__main__":
    unittest.main()
