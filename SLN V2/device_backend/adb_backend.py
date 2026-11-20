#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADB/uiautomator2 implementation. Preserves the existing bot behavior."""
from __future__ import annotations

import io
import os
import re
import subprocess
import time

from .base import DeviceBackend
from .models import (BackendError, BackendResult, DeviceStatus, ForegroundState,
                     ScreenshotFrame, tree_from_adb_xml)


class ADBBackend(DeviceBackend):
    name = "adb"

    def __init__(self, d, device="", cfg=None):
        if d is None:
            raise BackendError("ADB_DEVICE_NONE")
        self.d = d
        self.device = device
        self.cfg = cfg or {}

    def _size(self):
        try:
            w, h = self.d.window_size()
            return int(w), int(h)
        except Exception:
            return 1080, 2280

    def get_ui_tree(self, **kwargs):
        try:
            raw = self.d.dump_hierarchy()
        except Exception as e:
            raise BackendError("ADB_DUMP_FAILED:" + type(e).__name__)
        w, h = self._size()
        return tree_from_adb_xml(raw, w, h)

    def capture_screen(self, **kwargs):
        png = None
        try:
            img = self.d.screenshot()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png = buf.getvalue()
            w, h = img.size
        except Exception:
            try:
                png = subprocess.check_output(
                    ["adb", "-s", self.device, "exec-out", "screencap", "-p"],
                    timeout=10)
                from PIL import Image
                img = Image.open(io.BytesIO(png))
                w, h = img.size
            except Exception as e:
                raise BackendError("ADB_SCREENSHOT_FAILED:" + type(e).__name__)
        try:
            rot = int((self.d.info or {}).get("displayRotation", 0))
        except Exception:
            rot = 0
        return ScreenshotFrame(png, int(w), int(h), rot, "png")

    def get_foreground_app(self):
        try:
            cur = self.d.app_current() or {}
        except Exception as e:
            raise BackendError("ADB_FOREGROUND_FAILED:" + type(e).__name__)
        pkg = str(cur.get("package") or "")
        act = str(cur.get("activity") or "")
        return ForegroundState(pkg, act, pkg + "/" + act if pkg and act else "",
                               int(cur.get("window_id") or 0),
                               int(cur.get("observed_at_ms") or 0))

    def get_status(self):
        try:
            power = self.d.shell("dumpsys power").output or ""
            window = self.d.shell("dumpsys window").output or ""
            screen_on = not ("mWakefulness=Dozing" in power or "mWakefulness=Asleep" in power)
            keyguard = "isKeyguardShowing=true" in window or "mDreamingLockscreen=true" in window
            w, h = self._size()
            info = self.d.info or {}
            return DeviceStatus(
                screen_on=screen_on, keyguard_locked=keyguard,
                display={"width_px": w, "height_px": h,
                         "rotation": int(info.get("displayRotation", 0)),
                         "density_dpi": int(info.get("displayDensity", 0) or 0)},
                foreground_package=DeviceAccessibilityPackage.current(self.d),
                battery_pct=None, uptime_ms=info.get("uptime"), raw={"power": power[:2000]})
        except Exception as e:
            raise BackendError("ADB_STATUS_FAILED:" + type(e).__name__)

    def tap(self, x, y, duration_ms=50):
        try:
            self.d.click(float(x), float(y))
            return BackendResult("success", backend=self.name, data={"x": x, "y": y})
        except Exception as e:
            return BackendResult("failure", error=type(e).__name__, backend=self.name)

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        try:
            self.d.swipe(float(x1), float(y1), float(x2), float(y2),
                         duration=float(duration_ms) / 1000.0)
            return BackendResult("success", backend=self.name)
        except Exception as e:
            return BackendResult("failure", error=type(e).__name__, backend=self.name)

    def back(self):
        try:
            self.d.press("back")
            return BackendResult("success", backend=self.name)
        except Exception as e:
            return BackendResult("failure", error=type(e).__name__, backend=self.name)

    def home(self):
        try:
            self.d.press("home")
            return BackendResult("success", backend=self.name)
        except Exception as e:
            return BackendResult("failure", error=type(e).__name__, backend=self.name)

    def start_app(self, package):
        try:
            self.d.app_start(package)
            return BackendResult("success", backend=self.name, data={"package": package})
        except Exception as e:
            return BackendResult("failure", error=type(e).__name__, backend=self.name)

    def stop_app(self, package):
        try:
            self.d.app_stop(package)
            self.d.shell(f"am force-stop {package}")
            return BackendResult("success", backend=self.name, data={"package": package})
        except Exception as e:
            return BackendResult("failure", error=type(e).__name__, backend=self.name)

    def clear_app_data(self, package):
        started = time.time()
        try:
            self.d.app_stop(package)
            time.sleep(0.5)
            self.d.shell(f"am force-stop {package}")
            r = self.d.shell(f"pm clear {package}")
            out = str(getattr(r, "output", r) or "").strip()
            code = getattr(r, "exit_code", 0)
            ok = out.lower().startswith("success") and code == 0
            return BackendResult("success" if ok else "failure", backend=self.name,
                                 error="" if ok else "CLEAR_FAILED",
                                 data={"package": package, "output": out[:200],
                                       "duration_ms": int((time.time() - started) * 1000)})
        except Exception as e:
            return BackendResult("failure", error=type(e).__name__, backend=self.name)


class DeviceAccessibilityPackage:
    """Small compatibility helper so ADBBackend never imports the Agent package."""

    @staticmethod
    def current(_d):
        return ""
