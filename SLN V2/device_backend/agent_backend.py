#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Device Agent implementation. Never falls back to ADB and never executes in shadow."""
from __future__ import annotations

import base64
import hashlib

from .base import DeviceBackend
from .models import (BackendError, BackendResult, DeviceStatus, ForegroundState,
                     ShadowMutationBlocked, ScreenshotFrame, tree_from_agent_data)


class AgentBackend(DeviceBackend):
    name = "agent"

    def __init__(self, command_fn, agent_id="", execution_mode="shadow", timeout=20.0):
        if not command_fn:
            raise BackendError("AGENT_CONTROLLER_NOT_CONFIGURED")
        self.command_fn = command_fn
        self.agent_id = agent_id
        self.execution_mode = str(execution_mode or "shadow").lower()
        self.timeout = float(timeout)

    def _call(self, op, params=None):
        result = self.command_fn(op, params or {})
        if hasattr(result, "__await__"):
            raise BackendError("ASYNC_COMMAND_FN_NOT_SUPPORTED")
        if not isinstance(result, dict):
            raise BackendError("AGENT_BAD_RESULT")
        return result

    def _read(self, op, params=None):
        r = self._call(op, params)
        if str(r.get("status") or "").lower() != "success":
            raise BackendError("AGENT_" + str(r.get("error") or "READ_FAILED"))
        return r.get("data") or {}

    def get_ui_tree(self, **kwargs):
        d = self._read("GET_UI_TREE", kwargs)
        display = d.get("display") or {}
        return tree_from_agent_data(d, int(display.get("width_px") or 1080),
                                    int(display.get("height_px") or 2280))

    def capture_screen(self, **kwargs):
        d = self._read("CAPTURE_SCREEN", kwargs)
        payload = d.get("payload") or ""
        png = base64.b64decode(payload) if payload else b""
        return ScreenshotFrame(png, int(d.get("width") or 0), int(d.get("height") or 0),
                               int(d.get("rotation") or 0), str(d.get("format") or "png"),
                               str(d.get("sha256") or hashlib.sha256(png).hexdigest()))

    def get_foreground_app(self):
        d = self._read("GET_FOREGROUND_APP")
        return ForegroundState(str(d.get("package") or ""), str(d.get("activity") or ""),
                               str(d.get("component") or ""), int(d.get("window_id") or 0),
                               int(d.get("observed_at_ms") or 0))

    def get_status(self):
        d = self._read("GET_STATUS")
        return DeviceStatus(bool(d.get("screen_on", True)), bool(d.get("keyguard_locked", False)),
                            d.get("display") or {}, str(d.get("foreground_package") or ""),
                            str(d.get("foreground_activity") or ""), d.get("battery_pct"),
                            d.get("uptime_ms"), d)

    def _mutate(self, op, params):
        if self.execution_mode == "shadow":
            raise ShadowMutationBlocked("SHADOW_MUTATION_BLOCKED:" + op)
        r = self._call(op, params)
        status = str(r.get("status") or "failure").lower()
        return BackendResult(status, str(r.get("command_id") or ""),
                             str(r.get("error") or ""), r.get("data"), self.name)

    def tap(self, x, y, duration_ms=50):
        return self._mutate("TAP", {"x": x, "y": y, "duration_ms": duration_ms})

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        return self._mutate("SWIPE", {"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                      "duration_ms": duration_ms})

    def back(self):
        return self._mutate("BACK", {})

    def home(self):
        return self._mutate("HOME", {})

    def start_app(self, package):
        return self._mutate("START_APP", {"package": package})

    def stop_app(self, package):
        return self._mutate("STOP_APP", {"package": package})

    def clear_app_data(self, package):
        return self._mutate("CLEAR_APP_DATA", {"package": package})
