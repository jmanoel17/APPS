#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/ui_model.py — SLN V2: lógica PURA da aba DPN (testável sem Tkinter).

A interface é CLIENTE: só lê `state/dpn/status.json` (snapshot atômico do runner) e publica
comandos em `state/dpn/commands/`. NUNCA edita estado interno nem consulta a DPN diretamente.
"""
import os
import time
import uuid

from . import state as S

ALLOWED_ACTIONS = ("pause", "resume", "rotate_now", "reset_node_now")

STATE_COLORS = {
    "RUNNING": "#2e7d32", "READY": "#2e7d32", "INIT": "#607d8b",
    "DISCOVERING_IP": "#607d8b", "VERIFYING_IP": "#f9a825",
    "IP_LIMIT_REACHED": "#ef6c00", "DRAINING": "#ef6c00", "ROTATION_PENDING": "#ef6c00",
    "ROTATING": "#1565c0", "IP_COOLDOWN": "#6a1b9a", "NODE_EXHAUSTED": "#c62828",
    "RESET_NODE_PENDING": "#c62828", "RESETTING_NODE": "#c62828",
    "WAITING_NEW_NODE": "#c62828", "PAUSED": "#616161", "ERROR": "#b71c1c",
}


def state_color(state):
    return STATE_COLORS.get(str(state), "#000000")


def read_status(path, default=None):
    """Leitura TOLERANTE (arquivo ausente/corrompido/troca atômica) -> default (nunca lança)."""
    try:
        d = S.read_json(path, None)
        return d if isinstance(d, dict) else default
    except Exception:
        return default


def coordinator_online(status, now=None, stale_s=10.0):
    if not isinstance(status, dict):
        return False
    ts = status.get("updated_at")
    if ts is None:
        return False
    now = time.time() if now is None else now
    try:
        return (float(now) - float(ts)) <= float(stale_s)
    except Exception:
        return False


def is_stale(status, now=None, stale_s=10.0):
    return not coordinator_online(status, now, stale_s)


def rows(status):
    """Lista de linhas para a tabela (ordem estável por dpn_id)."""
    if not isinstance(status, dict) or not isinstance(status.get("dpns"), dict):
        return []
    out = []
    for dpn_id, d in sorted(status["dpns"].items()):
        if not isinstance(d, dict):
            continue
        out.append({
            "dpn_id": dpn_id,
            "state": d.get("state"),
            "node": d.get("node_id"),
            "ip": d.get("current_ip"),
            "work_units": d.get("work_units"),
            "limit": d.get("limit"),
            "cooldown_s": d.get("cooldown_remaining_s", 0),
            "duplicates": d.get("duplicate_rotations"),
            "active": d.get("active_devices"),
            "safe": d.get("safe_devices"),
            "last_transition": d.get("last_transition_at"),
            "error": d.get("last_error"),
        })
    return out


def make_command(dpn_id, action, requested_by="ui", now=None):
    if action not in ALLOWED_ACTIONS:
        raise ValueError("acao invalida: %r" % (action,))
    now = time.time() if now is None else now
    return {"command_id": uuid.uuid4().hex, "dpn_id": dpn_id, "action": action,
            "created_at": now, "requested_by": requested_by}


def commands_dir(state_dir):
    return os.path.join(state_dir, "commands")


def write_command(state_dir, cmd):
    """Publica um comando de forma atômica em state/dpn/commands/<id>.json."""
    d = commands_dir(state_dir)
    os.makedirs(d, exist_ok=True)
    S.atomic_write_json(os.path.join(d, cmd["command_id"] + ".json"), cmd)
    return cmd["command_id"]


_BUCKETS = (("smart_devices", "SMART"), ("disabled_devices", "DISABLED"),
            ("blackhole_devices", "BLACKHOLE"))


def _dev_row(iid, parent, x, tunnel, mode):
    return {
        "iid": iid, "parent": parent, "level": "device",
        "name": x.get("name") or x.get("mac") or "?",
        "state": "online" if x.get("online") else "offline",
        "tunnel": tunnel, "tunnel_ip": "",
        "online": 1 if x.get("online") else 0, "total": 1,
        "route_mode": x.get("route_mode") or mode,
        "lan_ip": x.get("lan_ip") or "",
    }


def tree_rows(status):
    """Linhas hierárquicas para a Treeview: DPN → Tunnel → Device (+ SMART/DISABLED/BLACKHOLE).

    Cada item: {iid, parent, level(dpN|tunnel|bucket|device), name, state, tunnel, tunnel_ip,
    online, total, route_mode, lan_ip}. Retrocompatível: status sem 'tunnels' → só linhas DPN.
    """
    out = []
    if not isinstance(status, dict) or not isinstance(status.get("dpns"), dict):
        return out
    for dpn_id, d in sorted(status["dpns"].items()):
        if not isinstance(d, dict):
            continue
        tunnels = d.get("tunnels")
        total = online = 0
        if isinstance(tunnels, list):
            for t in tunnels:
                ds = t.get("devices") or []
                total += len(ds)
                online += sum(1 for x in ds if x.get("online"))
        for bkey, _ in _BUCKETS:
            for x in (d.get(bkey) or []):
                total += 1
                online += 1 if x.get("online") else 0
        out.append({
            "iid": dpn_id, "parent": "", "level": "dpn", "name": dpn_id,
            "state": d.get("state"), "tunnel": d.get("node_id"),
            "tunnel_ip": d.get("current_ip"), "online": online, "total": total,
            "route_mode": "", "lan_ip": "",
        })
        if not isinstance(tunnels, list):   # status antigo: DPN flat (backward compatible)
            continue
        for t in tunnels:
            if not isinstance(t, dict):
                continue
            code = t.get("tunnel_code")
            tiid = "%s:%s" % (dpn_id, code)
            ds = t.get("devices") or []
            t_on = sum(1 for x in ds if x.get("online"))
            out.append({
                "iid": tiid, "parent": dpn_id, "level": "tunnel", "name": code, "state": "",
                "tunnel": code, "tunnel_ip": t.get("tunnel_ip") or t.get("active_ip"),
                "online": t_on, "total": len(ds), "route_mode": "tunnel", "lan_ip": "",
            })
            for i, x in enumerate(ds):
                out.append(_dev_row("%s:%d" % (tiid, i), tiid, x, code, "full"))
        for bkey, bnode in _BUCKETS:
            ds = d.get(bkey) or []
            if not ds:
                continue
            biid = "%s:%s" % (dpn_id, bnode)
            b_on = sum(1 for x in ds if x.get("online"))
            out.append({
                "iid": biid, "parent": dpn_id, "level": "bucket", "name": bnode, "state": "",
                "tunnel": bnode, "tunnel_ip": "", "online": b_on, "total": len(ds),
                "route_mode": bnode.lower(), "lan_ip": "",
            })
            for i, x in enumerate(ds):
                out.append(_dev_row("%s:%d" % (biid, i), biid, x, bnode, bnode.lower()))
    return out
