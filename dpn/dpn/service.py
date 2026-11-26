"""Camada de servico: storage, segredos, endpoints, agente e cliente por unidade.

Mantem UMA sessao por unidade (direta ou via agente). Para unidades com agente,
a sessao HTTP do AtomOS fica no agente; aqui so trafegam comandos tipados.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable

from dpn.agent.client import AgentClient
from dpn.agent.registry import AgentRecord
from dpn.atomos.client import AtomOSClient
from dpn.atomos.endpoints import EndpointRegistry
from dpn.config import Config
from dpn.models import ActionLog, ActionStatus, Device, IdentityState
from dpn.security import redact
from dpn.storage.database import Database
from dpn.storage.secrets import CredentialStore


class CredentialMissingError(Exception):
    """Senha da unidade ainda nao cadastrada (somente transporte direto)."""


_INVALID_IDS = {"", "-", "—", "unknown", "none", "null"}


class DPNService:
    def __init__(self, config: Config):
        self.config = config
        config.ensure_dirs()
        self.db = Database(config.db_path)
        self.secrets = CredentialStore(config.data_dir)
        self.registry = EndpointRegistry(config.endpoints_path)
        self.agent_manager = None

        self._clients: dict[int, object] = {}
        self._device_locks: dict[int, threading.Lock] = {}
        self._master = threading.Lock()
        self._tunnel_ips: dict[int, tuple[float, set[str]]] = {}

    # --------------------------------------------------------------- agentes
    def attach_agent_manager(self, manager) -> None:
        self.agent_manager = manager

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        return self.db.get_agent(agent_id)

    def list_agents(self) -> list[AgentRecord]:
        return self.db.list_agents()

    def upsert_agent(self, rec: AgentRecord) -> None:
        self.db.upsert_agent(rec)

    def delete_agent(self, agent_id: str) -> None:
        rec = self.db.get_agent(agent_id)
        did = rec.device_id if rec else None
        self.db.delete_agent(agent_id)
        if did is not None:
            self.close_device(did)

    def touch_agent(self, agent_id: str) -> None:
        self.db.touch_agent(agent_id, time.time())

    def set_agent_observed_id(self, agent_id: str, observed_id: str) -> None:
        self.db.set_agent_observed_id(agent_id, observed_id)

    def associate_agent(self, agent_id: str, device_id: int | None) -> None:
        """Associa explicitamente um agente a um cadastro, mantendo 1-para-1."""
        prev = self.db.get_agent(agent_id)
        prev_device = prev.device_id if prev else None
        dev_prev = self.db.get_device(device_id) if device_id is not None else None
        dev_prev_agent = dev_prev.agent_id if dev_prev else None
        self.db.associate_agent(agent_id, device_id)
        # invalida sessoes dos cadastros envolvidos
        for did in {prev_device, device_id}:
            if did is not None:
                self.close_device(did)

    def disassociate_agent(self, agent_id: str) -> None:
        rec = self.db.get_agent(agent_id)
        did = rec.device_id if rec else None
        self.db.disassociate_agent(agent_id)
        if did is not None:
            self.close_device(did)

    # ------------------------------------------------------------- dispositivos
    def list_devices(self) -> list[Device]:
        return self.db.list_devices()

    def get_device(self, device_id: int) -> Device | None:
        return self.db.get_device(device_id)

    def add_device(self, d: Device) -> Device:
        d.updated_at = d.created_at
        return self.db.add_device(d)

    def update_device(self, d: Device) -> None:
        self.db.update_device(d)
        self.invalidate(d)

    def delete_device(self, device_id: int) -> None:
        self.secrets.delete_password(device_id)
        self.db.delete_device(device_id)          # limpa refs de agentes
        self._tunnel_ips.pop(device_id, None)
        self.close_device(device_id)

    # ------------------------------------------------------------------- segredos
    def save_password(self, device_id: int, password: str) -> None:
        self.secrets.set_password(device_id, password)
        self.close_device(device_id)

    def has_password(self, device_id: int) -> bool:
        return self.secrets.get_password(device_id) is not None

    def get_password(self, device_id: int) -> str | None:
        return self.secrets.get_password(device_id)

    # -------------------------------------------------------------------- cliente
    def client_for(self, device: Device) -> AtomOSClient:
        def _provider() -> str:
            if device.id is None:
                raise CredentialMissingError("unidade sem id")
            pwd = self.secrets.get_password(device.id)
            if pwd is None:
                raise CredentialMissingError(
                    f"senha nao cadastrada para '{device.nickname}'")
            return pwd
        return AtomOSClient(
            device=device, password_provider=_provider, registry=self.registry,
            request_timeout=self.config.request_timeout,
            connect_timeout=self.config.connect_timeout,
            log_requests=self.config.log_requests,
        )

    def agent_client_for(self, device: Device) -> AgentClient:
        if self.agent_manager is None:
            raise RuntimeError("servidor de agentes não iniciado")
        aid = self._resolve_agent_id(device)
        if not aid:
            raise RuntimeError("modo agente sem agente associado")
        device.agent_id = aid  # auto-cura do cadastro
        return AgentClient(device, self.agent_manager)

    def is_agent_transport(self, device: Device) -> bool:
        return device.transport_mode == "agent" and bool(self._resolve_agent_id(device))

    def _resolve_agent_id(self, device: Device) -> str | None:
        if device.agent_id:
            return device.agent_id
        try:
            for a in self.db.list_agents():
                if a.device_id == device.id:
                    return a.agent_id
        except Exception:
            pass
        return None

    def set_device_serial(self, device_id: int, serial: str) -> None:
        self.db.set_device_serial(device_id, serial)

    def set_transport(self, device: Device, mode: str) -> None:
        """Troca direto<->agente SEM apagar associacao/credenciais/identidade."""
        if mode not in ("direct", "agent"):
            raise ValueError("modo de transporte inválido")
        if mode == "agent" and not self._resolve_agent_id(device):
            raise ValueError("associe um agente antes de usar o modo agente")
        device.transport_mode = mode
        self.db.set_device_transport(device.id, mode)
        self.invalidate(device)

    # ------------------------------------------------------------- identidade
    @staticmethod
    def valid_id(value) -> bool:
        if value is None:
            return False
        return str(value).strip().lower() not in _INVALID_IDS

    def set_observed_identity(self, device_id: int, observed_id: str | None,
                              origin: str = "", ts: float | None = None) -> bool:
        if not self.valid_id(observed_id):
            return False
        self.db.set_observed_identity(device_id, str(observed_id).strip(), origin, ts)
        return True

    def set_expected_identity(self, device_id: int, expected_id: str | None) -> None:
        self.db.set_expected_identity(device_id, expected_id)

    def bind_identity(self, device: Device) -> bool:
        """Vinculacao explicita; retorna True apenas se persistiu corretamente."""
        obs = device.last_observed_id
        if not self.valid_id(obs):
            return False
        self.db.set_expected_identity(device.id, obs)
        fresh = self.db.get_device(device.id)
        ok = bool(fresh and fresh.expected_dpn_id == obs)
        if fresh is not None:
            device.expected_dpn_id = fresh.expected_dpn_id
        self.invalidate(device)
        return ok

    def unbind_identity(self, device: Device) -> None:
        self.db.set_expected_identity(device.id, None)
        device.expected_dpn_id = None
        self.invalidate(device)

    def validate_direct_identity(self, client, device: Device) -> str | None:
        info = client.get_device_id()
        obs = info.get("deviceId") if isinstance(info, dict) else None
        if self.valid_id(obs):
            self.set_observed_identity(device.id, obs, origin="direct")
        return obs

    def finish_agent_setup(self, device_id: int, agent_id: str, observed_id: str | None,
                           transport: str = "agent", bind: bool = True) -> dict:
        """Conclui associate+observacao+vinculo numa transacao e devolve o estado
        persistido (para a UI so anunciar sucesso com base no que foi gravado)."""
        with self.db.transaction():
            self.db.associate_agent(agent_id, device_id)
            if self.valid_id(observed_id):
                self.db.set_observed_identity(device_id, str(observed_id).strip(),
                                              origin=f"agent:{agent_id}")
            if transport:
                self.db.set_device_transport(device_id, transport)
            if bind and self.valid_id(observed_id):
                self.db.set_expected_identity(device_id, str(observed_id).strip())
        self.close_device(device_id)
        dev = self.db.get_device(device_id)
        bound = bool(dev and dev.expected_dpn_id and
                     dev.expected_dpn_id == dev.last_observed_id)
        return {
            "device": dev,
            "agent_id": dev.agent_id if dev else None,
            "observed": dev.last_observed_id if dev else None,
            "expected": dev.expected_dpn_id if dev else None,
            "transport": dev.transport_mode if dev else None,
            "bound": bound,
        }

    # ------------------------------------------------------------- concorrencia
    def _device_lock(self, device_id: int) -> threading.RLock:
        with self._master:
            lock = self._device_locks.get(device_id)
            if lock is None:
                lock = threading.RLock()
                self._device_locks[device_id] = lock
            return lock

    def get_session(self, device: Device, password: str | None = None):
        if device.id is None:
            raise CredentialMissingError("unidade sem id")
        lock = self._device_lock(device.id)
        with lock:
            client = self._clients.get(device.id)
            if client is not None:
                return client
            if device.transport_mode == "agent":
                if not self._resolve_agent_id(device):
                    raise RuntimeError("modo agente sem agente associado")
                client = self.agent_client_for(device)
            else:
                client = self.client_for(device)
                client.login(password=password)
            self._clients[device.id] = client
            return client

    @contextmanager
    def device_session(self, device: Device, password: str | None = None):
        if device.id is None:
            raise CredentialMissingError("unidade sem id")
        lock = self._device_lock(device.id)
        with lock:
            yield self.get_session(device, password)

    def invalidate(self, device: Device) -> None:
        if device.id is not None:
            self.close_device(device.id)

    def close_device(self, device_id: int) -> None:
        lock = self._device_lock(device_id)
        with lock:
            with self._master:
                client = self._clients.pop(device_id, None)
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    # ------------------------------------------------------------- conflito
    def _identity_conflict_map(self) -> dict[str, list[int]]:
        by_obs: dict[str, list[int]] = {}
        for d in self.db.list_devices():
            if self.valid_id(d.last_observed_id):
                by_obs.setdefault(d.last_observed_id, []).append(d.id)
        return {obs: ids for obs, ids in by_obs.items() if len(ids) > 1}

    def conflicts(self) -> dict[str, list[int]]:
        return self._identity_conflict_map()

    # --------------------------------------------- IPs de saida por tunel
    def set_device_tunnel_ips(self, device_id: int, ips, origin: str = "") -> None:
        self._tunnel_ips[device_id] = (time.time(), {str(i) for i in ips if i})

    def clear_device_tunnel_ips(self, device_id: int) -> None:
        self._tunnel_ips.pop(device_id, None)

    def shared_tunnel_ips(self, max_age: float = 900.0) -> dict[str, list[int]]:
        """IPs de tunel usados por mais de uma DPN (alerta separado). Descarta
        cadastros inexistentes e observacoes antigas."""
        now = time.time()
        valid_ids = {d.id for d in self.db.list_devices()}
        by_ip: dict[str, list[int]] = {}
        for did, (ts, ips) in list(self._tunnel_ips.items()):
            if did not in valid_ids:
                self._tunnel_ips.pop(did, None)
                continue
            if now - ts > max_age:
                continue
            for ip in ips:
                by_ip.setdefault(ip, []).append(did)
        return {ip: ids for ip, ids in by_ip.items() if len(ids) > 1}

    # ------------------------------------------------------------- estados
    def identity_state(self, device: Device) -> str:
        conflict_map = self._identity_conflict_map()
        if self.valid_id(device.last_observed_id) and \
                len(conflict_map.get(device.last_observed_id, [])) > 1:
            return IdentityState.CONFLICT
        if device.transport_mode == "agent":
            aid = self._resolve_agent_id(device)
            if not aid:
                return IdentityState.NOT_CONFIGURED
            rt = (self.agent_manager.runtime(aid)
                  if self.agent_manager is not None else None)
            if rt is None or not rt.online:
                return IdentityState.AGENT_UNAVAILABLE
            if rt.atomos_status == "auth_failed":
                return IdentityState.AUTH_FAILED
            if rt.atomos_status == "unreachable" and rt.last_op:
                return IdentityState.ATOMOS_UNREACHABLE
        if not self.valid_id(device.expected_dpn_id):
            return IdentityState.IDENTITY_UNLINKED
        if self.valid_id(device.last_observed_id):
            if device.last_observed_id == device.expected_dpn_id:
                return IdentityState.ID_VALIDATED
            return IdentityState.ID_MISMATCH
        return IdentityState.AGENT_CONNECTED if device.transport_mode == "agent" \
            else IdentityState.IDENTITY_UNLINKED

    def can_mutate(self, device: Device) -> tuple[bool, str]:
        """Bloqueia alteracoes quando a identidade/agente nao permitem (AMBOS os modos)."""
        state = self.identity_state(device)
        if state == IdentityState.CONFLICT:
            return False, "conflito: mesma DPN"
        if state == IdentityState.ID_MISMATCH:
            return False, "ID físico divergente"
        if state == IdentityState.IDENTITY_UNLINKED:
            return False, "identidade não vinculada"
        if state in (IdentityState.NOT_CONFIGURED, IdentityState.AGENT_UNAVAILABLE):
            return False, "agente indisponível/não configurado"
        if state == IdentityState.AUTH_FAILED:
            return False, state
        # ATOMOS_UNREACHABLE (so ocorre no modo agente) NAO bloqueia: o agente
        # reconfere a identidade a cada operacao e o erro real aparece na
        # tentativa — evita "travar" apos uma oscilacao/ipNotChanged.
        if not self.valid_id(device.expected_dpn_id):
            return False, "identidade não vinculada"
        return True, ""

    # ------------------------------------------------------------------ historico
    def log(self, device: Device, action: str, status: ActionStatus, message: str) -> None:
        self.db.add_log(ActionLog(
            device_id=device.id, device_nickname=device.nickname, action=action,
            status=status.value, message=redact(message),
        ))

    def history(self, device_id: int | None = None, limit: int = 200) -> list[ActionLog]:
        return self.db.list_logs(device_id, limit)

    def close(self) -> None:
        for device_id in list(self._clients):
            self.close_device(device_id)
        self.db.close()
