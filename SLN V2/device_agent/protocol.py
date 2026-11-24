#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""device_agent/protocol.py - protocolo sln-device/1 (Controller -> Device Agent).

Mensagens JSON (WSS ou loopback): hello/hello_ok, pair_request/pair_ok/pair_denied,
command/result, heartbeat, error. command_id UUID obrigatório; deadline absoluto (ms).

OPS (E3.1/E3.3A): PING, GET_STATUS, GET_DEVICE_INFO, GET_NETWORK_INFO, GET_FOREGROUND_APP,
GET_ACCESSIBILITY_STATE (leituras) + START_APP, BACK, HOME, TAP, SWIPE (mutações serializadas).
Sem shell/SCREENSHOT/STOP_APP nesta fase.
"""
from __future__ import annotations

import ipaddress
import re
import time
import uuid

PROTOCOL_VERSION = "sln-device/1"

HELLO = "hello"
HELLO_OK = "hello_ok"
PAIR_REQUEST = "pair_request"
PAIR_OK = "pair_ok"
PAIR_DENIED = "pair_denied"
COMMAND = "command"
RESULT = "result"
HEARTBEAT = "heartbeat"
ERROR = "error"

PING = "PING"
GET_STATUS = "GET_STATUS"
GET_DEVICE_INFO = "GET_DEVICE_INFO"
GET_NETWORK_INFO = "GET_NETWORK_INFO"
GET_FOREGROUND_APP = "GET_FOREGROUND_APP"
GET_ACCESSIBILITY_STATE = "GET_ACCESSIBILITY_STATE"
START_APP = "START_APP"
BACK = "BACK"
HOME = "HOME"
TAP = "TAP"
SWIPE = "SWIPE"
GET_UI_TREE = "GET_UI_TREE"
CAPTURE_SCREEN = "CAPTURE_SCREEN"
GET_PRIVILEGED_BRIDGE_STATUS = "GET_PRIVILEGED_BRIDGE_STATUS"
REQUEST_SHIZUKU_PERMISSION = "REQUEST_SHIZUKU_PERMISSION"
STOP_APP = "STOP_APP"
CLEAR_APP_DATA = "CLEAR_APP_DATA"

GET_DPN_IDENTITY = "GET_DPN_IDENTITY"
GET_DPN_STATUS = "GET_DPN_STATUS"
LIST_DPN_TUNNELS = "LIST_DPN_TUNNELS"
GET_DPN_MODE = "GET_DPN_MODE"
GET_DPN_NODE_LB_MODE = "GET_DPN_NODE_LB_MODE"
LIST_DPN_ACCESS_CONTROL = "LIST_DPN_ACCESS_CONTROL"
DPN_SWITCH_NODE = "DPN_SWITCH_NODE"
DPN_REFRESH_ROUTE_SET = "DPN_REFRESH_ROUTE_SET"
DPN_READ_OPS = (GET_DPN_IDENTITY, GET_DPN_STATUS, LIST_DPN_TUNNELS,
                GET_DPN_MODE, GET_DPN_NODE_LB_MODE, LIST_DPN_ACCESS_CONTROL)
DPN_MUTATING_OPS = (DPN_SWITCH_NODE, DPN_REFRESH_ROUTE_SET)

OPS = (PING, GET_STATUS, GET_DEVICE_INFO, GET_NETWORK_INFO, GET_FOREGROUND_APP,
       GET_ACCESSIBILITY_STATE, START_APP, BACK, HOME, TAP, SWIPE,
       GET_UI_TREE, CAPTURE_SCREEN, GET_PRIVILEGED_BRIDGE_STATUS,
       REQUEST_SHIZUKU_PERMISSION, STOP_APP, CLEAR_APP_DATA, *DPN_READ_OPS,
       *DPN_MUTATING_OPS)
MUTATING_OPS = frozenset((START_APP, BACK, HOME, TAP, SWIPE, STOP_APP, CLEAR_APP_DATA) +
                         DPN_MUTATING_OPS)

TAP_MIN_MS, TAP_MAX_MS = 10, 1000
SWIPE_MIN_MS, SWIPE_MAX_MS = 10, 3000

# server-side command states
QUEUED = "QUEUED"
SENT = "SENT"
ACKED = "ACKED"
SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
EXPIRED = "EXPIRED"
UNCERTAIN = "UNCERTAIN"
CANCELLED = "CANCELLED"
TERMINAL = frozenset((SUCCESS, FAILURE, EXPIRED, UNCERTAIN, CANCELLED))

DEFAULT_TTL_MS = 20_000


def new_command_id():
    return str(uuid.uuid4())


def is_command_id(value):
    try:
        uuid.UUID(str(value))
        return True
    except Exception:
        return False


def now_ms():
    return int(time.time() * 1000)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _display_size(display):
    if not isinstance(display, dict):
        return 0, 0
    try:
        return int(display.get("width_px") or 0), int(display.get("height_px") or 0)
    except Exception:
        return 0, 0


def _validate_point(display, x, y):
    w, h = _display_size(display)
    if w <= 0 or h <= 0:
        return "display indisponível"
    if not (0 <= x < w) or not (0 <= y < h):
        return "coordenada fora dos bounds do display"
    return None


def _opt_int(params, key, lo, hi):
    if key not in params:
        return None
    v = params.get(key)
    if not isinstance(v, int) or isinstance(v, bool):
        return f"{key} deve ser inteiro"
    if not (lo <= v <= hi):
        return f"{key} fora da faixa"
    return None


def valid_package(pkg):
    if not isinstance(pkg, str):
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$", pkg))


def _valid_dpn_tunnel(value):
    return isinstance(value, str) and bool(re.match(r"^[A-Za-z0-9_-]{1,16}$", value))


def _valid_dpn_ip(value):
    try:
        ipaddress.ip_address(str(value))
        return True
    except Exception:
        return False


def validate_params(op, params, display=None):
    if not isinstance(params, dict):
        return "params inválido"

    if op in DPN_MUTATING_OPS:
        expected = params.get("expected_dpn_id")
        if not isinstance(expected, str) or not expected.strip() or len(expected) > 200:
            return "expected_dpn_id obrigatorio"
        if not _valid_dpn_tunnel(params.get("tunnel_code")):
            return "tunnel_code invalido"
        if op == DPN_SWITCH_NODE and not _valid_dpn_ip(params.get("current_ip")):
            return "current_ip invalido"

    if op == GET_UI_TREE:
        for key, lo, hi in (("max_depth", 1, 64), ("max_nodes", 1, 3000),
                            ("max_text_length", 0, 500),
                            ("max_payload_bytes", 1024, 2_000_000)):
            r = _opt_int(params, key, lo, hi)
            if r:
                return r
    if op == CAPTURE_SCREEN:
        r = _opt_int(params, "max_dim", 0, 4096)
        if r:
            return r
        r = _opt_int(params, "max_payload_bytes", 1024, 3_000_000)
        if r:
            return r
        if params.get("format", "png") != "png":
            return "format deve ser png"
    if op in (STOP_APP, CLEAR_APP_DATA):
        if not valid_package(params.get("package")):
            return f"{op} exige package v?lido"
    if op == START_APP:
        pkg = params.get("package")
        if not isinstance(pkg, str) or not pkg.strip():
            return "START_APP exige package"
    if op == TAP:
        x, y, dur = params.get("x"), params.get("y"), params.get("duration_ms")
        if not _is_num(x) or not _is_num(y) or not _is_num(dur):
            return "TAP exige x/y/duration_ms numéricos"
        if not (TAP_MIN_MS <= float(dur) <= TAP_MAX_MS):
            return "duration_ms fora da faixa segura"
        reason = _validate_point(display, float(x), float(y))
        if reason:
            return reason
    if op == SWIPE:
        x1, y1 = params.get("x1"), params.get("y1")
        x2, y2 = params.get("x2"), params.get("y2")
        dur = params.get("duration_ms")
        if not all(_is_num(v) for v in (x1, y1, x2, y2, dur)):
            return "SWIPE exige x1/y1/x2/y2/duration_ms numéricos"
        if not (SWIPE_MIN_MS <= float(dur) <= SWIPE_MAX_MS):
            return "duration_ms fora da faixa segura"
        r1 = _validate_point(display, float(x1), float(y1))
        if r1:
            return "origem: " + r1
        r2 = _validate_point(display, float(x2), float(y2))
        if r2:
            return "destino: " + r2
    return None


def validate_command(msg: dict, agent_id: str, now=None, display=None):
    """Valida um COMMAND (do controller). Retorna motivo de rejeição ou None."""
    if not isinstance(msg, dict):
        return "mensagem inválida"
    if msg.get("protocol") != PROTOCOL_VERSION:
        return "protocolo incompatível"
    if msg.get("type") != COMMAND:
        return "tipo inválido"
    if str(msg.get("agent_id") or "") != agent_id:
        return "comando destinado a outro agente"
    cid = str(msg.get("command_id") or "")
    if not cid:
        return "command_id ausente"
    if not is_command_id(cid):
        return "command_id inv?lido (UUID)"
    op = msg.get("op")
    if op not in OPS:
        return "operação desconhecida"
    if not isinstance(msg.get("params"), dict):
        return "params inválido"
    dl = msg.get("deadline_ms")
    if not isinstance(dl, int):
        return "deadline inválido"
    if (now or now_ms()) > int(dl):
        return "comando expirado"
    return validate_params(op, msg.get("params"), display=display)


def make_command(agent_id, op, params=None, deadline_ms=None, command_id=None, device_id=None):
    return {
        "protocol": PROTOCOL_VERSION,
        "type": COMMAND,
        "command_id": command_id or new_command_id(),
        "agent_id": agent_id,
        "device_id": device_id or agent_id,
        "op": op,
        "params": params or {},
        "deadline_ms": int(deadline_ms if deadline_ms is not None else now_ms() + DEFAULT_TTL_MS),
    }
