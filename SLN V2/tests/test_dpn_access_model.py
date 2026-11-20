#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2.5B — modelo puro DPN→tunnels→devices + árvore UI (sem I/O)."""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn.access_model import build_access_snapshot
import dpn.ui_model as UI


def rec(mac, mode, region="", ip="192.168.1.10", name="dev"):
    return {"mac": mac, "name": name, "ip": ip, "routeMode": mode, "regionCode": region}


def tunnels():
    return [
        {"tunnelCode": "GB", "activeIp": "1.1.1.1", "activeNum": 3, "countryCode": "GB", "regionCode": "EUW"},
        {"tunnelCode": "UB", "activeIp": "2.2.2.2", "activeNum": 4, "countryCode": "UB", "regionCode": "AMN"},
    ]


def tmap(snap):
    return {t["tunnel_code"]: t for t in snap["tunnels"]}


class TestAccessModel(unittest.TestCase):
    def test_01_full_to_tunnel(self):
        ac = {"online": [rec("aa", "full", "GB")], "offline": []}
        snap = build_access_snapshot("DPN_01", tunnels(), ac)
        self.assertEqual([d["mac"] for d in tmap(snap)["GB"]["devices"]], ["aa"])
        self.assertEqual(tmap(snap)["UB"]["devices"], [])
        self.assertEqual(tmap(snap)["GB"]["tunnel_ip"], "1.1.1.1")
        self.assertFalse(tmap(snap)["GB"]["egress_verified"])

    def test_02_smart_separate(self):
        ac = {"online": [rec("bb", "smart")], "offline": []}
        snap = build_access_snapshot("DPN_01", tunnels(), ac)
        self.assertEqual([d["mac"] for d in snap["smart_devices"]], ["bb"])
        self.assertEqual(tmap(snap)["GB"]["devices"], [])

    def test_03_disabled_separate(self):
        ac = {"online": [rec("cc", "disabled", "GB")], "offline": []}
        snap = build_access_snapshot("DPN_01", tunnels(), ac)
        self.assertEqual([d["mac"] for d in snap["disabled_devices"]], ["cc"])
        self.assertEqual(tmap(snap)["GB"]["devices"], [])

    def test_04_blackhole_separate(self):
        ac = {"online": [rec("dd", "blackHole")], "offline": []}
        snap = build_access_snapshot("DPN_01", tunnels(), ac)
        self.assertEqual([d["mac"] for d in snap["blackhole_devices"]], ["dd"])

    def test_05_online_offline(self):
        ac = {"online": [rec("a1", "full", "GB")], "offline": [rec("b1", "full", "GB")]}
        snap = build_access_snapshot("DPN_01", tunnels(), ac)
        devs = tmap(snap)["GB"]["devices"]
        flags = {d["mac"]: d["online"] for d in devs}
        self.assertTrue(flags["a1"]); self.assertFalse(flags["b1"])
        self.assertEqual(snap["totals"], {"online": 1, "total": 2})

    def test_06_two_devices_same_tunnel(self):
        ac = {"online": [rec("a", "full", "GB"), rec("b", "full", "GB")], "offline": []}
        snap = build_access_snapshot("DPN_01", tunnels(), ac)
        self.assertEqual(len(tmap(snap)["GB"]["devices"]), 2)

    def test_07_multiple_tunnels_same_dpn(self):
        ac = {"online": [rec("a", "full", "GB"), rec("b", "full", "UB")], "offline": []}
        snap = build_access_snapshot("DPN_01", tunnels(), ac)
        self.assertEqual([d["mac"] for d in tmap(snap)["GB"]["devices"]], ["a"])
        self.assertEqual([d["mac"] for d in tmap(snap)["UB"]["devices"]], ["b"])

    def test_08_same_code_diff_ip_across_dpns(self):
        t1 = [{"tunnelCode": "GB", "activeIp": "1.1.1.1", "activeNum": 3, "countryCode": "GB", "regionCode": ""}]
        t2 = [{"tunnelCode": "GB", "activeIp": "9.9.9.9", "activeNum": 5, "countryCode": "GB", "regionCode": ""}]
        ac = {"online": [rec("a", "full", "GB")], "offline": []}
        s1 = build_access_snapshot("DPN_01", t1, ac)
        s2 = build_access_snapshot("DPN_02", t2, ac)
        self.assertEqual(s1["tunnels"][0]["tunnel_ip"], "1.1.1.1")
        self.assertEqual(s2["tunnels"][0]["tunnel_ip"], "9.9.9.9")

    def test_09_ui_tree(self):
        ac = {"online": [rec("a", "full", "GB"), rec("s", "smart"), rec("d", "disabled")],
              "offline": [rec("o", "full", "GB")]}
        snap = build_access_snapshot("DPN_01", tunnels(), ac)
        status = {"dpns": {"DPN_01": {"state": "RUNNING", "node_id": "GB", "current_ip": "1.1.1.1",
                                      "tunnels": snap["tunnels"], "smart_devices": snap["smart_devices"],
                                      "disabled_devices": snap["disabled_devices"],
                                      "blackhole_devices": snap["blackhole_devices"]}}}
        rows = UI.tree_rows(status)
        self.assertEqual(rows[0]["level"], "dpn")
        self.assertEqual(rows[0]["parent"], "")
        # DPN row agregado: 4 devices (2 em GB, smart, disabled), online 3
        self.assertEqual(rows[0]["total"], 4)
        self.assertEqual(rows[0]["online"], 3)
        by_level = {}
        for r in rows:
            by_level.setdefault(r["level"], []).append(r)
        self.assertTrue(any(r["name"] == "GB" and r["parent"] == "DPN_01" for r in by_level["tunnel"]))
        gb_iid = "DPN_01:GB"
        gb_devs = [r for r in by_level["device"] if r["parent"] == gb_iid]
        self.assertEqual(len(gb_devs), 2)
        self.assertTrue(any(r["name"] == "SMART" for r in by_level["bucket"]))
        self.assertTrue(any(r["name"] == "DISABLED" for r in by_level["bucket"]))

    def test_10_backward_compatible_flat(self):
        status = {"dpns": {"DPN_01": {"state": "RUNNING", "node_id": "UB", "current_ip": "2.2.2.2"}}}
        rows = UI.tree_rows(status)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["level"], "dpn")
        self.assertEqual(rows[0]["parent"], "")


if __name__ == "__main__":
    unittest.main()
