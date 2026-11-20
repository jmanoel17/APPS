#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
device_agent — Controller do protocolo sln-device/1 (Device Agent). Namespace SEPARADO do
Deeper Agent (`dpn/agent_core`). E3.1: server-side + mock; sem APK/ADB/TAP/SWIPE/SCREENSHOT.

Módulos:
  protocol.py   mensagens/ops/validação
  errors.py     códigos de erro
  models.py     dataclasses
  state.py      SQLite (agents/pairing/commands/events/device_map)
  registry.py   autenticação (token hash constant-time), revogação
  pairing.py    pairing code (uso único, TTL) + token individual
  server.py     controller asyncio (transport-agnóstico) + WSS real (lazy)
  mock_agent.py agente loopback para testes/benchmark
  ui_model.py   correlação DPN/Access + linhas read-only de UI
"""

__all__ = ["protocol", "errors", "models", "state", "registry", "pairing", "server",
           "mock_agent", "ui_model"]
