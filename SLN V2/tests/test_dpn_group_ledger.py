#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2.6 - group aggregation, epoch threshold, overshoot, barrier and epochs."""
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn.group_ledger import (GroupLedgerCoordinator, GroupLedgerStore,
                              make_work_unit_event, DRAINING, LIMIT_REACHED,
                              ROTATING, ROUTE_GENERATION_EXHAUSTED, RUNNING,
                              VERIFYING_EGRESS)
from dpn.coordinator import FakeBarrier
from dpn.provider import FakeDPNProvider


def cfg(**over):
    c = {"dpn_work_units_per_ip": 20, "dpn_ip_reuse_cooldown_s": 3600,
         "dpn_drain_timeout_s": 1.0, "dpn_route_refresh_settle_s": 0.0,
         "dpn_max_route_refreshes": 2, "dpn_availability_probe_enabled": False,
         "dpn_availability_probe_retries": 2, "dpn_availability_probe_interval_s": 0}
    c.update(over)
    return c


def group(*members, gid="G1"):
    return {"group_id": gid, "members": list(members), "controller_id": "DPN_01",
            "tunnel_code": "UB"}


class TestGroupLedger(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = self._td.name
        self.t = [100.0]

    def tearDown(self):
        self._td.cleanup()

    def co(self, members=("A", "B", "C"), provider=None, barrier=None, **over):
        p = provider or FakeDPNProvider(sequence=["1.1.1.1", "2.2.2.2", "3.3.3.3"])
        return GroupLedgerCoordinator(
            self.d, group(*members), p, cfg(**over), barrier=barrier or FakeBarrier([True]),
            now_fn=lambda: self.t[0], sleep_fn=lambda s: None,
            log_fn=lambda *a, **k: None)

    def start(self, c):
        self.assertEqual(c.tick(), "ready")

    def event(self, n, device="A", group_id="G1", prefix="c", result="COMPLETED"):
        st = GroupLedgerStore(self.d)
        ev = make_work_unit_event("host", "inst-" + device, device,
                                  "%s%d" % (prefix, n), result,
                                  route_group_id=group_id)
        st.append_inbox(ev)
        return ev

    def test_A_group_aggregation_5_7_8(self):
        c = self.co()
        self.start(c)
        for i in range(5): self.event(i, "A")
        for i in range(7): self.event(i, "B")
        for i in range(8): self.event(i, "C")
        c.tick()
        g = c.snapshot()
        self.assertEqual(g["accepted_work_units"], 20)
        self.assertEqual(g["state"], LIMIT_REACHED)

    def test_B_same_event_is_idempotent(self):
        c = self.co()
        self.start(c)
        ev = self.event(1, "A")
        GroupLedgerStore(self.d).append_inbox(ev)
        c.tick()
        self.assertEqual(c.snapshot()["accepted_work_units"], 1)

    def test_C_unrelated_group_does_not_change(self):
        c = self.co()
        self.start(c)
        self.event(1, "A", group_id="OTHER")
        c.tick()
        self.assertEqual(c.snapshot()["accepted_work_units"], 0)

    def test_D_threshold_19_then_20(self):
        c = self.co(dpn_work_units_per_ip=20)
        self.start(c)
        for i in range(19): self.event(i)
        c.tick()
        self.assertEqual(c.snapshot()["state"], RUNNING)
        self.assertEqual(c.snapshot()["accepted_work_units"], 19)
        self.event(20)
        c.tick()
        self.assertEqual(c.snapshot()["state"], LIMIT_REACHED)
        self.assertEqual(c.tick(), "draining")

    def test_E_overshoot_does_not_increase_quota(self):
        c = self.co(dpn_work_units_per_ip=2)
        self.start(c)
        self.event(1); self.event(2); c.tick(); c.tick()
        self.assertEqual(c.snapshot()["state"], DRAINING)
        self.event(3)
        c.tick()
        g = c.snapshot()
        self.assertEqual(g["accepted_work_units"], 2)
        self.assertEqual(g["drain_overshoot"], 1)

    def test_F_barrier_active_blocks_rotation(self):
        c = self.co(dpn_work_units_per_ip=1, barrier=FakeBarrier([False]))
        self.start(c); self.event(1); c.tick(); c.tick()
        self.assertEqual(c.snapshot()["state"], DRAINING)
        c.tick()
        self.assertEqual(c.snapshot()["state"], DRAINING)

    def test_G_all_safe_allows_rotation(self):
        c = self.co(dpn_work_units_per_ip=1, barrier=FakeBarrier([True]))
        self.start(c); self.event(1); c.tick(); c.tick()
        self.assertEqual(c.snapshot()["state"], DRAINING)
        c.tick()
        self.assertEqual(c.snapshot()["state"], ROTATING)

    def test_H_new_epoch_counter_zero(self):
        c = self.co(dpn_work_units_per_ip=1)
        self.start(c); self.event(1); c.tick(); c.tick(); c.tick(); c.tick(); c.tick()
        g = c.snapshot()
        self.assertEqual(g["state"], RUNNING)
        self.assertEqual(g["accepted_work_units"], 0)
        self.assertEqual(g["current_public_ip"], "2.2.2.2")
        self.assertNotEqual(g["current_epoch"], "G1:epoch:1")

    def test_I_same_ip_rejected(self):
        c = self.co(provider=FakeDPNProvider(sequence=["1.1.1.1"]),
                    dpn_work_units_per_ip=1)
        self.start(c); self.event(1); c.tick(); c.tick(); c.tick(); c.tick()
        g = c.snapshot()
        self.assertEqual(g["current_epoch"], "G1:epoch:1")
        self.assertIn(g["state"], (ROTATING, ROUTE_GENERATION_EXHAUSTED,
                                   VERIFYING_EGRESS))

    def test_H2_drain_timeout_is_explicit(self):
        c = self.co(dpn_work_units_per_ip=1, barrier=FakeBarrier([False]),
                    dpn_drain_timeout_s=0.1)
        self.start(c); self.event(1); c.tick(); c.tick()
        self.assertEqual(c.snapshot()["state"], DRAINING)
        self.t[0] += 1.0
        c.tick()
        self.assertEqual(c.snapshot()["state"], "DRAIN_TIMEOUT")

    def test_worker_gate_v2_blocks_at_threshold_and_publishes_safe(self):
        import json
        import dpn.worker_hook as H
        gp = os.path.join(self.d, "groups.json")
        with open(gp, "w", encoding="utf-8") as f:
            json.dump({"G1": {"group_id": "G1", "members": ["A"], "devices": ["A"],
                              "controller": {"type": "fake"}}}, f)
        wcfg = dict(cfg())
        wcfg.update({"dpn_routing_enabled": True,
                     "dpn_group_ledger_v2_enabled": True,
                     "dpn_groups_file": gp, "dpn_state_dir": self.d})
        c = self.co(dpn_work_units_per_ip=1)
        self.start(c)
        self.assertTrue(H.gate("A", "i", cfg=wcfg))
        self.event(1)
        c.tick()
        self.assertFalse(H.gate("A", "i", cfg=wcfg))
        safe = os.path.join(self.d, "workers", "A.json")
        self.assertTrue(os.path.exists(safe))

    def test_Q_duplicate_runner_command_is_idempotent(self):
        import dpn_coordinator_run as R
        import dpn.ui_model as UI
        groups = {"G1": {"group_id": "G1", "members": ["A"], "devices": ["A"],
                         "controller": {"type": "fake", "sequence": ["1.1.1.1"]}}}
        rcfg = dict(cfg())
        rcfg.update({"dpn_routing_enabled": True,
                     "dpn_group_ledger_v2_enabled": True,
                     "dpn_state_dir": self.d, "dpn_groups_file": "x",
                     "dpn_coordinator_tick_s": 0})
        r = R.Runner(rcfg, groups, os.path.dirname(self.d), self.d, tick_s=0,
                     log_fn=lambda *a, **k: None, alive_fn=lambda d: True)
        r.step_all()
        cmd = UI.make_command("G1", "pause")
        UI.write_command(self.d, cmd)
        a1 = r.process_commands()
        a2 = r.process_commands()
        self.assertEqual(len(a1), 1)
        self.assertEqual(a2, [])

    def test_P_restart_preserves_epoch_and_counter(self):
        c = self.co(dpn_work_units_per_ip=20)
        self.start(c)
        for i in range(7): self.event(i)
        c.tick()
        self.assertEqual(c.snapshot()["accepted_work_units"], 7)
        c2 = self.co(dpn_work_units_per_ip=20)
        g = c2.snapshot()
        self.assertEqual(g["accepted_work_units"], 7)
        self.assertEqual(g["current_epoch"], "G1:epoch:1")
        self.assertEqual(c2.tick(), "ready")


if __name__ == "__main__":
    unittest.main()
