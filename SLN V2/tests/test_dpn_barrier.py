#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa D — SignalBarrier: J (worker desaparecido não trava) + alive-sem-safe espera e expira."""
import os
import sys
import tempfile
import time
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn import state as S
from dpn.barrier import SignalBarrier


class TestBarrier(unittest.TestCase):
    def _drain(self, d):
        S.begin_draining(d, "DPN_01")   # drain_epoch = 1

    def test_J_dead_worker_does_not_hang(self):
        with tempfile.TemporaryDirectory() as d:
            self._drain(d)
            devs = ["A", "B", "C"]
            b = SignalBarrier(d, "DPN_01", devs, alive_fn=lambda dev: dev != "C",
                              poll=0.02, now_fn=time.time)
            b.publish_safe("A")
            b.publish_safe("B")
            t0 = time.time()
            self.assertTrue(b.all_safe(timeout_s=2.0))
            self.assertLess(time.time() - t0, 1.0)   # C morto não bloqueia

    def test_alive_but_not_safe_waits_then_times_out(self):
        with tempfile.TemporaryDirectory() as d:
            self._drain(d)
            b = SignalBarrier(d, "DPN_01", ["A", "B"], alive_fn=lambda dev: True,
                              poll=0.02, now_fn=time.time)
            b.publish_safe("A")   # B vivo mas sem SAFE
            t0 = time.time()
            self.assertTrue(b.all_safe(timeout_s=0.3))
            self.assertGreaterEqual(time.time() - t0, 0.25)   # esperou e expirou (não travou)

    def test_all_safe_fast(self):
        with tempfile.TemporaryDirectory() as d:
            self._drain(d)
            b = SignalBarrier(d, "DPN_01", ["A", "B"], alive_fn=lambda dev: True, poll=0.02)
            b.publish_safe("A")
            b.publish_safe("B")
            t0 = time.time()
            self.assertTrue(b.all_safe(timeout_s=2.0))
            self.assertLess(time.time() - t0, 0.5)

    def test_stale_epoch_safe_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            # SAFE publicado no epoch antigo (0)
            S.set_state(d, "DPN_01", S.DRAINING, drain_epoch=0)
            b_old = SignalBarrier(d, "DPN_01", ["A"], alive_fn=lambda dev: True, poll=0.02)
            b_old.publish_safe("A")
            # novo DRAINING -> epoch 1 (safe antigo não vale)
            S.begin_draining(d, "DPN_01")
            b = SignalBarrier(d, "DPN_01", ["A"], alive_fn=lambda dev: True, poll=0.02)
            self.assertFalse(b.snapshot()["A"])
            b.publish_safe("A")   # agora no epoch atual
            self.assertTrue(b.snapshot()["A"])


if __name__ == "__main__":
    unittest.main()
