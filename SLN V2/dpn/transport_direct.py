#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/transport_direct.py — DirectorAtomOS: adapter fino sobre AtomOSClient (PC -> Deeper).

Não importa httpx aqui: recebe um cliente já construído (AtomOSClient ou um fake nos testes).
Preserva as exceções tipadas do atomos_core (AuthError/UncertainError/UnexpectedResponseError/
ConnectionError), que o DeeperConnectProvider já sabe tratar.
"""
from __future__ import annotations

from .atomos_ops import AtomOSOps


class DirectAtomOS(AtomOSOps):
    def __init__(self, client):
        self._client = client

    @property
    def raw(self):
        return self._client

    def check_online(self):
        return bool(self._client.check_online())

    def login(self):
        return self._client.login()

    def get_device_id(self):
        return self._client.get_device_id()

    def get_node_type(self):
        return self._client.get_node_type()

    def get_node_lb_mode(self):
        return self._client.get_node_lb_mode()

    def get_exit_ip(self):
        return self._client.get_exit_ip()

    def list_access_control(self):
        return self._client.list_devices()

    def set_node_lb_mode(self, mode):
        return self._client.set_node_lb_mode(mode)

    def switch_node(self, tunnel_code, current_ip):
        return self._client.switch_node(tunnel_code, current_ip)

    def refresh_tunnel(self, tunnel_code):
        return self._client.refresh_tunnel(tunnel_code)

    def close(self):
        return self._client.close()
