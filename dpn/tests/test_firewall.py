"""Testes da liberacao de firewall (sem elevar de fato)."""
from dpn.agent import firewall as fw


def test_is_admin_and_rule_exists_return_bool():
    assert isinstance(fw.is_admin(), bool)
    assert isinstance(fw.rule_exists(), bool)


def test_ensure_uses_existing_rule(monkeypatch):
    monkeypatch.setattr(fw, "rule_exists", lambda name=fw.RULE_NAME: True)
    ok, msg = fw.ensure_firewall_rule(8765)
    assert ok is True
    assert "já existe" in msg


def test_ensure_without_admin_and_no_elevate(monkeypatch):
    monkeypatch.setattr(fw, "rule_exists", lambda name=fw.RULE_NAME: False)
    monkeypatch.setattr(fw, "is_admin", lambda: False)
    ok, msg = fw.ensure_firewall_rule(8765, elevate=False)
    assert ok is False
    assert "administrador" in msg


def test_add_rule_cmd_scope_and_port():
    cmd = fw._add_rule_cmd(8765, "LocalSubnet")
    assert "8765" in cmd
    assert "LocalSubnet" in cmd
    assert "Inbound" in cmd and "Allow" in cmd
