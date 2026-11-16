#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa A — Cooldown GLOBAL de IP (A/B/C no nível de estado).

A) IP nunca usado               -> AVAILABLE
B) IP usado há 20 min (< 1h)   -> COOLDOWN
C) IP usado há 65 min (>= 1h)  -> AVAILABLE
"""
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn import state as S

COOLDOWN = 3600


class TestCooldown(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_A_never_used_available(self):
        status, info = S.ip_available(self.d, "10.0.0.1", COOLDOWN, now=1000000)
        self.assertEqual(status, "AVAILABLE")
        self.assertEqual(info["reason"], "never_used")

    def test_B_used_20min_cooldown(self):
        t0 = 1000000
        S.record_ip_use(self.d, "10.0.0.2", "DPN_01", "node_a", now=t0)
        status, info = S.ip_available(self.d, "10.0.0.2", COOLDOWN, now=t0 + 20 * 60)
        self.assertEqual(status, "COOLDOWN")
        self.assertGreater(info["remaining_s"], 0)

    def test_C_used_65min_available(self):
        t0 = 1000000
        S.record_ip_use(self.d, "10.0.0.3", "DPN_01", "node_a", now=t0)
        status, info = S.ip_available(self.d, "10.0.0.3", COOLDOWN, now=t0 + 65 * 60)
        self.assertEqual(status, "AVAILABLE")

    def test_history_is_global_across_dpns(self):
        t0 = 1000000
        S.record_ip_use(self.d, "10.0.0.9", "DPN_01", "node_a", now=t0)
        # outro DPN vê o MESMO cooldown (histórico é global, não por node)
        status, _ = S.ip_available(self.d, "10.0.0.9", COOLDOWN, now=t0 + 60)
        self.assertEqual(status, "COOLDOWN")


if __name__ == "__main__":
    unittest.main()
