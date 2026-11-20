#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""device_agent/mock_agent.py — agente mock (loopback) para testes/benchmark, sem APK/ADB."""
from __future__ import annotations

import asyncio
import json

from . import protocol as P


class MockSession:
    def __init__(self):
        self.agent_id = None
        self.inbox = asyncio.Queue()
        self.closed = False

    async def send(self, text):
        if not self.closed:
            await self.inbox.put(text)

    def close(self):
        self.closed = True

    async def recv(self, timeout=2.0):
        return await asyncio.wait_for(self.inbox.get(), timeout)


def _default_caps():
    return {op: True for op in P.OPS}


def _default_data(agent_id, op, params, info):
    if op == P.PING:
        return {"pong": True}
    if op == P.GET_STATUS:
        return {"uptime_ms": 1000, "battery_pct": 90, "charging": True, "screen_on": True,
                "foreground_package": "com.flixdigital.dramaclassic",
                "lan_ip": info.get("lan_ip", ""), "agent_version": "mock-1",
                "accessibility_enabled": True, "last_command_status": "success",
                "device_time_ms": 1000000000000, "elapsed_realtime_ms": 12345,
                "keyguard_locked": False,
                "display": info.get("display", {})}
    if op == P.GET_DEVICE_INFO:
        return {"model": info.get("model", "mock"), "manufacturer": info.get("manufacturer", "mock"),
                "sdk": info.get("sdk", 33), "android_id": info.get("android_id", "")}
    if op == P.GET_NETWORK_INFO:
        return {"lan_ip": info.get("lan_ip", ""), "mac": info.get("mac", "")}
    if op == P.GET_FOREGROUND_APP:
        return {"package": "com.flixdigital.dramaclassic",
                "activity": "com.flixdigital.dramaclassic.MainActivity",
                "component": "com.flixdigital.dramaclassic/com.flixdigital.dramaclassic.MainActivity",
                "window_id": 42, "observed_at_ms": 1000000000000}
    if op == P.GET_ACCESSIBILITY_STATE:
        return {"enabled": True}
    if op == P.GET_PRIVILEGED_BRIDGE_STATUS:
        return {"backend": "shizuku", "available": True, "authorized": True,
                "binder_alive": True, "user_service_ready": True,
                "effective_uid": 2000, "state": "READY", "api_version": 13,
                "shizuku_version": "mock", "process_name": "mock:privileged",
                "permission_requested": False, "error": "", "changed_at_ms": 1000000000000}
    if op == P.REQUEST_SHIZUKU_PERMISSION:
        return {"backend": "shizuku", "available": True, "authorized": True,
                "binder_alive": True, "user_service_ready": True,
                "effective_uid": 2000, "state": "READY", "permission_requested": False}
    if op == P.STOP_APP:
        return {"package": params.get("package"), "stopped": True,
                "effective_uid": 2000, "duration_ms": 12, "verified_stopped": True}
    if op == P.CLEAR_APP_DATA:
        return {"package": params.get("package"), "cleared": True,
                "effective_uid": 2000, "duration_ms": 20, "verified_stopped": True}
    if op == P.GET_UI_TREE:
        return {"snapshot_id": "mock-snap", "captured_at_ms": 1000000000000,
                "package": "com.flixdigital.dramaclassic",
                "activity": "com.flixdigital.dramaclassic.MainActivity",
                "window_id": 42, "display": info.get("display", {}),
                "nodes": [{"node_id": "n0", "parent_id": None, "depth": 0,
                           "class": "android.widget.FrameLayout", "text": "",
                           "content_desc": "", "resource_id": "",
                           "bounds": [0, 0, 1080, 2280], "clickable": False,
                           "enabled": True, "visible": True, "scrollable": False,
                           "password": False, "child_count": 0}],
                "truncated": False, "capture_ms": 5, "payload_bytes": 100}
    if op == P.CAPTURE_SCREEN:
        return {"captured_at_ms": 1000000000000, "width": 1080, "height": 2280,
                "rotation": 0, "format": "png", "size_bytes": 8,
                "sha256": "00" * 32, "payload": "iVBORw0KGgo=",
                "capture_ms": 3, "encoding_ms": 2}
    if op == P.TAP:
        return {"x": params.get("x"), "y": params.get("y"),
                "duration_ms": params.get("duration_ms"),
                "queue_ms": 1, "gesture_ms": 40}
    if op == P.SWIPE:
        return {"x1": params.get("x1"), "y1": params.get("y1"),
                "x2": params.get("x2"), "y2": params.get("y2"),
                "duration_ms": params.get("duration_ms"),
                "queue_ms": 1, "gesture_ms": 40}
    return {"ok": True}


class MockAgent:
    def __init__(self, server, agent_id, capabilities=None, behaviors=None, token=None,
                 pairing_code=None, info=None):
        self.server = server
        self.agent_id = agent_id
        self.session = MockSession()
        self.capabilities = capabilities if capabilities is not None else _default_caps()
        self.behaviors = behaviors or {}
        self.token = token
        self.pairing_code = pairing_code
        self.info = info or {"android_id": "aid-" + agent_id,
                             "build_fingerprint": "mock/" + agent_id,
                             "manufacturer": "mock", "model": "mock", "sdk": 33,
                             "lan_ip": "", "mac": "",
                             "display": {"width_px": 1080, "height_px": 1920,
                                         "rotation": 0, "density_dpi": 420}}
        self.handled = {}
        self.executions = 0
        self.executed_ops = []
        self._task = None
        self._stop = asyncio.Event()

    async def _send(self, obj):
        await self.server.handle_message(self.session, obj)

    async def connect(self):
        if self.token:
            await self._send({"protocol": P.PROTOCOL_VERSION, "type": P.HELLO,
                              "agent_id": self.agent_id, "token": self.token,
                              "capabilities": self.capabilities,
                              "agent_version": "mock-1", "device_id": self.agent_id,
                              "info": self.info})
        else:
            await self._send({"protocol": P.PROTOCOL_VERSION, "type": P.PAIR_REQUEST,
                              "agent_id": self.agent_id, "pairing_code": self.pairing_code,
                              "info": self.info})
        resp = json.loads(await self.session.recv(timeout=5.0))
        if resp.get("type") == P.PAIR_OK:
            self.token = resp.get("token")
        if resp.get("type") in (P.ERROR, P.PAIR_DENIED):
            return resp
        self._task = asyncio.create_task(self._loop())
        return resp

    async def _loop(self):
        while not self.session.closed and not self._stop.is_set():
            try:
                text = await self.session.recv(timeout=30.0)
            except Exception:
                return
            try:
                msg = json.loads(text)
            except Exception:
                continue
            if msg.get("type") == P.COMMAND:
                await self._handle_command(msg)

    async def _handle_command(self, m):
        cid = str(m.get("command_id") or "")
        op = m.get("op")
        if cid in self.handled:
            data, error = self.handled[cid]
            await self._send_result(cid, op, "success", data, error, m.get("device_id"))
            return
        self.executions += 1
        self.executed_ops.append(op)
        params = m.get("params") or {}
        beh = self.behaviors.get(op)
        status, data, error, delay = "success", None, "", 0.0
        if beh is not None:
            r = beh(self.agent_id, op, params) if callable(beh) else beh
            if asyncio.iscoroutine(r):
                r = await r
            status = r.get("status", "success")
            data = r.get("data")
            error = r.get("error", "")
            delay = float(r.get("delay", 0) or 0)
        else:
            data = _default_data(self.agent_id, op, params, self.info)
        if delay:
            await asyncio.sleep(delay)
        if status == "success":
            self.handled[cid] = (data, error)
        await self._send_result(cid, op, status, data, error, m.get("device_id"))

    async def _send_result(self, cid, op, status, data, error, device_id):
        await self._send({"protocol": P.PROTOCOL_VERSION, "type": P.RESULT,
                          "command_id": cid, "agent_id": self.agent_id,
                          "device_id": device_id or self.agent_id, "op": op,
                          "status": status, "data": data, "error": error or "",
                          "started_at_ms": P.now_ms(), "finished_at_ms": P.now_ms()})

    async def heartbeat(self, info=None):
        await self._send({"protocol": P.PROTOCOL_VERSION, "type": P.HEARTBEAT,
                          "agent_id": self.agent_id, "info": info or self.info})

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._task = None
        self.session.close()
