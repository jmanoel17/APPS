#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sync AtomOSOps adapter over the unified Device Agent pool."""
from __future__ import annotations

import asyncio

from .atomos_ops import AtomOSOps, AgentNotStarted, AtomOSOpsError
from .atomos_core.errors import AuthError, UncertainError, ConnectionError as AtomOSConnectionError


class AgentPoolAtomOS(AtomOSOps):
    def __init__(self, pool_client, physical_dpn_id, tunnel_code, loop=None,
                 timeout=25.0, log_fn=None):
        self.pool_client = pool_client
        self.physical_dpn_id = str(physical_dpn_id or "")
        self.tunnel_code = str(tunnel_code or "")
        self.loop = loop if loop is not None else getattr(pool_client, "loop", None)
        self.timeout = float(timeout)
        self.log = log_fn or (lambda *a, **k: None)

    def _run(self, coro):
        if self.loop is not None and self.loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
            return fut.result(timeout=self.timeout)
        return asyncio.run(coro)

    def _read(self, op):
        from .transport_pool import TransportUncertain, ReadFailover
        try:
            data, _candidate = self._run(
                self.pool_client.read(self.physical_dpn_id, op))
            return data
        except TransportUncertain as e:
            raise UncertainError(str(e))
        except ReadFailover as e:
            msg = str(e)
            if "UNAUTHORIZED" in msg or "AUTH" in msg.upper():
                raise AuthError(msg)
            raise AtomOSOpsError(msg)

    def check_online(self):
        try:
            return self.pool_client.pool.select_read_transport(self.physical_dpn_id) is not None
        except Exception:
            return False

    def login(self):
        # Identity read performs the AtomOS login inside the selected Agent.
        return self._read("GET_DPN_IDENTITY")

    def get_device_id(self):
        return self._read("GET_DPN_IDENTITY")

    def get_node_type(self):
        return self._read("GET_DPN_MODE")

    def get_node_lb_mode(self):
        return self._read("GET_DPN_NODE_LB_MODE")

    def get_exit_ip(self):
        return self._read("LIST_DPN_TUNNELS")

    def list_access_control(self):
        return self._read("LIST_DPN_ACCESS_CONTROL")

    def set_node_lb_mode(self, mode):
        raise AtomOSOpsError("DPN_NODE_LB_MODE_MUTATION_NOT_IMPLEMENTED")

    def _mutation(self, op, params):
        from .transport_pool import TransportUncertain, ReadFailover
        from .auth import AuthBackoff
        from .mutation_lease import LeaseBusy
        try:
            data, _candidate = self._run(self.pool_client.execute_mutation(
                self.physical_dpn_id, op, params, op))
            return data
        except TransportUncertain as e:
            raise UncertainError(str(e))
        except AuthBackoff as e:
            raise AuthError(str(e))
        except (LeaseBusy, ReadFailover) as e:
            raise AtomOSOpsError(str(e))

    def switch_node(self, tunnel_code, current_ip):
        params = {"expected_dpn_id": self.physical_dpn_id,
                  "tunnel_code": tunnel_code, "current_ip": current_ip}
        return self._mutation("DPN_SWITCH_NODE", params)

    def refresh_tunnel(self, tunnel_code):
        params = {"expected_dpn_id": self.physical_dpn_id,
                  "tunnel_code": tunnel_code}
        return self._mutation("DPN_REFRESH_ROUTE_SET", params)

    def close(self):
        pass
