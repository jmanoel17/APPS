#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.6 offline tests: normalized adapters and shared decision engine (no device)."""
import json
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

import decision_parity_replay as dp


def _meta(fg_pkg="com.flixdigital.dramaclassic",
          fg_act="com.flixdigital.dramaclassic.MainActivity", **kw):
    fg = {"package": fg_pkg, "activity": fg_act,
          "component": fg_pkg + "/" + fg_act}
    m = {"case": "synthetic", "ad_type": "interstitial", "armed": True,
         "reward_completed": True, "elapsed": 60,
         "foreground_adb": fg, "foreground_agent": fg,
         "display": {"width_px": 1080, "height_px": 2280,
                     "rotation": 0, "density_dpi": 420}}
    m.update(kw)
    return m


def _adb_xml(nodes_xml, td):
    p = os.path.join(td, "adb.xml")
    open(p, "w", encoding="utf-8").write("<hierarchy>" + nodes_xml + "</hierarchy>")
    return p


def _agent_json(nodes, td, pkg="com.flixdigital.dramaclassic"):
    data = {"package": pkg, "nodes": nodes,
            "display": {"width_px": 1080, "height_px": 2280,
                        "rotation": 0, "density_dpi": 420}}
    p = os.path.join(td, "agent.json")
    json.dump(data, open(p, "w", encoding="utf-8"))
    return p


def _main_xml():
    return ('<node package="com.flixdigital.dramaclassic" class="android.widget.FrameLayout" '
            'bounds="[0,0][1080,2280]" clickable="false" enabled="true">'
            '<node package="com.flixdigital.dramaclassic" class="android.widget.Button" '
            'text="ASSISTIR ANUNCIO" resource-id="com.flixdigital.dramaclassic:id/btnRewardAds" '
            'bounds="[251,1137][829,1295]" clickable="true" enabled="true"/></node>')


def _main_agent():
    return [
        {"node_id": "n0", "parent_id": None, "depth": 0,
         "class": "android.widget.FrameLayout", "text": "", "content_desc": "",
         "resource_id": "", "bounds": [0, 0, 1080, 2280], "clickable": False,
         "enabled": True, "visible": True, "scrollable": False, "password": False,
         "child_count": 1},
        {"node_id": "n1", "parent_id": "n0", "depth": 1,
         "class": "android.widget.Button", "text": "ASSISTIR ANUNCIO", "content_desc": "",
         "resource_id": "com.flixdigital.dramaclassic:id/btnRewardAds",
         "bounds": [251, 1137, 829, 1295], "clickable": True, "enabled": True,
         "visible": True, "scrollable": False, "password": False, "child_count": 0},
    ]


def _close_xml():
    return ('<node package="com.flixdigital.dramaclassic" class="android.widget.FrameLayout" '
            'bounds="[0,0][1080,2280]" clickable="false" enabled="true">'
            '<node package="com.flixdigital.dramaclassic" class="android.widget.FrameLayout" '
            'resource-id="close_button" bounds="[950,150][1020,220]" clickable="true" '
            'enabled="true"/></node>')


def _close_agent():
    return [
        {"node_id": "n0", "parent_id": None, "depth": 0,
         "class": "android.widget.FrameLayout", "text": "", "content_desc": "",
         "resource_id": "", "bounds": [0, 0, 1080, 2280], "clickable": False,
         "enabled": True, "visible": True, "scrollable": False, "password": False,
         "child_count": 1},
        {"node_id": "n1", "parent_id": "n0", "depth": 1,
         "class": "android.widget.FrameLayout", "text": "", "content_desc": "",
         "resource_id": "close_button", "bounds": [950, 150, 1020, 220],
         "clickable": True, "enabled": True, "visible": True, "scrollable": False,
         "password": False, "child_count": 0},
    ]


class TestE36Adapters(unittest.TestCase):
    def test_agent_adapter_preserves_hierarchy_and_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            tree = dp.AgentTreeAdapter.load(_agent_json(_main_agent(), td), _meta())
            self.assertEqual(tree.source, "agent")
            self.assertEqual(len(tree.nodes), 2)
            self.assertEqual(tree.nodes[1].parent_id, "n0")
            self.assertEqual(tree.nodes[1].resource_id,
                             "com.flixdigital.dramaclassic:id/btnRewardAds")
            self.assertEqual(tree.nodes[1].bounds, (251, 1137, 829, 1295))
            self.assertTrue(tree.nodes[1].clickable)
            self.assertEqual(tree.root.find(".//node[@resource-id='com.flixdigital.dramaclassic:id/btnRewardAds']") is not None, True)

    def test_adb_adapter_preserves_hierarchy_and_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            tree = dp.ADBTreeAdapter.load(_adb_xml(_main_xml(), td), _meta())
            self.assertEqual(tree.source, "adb")
            self.assertEqual(len(tree.nodes), 2)
            self.assertEqual(tree.nodes[1].parent_id, "adb-0")
            self.assertEqual(tree.nodes[1].bounds, (251, 1137, 829, 1295))
            self.assertTrue(tree.nodes[1].clickable)


class TestE36Decisions(unittest.TestCase):
    def test_main_reward_parity(self):
        with tempfile.TemporaryDirectory() as td:
            a = dp.ADBTreeAdapter.load(_adb_xml(_main_xml(), td), _meta())
            b = dp.AgentTreeAdapter.load(_agent_json(_main_agent(), td), _meta("", ""))
            da = dp.decide(a, _meta())
            db = dp.decide(b, _meta("", ""))
            self.assertEqual((da.state, db.state), ("main", "main"))
            self.assertEqual((da.decision, db.decision), ("CLICK", "CLICK"))
            self.assertEqual(da.candidate.source, "reward_button")
            self.assertEqual(db.candidate.source, "reward_button")

    def test_close_authorization_parity(self):
        fg = _meta("com.flixdigital.dramaclassic",
                   "com.applovin.adview.AppLovinFullscreenActivity", armed=True)
        with tempfile.TemporaryDirectory() as td:
            a = dp.ADBTreeAdapter.load(_adb_xml(_close_xml(), td), fg)
            b = dp.AgentTreeAdapter.load(_agent_json(_close_agent(), td), fg)
            da, db = dp.decide(a, fg), dp.decide(b, fg)
            self.assertEqual(da.decision, "CLICK")
            self.assertEqual(db.decision, "CLICK")
            self.assertTrue(da.candidate and db.candidate)
            self.assertTrue(da.authorized and db.authorized)

    def test_gate_closed_never_clicks(self):
        fg = _meta("com.flixdigital.dramaclassic",
                   "com.applovin.adview.AppLovinFullscreenActivity", armed=False)
        with tempfile.TemporaryDirectory() as td:
            a = dp.ADBTreeAdapter.load(_adb_xml(_close_xml(), td), fg)
            b = dp.AgentTreeAdapter.load(_agent_json(_close_agent(), td), fg)
            da, db = dp.decide(a, fg), dp.decide(b, fg)
            self.assertEqual((da.decision, db.decision), ("OBSERVE", "OBSERVE"))
            self.assertFalse(da.authorized or db.authorized)

    def test_store_back_parity(self):
        fg = _meta("com.android.vending",
                   "com.google.android.finsky.transparentmainactivity.TransparentMainActivityPrivate")
        with tempfile.TemporaryDirectory() as td:
            a = dp.ADBTreeAdapter.load(_adb_xml("", td), fg)
            b = dp.AgentTreeAdapter.load(_agent_json([], td, "com.android.vending"), fg)
            da, db = dp.decide(a, fg), dp.decide(b, fg)
            self.assertEqual((da.state, db.state), ("store", "store"))
            self.assertEqual((da.decision, db.decision), ("BACK", "BACK"))

    def test_candidate_match_thresholds(self):
        c1 = dp.Candidate("x", (100, 100, 200, 200), (150, 150))
        c2 = dp.Candidate("x", (110, 110, 210, 210), (160, 160))
        c3 = dp.Candidate("x", (500, 500, 600, 600), (550, 550))
        self.assertTrue(dp._candidate_match(c1, c2)[0])
        self.assertFalse(dp._candidate_match(c1, c3)[0])

    def test_fixture_replay_has_no_critical_or_agent_action(self):
        fixtures = os.path.join(PROJ, "tests", "fixtures", "e36")
        if not os.path.isdir(fixtures):
            self.skipTest("FIXTURE_MISSING")
        with tempfile.TemporaryDirectory() as td:
            rep = dp.replay(fixtures, os.path.join(td, "parity.json"))
            self.assertEqual(rep["safety"]["adb_observe_agent_click"], 0)
            self.assertEqual(rep["safety"]["agent_actions_executed"], 0)
            self.assertEqual(rep["metrics"]["critical_mismatches"], 0)
            self.assertGreaterEqual(rep["metrics"]["cases"], 1)


if __name__ == "__main__":
    unittest.main()
