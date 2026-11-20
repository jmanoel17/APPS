#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""device_agent/ui_model.py — correlação Device Agent ↔ DPN/Access e linhas read-only de UI."""
from __future__ import annotations


def _iter_devices(dpn_snapshot):
    dpns = (dpn_snapshot or {}).get("dpns") or {}
    for dpn_id, d in dpns.items():
        for t in (d.get("tunnels") or []):
            for dev in (t.get("devices") or []):
                yield dpn_id, dev.get("route_mode") or "full", t.get("tunnel_code"), \
                    t.get("tunnel_ip"), dev
        for bkey, mode in (("smart_devices", "smart"), ("disabled_devices", "disabled"),
                           ("blackhole_devices", "blackHole")):
            for dev in (d.get(bkey) or []):
                yield dpn_id, dev.get("route_mode") or mode, None, None, dev


def correlate_device_agent(agent_info, dpn_snapshot):
    """Associa agent→DPN/tunnel por LAN IP, depois MAC (se confiável), depois mapping explícito.
    NUNCA escolhe sozinho quando há ambiguidade. Não infere tunnel no APK."""
    agent_id = (agent_info or {}).get("agent_id")
    lan = (agent_info or {}).get("lan_ip") or ""
    mac = (agent_info or {}).get("mac") or ""
    explicit = (agent_info or {}).get("dpn_id")
    trust_mac = bool((agent_info or {}).get("trust_mac"))
    cands = []
    for dpn_id, mode, tc, tip, dev in _iter_devices(dpn_snapshot):
        src = None
        if explicit and explicit == dpn_id:
            src = "explicit"
        elif lan and (dev.get("lan_ip") or "") == lan:
            src = "lan_ip"
        elif trust_mac and mac and (dev.get("mac") or "").lower() == mac.lower():
            src = "mac"
        if src:
            cands.append({"agent_id": agent_id, "dpn_id": dpn_id, "route_mode": mode,
                          "tunnel_code": tc, "tunnel_ip": tip, "mapping_source": src,
                          "mapping_confidence": {"explicit": 1.0, "lan_ip": 0.9, "mac": 0.6}[src],
                          "device": {"mac": dev.get("mac"), "lan_ip": dev.get("lan_ip"),
                                     "name": dev.get("name"), "online": dev.get("online")}})
    if not cands:
        return {"agent_id": agent_id, "dpn_id": None, "route_mode": None, "tunnel_code": None,
                "tunnel_ip": None, "mapping_source": "none", "mapping_confidence": 0.0,
                "ambiguous": False, "candidates": []}
    uniq = {}
    for c in cands:
        uniq[(c["dpn_id"], c["tunnel_code"])] = c
    uniq = list(uniq.values())
    if len(uniq) == 1:
        out = dict(uniq[0])
        out["ambiguous"] = False
        return out
    return {"agent_id": agent_id, "dpn_id": None, "route_mode": None, "tunnel_code": None,
            "tunnel_ip": None, "mapping_source": "ambiguous", "mapping_confidence": 0.0,
            "ambiguous": True, "candidates": uniq}


def build_rows(snapshot, correlations=None):
    """Linhas read-only para futura aba: Device, Status, Agent version, SDK, Foreground,
    DPN, Tunnel, Tunnel IP, Last seen, LAN IP."""
    correlations = correlations or {}
    rows = []
    for a in (snapshot or {}).get("agents") or []:
        aid = a.get("agent_id")
        c = correlations.get(aid) or {}
        rows.append({
            "device": aid,
            "status": a.get("status"),
            "agent_version": a.get("agent_version"),
            "sdk": a.get("sdk"),
            "foreground": a.get("foreground_package"),
            "dpn": c.get("dpn_id"),
            "tunnel": c.get("tunnel_code"),
            "tunnel_ip": c.get("tunnel_ip"),
            "last_seen": a.get("last_seen"),
            "lan_ip": a.get("lan_ip"),
            "ambiguous": bool(c.get("ambiguous")),
        })
    return rows
