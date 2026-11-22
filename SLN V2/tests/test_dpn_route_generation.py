#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2.6 - route generations, candidate exhaustion, refresh and DEGRADED."""
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn.availability import AVAILABLE, UNAVAILABLE, SequenceAvailabilityProbe
from dpn.coordinator import FakeBarrier
from dpn.group_ledger import (GroupLedgerCoordinator, GroupLedgerStore,
                              make_work_unit_event, DEGRADED, REFRESHING_ROUTES,
                              ROTATING, ROUTE_GENERATION_EXHAUSTED, RUNNING,
                              WAIT_REFRESH, DISCOVERING_ROUTES)
from dpn.provider import FakeDPNProvider


class RefreshProvider(FakeDPNProvider):
    def __init__(self, sequence, refreshed_sequence=None, node_id="node_fake"):
        super().__init__(sequence=sequence, node_id=node_id)
        self.refreshed_sequence = list(refreshed_sequence or sequence)
        self.get_candidates_calls = 0

    def get_candidates(self):
        self.get_candidates_calls += 1
        return [{"public_ip": ip, "tunnel_code": self._node_id} for ip in self._seq]

    def refresh_route_set(self):
        self.refresh_calls += 1
        self._seq = list(self.refreshed_sequence)
        self._i = 0
        return True


def cfg(**over):
    c = {"dpn_work_units_per_ip": 1, "dpn_ip_reuse_cooldown_s": 3600,
         "dpn_drain_timeout_s": 1, "dpn_route_refresh_settle_s": 0,
         "dpn_max_route_refreshes": 2, "dpn_availability_probe_enabled": True,
         "dpn_availability_probe_retries": 0,
         "dpn_availability_probe_interval_s": 0}
    c.update(over)
    return c


def group():
    return {"group_id": "G1", "members": ["A"], "controller_id": "DPN_01"}


class TestRouteGeneration(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def co(self, provider, probe=None, **over):
        return GroupLedgerCoordinator(
            self.d, group(), provider, cfg(**over), barrier=FakeBarrier([True]),
            probe=probe or SequenceAvailabilityProbe([AVAILABLE]),
            now_fn=lambda: 100.0, sleep_fn=lambda s: None,
            log_fn=lambda *a, **k: None)

    def event(self, n=1):
        GroupLedgerStore(self.d).append_inbox(make_work_unit_event(
            "h", "i", "A", str(n), "COMPLETED", route_group_id="G1"))

    def drain_to_rotate(self, c):
        c.tick()  # start
        self.event(1)
        c.tick()  # LIMIT
        c.tick()  # DRAINING
        c.tick()  # ROTATING
        c.tick()  # VERIFYING_EGRESS

    def run_until(self, c, pred, max_ticks=40):
        for _ in range(max_ticks):
            if pred(c.snapshot()):
                return c.snapshot()
            c.tick()
        self.fail("condition not reached: %r" % (c.snapshot(),))

    def test_L_generation_exhausted_to_refresh(self):
        prov = RefreshProvider(["1.1.1.1", "2.2.2.2"], refreshed_sequence=["1.1.1.1", "2.2.2.2", "3.3.3.3"])
        probe = SequenceAvailabilityProbe([UNAVAILABLE, UNAVAILABLE, AVAILABLE])
        c = self.co(prov, probe, dpn_max_route_refreshes=2)
        self.drain_to_rotate(c)
        st = self.run_until(c, lambda s: s["state"] == ROUTE_GENERATION_EXHAUSTED)
        self.assertEqual(st["state"], ROUTE_GENERATION_EXHAUSTED)
        st = self.run_until(c, lambda s: s["state"] in (WAIT_REFRESH, REFRESHING_ROUTES))
        self.assertIn(st["state"], (WAIT_REFRESH, REFRESHING_ROUTES))

    def test_M_refresh_new_generation(self):
        prov = RefreshProvider(["1.1.1.1", "2.2.2.2"], refreshed_sequence=["3.3.3.3", "4.4.4.4"])
        probe = SequenceAvailabilityProbe([UNAVAILABLE, UNAVAILABLE, AVAILABLE])
        c = self.co(prov, probe, dpn_max_route_refreshes=2)
        self.drain_to_rotate(c)
        g = self.run_until(c, lambda s: s["state"] == RUNNING and
                           s["current_public_ip"] != "1.1.1.1")
        self.assertEqual(g["state"], RUNNING)
        self.assertEqual(g["accepted_work_units"], 0)
        self.assertEqual(prov.refresh_calls, 1)

    def test_N_refresh_no_change(self):
        prov = RefreshProvider(["1.1.1.1", "2.2.2.2"], refreshed_sequence=["1.1.1.1", "2.2.2.2"])
        probe = SequenceAvailabilityProbe([UNAVAILABLE, UNAVAILABLE, UNAVAILABLE])
        c = self.co(prov, probe, dpn_max_route_refreshes=2)
        self.drain_to_rotate(c)
        st = self.run_until(c, lambda s: s["refresh_attempt"] >= 1)
        self.assertGreaterEqual(st["refresh_attempt"], 1)
        self.assertIn(st["state"], (REFRESHING_ROUTES, WAIT_REFRESH, DEGRADED))

    def test_full_mock_timeline_threshold5(self):
        from dpn import state as S
        prov = RefreshProvider(["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"],
                               refreshed_sequence=["5.5.5.5"])
        probe = SequenceAvailabilityProbe([UNAVAILABLE, UNAVAILABLE, AVAILABLE])
        S.record_ip_use(self.d, "3.3.3.3", "G1", now=100.0)
        c = self.co(prov, probe, dpn_work_units_per_ip=5, dpn_max_route_refreshes=2)
        c.tick()  # start on 1.1.1.1
        for i in range(5):
            self.event(i + 1)
        c.tick()  # LIMIT
        c.tick()  # DRAINING
        c.tick()  # ROTATING
        st = self.run_until(c, lambda s: s["state"] == "RUNNING" and
                            s["current_public_ip"] == "5.5.5.5", max_ticks=60)
        self.assertEqual(st["state"], "RUNNING")
        self.assertEqual(st["accepted_work_units"], 0)
        self.assertEqual(prov.refresh_calls, 1)

    def test_O_refresh_max_degrades(self):
        prov = RefreshProvider(["1.1.1.1", "2.2.2.2"], refreshed_sequence=["1.1.1.1", "2.2.2.2"])
        probe = SequenceAvailabilityProbe([UNAVAILABLE, UNAVAILABLE, UNAVAILABLE])
        c = self.co(prov, probe, dpn_max_route_refreshes=1)
        self.drain_to_rotate(c)
        st = self.run_until(c, lambda s: s["state"] == DEGRADED)
        self.assertEqual(st["state"], DEGRADED)


if __name__ == "__main__":
    unittest.main()
