#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.1 — Device Agent controller (mock). 25+ casos, sem APK/ADB/WSS real."""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
import uuid

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from device_agent import protocol as P, errors
from device_agent.state import DeviceAgentStore
from device_agent.models import CommandRecord
from device_agent.registry import AgentRegistry, hash_token
from device_agent.pairing import PairingManager
from device_agent.server import DeviceAgentServer
from device_agent.mock_agent import MockAgent, MockSession
from device_agent.ui_model import correlate_device_agent


def _mk_store():
    td = tempfile.TemporaryDirectory()
    return td, DeviceAgentStore(td.name)


def _pair(pairing, agent_id, info=None):
    code = pairing.open(intent="test")
    return pairing.consume(code, agent_id, info or {})


class TestSync(unittest.TestCase):
    def setUp(self):
        self._td, self.store = _mk_store()
        self.reg = AgentRegistry(self.store)
        self.pairing = PairingManager(self.store)

    def tearDown(self):
        self.store.close()
        self._td.cleanup()

    def test_01_pairing_valid(self):
        res = _pair(self.pairing, "a1", {"android_id": "aid1", "build_fingerprint": "b1"})
        self.assertTrue(res["ok"])
        rec = self.store.get_agent("a1")
        self.assertEqual(rec.token_hash, hash_token(res["token"]))

    def test_02_pairing_expired(self):
        code = self.pairing.open(ttl_s=-1)
        res = self.pairing.consume(code, "a1", {})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], errors.PAIRING_EXPIRED)

    def test_03_pairing_reuse(self):
        code = self.pairing.open()
        self.assertTrue(self.pairing.consume(code, "a1", {})["ok"])
        res = self.pairing.consume(code, "a2", {})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], errors.PAIRING_USED)

    def test_04_token_invalid(self):
        tok = _pair(self.pairing, "a1")["token"]
        self.assertFalse(self.reg.authenticate("a1", "wrong-token"))
        self.assertTrue(self.reg.authenticate("a1", tok))

    def test_05_token_revoked(self):
        tok = _pair(self.pairing, "a1")["token"]
        self.reg.revoke("a1")
        self.assertFalse(self.reg.authenticate("a1", tok))

    def test_23_registry_no_secrets(self):
        tok = _pair(self.pairing, "a1")["token"]
        rec = self.store.get_agent("a1")
        self.assertEqual(rec.token_hash, hash_token(tok))
        self.assertNotEqual(rec.token_hash, tok)
        with open(self.store.db_path, "rb") as f:
            raw = f.read()
        self.assertNotIn(tok.encode("utf-8"), raw)

    def test_24_correlate_by_lan_ip(self):
        snap = {"dpns": {"DPN_01": {
            "tunnels": [{"tunnel_code": "GB", "tunnel_ip": "1.1.1.1",
                         "devices": [{"mac": "aa", "lan_ip": "192.168.1.50",
                                      "name": "d", "online": True}]}],
            "smart_devices": [], "disabled_devices": [], "blackhole_devices": []}}}
        c = correlate_device_agent({"agent_id": "a1", "lan_ip": "192.168.1.50"}, snap)
        self.assertEqual(c["dpn_id"], "DPN_01")
        self.assertEqual(c["tunnel_code"], "GB")
        self.assertEqual(c["mapping_source"], "lan_ip")
        self.assertFalse(c["ambiguous"])

    def test_25_ambiguous_mapping(self):
        dev = {"mac": "aa", "lan_ip": "192.168.1.50", "name": "d", "online": True}
        snap = {"dpns": {
            "DPN_01": {"tunnels": [{"tunnel_code": "GB", "tunnel_ip": "1.1.1.1", "devices": [dev]}],
                       "smart_devices": [], "disabled_devices": [], "blackhole_devices": []},
            "DPN_02": {"tunnels": [{"tunnel_code": "UB", "tunnel_ip": "2.2.2.2", "devices": [dev]}],
                       "smart_devices": [], "disabled_devices": [], "blackhole_devices": []}}}
        c = correlate_device_agent({"agent_id": "a1", "lan_ip": "192.168.1.50"}, snap)
        self.assertTrue(c["ambiguous"])
        self.assertIsNone(c["dpn_id"])


    def test_26_gesture_params_bounds(self):
        d = {"width_px": 1080, "height_px": 1920, "rotation": 0, "density_dpi": 420}
        self.assertIsNone(P.validate_params(P.TAP, {"x": 540, "y": 960, "duration_ms": 50}, d))
        self.assertIsNone(P.validate_params(P.SWIPE, {"x1": 10, "y1": 10, "x2": 500, "y2": 500,
                                                      "duration_ms": 300}, d))
        self.assertIn("bounds", P.validate_params(P.TAP, {"x": -1, "y": 10, "duration_ms": 50}, d))
        self.assertIn("bounds", P.validate_params(P.TAP, {"x": 1080, "y": 10, "duration_ms": 50}, d))
        self.assertIn("bounds", P.validate_params(P.SWIPE, {"x1": 10, "y1": 10, "x2": 20,
                                                            "y2": 5000, "duration_ms": 300}, d))
        self.assertIn("faixa", P.validate_params(P.TAP, {"x": 1, "y": 1, "duration_ms": 5}, d))
        self.assertIn("display", P.validate_params(P.TAP, {"x": 1, "y": 1, "duration_ms": 50}, {}))


    def test_27_store_terminal_monotonic(self):
        cid = "44444444-4444-4444-8444-444444444444"
        cmd = CommandRecord(cid, "a1", "a1", P.PING, {}, "QUEUED", time.time())
        self.assertTrue(self.store.save_command(cmd))
        self.assertFalse(self.store.update_command(cid, status=P.QUEUED))
        self.assertTrue(self.store.update_command(cid, status=P.SENT, sent_at=time.time()))
        self.assertFalse(self.store.update_command(cid, status=P.QUEUED))
        self.assertTrue(self.store.update_command(cid, status=P.SUCCESS, finished_at=time.time()))
        self.assertFalse(self.store.update_command(cid, status=P.SENT))
        self.assertEqual(self.store.get_command(cid).status, P.SUCCESS)

        cid2 = "55555555-5555-4555-8555-555555555555"
        self.assertTrue(self.store.save_command(
            CommandRecord(cid2, "a1", "a1", P.TAP, {}, "QUEUED", time.time())))
        self.assertTrue(self.store.update_command(cid2, status=P.UNCERTAIN))
        self.assertFalse(self.store.update_command(cid2, status=P.SENT, sent_at=time.time()))
        self.assertEqual(self.store.get_command(cid2).status, P.UNCERTAIN)

        cid3 = "66666666-6666-4666-8666-666666666666"
        self.assertTrue(self.store.save_command(
            CommandRecord(cid3, "a1", "a1", P.SWIPE, {}, "QUEUED", time.time())))
        self.assertTrue(self.store.update_command(cid3, status=P.FAILURE))
        self.assertFalse(self.store.update_command(cid3, status=P.QUEUED))
        self.assertEqual(self.store.get_command(cid3).status, P.FAILURE)

        cid4 = "77777777-7777-4777-8777-777777777777"
        self.assertTrue(self.store.save_command(
            CommandRecord(cid4, "a1", "a1", P.PING, {}, "SUCCESS", time.time())))
        self.assertFalse(self.store.save_command(
            CommandRecord(cid4, "a1", "a1", P.PING, {}, "QUEUED", time.time())))
        self.assertEqual(self.store.get_command(cid4).status, P.SUCCESS)

    def test_28_command_id_uuid(self):
        self.assertTrue(P.is_command_id(str(uuid.uuid4())))
        self.assertFalse(P.is_command_id("fixed-cid-1"))
        msg = P.make_command("a1", P.PING, command_id="bad")
        self.assertIn("UUID", P.validate_command(msg, "a1"))


    def test_30_privileged_params(self):
        self.assertIsNone(P.validate_params(P.STOP_APP, {"package": "com.qa.target"}))
        self.assertIsNone(P.validate_params(P.CLEAR_APP_DATA, {"package": "com.qa.target"}))
        self.assertIn("package", P.validate_params(P.STOP_APP, {}))
        self.assertIn("package", P.validate_params(P.CLEAR_APP_DATA, {"package": "bad"}))

    def test_29_perception_params(self):
        self.assertIsNone(P.validate_params(P.GET_UI_TREE, {}))
        self.assertIsNone(P.validate_params(P.GET_UI_TREE, {"max_depth": 20, "max_nodes": 500,
                                                            "max_text_length": 80,
                                                            "max_payload_bytes": 250000}))
        self.assertIn("max_depth", P.validate_params(P.GET_UI_TREE, {"max_depth": 0}))
        self.assertIn("max_nodes", P.validate_params(P.GET_UI_TREE, {"max_nodes": 99999}))
        self.assertIn("max_payload", P.validate_params(P.GET_UI_TREE, {"max_payload_bytes": 10}))
        self.assertIsNone(P.validate_params(P.CAPTURE_SCREEN, {}))
        self.assertIsNone(P.validate_params(P.CAPTURE_SCREEN, {"max_dim": 1080,
                                                               "max_payload_bytes": 1500000}))
        self.assertIn("max_dim", P.validate_params(P.CAPTURE_SCREEN, {"max_dim": -1}))
        self.assertIn("format", P.validate_params(P.CAPTURE_SCREEN, {"format": "jpeg"}))


class TestAsync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._td, self.store = _mk_store()
        self.reg = AgentRegistry(self.store)
        self.pairing = PairingManager(self.store)
        self.srv = DeviceAgentServer(self.store, registry=self.reg, pairing=self.pairing)
        self.agents = []
        self._extra = []

    async def asyncTearDown(self):
        for a in self.agents + self._extra:
            try:
                await a.stop()
            except Exception:
                pass
        if not self.srv._closed:
            try:
                await self.srv.shutdown()
            except Exception:
                pass
        self._td.cleanup()

    async def _agent(self, aid, token=None, **kw):
        a = MockAgent(self.srv, aid, token=token, **kw)
        self.agents.append(a)
        return a

    def _token(self, aid, info=None):
        return _pair(self.pairing, aid, info or {})["token"]

    # 6 hello válido
    async def test_06_hello_valid(self):
        tok = self._token("a1")
        a = await self._agent("a1", token=tok)
        resp = await a.connect()
        self.assertEqual(resp.get("type"), P.HELLO_OK)
        self.assertIn("a1", self.srv.connections)

    # 7 protocolo inválido
    async def test_07_protocol_invalid(self):
        sess = MockSession()
        await self.srv.handle_message(sess, {"type": P.HELLO, "protocol": "bad",
                                             "agent_id": "x", "token": "y"})
        resp = json.loads(await sess.recv())
        self.assertEqual(resp.get("error"), errors.PROTOCOL_MISMATCH)

    # 8 duplicate connection (takeover e reject)
    async def test_08a_duplicate_takeover(self):
        tok = self._token("a1")
        a1 = await self._agent("a1", token=tok)
        await a1.connect()
        s1 = a1.session
        a2 = await self._agent("a1", token=tok)
        resp = await a2.connect()
        self.assertEqual(resp.get("type"), P.HELLO_OK)
        self.assertTrue(s1.closed)
        self.assertIs(self.srv.connections["a1"], a2.session)

    async def test_08b_duplicate_reject(self):
        srv2 = DeviceAgentServer(self.store, registry=self.reg, pairing=self.pairing,
                                 duplicate_policy="reject")
        tok = self._token("a2")
        a1 = MockAgent(srv2, "a2", token=tok)
        a2 = MockAgent(srv2, "a2", token=tok)
        self._extra += [a1, a2]
        await a1.connect()
        resp = await a2.connect()
        self.assertEqual(resp.get("error"), errors.DUPLICATE_CONN_REJECTED)
        self.assertIs(srv2.connections["a2"], a1.session)
        await srv2.shutdown()

    # 9 heartbeat
    async def test_09_heartbeat(self):
        a = await self._agent("a1", token=self._token("a1", {"lan_ip": "192.168.1.50"}))
        await a.connect()
        await a.heartbeat({"lan_ip": "192.168.1.50", "foreground_package": "x"})
        await asyncio.sleep(0.01)
        info, ts = self.srv.hearts["a1"]
        self.assertEqual(info["lan_ip"], "192.168.1.50")
        self.assertEqual(self.srv.status("a1"), "ONLINE")

    # 10 PING / 11 GET_STATUS / 13 success
    async def test_10_ping(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.PING)
        self.assertEqual(r["status"], "success")
        self.assertTrue(r["data"]["pong"])

    async def test_11_get_status(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.GET_STATUS)
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["data"]["battery_pct"], 90)

    async def test_13_command_success(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.GET_DEVICE_INFO)
        self.assertEqual(r["status"], "success")
        self.assertIn("model", r["data"])

    # 12 capability unsupported
    async def test_12_capability_unsupported(self):
        caps = {op: True for op in P.OPS if op != P.START_APP}
        a = await self._agent("a1", token=self._token("a1"), capabilities=caps)
        await a.connect()
        r = await self.srv.send_command("a1", P.START_APP, {"package": "com.x"})
        self.assertEqual(r["error"], errors.CAPABILITY_NOT_SUPPORTED)
        self.assertEqual(a.executions, 0)

    # 14 command failure
    async def test_14_command_failure(self):
        beh = {P.GET_STATUS: {"status": "failure", "data": None, "error": "boom"}}
        a = await self._agent("a1", token=self._token("a1"), behaviors=beh)
        await a.connect()
        r = await self.srv.send_command("a1", P.GET_STATUS)
        self.assertEqual(r["status"], "failure")
        self.assertEqual(r["error"], "boom")

    # 15 deadline antes de send
    async def test_15_deadline_before_send(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.PING, deadline_ms=P.now_ms() - 1000)
        self.assertEqual(r["status"], "expired")
        self.assertEqual(r["error"], errors.DEADLINE_EXPIRED)
        self.assertEqual(a.executions, 0)

    # 16 timeout depois de send => UNCERTAIN
    async def test_16_timeout_uncertain(self):
        beh = {P.PING: {"status": "success", "data": {"pong": True}, "delay": 0.3}}
        a = await self._agent("a1", token=self._token("a1"), behaviors=beh)
        await a.connect()
        r = await self.srv.send_command("a1", P.PING, timeout=0.05)
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["error"], errors.COMMAND_TIMEOUT)

    # 17 duplicate command_id sem reexecução
    async def test_17_duplicate_command_id(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        cid = "11111111-1111-4111-8111-111111111111"
        r1 = await self.srv.send_command("a1", P.PING, command_id=cid)
        r2 = await self.srv.send_command("a1", P.PING, command_id=cid)
        self.assertEqual(r1["status"], "success")
        self.assertEqual(r2["status"], "success")
        self.assertEqual(a.executions, 1)

    # 18 queue full
    async def test_18_queue_full(self):
        self.srv.queue_cap = 1
        beh = {P.BACK: {"status": "success", "data": {"ok": True}, "delay": 0.2}}
        a = await self._agent("a1", token=self._token("a1"), behaviors=beh)
        await a.connect()
        t1 = asyncio.create_task(self.srv.send_command("a1", P.BACK))
        await asyncio.sleep(0.02)
        r2 = await self.srv.send_command("a1", P.BACK)
        self.assertEqual(r2["error"], errors.QUEUE_FULL)
        r1 = await t1
        self.assertEqual(r1["status"], "success")

    # 19 isolamento entre 2 agents
    async def test_19_isolation(self):
        behA = {P.PING: {"status": "success", "data": {"who": "A"}}}
        behB = {P.PING: {"status": "success", "data": {"who": "B"}}}
        a = await self._agent("a1", token=self._token("a1"), behaviors=behA)
        b = await self._agent("a2", token=self._token("a2"), behaviors=behB)
        await a.connect(); await b.connect()
        ra, rb = await asyncio.gather(self.srv.send_command("a1", P.PING),
                                      self.srv.send_command("a2", P.PING))
        self.assertEqual(ra["data"]["who"], "A")
        self.assertEqual(rb["data"]["who"], "B")

    # 20 200 agents mock
    async def test_20_200_agents(self):
        n = 200
        agents = []
        tokens = []
        for i in range(n):
            aid = "dev%03d" % i
            tok = self._token(aid, {"android_id": "aid%03d" % i, "build_fingerprint": "b%03d" % i,
                                    "lan_ip": "10.0.0.%d" % (i % 250)})
            tokens.append(tok)
        t0 = time.time()
        for i in range(n):
            a = MockAgent(self.srv, "dev%03d" % i, token=tokens[i],
                          info={"android_id": "aid%03d" % i, "lan_ip": "10.0.0.%d" % (i % 250)})
            agents.append(a)
        self._extra += agents
        await asyncio.gather(*[a.connect() for a in agents])
        self.assertEqual(len(self.srv.connections), n)
        results = await asyncio.gather(*[self.srv.send_command("dev%03d" % i, P.PING)
                                         for i in range(n)])
        self.assertTrue(all(r["status"] == "success" for r in results))
        print("\n[bench] 200 mocks connect+ping: %.3fs" % (time.time() - t0))

    # 21 disconnect/reconnect
    async def test_21_disconnect_reconnect(self):
        tok = self._token("a1")
        beh = {P.BACK: {"status": "success", "data": {"ok": True}, "delay": 0.3}}
        a = await self._agent("a1", token=tok, behaviors=beh)
        await a.connect()
        t = asyncio.create_task(self.srv.send_command("a1", P.BACK))
        await asyncio.sleep(0.05)
        self.srv.close_session(a.session, "test")
        r = await t
        self.assertEqual(r["status"], "uncertain")
        a2 = await self._agent("a1", token=tok)
        resp = await a2.connect()
        self.assertEqual(resp.get("type"), P.HELLO_OK)
        r2 = await self.srv.send_command("a1", P.PING)
        self.assertEqual(r2["status"], "success")

    # ---------------- E3.3A TAP/SWIPE ----------------
    async def test_27_tap_valid(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.TAP, {"x": 540, "y": 960, "duration_ms": 50})
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["data"]["gesture_ms"], 40)
        self.assertEqual(a.executions, 1)

    async def test_28_tap_bounds_negative(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.TAP, {"x": -1, "y": 10, "duration_ms": 50})
        self.assertEqual(r["error"], errors.BAD_PARAMS)
        self.assertEqual(a.executions, 0)

    async def test_29_tap_bounds_over_display(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.TAP, {"x": 1080, "y": 10, "duration_ms": 50})
        self.assertEqual(r["error"], errors.BAD_PARAMS)
        self.assertEqual(a.executions, 0)

    async def test_30_tap_duration_range(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        for dur in (5, 1500):
            r = await self.srv.send_command("a1", P.TAP, {"x": 10, "y": 10, "duration_ms": dur})
            self.assertEqual(r["error"], errors.BAD_PARAMS)
        self.assertEqual(a.executions, 0)

    async def test_31_swipe_valid(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.SWIPE, {"x1": 540, "y1": 1500,
                                                        "x2": 540, "y2": 500,
                                                        "duration_ms": 300})
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["data"]["duration_ms"], 300)

    async def test_32_swipe_bounds(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.SWIPE, {"x1": 540, "y1": 1500,
                                                        "x2": 540, "y2": 5000,
                                                        "duration_ms": 300})
        self.assertEqual(r["error"], errors.BAD_PARAMS)
        self.assertEqual(a.executions, 0)

    async def test_33_accessibility_unavailable(self):
        caps = {op: True for op in P.OPS}
        caps[P.TAP] = False
        caps[P.SWIPE] = False
        a = await self._agent("a1", token=self._token("a1"), capabilities=caps)
        await a.connect()
        r = await self.srv.send_command("a1", P.TAP, {"x": 10, "y": 10, "duration_ms": 50})
        self.assertEqual(r["error"], errors.CAPABILITY_NOT_SUPPORTED)
        self.assertEqual(a.executions, 0)

    async def test_34_callback_success_and_cancelled(self):
        beh = {P.TAP: {"status": "failure", "error": errors.GESTURE_CANCELLED}}
        a = await self._agent("a1", token=self._token("a1"), behaviors=beh)
        await a.connect()
        r = await self.srv.send_command("a1", P.TAP, {"x": 10, "y": 10, "duration_ms": 50})
        self.assertEqual(r["status"], "failure")
        self.assertEqual(r["error"], errors.GESTURE_CANCELLED)

        beh2 = {P.SWIPE: {"status": "failure", "error": errors.GESTURE_TIMEOUT}}
        b = await self._agent("a2", token=self._token("a2"), behaviors=beh2)
        await b.connect()
        r2 = await self.srv.send_command("a2", P.SWIPE, {"x1": 1, "y1": 1, "x2": 10,
                                                         "y2": 10, "duration_ms": 50})
        self.assertEqual(r2["error"], errors.GESTURE_TIMEOUT)

    async def test_35_dedupe_tap_same_command_id(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        cid = "22222222-2222-4222-8222-222222222222"
        p = {"x": 100, "y": 200, "duration_ms": 50}
        r1 = await self.srv.send_command("a1", P.TAP, p, command_id=cid)
        r2 = await self.srv.send_command("a1", P.TAP, p, command_id=cid)
        self.assertEqual(r1["status"], "success")
        self.assertEqual(r2["status"], "success")
        self.assertEqual(a.executions, 1)

    async def test_36_two_taps_serialized(self):
        beh = {P.TAP: {"status": "success", "data": {"gesture_ms": 40}, "delay": 0.2}}
        a = await self._agent("a1", token=self._token("a1"), behaviors=beh)
        await a.connect()
        t0 = time.time()
        rs = await asyncio.gather(
            self.srv.send_command("a1", P.TAP, {"x": 100, "y": 100, "duration_ms": 50}),
            self.srv.send_command("a1", P.TAP, {"x": 200, "y": 200, "duration_ms": 50}))
        self.assertTrue(all(r["status"] == "success" for r in rs))
        self.assertEqual(a.executions, 2)
        self.assertGreaterEqual(time.time() - t0, 0.38)

    async def test_37_rotation_display_update(self):
        info = {
            "android_id": "aid-a1", "build_fingerprint": "mock/a1",
            "manufacturer": "mock", "model": "mock", "sdk": 33, "lan_ip": "", "mac": "",
            "display": {"width_px": 1080, "height_px": 1920,
                        "rotation": 0, "density_dpi": 420}}
        a = await self._agent("a1", token=self._token("a1"), info=info)
        await a.connect()
        r = await self.srv.send_command("a1", P.TAP, {"x": 1500, "y": 500, "duration_ms": 50})
        self.assertEqual(r["error"], errors.BAD_PARAMS)
        hello = {"protocol": P.PROTOCOL_VERSION, "type": P.HELLO, "agent_id": "a1",
                 "token": a.token, "capabilities": a.capabilities,
                 "agent_version": "mock-1", "device_id": "a1",
                 "info": dict(info, display={"width_px": 1920, "height_px": 1080,
                                             "rotation": 1, "density_dpi": 420})}
        await a._send(hello)
        await asyncio.sleep(0.02)
        rec = self.store.get_agent("a1")
        self.assertEqual(rec.display["width_px"], 1920)
        r2 = await self.srv.send_command("a1", P.TAP, {"x": 1500, "y": 500, "duration_ms": 50})
        self.assertEqual(r2["status"], "success")

    async def test_38_disconnect_gesture_uncertain_no_resend(self):
        tok = self._token("a1")
        beh = {P.TAP: {"status": "success", "data": {"gesture_ms": 40}, "delay": 0.4}}
        a = await self._agent("a1", token=tok, behaviors=beh)
        await a.connect()
        cid = "33333333-3333-4333-8333-333333333333"
        p = {"x": 100, "y": 100, "duration_ms": 400}
        t = asyncio.create_task(self.srv.send_command("a1", P.TAP, p, command_id=cid))
        await asyncio.sleep(0.05)
        self.srv.close_session(a.session, "netcut")
        r = await t
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(a.executions, 1)
        a2 = await self._agent("a1", token=tok)
        await a2.connect()
        r2 = await self.srv.send_command("a1", P.TAP, p, command_id=cid)
        self.assertEqual(r2["status"], "uncertain")
        self.assertEqual(a2.executions, 0)

    async def test_39_capability_refresh(self):
        caps = {op: True for op in P.OPS}
        caps[P.TAP] = False
        caps[P.SWIPE] = False
        a = await self._agent("a1", token=self._token("a1"), capabilities=caps)
        await a.connect()
        r = await self.srv.send_command("a1", P.TAP, {"x": 10, "y": 10, "duration_ms": 50})
        self.assertEqual(r["error"], errors.CAPABILITY_NOT_SUPPORTED)
        hello = {"protocol": P.PROTOCOL_VERSION, "type": P.HELLO, "agent_id": "a1",
                 "token": a.token, "capabilities": {op: True for op in P.OPS},
                 "agent_version": "mock-1", "device_id": "a1", "info": a.info}
        await a._send(hello)
        await asyncio.sleep(0.02)
        r2 = await self.srv.send_command("a1", P.TAP, {"x": 10, "y": 10, "duration_ms": 50})
        self.assertEqual(r2["status"], "success")

    async def test_40_status_exposes_display(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.GET_STATUS)
        self.assertEqual(r["data"]["display"]["width_px"], 1080)
        self.assertEqual(r["data"]["display"]["rotation"], 0)

    # ---------------- E3.3A.1 hardening ----------------
    async def _send_bad_result(self, a, cid, **over):
        msg = {"protocol": P.PROTOCOL_VERSION, "type": P.RESULT, "command_id": cid,
               "agent_id": a.agent_id, "device_id": a.agent_id, "op": P.PING,
               "status": "success", "data": {"pong": True}, "error": ""}
        msg.update(over)
        await self.srv.handle_message(a.session, msg)

    async def _pending_ping(self, a, cid, delay=0.4):
        a.behaviors[P.PING] = {"status": "success", "data": {"pong": True}, "delay": delay}
        t = asyncio.create_task(self.srv.send_command(a.agent_id, P.PING, command_id=cid))
        await asyncio.sleep(0.05)
        return t

    async def test_41_result_agent_mismatch(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        cid = str(uuid.uuid4())
        t = await self._pending_ping(a, cid)
        await self._send_bad_result(a, cid, agent_id="a2")
        self.assertEqual(self.store.get_command(cid).status, P.SENT)
        r = await t
        self.assertEqual(r["status"], "success")
        self.assertGreaterEqual(self.srv.metrics["results_invalid"], 1)

    async def test_42_result_device_mismatch(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        cid = str(uuid.uuid4())
        t = await self._pending_ping(a, cid)
        await self._send_bad_result(a, cid, device_id="a2")
        self.assertEqual(self.store.get_command(cid).status, P.SENT)
        self.assertEqual((await t)["status"], "success")

    async def test_43_result_op_mismatch(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        cid = str(uuid.uuid4())
        t = await self._pending_ping(a, cid)
        await self._send_bad_result(a, cid, op=P.GET_STATUS)
        self.assertEqual(self.store.get_command(cid).status, P.SENT)
        self.assertEqual((await t)["status"], "success")

    async def test_44_invalid_result_status(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        cid = str(uuid.uuid4())
        t = await self._pending_ping(a, cid)
        await self._send_bad_result(a, cid, status="weird")
        self.assertEqual(self.store.get_command(cid).status, P.SENT)
        self.assertEqual((await t)["status"], "success")

    async def test_45_result_protocol_mismatch(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        cid = str(uuid.uuid4())
        t = await self._pending_ping(a, cid)
        await self._send_bad_result(a, cid, protocol="bad")
        self.assertEqual(self.store.get_command(cid).status, P.SENT)
        self.assertEqual((await t)["status"], "success")

    async def test_46_invalid_command_uuid(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.PING, command_id="not-a-uuid")
        self.assertEqual(r["error"], errors.INVALID_COMMAND_ID)
        self.assertEqual(a.executions, 0)

    async def test_47_start_app_allowlist(self):
        srv2 = DeviceAgentServer(self.store, registry=self.reg, pairing=self.pairing,
                                 allowed_packages={"com.allowed"})
        tok = self._token("a3")
        a = MockAgent(srv2, "a3", token=tok)
        self._extra.append(a)
        await a.connect()
        r1 = await srv2.send_command("a3", P.START_APP, {"package": "com.allowed"})
        self.assertEqual(r1["status"], "success")
        r2 = await srv2.send_command("a3", P.START_APP, {"package": "com.blocked"})
        self.assertEqual(r2["error"], errors.PACKAGE_NOT_ALLOWED)
        self.assertEqual(a.executions, 1)
        a.behaviors[P.START_APP] = {"status": "failure", "error": "PACKAGE_NOT_FOUND"}
        r3 = await srv2.send_command("a3", P.START_APP, {"package": "com.allowed"})
        self.assertEqual(r3["error"], "PACKAGE_NOT_FOUND")
        await srv2.shutdown()

    async def test_48_concurrent_same_uuid_single_send(self):
        beh = {P.TAP: {"status": "success", "data": {"gesture_ms": 40}, "delay": 0.3}}
        a = await self._agent("a1", token=self._token("a1"), behaviors=beh)
        await a.connect()
        cid = str(uuid.uuid4())
        p = {"x": 100, "y": 100, "duration_ms": 50}
        r1, r2 = await asyncio.gather(
            self.srv.send_command("a1", P.TAP, p, command_id=cid),
            self.srv.send_command("a1", P.TAP, p, command_id=cid))
        rs = [r1, r2]
        self.assertEqual(sum(1 for r in rs if r["status"] == "success"), 1)
        self.assertEqual(sum(1 for r in rs if r.get("error") == "DUPLICATE_IN_PROGRESS"), 1)
        self.assertEqual(a.executions, 1)

    async def test_49_same_uuid_after_success_cached(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        cid = str(uuid.uuid4())
        r1 = await self.srv.send_command("a1", P.PING, command_id=cid)
        r2 = await self.srv.send_command("a1", P.PING, command_id=cid)
        self.assertEqual(r1["status"], "success")
        self.assertEqual(r2["status"], "success")
        self.assertEqual(a.executions, 1)

    async def test_50_same_uuid_after_uncertain_no_resend(self):
        beh = {P.TAP: {"status": "success", "data": {"gesture_ms": 40}, "delay": 0.3}}
        a = await self._agent("a1", token=self._token("a1"), behaviors=beh)
        await a.connect()
        cid = str(uuid.uuid4())
        p = {"x": 100, "y": 100, "duration_ms": 50}
        r1 = await self.srv.send_command("a1", P.TAP, p, command_id=cid, timeout=0.05)
        self.assertEqual(r1["status"], "uncertain")
        r2 = await self.srv.send_command("a1", P.TAP, p, command_id=cid)
        self.assertEqual(r2["status"], "uncertain")
        self.assertEqual(a.executions, 1)

    async def test_51_same_uuid_other_agent_conflict(self):
        a1 = await self._agent("a1", token=self._token("a1"))
        a2 = await self._agent("a2", token=self._token("a2"))
        await a1.connect(); await a2.connect()
        cid = str(uuid.uuid4())
        r1 = await self.srv.send_command("a1", P.PING, command_id=cid)
        self.assertEqual(r1["status"], "success")
        r2 = await self.srv.send_command("a2", P.PING, command_id=cid)
        self.assertEqual(r2["error"], errors.COMMAND_ID_CONFLICT)
        self.assertEqual(a2.executions, 0)

    async def test_52_same_uuid_other_op_conflict(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        cid = str(uuid.uuid4())
        r1 = await self.srv.send_command("a1", P.PING, command_id=cid)
        self.assertEqual(r1["status"], "success")
        r2 = await self.srv.send_command("a1", P.TAP, {"x": 10, "y": 10, "duration_ms": 50},
                                         command_id=cid)
        self.assertEqual(r2["error"], errors.COMMAND_ID_CONFLICT)
        self.assertEqual(a.executions, 1)

    async def test_53_controller_dedupe_survives_agent_cache_loss(self):
        tok = self._token("a1")
        a1 = await self._agent("a1", token=tok)
        await a1.connect()
        cid = str(uuid.uuid4())
        r1 = await self.srv.send_command("a1", P.PING, command_id=cid)
        self.assertEqual(r1["status"], "success")
        a2 = await self._agent("a1", token=tok)
        resp = await a2.connect()
        self.assertEqual(resp.get("type"), P.HELLO_OK)
        r2 = await self.srv.send_command("a1", P.PING, command_id=cid)
        self.assertEqual(r2["status"], "success")
        self.assertEqual(a2.executions, 0)

    async def test_54_get_ui_tree(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.GET_UI_TREE, {"max_nodes": 100})
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["data"]["snapshot_id"], "mock-snap")
        self.assertTrue(r["data"]["nodes"])
        self.assertEqual(r["data"]["nodes"][0]["bounds"], [0, 0, 1080, 2280])
        self.assertFalse(r["data"]["truncated"])

    async def test_55_capture_screen(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.CAPTURE_SCREEN, {"max_dim": 1080})
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["data"]["format"], "png")
        self.assertEqual(r["data"]["payload"], "iVBORw0KGgo=")
        self.assertEqual(r["data"]["width"], 1080)

    async def test_56_capture_screen_capability(self):
        caps = {op: True for op in P.OPS}
        caps[P.CAPTURE_SCREEN] = False
        a = await self._agent("a1", token=self._token("a1"), capabilities=caps)
        await a.connect()
        r = await self.srv.send_command("a1", P.CAPTURE_SCREEN)
        self.assertEqual(r["error"], errors.CAPABILITY_NOT_SUPPORTED)
        self.assertEqual(a.executions, 0)

    async def test_57_foreground_extended(self):
        a = await self._agent("a1", token=self._token("a1"))
        await a.connect()
        r = await self.srv.send_command("a1", P.GET_FOREGROUND_APP)
        self.assertEqual(r["data"]["package"], "com.flixdigital.dramaclassic")
        self.assertIn("MainActivity", r["data"]["activity"])
        self.assertIn("window_id", r["data"])

    async def test_58_privileged_bridge_status_and_allowlist(self):
        srv2 = DeviceAgentServer(self.store, registry=self.reg, pairing=self.pairing,
                                 allowed_privileged_packages={"com.qa.target"})
        tok = self._token("a4")
        a = MockAgent(srv2, "a4", token=tok)
        self._extra.append(a)
        await a.connect()
        r = await srv2.send_command("a4", P.GET_PRIVILEGED_BRIDGE_STATUS)
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["data"]["state"], "READY")
        self.assertEqual(r["data"]["effective_uid"], 2000)
        r = await srv2.send_command("a4", P.STOP_APP, {"package": "com.qa.target"})
        self.assertEqual(r["status"], "success")
        self.assertTrue(r["data"]["stopped"])
        r = await srv2.send_command("a4", P.CLEAR_APP_DATA, {"package": "com.qa.target"})
        self.assertEqual(r["status"], "success")
        self.assertTrue(r["data"]["cleared"])
        r = await srv2.send_command("a4", P.STOP_APP, {"package": "com.other"})
        self.assertEqual(r["error"], errors.PACKAGE_NOT_ALLOWED)
        r = await srv2.send_command("a4", P.STOP_APP, {"package": "com.sln.deviceagent"})
        self.assertEqual(r["error"], errors.PRIVILEGED_PACKAGE_DENIED)
        r = await srv2.send_command("a4", P.STOP_APP, {"package": "moe.shizuku.privileged.api"})
        self.assertEqual(r["error"], errors.PRIVILEGED_PACKAGE_DENIED)
        r = await srv2.send_command("a4", P.STOP_APP, {"package": "com.android.systemui"})
        self.assertEqual(r["error"], errors.PRIVILEGED_PACKAGE_DENIED)
        await srv2.shutdown()

    async def test_59_privileged_capability_and_dedupe(self):
        caps = {op: True for op in P.OPS}
        caps[P.STOP_APP] = False
        caps[P.CLEAR_APP_DATA] = False
        a = await self._agent("a1", token=self._token("a1"), capabilities=caps)
        await a.connect()
        r = await self.srv.send_command("a1", P.STOP_APP, {"package": "com.qa.target"})
        self.assertEqual(r["error"], errors.CAPABILITY_NOT_SUPPORTED)
        self.assertEqual(a.executions, 0)
        srv2 = DeviceAgentServer(self.store, registry=self.reg, pairing=self.pairing,
                                 allowed_privileged_packages={"com.qa.target"})
        b = MockAgent(srv2, "a5", token=self._token("a5"))
        self._extra.append(b)
        await b.connect()
        cid = str(uuid.uuid4())
        r1 = await srv2.send_command("a5", P.STOP_APP, {"package": "com.qa.target"},
                                     command_id=cid)
        r2 = await srv2.send_command("a5", P.STOP_APP, {"package": "com.qa.target"},
                                     command_id=cid)
        self.assertEqual(r1["status"], "success")
        self.assertEqual(r2["status"], "success")
        self.assertEqual(b.executions, 1)
        ccid = str(uuid.uuid4())
        c1 = await srv2.send_command("a5", P.CLEAR_APP_DATA, {"package": "com.qa.target"},
                                     command_id=ccid)
        c2 = await srv2.send_command("a5", P.CLEAR_APP_DATA, {"package": "com.qa.target"},
                                     command_id=ccid)
        self.assertEqual(c1["status"], "success")
        self.assertEqual(c2["status"], "success")
        self.assertEqual(b.executions, 2)
        await srv2.shutdown()

    async def test_60_privileged_disconnect_uncertain(self):
        srv2 = DeviceAgentServer(self.store, registry=self.reg, pairing=self.pairing,
                                 allowed_privileged_packages={"com.qa.target"})
        tok = self._token("a6")
        beh = {P.STOP_APP: {"status": "success",
                            "data": {"package": "com.qa.target", "stopped": True},
                            "delay": 0.3}}
        a = MockAgent(srv2, "a6", token=tok, behaviors=beh)
        self._extra.append(a)
        await a.connect()
        cid = str(uuid.uuid4())
        t = asyncio.create_task(srv2.send_command("a6", P.STOP_APP,
                                                   {"package": "com.qa.target"}, command_id=cid))
        await asyncio.sleep(0.05)
        srv2.close_session(a.session, "netcut")
        r = await t
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(a.executions, 1)
        a2 = MockAgent(srv2, "a6", token=tok)
        self._extra.append(a2)
        await a2.connect()
        r2 = await srv2.send_command("a6", P.STOP_APP, {"package": "com.qa.target"},
                                     command_id=cid)
        self.assertEqual(r2["status"], "uncertain")
        self.assertEqual(a2.executions, 0)
        r3 = await srv2.send_command("a6", P.STOP_APP, {"package": "com.qa.target"},
                                     deadline_ms=P.now_ms() - 1000)
        self.assertEqual(r3["status"], "expired")
        self.assertEqual(a2.executions, 0)
        await srv2.shutdown()

    # 22 shutdown limpo
    async def test_22_clean_shutdown(self):
        srv2 = DeviceAgentServer(self.store, registry=self.reg, pairing=self.pairing)
        tok = self._token("a9")
        a = MockAgent(srv2, "a9", token=tok,
                      behaviors={P.BACK: {"status": "success", "data": {}, "delay": 0.5}})
        self._extra.append(a)
        await a.connect()
        t = asyncio.create_task(srv2.send_command("a9", P.BACK))
        await asyncio.sleep(0.05)
        await srv2.shutdown()
        self.assertTrue(srv2._closed)
        self.assertEqual(srv2.connections, {})
        self.assertEqual(srv2.pending, {})
        r = await t
        self.assertEqual(r["status"], "uncertain")


if __name__ == "__main__":
    unittest.main()
