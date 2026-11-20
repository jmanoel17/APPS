#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uiautomator2-compatible facade for Agent-only execution.

Only the methods used by main_sln_v2 are implemented. Reads come from AgentBackend.
Alegacy selector syntax is evaluated on the Agent tree; no UiAutomation/u2 connection is
opened while the Agent is the backend.
"""
from __future__ import annotations

import io
import re
import subprocess
import time
import types

from .e38_telemetry import get_telemetry


class _ExistsProxy:
    def __init__(self, selector):
        self.selector = selector

    def __call__(self, timeout=0):
        return self.selector._exists(timeout=float(timeout or 0))

    def __bool__(self):
        return self.selector._exists(0)


class NodeRef:
    def __init__(self, facade, node):
        self.facade = facade
        self.node = node

    @property
    def info(self):
        b = self.node.bounds or (0, 0, 0, 0)
        return {"bounds": {"left": b[0], "top": b[1], "right": b[2], "bottom": b[3]},
                "clickable": bool(self.node.clickable), "enabled": bool(self.node.enabled),
                "focusable": False, "text": self.node.text, "contentDescription":
                self.node.content_desc, "resourceId": self.node.resource_id}

    def click(self):
        b = self.node.bounds
        if not b:
            return False
        return bool(self.facade.backend.tap((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, 50).ok)

    def get_text(self):
        return self.node.text


class Selector:
    def __init__(self, facade, query=None, fixed=None):
        self.facade = facade
        self.query = query or {}
        self.fixed = fixed

    def _nodes(self):
        if self.fixed is not None:
            return [self.fixed]
        return self.facade._select(self.query)

    def _exists(self, timeout=0):
        end = time.time() + max(0.0, timeout)
        while True:
            nodes = self._nodes()
            if nodes:
                return True
            if time.time() >= end:
                return False
            time.sleep(min(0.2, max(0.0, end - time.time())))

    @property
    def exists(self):
        return _ExistsProxy(self)

    @property
    def info(self):
        nodes = self._nodes()
        return NodeRef(self.facade, nodes[0]).info if nodes else {}

    def click(self):
        nodes = self._nodes()
        if not nodes:
            return False
        return NodeRef(self.facade, nodes[0]).click()

    def get_text(self):
        nodes = self._nodes()
        return nodes[0].text if nodes else ""


class XPathSelector:
    def __init__(self, facade, xpath):
        self.facade = facade
        self.xpath = xpath
        self._matched = None

    def _nodes(self):
        if self._matched is None:
            self._matched = self.facade._xpath(self.xpath)
        return self._matched

    @property
    def exists(self):
        return bool(self._nodes())

    def all(self):
        return [NodeRef(self.facade, n) for n in self._nodes()]

    def click(self):
        nodes = self._nodes()
        return NodeRef(self.facade, nodes[0]).click() if nodes else False


class AgentDeviceFacade:
    def __init__(self, device, bridge=None, backend=None, log_fn=None):
        self.device = device
        self.bridge = bridge
        self.backend = backend
        self.log = log_fn or (lambda *a, **k: None)
        self.telemetry = get_telemetry(bridge.telemetry_path if bridge else None, self.log)
        self._size_cache = (0.0, (1080, 2280))

    # ------------------------------------------------------------ Backend-backed reads
    def _tree(self):
        return self.backend.get_ui_tree()

    def _select(self, query):
        try:
            nodes = self._tree().nodes
        except Exception as e:
            self.log("[E38][AGENT][ERROR] selector_read %s:%s" % (type(e).__name__, e))
            return []
        out = []
        for n in nodes:
            ok = True
            for k, v in query.items():
                if k == "resourceId":
                    ok = n.resource_id == str(v)
                elif k == "text":
                    ok = n.text == str(v)
                elif k in ("description", "content-desc"):
                    ok = n.content_desc == str(v)
                elif k == "className":
                    ok = n.cls == str(v)
                elif k == "textContains":
                    ok = str(v) in n.text
                elif k == "enabled":
                    ok = n.enabled == bool(v)
                elif k == "clickable":
                    ok = n.clickable == bool(v)
                if not ok:
                    break
            if ok:
                out.append(n)
        return out

    def _xpath(self, xpath):
        try:
            nodes = self._tree().nodes
        except Exception as e:
            self.log("[E38][AGENT][ERROR] xpath_read %s:%s" % (type(e).__name__, e))
            return []
        m = re.match(r"^//(?P<tag>[^\[]+)?(?:\[@(?P<attr>[^=]+)=\"(?P<val>.*)\"\])?$",
                     str(xpath or "").strip())
        if not m:
            return []
        tag = (m.group("tag") or "*").strip()
        attr, val = m.group("attr"), m.group("val")
        out = []
        for n in nodes:
            if tag != "*" and n.cls != tag:
                continue
            if attr == "resource-id" and n.resource_id != val:
                continue
            if attr == "text" and n.text != val:
                continue
            if attr == "content-desc" and n.content_desc != val:
                continue
            if attr and attr not in ("resource-id", "text", "content-desc"):
                continue
            out.append(n)
        return out

    @property
    def info(self):
        try:
            st = self.backend.get_status()
            d = st.display or {}
            return {"displayWidth": int(d.get("width_px") or 1080),
                    "displayHeight": int(d.get("height_px") or 2280),
                    "displayRotation": int(d.get("rotation") or 0),
                    "displayDensity": int(d.get("density_dpi") or 0)}
        except Exception:
            return {}

    def window_size(self):
        now = time.time()
        if now - self._size_cache[0] < 1.0:
            return self._size_cache[1]
        try:
            d = self.backend.get_status().display or {}
            size = (int(d.get("width_px") or 1080), int(d.get("height_px") or 2280))
            self._size_cache = (now, size)
            return size
        except Exception:
            return self._size_cache[1]

    def app_current(self):
        try:
            return self.backend.get_foreground_app().as_dict()
        except Exception:
            return {}

    def dump_hierarchy(self):
        return self.backend.get_ui_tree().raw

    def screenshot(self):
        from PIL import Image
        frame = self.backend.capture_screen()
        img = Image.open(io.BytesIO(frame.png))
        return img

    # ------------------------------------------------------------ explicit ADB gaps
    def _legacy(self, operation, reason, mutation=False, adb=True):
        self.telemetry.record_legacy(operation, reason, mutation=mutation, adb=adb)

    def _shell(self, cmd, timeout=30):
        low = str(cmd).lower()
        mutation = any(k in low for k in ("pm clear", "force-stop", "am start", "input tap",
                                          "input swipe", "input keyevent"))
        self._legacy("shell:%s" % str(cmd)[:120], "legacy_shell_gap", mutation=mutation, adb=True)
        try:
            p = subprocess.run(["adb", "-s", self.device, "shell", str(cmd)],
                               capture_output=True, text=True, timeout=timeout)
            return types.SimpleNamespace(output=p.stdout or "", exit_code=p.returncode)
        except Exception:
            return types.SimpleNamespace(output="", exit_code=-1)

    def shell(self, cmd, timeout=30):
        return self._shell(cmd, timeout=timeout)

    def swipe_ext(self, direction, scale=0.5):
        self._legacy("swipe_ext:%s" % direction, "legacy_u2_swipe_ext", mutation=True, adb=True)
        try:
            w, h = self.window_size()
            x, y = w // 2, h // 2
            dist = int((h if direction in ("up", "down") else w) * float(scale) / 2)
            if direction == "up":
                p1, p2 = (x, y + dist), (x, y - dist)
            elif direction == "down":
                p1, p2 = (x, y - dist), (x, y + dist)
            elif direction == "left":
                p1, p2 = (x + dist, y), (x - dist, y)
            else:
                p1, p2 = (x - dist, y), (x + dist, y)
            subprocess.run(["adb", "-s", self.device, "shell", "input", "swipe",
                            str(p1[0]), str(p1[1]), str(p2[0]), str(p2[1]), "300"],
                           capture_output=True, text=True, timeout=10)
            return True
        except Exception:
            return False

    def press(self, key):
        keymap = {"power": 26, "home": 3, "back": 4}
        self._legacy("press:%s" % key, "legacy_power_key", mutation=True, adb=True)
        try:
            subprocess.run(["adb", "-s", self.device, "shell", "input", "keyevent",
                            str(keymap.get(str(key), key))], capture_output=True,
                           text=True, timeout=10)
            return True
        except Exception:
            return False

    def click(self, x, y):
        return bool(self.backend.tap(x, y, 50).ok)

    def swipe(self, x1, y1, x2, y2, duration=0.3):
        return bool(self.backend.swipe(x1, y1, x2, y2, int(float(duration) * 1000)).ok)

    def app_start(self, package):
        return bool(self.backend.start_app(package).ok)

    def app_stop(self, package):
        return bool(self.backend.stop_app(package).ok)

    def __call__(self, **kwargs):
        return Selector(self, query=kwargs)

    def xpath(self, xpath):
        return XPathSelector(self, xpath)
