"""Regressao: 'Switch active IP' nao deve 'envenenar' o estado da unidade.

Erros de negocio (ex.: ipNotChanged) significam que a unidade RESPONDEU e
continua alcancavel; apenas erros de rede/TLS devem marcar "unreachable".
"""
from dpn.agent.manager import _atomos_status_for_failure


def test_business_error_keeps_unit_reachable():
    # ipNotChanged: a unidade respondeu, so nao havia outro IP para trocar
    assert _atomos_status_for_failure("ipNotChanged") == "connected"


def test_auth_error_maps_to_auth_failed():
    assert _atomos_status_for_failure("wrongPassword") == "auth_failed"
    assert _atomos_status_for_failure("tooManyAttempts") == "auth_failed"


def test_network_error_maps_to_unreachable():
    assert _atomos_status_for_failure("") == "unreachable"
    assert _atomos_status_for_failure("desconhecido") == "unreachable"
