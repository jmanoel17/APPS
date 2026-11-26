"""Cliente AtomOS por dispositivo.

Cada instancia e amarrada a EXATAMENTE uma unidade (address) e possui sua propria
sessao HTTP (cookies), contexto TLS, timeout e lock. Nenhuma sessao e compartilhada
entre unidades. Redirects sao desativados para que uma unidade nao "puxe" a acao
para outro host.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

import httpx

from dpn.atomos.endpoints import EndpointRegistry
from dpn.atomos.errors import (
    AtomOSError,
    AuthError,
    ConnectionError,
    EndpointUnconfirmedError,
    UnitMismatchError,
    UnexpectedResponseError,
)
from dpn.atomos.tls import verify_arg_for
from dpn.models import Device
from dpn.security import ATOMOS_RSA_PUBLIC_KEY, rsa_encrypt_password


class AtomOSClient:
    """Cliente HTTP de uma unica unidade Deeper Connect."""

    def __init__(
        self,
        device: Device,
        password_provider: Callable[[], str],
        registry: EndpointRegistry,
        request_timeout: float = 10.0,
        connect_timeout: float = 5.0,
        log_requests: bool = False,
        transport: httpx.BaseTransport | None = None,
    ):
        self.device = device
        self._base_url = device.base_url
        self._password_provider = password_provider
        self._registry = registry
        self._log_requests = log_requests
        self._request_timeout = request_timeout
        self._connect_timeout = connect_timeout
        self._transport = transport

        self._session: httpx.Client | None = None
        self._lock = threading.Lock()
        self._token: str | None = None   # token capturado em memoria, se a API devolver
        self._public_key: str | None = None  # chave RSA da unidade (None => fallback)

    # ------------------------------------------------------------------ infra
    @property
    def base_url(self) -> str:
        return self._base_url

    def _client(self) -> httpx.Client:
        if self._session is None:
            timeout = httpx.Timeout(timeout=self._request_timeout, connect=self._connect_timeout)
            self._session = httpx.Client(
                base_url=self._base_url,
                verify=verify_arg_for(self.device.tls_mode, self.device.ca_bundle_path),
                timeout=timeout,
                follow_redirects=False,
                transport=self._transport,
            )
        return self._session

    def _require_confirmed(self, key: str) -> None:
        ep = self._registry.get(key)
        if not ep.confirmed or not ep.path:
            raise EndpointUnconfirmedError(
                f"Operacao '{key}' ainda nao confirmada por captura real. "
                f"Confirme o endpoint em {self._registry.endpoints_path or 'endpoints.json'}."
            )

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _extract_token(self, payload: Any) -> None:
        """Captura um eventual token devolvido pela API (sem logar)."""
        if isinstance(payload, dict):
            for field in ("token", "authToken", "access_token", "session"):
                val = payload.get(field)
                if isinstance(val, str) and val:
                    self._token = val
                    return

    @staticmethod
    def _reason(resp: httpx.Response) -> str:
        """Extrai `reason` do corpo JSON de erro, sem estourar em corpo nao-JSON."""
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("reason"):
                return str(data["reason"])
        except Exception:
            pass
        return ""

    def fetch_public_key(self) -> str | None:
        """Busca a chave publica RSA servida no JS da propria unidade.

        Util para tolerar firmwares com chave diferente da embutida. Retorna o
        PEM ou None (nesse caso o login usa a chave embutida).
        """
        import re
        try:
            with self._lock:
                html = self._client().get("/admin/system-info").text
            m = re.search(r'src="([^"]+\.js)"', html)
            if not m:
                return None
            js_url = m.group(1)
            with self._lock:
                js = self._client().get(js_url).text
            k = re.search(r'-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----',
                          js, re.DOTALL)
            if k:
                self._public_key = k.group(0)
                return self._public_key
        except Exception:
            pass
        return None

    # --------------------------------------------------------------- request
    def request(self, key: str, **kwargs) -> dict | list | None:
        """Executa um endpoint REGISTRADO. Exige endpoint confirmado."""
        ep = self._registry.get(key)
        self._require_confirmed(key)
        return self._raw_request(
            method=ep.method,
            path=ep.path,
            json=kwargs.get("json"),
            params=kwargs.get("params"),
        )

    def _raw_request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
        check_success: bool = True,
    ) -> dict | list | None:
        url_path = path if path.startswith("/") else f"/{path}"
        with self._lock:
            try:
                resp = self._client().request(
                    method, url_path, json=json, params=params, headers=self._headers()
                )
            except httpx.HTTPError as exc:
                raise ConnectionError(f"falha de rede/TLS: {exc}") from exc

        if self._log_requests:
            # nunca loga corpo de login/payload sensivel
            print(f"[atomos] {method} {url_path} -> {resp.status_code}")

        if resp.status_code in (401, 403):
            raise AuthError(f"nao autorizado ({resp.status_code}): {self._reason(resp)}")

        if 300 <= resp.status_code < 400:
            raise UnexpectedResponseError(
                f"{method} {url_path} redirecionou ({resp.status_code}); "
                f"acao bloqueada para nao atingir outro host"
            )

        if resp.status_code >= 400:
            raise UnexpectedResponseError(
                f"{method} {url_path} respondeu {resp.status_code}: "
                f"{self._reason(resp) or resp.text[:200]}"
            )

        if not resp.content:
            return None

        try:
            payload = resp.json()
        except ValueError:
            return {"raw": resp.text}

        self._extract_token(payload)
        # 200 com success:false NAO e sucesso
        if check_success and isinstance(payload, dict) and payload.get("success") is False:
            raise UnexpectedResponseError(
                f"{method} {url_path}: {payload.get('reason') or 'success=false'}"
            )
        return payload

    # ------------------------------------------------------------------ ops
    def login(self, username: str | None = None, password: str | None = None) -> dict | None:
        """POST /api/admin/login (senha criptografada com RSA-OAEP/SHA-1)."""
        username = username or self.device.username
        password = password if password is not None else self._password_provider()
        ep = self._registry.get("login")
        encrypted = rsa_encrypt_password(password, self._public_key or ATOMOS_RSA_PUBLIC_KEY)
        body = {"username": username, "password": encrypted}
        result = self._raw_request(ep.method, ep.path, json=body, check_success=False)
        # A API responde {success:false, reason} em credenciais invalidas (HTTP 200).
        if isinstance(result, dict) and result.get("success") is False:
            raise AuthError(f"falha de autenticacao: {result.get('reason') or 'credenciais invalidas'}")
        return result

    def check_online(self) -> bool:
        """Verifica alcancabilidade da unidade (GET raiz, sem login)."""
        try:
            with self._lock:
                resp = self._client().get("/")
            return resp.status_code < 500
        except Exception:
            return False

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None

    # ---------------------------------------- operacoes (confirmadas no frontend)
    def get_device_info(self) -> dict:
        """Compoe modelo/versao/IP/deviceId a partir dos endpoints de sistema."""
        out: dict = {}
        for key in ("device_id", "device_info", "software_info", "network_info"):
            try:
                data = self.request(key)
                if isinstance(data, dict):
                    out.update(data)
            except AtomOSError:
                continue
        return out

    def get_device_id(self) -> dict | list | None:
        return self.request("device_id")

    def list_devices(self) -> dict | list | None:
        return self.request("device_list")

    def get_access_control_switch(self) -> dict | list | None:
        return self.request("access_switch_get")

    def set_access_control_switch(self, value: bool) -> dict | list | None:
        return self.request("access_switch_set", json={"value": value})

    def set_access_control_item(self, record: dict) -> dict | list | None:
        return self.request("access_set_one", json=record)

    def delete_access_control_devices(self, macs: list[str]) -> dict | list | None:
        return self.request("access_delete", json={"macList": macs})

    def get_node_type(self) -> dict | list | None:
        return self.request("node_type_get")

    def set_node_type(self, dpn_mode: str, tunnel_code: str | None = None,
                      kill_switch: bool | None = None) -> dict | list | None:
        body = {"dpnMode": dpn_mode}
        if tunnel_code is not None:
            body["tunnelCode"] = tunnel_code
        if kill_switch is not None:
            body["killSwitch"] = kill_switch
        return self.request("node_type_set", json=body)

    def get_exit_ip(self) -> dict | list | None:
        return self.request("exit_ip_get")

    def switch_node(self, tunnel_code: str, current_ip: str) -> dict | list | None:
        return self.request("exit_ip_set", json={"tunnelCode": tunnel_code,
                                                 "currentIp": current_ip})

    def refresh_tunnel(self, tunnel_code: str) -> dict | list | None:
        return self.request("refresh_tunnel", json={"tunnelCode": tunnel_code})

    def get_node_lb_mode(self) -> dict | list | None:
        return self.request("node_lb_mode_get")

    def set_node_lb_mode(self, mode: str) -> dict | list | None:
        return self.request("node_lb_mode_set", json={"mode": mode})

    def add_tunnel(self, region_code: str, country_code: str) -> dict | list | None:
        return self.request("add_tunnel", json={"regionCode": region_code,
                                                "countryCode": country_code})

    def reboot(self) -> dict | list | None:
        return self.request("reboot")

    def validate_token(self) -> dict | list | None:
        return self.request("validate_token")

    def logout(self) -> dict | list | None:
        return self.request("logout")

    def __enter__(self) -> "AtomOSClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
