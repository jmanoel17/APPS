"""Registro de endpoints da API AtomOS.

Todos os caminhos abaixo foram CONFIRMADOS inspecionando o bundle JS servido
pela propria unidade (index.*.js do painel em https://34.34.34.34), nao por
suposicao. O arquivo endpoints.json (opcional) pode sobrescrever/adicionar
caminhos para tolerar diferencas de versao de firmware sem tocar no codigo.

Envelope de resposta observado: JSON com `success: boolean` e, em caso de
falha, um campo `reason` (string) — alem de casos isolados com `code: 0`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class Endpoint:
    key: str
    method: str = "GET"
    path: str | None = None            # None => NAO confirmado
    confirmed: bool = False
    mutating: bool = False
    payload: dict | None = None        # template com placeholders {campo}
    description: str = ""


# Endpoints CONFIRMADOS (extraidos do frontend servido pela unidade).
_CONFIRMED: dict[str, Endpoint] = {
    "login": Endpoint(
        key="login", method="POST", path="/api/admin/login", confirmed=True,
        description="Autenticacao (senha criptografada com RSA-OAEP/SHA-1)",
    ),
    "validate_token": Endpoint(
        key="validate_token", method="POST", path="/api/admin/validateToken",
        confirmed=True, description="Valida sessao atual",
    ),
    "logout": Endpoint(
        key="logout", method="POST", path="/api/admin/logOut", confirmed=True,
        description="Encerra sessao",
    ),
    "device_id": Endpoint(
        key="device_id", method="GET", path="/api/admin/getDeviceId",
        confirmed=True, description="Identificador estavel da unidade",
    ),
    "device_info": Endpoint(
        key="device_info", method="GET", path="/api/system-info/hardware-info",
        confirmed=True, description="Informacoes de hardware (modelo, MAC, etc.)",
    ),
    "software_info": Endpoint(
        key="software_info", method="GET", path="/api/system-info/software-info",
        confirmed=True, description="Versao de software",
    ),
    "network_info": Endpoint(
        key="network_info", method="GET", path="/api/system-info/network-address",
        confirmed=True, description="IP/enderecos de rede da unidade",
    ),
    "device_list": Endpoint(
        key="device_list", method="GET", path="/api/accessControl/list",
        confirmed=True, description="Lista de dispositivos conectados (access control)",
    ),
    "access_switch_get": Endpoint(
        key="access_switch_get", method="GET", path="/api/accessControl/switch",
        confirmed=True, description="Estado do switch geral do Access Control",
    ),
    "access_switch_set": Endpoint(
        key="access_switch_set", method="POST", path="/api/accessControl/switch",
        confirmed=True, mutating=True,
        description="Liga/desliga o Access Control {value}",
    ),
    "access_set_one": Endpoint(
        key="access_set_one", method="POST", path="/api/accessControl/setOne",
        confirmed=True, mutating=True,
        description="Atualiza um dispositivo {registro + campo alterado}",
    ),
    "access_delete": Endpoint(
        key="access_delete", method="POST", path="/api/accessControl/delete",
        confirmed=True, mutating=True,
        description="Remove dispositivos {macList}",
    ),
    "node_type_get": Endpoint(
        key="node_type_get", method="GET", path="/api/smartRoute/getDpnMode",
        confirmed=True, description="Leitura do modo DPN (smart/full/disabled/blackHole)",
    ),
    "node_type_set": Endpoint(
        key="node_type_set", method="POST", path="/api/smartRoute/setDpnMode",
        confirmed=True, mutating=True,
        description="Alteracao do modo DPN {dpnMode, tunnelCode, killSwitch}",
    ),
    "exit_ip_get": Endpoint(
        key="exit_ip_get", method="GET", path="/api/smartRoute/listTunnels",
        confirmed=True, description="Tunneis ativos com IP de saida (activeIp)",
    ),
    "exit_ip_set": Endpoint(
        key="exit_ip_set", method="POST", path="/api/smartRoute/switchNode",
        confirmed=True, mutating=True,
        description="Troca do IP ativo {tunnelCode, currentIp}",
    ),
    "refresh_tunnel": Endpoint(
        key="refresh_tunnel", method="POST", path="/api/smartRoute/refreshTunnel",
        confirmed=True, mutating=True,
        description="Gera novos IPs para um tunel {tunnelCode}",
    ),
    "node_lb_mode_get": Endpoint(
        key="node_lb_mode_get", method="GET", path="/api/smartRoute/nodeLbMode",
        confirmed=True, description="Leitura do Node Selection Mode",
    ),
    "node_lb_mode_set": Endpoint(
        key="node_lb_mode_set", method="POST", path="/api/smartRoute/nodeLbMode",
        confirmed=True, mutating=True,
        description="Alteracao do Node Selection Mode {mode}",
    ),
    "add_tunnel": Endpoint(
        key="add_tunnel", method="POST", path="/api/smartRoute/addTunnel",
        confirmed=True, mutating=True,
        description="Adiciona tunel {regionCode, countryCode}",
    ),
    "reboot": Endpoint(
        key="reboot", method="POST", path="/api/admin/reboot", confirmed=True,
        mutating=True, description="Reinicio da unidade",
    ),
}


class EndpointRegistry:
    """Resolve endpoints: confirmados em codigo + sobrescritas em endpoints.json."""

    def __init__(self, endpoints_path: str | None = None):
        self._extra: dict[str, Endpoint] = {}
        self.endpoints_path = endpoints_path
        if endpoints_path and os.path.isfile(endpoints_path):
            self._load_file(endpoints_path)

    def _load_file(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        for key, spec in data.items():
            if not isinstance(spec, dict):
                continue
            self._extra[key] = Endpoint(
                key=key,
                method=str(spec.get("method", "GET")).upper(),
                path=spec.get("path"),
                confirmed=bool(spec.get("confirmed", True)),
                mutating=bool(spec.get("mutating", False)),
                payload=spec.get("payload"),
                description=str(spec.get("description", "")),
            )

    def get(self, key: str) -> Endpoint:
        if key in self._extra:
            return self._extra[key]
        if key in _CONFIRMED:
            return _CONFIRMED[key]
        raise KeyError(f"endpoint desconhecido: {key}")

    def confirm(self, key: str, method: str, path: str, mutating: bool,
                payload: dict | None = None, description: str = "") -> None:
        self._extra[key] = Endpoint(
            key=key, method=method.upper(), path=path, confirmed=True,
            mutating=mutating, payload=payload, description=description,
        )

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out = {}
        for key, ep in self._extra.items():
            out[key] = {
                "method": ep.method,
                "path": ep.path,
                "confirmed": ep.confirmed,
                "mutating": ep.mutating,
                "payload": ep.payload,
                "description": ep.description,
            }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)

    def confirmed_map(self) -> dict[str, bool]:
        return {key: self.get(key).confirmed for key in _CONFIRMED}
