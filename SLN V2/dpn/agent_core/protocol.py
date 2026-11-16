"""Protocolo de comandos entre o controlador DPN Control e os agentes Android.

Versao do protocolo e mensagens tipadas. Cada mensagem inclui versao, request_id,
agent_id, identidade esperada da DPN, operacao, parametros e prazo de validade.

Nada aqui transporta senha em claro: a senha do AtomOS e RSA-OAEP no agente; o
controlador nunca a envia pelo canal. Tokens/cookies do AtomOS ficam no agente.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

PROTOCOL_VERSION = "dpn-agent/1"
DEFAULT_COMMAND_TTL = 20.0


class MsgType(str, Enum):
    HELLO = "hello"
    HELLO_OK = "hello_ok"
    PAIR_REQUEST = "pair_request"
    PAIR_OK = "pair_ok"
    PAIR_DENIED = "pair_denied"
    COMMAND = "command"
    RESULT = "result"
    HEARTBEAT = "heartbeat"
    ERROR = "error"


class Op(str, Enum):
    # controle (nao acessa o AtomOS)
    CONFIGURE = "configure"
    # leitura
    GET_DEVICE_ID = "get_device_id"
    GET_DEVICE_INFO = "get_device_info"
    LOGIN = "login"
    GET_DPN_MODE = "get_dpn_mode"
    LIST_DEVICES = "list_devices"
    ACCESS_GET_SWITCH = "access_get_switch"
    LIST_TUNNELS = "list_tunnels"
    NODE_LB_MODE_GET = "node_lb_mode_get"
    # escrita
    SET_DPN_MODE = "set_dpn_mode"
    ACCESS_SET_SWITCH = "access_set_switch"
    ACCESS_SET_ONE = "access_set_one"
    ACCESS_DELETE = "access_delete"
    SWITCH_NODE = "switch_node"
    REFRESH_TUNNEL = "refresh_tunnel"
    NODE_LB_MODE_SET = "node_lb_mode_set"
    ADD_TUNNEL = "add_tunnel"
    REBOOT = "reboot"


MUTATING_OPS = {
    Op.SET_DPN_MODE, Op.ACCESS_SET_SWITCH, Op.ACCESS_SET_ONE, Op.ACCESS_DELETE,
    Op.SWITCH_NODE, Op.REFRESH_TUNNEL, Op.NODE_LB_MODE_SET, Op.ADD_TUNNEL,
    Op.REBOOT,
}


class ResultStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    EXPIRED = "expired"
    UNCERTAIN = "uncertain"


def new_request_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Command:
    op: str
    agent_id: str
    expected_dpn_id: str | None = None
    params: dict = field(default_factory=dict)
    request_id: str = field(default_factory=new_request_id)
    deadline: float = 0.0
    protocol: str = PROTOCOL_VERSION
    mutating: bool = False

    def __post_init__(self) -> None:
        if not self.deadline:
            self.deadline = time.time() + DEFAULT_COMMAND_TTL
        if self.op in tuple(o.value for o in MUTATING_OPS):
            self.mutating = True

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) > self.deadline

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "type": MsgType.COMMAND.value,
            "request_id": self.request_id,
            "agent_id": self.agent_id,
            "expected_dpn_id": self.expected_dpn_id,
            "op": self.op,
            "params": self.params,
            "deadline": self.deadline,
        }

    @classmethod
    def from_dict(cls, m: dict) -> "Command":
        return cls(
            op=str(m.get("op", "")),
            agent_id=str(m.get("agent_id", "")),
            expected_dpn_id=m.get("expected_dpn_id"),
            params=m.get("params") or {},
            request_id=str(m.get("request_id", new_request_id())),
            deadline=float(m.get("deadline") or 0),
            protocol=str(m.get("protocol", PROTOCOL_VERSION)),
        )


@dataclass
class Result:
    request_id: str
    agent_id: str
    op: str
    status: str
    data: dict | list | None = None
    error: str = ""
    reason: str = ""
    observed_dpn_id: str | None = None
    protocol: str = PROTOCOL_VERSION

    @property
    def ok(self) -> bool:
        return self.status == ResultStatus.SUCCESS.value

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "type": MsgType.RESULT.value,
            "request_id": self.request_id,
            "agent_id": self.agent_id,
            "op": self.op,
            "status": self.status,
            "data": self.data,
            "error": self.error,
            "reason": self.reason,
            "observed_dpn_id": self.observed_dpn_id,
        }

    @classmethod
    def from_dict(cls, m: dict) -> "Result":
        return cls(
            request_id=str(m.get("request_id", "")),
            agent_id=str(m.get("agent_id", "")),
            op=str(m.get("op", "")),
            status=str(m.get("status", ResultStatus.FAILURE.value)),
            data=m.get("data"),
            error=str(m.get("error", "")),
            reason=str(m.get("reason", "")),
            observed_dpn_id=m.get("observed_dpn_id"),
            protocol=str(m.get("protocol", PROTOCOL_VERSION)),
        )


def validate_command(cmd: Command, agent_id: str, now: float | None = None) -> str | None:
    """Retorna motivo de rejeicao ou None se aceitavel."""
    if cmd.protocol != PROTOCOL_VERSION:
        return "protocolo incompatível"
    if cmd.agent_id != agent_id:
        return "comando destinado a outro agente"
    if cmd.op not in tuple(o.value for o in Op):
        return "operação desconhecida"
    if cmd.is_expired(now):
        return "comando expirado"
    return None
