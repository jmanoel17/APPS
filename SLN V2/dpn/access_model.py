#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/access_model.py — SLN V2: modelo PURO DPN → tunnels → devices (somente observabilidade).

Regra de associação (comprovada na E2.5A):
  device pertence ao tunnel sse routeMode == "full" AND device.regionCode == tunnel.tunnelCode.
  smart → smart_devices; disabled → disabled_devices; blackHole → blackhole_devices.
Não infere tunnel para smart/disabled/blackHole.

Não executa I/O, não tem state machine, não altera contagem operacional.
"""
from __future__ import annotations


def _norm_tunnels(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("list", "tunnels"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def _norm_ac(data):
    if isinstance(data, dict):
        return list(data.get("online") or []), list(data.get("offline") or [])
    if isinstance(data, list):
        return list(data), []
    return [], []


def _dev(rec, online):
    return {
        "mac": rec.get("mac"),
        "name": rec.get("name"),
        "lan_ip": rec.get("ip"),
        "route_mode": rec.get("routeMode"),
        "online": bool(online),
    }


def build_access_snapshot(dpn_id, tunnels, access_control, configured_tunnel=None):
    """Retorna {dpn_id, tunnels[], smart_devices[], disabled_devices[], blackhole_devices[],
    unmatched_devices[], totals{online,total}}. Puro/determinístico."""
    tun_list = _norm_tunnels(tunnels)
    online, offline = _norm_ac(access_control)
    devs = [(True, r) for r in online if isinstance(r, dict)] + \
           [(False, r) for r in offline if isinstance(r, dict)]

    order = []
    tentries = {}
    for t in tun_list:
        if not isinstance(t, dict):
            continue
        code = t.get("tunnelCode")
        if code in tentries:
            continue
        order.append(code)
        tentries[code] = {
            "tunnel_code": code,
            "active_ip": t.get("activeIp"),
            "active_num": t.get("activeNum"),
            "country_code": t.get("countryCode"),
            "region_code": t.get("regionCode"),
            # tunnel_ip = activeIp; NÃO é egress verificado (dpnMode pode estar disabled)
            "tunnel_ip": t.get("activeIp"),
            "egress_verified": False,
            "tunnel_work_units": 0,
            "device_work_units": 0,
            "devices": [],
        }

    smart, disabled, blackhole, unmatched = [], [], [], []
    for on, rec in devs:
        d = _dev(rec, on)
        rm = str(rec.get("routeMode") or "").lower()
        if rm == "full":
            code = rec.get("regionCode")
            if code in tentries:
                tentries[code]["devices"].append(d)
            else:
                unmatched.append(d)
        elif rm == "smart":
            smart.append(d)
        elif rm in ("blackhole", "black_hole"):
            blackhole.append(d)
        else:  # disabled ou desconhecido
            disabled.append(d)

    total = len(devs)
    total_online = sum(1 for on, _ in devs if on)
    return {
        "dpn_id": dpn_id,
        "tunnels": [tentries[c] for c in order if c in tentries],
        "smart_devices": smart,
        "disabled_devices": disabled,
        "blackhole_devices": blackhole,
        "unmatched_devices": unmatched,
        "totals": {"online": total_online, "total": total},
    }
