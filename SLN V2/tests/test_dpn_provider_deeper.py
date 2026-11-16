#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa E2.1 — DeeperConnectProvider REAL com AtomOSClient MOCKADO (sem unidade real)."""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn.atomos_core.errors import AuthError, UnexpectedResponseError, UncertainError
from dpn.provider_deeper import (
    DeeperConnectProvider, ProviderNotConfigured, ProviderMissingPassword, TunnelNotFound,
    ProviderInvalidIP, ProviderNotImplemented, RotationStatus,
)


def _cfg(**over):
    c = {"address": "10.0.0.9", "port": 443, "username": "admin", "tls_mode": "insecure",
         "tunnel_code": "US", "node_lb_mode": "none", "request_timeout_s": 10,
         "connect_timeout_s": 5, "_dpn_id": "DPN_01"}
    c.update(over)
    return c


class FakeAtomOS:
    def __init__(self, tunnels=None, mode="none", device_id="dev-1", online=True,
                 switch_result=None, switch_exc=None, refresh_result=None, refresh_exc=None,
                 auth_error_reads=0):
        self.tunnels = tunnels if tunnels is not None else [{"tunnelCode": "US", "activeIp": "1.2.3.4"}]
        self.mode = mode
        self.device_id = device_id
        self.online = online
        self.switch_result = switch_result
        self.switch_exc = switch_exc
        self.refresh_result = refresh_result
        self.refresh_exc = refresh_exc
        self._auth_left = int(auth_error_reads)
        self.login_calls = 0
        self.closed = False
        self.set_mode_calls = []
        self.switch_calls = []
        self.refresh_calls = []
        self.get_exit_calls = 0

    def login(self, username=None, password=None):
        self.login_calls += 1

    def check_online(self):
        return self.online

    def get_device_id(self):
        return {"deviceId": self.device_id}

    def get_exit_ip(self):
        self.get_exit_calls += 1
        if self._auth_left > 0:
            self._auth_left -= 1
            raise AuthError("sessao expirada")
        return self.tunnels

    def get_node_lb_mode(self):
        return {"mode": self.mode}

    def set_node_lb_mode(self, mode):
        self.set_mode_calls.append(mode)
        self.mode = mode
        return {"success": True}

    def switch_node(self, tunnel_code, current_ip):
        self.switch_calls.append((tunnel_code, current_ip))
        if self.switch_exc is not None:
            raise self.switch_exc
        return self.switch_result if self.switch_result is not None else {"success": True}

    def refresh_tunnel(self, tunnel_code):
        self.refresh_calls.append(tunnel_code)
        if self.refresh_exc is not None:
            raise self.refresh_exc
        return self.refresh_result if self.refresh_result is not None else {"success": True}

    def close(self):
        self.closed = True


def _provider(fake, **cfg_over):
    return DeeperConnectProvider(_cfg(**cfg_over), client_factory=lambda: fake,
                                 password="pw", env={}, log_fn=lambda m: None)


class TestDeeperProvider(unittest.TestCase):
    def test_01_login_lazy(self):
        fake = FakeAtomOS()
        p = _provider(fake)
        self.assertEqual(fake.login_calls, 0)
        self.assertEqual(p.get_public_ip(), "1.2.3.4")
        self.assertEqual(fake.login_calls, 1)

    def test_02_missing_password(self):
        fake = FakeAtomOS()
        p = DeeperConnectProvider(_cfg(), client_factory=lambda: fake, password=None,
                                  env={}, log_fn=lambda m: None)
        with self.assertRaises(ProviderMissingPassword):
            p.get_public_ip()
        self.assertEqual(fake.login_calls, 0)

    def test_03_tunnel_found(self):
        fake = FakeAtomOS(tunnels=[{"tunnelCode": "US", "activeIp": "8.8.8.8"}])
        self.assertEqual(_provider(fake).get_public_ip(), "8.8.8.8")

    def test_04_tunnel_missing(self):
        fake = FakeAtomOS(tunnels=[{"tunnelCode": "BR", "activeIp": "9.9.9.9"}])
        with self.assertRaises(TunnelNotFound) as cm:
            _provider(fake).get_public_ip()
        self.assertEqual(getattr(cm.exception, "code", ""), "TUNNEL_NOT_FOUND")

    def test_05_list(self):
        fake = FakeAtomOS(tunnels=[{"tunnelCode": "US", "activeIp": "1.1.1.1"}])
        self.assertEqual(_provider(fake).get_public_ip(), "1.1.1.1")

    def test_06_dict_list(self):
        fake = FakeAtomOS(tunnels={"list": [{"tunnelCode": "US", "activeIp": "2.2.2.2"}]})
        self.assertEqual(_provider(fake).get_public_ip(), "2.2.2.2")

    def test_07_dict_tunnels(self):
        fake = FakeAtomOS(tunnels={"tunnels": [{"tunnelCode": "US", "activeIp": "3.3.3.3"}]})
        self.assertEqual(_provider(fake).get_public_ip(), "3.3.3.3")

    def test_08_active_ip_invalid(self):
        fake = FakeAtomOS(tunnels=[{"tunnelCode": "US", "activeIp": "999.1.1.1"}])
        with self.assertRaises(ProviderInvalidIP):
            _provider(fake).get_public_ip()

    def test_09_node_id_is_tunnel_code(self):
        fake = FakeAtomOS(tunnels=[{"tunnelCode": "US", "activeIp": "1.2.3.4"}])
        self.assertEqual(_provider(fake).get_node_id(), "US")

    def test_10_device_id_metadata(self):
        fake = FakeAtomOS(device_id="PHYS-123")
        self.assertEqual(_provider(fake).device_metadata()["physical_device_id"], "PHYS-123")

    def test_11_mode_already_none(self):
        fake = FakeAtomOS(mode="none")
        p = _provider(fake)
        r = p.rotate_ip()
        self.assertEqual(fake.set_mode_calls, [])
        self.assertEqual(len(fake.switch_calls), 1)
        self.assertEqual(r.status, RotationStatus.OK)

    def test_12_mode_not_none_set_none(self):
        fake = FakeAtomOS(mode="random")
        p = _provider(fake)
        p.rotate_ip()
        self.assertEqual(fake.set_mode_calls, ["none"])
        self.assertEqual(fake.mode, "none")
        self.assertEqual(len(fake.switch_calls), 1)

    def test_13_switch_returns_new_ip(self):
        fake = FakeAtomOS(switch_result={"success": True, "activeIp": "5.6.7.8"})
        r = _provider(fake).rotate_ip()
        self.assertEqual(r.status, RotationStatus.OK)
        self.assertEqual(r.new_ip, "5.6.7.8")

    def test_14_ip_not_changed_triggers_refresh(self):
        fake = FakeAtomOS(switch_exc=UnexpectedResponseError("switchNode: ipNotChanged"))
        r = _provider(fake).rotate_ip()
        self.assertEqual(r.status, RotationStatus.REFRESH_REQUESTED)
        self.assertEqual(fake.refresh_calls, ["US"])
        self.assertEqual(len(fake.switch_calls), 1)

    def test_15_uncertain_no_resend(self):
        fake = FakeAtomOS(switch_exc=UncertainError("resultado incerto"))
        r = _provider(fake).rotate_ip()
        self.assertEqual(r.status, RotationStatus.UNCERTAIN)
        self.assertEqual(len(fake.switch_calls), 1)   # NÃO reenviou
        self.assertEqual(fake.refresh_calls, [])

    def test_16_auth_error_read_relogin_once(self):
        fake = FakeAtomOS(auth_error_reads=1)
        p = _provider(fake)
        self.assertEqual(p.get_public_ip(), "1.2.3.4")
        self.assertEqual(fake.login_calls, 2)         # login inicial + relogin (1x)
        self.assertEqual(fake.get_exit_calls, 2)      # leitura + retry

    def test_17_health(self):
        self.assertTrue(_provider(FakeAtomOS(online=True)).health())
        self.assertFalse(_provider(FakeAtomOS(online=False)).health())

    def test_18_close(self):
        fake = FakeAtomOS()
        p = _provider(fake)
        p.get_public_ip()          # abre sessão
        p.close()
        self.assertTrue(fake.closed)
        self.assertIsNone(p._client)

    def test_19_reset_node_not_implemented(self):
        with self.assertRaises(ProviderNotImplemented) as cm:
            _provider(FakeAtomOS()).reset_node()
        self.assertEqual(str(cm.exception), "NO_NODE_RESET_ENDPOINT")

    def test_20_two_providers_isolated(self):
        fa, fb = FakeAtomOS(device_id="A"), FakeAtomOS(device_id="B")
        pa, pb = _provider(fa), _provider(fb)
        pa.get_public_ip()
        self.assertEqual(fa.login_calls, 1)
        self.assertEqual(fb.login_calls, 0)
        pb.get_public_ip()
        self.assertEqual(fb.login_calls, 1)


if __name__ == "__main__":
    unittest.main()
