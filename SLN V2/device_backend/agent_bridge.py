#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.8 in-process controller bridge for the real Device Agent.

The Agent is the primary backend. ADB is used only for bootstrap (reverse/accessibility/
launch) and for the explicitly measured legacy gaps in main_sln_v2. There is no automatic
ADB mutation fallback when an Agent command fails.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import os
import subprocess
import threading
import time

from .models import BackendError
from .e38_telemetry import get_telemetry, reset as reset_telemetry

AGENT_PACKAGE = "com.sln.deviceagent"
TARGET_PACKAGE = "com.flixdigital.dramaclassic"
REQUIRED_CAPS = (
    "GET_UI_TREE", "CAPTURE_SCREEN", "GET_FOREGROUND_APP", "TAP", "SWIPE", "BACK",
    "HOME", "START_APP", "STOP_APP", "CLEAR_APP_DATA",
)


class AgentControllerBridge:
    def __init__(self, device, cfg, log_fn=None, workspace=None):
        self.device = device
        self.cfg = cfg or {}
        self.log = log_fn or (lambda *a, **k: None)
        self.workspace = workspace or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.store_dir = os.path.join(self.workspace, "state", "device_agents", "e32b")
        self.cert = os.path.join(self.workspace, "state", "device_agents", "tls",
                                 "device-controller-cert.pem")
        self.key = os.path.join(self.workspace, "state", "device_agents", "tls",
                                "device-controller-key.pem")
        self.telemetry_path = os.path.join(self.workspace, "state", "device_agents",
                                           "e38_telemetry.json")
        self.telemetry = get_telemetry(self.telemetry_path, self.log)
        self.server = None
        self.agent_id = ""
        self.loop = None
        self.thread = None
        self.ready_evt = threading.Event()
        self.started = False
        self.preflight = {}
        self._stop_called = False

    # ------------------------------------------------------------ ADB bootstrap only
    def _adb(self, *args, timeout=30):
        try:
            p = subprocess.run(["adb", "-s", self.device, *args], capture_output=True,
                               text=True, timeout=timeout)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except Exception as e:
            return -1, "%s:%s" % (type(e).__name__, e)

    def _bootstrap_agent(self):
        self._adb("reverse", "tcp:8766", "tcp:8766")
        # Force-stop FIRST: Android clears enabled_accessibility_services for the package on
        # force-stop. Enabling it before that would silently preflight-fail on this ROM.
        self._adb("shell", "am", "force-stop", AGENT_PACKAGE)
        time.sleep(0.5)
        self._adb("shell", "settings", "put", "secure", "accessibility_enabled", "1")
        self._adb("shell", "settings", "put", "secure", "enabled_accessibility_services",
                  AGENT_PACKAGE + "/" + AGENT_PACKAGE + ".DeviceAccessibilityService")
        self._adb("shell", "am", "startservice", "-n", AGENT_PACKAGE + "/.AgentService",
                  "-e", "controller_url", "wss://127.0.0.1:8766")
        self._adb("shell", "am", "start", "-n", AGENT_PACKAGE + "/.MainActivity")
        self.log("[E38][BOOTSTRAP] reverse/accessibility/agent_start requested device=%s" %
                 self.device)

    # ------------------------------------------------------------ loop/thread
    def _run_loop(self):
        try:
            from device_agent.server import DeviceAgentServer
            from device_agent.state import DeviceAgentStore
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            store = DeviceAgentStore(self.store_dir)
            self.server = DeviceAgentServer(
                store,
                allowed_packages={TARGET_PACKAGE},
                allowed_privileged_packages={TARGET_PACKAGE},
            )
            self.loop.run_until_complete(
                self.server.start_wss("127.0.0.1", 8766, self.cert, self.key))
            self.ready_evt.set()
            self.loop.run_forever()
        except Exception as e:
            self.preflight["startup_error"] = "%s:%s" % (type(e).__name__, e)
            self.ready_evt.set()
            self.log("[E38][BRIDGE][ERROR] startup %s:%s" % (type(e).__name__, e))

    def _wait_connection(self, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if self.server is not None and self.server.connections:
                return next(iter(self.server.connections))
            time.sleep(0.25)
        return None

    def _call(self, op, params=None, timeout=25):
        if not self.agent_id:
            raise BackendError("AGENT_OFFLINE")
        fut = asyncio.run_coroutine_threadsafe(
            self.server.send_command(self.agent_id, op, params or {}, timeout=20),
            self.loop)
        return fut.result(timeout)

    def _caps(self):
        try:
            rec = self.server.store.get_agent(self.agent_id)
            return dict((rec.capabilities or {})) if rec is not None else {}
        except Exception:
            return {}

    # ------------------------------------------------------------ lifecycle
    def start(self):
        if self.started:
            return True
        self.log("[E38][PREFLIGHT] starting controller/agent device=%s" % self.device)
        self.thread = threading.Thread(target=self._run_loop, name="e38-agent-bridge",
                                       daemon=True)
        self.thread.start()
        if not self.ready_evt.wait(15):
            raise BackendError("CONTROLLER_START_TIMEOUT")
        if self.preflight.get("startup_error"):
            raise BackendError("CONTROLLER_START_FAILED:" + self.preflight["startup_error"])
        self._bootstrap_agent()
        self.agent_id = self._wait_connection(30) or ""
        if not self.agent_id:
            raise BackendError("AGENT_NOT_CONNECTED")
        self.preflight["agent_id"] = self.agent_id
        self.log("[E38][PREFLIGHT] agent connected id=%s" % self.agent_id)

        status = self._call("GET_STATUS")
        bridge = self._call("GET_PRIVILEGED_BRIDGE_STATUS")
        if str((bridge.get("data") or {}).get("state") or "") != "READY":
            self._call("REQUEST_SHIZUKU_PERMISSION")
            end = time.time() + 15
            while time.time() < end:
                time.sleep(0.5)
                bridge = self._call("GET_PRIVILEGED_BRIDGE_STATUS")
                if str((bridge.get("data") or {}).get("state") or "") == "READY":
                    break
        bridge_data = bridge.get("data") or {}
        caps = self._caps()
        # The APK sends a fresh HELLO after the Shizuku bridge becomes READY.
        end = time.time() + 10
        while time.time() < end and not all(caps.get(c, False) for c in REQUIRED_CAPS):
            time.sleep(0.5)
            caps = self._caps()

        missing = [c for c in REQUIRED_CAPS if not caps.get(c, False)]
        tree = self._call("GET_UI_TREE")
        shot = self._call("CAPTURE_SCREEN")
        fg = self._call("GET_FOREGROUND_APP")
        checks = {
            "agent_id": self.agent_id,
            "caps": caps,
            "missing_caps": missing,
            "bridge_state": bridge_data.get("state"),
            "effective_uid": bridge_data.get("effective_uid"),
            "accessibility_enabled": (status.get("data") or {}).get("accessibility_enabled"),
            "screen_on": (status.get("data") or {}).get("screen_on"),
            "keyguard_locked": (status.get("data") or {}).get("keyguard_locked"),
            "status": status.get("status"),
            "tree": tree.get("status"),
            "tree_nodes": len((tree.get("data") or {}).get("nodes") or []),
            "screenshot": shot.get("status"),
            "foreground": fg.get("status"),
            "foreground_data": fg.get("data") or {},
        }
        self.preflight.update(checks)
        ok = (not missing and bridge_data.get("state") == "READY"
              and bridge_data.get("effective_uid") == 2000
              and checks["status"] == "success" and checks["tree"] == "success"
              and checks["screenshot"] == "success" and checks["foreground"] == "success"
              and bool(checks["accessibility_enabled"])
              and bool(checks["screen_on"]) and not bool(checks["keyguard_locked"]))
        if not ok:
            self.log("[E38][PREFLIGHT][FAIL] " + json.dumps(checks, ensure_ascii=False))
            raise BackendError("E38_PREFLIGHT_FAILED")
        self.preflight["result"] = "PASS"
        self.log("[E38][PREFLIGHT][PASS] agent=%s bridge=%s uid=%s tree_nodes=%d caps=%d" % (
            self.agent_id, bridge_data.get("state"), bridge_data.get("effective_uid"),
            checks["tree_nodes"], len([c for c in REQUIRED_CAPS if caps.get(c)])))
        reset_telemetry(self.telemetry_path, "cycle", self.log)
        self.telemetry = get_telemetry(self.telemetry_path, self.log)
        self.started = True
        atexit.register(self.stop)
        return True

    def wait_connected(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.server is not None and self.agent_id in self.server.connections:
                return True
            time.sleep(0.25)
        return False

    def command_fn(self, op, params=None):
        if not self.started:
            raise BackendError("AGENT_BRIDGE_NOT_STARTED")
        if not (self.server is not None and self.agent_id in self.server.connections):
            raise BackendError("AGENT_OFFLINE")
        t0 = time.perf_counter()
        try:
            result = self._call(op, params or {}, timeout=25)
        except Exception as e:
            result = {"command_id": "", "status": "uncertain", "data": None,
                      "error": "CONTROLLER_%s" % type(e).__name__}
        ms = (time.perf_counter() - t0) * 1000
        self.telemetry.record_command(op, params or {}, result, ms)
        return result

    def stop(self):
        if self._stop_called:
            return
        self._stop_called = True
        try:
            self._adb("reverse", "--remove", "tcp:8766")
        except Exception:
            pass
        try:
            if self.loop is not None and self.server is not None:
                fut = asyncio.run_coroutine_threadsafe(self.server.stop_wss(), self.loop)
                fut.result(10)
        except Exception:
            pass
        try:
            if self.loop is not None:
                self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass
        try:
            self.telemetry.save()
        except Exception:
            pass
        self.started = False
