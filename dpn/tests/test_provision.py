"""Testes de provisionamento por ADB, QR, pareamento e troca de transporte."""
import json
import tempfile

import pytest

from dpn.agent.provision import (
    android_ip_from_serial,
    assert_no_secrets,
    build_install_args,
    build_provision_args,
    build_reverse_args,
    ensure_adb_reverse,
    rank_controller_addresses,
)
from dpn.agent.qr import qr_payload
from dpn.agent.registry import PairingManager
from dpn.config import Config
from dpn.models import Device, TLSMode
from dpn.service import DPNService


def make_service():
    cfg = Config()
    cfg.data_dir = tempfile.mkdtemp()
    cfg.resolve()
    return DPNService(cfg)


def test_install_args_explicit_serial_and_preserve_data():
    args = build_install_args("192.168.1.50:5555", "app.apk")
    assert args[:2] == ["-s", "192.168.1.50:5555"]
    assert "-r" in args                      # atualiza preservando dados/agent_id
    with pytest.raises(ValueError):
        build_install_args("", "app.apk")    # exige selecao explicita


def test_provision_args_have_no_secrets():
    args = build_provision_args("serial1", "wss://192.168.1.3:8765", "fpfp",
                                "123456", "https://34.34.34.34")
    joined = " ".join(args)
    assert "wss://192.168.1.3:8765" in joined
    assert "fpfp" in joined and "123456" in joined
    assert "https://34.34.34.34" in joined
    low = joined.lower()
    assert "password" not in low and "senha" not in low and "token" not in low
    with pytest.raises(ValueError):
        assert_no_secrets(["--es", "password", "secreto"])


def test_qr_payload_has_no_password():
    payload = qr_payload("wss://192.168.1.3:8765", "fp", "654321")
    d = json.loads(payload)
    assert d == {"v": 1, "url": "wss://192.168.1.3:8765", "fp": "fp", "code": "654321"}
    assert "password" not in payload.lower()


def test_rank_controller_addresses_prefers_same_subnet():
    ips = ["10.0.0.5", "192.168.1.3", "172.16.0.9"]
    ranked = rank_controller_addresses("192.168.1.50", ips)
    assert ranked[0] == "192.168.1.3"
    assert set(ranked) == set(ips)


def test_android_ip_from_serial():
    assert android_ip_from_serial("192.168.1.50:5555") == "192.168.1.50"
    assert android_ip_from_serial("emulator-5554") is None


def test_build_reverse_args_and_no_serial():
    args = build_reverse_args("192.168.1.50:5555", 8765)
    assert args == ["-s", "192.168.1.50:5555", "reverse",
                    "tcp:8765", "tcp:8765"]
    with pytest.raises(ValueError):
        build_reverse_args("", 8765)


def test_ensure_adb_reverse(monkeypatch):
    calls = []

    def fake_run_adb(args, timeout=180):
        calls.append((args, timeout))
        return 0, "8765", ""

    monkeypatch.setattr("dpn.agent.provision.run_adb", fake_run_adb)
    ok, msg = ensure_adb_reverse("192.168.1.50:5555", 8765)
    assert ok is True
    assert calls and calls[0][0][:2] == ["-s", "192.168.1.50:5555"]

    ok, msg = ensure_adb_reverse("", 8765)      # sem serial: nao chama adb
    assert ok is False
    assert len(calls) == 1


def test_pairing_single_use_and_expiry():
    pm = PairingManager(ttl=300)
    code = pm.open()
    assert pm.is_valid(code)
    assert pm.consume(code) is True
    assert pm.is_consumed(code)
    assert pm.consume(code) is False            # reutilizacao recusada


def test_expired_pairing_rejected():
    pm = PairingManager(ttl=-1)
    code = pm.open()
    assert not pm.is_valid(code)
    assert pm.consume(code) is False


def test_transport_switch_preserves_configuration():
    svc = make_service()
    dev = svc.add_device(Device(nickname="DPN 1", address="34.34.34.34",
                                tls_mode=TLSMode.INSECURE,
                                mac_reference="70:19:88:c1:a2:fe"))
    svc.associate_agent("ag1", dev.id)
    svc.set_expected_identity(dev.id, "ID-1")
    svc.set_observed_identity(dev.id, "ID-1")

    d = svc.get_device(dev.id)
    assert d.transport_mode == "agent"
    assert d.agent_id == "ag1"

    svc.set_transport(d, "direct")
    d = svc.get_device(dev.id)
    assert d.transport_mode == "direct"
    assert d.agent_id == "ag1"                 # associacao preservada
    assert d.expected_dpn_id == "ID-1"         # identidade preservada
    assert not svc.is_agent_transport(d)

    svc.set_transport(d, "agent")
    d = svc.get_device(dev.id)
    assert svc.is_agent_transport(d)


def test_set_transport_agent_requires_agent():
    svc = make_service()
    dev = svc.add_device(Device(nickname="x", address="34.34.34.34"))
    with pytest.raises(ValueError):
        svc.set_transport(dev, "agent")


def test_validate_direct_identity():
    svc = make_service()
    dev = svc.add_device(Device(nickname="x", address="34.34.34.34",
                                tls_mode=TLSMode.INSECURE))

    class FakeClient:
        def get_device_id(self):
            return {"deviceId": "ID-DIRETO"}

    assert svc.validate_direct_identity(FakeClient(), dev) == "ID-DIRETO"
    assert svc.get_device(dev.id).last_observed_id == "ID-DIRETO"
