#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""device_agent/models.py — dataclasses do controller Device Agent."""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class AgentRecord:
    agent_id: str
    token_hash: str = ""
    revoked: bool = False
    epoch: int = 1
    android_id: str = ""
    build_fingerprint: str = ""
    manufacturer: str = ""
    model: str = ""
    sdk: int = 0
    agent_version: str = ""
    capabilities: dict = dataclasses.field(default_factory=dict)
    last_seen: float = 0.0
    created_at: float = 0.0
    aliases: dict = dataclasses.field(default_factory=dict)
    display: dict = dataclasses.field(default_factory=dict)
    possible_reenrollment: bool = False


@dataclasses.dataclass
class CommandRecord:
    command_id: str
    agent_id: str
    device_id: str
    op: str
    params: dict
    status: str
    created_at: float
    sent_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    data: object = None
    deadline_ms: int = 0
    mutating: bool = False


@dataclasses.dataclass
class HeartbeatInfo:
    uptime_ms: int = 0
    battery_pct: int = 0
    charging: bool = False
    screen_on: bool = False
    foreground_package: str = ""
    lan_ip: str = ""
    agent_version: str = ""
    accessibility_enabled: bool = False
    last_command_status: str = ""
    ts: float = 0.0
