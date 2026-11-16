#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/agent_core/provision.py — SLN V2 (slim): manutenção do `adb reverse` (sem discovery/UI).

Reimplementa apenas o necessário de C:\\AndroidProjects\\dpn\\dpn\\agent\\provision.py:
  adb -s <serial> reverse tcp:<port> tcp:<port>
Usado pelo AgentHub para o agente alcançar o controlador via 127.0.0.1 quando a rede
bloqueia conexões iniciadas pelo aparelho.
"""
from __future__ import annotations

import subprocess


def build_reverse_args(serial: str, port: int) -> list[str]:
    if not serial:
        raise ValueError("serial obrigatório")
    return ["-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"]


def run_adb(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "adb não encontrado no PATH"
    except subprocess.TimeoutExpired:
        return 124, "", "adb expirou"


def ensure_adb_reverse(serial: str, port: int, timeout: int = 30) -> tuple[bool, str]:
    if not serial:
        return False, "sem serial ADB"
    rc, out, err = run_adb(build_reverse_args(serial, port), timeout=timeout)
    return rc == 0, (err or out or "").strip()
