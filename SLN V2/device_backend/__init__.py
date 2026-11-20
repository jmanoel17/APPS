#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeviceBackend factory."""
from .models import (NormalizedTree, NormalizedNode, ForegroundState, ScreenshotFrame,
                     DeviceStatus, BackendResult, BackendError, ShadowMutationBlocked)
from .adb_backend import ADBBackend
from .agent_backend import AgentBackend


def make_backend(name, d=None, device="", cfg=None, command_fn=None, agent_id=None):
    name = str(name or "adb").lower()
    if name == "adb":
        return ADBBackend(d, device=device, cfg=cfg)
    if name == "agent":
        mode = str((cfg or {}).get("execution_mode", "shadow")).lower()
        return AgentBackend(command_fn, agent_id=agent_id or "", execution_mode=mode)
    raise BackendError("UNKNOWN_DEVICE_BACKEND:" + name)


__all__ = ["NormalizedTree", "NormalizedNode", "ForegroundState", "ScreenshotFrame",
           "DeviceStatus", "BackendResult", "BackendError", "ShadowMutationBlocked",
           "ADBBackend", "AgentBackend", "make_backend"]
