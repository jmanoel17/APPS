"""Testes do registro de endpoints (confirmados)."""
import json
import tempfile
import os

from dpn.atomos.endpoints import EndpointRegistry


def test_login_is_confirmed():
    reg = EndpointRegistry(None)
    assert reg.get("login").confirmed is True
    assert reg.get("login").path == "/api/admin/login"


def test_operations_confirmed_paths():
    reg = EndpointRegistry(None)
    assert reg.get("device_list").path == "/api/accessControl/list"
    assert reg.get("node_type_get").path == "/api/smartRoute/getDpnMode"
    assert reg.get("node_type_set").path == "/api/smartRoute/setDpnMode"
    assert reg.get("exit_ip_get").path == "/api/smartRoute/listTunnels"
    assert reg.get("exit_ip_set").path == "/api/smartRoute/switchNode"
    assert reg.get("node_lb_mode_get").path == "/api/smartRoute/nodeLbMode"
    assert reg.get("node_lb_mode_set").path == "/api/smartRoute/nodeLbMode"
    assert reg.get("add_tunnel").path == "/api/smartRoute/addTunnel"
    assert reg.get("access_switch_get").path == "/api/accessControl/switch"
    assert reg.get("access_set_one").path == "/api/accessControl/setOne"
    assert reg.get("reboot").path == "/api/admin/reboot"


def test_confirm_and_save_load_overrides():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "endpoints.json")
        reg = EndpointRegistry(path)
        reg.confirm("device_list", "GET", "/api/accessControl/list", False)
        reg.save(path)

        reg2 = EndpointRegistry(path)
        ep = reg2.get("device_list")
        assert ep.confirmed is True
        assert ep.path == "/api/accessControl/list"
