#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.7 DeviceBackend contract tests (offline, no device)."""
import base64
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from device_backend import (ADBBackend, AgentBackend, BackendError,
                            ShadowMutationBlocked, make_backend)
from device_backend.models import tree_from_adb_xml, tree_from_agent_data


class _Shell:
    def __init__(self, output="Success", exit_code=0):
        self.output = output
        self.exit_code = exit_code


class FakeADB:
    def __init__(self):
        self.calls = []
        self.info = {"displayRotation": 0, "displayDensity": 420, "uptime": 1234}

    def dump_hierarchy(self):
        return ('<hierarchy><node package="com.flixdigital.dramaclassic" '
                'class="android.widget.FrameLayout" bounds="[0,0][1080,2280]" '
                'clickable="false" enabled="true"><node '
                'package="com.flixdigital.dramaclassic" class="android.widget.Button" '
                'text="ASSISTIR" resource-id="com.flixdigital.dramaclassic:id/btnRewardAds" '
                'bounds="[251,1137][829,1295]" clickable="true" enabled="true"/>'
                '</node></hierarchy>')

    def window_size(self):
        return 1080, 2280

    def screenshot(self):
        from PIL import Image
        return Image.new("RGB", (1080, 2280), "black")

    def app_current(self):
        return {"package": "com.flixdigital.dramaclassic",
                "activity": "com.flixdigital.dramaclassic.MainActivity"}

    def shell(self, cmd):
        self.calls.append(cmd)
        return _Shell("Success" if "pm clear" in cmd else "", 0)

    def click(self, x, y): self.calls.append(("click", x, y))
    def swipe(self, *a, **k): self.calls.append(("swipe", a, k))
    def press(self, k): self.calls.append(("press", k))
    def app_start(self, p): self.calls.append(("start", p))
    def app_stop(self, p): self.calls.append(("stop", p))


def _agent_tree_result():
    data = {"package": "com.flixdigital.dramaclassic",
            "activity": "com.flixdigital.dramaclassic.MainActivity",
            "display": {"width_px": 1080, "height_px": 2280, "rotation": 0, "density_dpi": 420},
            "nodes": [
                {"node_id": "n0", "parent_id": None, "depth": 0,
                 "class": "android.widget.FrameLayout", "text": "", "content_desc": "",
                 "resource_id": "", "bounds": [0, 0, 1080, 2280], "clickable": False,
                 "enabled": True, "visible": True, "scrollable": False},
                {"node_id": "n1", "parent_id": "n0", "depth": 1,
                 "class": "android.widget.Button", "text": "ASSISTIR",
                 "content_desc": "", "resource_id": "com.flixdigital.dramaclassic:id/btnRewardAds",
                 "bounds": [251, 1137, 829, 1295], "clickable": True, "enabled": True,
                 "visible": True, "scrollable": False}]}
    return data


class TestADBBackend(unittest.TestCase):
    def test_contract_reads_and_mutations(self):
        a = ADBBackend(FakeADB(), device="dummy")
        self.assertEqual(a.name, "adb")
        t = a.get_ui_tree()
        self.assertEqual(t.source, "adb")
        self.assertEqual(t.width, 1080)
        self.assertTrue(any(n.resource_id.endswith("btnRewardAds") for n in t.nodes))
        fg = a.get_foreground_app()
        self.assertEqual(fg.package, "com.flixdigital.dramaclassic")
        self.assertIn("MainActivity", fg.activity)
        frame = a.capture_screen()
        self.assertEqual(frame.png[:4], b"\x89PNG")
        self.assertEqual((frame.width, frame.height), (1080, 2280))
        st = a.get_status()
        self.assertTrue(st.screen_on)
        for r in (a.tap(1, 2), a.swipe(1, 2, 3, 4), a.back(), a.home(),
                  a.start_app("com.qa.target"), a.stop_app("com.qa.target"),
                  a.clear_app_data("com.qa.target")):
            self.assertTrue(r.ok, r.as_dict())
            self.assertEqual(r.backend, "adb")

    def test_factory_default_adb(self):
        self.assertIsInstance(make_backend("adb", d=FakeADB()), ADBBackend)
        with self.assertRaises(BackendError):
            make_backend("nope")


class TestAgentBackend(unittest.TestCase):
    def _fn(self, status="success", uncertain=False):
        calls = []

        def fn(op, params):
            calls.append((op, params))
            if op == "GET_UI_TREE":
                return {"status": "success", "data": _agent_tree_result()}
            if op == "CAPTURE_SCREEN":
                png = b"\x89PNG\r\n\x1a\n"
                return {"status": "success", "data": {
                    "width": 1080, "height": 2280, "rotation": 0, "format": "png",
                    "sha256": "x", "payload": base64.b64encode(png).decode()}}
            if op == "GET_FOREGROUND_APP":
                return {"status": "success", "data": {
                    "package": "com.flixdigital.dramaclassic",
                    "activity": "com.flixdigital.dramaclassic.MainActivity",
                    "component": "com.flixdigital.dramaclassic/com.flixdigital.dramaclassic.MainActivity"}}
            if op == "GET_STATUS":
                return {"status": "success", "data": {"screen_on": True, "keyguard_locked": False,
                                                      "display": {"width_px": 1080, "height_px": 2280}}}
            return {"status": "uncertain" if uncertain else status,
                    "error": "COMMAND_TIMEOUT" if uncertain else "",
                    "data": {"package": params.get("package")}}
        fn.calls = calls
        return fn

    def test_contract_reads(self):
        a = AgentBackend(self._fn(), agent_id="a1", execution_mode="normal")
        self.assertEqual(a.name, "agent")
        self.assertEqual(a.get_ui_tree().source, "agent")
        self.assertEqual(a.get_foreground_app().package, "com.flixdigital.dramaclassic")
        self.assertEqual(a.capture_screen().width, 1080)
        self.assertTrue(a.get_status().screen_on)

    def test_mutations_and_uncertain(self):
        fn = self._fn()
        a = AgentBackend(fn, agent_id="a1", execution_mode="normal")
        for r in (a.tap(1, 2), a.swipe(1, 2, 3, 4), a.back(), a.home(),
                  a.start_app("com.qa.target"), a.stop_app("com.qa.target"),
                  a.clear_app_data("com.qa.target")):
            self.assertTrue(r.ok, r.as_dict())
        u = AgentBackend(self._fn(uncertain=True), agent_id="a1", execution_mode="normal")
        r = u.clear_app_data("com.qa.target")
        self.assertEqual(r.status, "uncertain")
        self.assertTrue(r.uncertain)

    def test_shadow_blocks_all_mutations(self):
        a = AgentBackend(self._fn(), agent_id="a1", execution_mode="shadow")
        for call in (lambda: a.tap(1, 2), lambda: a.swipe(1, 2, 3, 4),
                     lambda: a.back(), lambda: a.home(),
                     lambda: a.start_app("com.qa.target"),
                     lambda: a.stop_app("com.qa.target"),
                     lambda: a.clear_app_data("com.qa.target")):
            with self.assertRaises(ShadowMutationBlocked):
                call()

    def test_factory_requires_controller(self):
        with self.assertRaises(BackendError):
            make_backend("agent")
        self.assertIsInstance(make_backend("agent", command_fn=self._fn(), cfg={}),
                              AgentBackend)


class TestNormalizationEquivalence(unittest.TestCase):
    def test_e36_fixture_normalized_nodes_match(self):
        base = os.path.join(PROJ, "tests", "fixtures", "e36", "main_reward")
        if not os.path.isdir(base):
            self.skipTest("FIXTURE_MISSING")
        import json
        adb_tree = tree_from_adb_xml(open(os.path.join(base, "adb.xml"), encoding="utf-8").read())
        agent_tree = tree_from_agent_data(json.load(open(os.path.join(base, "agent.json"), encoding="utf-8")))
        def keys(t):
            return {(n.cls.split(".")[-1], n.text, n.resource_id)
                    for n in t.nodes if n.text or n.resource_id}
        self.assertEqual(keys(adb_tree), keys(agent_tree))
        # Clickable element bounds must be identical; full-screen containers differ by
        # the documented uiautomator nav-bar clip (Agent uses screen coordinates).
        for side in (adb_tree, agent_tree):
            btn = [n for n in side.nodes if n.resource_id.endswith("btnRewardAds")][0]
            self.assertEqual(btn.bounds, (251, 1137, 829, 1295))


if __name__ == "__main__":
    unittest.main()
