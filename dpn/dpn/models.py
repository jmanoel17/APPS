"""Modelos de dados e enumeracoes do DPN Control.

Nada aqui persiste segredos: senhas/tokens/cookies vivem somente em memoria
ou no armazenamento seguro do sistema (ver storage.secrets).
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field, asdict


class TLSMode(str, enum.Enum):
    """Como validar o certificado HTTPS de uma unidade."""

    VERIFY = "verify"          # valida contra a cadeia de confianca do sistema
    CA_BUNDLE = "ca_bundle"    # valida contra um certificado/CA fornecido pelo usuario
    INSECURE = "insecure"      # NAO valida (aceita cert autoassinado) — risco MITM


class NodeType:
    """Tipos de no (modo DPN) CONFIRMADOS no frontend da unidade."""

    SMART = "smart"          # rota inteligente
    FULL = "full"            # rota completa (requer tunnelCode)
    DISABLED = "disabled"    # DPN desligado
    BLACK_HOLE = "blackHole" # sem internet

    ALL = (SMART, FULL, DISABLED, BLACK_HOLE)

    LABELS = {
        SMART: "Smart (rota inteligente)",
        FULL: "Full (rota completa)",
        DISABLED: "Desligado",
        BLACK_HOLE: "Black Hole (sem internet)",
    }

    # rotulos usados no Access Control (por dispositivo)
    SHORT_LABELS = {
        SMART: "Smart Routing",
        FULL: "Full Routing",
        DISABLED: "Disabled",
        BLACK_HOLE: "Black Hole",
    }


class NodeLbMode:
    """Node Selection Mode (modo de selecao de no do tunnel)."""

    NONE = "none"       # Regular
    DOMAIN = "domain"   # Domain-Aware
    RANDOM = "random"   # Random
    RR = "rr"           # Round Robin

    ALL = (NONE, DOMAIN, RANDOM, RR)

    LABELS = {
        NONE: "Regular",
        DOMAIN: "Domain-Aware",
        RANDOM: "Random",
        RR: "Round Robin",
    }


@dataclass
class Device:
    """Unidade cadastrada (persistente)."""

    nickname: str
    address: str                      # host/IP de gerencia (NUNCA fixar 34.34.34.34)
    port: int = 443
    tls_mode: TLSMode = TLSMode.VERIFY
    ca_bundle_path: str | None = None  # so quando tls_mode == CA_BUNDLE
    username: str = "admin"
    notes: str = ""
    id: int | None = None              # preenchido pelo storage
    # --- agente / identidade ---
    mac_reference: str = ""            # MAC de referencia (DPN 1/2/3)
    android_serial: str = ""           # serial ADB selecionado no wizard
    agent_id: str | None = None        # agente Android pareado
    expected_dpn_id: str | None = None  # ID fisico vinculado explicitamente
    last_observed_id: str | None = None  # ultimo ID observado (nao substitui o esperado)
    last_observed_at: float = 0.0        # quando o ID foi observado (0 = nunca)
    last_observed_origin: str = ""       # origem: agent:<id> | direct | heartbeat
    bind_direct: bool = True           # heranca; ver transport_mode
    transport_mode: str = "direct"     # "direct" (PC->AtomOS) | "agent" (Android->AtomOS)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def base_url(self) -> str:
        host = self.address.strip().rstrip("/")
        if "://" in host:
            return host
        return f"https://{host}:{self.port}"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["tls_mode"] = self.tls_mode.value
        return d


@dataclass
class DeviceInfo:
    """Estado/informacoes de runtime obtidas da unidade (via API)."""

    online: bool = False
    last_seen: float | None = None
    last_error: str | None = None
    model: str | None = None
    version: str | None = None
    management_ip: str | None = None
    node_type: str | None = None
    exit_ip: str | None = None
    connected_devices: list[dict] = field(default_factory=list)


@dataclass
class ActionResult:
    """Resultado de uma acao executada contra uma unidade."""

    device_nickname: str
    action: str
    ok: bool
    message: str = ""
    data: dict | None = None
    ts: float = field(default_factory=time.time)


class ActionStatus(str, enum.Enum):
    OK = "ok"
    ERROR = "error"
    UNCONFIRMED = "unconfirmed"


@dataclass
class ActionLog:
    """Registro de historico local (nunca guarda segredos)."""

    device_id: int | None
    device_nickname: str
    action: str
    status: str          # ActionStatus
    message: str         # ja sanitizado
    ts: float = field(default_factory=time.time)
    id: int | None = None


class IdentityState:
    """Estados de agente/identidade exibidos por cadastro."""

    NOT_CONFIGURED = "Não configurado"
    AGENT_UNAVAILABLE = "Agente indisponível"
    AGENT_CONNECTED = "Agente conectado"
    ATOMOS_UNREACHABLE = "AtomOS inacessível"
    AUTH_FAILED = "Falha de autenticação"
    IDENTITY_UNLINKED = "Identidade não vinculada"
    ID_VALIDATED = "ID validado"
    ID_MISMATCH = "ID divergente"
    CONFLICT = "Conflito: mesma DPN"
