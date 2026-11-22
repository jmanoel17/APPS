#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3.8B - conservative HIDDEN_CONTROLS / TAP_TO_REVEAL helper.

Pure code: no device, no ADB, no Fast Close decisions. The watcher owns timing and the
single backend tap; this module only decides whether a reveal is safe/eligible and tracks
the one-attempt-per-stage budget.
"""
from __future__ import annotations

import hashlib
import re

DEFAULT_TOP_RATIO = 0.15
DEFAULT_BLOCKER_MAX_AREA = 0.35
DEFAULT_MARGIN_RATIO = 0.04
CTA_RE = re.compile(
    r"\b(install|instalar|download|baixar|get|open|abrir|play|shop|comprar|buy|continue|"
    r"continuar|learn more|saiba mais|call to action|cta)\b",
    re.I,
)

# Central content first. Top/bottom bands are deliberately avoided.
_SAFE_POINTS = (
    (0.50, 0.45, "center_safe"),
    (0.50, 0.55, "center_low_safe"),
    (0.38, 0.50, "left_center_safe"),
    (0.62, 0.50, "right_center_safe"),
    (0.50, 0.62, "center_lower_safe"),
    (0.35, 0.58, "left_lower_safe"),
    (0.65, 0.58, "right_lower_safe"),
)


def bounds(node):
    nums = re.findall(r"-?\d+", node.attrib.get("bounds", "") or "")
    if len(nums) != 4:
        return None
    x1, y1, x2, y2 = map(int, nums)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def tree_hash(raw):
    return hashlib.sha256((raw or "").encode("utf-8", "replace")).hexdigest()


def _is_cta(node):
    text = " ".join((node.attrib.get("resource-id", ""),
                     node.attrib.get("text", ""),
                     node.attrib.get("content-desc", "")))
    return bool(CTA_RE.search(text))


def choose_reveal_point(an, w, h, cfg=None):
    """Return {safe,x,y,reason}. Never return a point on a CTA/clickable control/top band."""
    cfg = cfg or {}
    w = int(w)
    h = int(h)
    top = h * float(cfg.get("fast_close_top_region_ratio", DEFAULT_TOP_RATIO))
    max_area = DEFAULT_BLOCKER_MAX_AREA * w * h
    margin = w * DEFAULT_MARGIN_RATIO
    blockers = []
    for n in (an or {}).get("nodes", []):
        b = bounds(n)
        if not b:
            continue
        x1, y1, x2, y2 = b
        area = (x2 - x1) * (y2 - y1)
        # Any clickable control is a blocker. A full-screen clickable ad region also makes the
        # center unsafe (it can open the advertiser); do not guess. CTA text only blocks when
        # it is on a small control, not a full-screen error/video label.
        if n.attrib.get("clickable") == "true" or (area < max_area and _is_cta(n)):
            blockers.append(b)
    if bool(cfg.get("hidden_controls_prefer_top_center", False)):
        # Google Play-style creatives expose the >| control only after a top-center tap.
        # It is not top-right/top-left and is still checked against every clickable/CTA blocker.
        x, y = int(w * 0.50), int(h * float(cfg.get("hidden_controls_top_reveal_y_ratio", 0.075)))
        if not any(b[0] - margin <= x <= b[2] + margin and
                   b[1] - margin <= y <= b[3] + margin for b in blockers):
            return {"safe": True, "x": x, "y": y, "reason": "top_center_safe"}

    for rx, ry, reason in _SAFE_POINTS:
        x, y = int(w * rx), int(h * ry)
        if y <= top or y >= h * 0.92:
            continue
        if any(b[0] - margin <= x <= b[2] + margin and
               b[1] - margin <= y <= b[3] + margin for b in blockers):
            continue
        return {"safe": True, "x": x, "y": y, "reason": reason}
    reason = "no_safe_point"
    if blockers:
        reason = "all_points_blocked"
    return {"safe": False, "x": None, "y": None, "reason": reason}


class HiddenControlsTracker:
    """One reveal attempt per stage, with explicit observation/uncertain handling."""

    def __init__(self, cfg, log_fn=None):
        self.cfg = cfg or {}
        self.log = log_fn or (lambda *a, **k: None)
        self.stage = None
        self.controls_seen = False
        self.last_control_at = None
        self.hidden_since = None
        self.attempts = 0
        self.pending = False
        self.pending_at = None
        self.uncertain = False
        self.before_hash = ""
        self.before_candidate = None
        self.last_result = ""
        self.last_command_id = ""

    def _stage(self, stage):
        if stage == self.stage:
            return
        self.stage = stage
        self.attempts = 0
        self.pending = False
        self.pending_at = None
        self.uncertain = False
        self.hidden_since = None
        self.before_hash = ""
        self.before_candidate = None
        self.last_result = ""
        self.last_command_id = ""
        # controls_seen is intentionally retained across a stage boundary only as historical
        # evidence for the same ad; a new ad gets a new tracker instance.

    def mark_control(self, now):
        self.controls_seen = True
        self.last_control_at = now
        self.hidden_since = None

    def observe(self, stage, now, visible_controls, eligible, raw="", candidate=None,
                safe_point=True):
        """Return dict(action=...) where action is NONE/REVEAL/REVEAL_PENDING/
        REVEAL_SUCCESS/REVEAL_NO_EFFECT/BLOCKED_*."""
        self._stage(stage)
        if visible_controls:
            self.mark_control(now)
            if self.pending:
                self.pending = False
                self.pending_at = None
                self.last_result = "CONTROLS_REAPPEARED"
                return {"action": "REVEAL_SUCCESS", "reason": "CONTROLS_REAPPEARED"}
            return {"action": "NONE", "reason": "CONTROLS_VISIBLE"}

        if not eligible:
            # A transition/countdown/CTA state is not an absent-controls window.
            self.hidden_since = None
            if self.pending:
                return {"action": "REVEAL_PENDING", "reason": "NOT_ELIGIBLE"}
            return {"action": "NONE", "reason": "NOT_ELIGIBLE"}

        if self.hidden_since is None:
            self.hidden_since = now

        if self.pending:
            obs_s = float(self.cfg.get("reveal_observation_window_ms", 1000)) / 1000.0
            if now - self.pending_at < obs_s:
                return {"action": "REVEAL_PENDING", "reason": "OBSERVATION_WINDOW"}
            self.pending = False
            self.pending_at = None
            self.last_result = "REVEAL_NO_EFFECT"
            return {"action": "REVEAL_NO_EFFECT", "reason": "REVEAL_NO_EFFECT"}

        if not safe_point:
            return {"action": "NONE", "reason": "NO_SAFE_POINT"}
        if self.uncertain:
            return {"action": "BLOCKED_UNCERTAIN", "reason": "TAP_UNCERTAIN"}
        if self.attempts >= int(self.cfg.get("max_reveal_attempts_per_stage", 1)):
            return {"action": "BLOCKED_MAX", "reason": "MAX_ATTEMPTS"}
        if bool(self.cfg.get("hidden_controls_require_history", True)) and not self.controls_seen:
            return {"action": "NONE", "reason": "NO_HISTORY"}
        age = now - self.hidden_since
        if age < float(self.cfg.get("hidden_controls_min_age_s", 5.0)):
            return {"action": "NONE", "reason": "TOO_EARLY", "age": age}

        self.attempts += 1
        self.pending = True
        self.pending_at = now
        self.before_hash = tree_hash(raw)
        self.before_candidate = candidate
        self.last_result = "REVEAL_ATTEMPTED"
        return {"action": "REVEAL", "reason": "REVEAL_ELIGIBLE", "age": age}

    def on_tap_result(self, status, command_id=""):
        status = str(status or "").lower()
        self.last_command_id = command_id or ""
        if status == "uncertain":
            self.uncertain = True
            self.pending = False
            self.pending_at = None
            self.last_result = "REVEAL_UNCERTAIN"
            return
        if status == "success":
            # Keep pending until the observation window sees controls or expires.
            self.pending = True
            return
        # failure/expired: the attempt is consumed; never auto-retry the same stage.
        self.pending = False
        self.pending_at = None
        self.last_result = "REVEAL_" + (status or "FAILED").upper()

    def result_after(self, visible_controls, after_hash="", candidate=None, elapsed_ms=None):
        out = {
            "controls_reappeared": bool(visible_controls),
            "result": "CONTROLS_REAPPEARED" if visible_controls else "REVEAL_NO_EFFECT",
            "before_tree_hash": self.before_hash,
            "after_tree_hash": after_hash or "",
            "candidate_before": self.before_candidate,
            "candidate_after": candidate,
            "time_to_controls_ms": elapsed_ms,
            "attempts": self.attempts,
        }
        self.last_result = out["result"]
        return out
