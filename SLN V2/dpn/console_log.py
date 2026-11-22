#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-device console logging for DPN/IP rotation visibility."""
from __future__ import annotations

import os
import threading
from datetime import datetime

from . import state as S


class DPNConsoleLog:
    def __init__(self, base_dir, enabled=True, log_fn=None):
        self.base_dir = base_dir
        self.enabled = bool(enabled)
        self.log = log_fn or (lambda *a, **k: None)
        self._lock = threading.Lock()
        self.logs_dir = os.path.join(base_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

    def emit(self, group_id, members, event, **fields):
        if not self.enabled:
            return
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        extras = " ".join("%s=%s" % (k, v) for k, v in fields.items()
                          if v is not None and v != "")
        line = "[%s] [DPN][%s] group=%s%s\n" % (
            ts, event, group_id, (" " + extras if extras else ""))
        targets = [os.path.join(self.logs_dir, "dpn_%s.log" % S.safe_id(group_id))]
        for device in members or []:
            targets.append(os.path.join(self.logs_dir, S.safe_id(device) + ".log"))
        seen = set()
        with self._lock:
            for path in targets:
                if path in seen:
                    continue
                seen.add(path)
                try:
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(line)
                        f.flush()
                except Exception:
                    pass
        self.log(line.rstrip())
