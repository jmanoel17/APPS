#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.8B offline tests for hidden-controls reveal eligibility and safety."""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

import hidden_controls as HC


def cfg(**kw):
    out = {
        "fast_close_hidden_controls_reveal_enabled": True,
        "hidden_controls_min_age_s": 5.0,
        "max_reveal_attempts_per_stage": 1,
        "reveal_observation_window_ms": 1000,
        "hidden_controls_require_history": True,
        "fast_close_top_region_ratio": 0.15,
    }
    out.update(kw)
    return out


def node(cls="android.view.View", bounds="[0,0][1080,2280]", clickable=False,
         text="", desc="", rid=""):
    a = {"class": cls, "bounds": bounds, "clickable": "true" if clickable else "false",
         "text": text, "content-desc": desc, "resource-id": rid,
         "visible-to-user": "true", "enabled": "true"}
    return ET.Element("node", a)


def an(nodes):
    return {"nodes": nodes}


class TestChooseRevealPoint(unittest.TestCase):
    def test_center_safe_with_bottom_cta(self):
        nodes = [node(text="ad_video"),
                 node(cls="android.widget.Button", bounds="[690,2078][1008,2228]",
                      clickable=True, text="Instalar")]
        p = HC.choose_reveal_point(an(nodes), 1080, 2280, cfg())
        self.assertTrue(p["safe"])
        self.assertEqual((p["x"], p["y"]), (540, 1026))

    def test_full_screen_clickable_rejected(self):
        p = HC.choose_reveal_point(an([node(clickable=True)]), 1080, 2280, cfg())
        self.assertFalse(p["safe"])

    def test_top_control_not_selected(self):
        p = HC.choose_reveal_point(an([node()]), 1080, 2280,
                                   cfg(fast_close_top_region_ratio=0.70))
        self.assertFalse(p["safe"])


class TestHiddenControlsTracker(unittest.TestCase):
    def tracker(self, **kw):
        return HC.HiddenControlsTracker(cfg(**kw))

    def test_no_history_no_reveal(self):
        t = self.tracker()
        self.assertEqual(t.observe("video", 100.0, False, True, "x", safe_point=True)["action"], "NONE")
        self.assertEqual(t.observe("video", 110.0, False, True, "x", safe_point=True)["reason"], "NO_HISTORY")

    def test_too_early_no_reveal(self):
        t = self.tracker()
        t.observe("video", 0.0, True, False)
        d = t.observe("video", 2.0, False, True, "x", safe_point=True)
        self.assertEqual(d["action"], "NONE")
        self.assertEqual(d["reason"], "TOO_EARLY")

    def test_countdown_blocks(self):
        t = self.tracker()
        t.observe("video", 0.0, True, False)
        d = t.observe("video", 10.0, True, True, "x", safe_point=True)
        self.assertEqual(d["action"], "NONE")
        self.assertEqual(d["reason"], "CONTROLS_VISIBLE")

    def test_store_or_ineligible_blocks(self):
        t = self.tracker()
        t.observe("video", 0.0, True, False)
        d = t.observe("video", 10.0, False, False, "x", safe_point=True)
        self.assertIn(d["action"], ("NONE", "REVEAL_PENDING"))
        self.assertNotEqual(d["action"], "REVEAL")

    def test_no_safe_point_no_reveal(self):
        t = self.tracker()
        t.observe("video", 0.0, True, False)
        d = t.observe("video", 10.0, False, True, "x", safe_point=False)
        self.assertEqual(d["action"], "NONE")
        self.assertEqual(d["reason"], "NO_SAFE_POINT")
        self.assertEqual(t.attempts, 0)

    def test_reveal_once_and_no_effect(self):
        t = self.tracker()
        t.observe("video", 0.0, True, False)
        t.observe("video", 1.0, False, True, "before", safe_point=True)  # starts absence
        d = t.observe("video", 6.0, False, True, "before", safe_point=True)
        self.assertEqual(d["action"], "REVEAL")
        self.assertEqual(t.attempts, 1)
        t.on_tap_result("success", "cmd-1")
        self.assertEqual(t.observe("video", 6.2, False, True, "x", safe_point=True)["action"],
                         "REVEAL_PENDING")
        d2 = t.observe("video", 7.2, False, True, "after", safe_point=True)
        self.assertEqual(d2["action"], "REVEAL_NO_EFFECT")
        d3 = t.observe("video", 8.0, False, True, "after", safe_point=True)
        self.assertEqual(d3["action"], "BLOCKED_MAX")
        self.assertEqual(t.attempts, 1)

    def test_success_candidate_appears(self):
        t = self.tracker()
        t.observe("video", 0.0, True, False)
        t.observe("video", 1.0, False, True)
        self.assertEqual(t.observe("video", 6.0, False, True, "before", safe_point=True)["action"],
                         "REVEAL")
        t.on_tap_result("success", "cmd-2")
        d = t.observe("video", 6.5, True, False, "after")
        self.assertEqual(d["action"], "REVEAL_SUCCESS")
        self.assertEqual(t.attempts, 1)

    def test_stage_change_allows_one_more(self):
        t = self.tracker()
        t.observe("video", 0.0, True, False)
        t.observe("video", 1.0, False, True)
        self.assertEqual(t.observe("video", 6.0, False, True, "a", safe_point=True)["action"], "REVEAL")
        t.on_tap_result("failure", "cmd-3")
        t.observe("endcard", 7.0, True, False)
        t.observe("endcard", 8.0, False, True)
        self.assertEqual(t.observe("endcard", 13.0, False, True, "b", safe_point=True)["action"],
                         "REVEAL")
        self.assertEqual(t.attempts, 1)

    def test_uncertain_blocks_retry(self):
        t = self.tracker()
        t.observe("video", 0.0, True, False)
        t.observe("video", 1.0, False, True)
        self.assertEqual(t.observe("video", 6.0, False, True, "a", safe_point=True)["action"], "REVEAL")
        t.on_tap_result("uncertain", "cmd-u")
        self.assertEqual(t.observe("video", 8.0, False, True, "b", safe_point=True)["action"],
                         "BLOCKED_UNCERTAIN")
        self.assertEqual(t.attempts, 1)

    def test_same_command_id_is_tracked_but_no_second_tap(self):
        t = self.tracker()
        t.observe("video", 0.0, True, False)
        t.observe("video", 1.0, False, True)
        self.assertEqual(t.observe("video", 6.0, False, True, "a", safe_point=True)["action"], "REVEAL")
        t.on_tap_result("success", "same-id")
        t.on_tap_result("success", "same-id")
        # The Agent/controller owns command_id dedupe; the tracker never issues another reveal.
        self.assertEqual(t.observe("video", 10.0, False, True, "b", safe_point=True)["action"],
                         "REVEAL_NO_EFFECT")


if __name__ == "__main__":
    unittest.main()
