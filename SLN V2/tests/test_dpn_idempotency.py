#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa A — Idempotência (F): o MESMO ciclo (instance_id,cycle_id) conta UMA vez, mesmo com
dois processos concorrentes."""
import multiprocessing as mp
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn import state as S


def _same_token_worker(args):
    state_dir, dpn_id, q = args
    r = S.account_cycle(state_dir, dpn_id, "devA", 20, cycle_id=42, instance_id="inst-xyz")
    q.put({"counted": r["counted"], "duplicate": r["duplicate"]})


class TestIdempotency(unittest.TestCase):
    def test_same_cycle_across_processes_counted_once(self):
        with tempfile.TemporaryDirectory() as d:
            dpn = "DPN_01"
            S.set_current_ip(d, dpn, "2.2.2.2", node_id="node_a")
            ctx = mp.get_context("spawn")
            q = ctx.Queue()
            ps = [ctx.Process(target=_same_token_worker, args=((d, dpn, q),)) for _ in range(2)]
            for p in ps:
                p.start()
            for p in ps:
                p.join(60)
            res = [q.get(timeout=10) for _ in range(2)]
            counted = [r for r in res if r["counted"]]
            dup = [r for r in res if r["duplicate"]]
            self.assertEqual(len(counted), 1)
            self.assertEqual(len(dup), 1)
            st = S.read_dpn_state(d, dpn)
            self.assertEqual(st["current_ip_work_units"], 1)   # contou 1 vez só
            self.assertEqual(st["devices"]["devA"]["seq"], 1)


if __name__ == "__main__":
    unittest.main()
