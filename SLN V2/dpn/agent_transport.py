#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/agent_transport.py — transporte AGENT por OPS tipadas (reutiliza o APK existente).

Camadas:
  AgentRegistrySource / DPNControlSQLiteRegistry  (read-only: agent_id, token_hash,
      android_serial, expected_dpn_id, name, last_seen)
  AgentHub    (Qt-free; sobe AgentServer WSS, mantém conexões por agent_id, envia comandos,
               aguarda Result por request_id, online/offline, mantém adb reverse, shutdown)
  AgentAtomOS (AtomOSOps sobre AgentHub; mapeia métodos -> Ops; valida request/agent/identidade;
               traduz status/reason para erros tipados; NÃO tem state machine DPN)

Segurança: não imprime token/token_hash/chaves/senha. A senha AtomOS fica no Android Keystore
e os cookies no AtomOsClient do agente; o host não precisa de senha em transport=agent.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import sqlite3
import threading
import time
import uuid

from .atomos_ops import (
    AtomOSOps, AtomOSOpsError, AgentOfflineError, AgentTimeout, AgentServerPortInUse,
    AgentHubBusy, AgentNotStarted, AgentResponseMismatch, AtomOSTargetUnreachable,
    TLSIdentityError,
)
from .atomos_core.errors import AuthError, UncertainError, UnexpectedResponseError, IdentityMismatchError
from .locks import InterProcessFileLock

PROTOCOL_VERSION = "dpn-agent/1"


# ---------------------------------------------------------------- registry (read-only)
@dataclasses.dataclass
class AgentRecordView:
    agent_id: str
    token_hash: str = ""
    android_serial: str = ""
    expected_dpn_id: str = ""
    name: str = ""
    last_seen: float = 0.0


class AgentRegistrySource:
    def get(self, agent_id):
        raise NotImplementedError

    def authenticate(self, agent_id, token):
        rec = self.get(agent_id)
        if not rec or not rec.token_hash:
            return False
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest() == rec.token_hash

    def serials(self):
        return []


class DPNControlSQLiteRegistry(AgentRegistrySource):
    """Somente leitura de %LOCALAPPDATA%\\dpn\\dpn.db (nunca escreve; nunca lê senha)."""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "dpn", "dpn.db")

    def _connect(self):
        p = str(self.db_path).replace("\\", "/")
        return sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=3)

    def get(self, agent_id):
        try:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT a.agent_id, a.token_hash, a.name, a.last_seen, a.observed_dpn_id,"
                    " d.android_serial, d.expected_dpn_id"
                    " FROM agents a LEFT JOIN devices d ON a.device_id = d.id"
                    " WHERE a.agent_id = ?", (agent_id,)).fetchone()
            finally:
                con.close()
            if not row:
                return None
            return AgentRecordView(agent_id=row[0], token_hash=row[1] or "", name=row[2] or "",
                                   last_seen=float(row[3] or 0),
                                   expected_dpn_id=(row[6] or row[4] or ""),
                                   android_serial=row[5] or "")
        except Exception:
            return None

    def serials(self):
        try:
            con = self._connect()
            try:
                rows = con.execute(
                    "SELECT android_serial FROM devices WHERE android_serial IS NOT NULL"
                    " AND android_serial <> ''").fetchall()
            finally:
                con.close()
            return [r[0] for r in rows if r[0]]
        except Exception:
            return []


# ---------------------------------------------------------------- command (duck-type)
@dataclasses.dataclass
class _Command:
    op: str
    agent_id: str
    expected_dpn_id: str = None
    params: dict = dataclasses.field(default_factory=dict)
    request_id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex)
    deadline: float = 0.0
    protocol: str = PROTOCOL_VERSION
    mutating: bool = False

    def __post_init__(self):
        if not self.deadline:
            self.deadline = time.time() + 20.0

    def is_expired(self, now=None):
        return (now or time.time()) > self.deadline

    def to_dict(self):
        return {"protocol": self.protocol, "type": "command", "request_id": self.request_id,
                "agent_id": self.agent_id, "expected_dpn_id": self.expected_dpn_id,
                "op": self.op, "params": self.params, "deadline": self.deadline}


# ---------------------------------------------------------------- AgentHub
class AgentHub:
    """Autoridade WSS por host. Qt-free. Injetável para testes (server_factory/reverse_fn)."""

    def __init__(self, registry=None, state_dir="state/dpn", host="0.0.0.0", port=8765,
                 cert_path=None, key_path=None, server_factory=None, reverse_fn=None,
                 reverse_serials_fn=None, reverse_interval_s=15.0, log_fn=None,
                 now_fn=time.time):
        self.registry = registry
        self.state_dir = state_dir
        self.host = host
        self.port = int(port)
        self.cert_path = cert_path
        self.key_path = key_path
        self._server_factory = server_factory
        self._reverse_fn = reverse_fn
        self._reverse_serials_fn = reverse_serials_fn
        self.reverse_interval_s = float(reverse_interval_s)
        self._log = log_fn or (lambda m: None)
        self.now_fn = now_fn
        self._server = None
        self._lock = None
        self._reverse_stop = threading.Event()
        self._reverse_thread = None
        self._ops = []
        self._ops_lock = threading.Lock()

    # ---- factory default (lazy: só importa websockets/cryptography quando real) ----
    def _default_server_factory(self):
        def build():
            from .agent_core.server import AgentServer
            if not (self.cert_path and self.key_path):
                raise AgentNotStarted("cert/key do controlador não informados")
            return AgentServer(self.host, self.port, self.cert_path, self.key_path,
                               self._authenticate, self._on_pair, self._on_event)
        return build

    def _authenticate(self, agent_id, token):
        return bool(self.registry and self.registry.authenticate(agent_id, token))

    def _on_pair(self, agent_id, code, info):
        return None  # não re-parear nesta etapa

    def _on_event(self, event, data):
        pass

    # ---- ciclo de vida ----
    def start(self):
        os.makedirs(self.state_dir, exist_ok=True)
        lk = InterProcessFileLock(os.path.join(self.state_dir, "agent_hub.lock"), timeout=0.5)
        if not lk.acquire():
            raise AgentHubBusy("AgentHub já ativo neste host")
        self._lock = lk
        try:
            factory = self._server_factory or self._default_server_factory()
            self._server = factory() if callable(factory) else factory
            ok = self._server.start()
            if not ok:
                err = str(getattr(self._server, "start_error", "") or "")
                self._server = None
                raise AgentServerPortInUse(err or ("porta %d em uso" % self.port))
            self._start_reverse()
            return True
        except Exception:
            self._cleanup()
            raise

    def stop(self):
        try:
            self._reverse_stop.set()
            t = self._reverse_thread
            self._reverse_thread = None
            if t is not None:
                t.join(timeout=2.0)
            srv = self._server
            self._server = None
            if srv is not None:
                try:
                    srv.stop()
                except Exception:
                    pass
        finally:
            self._cleanup()

    def _cleanup(self):
        if self._lock is not None:
            try:
                self._lock.release()
            except Exception:
                pass
            self._lock = None

    # ---- adb reverse scheduler ----
    def _start_reverse(self):
        if self._reverse_thread is not None:
            return
        self._reverse_stop = threading.Event()
        t = threading.Thread(target=self._reverse_loop, daemon=True, name="agent-reverse")
        self._reverse_thread = t
        t.start()

    def _reverse_loop(self):
        while not self._reverse_stop.wait(self.reverse_interval_s):
            self.reverse_once()

    def reverse_once(self):
        try:
            serials = self._reverse_serials_fn() if self._reverse_serials_fn else (
                self.registry.serials() if self.registry else [])
        except Exception:
            serials = []
        fn = self._reverse_fn
        if fn is None:
            def fn(serial, port):
                from .agent_core.provision import ensure_adb_reverse
                return ensure_adb_reverse(serial, port)
        for serial in serials:
            try:
                fn(serial, self.port)
            except Exception:
                pass

    # ---- comandos ----
    def online(self, agent_id):
        return bool(self._server is not None and self._server.is_online(agent_id))

    def send(self, agent_id, op, params=None, expected_dpn_id=None, timeout=20.0):
        if self._server is None:
            raise AgentNotStarted("AgentHub não iniciado")
        try:
            with self._ops_lock:
                self._ops.append(op)
                if len(self._ops) > 5000:
                    del self._ops[:1000]
        except Exception:
            pass
        cmd = _Command(op=op, agent_id=agent_id, expected_dpn_id=expected_dpn_id,
                       params=params or {}, deadline=time.time() + float(timeout))
        try:
            return self._server.send_command_blocking(cmd, float(timeout))
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            msg = str(e) or name
            if isinstance(e, TimeoutError) or "timeout" in msg.lower() or name == "TimeoutError":
                raise AgentTimeout(msg)
            raise AgentOfflineError(msg)

    def ops_snapshot(self):
        try:
            with self._ops_lock:
                return list(self._ops)
        except Exception:
            return []

    def ops_counts(self):
        from collections import Counter
        try:
            with self._ops_lock:
                return Counter(self._ops)
        except Exception:
            return {}


# ---------------------------------------------------------------- AgentAtomOS
class AgentAtomOS(AtomOSOps):
    """AtomOSOps sobre AgentHub. NÃO tem state machine; só opera/transporta."""

    def __init__(self, hub, agent_id, expected_dpn_id=None, timeout=20.0, log_fn=None):
        if not agent_id:
            raise AgentOfflineError("agent_id ausente")
        self._hub = hub
        self.agent_id = str(agent_id)
        self.expected_dpn_id = expected_dpn_id
        self.timeout = float(timeout)
        self._log = log_fn or (lambda m: None)

    def _send(self, op, params=None):
        hub = self._hub
        if hub is None:
            raise AgentNotStarted("AgentAtomOS sem hub")
        if not hub.online(self.agent_id):
            raise AgentOfflineError("agente %s offline" % self.agent_id)
        try:
            r = hub.send(self.agent_id, op, params or {},
                         expected_dpn_id=self.expected_dpn_id, timeout=self.timeout)
        except (AgentOfflineError, AgentTimeout, AgentNotStarted, AgentHubBusy):
            raise
        except Exception as e:  # noqa: BLE001
            raise AgentOfflineError(str(e) or type(e).__name__)

        rid = getattr(r, "request_id", None)
        aid = getattr(r, "agent_id", None)
        if aid and str(aid) != self.agent_id:
            raise AgentResponseMismatch(
                "resposta do agente %r para comando de %r (request_id=%s)"
                % (aid, self.agent_id, rid))
        obs = getattr(r, "observed_dpn_id", None)
        if self.expected_dpn_id and obs and str(obs) != str(self.expected_dpn_id):
            raise IdentityMismatchError(
                "ID físico divergente (%s != %s)" % (obs, self.expected_dpn_id))

        status = str(getattr(r, "status", "") or "")
        if status == "success":
            return getattr(r, "data", None)
        if status == "uncertain":
            raise UncertainError(getattr(r, "error", "") or "uncertain")
        if status == "expired":
            raise AgentTimeout(getattr(r, "error", "") or "comando expirado")

        reason = str(getattr(r, "reason", "") or "")
        err = str(getattr(r, "error", "") or "")
        low = (reason + " " + err).lower()
        if reason in ("auth_failed", "unauthorized") or "auth" in low or "autorizado" in low \
                or "wrongpassword" in low:
            raise AuthError(err or reason or "falha de autenticação")
        if reason == "identity_mismatch" or "identidade" in low:
            raise IdentityMismatchError(err or reason)
        if reason == "tls_pin" or "tls" in low:
            raise TLSIdentityError(err or reason)
        if reason == "http":
            raise AtomOSTargetUnreachable(err or reason)
        if "ipnotchanged" in low:
            raise UnexpectedResponseError("switchNode: ipNotChanged")
        raise UnexpectedResponseError(err or reason or "failure")

    # ---- AtomOSOps ----
    def check_online(self):
        try:
            return bool(self._hub and self._hub.online(self.agent_id))
        except Exception:  # noqa: BLE001
            return False

    def login(self):
        return self._send("login")

    def get_device_id(self):
        return self._send("get_device_id")

    def get_node_type(self):
        return self._send("get_dpn_mode")

    def get_node_lb_mode(self):
        return self._send("node_lb_mode_get")

    def get_exit_ip(self):
        return self._send("list_tunnels")

    def list_access_control(self):
        return self._send("list_devices")

    def set_node_lb_mode(self, mode):
        return self._send("node_lb_mode_set", {"mode": mode})

    def switch_node(self, tunnel_code, current_ip):
        return self._send("switch_node", {"tunnelCode": tunnel_code, "currentIp": current_ip})

    def refresh_tunnel(self, tunnel_code):
        return self._send("refresh_tunnel", {"tunnelCode": tunnel_code})

    def close(self):
        # NÃO derruba o AgentHub global: apenas libera a referência deste provider.
        self._hub = None
