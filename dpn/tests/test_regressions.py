"""Testes de regressao dos bugs de identidade/associacao/observacao."""
import tempfile

from dpn.agent.registry import AgentRecord
from dpn.config import Config
from dpn.models import Device, IdentityState, TLSMode
from dpn.service import DPNService


def make_service():
    cfg = Config()
    cfg.data_dir = tempfile.mkdtemp()
    cfg.resolve()
    return DPNService(cfg)


def _dev(svc, name="DPN 1"):
    return svc.add_device(Device(nickname=name, address="34.34.34.34",
                                 tls_mode=TLSMode.INSECURE))


def _agent(svc, aid="ag1"):
    svc.upsert_agent(AgentRecord(agent_id=aid))
    return aid


def test_valid_id_rejects_placeholders():
    assert DPNService.valid_id("Deeper-Wire-HW-1|abc")
    for bad in (None, "", "-", "—", "unknown", "none", "null"):
        assert not DPNService.valid_id(bad)


def test_finish_agent_setup_persists_observed_and_binds():
    svc = make_service()
    d = _dev(svc)
    a = _agent(svc)
    st = svc.finish_agent_setup(d.id, a, "ID-1", transport="agent", bind=True)
    assert st["observed"] == "ID-1"
    assert st["expected"] == "ID-1"
    assert st["bound"] is True
    fresh = svc.get_device(d.id)
    assert fresh.last_observed_id == "ID-1"
    assert fresh.expected_dpn_id == "ID-1"
    assert fresh.last_observed_at > 0
    assert fresh.transport_mode == "agent"
    assert fresh.agent_id == a


def test_bind_requires_observed_and_reports_failure():
    svc = make_service()
    d = _dev(svc)
    fresh = svc.get_device(d.id)
    assert svc.bind_identity(fresh) is False        # sem observacao
    svc.set_observed_identity(d.id, "ID-X")
    fresh = svc.get_device(d.id)
    assert svc.bind_identity(fresh) is True
    assert svc.get_device(d.id).expected_dpn_id == "ID-X"


def test_stale_update_does_not_clobber_observed():
    svc = make_service()
    d = _dev(svc)
    stale = svc.get_device(d.id)                    # objeto antigo (sem observed)
    svc.set_observed_identity(d.id, "ID-9")
    stale.nickname = "renomeada"
    svc.update_device(stale)                        # NAO deve apagar a observacao
    fresh = svc.get_device(d.id)
    assert fresh.last_observed_id == "ID-9"
    assert fresh.nickname == "renomeada"


def test_associate_is_one_to_one():
    svc = make_service()
    d1 = _dev(svc, "DPN 1")
    d2 = _dev(svc, "DPN 2")
    a = _agent(svc, "agA")
    svc.associate_agent(a, d1.id)
    assert svc.get_device(d1.id).agent_id == "agA"
    # reaproveita o MESMO agente em outro cadastro -> remove o vinculo anterior
    svc.associate_agent(a, d2.id)
    assert svc.get_device(d2.id).agent_id == "agA"
    assert svc.get_device(d1.id).agent_id is None
    assert svc.get_agent("agA").device_id == d2.id


def test_disassociate_updates_both_sides():
    svc = make_service()
    d = _dev(svc)
    a = _agent(svc)
    svc.associate_agent(a, d.id)
    svc.disassociate_agent(a)
    assert svc.get_device(d.id).agent_id is None
    assert svc.get_agent(a).device_id is None


def test_delete_device_clears_refs_keeps_history():
    svc = make_service()
    d = _dev(svc)
    a = _agent(svc)
    svc.associate_agent(a, d.id)
    svc.set_device_tunnel_ips(d.id, ["1.2.3.4"])
    from dpn.models import ActionStatus
    svc.log(d, "login", ActionStatus.OK, "ok")
    svc.delete_device(d.id)
    assert svc.get_device(d.id) is None
    assert svc.get_agent(a).device_id is None
    assert svc.shared_tunnel_ips() == {}
    # historico preservado (device_id pode ficar orfao, mas o registro fica)
    assert len(svc.history()) >= 1


def test_shared_tunnel_ips_resolves_after_delete():
    svc = make_service()
    d1 = _dev(svc, "DPN 1")
    d2 = _dev(svc, "DPN 2")
    svc.set_device_tunnel_ips(d1.id, ["9.9.9.9"])
    svc.set_device_tunnel_ips(d2.id, ["9.9.9.9"])
    assert "9.9.9.9" in svc.shared_tunnel_ips()
    svc.delete_device(d2.id)
    assert "9.9.9.9" not in svc.shared_tunnel_ips()


def test_direct_mode_without_expected_blocks_mutation():
    svc = make_service()
    d = _dev(svc)
    fresh = svc.get_device(d.id)
    assert fresh.transport_mode == "direct"
    ok, why = svc.can_mutate(fresh)
    assert ok is False
    assert "não vinculada" in why


def test_transport_switch_preserves_agent_and_identity():
    svc = make_service()
    d = _dev(svc)
    a = _agent(svc)
    svc.associate_agent(a, d.id)
    svc.set_observed_identity(d.id, "ID-1")
    svc.bind_identity(svc.get_device(d.id))
    svc.set_transport(svc.get_device(d.id), "direct")
    fresh = svc.get_device(d.id)
    assert fresh.transport_mode == "direct"
    assert fresh.agent_id == a               # associacao preservada
    assert fresh.expected_dpn_id == "ID-1"   # identidade preservada
    svc.set_transport(fresh, "agent")
    assert svc.is_agent_transport(svc.get_device(d.id))


def test_agent_mode_without_agent_is_pending_not_direct():
    svc = make_service()
    d = _dev(svc)
    fresh = svc.get_device(d.id)
    fresh.transport_mode = "agent"           # forca modo agente sem agente
    svc.db.set_device_transport(d.id, "agent")
    fresh = svc.get_device(d.id)
    assert svc.identity_state(fresh) == IdentityState.NOT_CONFIGURED
    ok, why = svc.can_mutate(fresh)
    assert ok is False


def test_repeated_observation_is_repersisted():
    svc = make_service()
    d = _dev(svc)
    assert svc.set_observed_identity(d.id, "ID-R", origin="agent:ag1") is True
    at1 = svc.get_device(d.id).last_observed_at
    assert svc.set_observed_identity(d.id, "ID-R", origin="agent:ag1") is True
    fresh = svc.get_device(d.id)
    assert fresh.last_observed_id == "ID-R"
    assert fresh.last_observed_at >= at1


def test_edit_form_preserves_linkage_and_identity():
    """Simula editar pelo formulario (novo Device com so os campos de config):
    NAO pode apagar agent_id, MAC, identidade esperada/observada."""
    svc = make_service()
    d = svc.add_device(Device(nickname="DPN 1", address="34.34.34.34",
                              tls_mode=TLSMode.INSECURE,
                              mac_reference="70:19:88:c1:a2:fe"))
    a = _agent(svc)
    svc.associate_agent(a, d.id)
    svc.finish_agent_setup(d.id, a, "ID-1", bind=True)

    form_dev = Device(nickname="renomeada", address="34.34.34.34",
                      tls_mode=TLSMode.INSECURE, transport_mode="agent",
                      mac_reference="70:19:88:c1:a2:fe")   # como o formulario carrega
    form_dev.id = d.id
    svc.update_device(form_dev)

    fresh = svc.get_device(d.id)
    assert fresh.nickname == "renomeada"
    assert fresh.agent_id == a
    assert fresh.mac_reference == "70:19:88:c1:a2:fe"
    assert fresh.expected_dpn_id == "ID-1"
    assert fresh.last_observed_id == "ID-1"
    assert fresh.transport_mode == "agent"


def test_finish_agent_setup_is_idempotent():
    svc = make_service()
    d = _dev(svc)
    a = _agent(svc)
    svc.finish_agent_setup(d.id, a, "ID-1", bind=True)
    svc.finish_agent_setup(d.id, a, "ID-1", bind=True)
    assert len(svc.list_agents()) == 1
    assert len(svc.list_devices()) == 1


def test_cancel_before_configure_creates_no_device():
    """Adicionar e cancelar antes de Configurar nao cria cadastro."""
    svc = make_service()
    before = len(svc.list_devices())
    # o wizard so cria o cadastro em _on_configure(); cancelar nao cria
    assert len(svc.list_devices()) == before


def test_set_device_serial_persists_distinct_from_atomos():
    svc = make_service()
    d = _dev(svc)
    svc.set_device_serial(d.id, "192.168.1.50:5555")
    fresh = svc.get_device(d.id)
    assert fresh.android_serial == "192.168.1.50:5555"
    assert fresh.address == "34.34.34.34"          # endpoint AtomOS nao muda
    # editar nome preserva o serial
    fresh.nickname = "renomeada"
    svc.update_device(fresh)
    assert svc.get_device(d.id).android_serial == "192.168.1.50:5555"


def test_agent_transport_does_not_require_pc_password():
    svc = make_service()
    d = _dev(svc)
    a = _agent(svc)
    svc.associate_agent(a, d.id)
    fresh = svc.get_device(d.id)
    assert fresh.transport_mode == "agent"
    assert svc.is_agent_transport(fresh) is True
    assert svc.has_password(d.id) is False        # agente usa a senha dele, nao do PC


