#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automatic registration of physical DPNs discovered through Agent presence.

The catalog is read-only (legacy dpn_groups.json or a supplied dict); the output
file contains only identity/routing metadata, never credentials.
"""
from __future__ import annotations

import copy
import os


def load_catalog(path):
    from . import state as S
    data = S.read_json(path, {}) or {}
    return data if isinstance(data, dict) else {}


def _catalog_by_expected(catalog):
    out = {}
    for key, spec in (catalog or {}).items():
        if str(key).startswith("_") or not isinstance(spec, dict):
            continue
        c = spec.get("controller") or {}
        pid = str(c.get("expected_dpn_id") or spec.get("expected_dpn_id") or "")
        if pid:
            out[pid] = (str(key), spec)
    return out


def _safe_suffix(physical_dpn_id):
    tail = str(physical_dpn_id or "").split("|")[-1]
    tail = "".join(ch for ch in tail if ch.isalnum())[-10:]
    return tail or "UNKNOWN"


def _serial(lan_ip):
    ip = str(lan_ip or "").strip()
    if not ip:
        return ""
    return ip if ":" in ip else ip + ":5555"


def build_groups(status, catalog):
    """Build agent_pool groups from current pool status.

    Known physical IDs inherit tunnel/address from the catalog; unknown IDs are
    registered as disabled so they are visible but never mutated blindly.
    """
    catalog_map = _catalog_by_expected(catalog)
    seen = {}
    meta = {"known": 0, "unknown": 0, "members": 0}
    for row in (status or {}).get("agents") or []:
        pid = str(row.get("physical_dpn_id") or "")
        if not pid:
            continue
        entry = seen.get(pid)
        if entry is None:
            known = catalog_map.get(pid)
            if known:
                key, spec = known
                ctl = copy.deepcopy(spec.get("controller") or {})
                gid = str(spec.get("group_id") or key)
                tunnel = str(ctl.get("tunnel_code") or spec.get("tunnel_code") or "")
                enabled = bool(tunnel)
                meta["known"] += 1
            else:
                gid = "DPN_" + _safe_suffix(pid)
                tunnel = ""
                ctl = {}
                enabled = False
                meta["unknown"] += 1
            ctl.pop("agent_id", None)
            ctl.pop("android_serial", None)
            ctl.update({
                "type": "deeper_connect", "transport": "agent_pool",
                "expected_dpn_id": pid, "tunnel_code": tunnel,
                "address": ctl.get("address") or "34.34.34.34",
                "port": ctl.get("port", 443), "username": ctl.get("username", "admin"),
                "tls_mode": ctl.get("tls_mode", "insecure"),
                "node_lb_mode": ctl.get("node_lb_mode", "none"),
                "request_timeout_s": ctl.get("request_timeout_s", 25),
                "connect_timeout_s": ctl.get("connect_timeout_s", 5),
            })
            entry = {"group_id": gid, "provider": "deeper_connect",
                     "controller_id": gid, "tunnel_code": tunnel,
                     "expected_dpn_id": pid, "members": [], "enabled": enabled,
                     "source": "auto", "controller": ctl}
            seen[pid] = entry
        if str(row.get("status") or "").upper() != "ONLINE":
            continue
        serial = _serial(row.get("lan_ip"))
        if serial and serial not in entry["members"]:
            entry["members"].append(serial)
            meta["members"] += 1
    groups = {}
    for entry in seen.values():
        gid = entry["group_id"]
        base = gid
        n = 2
        while gid in groups:
            gid = "%s_%d" % (base, n)
            n += 1
        entry["group_id"] = gid
        entry["controller_id"] = gid
        groups[gid] = entry
    return groups, meta


def write_groups(path, groups):
    from . import state as S
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    S.atomic_write_json(path, groups)
    return path
