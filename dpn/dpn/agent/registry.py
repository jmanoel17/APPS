"""Registro de agentes pareados e sessoes de pareamento."""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class AgentRecord:
    agent_id: str
    token_hash: str = ""
    name: str = ""
    dpn_mac: str = ""            # MAC de referencia (DPN 1/2/3)
    device_id: int | None = None  # cadastro (unidade) associado
    local_ip: str = ""           # IP local informado pelo agente (opcional)
    paired_at: float = 0.0
    last_seen: float = 0.0


@dataclass
class AgentRuntime:
    """Estado volatil do agente (nao persistido)."""

    agent_id: str
    online: bool = False
    observed_ip: str = ""         # IP observado na conexao (peer do WSS)
    local_ip: str = ""            # IP informado pelo agente
    last_heartbeat: float = 0.0
    observed_dpn_id: str = ""
    atomos_status: str = ""       # "", connected, auth_failed, unreachable
    last_error: str = ""
    last_op: str = ""
    last_result_status: str = ""


@dataclass
class PairingSession:
    code: str
    expires_at: float
    used: bool = False

    def valid(self, now: float | None = None) -> bool:
        return not self.used and (now or time.time()) < self.expires_at


class PairingManager:
    """Codigos temporarios de pareamento (um por agente)."""

    def __init__(self, ttl: float = 300.0):
        self.ttl = ttl
        self._sessions: dict[str, PairingSession] = {}

    def open(self, ttl: float | None = None) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._sessions[code] = PairingSession(code, time.time() + (ttl or self.ttl))
        return code

    def consume(self, code: str) -> bool:
        sess = self._sessions.get(code)
        if sess is None or not sess.valid():
            return False
        sess.used = True
        return True

    def is_consumed(self, code: str) -> bool:
        sess = self._sessions.get(code)
        return bool(sess and sess.used)

    def is_valid(self, code: str) -> bool:
        sess = self._sessions.get(code)
        return bool(sess and sess.valid())

    def cancel(self, code: str) -> None:
        self._sessions.pop(code, None)
