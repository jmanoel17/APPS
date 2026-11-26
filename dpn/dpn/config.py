"""Configuracao da aplicacao (arquivo config.json opcional, sem segredos)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


APP_DIR_NAME = "dpn"


def default_data_dir() -> str:
    """Diretorio de dados do usuario (perfil)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


@dataclass
class Config:
    data_dir: str = field(default_factory=default_data_dir)
    db_path: str | None = None
    endpoints_path: str | None = None     # JSON com endpoints confirmados (opcional)
    request_timeout: float = 10.0         # segundos
    connect_timeout: float = 5.0
    discovery_timeout: float = 1.2        # timeout por host no scan
    discovery_max_hosts: int = 254
    log_requests: bool = False            # nunca loga payload sensivel
    agent_host: str = "0.0.0.0"           # bind do servidor WSS de agentes
    agent_port: int = 8765                # porta unica a liberar no firewall
    monitor_interval: int = 60            # segundos do monitoramento periodico

    def resolve(self) -> None:
        if self.db_path is None:
            self.db_path = os.path.join(self.data_dir, "dpn.db")
        if self.endpoints_path is None:
            self.endpoints_path = os.path.join(self.data_dir, "endpoints.json")

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        cfg = cls()
        candidates = []
        if path:
            candidates.append(path)
        candidates.append(os.path.join(default_data_dir(), "config.json"))
        for p in candidates:
            if p and os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for k, v in data.items():
                        if hasattr(cfg, k) and v is not None:
                            setattr(cfg, k, v)
                except (OSError, json.JSONDecodeError):
                    pass
        cfg.resolve()
        return cfg

    def ensure_dirs(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
