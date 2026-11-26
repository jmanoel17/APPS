"""Cliente de uma DPN atendida por um agente Android (mesma interface do AtomOSClient).

A sessao HTTP do AtomOS (cookies/login) fica no agente; aqui so trafegam comandos
tipados. A identidade esperada da DPN e conferida pelo agente no MESMO caminho de
rede da operacao.
"""
from __future__ import annotations

from dpn.agent.protocol import Op, ResultStatus, new_request_id
from dpn.atomos.errors import (
    AuthError,
    ConnectionError,
    IdentityMismatchError,
    UncertainError,
    UnexpectedResponseError,
)
from dpn.models import Device

_AUTH_REASONS = {"wrongField", "wrongPassword", "tooManyAttempts", "invalidToken"}


class AgentClient:
    def __init__(self, device: Device, manager):
        self.device = device
        self.manager = manager
        self._agent_id = device.agent_id or ""

    # ------------------------------------------------------------------ interno
    @property
    def base_url(self) -> str:
        return f"agent:{self._agent_id}"

    def _call(self, op: str, params: dict | None = None):
        if not self._agent_id:
            raise ConnectionError("unidade sem agente associado")
        res = self.manager.send(self._agent_id, op, params or {},
                                expected_dpn_id=self.device.expected_dpn_id)
        if res.ok:
            return res.data
        status = res.status
        reason = res.reason or ""
        text = res.error or ""
        if status == ResultStatus.UNCERTAIN.value:
            raise UncertainError(text or "resultado incerto")
        if reason in _AUTH_REASONS or "autentic" in text.lower() or "autorizado" in text.lower():
            raise AuthError(text or reason or "falha de autenticação")
        if reason == "identity_mismatch" or "identidade" in text.lower():
            raise IdentityMismatchError(text or "ID físico divergente")
        if status == ResultStatus.EXPIRED.value:
            raise UnexpectedResponseError("comando expirado")
        raise UnexpectedResponseError(text or f"{op}: {status}")

    # ------------------------------------------------------------------ leitura
    def login(self):
        return self._call(Op.LOGIN.value)

    def get_device_id(self):
        return self._call(Op.GET_DEVICE_ID.value)

    def get_device_info(self):
        return self._call(Op.GET_DEVICE_INFO.value)

    def list_devices(self):
        return self._call(Op.LIST_DEVICES.value)

    def get_node_type(self):
        return self._call(Op.GET_DPN_MODE.value)

    def get_exit_ip(self):
        return self._call(Op.LIST_TUNNELS.value)

    def get_node_lb_mode(self):
        return self._call(Op.NODE_LB_MODE_GET.value)

    def get_access_control_switch(self):
        return self._call(Op.ACCESS_GET_SWITCH.value)

    def check_online(self) -> bool:
        return self.manager.server.is_online(self._agent_id)

    # ------------------------------------------------------------------ escrita
    def set_node_type(self, dpn_mode: str, tunnel_code: str | None = None,
                      kill_switch: bool | None = None):
        params = {"dpnMode": dpn_mode}
        if tunnel_code is not None:
            params["tunnelCode"] = tunnel_code
        if kill_switch is not None:
            params["killSwitch"] = kill_switch
        return self._call(Op.SET_DPN_MODE.value, params)

    def switch_node(self, tunnel_code: str, current_ip: str):
        return self._call(Op.SWITCH_NODE.value,
                          {"tunnelCode": tunnel_code, "currentIp": current_ip})

    def refresh_tunnel(self, tunnel_code: str):
        return self._call(Op.REFRESH_TUNNEL.value, {"tunnelCode": tunnel_code})

    def set_node_lb_mode(self, mode: str):
        return self._call(Op.NODE_LB_MODE_SET.value, {"mode": mode})

    def add_tunnel(self, region_code: str, country_code: str):
        return self._call(Op.ADD_TUNNEL.value,
                          {"regionCode": region_code, "countryCode": country_code})

    def set_access_control_switch(self, value: bool):
        return self._call(Op.ACCESS_SET_SWITCH.value, {"value": bool(value)})

    def set_access_control_item(self, record: dict):
        return self._call(Op.ACCESS_SET_ONE.value, {"record": record})

    def delete_access_control_devices(self, macs: list[str]):
        return self._call(Op.ACCESS_DELETE.value, {"macList": macs})

    def reboot(self):
        return self._call(Op.REBOOT.value)

    def close(self) -> None:
        pass
