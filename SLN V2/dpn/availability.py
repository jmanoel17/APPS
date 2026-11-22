#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DPN E2.6 - workload availability probe contract (external to Fast Close)."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class AvailabilityResult:
    status: str
    reason: str = ""
    observed_at_ms: int = 0
    probe_id: str = ""
    duration_ms: int = 0
    detail: dict = field(default_factory=dict)


class AvailabilityProbe:
    def probe(self, route_group_id, epoch_candidate, timeout_s=5.0):
        raise NotImplementedError


class NullAvailabilityProbe(AvailabilityProbe):
    """Disabled probe: connectivity health alone is authoritative."""

    def probe(self, route_group_id, epoch_candidate, timeout_s=5.0):
        return AvailabilityResult(AVAILABLE, "probe_disabled", int(time.time() * 1000))


class SequenceAvailabilityProbe(AvailabilityProbe):
    """Deterministic test probe. The last result repeats after the sequence ends."""

    def __init__(self, sequence=None, default=AVAILABLE):
        self.sequence = list(sequence) if sequence is not None else [default]
        self.i = 0
        self.calls = []

    def probe(self, route_group_id, epoch_candidate, timeout_s=5.0):
        status = self.sequence[min(self.i, len(self.sequence) - 1)]
        self.i += 1
        self.calls.append((route_group_id, epoch_candidate, status))
        return AvailabilityResult(status, "sequence", int(time.time() * 1000),
                                  str(uuid.uuid4()), 0)
