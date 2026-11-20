#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeviceBackend contract used by the bot. No generic shell operation is exposed."""
from __future__ import annotations

import abc


class DeviceBackend(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def get_ui_tree(self, **kwargs):
        raise NotImplementedError

    @abc.abstractmethod
    def capture_screen(self, **kwargs):
        raise NotImplementedError

    @abc.abstractmethod
    def get_foreground_app(self):
        raise NotImplementedError

    @abc.abstractmethod
    def get_status(self):
        raise NotImplementedError

    @abc.abstractmethod
    def tap(self, x, y, duration_ms=50):
        raise NotImplementedError

    @abc.abstractmethod
    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        raise NotImplementedError

    @abc.abstractmethod
    def back(self):
        raise NotImplementedError

    @abc.abstractmethod
    def home(self):
        raise NotImplementedError

    @abc.abstractmethod
    def start_app(self, package):
        raise NotImplementedError

    @abc.abstractmethod
    def stop_app(self, package):
        raise NotImplementedError

    @abc.abstractmethod
    def clear_app_data(self, package):
        raise NotImplementedError
