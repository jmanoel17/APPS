#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa F — ui_model (lógica pura da aba DPN, sem Tk): status ausente/stale/online, vários DPNs,
estados mudando, command creation, sem travar em JSON inválido."""
import os
import sys
import tempfile
import time
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn import state as S
import dpn.ui_model as UI


def _status(now):
    return {"updated_at": now, "runner_pid": 123, "host_id": "h",
            "dpns": {
                "DPN_01": {"state": "RUNNING", "node_id": "n1", "current_ip": "1.1.1.1",
                           "work_units": 7, "limit": 20, "duplicate_rotations": 0,
                           "safe_devices": 2, "active_devices": 2,
                           "last_transition_at": now, "last_error": None, "cooldown_remaining_s": 0},
                "DPN_02": {"state": "DRAINING", "node_id": "n2", "current_ip": "2.2.2.2",
                           "work_units": 20, "limit": 20, "duplicate_rotations": 1,
                           "safe_devices": 1, "active_devices": 3,
                           "last_transition_at": now, "last_error": None, "cooldown_remaining_s": 12},
            }}


class TestUiModel(unittest.TestCase):
    def test_status_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "status.json")
            self.assertIsNone(UI.read_status(p))
            self.assertEqual(UI.rows(None), [])
            self.assertFalse(UI.coordinator_online(None))
            self.assertTrue(UI.is_stale(None))

    def test_status_online_and_stale(self):
        now = time.time()
        st = _status(now)
        self.assertTrue(UI.coordinator_online(st, now=now))
        self.assertFalse(UI.is_stale(st, now=now))
        self.assertFalse(UI.coordinator_online(st, now=now + 30, stale_s=10))
        self.assertTrue(UI.is_stale(st, now=now + 30, stale_s=10))

    def test_rows_multiple_and_sorted(self):
        now = time.time()
        rows = UI.rows(_status(now))
        self.assertEqual([r["dpn_id"] for r in rows], ["DPN_01", "DPN_02"])
        self.assertEqual(rows[0]["ip"], "1.1.1.1")
        self.assertEqual(rows[1]["safe"], 1)
        self.assertEqual(rows[1]["cooldown_s"], 12)

    def test_states_changing_colors(self):
        self.assertNotEqual(UI.state_color("RUNNING"), UI.state_color("DRAINING"))
        self.assertNotEqual(UI.state_color("ERROR"), UI.state_color("PAUSED"))
        self.assertEqual(UI.state_color("UNKNOWN_XYZ"), "#000000")

    def test_command_creation_unique(self):
        a = UI.make_command("DPN_01", "pause")
        b = UI.make_command("DPN_01", "pause")
        self.assertNotEqual(a["command_id"], b["command_id"])
        self.assertEqual(a["action"], "pause")
        for k in ("command_id", "dpn_id", "action", "created_at", "requested_by"):
            self.assertIn(k, a)
        with self.assertRaises(ValueError):
            UI.make_command("DPN_01", "delete_everything")

    def test_write_command_atomic_and_duplicate_guard(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = UI.make_command("DPN_01", "rotate_now")
            UI.write_command(d, cmd)
            UI.write_command(d, cmd)   # mesmo id -> mesmo arquivo (1)
            files = [f for f in os.listdir(os.path.join(d, "commands")) if f.endswith(".json")]
            self.assertEqual(len(files), 1)

    def test_invalid_json_during_swap_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "status.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"updated_at": ')   # truncado
            self.assertIsNone(UI.read_status(p))
            # volta a ser válido
            S.atomic_write_json(p, _status(time.time()))
            self.assertIsInstance(UI.read_status(p), dict)


if __name__ == "__main__":
    unittest.main()
