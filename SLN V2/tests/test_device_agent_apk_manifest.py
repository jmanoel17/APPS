#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Manifest surface tests: QA/Provision debug-only, Provision exported only in debug."""
import os
import unittest
import xml.etree.ElementTree as ET

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(PROJ, "device_agent_apk", "app", "src")
A = "{http://schemas.android.com/apk/res/android}"


def activities(path):
    root = ET.parse(path).getroot()
    out = {}
    for act in root.iter("activity"):
        name = act.get(A + "name") or ""
        out[name.split(".")[-1]] = act.get(A + "exported")
    return out


class TestApkManifests(unittest.TestCase):
    def test_main_has_no_qa_or_provision(self):
        acts = activities(os.path.join(SRC, "main", "AndroidManifest.xml"))
        self.assertNotIn("QaActivity", acts)
        self.assertNotIn("ProvisionActivity", acts)

    def test_debug_has_qa_and_provision_exported(self):
        acts = activities(os.path.join(SRC, "debug", "AndroidManifest.xml"))
        self.assertEqual(acts.get("QaActivity"), "true")
        self.assertEqual(acts.get("ProvisionActivity"), "true")

    def test_merged_manifests_if_built(self):
        base = os.path.join(PROJ, "device_agent_apk", "app", "build", "intermediates",
                            "merged_manifests")
        dbg = os.path.join(base, "debug", "processDebugManifest", "AndroidManifest.xml")
        rel = os.path.join(base, "release", "processReleaseManifest", "AndroidManifest.xml")
        if os.path.exists(dbg):
            acts = activities(dbg)
            self.assertEqual(acts.get("QaActivity"), "true")
            self.assertEqual(acts.get("ProvisionActivity"), "true")
        if os.path.exists(rel):
            acts = activities(rel)
            self.assertNotIn("QaActivity", acts)
            self.assertNotIn("ProvisionActivity", acts)


if __name__ == "__main__":
    unittest.main()
