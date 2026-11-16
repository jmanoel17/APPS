#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa A — dpn/state.py: escrita atômica, contabilidade (clamp/overshoot), seq persistido."""
import multiprocessing as mp
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn import state as S


def _acct_worker(args):
    state_dir, dpn_id, device, limit, token, q = args
    r = S.account_cycle(state_dir, dpn_id, device, limit, cycle_id=token, instance_id="instA")
    q.put(r)


def _acct_distinct_worker(args):
    state_dir, dpn_id, device, limit, k, q = args
    r = S.account_cycle(state_dir, dpn_id, device, limit, cycle_id=k, instance_id="inst")
    q.put((k, r["accepted_for_ip_quota"]))


class TestState(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = self._td.name
        self.dpn = "DPN_01"
        S.set_current_ip(self.d, self.dpn, "1.1.1.1", node_id="node_a")

    def tearDown(self):
        self._td.cleanup()

    def test_atomic_write_read(self):
        p = os.path.join(self.d, "x.json")
        self.assertTrue(S.atomic_write_json(p, {"a": 1, "b": [1, 2]}))
        self.assertEqual(S.read_json(p), {"a": 1, "b": [1, 2]})
        self.assertEqual(S.read_json(os.path.join(self.d, "missing.json"), {"z": 0}), {"z": 0})

    def test_clamp_and_overshoot_sequential(self):
        limit = 20
        for i in range(25):
            S.account_cycle(self.d, self.dpn, "devA", limit, cycle_id=i, instance_id="inst")
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["current_ip_work_units"], limit)   # nunca > 20
        self.assertEqual(st["drain_overshoot"], 5)             # 5 ciclos em voo rejeitados
        self.assertEqual(st["state"], S.IP_LIMIT_REACHED)

    def test_seq_persisted_per_device(self):
        for i in range(7):
            S.account_cycle(self.d, self.dpn, "devA", 20, cycle_id=i, instance_id="inst")
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["devices"]["devA"]["seq"], 7)
        # seq continua de onde parou após reload
        r = S.account_cycle(self.d, self.dpn, "devA", 20, cycle_id=99, instance_id="inst")
        self.assertEqual(r["seq"], 8)

    def test_duplicate_token_not_counted(self):
        r1 = S.account_cycle(self.d, self.dpn, "devA", 20, cycle_id=5, instance_id="inst")
        r2 = S.account_cycle(self.d, self.dpn, "devA", 20, cycle_id=5, instance_id="inst")
        self.assertTrue(r1["counted"])
        self.assertFalse(r2["counted"])
        self.assertTrue(r2["duplicate"])
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["current_ip_work_units"], 1)
        self.assertEqual(st["devices"]["devA"]["seq"], 1)

    def test_concurrent_clamp_never_exceeds_limit(self):
        limit = 20
        # começa em 19 (força a corrida em volta do limite)
        S.set_state(self.d, self.dpn, S.RUNNING, current_ip="1.1.1.1",
                    current_ip_work_units=19,
                    node={"node_id": "node_a", "created_at": 1, "ips_seen": ["1.1.1.1"]})
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        ps = [ctx.Process(target=_acct_distinct_worker,
                          args=((self.d, self.dpn, "devA", limit, k, q),)) for k in range(20)]
        for p in ps:
            p.start()
        for p in ps:
            p.join(60)
        results = [q.get(timeout=10) for _ in range(20)]
        accepted = [k for k, acc in results if acc]
        st = S.read_dpn_state(self.d, self.dpn)
        self.assertEqual(st["current_ip_work_units"], limit)   # 20, nunca 21
        self.assertEqual(len(accepted), 1)                     # só 19->20 incrementa
        self.assertEqual(st["drain_overshoot"], 19)


if __name__ == "__main__":
    unittest.main()
