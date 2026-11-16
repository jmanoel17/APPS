#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa E1 — Runner standalone: singleton, reconcile, 2 DPNs, isolamento de erro, status
atômico, command bus idempotente, pause/resume/rotate/reset, shutdown, provider desconhecido,
deeper_connect sem config real."""
import json
import os
import sys
import tempfile
import time
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn import state as S
import dpn.ui_model as UI
import dpn_coordinator_run as R
from dpn.provider_deeper import DeeperConnectProvider, ProviderNotConfigured
from dpn.provider import DPNProvider


def _cfg(**over):
    c = {"dpn_routing_enabled": True, "dpn_work_units_per_ip": 20,
         "dpn_ip_reuse_cooldown_s": 3600, "dpn_network_settle_s": 0,
         "dpn_rotation_poll_s": 0.01, "dpn_rotation_timeout_s": 0.05,
         "dpn_drain_timeout_s": 0.1, "dpn_duplicate_rotations_before_node_reset": 5}
    c.update(over)
    return c


def _groups(**dpns):
    return {k: v for k, v in dpns.items()}


def _runner(sd, cfg, groups):
    return R.Runner(cfg, groups, PROJ, sd, tick_s=0.0,
                    log_fn=lambda *a, **k: None, alive_fn=lambda d: True)


class _Raising(DPNProvider):
    def get_public_ip(self):
        raise RuntimeError("boom")

    def get_node_id(self):
        return "n"

    def rotate_ip(self, mode="REGULAR"):
        return True

    def reset_node(self):
        return True

    def health(self):
        return True


class _Closable(DPNProvider):
    def __init__(self):
        self.closed = False

    def get_public_ip(self):
        return "1.1.1.1"

    def get_node_id(self):
        return "n"

    def rotate_ip(self, mode="REGULAR"):
        return True

    def reset_node(self):
        return True

    def health(self):
        return True

    def close(self):
        self.closed = True


class TestRunner(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.sd = os.path.join(self._td.name, "dpn")

    def tearDown(self):
        self._td.cleanup()

    def _to_ready(self, r, n=4):
        for _ in range(n):
            r.step_all()

    # 1 singleton
    def test_01_singleton_host_lock(self):
        os.makedirs(self.sd, exist_ok=True)
        l1 = R.acquire_host_lock(self.sd)
        self.assertIsNotNone(l1)
        l2 = R.acquire_host_lock(self.sd)
        self.assertIsNone(l2)
        l1.release()
        l3 = R.acquire_host_lock(self.sd)
        self.assertIsNotNone(l3)
        l3.release()

    # 2 startup reconcile
    def test_02_startup_reconcile(self):
        S.set_state(self.sd, "DPN_01", S.ROTATING, _old_ip="OLD",
                    current_ip="OLD", current_ip_work_units=20)
        g = _groups(DPN_01={"devices": ["d1"], "controller": {"type": "fake", "sequence": ["NEW"]}})
        _runner(self.sd, _cfg(), g)
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(st["state"], S.VERIFYING_IP)
        self.assertEqual(st["_pending_ip"], "NEW")

    # 3 two independent DPNs
    def test_03_two_dpns_independent(self):
        g = _groups(
            DPN_01={"devices": ["d1"], "controller": {"type": "fake", "sequence": ["1.1.1.1"]}},
            DPN_02={"devices": ["d2"], "controller": {"type": "fake", "sequence": ["2.2.2.2"]}})
        r = _runner(self.sd, _cfg(), g)
        self._to_ready(r)
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_01")["current_ip"], "1.1.1.1")
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_02")["current_ip"], "2.2.2.2")
        # pausa só DPN_01
        cmd = UI.make_command("DPN_01", "pause")
        UI.write_command(self.sd, cmd)
        r.process_commands()
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_01")["state"], S.PAUSED)
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_02")["state"], S.RUNNING)

    # 4 error isolation
    def test_04_error_in_one_dpn_does_not_kill_other(self):
        g = _groups(
            DPN_01={"devices": ["d1"], "controller": {"type": "fake", "sequence": ["1.1.1.1"]}},
            DPN_02={"devices": ["d2"], "controller": {"type": "fake", "sequence": ["2.2.2.2"]}})
        r = _runner(self.sd, _cfg(), g)
        r.dpns["DPN_02"]["coord"].provider = _Raising()
        r.step_all()
        r.step_all()
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_01")["state"], S.RUNNING)
        snap = r.snapshot()
        self.assertIsNotNone(snap["dpns"]["DPN_02"]["last_error"])

    # 5 status.json atomic
    def test_05_status_atomic(self):
        g = _groups(DPN_01={"devices": ["d1"], "controller": {"type": "fake", "sequence": ["1.1.1.1"]}})
        r = _runner(self.sd, _cfg(), g)
        r.publish_status()
        p = os.path.join(self.sd, "status.json")
        d = S.read_json(p)
        self.assertIn("DPN_01", d["dpns"])
        self.assertIn("updated_at", d)
        # corrompe e republica -> JSON válido de novo
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        r.publish_status()
        self.assertIsInstance(S.read_json(p), dict)

    # 6 command idempotency
    def test_06_command_idempotency(self):
        g = _groups(DPN_01={"devices": ["d1"], "controller": {"type": "fake", "sequence": ["1.1.1.1"]}})
        r = _runner(self.sd, _cfg(), g)
        self._to_ready(r)
        cmd = UI.make_command("DPN_01", "pause")
        UI.write_command(self.sd, cmd)
        a1 = r.process_commands()
        self.assertEqual(len(a1), 1)
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_01")["state"], S.PAUSED)
        # re-publica o MESMO command_id -> não reaplica
        UI.write_command(self.sd, cmd)
        a2 = r.process_commands()
        self.assertEqual(a2, [])

    # 7 pause / resume
    def test_07_pause_resume(self):
        g = _groups(DPN_01={"devices": ["d1"], "controller": {"type": "fake", "sequence": ["1.1.1.1"]}})
        r = _runner(self.sd, _cfg(), g)
        self._to_ready(r)
        UI.write_command(self.sd, UI.make_command("DPN_01", "pause"))
        r.process_commands()
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_01")["state"], S.PAUSED)
        UI.write_command(self.sd, UI.make_command("DPN_01", "resume"))
        r.process_commands()
        self.assertIn(S.read_dpn_state(self.sd, "DPN_01")["state"], (S.RUNNING, S.INIT))

    # 8 rotate_now
    def test_08_rotate_now(self):
        g = _groups(DPN_01={"devices": ["d1"], "controller": {"type": "fake", "sequence": ["1.1.1.1", "1.1.1.2"]}})
        r = _runner(self.sd, _cfg(), g)
        self._to_ready(r)
        UI.write_command(self.sd, UI.make_command("DPN_01", "rotate_now"))
        r.process_commands()
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_01")["state"], S.ROTATION_PENDING)

    # 9 reset_node_now
    def test_09_reset_node_now(self):
        g = _groups(DPN_01={"devices": ["d1"], "controller": {"type": "fake", "sequence": ["1.1.1.1"]}})
        r = _runner(self.sd, _cfg(), g)
        self._to_ready(r)
        UI.write_command(self.sd, UI.make_command("DPN_01", "reset_node_now"))
        r.process_commands()
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_01")["state"], S.RESET_NODE_PENDING)

    # 10 clean shutdown
    def test_10_clean_shutdown(self):
        g = _groups(DPN_01={"devices": ["d1"], "controller": {"type": "fake", "sequence": ["1.1.1.1"]}})
        r = _runner(self.sd, _cfg(), g)
        dummy = _Closable()
        r.dpns["DPN_01"]["provider"] = dummy
        r.close()
        self.assertTrue(dummy.closed)
        # run() com duration curta encerra sem travar
        r2 = _runner(self.sd, _cfg(), g)
        r2.run(duration=0.2)
        r2.close()

    # 11 unknown provider
    def test_11_unknown_provider(self):
        g = _groups(DPN_01={"devices": ["d1"], "controller": {"type": "nope"}},
                    DPN_02={"devices": ["d2"], "controller": {"type": "fake", "sequence": ["2.2.2.2"]}})
        r = _runner(self.sd, _cfg(), g)
        snap = r.snapshot()
        self.assertEqual(snap["dpns"]["DPN_01"]["state"], "ERROR")
        self.assertIn("unknown_provider", snap["dpns"]["DPN_01"]["last_error"])
        r.step_all(); r.step_all()
        self.assertEqual(S.read_dpn_state(self.sd, "DPN_02")["state"], S.RUNNING)

    # 12 deeper_connect sem config real -> estado explícito, sem exception infinita
    def test_12_deeper_not_configured_explicit(self):
        g = _groups(DPN_01={"devices": ["d1"], "controller": {"type": "deeper_connect"}})
        r = _runner(self.sd, _cfg(), g)
        r.step_all()
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(st["state"], S.ERROR)   # explícito, não trava
        # contrato levanta exceção explícita
        p = DeeperConnectProvider.from_config({})
        with self.assertRaises(ProviderNotConfigured):
            p.rotate_ip("REGULAR")


if __name__ == "__main__":
    unittest.main()
