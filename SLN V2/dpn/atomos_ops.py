#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/atomos_ops.py — Interface de OPERAÇÕES AtomOS (nível de operação, não HTTP cru).

O agente Android NÃO é proxy HTTP: expõe Ops tipadas. Para reutilizá-lo sem alterar o APK,
o limite de transporte é por OPERAÇÃO:

    AtomOSOps: check_online/login/get_device_id/get_node_type/get_node_lb_mode/get_exit_ip/
               set_node_lb_mode/switch_node/refresh_tunnel/close

Implementações:
    DirectAtomOS (dpn/transport_direct.py) -> AtomOSClient (httpx)
    AgentAtomOS  (dpn/agent_transport.py)  -> AgentHub (WSS -> Android -> AtomOS)

O DeeperConnectProvider usa esta interface e escolhe por controller.transport.
Não contém state machine (isso é do coordinator).
"""
from __future__ import annotations


class AtomOSOpsError(Exception):
    code = "ATOMOS_OPS_ERROR"


class AgentOfflineError(AtomOSOpsError):
    code = "AGENT_OFFLINE"


class AgentTimeout(AtomOSOpsError):
    code = "AGENT_TIMEOUT"


class AgentServerPortInUse(AtomOSOpsError):
    code = "AGENT_SERVER_PORT_IN_USE"


class AgentHubBusy(AtomOSOpsError):
    code = "AGENT_HUB_BUSY"


class AgentNotStarted(AtomOSOpsError):
    code = "AGENT_NOT_STARTED"


class AgentResponseMismatch(AtomOSOpsError):
    code = "AGENT_RESPONSE_MISMATCH"


class AtomOSTargetUnreachable(AtomOSOpsError):
    code = "HTTP_TARGET_UNREACHABLE"


class TLSIdentityError(AtomOSOpsError):
    code = "TLS_IDENTITY"


class AtomOSOps:
    """Contrato de operações AtomOS usado pelo DeeperConnectProvider."""

    def check_online(self):
        raise NotImplementedError

    def login(self):
        raise NotImplementedError

    def get_device_id(self):
        raise NotImplementedError

    def get_node_type(self):
        raise NotImplementedError

    def get_node_lb_mode(self):
        raise NotImplementedError

    def get_exit_ip(self):
        raise NotImplementedError

    def set_node_lb_mode(self, mode):
        raise NotImplementedError

    def switch_node(self, tunnel_code, current_ip):
        raise NotImplementedError

    def refresh_tunnel(self, tunnel_code):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError
