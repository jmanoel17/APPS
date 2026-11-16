#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/provider_deeper.py — SLN V2: provedor REAL da Deeper Connect (AtomOS), E2.1.

Implementa APENAS operações mapeadas/confirmadas na auditoria E2.0, usando a camada
AtomOS **vendorizada** (`dpn/atomos_core`, sem Qt). Decisões congeladas:
  1) node_id de roteamento = tunnelCode;
  2) AtomOS deviceId = identidade física (apenas metadata);
  3) IP público atual = activeIp do tunnelCode configurado;
  4) REGULAR = nodeLbMode "none";
  5) reset_node NÃO é implementado (nem reboot/addTunnel/refreshTunnel);
  6) refreshTunnel é refresh do túnel, não "reset node";
  7) ipNotChanged NÃO é erro de conectividade;
  8) UncertainError NUNCA gera retry cego de mutação.

Contrato (DPNProvider):
  get_public_ip() -> str|None    (None se não configurado; TunnelNotFound se túnel ausente)
  get_node_id()   -> str         (tunnelCode confirmado em listTunnels)
  rotate_ip()     -> RotationResult (NÃO faz polling/state machine)
  health()        -> bool
  reset_node()    -> raise ProviderNotImplemented("NO_NODE_RESET_ENDPOINT")
  close()         -> None

Dependência de runtime real: `httpx` (+ `cryptography` no login RSA). O import do cliente
AtomOS é PREGUIÇOSO: testes com client_factory (mock) não exigem httpx/cryptography.
"""
from __future__ import annotations

import ipaddress
import os
import re
import threading
from dataclasses import dataclass

from .provider import DPNProvider
from .atomos_core.models import Device, TLSMode
from .atomos_core.endpoints import EndpointRegistry
from .atomos_core.errors import (
    AtomOSError,
    AuthError,
    ConnectionError as AtomOSConnectionError,
    UnexpectedResponseError,
    UncertainError,
)
from .atomos_ops import (
    AtomOSOpsError, AgentOfflineError, AgentTimeout, AtomOSTargetUnreachable,
    TLSIdentityError, AgentResponseMismatch,
)
from .transport_direct import DirectAtomOS
from .agent_transport import AgentAtomOS

DEFAULT_TLS = "insecure"


# ---------------------------------------------------------------- erros tipados
class ProviderError(Exception):
    code = "PROVIDER_ERROR"


class ProviderNotConfigured(ProviderError):
    code = "NOT_CONFIGURED"


class ProviderMissingPassword(ProviderError):
    code = "PASSWORD_MISSING"


class ProviderAuthError(ProviderError):
    code = "AUTH_ERROR"


class ProviderConnectionError(ProviderError):
    code = "CONNECTION_ERROR"


class ProviderUnexpected(ProviderError):
    code = "UNEXPECTED_RESPONSE"


class ProviderUncertain(ProviderError):
    code = "UNCERTAIN"


class TunnelNotFound(ProviderError):
    code = "TUNNEL_NOT_FOUND"


class ProviderInvalidIP(ProviderError):
    code = "INVALID_IP"


class ProviderNotImplemented(Exception):
    code = "NOT_IMPLEMENTED"


# ---------------------------------------------------------------- resultado de rotação
class RotationStatus:
    OK = "OK"
    REFRESH_REQUESTED = "ROTATION_PENDING_REFRESH"
    UNCERTAIN = "UNCERTAIN"
    AUTH_RENEWED = "AUTH_RENEWED"


@dataclass
class RotationResult:
    status: str
    old_ip: str | None = None
    new_ip: str | None = None
    tunnel_code: str | None = None
    detail: str = ""


def _norm_dpn_id(dpn_id):
    return re.sub(r"[^A-Za-z0-9]", "_", str(dpn_id or "")).upper()


def _valid_ip(ip):
    try:
        ipaddress.ip_address(str(ip))
        return True
    except Exception:
        return False


def _normalize_tunnels(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("list", "tunnels"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


class DeeperConnectProvider(DPNProvider):
    """Provedor real da Deeper Connect (AtomOS). Sessão/login lazy e isolados por unidade."""

    def __init__(self, controller_cfg=None, client_factory=None, password=None,
                 log_fn=None, env=None, agent_hub=None):
        c = controller_cfg or {}
        self.cfg = c
        self.address = str(c.get("address") or "").strip()
        self.port = int(c.get("port", 443) or 443)
        self.username = str(c.get("username", "admin") or "admin")
        self.tls_mode = str(c.get("tls_mode", DEFAULT_TLS) or DEFAULT_TLS).lower()
        self.ca_bundle_path = c.get("ca_bundle_path")
        self.tunnel_code = c.get("tunnel_code")
        self.node_lb_mode = str(c.get("node_lb_mode", "none") or "none")
        self.request_timeout = float(c.get("request_timeout_s", 10) or 10)
        self.connect_timeout = float(c.get("connect_timeout_s", 5) or 5)
        self.dpn_id = c.get("_dpn_id") or c.get("dpn_id") or ""
        # transporte: "agent" (WSS->Android->AtomOS) | "direct" (PC->AtomOS)
        self.agent_id = c.get("agent_id")
        self.expected_dpn_id = c.get("expected_dpn_id")
        self.android_serial = c.get("android_serial")
        self._agent_hub = agent_hub
        self._client_factory = client_factory
        self.transport = self._resolve_transport(c)

        self._password_override = password
        self._env = env if env is not None else os.environ
        self._log = log_fn or (lambda m: None)

        self._client = None
        self._lock = threading.RLock()

    def _resolve_transport(self, c):
        t = str(c.get("transport") or "").strip().lower()
        if t in ("agent", "direct"):
            return t
        # default do ambiente = agent; porém client_factory injetado (testes/legado) => direct
        return "direct" if self._client_factory is not None else "agent"

    @classmethod
    def from_config(cls, controller_cfg=None):
        return cls(controller_cfg)

    # ---------------------------------------------------------------- infra
    def _configured(self):
        return bool(self.address and self.tunnel_code)

    def _tls(self):
        if self.tls_mode == "verify":
            return TLSMode.VERIFY
        if self.tls_mode == "ca_bundle":
            return TLSMode.CA_BUNDLE
        return TLSMode.INSECURE

    def _resolve_password(self):
        if self._password_override is not None:
            if self._password_override == "":
                raise ProviderMissingPassword("senha vazia para %s" % (self.dpn_id or "DPN"))
            return self._password_override
        key = "SLN_DPN_PASSWORD_" + _norm_dpn_id(self.dpn_id)
        val = self._env.get(key)
        if not val:
            raise ProviderMissingPassword(
                "senha ausente: defina a variável de ambiente %s" % key)
        return val

    def _make_client(self):
        if self.transport == "agent":
            return AgentAtomOS(self._agent_hub, self.agent_id,
                               expected_dpn_id=self.expected_dpn_id,
                               timeout=self.request_timeout, log_fn=self._log)
        if self._client_factory is not None:
            return DirectAtomOS(self._client_factory())
        # import preguiçoso: puxa httpx só quando há cliente real (direct)
        from .atomos_core.client import AtomOSClient
        dev = Device(nickname=self.dpn_id or "deeper", address=self.address, port=self.port,
                     tls_mode=self._tls(), ca_bundle_path=self.ca_bundle_path,
                     username=self.username)
        return DirectAtomOS(AtomOSClient(
            device=dev, password_provider=lambda: self._resolve_password(),
            registry=EndpointRegistry(None),
            request_timeout=self.request_timeout, connect_timeout=self.connect_timeout))

    def _login(self, client):
        try:
            client.login()
        except ProviderMissingPassword:
            raise
        except AuthError as e:
            raise ProviderAuthError(str(e) or "login falhou")
        except AgentResponseMismatch as e:
            raise ProviderConnectionError(str(e))
        except AtomOSOpsError as e:
            raise ProviderConnectionError(str(e))
        except AtomOSConnectionError as e:
            raise ProviderConnectionError(str(e))
        except UnexpectedResponseError as e:
            raise ProviderUnexpected(str(e))
        except AtomOSError as e:
            raise ProviderError(str(e))

    def _ensure_session(self):
        with self._lock:
            if self._client is None:
                # transport=agent: senha/cookies ficam no Android Keystore/agente (NÃO resolver local)
                if self.transport != "agent":
                    self._resolve_password()
                self._client = self._make_client()
                self._login(self._client)
            return self._client

    def _relogin(self):
        with self._lock:
            if self._client is None:
                self._client = self._make_client()
            self._login(self._client)

    def _read(self, call):
        """Executa uma LEITURA; em AuthError renova o login UMA vez e repete a leitura."""
        client = self._ensure_session()
        try:
            try:
                return call(client)
            except AuthError:
                self._relogin()
                return call(client)
        except ProviderError:
            raise
        except AuthError as e:
            raise ProviderAuthError(str(e) or "não autorizado")
        except AtomOSOpsError as e:
            raise ProviderConnectionError(str(e))
        except AtomOSConnectionError as e:
            raise ProviderConnectionError(str(e))
        except UnexpectedResponseError as e:
            raise ProviderUnexpected(str(e))
        except AtomOSError as e:
            raise ProviderError(str(e))

    def _find_tunnel(self, data):
        for t in _normalize_tunnels(data):
            if isinstance(t, dict) and t.get("tunnelCode") == self.tunnel_code:
                return t
        return None

    # ---------------------------------------------------------------- contrato
    def get_public_ip(self):
        if not self._configured():
            return None  # coordinator transforma em ERROR(no_public_ip)
        data = self._read(lambda c: c.get_exit_ip())
        t = self._find_tunnel(data)
        if t is None:
            raise TunnelNotFound("túnel %r ausente em listTunnels" % (self.tunnel_code,))
        ip = t.get("activeIp")
        if not ip:
            raise ProviderError("ACTIVE_IP_MISSING: túnel %r sem activeIp" % (self.tunnel_code,))
        if not _valid_ip(ip):
            raise ProviderInvalidIP("activeIp inválido: %r" % (ip,))
        return str(ip)

    def get_node_id(self):
        # node_id de roteamento = tunnelCode (decisão congelada). O get_public_ip() já confirmou
        # o túnel em listTunnels no mesmo fluxo; aqui não repetimos a leitura.
        if not self._configured():
            return None
        return self.tunnel_code

    def device_metadata(self):
        """Metadata opcional: identidade física (deviceId). Nunca levanta."""
        out = {"physical_device_id": None}
        try:
            data = self._read(lambda c: c.get_device_id())
            if isinstance(data, dict):
                out["physical_device_id"] = data.get("deviceId")
        except Exception as e:  # noqa: BLE001
            out["error"] = type(e).__name__
        return out

    def health(self):
        if not self.address:
            return False
        try:
            client = self._client
            if client is None:
                with self._lock:
                    if self._client is None:
                        self._client = self._make_client()
                    client = self._client
            return bool(client.check_online())
        except Exception:  # noqa: BLE001
            return False

    def health_detail(self):
        d = {"online": False, "authenticated": False, "device_id": None, "tunnel_found": False}
        d["online"] = self.health()
        try:
            if d["online"]:
                data = self._read(lambda c: c.get_exit_ip())
                d["tunnel_found"] = self._find_tunnel(data) is not None
                d["authenticated"] = True
                meta = self.device_metadata()
                d["device_id"] = meta.get("physical_device_id")
        except Exception:  # noqa: BLE001
            pass
        return d

    def _ensure_regular(self):
        desired = self.node_lb_mode or "none"
        r = self._read(lambda c: c.get_node_lb_mode())
        mode = r.get("mode") if isinstance(r, dict) else None
        if mode is None or mode == desired:
            return
        client = self._ensure_session()
        try:
            client.set_node_lb_mode(desired)
        except AuthError:
            self._relogin()        # renova sessão; NÃO reenvia a mutação
        except UncertainError as e:
            raise ProviderUncertain(str(e) or "set nodeLbMode incerto")
        except AtomOSOpsError as e:
            raise ProviderConnectionError(str(e))
        except AtomOSConnectionError as e:
            raise ProviderConnectionError(str(e))
        except UnexpectedResponseError as e:
            raise ProviderUnexpected(str(e))
        # confirma por LEITURA (retry de leitura é seguro)
        r2 = self._read(lambda c: c.get_node_lb_mode())
        m2 = r2.get("mode") if isinstance(r2, dict) else None
        if m2 != desired:
            raise ProviderError("MODE_NOT_REGULAR: nodeLbMode=%r (desejado %r)" % (m2, desired))

    def _refresh_tunnel(self):
        """Refresh do túnel (NÃO é reset de node). Telemetria + resultado bruto."""
        self._log("[DPN][DEEPER_REFRESH_TUNNEL] dpn=%s tunnel=%s"
                  % (self.dpn_id, self.tunnel_code))
        client = self._ensure_session()
        try:
            return client.refresh_tunnel(self.tunnel_code)
        except UncertainError:
            return None            # incerto: coordinator reconcilia via get_public_ip()
        except AuthError:
            try:
                self._relogin()    # renova; NÃO reenvia a mutação
            except ProviderError:
                pass
            return None
        except AgentTimeout:
            return None            # timeout de agente em mutação: incerto -> reconciliar por leitura
        except AtomOSOpsError as e:
            raise ProviderConnectionError(str(e))
        except AtomOSConnectionError as e:
            raise ProviderConnectionError(str(e))
        except UnexpectedResponseError as e:
            raise ProviderUnexpected(str(e))

    def rotate_ip(self, mode="REGULAR"):
        """Solicita troca de IP. NÃO faz polling/state machine (coordinator faz).

        Retorna RotationResult:
          OK                -> switchNode solicitado (ou já retornou activeIp novo)
          REFRESH_REQUESTED -> ipNotChanged: refreshTunnel disparado (async)
          UNCERTAIN         -> UncertainError: NÃO reenviar; reconciliar via get_public_ip()
          AUTH_RENEWED      -> AuthError em mutação: relogin feito, NÃO reenviado
        """
        if not self._configured():
            raise ProviderNotConfigured("address/tunnel_code ausentes")
        with self._lock:
            self._ensure_regular()
            data = self._read(lambda c: c.get_exit_ip())
            t = self._find_tunnel(data)
            if t is None:
                raise TunnelNotFound("túnel %r ausente em listTunnels" % (self.tunnel_code,))
            current = t.get("activeIp")
            if not current:
                raise ProviderError("ACTIVE_IP_MISSING: túnel %r sem activeIp" % (self.tunnel_code,))
            if not _valid_ip(current):
                raise ProviderInvalidIP("activeIp inválido: %r" % (current,))

            client = self._ensure_session()
            try:
                resp = client.switch_node(self.tunnel_code, current)
            except UnexpectedResponseError as e:
                if "ipnotchanged" in str(e).lower():
                    self._refresh_tunnel()
                    return RotationResult(RotationStatus.REFRESH_REQUESTED, str(current), None,
                                          self.tunnel_code, "ipNotChanged")
                raise ProviderUnexpected(str(e))
            except UncertainError as e:
                return RotationResult(RotationStatus.UNCERTAIN, str(current), None,
                                      self.tunnel_code, str(e) or "uncertain")
            except AgentTimeout as e:
                return RotationResult(RotationStatus.UNCERTAIN, str(current), None,
                                      self.tunnel_code, str(e) or "agent timeout")
            except AuthError:
                try:
                    self._relogin()
                except ProviderError:
                    pass
                return RotationResult(RotationStatus.AUTH_RENEWED, str(current), None,
                                      self.tunnel_code, "no_resend_after_auth")
            except AtomOSOpsError as e:
                raise ProviderConnectionError(str(e))
            except AtomOSConnectionError as e:
                raise ProviderConnectionError(str(e))

            new_ip = resp.get("activeIp") if isinstance(resp, dict) else None
            if new_ip and str(new_ip) != str(current):
                return RotationResult(RotationStatus.OK, str(current), str(new_ip),
                                      self.tunnel_code, "switched")
            return RotationResult(RotationStatus.OK, str(current), None,
                                  self.tunnel_code, "requested")

    def reset_node(self):
        # Decisão congelada: NÃO usar reboot/addTunnel/refreshTunnel como "reset node".
        raise ProviderNotImplemented("NO_NODE_RESET_ENDPOINT")

    def close(self):
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:  # noqa: BLE001
                    pass
                self._client = None
