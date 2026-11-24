#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.10C/D - DPN mutations through the unified Agent pool."""
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
from dpn.auth import AuthBackoff, DPNAuthCoordinator
from dpn.mutation_lease import DPNMutationLeaseManager, LeaseBusy
from dpn.provider_deeper import DeeperConnectProvider
from dpn.provider_factory import build_provider
from dpn.transport_pool import PresenceRegistry, ReadFailover, TransportUncertain


def agent(aid, status="ONLINE", latency=10):
    return {"agent_id": aid, "status": status, "latency_ms": latency,
            "last_seen": time.time(), "agent_version": "0.5",
            "capabilities": {op: True for op in P.OPS}}


class FakeServer:
    def __init__(self, agents, script=None):
        self.agents = agents
        self.script = script or {}
        self.calls = []

    def snapshot(self):
        return {"agents": list(self.agents), "connections": len(self.agents),
                "pending": 0, "metrics": {}}

    async def send_command(self, agent_id, op, params=None, timeout=None,
                           command_id=None, **kw):
        self.calls.append((agent_id, op, dict(params or {})))
        r = self.script.get((agent_id, op))
        if callable(r):
            r = r()
        if isinstance(r, Exception):
            raise r
        if r is None:
            r = {"status": "success", "data": {"ok": agent_id}}
        if "agent_id" not in r:
            r = dict(r)
            r["agent_id"] = agent_id
        return r


class PoolBase(unittest.TestCase):
    def setUp(self):
        self.reg = PresenceRegistry()
        self.pool = None
        self.lease = DPNMutationLeaseManager(ttl_s=30)
        self.auth = DPNAuthCoordinator(backoff_s=60)
        self.t = time.time()
        for aid, lat in (("A", 10), ("B", 20), ("C", 30)):
            self.reg.upsert(aid, aid, "DPN_X", lat, True,
                            set(P.DPN_READ_OPS) | set(P.DPN_MUTATING_OPS),
                            last_seen=self.t)

    def client(self, server):
        return AgentPoolClient(server, self.reg, cfg={"dpn_agent_request_timeout_s": 2},
                               lease=self.lease, auth=self.auth)


class TestMutationLease(unittest.TestCase):
    def test_one_lease_per_physical_and_expiry(self):
        now = [100.0]
        lm = DPNMutationLeaseManager(ttl_s=10, now_fn=lambda: now[0])
        a = lm.acquire("DPN_X", "A", "switch", "c1")
        with self.assertRaises(LeaseBusy):
            lm.acquire("DPN_X", "B", "refresh", "c2")
        with self.assertRaises(LeaseBusy):
            lm.acquire("DPN_X", "A", "switch", "c1")
        now[0] = 111.0
        b = lm.acquire("DPN_X", "B", "refresh", "c2")
        self.assertGreater(b["generation"], a["generation"])
        lm.release(b["lease_id"])
        self.assertEqual(lm.get("DPN_X")["state"], "RELEASED")

    def test_controller_restart_keeps_acquired_lease_uncertain(self):
        with tempfile.TemporaryDirectory() as d:
            a = DPNMutationLeaseManager(state_dir=d, ttl_s=30)
            lease = a.acquire("DPN_X", "A", "switch", "c1")
            b = DPNMutationLeaseManager(state_dir=d, ttl_s=30)
            self.assertEqual(b.get("DPN_X")["state"], "UNCERTAIN")
            with self.assertRaises(LeaseBusy):
                b.acquire("DPN_X", "B", "switch", "c2")
            b.release(lease["lease_id"], "RELEASED")
            self.assertEqual(b.acquire("DPN_X", "B", "switch", "c3")["holder_agent_id"], "B")


class TestPoolMutation(PoolBase):
    def test_pre_dispatch_failure_fails_over_to_next_agent(self):
        srv = FakeServer([agent("A"), agent("B")], {
            ("A", P.DPN_SWITCH_NODE): {"status": "failure", "error": "AGENT_OFFLINE"},
            ("B", P.DPN_SWITCH_NODE): {"status": "success", "data": {"result": "REQUESTED"}},
        })
        c = self.client(srv)
        data, chosen = asyncio.run(c.execute_mutation(
            "DPN_X", P.DPN_SWITCH_NODE,
            {"expected_dpn_id": "DPN_X", "tunnel_code": "UB", "current_ip": "1.1.1.1"},
            P.DPN_SWITCH_NODE, command_id="logical-1"))
        self.assertEqual(chosen.agent_id, "B")
        self.assertEqual(data["result"], "REQUESTED")
        self.assertEqual([x[0] for x in srv.calls], ["A", "B"])
        self.assertEqual(self.lease.get("DPN_X")["state"], "RELEASED")

    def test_same_logical_command_id_reuses_success_without_second_dispatch(self):
        srv = FakeServer([agent("A")], {
            ("A", P.DPN_SWITCH_NODE): {"status": "success", "data": {"result": "REQUESTED"}},
        })
        c = self.client(srv)
        params = {"expected_dpn_id": "DPN_X", "tunnel_code": "UB",
                  "current_ip": "1.1.1.1"}
        a = asyncio.run(c.execute_mutation("DPN_X", P.DPN_SWITCH_NODE, params,
                                           P.DPN_SWITCH_NODE, command_id="dedupe-1"))
        b = asyncio.run(c.execute_mutation("DPN_X", P.DPN_SWITCH_NODE, params,
                                           P.DPN_SWITCH_NODE, command_id="dedupe-1"))
        self.assertEqual(a[0]["result"], b[0]["result"])
        self.assertEqual(len(srv.calls), 1)

    def test_post_dispatch_uncertain_never_fails_over(self):
        srv = FakeServer([agent("A"), agent("B")], {
            ("A", P.DPN_SWITCH_NODE): {"status": "uncertain", "error": "CONNECTION_LOST"},
            ("B", P.DPN_SWITCH_NODE): {"status": "success", "data": {}},
        })
        c = self.client(srv)
        with self.assertRaises(TransportUncertain):
            asyncio.run(c.execute_mutation(
                "DPN_X", P.DPN_SWITCH_NODE,
                {"expected_dpn_id": "DPN_X", "tunnel_code": "UB", "current_ip": "1.1.1.1"},
                P.DPN_SWITCH_NODE, command_id="logical-2"))
        self.assertEqual([x[0] for x in srv.calls], ["A"])
        with self.assertRaises(TransportUncertain):
            asyncio.run(c.execute_mutation(
                "DPN_X", P.DPN_SWITCH_NODE,
                {"expected_dpn_id": "DPN_X", "tunnel_code": "UB", "current_ip": "1.1.1.1"},
                P.DPN_SWITCH_NODE, command_id="logical-2"))
        self.assertEqual(len(srv.calls), 1)

    def test_lease_busy_prevents_second_mutation(self):
        srv = FakeServer([agent("A")])
        c = self.client(srv)
        self.lease.acquire("DPN_X", "A", "switch", "x")
        with self.assertRaises(LeaseBusy):
            asyncio.run(c.execute_mutation(
                "DPN_X", P.DPN_SWITCH_NODE,
                {"expected_dpn_id": "DPN_X", "tunnel_code": "UB", "current_ip": "1.1.1.1"},
                P.DPN_SWITCH_NODE, command_id="x2"))
        self.assertEqual(srv.calls, [])

    def test_auth_failure_sets_shared_backoff(self):
        srv = FakeServer([agent("A"), agent("B")], {
            ("A", P.DPN_SWITCH_NODE): {"status": "failure", "error": "DPN_UNAUTHORIZED"},
        })
        c = self.client(srv)
        with self.assertRaises(AuthBackoff):
            asyncio.run(c.execute_mutation(
                "DPN_X", P.DPN_SWITCH_NODE,
                {"expected_dpn_id": "DPN_X", "tunnel_code": "UB", "current_ip": "1.1.1.1"},
                P.DPN_SWITCH_NODE, command_id="auth1"))
        self.assertTrue(c.auth.is_blocked("DPN_X"))
        with self.assertRaises(AuthBackoff):
            asyncio.run(c.execute_mutation(
                "DPN_X", P.DPN_SWITCH_NODE,
                {"expected_dpn_id": "DPN_X", "tunnel_code": "UB", "current_ip": "1.1.1.1"},
                P.DPN_SWITCH_NODE, command_id="auth2"))

    def test_reconcile_via_other_agent_read(self):
        srv = FakeServer([agent("A")], {
            ("A", P.LIST_DPN_TUNNELS): {"status": "success", "data": {"tunnels": [
                {"tunnelCode": "UB", "activeIp": "2.2.2.2"}]}},
        })
        c = self.client(srv)
        r = asyncio.run(c.reconcile_switch("DPN_X", "UB", "1.1.1.1"))
        self.assertEqual(r["result"], "APPLIED")
        self.assertEqual(r["current_ip"], "2.2.2.2")

    def test_missing_capability_excluded(self):
        reg = PresenceRegistry()
        reg.upsert("A", "A", "DPN_X", 10, True, set(P.DPN_READ_OPS))
        srv = FakeServer([agent("A")])
        c = AgentPoolClient(srv, reg, cfg={}, lease=self.lease, auth=self.auth)
        with self.assertRaises(ReadFailover):
            asyncio.run(c.execute_mutation(
                "DPN_X", P.DPN_SWITCH_NODE,
                {"expected_dpn_id": "DPN_X", "tunnel_code": "UB", "current_ip": "1.1.1.1"},
                P.DPN_SWITCH_NODE))


class FakePool:
    """Sync fake used only to verify DeeperConnectProvider transport wiring."""
    def __init__(self):
        self.pool = self
        self.reads = []
        self.mutations = []

    def select_read_transport(self, physical, capability=None):
        return object()

    async def read(self, physical, op):
        self.reads.append((physical, op))
        if op == P.LIST_DPN_TUNNELS:
            return {"tunnels": [{"tunnelCode": "UB", "activeIp": "1.1.1.1"}]}, object()
        if op == P.GET_DPN_NODE_LB_MODE:
            return {"mode": "none"}, object()
        return {"ok": True}, object()

    async def execute_mutation(self, physical, op, params, capability):
        self.mutations.append((physical, op, params))
        return {"result": "REQUESTED"}, object()


class TestProviderPool(unittest.TestCase):
    def test_factory_and_rotate_refresh_use_injected_pool(self):
        pool = FakePool()
        p = build_provider("deeper_connect", {
            "type": "deeper_connect", "transport": "agent_pool",
            "expected_dpn_id": "DPN_X", "tunnel_code": "UB", "address": "34.34.34.34",
        }, agent_pool=pool)
        self.assertEqual(p.transport, "agent_pool")
        self.assertEqual(p.get_public_ip(), "1.1.1.1")
        p.rotate_ip("REGULAR")
        p.refresh_route_set()
        self.assertEqual(pool.mutations[0][1], P.DPN_SWITCH_NODE)
        self.assertEqual(pool.mutations[1][1], P.DPN_REFRESH_ROUTE_SET)


class TestAuthCoordinator(unittest.TestCase):
    def test_one_auth_attempt_per_physical_dpn(self):
        async def run():
            auth = DPNAuthCoordinator(backoff_s=60)
            active = [0]
            peak = [0]

            async def one():
                async with auth.lock("DPN_X"):
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                    await asyncio.sleep(0.02)
                    active[0] -= 1

            await asyncio.gather(one(), one(), one())
            return peak[0]

        self.assertEqual(asyncio.run(run()), 1)

    def test_too_many_attempts_shared_backoff(self):
        srv = FakeServer([agent("A"), agent("B")], {
            ("A", P.DPN_SWITCH_NODE): {"status": "failure",
                                       "error": "DPN_AUTH_BACKOFF"},
        })
        reg = PresenceRegistry()
        for aid in ("A", "B"):
            reg.upsert(aid, aid, "DPN_X", 10, True,
                       set(P.DPN_READ_OPS) | set(P.DPN_MUTATING_OPS))
        c = AgentPoolClient(srv, reg, cfg={}, lease=DPNMutationLeaseManager(),
                            auth=DPNAuthCoordinator(backoff_s=60))
        with self.assertRaises(AuthBackoff):
            asyncio.run(c.execute_mutation(
                "DPN_X", P.DPN_SWITCH_NODE,
                {"expected_dpn_id": "DPN_X", "tunnel_code": "UB",
                 "current_ip": "1.1.1.1"}, P.DPN_SWITCH_NODE))
        self.assertTrue(c.auth.is_blocked("DPN_X"))
        # B is in the same physical DPN and must respect the same backoff.
        with self.assertRaises(AuthBackoff):
            asyncio.run(c.execute_mutation(
                "DPN_X", P.DPN_SWITCH_NODE,
                {"expected_dpn_id": "DPN_X", "tunnel_code": "UB",
                 "current_ip": "1.1.1.1"}, P.DPN_SWITCH_NODE))
        self.assertEqual(len(srv.calls), 1)


class TestProtocolAndApkContract(unittest.TestCase):
    def test_protocol_mutating_ops_and_validation(self):
        self.assertIn(P.DPN_SWITCH_NODE, P.OPS)
        self.assertIn(P.DPN_REFRESH_ROUTE_SET, P.MUTATING_OPS)
        good = {"expected_dpn_id": "DPN_X", "tunnel_code": "BR",
                "current_ip": "1.1.1.1"}
        self.assertIsNone(P.validate_params(P.DPN_SWITCH_NODE, good))
        self.assertIsNone(P.validate_params(P.DPN_REFRESH_ROUTE_SET,
                                            {"expected_dpn_id": "DPN_X",
                                             "tunnel_code": "BR"}))
        bad = dict(good, current_ip="not-an-ip")
        self.assertIsNotNone(P.validate_params(P.DPN_SWITCH_NODE, bad))
        self.assertIsNotNone(P.validate_params(P.DPN_REFRESH_ROUTE_SET,
                                               {"expected_dpn_id": "", "tunnel_code": "BR"}))
        self.assertIsNotNone(P.validate_params(P.DPN_SWITCH_NODE,
                                               {"expected_dpn_id": "DPN_X",
                                                "tunnel_code": "../x",
                                                "current_ip": "1.1.1.1"}))

    def test_apk_declares_mutation_ops(self):
        base = os.path.join(PROJ, "device_agent_apk", "app", "src", "main", "java",
                            "com", "sln", "deviceagent")
        proto = open(os.path.join(base, "Proto.kt"), encoding="utf-8").read()
        client = open(os.path.join(base, "DpnClient.kt"), encoding="utf-8").read()
        self.assertIn("DPN_SWITCH_NODE", proto)
        self.assertIn("DPN_REFRESH_ROUTE_SET", proto)
        self.assertIn("switchNode", client)
        self.assertIn("refreshRouteSet", client)
        self.assertIn("DPN_IDENTITY_MISMATCH", client)
        self.assertIn("IP_NOT_CHANGED", client)


class TestAutoRegister(unittest.TestCase):
    def test_auto_groups_use_known_catalog_and_members(self):
        from dpn.auto_register import build_groups
        catalog = {"DPN_01": {
            "group_id": "DPN_01", "tunnel_code": "UB",
            "expected_dpn_id": "PHYS_1",
            "controller": {"transport": "agent", "agent_id": "legacy",
                           "address": "34.34.34.34", "tunnel_code": "UB",
                           "expected_dpn_id": "PHYS_1"}}}
        status = {"agents": [
            {"agent_id": "a1", "status": "ONLINE", "physical_dpn_id": "PHYS_1",
             "lan_ip": "192.168.1.10", "authenticated": False},
            {"agent_id": "a2", "status": "ONLINE", "physical_dpn_id": "PHYS_1",
             "lan_ip": "192.168.1.11", "authenticated": True},
            {"agent_id": "a3", "status": "ONLINE", "physical_dpn_id": "PHYS_UNKNOWN",
             "lan_ip": "192.168.1.12"},
        ]}
        groups, meta = build_groups(status, catalog)
        self.assertEqual(groups["DPN_01"]["controller"]["transport"], "agent_pool")
        self.assertEqual(groups["DPN_01"]["controller"]["expected_dpn_id"], "PHYS_1")
        self.assertEqual(groups["DPN_01"]["members"], ["192.168.1.10:5555",
                                                       "192.168.1.11:5555"])
        unknown = next(g for g in groups.values() if g["expected_dpn_id"] == "PHYS_UNKNOWN")
        self.assertFalse(unknown["enabled"])
        self.assertEqual(meta["known"], 1)
        self.assertEqual(meta["unknown"], 1)
        self.assertEqual(meta["members"], 3)


    def test_required_fixture_keeps_online_unauthenticated_member(self):
        from dpn.auto_register import build_groups
        catalog = {"DPN_01": {"group_id": "DPN_01", "tunnel_code": "UB",
                              "expected_dpn_id": "DPN1_ID",
                              "controller": {"expected_dpn_id": "DPN1_ID",
                                             "tunnel_code": "UB"}}}
        status = {"agents": [
            {"status": "ONLINE", "lan_ip": "192.168.1.50",
             "physical_dpn_id": "DPN1_ID", "authenticated": False},
            {"status": "ONLINE", "lan_ip": "192.168.1.76",
             "physical_dpn_id": "DPN1_ID", "authenticated": True},
            {"status": "OFFLINE", "lan_ip": None, "physical_dpn_id": "DPN1_ID"},
        ]}
        groups, _ = build_groups(status, catalog)
        self.assertEqual(groups["DPN_01"]["members"],
                         ["192.168.1.50:5555", "192.168.1.76:5555"])

    def test_auto_groups_writes_json(self):
        import json, tempfile
        from dpn.auto_register import build_groups, write_groups
        groups, _ = build_groups({"agents": []}, {})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "groups.json")
            write_groups(path, groups)
            self.assertEqual(json.load(open(path, encoding="utf-8")), {})


if __name__ == "__main__":
    unittest.main()
