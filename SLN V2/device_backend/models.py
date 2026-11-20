#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalized types shared by DeviceBackend implementations (no u2/Agent JSON leaks)."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import xml.etree.ElementTree as ET


@dataclasses.dataclass
class NormalizedNode:
    node_id: str
    parent_id: str | None
    depth: int
    cls: str
    text: str
    content_desc: str
    resource_id: str
    bounds: tuple[int, int, int, int] | None
    clickable: bool
    enabled: bool
    visible: bool
    scrollable: bool


def _bounds_tuple(value):
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            b = tuple(int(v) for v in value)
        except Exception:
            return None
    else:
        nums = re.findall(r"-?\d+", str(value or ""))
        if len(nums) < 4:
            return None
        b = tuple(int(v) for v in nums[:4])
    if b[2] <= b[0] or b[3] <= b[1]:
        return None
    return b


def _bounds_str(b):
    return "" if not b else f"[{b[0]},{b[1]}][{b[2]},{b[3]}]"


@dataclasses.dataclass
class NormalizedTree:
    source: str
    raw_xml: str
    nodes: list[NormalizedNode]
    width: int
    height: int
    package: str = ""
    activity: str = ""

    @property
    def raw(self):
        return self.raw_xml

    def to_element(self):
        return ET.fromstring(self.raw_xml)


def tree_from_adb_xml(raw_xml, width=1080, height=2280):
    root = ET.fromstring(raw_xml)
    nodes = []

    def walk(el, parent_id, depth):
        if el.tag != "node":
            return
        a = el.attrib
        b = _bounds_tuple(a.get("bounds"))
        nid = f"n{len(nodes)}"
        nodes.append(NormalizedNode(
            nid, parent_id, depth, a.get("class", ""), a.get("text", ""),
            a.get("content-desc", ""), a.get("resource-id", ""), b,
            a.get("clickable", "") == "true", a.get("enabled", "true") != "false",
            a.get("visible-to-user", "true") != "false", a.get("scrollable", "") == "true"))
        for child in list(el):
            walk(child, nid, depth + 1)

    for el in list(root):
        walk(el, None, 0)
    pkg = ""
    for el in root.iter("node"):
        if el.attrib.get("package"):
            pkg = el.attrib["package"]
            break
    return NormalizedTree("adb", raw_xml, nodes, int(width), int(height), pkg)


def tree_from_agent_data(data, width=1080, height=2280):
    data = data or {}
    pkg = str(data.get("package") or "")
    root = ET.Element("hierarchy")
    elems = {}
    nodes = []
    for raw in data.get("nodes") or []:
        nid = str(raw.get("node_id") or f"n{len(nodes)}")
        pid = raw.get("parent_id")
        pid = None if pid is None else str(pid)
        b = _bounds_tuple(raw.get("bounds"))
        node = NormalizedNode(
            nid, pid, int(raw.get("depth") or 0), str(raw.get("class") or ""),
            str(raw.get("text") or ""), str(raw.get("content_desc") or ""),
            str(raw.get("resource_id") or ""), b, bool(raw.get("clickable")),
            bool(raw.get("enabled", True)), bool(raw.get("visible", True)),
            bool(raw.get("scrollable")))
        nodes.append(node)
        elems[nid] = ET.Element("node", {
            "package": pkg, "class": node.cls, "text": node.text,
            "content-desc": node.content_desc, "resource-id": node.resource_id,
            "bounds": _bounds_str(node.bounds),
            "clickable": "true" if node.clickable else "false",
            "enabled": "true" if node.enabled else "false",
            "visible-to-user": "true" if node.visible else "false",
            "scrollable": "true" if node.scrollable else "false"})
    for node in nodes:
        el = elems[node.node_id]
        if node.parent_id and node.parent_id in elems:
            elems[node.parent_id].append(el)
        else:
            root.append(el)
    raw_xml = ET.tostring(root, encoding="unicode")
    return NormalizedTree("agent", raw_xml, nodes, int(width), int(height), pkg,
                          str(data.get("activity") or ""))


@dataclasses.dataclass
class ForegroundState:
    package: str = ""
    activity: str = ""
    component: str = ""
    window_id: int = 0
    observed_at_ms: int = 0

    def as_dict(self):
        return dataclasses.asdict(self)


@dataclasses.dataclass
class ScreenshotFrame:
    png: bytes
    width: int
    height: int
    rotation: int = 0
    format: str = "png"
    sha256: str = ""

    def __post_init__(self):
        if not self.sha256 and self.png:
            self.sha256 = hashlib.sha256(self.png).hexdigest()


@dataclasses.dataclass
class DeviceStatus:
    screen_on: bool = True
    keyguard_locked: bool = False
    display: dict = dataclasses.field(default_factory=dict)
    foreground_package: str = ""
    foreground_activity: str = ""
    battery_pct: int | None = None
    uptime_ms: int | None = None
    raw: dict = dataclasses.field(default_factory=dict)

    def blocked(self):
        if not self.screen_on:
            return True, "screen_off"
        if self.keyguard_locked:
            return True, "keyguard_showing"
        return False, ""


@dataclasses.dataclass
class BackendResult:
    status: str
    command_id: str = ""
    error: str = ""
    data: object = None
    backend: str = ""

    @property
    def ok(self):
        return self.status.lower() == "success"

    @property
    def uncertain(self):
        return self.status.lower() == "uncertain"

    def as_dict(self):
        return dataclasses.asdict(self)


class BackendError(RuntimeError):
    pass


class ShadowMutationBlocked(BackendError):
    pass
