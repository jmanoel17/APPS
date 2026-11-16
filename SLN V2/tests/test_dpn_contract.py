#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2.1b — CONTRACT TEST DeeperConnectProvider ↔ DPNCoordinator (AtomOS mockado).

Prova que o coordinator interpreta corretamente os RotationResult e mantém a política
existente (polling, duplicate_rotations, NODE_EXHAUSTED, isolamento), SEM duplicar a state
machine no provider. Nenhuma unidade real.
"""
import os
import sys
import tempfile
import time
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn import state as S
from dpn.coordinator import DPNCoordinator
from dpn.provider_deeper import DeeperConnectProvider
from dpn.atomos_core.errors import (
    AuthError, UnexpectedResponseError, UncertainError, ConnectionError as AtomConnError,
)


class FakeAtomOS2:
    def __init__(self, tunnels, switch_new_ip=None, switch_exc=None, switch_apply_then=None,
                 refresh_new_ip=None, refresh_exc=None, online=True, mode="none",
                 device_id="dev-1"):
        self.tunnels = [dict(t) for t in tunnels]
        self.switch_new_ip = switch_new_ip
        self.switch_exc = switch_exc
        self.switch_apply_then = switch_apply_then
        self.refresh_new_ip = refresh_new_ip
        self.refresh_exc = refresh_exc
        self.online = online
        self.mode = mode
        self.device_id = device_id
        self.login_calls = 0
        self.closed = False
        self.set_mode_calls = []
        self.switch_calls = []
        self.refresh_calls = []

    def _set_ip(self, ip):
        for t in self.tunnels:
            if t.get("tunnelCode") == "US":
                t["activeIp"] = ip

    def login(self, username=None, password=None):
        self.login_calls += 1

    def check_online(self):
        return self.online

    def get_device_id(self):
        return {"deviceId": self.device_id}

    def get_exit_ip(self):
        return self.tunnels

    def get_node_lb_mode(self):
        return {"mode": self.mode}

    def set_node_lb_mode(self, mode):
        self.set_mode_calls.append(mode)
        self.mode = mode
        return {"success": True}

    def switch_node(self, tunnel_code, current_ip):
        self.switch_calls.append((tunnel_code, current_ip))
        if self.switch_apply_then is not None:      # aplicou e depois deu incerto
            self._set_ip(self.switch_apply_then)
            raise self.switch_exc
        if self.switch_exc is not None:
            raise self.switch_exc
        if self.switch_new_ip is not None:
            self._set_ip(self.switch_new_ip)
            return {"success": True, "activeIp": self.switch_new_ip}
        return {"success": True}

    def refresh_tunnel(self, tunnel_code):
        self.refresh_calls.append(tunnel_code)
        if self.refresh_exc is not None:
            raise self.refresh_exc
        if self.refresh_new_ip is not None:
            self._set_ip(self.refresh_new_ip)
        return {"success": True}

    def close(self):
        self.closed = True


def _provider(fake, tunnel="US", dpn_id="DPN_01"):
    cfg = {"address": "10.0.0.9", "port": 443, "username": "admin", "tls_mode": "insecure",
           "tunnel_code": tunnel, "node_lb_mode": "none", "_dpn_id": dpn_id}
    return DeeperConnectProvider(cfg, client_factory=lambda: fake, password="pw", env={},
                                 log_fn=lambda m: None)


def _coord(sd, prov, dpn="DPN_01", dup=5, rt=0.03, poll=0.01):
    return DPNCoordinator(sd, dpn, prov, limit=20, cooldown_s=3600, dup_before_reset=dup,
                          settle_s=0, rotation_poll_s=poll, rotation_timeout_s=rt,
                          drain_timeout_s=0.05, barrier=None, now_fn=time.time,
                          sleep_fn=time.sleep, log_fn=lambda *a, **k: None)


def _arm(sd, dpn="DPN_01", ip="1.2.3.4", units=20):
    S.set_state(sd, dpn, S.ROTATION_PENDING, current_ip=ip, current_ip_work_units=units,
                node={"node_id": "US", "created_at": 1, "ips_seen": [ip]})


class TestContract(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.sd = os.path.join(self._td.name, "dpn")
        os.makedirs(self.sd, exist_ok=True)

    def tearDown(self):
        self._td.cleanup()

    def _tunnels(self):
        return [{"tunnelCode": "US", "activeIp": "1.2.3.4"}]

    # 1) OK + new_ip
    def test_01_ok_new_ip_converges(self):
        fake = FakeAtomOS2(self._tunnels(), switch_new_ip="9.9.9.9")
        prov = _provider(fake)
        co = _coord(self.sd, prov)
        _arm(self.sd)
        self.assertEqual(co.run(max_steps=10), "ready")
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(st["state"], S.RUNNING)
        self.assertEqual(st["current_ip"], "9.9.9.9")
        self.assertEqual(len(fake.switch_calls), 1)

    # 2) OK sem new_ip -> não assume; polling; timeout -> dup++
    def test_02_ok_without_new_ip_does_not_assume(self):
        fake = FakeAtomOS2(self._tunnels(), switch_new_ip=None)
        prov = _provider(fake)
        co = _coord(self.sd, prov)
        _arm(self.sd)
        co.tick()             # ROTATION_PENDING -> rotate
        co.tick()             # after_rotate -> poll timeout -> dup=1
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertNotIn(st["state"], (S.RUNNING, S.READY, S.ERROR))
        self.assertEqual(st["duplicate_rotations"], 1)
        self.assertEqual(st["current_ip"], "1.2.3.4")
        self.assertEqual(len(fake.switch_calls), 1)   # não re-rotou durante o poll

    # 3a) ipNotChanged -> refresh + IP muda -> sucesso
    def test_03a_ip_not_changed_refresh_success(self):
        fake = FakeAtomOS2(self._tunnels(),
                           switch_exc=UnexpectedResponseError("switchNode: ipNotChanged"),
                           refresh_new_ip="8.8.8.8")
        prov = _provider(fake)
        co = _coord(self.sd, prov)
        _arm(self.sd)
        self.assertEqual(co.run(max_steps=10), "ready")
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(st["state"], S.RUNNING)
        self.assertEqual(st["current_ip"], "8.8.8.8")
        self.assertEqual(fake.refresh_calls, ["US"])
        self.assertEqual(len(fake.switch_calls), 1)

    # 3b) ipNotChanged -> refresh sem convergir -> não ERROR; dup++
    def test_03b_ip_not_changed_refresh_no_change(self):
        fake = FakeAtomOS2(self._tunnels(),
                           switch_exc=UnexpectedResponseError("switchNode: ipNotChanged"),
                           refresh_new_ip=None)
        prov = _provider(fake)
        co = _coord(self.sd, prov)
        _arm(self.sd)
        co.tick()
        co.tick()
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertNotEqual(st["state"], S.ERROR)
        self.assertEqual(st["duplicate_rotations"], 1)

    # 4a) UNCERTAIN sem aplicar -> não reenvia; reconcilia por get_public_ip; dup++
    def test_04a_uncertain_not_applied(self):
        fake = FakeAtomOS2(self._tunnels(), switch_exc=UncertainError("incerto"))
        prov = _provider(fake)
        co = _coord(self.sd, prov)
        _arm(self.sd)
        co.tick()
        co.tick()
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(len(fake.switch_calls), 1)   # NÃO reenviou
        self.assertEqual(st["duplicate_rotations"], 1)
        self.assertNotEqual(st["state"], S.ERROR)

    # 4b) UNCERTAIN mas aplicou -> reconcilia via get_public_ip -> sucesso
    def test_04b_uncertain_applied_reconciled(self):
        fake = FakeAtomOS2(self._tunnels(), switch_exc=UncertainError("incerto"),
                           switch_apply_then="7.7.7.7")
        prov = _provider(fake)
        co = _coord(self.sd, prov)
        _arm(self.sd)
        self.assertEqual(co.run(max_steps=10), "ready")
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(st["state"], S.RUNNING)
        self.assertEqual(st["current_ip"], "7.7.7.7")
        self.assertEqual(len(fake.switch_calls), 1)

    # 5) AUTH_RENEWED -> não reenvia; reconcilia; nunca sucesso automático
    def test_05_auth_renewed_no_auto_success(self):
        fake = FakeAtomOS2(self._tunnels(), switch_exc=AuthError("expirado"))
        prov = _provider(fake)
        co = _coord(self.sd, prov)
        _arm(self.sd)
        co.tick()
        co.tick()
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(len(fake.switch_calls), 1)         # não reenviou
        self.assertNotEqual(st["state"], S.RUNNING)         # não assumiu sucesso
        self.assertGreaterEqual(fake.login_calls, 2)        # relogin aconteceu

    # 6) TunnelNotFound -> ERROR explícito
    def test_06_tunnel_not_found_error(self):
        fake = FakeAtomOS2([{"tunnelCode": "BR", "activeIp": "9.9.9.9"}])  # US ausente
        prov = _provider(fake, tunnel="US")
        co = _coord(self.sd, prov)
        co.tick()   # INIT -> discover -> get_public_ip levanta TunnelNotFound
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(st["state"], S.ERROR)

    # 7) ProviderConnectionError -> ERROR sem duplicar mutação
    def test_07_connection_error_error_no_dup(self):
        fake = FakeAtomOS2(self._tunnels(), switch_exc=AtomConnError("net down"))
        prov = _provider(fake)
        co = _coord(self.sd, prov)
        _arm(self.sd)
        co.tick()   # rotate -> ProviderConnectionError -> ERROR
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(st["state"], S.ERROR)
        self.assertEqual(len(fake.switch_calls), 1)

    # 8) Duas DPNs isoladas
    def test_08_two_dpns_isolated(self):
        fakeA = FakeAtomOS2(self._tunnels(), switch_exc=UncertainError("incerto"))  # não muda
        fakeB = FakeAtomOS2(self._tunnels(), switch_new_ip="5.5.5.5")               # muda
        coA = _coord(self.sd, _provider(fakeA, dpn_id="DPN_01"), dpn="DPN_01")
        coB = _coord(self.sd, _provider(fakeB, dpn_id="DPN_02"), dpn="DPN_02")
        _arm(self.sd, "DPN_01")
        _arm(self.sd, "DPN_02")
        coA.tick()
        coA.tick()
        coB.run(max_steps=10)
        a = S.read_dpn_state(self.sd, "DPN_01")
        b = S.read_dpn_state(self.sd, "DPN_02")
        self.assertNotEqual(a["state"], S.RUNNING)
        self.assertNotEqual(a["state"], S.ERROR)
        self.assertEqual(b["state"], S.RUNNING)
        self.assertEqual(b["current_ip"], "5.5.5.5")

    # 9) Não re-rotar enquanto aguarda refresh
    def test_09_no_rereotate_while_waiting_refresh(self):
        fake = FakeAtomOS2(self._tunnels(),
                           switch_exc=UnexpectedResponseError("switchNode: ipNotChanged"),
                           refresh_new_ip=None)
        prov = _provider(fake)
        co = _coord(self.sd, prov)
        _arm(self.sd)
        co.tick()   # rotate: switch + refresh; ROTATING
        self.assertEqual(len(fake.switch_calls), 1)
        self.assertEqual(fake.refresh_calls, ["US"])
        co.tick()   # after_rotate: poll (aguardando refresh) -> timeout -> dup
        self.assertEqual(len(fake.switch_calls), 1)   # NÃO re-chamou rotate no poll
        co.tick()   # ROTATION_PENDING -> rotate de novo (só após o wait)
        self.assertEqual(len(fake.switch_calls), 2)

    # 10) duplicate_rotations não é contornado por RotationResult -> NODE_EXHAUSTED
    def test_10_duplicate_rotation_leads_to_node_exhausted(self):
        fake = FakeAtomOS2(self._tunnels(),
                           switch_exc=UnexpectedResponseError("switchNode: ipNotChanged"),
                           refresh_new_ip=None)
        prov = _provider(fake)
        co = _coord(self.sd, prov, dup=3)
        _arm(self.sd)
        for _ in range(20):
            co.tick()
            st = S.read_dpn_state(self.sd, "DPN_01")
            if st["state"] == S.NODE_EXHAUSTED:
                break
        st = S.read_dpn_state(self.sd, "DPN_01")
        self.assertEqual(st["state"], S.NODE_EXHAUSTED)
        self.assertGreaterEqual(int(st["duplicate_rotations"]), 3)


if __name__ == "__main__":
    unittest.main()
