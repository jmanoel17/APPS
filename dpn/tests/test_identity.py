"""Identidade, conflito (mesma DPN), agente indisponivel e migracao."""
import sqlite3
import tempfile

import pytest

from dpn.agent.client import AgentClient
from dpn.agent.manager import AgentRuntime
from dpn.agent.protocol import Result
from dpn.atomos.errors import IdentityMismatchError
from dpn.config import Config
from dpn.models import Device, IdentityState, TLSMode
from dpn.service import DPNService
from dpn.storage.database import Database


class FakeManager:
    def __init__(self, results: dict | None = None):
        self.runtimes: dict[str, AgentRuntime] = {}
        self.results = results or {}

    def runtime(self, agent_id):
        return self.runtimes.setdefault(agent_id, AgentRuntime(agent_id=agent_id))

    def send(self, agent_id, op, params=None, expected_dpn_id=None, timeout=20):
        return self.results.get(op, Result("r", agent_id, op, "success", data={}))


def make_service(manager=None):
    cfg = Config()
    cfg.data_dir = tempfile.mkdtemp()
    cfg.resolve()
    svc = DPNService(cfg)
    if manager is not None:
        svc.attach_agent_manager(manager)
    return svc


def _online(fm, agent_id, status="connected", last_op=""):
    rt = fm.runtime(agent_id)
    rt.online = True
    rt.atomos_status = status
    rt.last_op = last_op


def test_three_agents_distinct_ids():
    fm = FakeManager()
    svc = make_service(fm)
    ids = []
    for i, mac in enumerate(("70:19:88:c1:a2:fe", "dc:32:62:24:9e:d2",
                             "70:19:88:c1:80:ce"), start=1):
        d = svc.add_device(Device(nickname=f"DPN {i}", address="34.34.34.34",
                                  tls_mode=TLSMode.INSECURE, mac_reference=mac))
        svc.associate_agent(f"ag{i}", d.id)
        _online(fm, f"ag{i}")
        obs = f"ID-{i}"
        svc.set_observed_identity(d.id, obs)
        svc.set_expected_identity(d.id, obs)
        ids.append(d.id)
    states = [svc.identity_state(svc.get_device(i)) for i in ids]
    assert states == [IdentityState.ID_VALIDATED] * 3
    assert svc.conflicts() == {}
    for i in ids:
        assert svc.can_mutate(svc.get_device(i))[0] is True


def test_two_agents_same_id_conflict_blocks():
    fm = FakeManager()
    svc = make_service(fm)
    d2 = svc.add_device(Device(nickname="DPN 2", address="34.34.34.34",
                               tls_mode=TLSMode.INSECURE))
    d3 = svc.add_device(Device(nickname="DPN 3", address="34.34.34.34",
                               tls_mode=TLSMode.INSECURE))
    svc.associate_agent("ag2", d2.id)
    svc.associate_agent("ag3", d3.id)
    _online(fm, "ag2")
    _online(fm, "ag3")
    svc.set_observed_identity(d2.id, "ID-SAME")
    svc.set_observed_identity(d3.id, "ID-SAME")
    svc.set_expected_identity(d2.id, "ID-SAME")
    svc.set_expected_identity(d3.id, "ID-SAME")

    assert svc.identity_state(svc.get_device(d2.id)) == IdentityState.CONFLICT
    assert svc.identity_state(svc.get_device(d3.id)) == IdentityState.CONFLICT
    assert svc.can_mutate(svc.get_device(d2.id))[0] is False
    assert "conflito" in svc.can_mutate(svc.get_device(d3.id))[1].lower()


def test_id_change_behind_agent_blocks():
    fm = FakeManager()
    svc = make_service(fm)
    d = svc.add_device(Device(nickname="DPN 1", address="34.34.34.34",
                              tls_mode=TLSMode.INSECURE))
    svc.associate_agent("ag1", d.id)
    _online(fm, "ag1")
    svc.set_observed_identity(d.id, "ID-A")
    svc.set_expected_identity(d.id, "ID-A")
    assert svc.identity_state(svc.get_device(d.id)) == IdentityState.ID_VALIDATED

    # a DPN atras do agente muda
    svc.set_observed_identity(d.id, "ID-B")
    assert svc.identity_state(svc.get_device(d.id)) == IdentityState.ID_MISMATCH
    assert svc.can_mutate(svc.get_device(d.id))[0] is False


def test_agent_up_atomos_down():
    fm = FakeManager()
    svc = make_service(fm)
    d = svc.add_device(Device(nickname="DPN 1", address="34.34.34.34",
                              tls_mode=TLSMode.INSECURE))
    svc.associate_agent("ag1", d.id)
    _online(fm, "ag1", status="unreachable", last_op="get_device_id")
    assert svc.identity_state(svc.get_device(d.id)) == IdentityState.ATOMOS_UNREACHABLE


def test_atomos_unreachable_does_not_block_agent_mutation():
    # Regressao: apos uma oscilacao/ipNotChanged no switchNode, o estado fica
    # "inacessivel" (exibido), mas NAO pode bloquear mutacoes no modo agente —
    # o agente reconfere a identidade a cada operacao.
    fm = FakeManager()
    svc = make_service(fm)
    d = svc.add_device(Device(nickname="DPN 1", address="34.34.34.34",
                              tls_mode=TLSMode.INSECURE))
    svc.associate_agent("ag1", d.id)
    _online(fm, "ag1", status="unreachable", last_op="switch_node")
    svc.set_observed_identity(d.id, "ID-A")
    svc.set_expected_identity(d.id, "ID-A")

    assert svc.identity_state(svc.get_device(d.id)) == IdentityState.ATOMOS_UNREACHABLE
    ok, why = svc.can_mutate(svc.get_device(d.id))
    assert ok is True, why


def test_unlinked_and_agent_unavailable():
    fm = FakeManager()
    svc = make_service(fm)
    d = svc.add_device(Device(nickname="DPN 1", address="34.34.34.34",
                              tls_mode=TLSMode.INSECURE))
    svc.associate_agent("ag1", d.id)
    # agente offline
    assert svc.identity_state(svc.get_device(d.id)) == IdentityState.AGENT_UNAVAILABLE
    # online mas sem vinculo
    _online(fm, "ag1")
    assert svc.identity_state(svc.get_device(d.id)) == IdentityState.IDENTITY_UNLINKED


def test_agent_client_identity_mismatch_and_uncertain():
    dev = Device(nickname="x", address="34.34.34.34", agent_id="ag1",
                 expected_dpn_id="ID-A", tls_mode=TLSMode.INSECURE)
    mgr = FakeManager({"reboot": Result("r", "ag1", "reboot", "failure",
                                        reason="identity_mismatch")})
    client = AgentClient(dev, mgr)
    with pytest.raises(IdentityMismatchError):
        client.reboot()

    from dpn.atomos.errors import UncertainError
    mgr2 = FakeManager({"reboot": Result("r", "ag1", "reboot", "uncertain",
                                         error="sem confirmação")})
    with pytest.raises(UncertainError):
        AgentClient(dev, mgr2).reboot()


def test_migration_preserves_data_and_adds_columns():
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/old.db"
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname TEXT NOT NULL, address TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 443,
                tls_mode TEXT NOT NULL DEFAULT 'verify',
                ca_bundle_path TEXT, username TEXT NOT NULL DEFAULT 'admin',
                notes TEXT DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER,
                device_nickname TEXT NOT NULL, action TEXT NOT NULL,
                status TEXT NOT NULL, message TEXT NOT NULL, ts REAL NOT NULL
            );
        """)
        con.execute("INSERT INTO devices (nickname,address,created_at,updated_at)"
                    " VALUES ('antigo','34.34.34.34',1,1)")
        con.execute("INSERT INTO history (device_id,device_nickname,action,status,"
                    "message,ts) VALUES (1,'antigo','login','ok','ok',1)")
        con.commit()
        con.close()

        db = Database(path)
        cols = {r["name"] for r in db._conn.execute(
            "PRAGMA table_info(devices)").fetchall()}
        assert {"agent_id", "expected_dpn_id", "last_observed_id",
                "mac_reference"} <= cols
        devices = db.list_devices()
        assert len(devices) == 1 and devices[0].nickname == "antigo"
        assert devices[0].agent_id is None
        assert len(db.list_logs()) == 1
        db.close()
