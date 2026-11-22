#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.8B reveal-event telemetry (small JSON, no tree/screenshot payload)."""
from __future__ import annotations

import json
import os
import threading
import time

_LOCK = threading.RLock()
_EVENTS = []


def _path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "state", "device_agents", "e38b_reveal_events.json")


def reset():
    with _LOCK:
        _EVENTS[:] = []
    save()


def record(event):
    item = dict(event or {})
    item.setdefault("ts", time.time())
    with _LOCK:
        _EVENTS.append(item)
    save()
    return item


def save():
    p = _path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with _LOCK:
            payload = {"events": list(_EVENTS), "updated_at": time.time()}
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        pass


def snapshot():
    with _LOCK:
        return list(_EVENTS)
