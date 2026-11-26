"""Testes do protocolo de agentes (puros)."""
import time

from dpn.agent.protocol import (
    PROTOCOL_VERSION,
    Command,
    MUTATING_OPS,
    Op,
    Result,
    ResultStatus,
    validate_command,
)


def test_command_roundtrip():
    cmd = Command(op=Op.SET_DPN_MODE.value, agent_id="a1",
                  expected_dpn_id="ID-1", params={"dpnMode": "full"})
    d = cmd.to_dict()
    back = Command.from_dict(d)
    assert back.op == cmd.op
    assert back.agent_id == "a1"
    assert back.expected_dpn_id == "ID-1"
    assert back.params == {"dpnMode": "full"}
    assert back.mutating is True
    assert d["protocol"] == PROTOCOL_VERSION
    assert d["type"] == "command"


def test_validate_rejects_wrong_agent_op_and_expired():
    cmd = Command(op=Op.REBOOT.value, agent_id="a2")
    assert validate_command(cmd, "a1") == "comando destinado a outro agente"

    bad = Command(op="nao_existe", agent_id="a1")
    assert validate_command(bad, "a1") == "operação desconhecida"

    exp = Command(op=Op.REBOOT.value, agent_id="a1", deadline=time.time() - 1)
    assert validate_command(exp, "a1") == "comando expirado"
    assert exp.is_expired()


def test_mutating_ops_superset():
    for op in (Op.REBOOT, Op.SWITCH_NODE, Op.REFRESH_TUNNEL, Op.SET_DPN_MODE,
               Op.ACCESS_SET_ONE, Op.NODE_LB_MODE_SET, Op.ADD_TUNNEL):
        assert op in MUTATING_OPS
    for op in (Op.GET_DEVICE_ID, Op.LIST_DEVICES, Op.GET_DPN_MODE):
        assert op not in MUTATING_OPS


def test_result_from_dict_statuses():
    r = Result.from_dict({"request_id": "x", "agent_id": "a1",
                          "op": "reboot", "status": "uncertain",
                          "error": "sem confirmação"})
    assert r.status == ResultStatus.UNCERTAIN.value
    assert not r.ok
