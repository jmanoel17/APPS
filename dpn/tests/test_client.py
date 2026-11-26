"""Testes do AtomOSClient com respostas simuladas (httpx.MockTransport)."""
import base64
import json

import httpx
import pytest

from dpn.atomos.client import AtomOSClient
from dpn.atomos.endpoints import EndpointRegistry
from dpn.atomos.errors import (
    AuthError,
    ConnectionError,
    UnexpectedResponseError,
)
from dpn.models import Device, TLSMode


def make_device(address="192.168.1.50", tls_mode=TLSMode.VERIFY) -> Device:
    return Device(nickname="dpn-a", address=address, tls_mode=tls_mode)


def make_client(device, handler, password="secret"):
    transport = httpx.MockTransport(handler)
    registry = EndpointRegistry(None)
    return AtomOSClient(
        device=device,
        password_provider=lambda: password,
        registry=registry,
        transport=transport,
    )


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


def test_login_encrypts_password_not_plaintext():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = _body(request)
        assert request.method == "POST"
        assert request.url.path == "/api/admin/login"
        return httpx.Response(200, json={"success": True, "token": "tok"})

    client = make_client(make_device(), handler)
    result = client.login()
    assert result["success"] is True
    body = captured["body"]
    assert body["username"] == "admin"
    # senha NAO pode ir em claro
    assert body["password"] != "secret"
    # deve ser base64 decodificavel (cifra RSA)
    raw = base64.b64decode(body["password"])
    assert len(raw) == 256  # RSA-2048 => bloco de 256 bytes


def test_login_success_false_raises_auth_error():
    def handler(request):
        return httpx.Response(200, json={"success": False, "reason": "wrongField"})

    client = make_client(make_device(), handler)
    with pytest.raises(AuthError):
        client.login()


def test_login_http_401():
    def handler(request):
        return httpx.Response(401, json={"reason": "invalid"})

    client = make_client(make_device(), handler)
    with pytest.raises(AuthError):
        client.login()


def test_unexpected_status():
    def handler(request):
        return httpx.Response(500, text="boom")

    client = make_client(make_device(), handler)
    with pytest.raises(UnexpectedResponseError):
        client.login()


def test_network_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    client = make_client(make_device(), handler)
    with pytest.raises(ConnectionError):
        client.login()


def test_redirect_rejected():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://evil.example/"})

    client = make_client(make_device(), handler)
    with pytest.raises(UnexpectedResponseError):
        client.login()


def test_sessions_isolated_between_devices():
    a = make_device(address="192.168.1.50")
    b = make_device(address="192.168.1.51")

    def handler_a(request):
        return httpx.Response(200, json={"success": True, "token": "token-A"})

    def handler_b(request):
        return httpx.Response(200, json={"success": True, "token": "token-B"})

    ca = make_client(a, handler_a)
    cb = make_client(b, handler_b)
    ca.login()
    cb.login()

    assert ca._token == "token-A"
    assert cb._token == "token-B"
    assert ca.base_url != cb.base_url


def test_set_node_type_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = _body(request)
        return httpx.Response(200, json={"success": True})

    client = make_client(make_device(), handler)
    client.set_node_type("full", tunnel_code="US-NY", kill_switch=True)
    assert captured["path"] == "/api/smartRoute/setDpnMode"
    assert captured["body"] == {"dpnMode": "full", "tunnelCode": "US-NY",
                                "killSwitch": True}


def test_switch_node_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = _body(request)
        return httpx.Response(200, json={"success": True})

    client = make_client(make_device(), handler)
    client.switch_node("US-NY", "1.2.3.4")
    assert captured["path"] == "/api/smartRoute/switchNode"
    assert captured["body"] == {"tunnelCode": "US-NY", "currentIp": "1.2.3.4"}


def test_node_lb_mode_and_add_tunnel_payloads():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, _body(request)))
        return httpx.Response(200, json={"success": True})

    client = make_client(make_device(), handler)
    client.set_node_lb_mode("domain")
    client.add_tunnel("AMN", "US")
    assert ("/api/smartRoute/nodeLbMode", {"mode": "domain"}) in captured
    assert ("/api/smartRoute/addTunnel",
            {"regionCode": "AMN", "countryCode": "US"}) in captured


def test_operation_requires_login_on_same_session():
    """Sem login na MESMA sessao, o servidor responde 401; apos login, 200."""
    state = {"logged_in": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/admin/login":
            state["logged_in"] = True
            return httpx.Response(200, json={"success": True})
        if not state["logged_in"]:
            return httpx.Response(401, json={"reason": "unauthorized"})
        return httpx.Response(200, json={"online": [], "offline": []})

    client = make_client(make_device(), handler)
    with pytest.raises(AuthError):
        client.list_devices()          # sem login -> 401
    client.login()
    assert client.list_devices() == {"online": [], "offline": []}


def test_access_control_payloads():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, _body(request)))
        return httpx.Response(200, json={"success": True})

    client = make_client(make_device(), handler)
    client.set_access_control_switch(True)
    client.set_access_control_item({"mac": "aa:bb", "routeMode": "full",
                                    "regionCode": "BR"})
    client.delete_access_control_devices(["aa:bb"])
    assert ("/api/accessControl/switch", {"value": True}) in captured
    assert ("/api/accessControl/setOne",
            {"mac": "aa:bb", "routeMode": "full", "regionCode": "BR"}) in captured
    assert ("/api/accessControl/delete", {"macList": ["aa:bb"]}) in captured


def test_success_false_on_200_is_not_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "reason": "noSuchTunnel"})

    client = make_client(make_device(), handler)
    with pytest.raises(UnexpectedResponseError):
        client.set_node_type("full", "X")


def test_all_operations_are_confirmed():
    reg = EndpointRegistry(None)
    for key in ("device_list", "node_type_get", "node_type_set", "exit_ip_get",
                "exit_ip_set", "reboot", "refresh_tunnel", "node_lb_mode_get",
                "node_lb_mode_set", "add_tunnel", "access_switch_get",
                "access_switch_set", "access_set_one", "access_delete"):
        assert reg.get(key).confirmed is True
        assert reg.get(key).path is not None
