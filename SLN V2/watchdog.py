#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watchdog.py — SLN V2 (FASE 2 / v2 — OWNERSHIP + GENERATIONS)

Supervisão EXTERNA por device. O bot publica um heartbeat barato (JSON atômico) a cada N s.

Invariante: UM DEVICE = NO MÁXIMO UM BOT ATIVO. O supervisor possui um REGISTRY da sessão
(device -> {pid, instance_id, generation, state}) que é a AUTORIDADE; o arquivo de heartbeat
é só sinal. Heartbeat de uma GERAÇÃO/INSTÂNCIA antiga é ignorado (STALE_HEARTBEAT_GENERATION).

Distinção: `heartbeat_alive` (thread ainda escreve) vs `functional_progress` (o loop avançou). Um
worker vivo preso em dump_hierarchy por dezenas de segundos é STALE — e é isso que detectamos.

Tudo testável offline (write/read/stale/Supervisor com restart injetado).
"""
import json
import os
import time

RUNNING = "RUNNING"
BACKOFF = "BACKOFF"
RECOVERING = "RECOVERING"
DISCONNECTED = "DISCONNECTED"
QUARANTINED = "QUARANTINED"
STARTING = "STARTING"
STALE_HEARTBEAT_GENERATION = "STALE_HEARTBEAT_GENERATION"


def heartbeat_path(hb_dir, device):
    return os.path.join(hb_dir, "hb_" + device.replace(":", "_").replace(".", "_") + ".json")


def write_heartbeat(path, data):
    """Escrita ATÔMICA (tmp + os.replace). Nunca levanta exceção."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def read_heartbeat(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


class Supervisor:
    """Supervisor externo. `restart_fn(device)` deve reiniciar o worker daquele device de forma
    TRANSACIONAL (verificar PID antigo morto antes de spawnar). `now_fn` = relógio monotônico.

    registry[dev] = {pid, instance_id, generation, state, restarts, last_pid, quarantine_reason}
    """

    def __init__(self, hb_dir, stale_s=120.0, restart_max=3, restart_fn=None, now_fn=None,
                 log_fn=print, startup_grace_s=0.0, post_restart_grace_s=0.0):
        self.hb_dir = hb_dir
        self.stale_s = float(stale_s)
        self.restart_max = int(restart_max)
        self.restart_fn = restart_fn
        self.now_fn = now_fn or time.monotonic
        self.log = log_fn
        self.startup_grace_s = float(startup_grace_s)
        self.post_restart_grace_s = float(post_restart_grace_s)
        self.state = {}        # device -> último estado classificado
        self.restarts = {}     # device -> contagem
        self.registry = {}     # device -> ownership da sessão atual
        self._started_at = self.now_fn()
        self._grace_until = {}   # device -> monotônico até quando NÃO agir
        self._restarting = set()  # device -> restart em andamento (coalescing)

    # ---------- registry / ownership ----------
    def register(self, device, pid=None, instance_id=None, generation=None, state=RUNNING):
        r = self.registry.setdefault(device, {})
        if pid is not None:
            r["pid"] = int(pid)
        if instance_id is not None:
            r["instance_id"] = instance_id
        if generation is not None:
            r["generation"] = int(generation)
        r.setdefault("restarts", 0)
        r["state"] = state
        return r

    def expected_generation(self, device):
        return self.registry.get(device, {}).get("generation")

    # ---------- classificação ----------
    def _path(self, device):
        return heartbeat_path(self.hb_dir, device)

    def _in_grace(self, device, now):
        if device in self._grace_until and now < self._grace_until[device]:
            return True
        if (now - self._started_at) < self.startup_grace_s:
            return True
        return False

    def classify(self, device, hb, now):
        """Estado a partir do heartbeat. Pura (sem I/O). Ignora geração antiga."""
        if hb is None:
            return DISCONNECTED
        # geração/instância: se o registry conhece uma geração, só aceita o heartbeat dela
        exp = self.expected_generation(device)
        if exp is not None and hb.get("generation") is not None and int(hb["generation"]) != int(exp):
            return STALE_HEARTBEAT_GENERATION
        if hb.get("state") == QUARANTINED:
            return QUARANTINED
        prog = hb.get("last_progress")
        if prog is None:
            return DISCONNECTED
        if (now - prog) > self.stale_s:
            return RECOVERING
        st = hb.get("state")
        return st if st in (RUNNING, BACKOFF, RECOVERING, DISCONNECTED) else RUNNING

    def poll(self, devices):
        """Verifica todos os devices; age nos STALE sem bloquear os demais. Retorna lista de ações.
        Respeita grace (startup/pós-restart) e coalesce restarts concorrentes do MESMO device."""
        now = self.now_fn()
        actions = []
        for dev in devices:
            if dev in self._restarting:
                continue
            hb = read_heartbeat(self._path(dev))
            cur = self.classify(dev, hb, now)
            prev = self.state.get(dev)
            self.state[dev] = cur
            if cur == QUARANTINED:
                continue
            if cur == STALE_HEARTBEAT_GENERATION:
                self.log(f"[WATCHDOG] {dev} STALE_HEARTBEAT_GENERATION (hb de geração antiga) — ignorado")
                continue
            if cur in (RECOVERING, DISCONNECTED, STARTING):
                if self._in_grace(dev, now):
                    continue
                n = self.restarts.get(dev, 0) + 1
                self.restarts[dev] = n
                if n > self.restart_max:
                    self.state[dev] = QUARANTINED
                    r = self.registry.setdefault(dev, {})
                    r["state"] = QUARANTINED
                    r["quarantine_reason"] = "WORKER_STALE"
                    self.log(f"[WATCHDOG] {dev} QUARANTINED após {n-1} reinícios (WORKER_STALE)")
                    actions.append((dev, "quarantine", prev))
                else:
                    self.log(f"[WATCHDOG] {dev} WORKER_STALE (state={cur}) -> restart {n}/{self.restart_max}")
                    self._restarting.add(dev)
                    try:
                        if self.restart_fn is not None:
                            self.restart_fn(dev)
                    except Exception as e:
                        self.log(f"[WATCHDOG] restart_fn erro {dev}: {type(e).__name__}: {e}")
                    finally:
                        self._restarting.discard(dev)
                        if self.post_restart_grace_s:
                            self._grace_until[dev] = self.now_fn() + self.post_restart_grace_s
                    actions.append((dev, "restart", prev))
        return actions

    def stale_devices(self, devices):
        now = self.now_fn()
        out = []
        for dev in devices:
            hb = read_heartbeat(self._path(dev))
            if self.classify(dev, hb, now) in (RECOVERING, DISCONNECTED):
                out.append(dev)
        return out
