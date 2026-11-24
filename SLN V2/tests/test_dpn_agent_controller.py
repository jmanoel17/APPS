#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Controller-side unified Device Agent pool (read-only)."""
import asyncio
import os
import sys
import tempfile
import time
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from device_agent import protocol as P
from dpn.agent_pool import AgentPoolClient
from dpn.transport_pool import PresenceRegistry
import dpn.ui_model as UI


def agent(aid, dpn="DPN_X", status="ONLINE", latency=10, caps=True, seen=None):
    return {"agent_id": aid, "status": status, "latency_ms": latency,
            "last_seen": seen if seen is not None else time.time(),
            "agent_version": "mock",
            "capabilities": {op: caps for op in P.DPN_READ_OPS}}


class FakeServer:
    def __init__(self, agents, identities=None, reads=None):
        self.agents = agents
        self.identities = identities or {}
        self.reads = reads or {}
        self.calls = []

    def snapshot(self):
        return {"agents": list(self.agents), "connections": len(self.agents),
                "pending": 0, "metrics": {}}

    async def send_command(self, agent_id, op, params=None, timeout=None, **kw):
        self.calls.append((agent_id, op))
        if op == P.GET_DPN_IDENTITY:
            v = self.identities.get(agent_id)
            if isinstance(v, Exception):
                raise v
            if v is None:
                return {"status": "failure", "error": "no_identity"}
            return {"status": "success", "data": {
                "physical_dpn_id": v, "latency_ms": 12, "reachable": True}}
        v = self.reads.get((agent_id, op), {"status": "success", "data": {"ok": agent_id}})
        if isinstance(v, Exception):
            raise v
        return v


class TestAgentPool(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.reg = PresenceRegistry()

    def tearDown(self):
        self._td.cleanup()

    def test_refresh_groups_same_physical_dpn(self):
        srv = FakeServer([agent("A"), agent("B"), agent("C")],
                         identities={"A": "DPN_X", "B": "DPN_X", "C": "DPN_Y"})
        c = AgentPoolClient(srv, self.reg, cfg={"dpn_agent_refresh_max": 8})
        rows = asyncio.run(c.refresh_identities())
        self.assertEqual(sum(1 for r in rows if r["status"] == "ok"), 3)
        self.assertEqual(len(self.reg.by_dpn("DPN_X")), 2)
        self.assertEqual(len(self.reg.by_dpn("DPN_Y")), 1)
        self.assertTrue(c.pool.select_read_transport("DPN_X").agent_id in ("A", "B"))

    def test_refresh_skips_offline_and_no_credentials(self):
        srv = FakeServer([agent("A", status="OFFLINE"),
                          agent("B", caps=False), agent("C")],
                         identities={"C": "DPN_X"})
        c = AgentPoolClient(srv, self.reg, cfg={"dpn_agent_refresh_max": 8})
        rows = asyncio.run(c.refresh_identities())
        st = {r["agent_id"]: r["status"] for r in rows}
        self.assertEqual(st["A"], "offline")
        self.assertEqual(st["B"], "no_dpn_credentials")
        self.assertEqual(st["C"], "ok")

    def test_read_uses_same_dpn_failover(self):
        srv = FakeServer([agent("A", latency=10), agent("B", latency=20)],
                         reads={("A", P.LIST_DPN_TUNNELS): {"status": "failure",
                                                           "error": "agent_offline"}})
        c = AgentPoolClient(srv, self.reg, cfg={"dpn_agent_refresh_max": 8})
        self.reg.upsert("A", "A", "DPN_X", 10, True, set(P.DPN_READ_OPS))
        self.reg.upsert("B", "B", "DPN_X", 20, True, set(P.DPN_READ_OPS))
        data, chosen = asyncio.run(c.read("DPN_X", P.LIST_DPN_TUNNELS))
        self.assertEqual(chosen.agent_id, "B")
        self.assertEqual(data["ok"], "B")
        self.assertEqual(c.pool.failovers[0]["from_agent"], "A")

    def test_read_rejects_non_dpn_op(self):
        c = AgentPoolClient(FakeServer([]), self.reg)
        with self.assertRaises(ValueError):
            asyncio.run(c.read("DPN_X", P.TAP))

    def test_status_groups_and_unknown_pending(self):
        srv = FakeServer([agent("A"), agent("B")])
        c = AgentPoolClient(srv, self.reg)
        self.reg.upsert("A", "A", "DPN_X", 15, True, set(P.DPN_READ_OPS))
        st = c.status()
        self.assertEqual(len(st["agents"]), 2)
        self.assertEqual(st["dpns"]["DPN_X"]["agents"], 1)
        self.assertEqual(st["dpns"]["DPN_X"]["best_agent_id"], "A")
        b = next(r for r in st["agents"] if r["agent_id"] == "B")
        self.assertTrue(b["dpn_ready"])
        self.assertEqual(b["error"], "identity_pending")


class TestAgentUiModel(unittest.TestCase):
    def test_agent_rows_and_summary(self):
        st = {"updated_at": 100, "agents": [
            {"agent_id": "A", "physical_dpn_id": "DPN_X", "status": "ONLINE",
             "authenticated": True, "dpn_ready": True, "latency_ms": 12,
             "last_seen": 1, "error": ""}],
            "dpns": {"DPN_X": {"agents": 1, "online": 1, "authenticated": 1,
                               "best_agent_id": "A", "latency_ms": 12}}}
        rows = UI.agent_rows(st)
        self.assertEqual(rows[0]["agent_id"], "A")
        self.assertEqual(UI.agent_dpn_rows(st)[0]["best_agent_id"], "A")
        self.assertTrue(UI.agent_controller_online(st, now=105, stale_s=8))


if __name__ == "__main__":
    unittest.main()
