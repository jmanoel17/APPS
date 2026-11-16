#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa B — DPNCoordinator: state machine, rotação, cooldown global, node reset, recovery.

Cobre (plano): D (esgotamento do node), E (node novo devolve IP antigo em cooldown),
H (restart em 17 preserva), I (crash durante ROTATING reconcilia) + happy path + barrier.
"""
import os
import sys
import tempfile
import time
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn import state as S
from dpn.coordinator import DPNCoordinator, FakeBarrier
from dpn.provider import FakeDPNProvider


class TestCoordinator(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = self._td.name
        self.dpn = "DPN_01"

    def tearDown(self):
        self._td.cleanup()

    def _coord(self, provider, barrier=None):
        return DPNCoordinator(
            self.d, self.dpn, provider, limit=20, cooldown_s=3600, dup_before_reset=5,
            settle_s=0, rotation_poll_s=0.01, rotation_timeout_s=0.05, drain_timeout_s=0.1,
            barrier=barrier, now_fn=time.time, sleep_fn=time.sleep,
            log_fn=lambda *a, **k: None)

    def _account_n(self, n, start=0):
        for i in range(start, start + n):
            S.account_cycle(self.d, self.dpn, "devA", 20, cycle_id=i, instance_id="inst")

    # ---------------- happy path ----------------
    def test_happy_limit_rotate_to_new_ip(self):
        prov = FakeDPNProvider(sequence=["1.1.1.1", "1.1.1.2"])
        co = self._coord(prov)
        self.assertEqual(co.run(max_steps=10), "ready")
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["current_ip"], "1.1.1.1")
        self.assertEqual(st["state"], S.RUNNING)

        self._account_n(20)
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["current_ip_work_units"], 20)
        self.assertEqual(st["state"], S.IP_LIMIT_REACHED)

        self.assertEqual(co.run(max_steps=20), "ready")
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["current_ip"], "1.1.1.2")
        self.assertEqual(st["state"], S.RUNNING)
        self.assertEqual(st["current_ip_work_units"], 0)

    # ---------------- D: esgotamento do node ----------------
    def test_D_node_exhausted_after_duplicates(self):
        prov = FakeDPNProvider(sequence=["1.1.1.1"], node_id="node_a")  # rotate mantém o IP
        co = self._coord(prov)
        co.run(max_steps=10)
        self._account_n(20)
        r = co.run(max_steps=60)
        self.assertEqual(r, "node_exhausted")
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertGreaterEqual(st["duplicate_rotations"], 5)
        # próximo passo faz reset do node
        r2 = co.tick()
        self.assertEqual(r2, "node_reset")
        self.assertEqual(prov.reset_calls, 1)
        self.assertEqual(S.read_dpn_state(self.d, self.dpn)["state"], S.WAITING_NEW_NODE)

    # ---------------- E: node novo devolve IP antigo em cooldown ----------------
    def test_E_new_node_old_ip_in_cooldown_rejected(self):
        now = time.time()
        S.record_ip_use(self.d, "9.9.9.9", "DPN_OTHER", "node_other", now=now)
        prov = FakeDPNProvider(sequence=["9.9.9.9"], node_id="node_new")
        co = self._coord(prov)
        co.tick()  # INIT -> DISCOVERING -> VERIFYING
        r = co.tick()  # verifica 9.9.9.9 -> COOLDOWN (histórico global)
        self.assertEqual(r, "cooldown")
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["state"], S.IP_COOLDOWN)
        self.assertIsNone(st["current_ip"])   # novo node NÃO valida IP antigo

    # ---------------- H: restart em 17 preserva ----------------
    def test_H_restart_at_17_preserves(self):
        S.set_current_ip(self.d, self.dpn, "1.1.1.1", node_id="node_a")
        self._account_n(17)
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["current_ip_work_units"], 17)
        # novo coordinator lê o MESMO estado
        co2 = self._coord(FakeDPNProvider(sequence=["1.1.1.1"]))
        snap = co2.snapshot()
        self.assertEqual(snap["work_units"], 17)
        co2.tick()
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["current_ip_work_units"], 17)   # não zerou, não rotacionou
        self.assertEqual(st["state"], S.RUNNING)

    # ---------------- I: crash durante ROTATING ----------------
    def test_I_reconcile_rotating_ip_changed(self):
        S.set_current_ip(self.d, self.dpn, "1.1.1.1", node_id="node_a")
        S.set_state(self.d, self.dpn, S.ROTATING, _old_ip="1.1.1.1",
                    current_ip="1.1.1.1", current_ip_work_units=20)
        prov = FakeDPNProvider(sequence=["1.1.1.1", "1.1.1.2"])
        prov.rotate_ip()  # simula a rotação que aconteceu antes do crash (agora 1.1.1.2)
        co = self._coord(prov)
        co.reconcile()
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["state"], S.VERIFYING_IP)
        self.assertEqual(st["_pending_ip"], "1.1.1.2")
        self.assertEqual(co.tick(), "ready")
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["current_ip"], "1.1.1.2")
        self.assertEqual(st["state"], S.RUNNING)

    def test_I_reconcile_rotating_ip_unchanged(self):
        S.set_current_ip(self.d, self.dpn, "1.1.1.1", node_id="node_a")
        S.set_state(self.d, self.dpn, S.ROTATING, _old_ip="1.1.1.1",
                    current_ip="1.1.1.1", current_ip_work_units=20)
        co = self._coord(FakeDPNProvider(sequence=["1.1.1.1"]))  # rotação não mudou
        co.reconcile()
        self.assertEqual(S.read_dpn_state(self.d, self.dpn)["state"], S.ROTATION_PENDING)

    # ---------------- barrier ----------------
    def test_barrier_waits_until_all_safe(self):
        S.set_current_ip(self.d, self.dpn, "1.1.1.1", node_id="node_a")
        self._account_n(20)
        barrier = FakeBarrier([False, False, True])
        co = self._coord(FakeDPNProvider(sequence=["1.1.1.1", "1.1.1.2"]), barrier=barrier)
        self.assertEqual(co.tick(), "draining")     # begin_drain
        self.assertEqual(co.tick(), "draining")     # all_safe False
        self.assertEqual(co.tick(), "draining")     # all_safe False
        self.assertEqual(co.tick(), "rotating")     # all_safe True -> ROTATION_PENDING
        self.assertGreaterEqual(barrier.reset_calls, 1)


if __name__ == "__main__":
    unittest.main()
