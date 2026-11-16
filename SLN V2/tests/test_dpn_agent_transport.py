#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2.AGENT.1 — transporte AGENT por Ops (mockado). Sem Android/AgentServer real."""
import hashlib
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn.atomos_ops import (
    AgentOfflineError, AgentTimeout, AgentServerPortInUse, AgentHubBusy,
    AgentNotStarted, AgentResponseMismatch, TLSIdentityError,
)
from dpn.atomos_core.errors import AuthError, UncertainError, IdentityMismatchError
from dpn.agent_transport import AgentAtomOS, AgentHub, DPNControlSQLiteRegistry
from dpn.provider_deeper import DeeperConnectProvider


class FakeResult:
    def __init__(self, status="success", data=None, error="", reason="",
                 agent_id=None, request_id="rid", observed_dpn_id=None):
        self.status = status
        self.data = data
        self.error = error
        self.reason = reason
        self.agent_id = agent_id
        self.request_id = request_id
        self.observed_dpn_id = observed_dpn_id


class FakeHub:
    def __init__(self):
        self.online_map = {}
        self.calls = []
        self.script = {}
        self.send_exc = None

    def set_online(self, agent_id, v=True):
        self.online_map[agent_id] = v

    def online(self, agent_id):
        return self.online_map.get(agent_id, False)

    def script_op(self, agent_id, op, result):
        self.script[(agent_id, op)] = result

    def send(self, agent_id, op, params=None, expected_dpn_id=None, timeout=None):
        self.calls.append((agent_id, op, dict(params or {}), expected_dpn_id))
        if self.send_exc is not None:
            raise self.send_exc
        r = self.script.get((agent_id, op))
        if callable(r):
            r = r()
        if r is None:
            return FakeResult(status="success", data={}, agent_id=agent_id)
        if isinstance(r, Exception):
            raise r
        if r.agent_id is None:
            r.agent_id = agent_id
        return r


def _agent(hub, aid, expected=None):
    hub.set_online(aid, True)
    return AgentAtomOS(hub, aid, expected_dpn_id=expected, timeout=5)


class TestAgentAtomOS(unittest.TestCase):
    def test_01_dpn01_agentA_get_device_id(self):
        hub = FakeHub()
        hub.script_op("agentA", "get_device_id", FakeResult(data={"deviceId": "A"}))
        a = _agent(hub, "agentA")
        self.assertEqual(a.get_device_id(), {"deviceId": "A"})
        self.assertEqual(hub.calls[0][0], "agentA")
        self.assertEqual(hub.calls[0][1], "get_device_id")

    def test_02_dpn02_agentB_get_device_id(self):
        hub = FakeHub()
        hub.script_op("agentB", "get_device_id", FakeResult(data={"deviceId": "B"}))
        a = _agent(hub, "agentB")
        self.assertEqual(a.get_device_id(), {"deviceId": "B"})
        self.assertEqual(hub.calls[0][0], "agentB")

    def test_03_simultaneous_isolated(self):
        hA, hB = FakeHub(), FakeHub()
        hA.script_op("agentA", "get_device_id", FakeResult(data={"deviceId": "A"}))
        hB.script_op("agentB", "get_device_id", FakeResult(data={"deviceId": "B"}))
        a = _agent(hA, "agentA")
        b = _agent(hB, "agentB")
        out = {}

        def runa():
            out["A"] = a.get_device_id()

        def runb():
            out["B"] = b.get_device_id()

        t1 = threading.Thread(target=runa)
        t2 = threading.Thread(target=runb)
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(out["A"], {"deviceId": "A"})
        self.assertEqual(out["B"], {"deviceId": "B"})
        self.assertTrue(all(c[0] == "agentA" for c in hA.calls))
        self.assertTrue(all(c[0] == "agentB" for c in hB.calls))

    def test_04_list_tunnels(self):
        hub = FakeHub()
        data = [{"tunnelCode": "US", "activeIp": "1.1.1.1"}]
        hub.script_op("a", "list_tunnels", FakeResult(data=data))
        self.assertEqual(_agent(hub, "a").get_exit_ip(), data)

    def test_05_node_lb_mode_get(self):
        hub = FakeHub()
        hub.script_op("a", "node_lb_mode_get", FakeResult(data={"mode": "none"}))
        self.assertEqual(_agent(hub, "a").get_node_lb_mode(), {"mode": "none"})

    def test_06_switch_node_mapping(self):
        hub = FakeHub()
        _agent(hub, "a").switch_node("US", "1.1.1.1")
        self.assertEqual(hub.calls[0][1], "switch_node")
        self.assertEqual(hub.calls[0][2], {"tunnelCode": "US", "currentIp": "1.1.1.1"})

    def test_07_refresh_tunnel_mapping(self):
        hub = FakeHub()
        _agent(hub, "a").refresh_tunnel("US")
        self.assertEqual(hub.calls[0][1], "refresh_tunnel")
        self.assertEqual(hub.calls[0][2], {"tunnelCode": "US"})

    def test_08_success(self):
        hub = FakeHub()
        hub.script_op("a", "get_dpn_mode", FakeResult(data={"dpnMode": "smart"}))
        self.assertEqual(_agent(hub, "a").get_node_type(), {"dpnMode": "smart"})

    def test_09_failure_auth(self):
        hub = FakeHub()
        hub.script_op("a", "get_device_id", FakeResult(status="failure", reason="auth_failed"))
        with self.assertRaises(AuthError):
            _agent(hub, "a").get_device_id()

    def test_10_uncertain(self):
        hub = FakeHub()
        hub.script_op("a", "switch_node", FakeResult(status="uncertain", error="sem confirmação"))
        with self.assertRaises(UncertainError):
            _agent(hub, "a").switch_node("US", "1.1.1.1")

    def test_11_expired(self):
        hub = FakeHub()
        hub.script_op("a", "get_device_id", FakeResult(status="expired"))
        with self.assertRaises(AgentTimeout):
            _agent(hub, "a").get_device_id()

    def test_12_agent_offline(self):
        hub = FakeHub()
        a = AgentAtomOS(hub, "off", timeout=2)
        with self.assertRaises(AgentOfflineError):
            a.get_device_id()

    def test_13_identity_mismatch(self):
        hub = FakeHub()
        hub.script_op("a", "get_device_id",
                      FakeResult(data={}, observed_dpn_id="OTHER"))
        with self.assertRaises(IdentityMismatchError):
            _agent(hub, "a", expected="EXPECTED").get_device_id()

    def test_14_wrong_agent_id_response(self):
        hub = FakeHub()
        hub.script_op("a", "get_device_id", FakeResult(data={}, agent_id="B"))
        with self.assertRaises(AgentResponseMismatch):
            _agent(hub, "a").get_device_id()

    def test_15_timeout(self):
        hub = FakeHub()
        hub.send_exc = AgentTimeout("timeout")
        with self.assertRaises(AgentTimeout):
            _agent(hub, "a").get_device_id()

    def test_16_close_does_not_stop_hub(self):
        hub = FakeHub()
        a = _agent(hub, "a")
        a.close()
        self.assertFalse(hasattr(hub, "stopped") and hub.stopped)
        with self.assertRaises(AgentNotStarted):
            a.get_device_id()


class FakeServer:
    def __init__(self, start_ok=True, start_error="", online=True, send_impl=None):
        self.start_ok = start_ok
        self.start_error = start_error
        self._online = online
        self._send_impl = send_impl
        self.stopped = False

    def start(self):
        return self.start_ok

    def stop(self):
        self.stopped = True

    def is_online(self, agent_id):
        return self._online

    def send_command_blocking(self, cmd, timeout):
        if self._send_impl is not None:
            return self._send_impl(cmd, timeout)
        return FakeResult(status="success", data={}, agent_id=cmd.agent_id,
                          request_id=cmd.request_id)


class TestAgentHub(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.sd = os.path.join(self._td.name, "dpn")

    def tearDown(self):
        self._td.cleanup()

    def test_20_adb_reverse_scheduler(self):
        calls = []
        srv = FakeServer()
        hub = AgentHub(state_dir=self.sd, server_factory=lambda: srv,
                       reverse_fn=lambda serial, port: calls.append((serial, port)),
                       reverse_serials_fn=lambda: ["s1", "s2"], reverse_interval_s=100)
        hub.start()
        hub.reverse_once()
        self.assertIn(("s1", 8765), calls)
        self.assertIn(("s2", 8765), calls)
        hub.stop()

    def test_21_port_in_use(self):
        srv = FakeServer(start_ok=False, start_error="address already in use (10048)")
        hub = AgentHub(state_dir=self.sd, server_factory=lambda: srv)
        with self.assertRaises(AgentServerPortInUse):
            hub.start()
        # lock liberado: outro hub consegue subir
        srv2 = FakeServer()
        hub2 = AgentHub(state_dir=self.sd, server_factory=lambda: srv2)
        self.assertTrue(hub2.start())
        hub2.stop()

    def test_21b_busy_second_instance(self):
        srv = FakeServer()
        hub = AgentHub(state_dir=self.sd, server_factory=lambda: srv)
        hub.start()
        hub2 = AgentHub(state_dir=self.sd, server_factory=lambda: FakeServer())
        with self.assertRaises(AgentHubBusy):
            hub2.start()
        hub.stop()

    def test_22_clean_shutdown(self):
        srv = FakeServer()
        hub = AgentHub(state_dir=self.sd, server_factory=lambda: srv)
        hub.start()
        hub.stop()
        self.assertTrue(srv.stopped)

    def test_hub_send_timeout_and_offline(self):
        srv = FakeServer(send_impl=lambda cmd, t: (_ for _ in ()).throw(TimeoutError("x")))
        hub = AgentHub(state_dir=self.sd, server_factory=lambda: srv)
        hub.start()
        with self.assertRaises(AgentTimeout):
            hub.send("a", "get_device_id")
        hub.stop()
        srv2 = FakeServer(send_impl=lambda cmd, t: (_ for _ in ()).throw(RuntimeError("offline")))
        hub2 = AgentHub(state_dir=self.sd, server_factory=lambda: srv2)
        hub2.start()
        with self.assertRaises(AgentOfflineError):
            hub2.send("a", "get_device_id")
        hub2.stop()

    def test_not_started(self):
        hub = AgentHub(state_dir=self.sd)
        with self.assertRaises(AgentNotStarted):
            hub.send("a", "get_device_id")


class DirectFake:
    def __init__(self):
        self.tunnels = [{"tunnelCode": "US", "activeIp": "2.2.2.2"}]

    def login(self, username=None, password=None):
        return {"success": True}

    def check_online(self):
        return True

    def get_device_id(self):
        return {"deviceId": "D"}

    def get_node_type(self):
        return {"dpnMode": "smart"}

    def get_node_lb_mode(self):
        return {"mode": "none"}

    def get_exit_ip(self):
        return self.tunnels

    def set_node_lb_mode(self, mode):
        return {"success": True}

    def switch_node(self, tc, ip):
        return {"success": True}

    def refresh_tunnel(self, tc):
        return {"success": True}

    def close(self):
        pass


class TestProviderTransport(unittest.TestCase):
    def test_17_agent_no_local_password(self):
        hub = FakeHub()
        hub.set_online("agentA", True)
        hub.script_op("agentA", "login", FakeResult(data={"success": True}))
        hub.script_op("agentA", "node_lb_mode_get", FakeResult(data={"mode": "none"}))
        hub.script_op("agentA", "list_tunnels",
                      FakeResult(data=[{"tunnelCode": "US", "activeIp": "1.2.3.4"}]))
        cfg = {"transport": "agent", "agent_id": "agentA", "address": "34.34.34.34",
               "tunnel_code": "US", "_dpn_id": "DPN_01"}
        prov = DeeperConnectProvider(cfg, agent_hub=hub, password=None, env={},
                                     log_fn=lambda m: None)
        self.assertEqual(prov.transport, "agent")
        self.assertEqual(prov.get_public_ip(), "1.2.3.4")
        self.assertEqual(prov.get_node_id(), "US")

    def test_18_direct_still_works(self):
        cfg = {"address": "10.0.0.9", "tunnel_code": "US", "_dpn_id": "DPN_01"}
        prov = DeeperConnectProvider(cfg, client_factory=lambda: DirectFake(),
                                     password="pw", env={}, log_fn=lambda m: None)
        self.assertEqual(prov.transport, "direct")
        self.assertEqual(prov.get_public_ip(), "2.2.2.2")


class TestRegistry(unittest.TestCase):
    def test_19_sqlite_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "dpn.db")
            con = sqlite3.connect(p)
            con.execute("CREATE TABLE agents(agent_id TEXT, token_hash TEXT, name TEXT,"
                        " dpn_mac TEXT, device_id INTEGER, local_ip TEXT, paired_at REAL,"
                        " last_seen REAL, observed_dpn_id TEXT)")
            con.execute("CREATE TABLE devices(id INTEGER, android_serial TEXT,"
                        " expected_dpn_id TEXT, agent_id TEXT, transport_mode TEXT)")
            th = hashlib.sha256(b"tok").hexdigest()
            con.execute("INSERT INTO agents VALUES('agentA',?,'DPN 1','',1,'',0,123,'')", (th,))
            con.execute("INSERT INTO devices(id,android_serial,expected_dpn_id,agent_id,"
                        "transport_mode) VALUES(1,'192.168.1.100:5555','PHYS','agentA','agent')")
            con.commit(); con.close()
            reg = DPNControlSQLiteRegistry(p)
            rec = reg.get("agentA")
            self.assertIsNotNone(rec)
            self.assertEqual(rec.token_hash, th)
            self.assertEqual(rec.android_serial, "192.168.1.100:5555")
            self.assertEqual(rec.expected_dpn_id, "PHYS")
            self.assertTrue(reg.authenticate("agentA", "tok"))
            self.assertFalse(reg.authenticate("agentA", "wrong"))
            self.assertIn("192.168.1.100:5555", reg.serials())
            self.assertIsNone(DPNControlSQLiteRegistry(os.path.join(d, "missing.db")).get("x"))


if __name__ == "__main__":
    unittest.main()
