#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/agent_core — camada do transporte AGENT VENDORIZADA (Qt-free) do projeto DPN Control.

Origem: C:\\AndroidProjects\\dpn\\dpn\\agent\\{protocol.py,server.py,provision.py}.
- `protocol.py`: mensagens/Op/Result (stdlib).
- `server.py`: AgentServer WSS (importa `websockets`; carregado LAZILY pelo AgentHub).
- `provision.py`: `adb reverse` (slim, sem discovery/UI).

NÃO importa PySide6/manager.py/UI/service/storage. Não é state machine DPN.
"""

__all__ = ["protocol", "server", "provision"]
