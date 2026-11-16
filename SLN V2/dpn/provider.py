#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/provider.py — SLN V2: abstração do provedor DPN.

O coordinator NUNCA sabe COMO a DPN rotaciona; só chama a interface.
A implementação real (controller.type="deeper_connect") fica em provider_deeper.py (Etapa E).
Aqui: ABC + FakeDPNProvider determinístico para testes offline (sem ADB, sem rede).
"""


class DPNProvider:
    """Interface mínima do provedor de DPN/IP."""

    @classmethod
    def from_config(cls, controller_cfg=None):
        return cls()

    def get_public_ip(self):
        raise NotImplementedError

    def get_node_id(self):
        raise NotImplementedError

    def rotate_ip(self, mode="REGULAR"):
        raise NotImplementedError

    def reset_node(self):
        raise NotImplementedError

    def health(self):
        raise NotImplementedError

    def close(self):
        pass


class FakeDPNProvider(DPNProvider):
    """Provedor fake determinístico.

    sequence=["10.0.0.1","10.0.0.2","10.0.0.2","10.0.0.3"] — cada rotate_ip() avança a sequência
    (para no último, simulando "IP repetido"/esgotamento). reset_node() troca o node e volta ao
    início da sequência (permite testar NODE_HISTORY != GLOBAL_IP_HISTORY).

    `rotate_noop=True` simula uma rotação que NÃO muda o IP (para testar timeout/verify).
    """

    def __init__(self, sequence=None, node_id="node_fake", health_ok=True, rotate_noop=False,
                 reset_sequence=None):
        self._seq = list(sequence) if sequence else ["10.0.0.1"]
        self._i = 0
        self._node_id = node_id
        self._health_ok = bool(health_ok)
        self._rotate_noop = bool(rotate_noop)
        self._reset_seq = list(reset_sequence) if reset_sequence else None
        self.rotate_calls = 0
        self.reset_calls = 0

    # ---- leitura ----
    def get_public_ip(self):
        if not self._seq:
            return None
        return self._seq[min(self._i, len(self._seq) - 1)]

    def get_node_id(self):
        return self._node_id

    def health(self):
        return self._health_ok

    # ---- ações ----
    def rotate_ip(self, mode="REGULAR"):
        self.rotate_calls += 1
        if self._rotate_noop:
            return True
        if self._i + 1 < len(self._seq):
            self._i += 1
        return True

    def reset_node(self):
        self.reset_calls += 1
        self._node_id = "%s_r%d" % (self._node_id.split("_r")[0], self.reset_calls)
        if self._reset_seq is not None:
            self._seq = list(self._reset_seq)
        self._i = 0
        return True

    # ---- helpers de teste ----
    def set_sequence(self, sequence):
        self._seq = list(sequence)
        self._i = 0

    def current_index(self):
        return self._i

    @classmethod
    def from_config(cls, controller_cfg=None):
        c = controller_cfg or {}
        return cls(sequence=c.get("sequence"), node_id=c.get("node_id", "node_fake"),
                   health_ok=bool(c.get("health_ok", True)), rotate_noop=bool(c.get("rotate_noop", False)),
                   reset_sequence=c.get("reset_sequence"))
