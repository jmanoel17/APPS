#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.6R real fixture checks (offline; no device, no Agent action)."""
import json
import os
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL = os.path.join(PROJ, "tests", "fixtures", "e36_real")


class TestE36RRealFixtures(unittest.TestCase):
    def test_critical_real_fixtures_match(self):
        required = ["sub_ad_close", "playable", "store", "reward", "return_main"]
        for name in required:
            d = os.path.join(REAL, name)
            self.assertTrue(os.path.isdir(d), "FIXTURE_MISSING " + name)
            dec = json.load(open(os.path.join(d, "decision.json"), encoding="utf-8"))
            self.assertEqual(dec["agent"]["state"], dec["adb_reference"]["state"], name)
            self.assertEqual(dec["agent"]["decision"], dec["adb_reference"]["decision"], name)
            self.assertEqual(dec["agent"]["authorized"], dec["adb_reference"]["authorized"], name)

    def test_subad_close_real_signature(self):
        d = os.path.join(REAL, "sub_ad_close")
        if not os.path.isdir(d):
            self.skipTest("FIXTURE_MISSING")
        dec = json.load(open(os.path.join(d, "decision.json"), encoding="utf-8"))
        for side in ("agent", "adb_reference"):
            cand = dec[side]["candidate"]
            self.assertIsNotNone(cand)
            self.assertEqual(cand["source"], "subad_close")
            self.assertEqual(cand["bounds"], [958, 217, 1028, 287])
            self.assertTrue(dec[side]["authorized"])
            self.assertEqual(dec[side]["decision"], "CLICK")

    def test_capture_never_acted(self):
        report = os.path.join(PROJ, "state", "device_agents", "e36r_report.json")
        if not os.path.exists(report):
            self.skipTest("REPORT_MISSING")
        r = json.load(open(report, encoding="utf-8"))
        self.assertEqual(r.get("shadow_actions_executed"), 0)
        self.assertEqual(r.get("metrics", {}).get("shadow_actions_executed"), 0)


if __name__ == "__main__":
    unittest.main()
