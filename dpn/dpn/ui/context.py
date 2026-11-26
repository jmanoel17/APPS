"""Contexto explicito de selecao/requisicao para invalidar respostas assincronas.

Nao usar identidade de objeto (self.device is dev): heartbeats trocam a instancia
do mesmo cadastro e descartariam respostas validas. Aqui o contexto e
(device_id, chave de configuracao/transporte) e cada requisicao recebe um token.
"""
from __future__ import annotations

import threading


class SelectionContext:
    def __init__(self):
        self._lock = threading.Lock()
        self.device_id: int | None = None
        self.key: tuple | None = None
        self.gen = 0
        self._req = 0

    def select(self, device_id: int | None, key: tuple | None = None) -> bool:
        """Seleciona um cadastro. Retorna True se o contexto (id/key) mudou."""
        with self._lock:
            changed = (device_id != self.device_id) or (key != self.key)
            self.device_id = device_id
            self.key = key
            if changed:
                self.gen += 1
            return changed

    def token(self) -> tuple:
        with self._lock:
            self._req += 1
            return (self.device_id, self.key, self.gen, self._req)

    def valid(self, token: tuple) -> bool:
        with self._lock:
            did, key, gen, _ = token
            return did == self.device_id and key == self.key and gen == self.gen
