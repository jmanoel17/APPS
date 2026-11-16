#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa C/D — worker_hook: no-op quando OFF, gate, limite->DRAINING, G (clamp concorrente)."""
import json
import multiprocessing as mp
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn import state as S
import dpn.worker_hook as H


def _mk_cfg(d, enabled=True, limit=20):
    sd = os.path.join(d, "dpn")
    gp = os.path.join(d, "dpn_groups.json")
    with open(gp, "w", encoding="utf-8") as f:
        json.dump({"DPN_01": {"devices": ["devA"]}, "_comentario": "x"}, f)
    return {"dpn_routing_enabled": enabled, "dpn_work_units_per_ip": limit,
            "dpn_state_dir": sd, "dpn_groups_file": gp}


def _g_worker(args):
    cfg, k, q = args
    r = H.on_terminal_cycle("devA", "inst", "c%d" % k, "COMPLETED", cfg=cfg)
    q.put(bool(r and r.get("accepted_for_ip_quota")))


class TestWorkerHook(unittest.TestCase):
    def test_off_creates_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = _mk_cfg(d, enabled=False)
            self.assertTrue(H.gate("devA", "inst", cfg=cfg))
            self.assertIsNone(H.on_terminal_cycle("devA", "inst", 1, "COMPLETED", cfg=cfg))
            self.assertFalse(os.path.exists(cfg["dpn_state_dir"]))  # nenhum arquivo/lock DPN

    def test_gate_blocks_when_draining_and_publishes_safe(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = _mk_cfg(d)
            S.begin_draining(cfg["dpn_state_dir"], "DPN_01")
            self.assertFalse(H.gate("devA", "inst", cfg=cfg))
            sig = os.path.join(cfg["dpn_state_dir"], "workers", S.safe_id("devA") + ".json")
            self.assertTrue(os.path.exists(sig))
            with open(sig, encoding="utf-8") as f:
                self.assertTrue(json.load(f)["safe"])

    def test_gate_allows_when_ready(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = _mk_cfg(d)
            S.set_current_ip(cfg["dpn_state_dir"], "DPN_01", "1.1.1.1", node_id="n")
            self.assertTrue(H.gate("devA", "inst", cfg=cfg))

    def test_limit_triggers_draining(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = _mk_cfg(d, limit=20)
            sd = cfg["dpn_state_dir"]
            S.set_current_ip(sd, "DPN_01", "1.1.1.1", node_id="n")
            for i in range(20):
                H.on_terminal_cycle("devA", "inst", i, "COMPLETED", cfg=cfg)
            st = S.read_dpn_state(sd, "DPN_01")
            self.assertEqual(st["current_ip_work_units"], 20)
            self.assertEqual(st["state"], S.DRAINING)
            self.assertFalse(H.gate("devA", "inst", cfg=cfg))   # novos ciclos bloqueados

    def test_G_concurrent_quota_clamp_never_21(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = _mk_cfg(d, limit=20)
            sd = cfg["dpn_state_dir"]
            S.set_state(sd, "DPN_01", S.RUNNING, current_ip="1.1.1.1",
                        current_ip_work_units=19,
                        node={"node_id": "n", "created_at": 1, "ips_seen": ["1.1.1.1"]})
            ctx = mp.get_context("spawn")
            q = ctx.Queue()
            ps = [ctx.Process(target=_g_worker, args=((cfg, k, q),)) for k in range(20)]
            for p in ps:
                p.start()
            for p in ps:
                p.join(60)
            acc = [q.get(timeout=10) for _ in range(20)]
            st = S.read_dpn_state(sd, "DPN_01")
            self.assertEqual(st["current_ip_work_units"], 20)      # nunca 21
            self.assertEqual(sum(1 for a in acc if a), 1)          # exatamente 1 accepted
            self.assertEqual(st["drain_overshoot"], 19)            # demais -> overshoot
            self.assertEqual(st["state"], S.DRAINING)
            self.assertFalse(H.gate("devA", "inst", cfg=cfg))      # novos ciclos bloqueados


if __name__ == "__main__":
    unittest.main()
