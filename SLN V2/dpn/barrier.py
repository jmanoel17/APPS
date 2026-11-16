#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/barrier.py — SLN V2: barreira de DRAINING baseada em SINAIS de worker (arquivos) + liveness.

Um worker publica `state/dpn/workers/<safe>.json` = {safe:true, drain_epoch, last_seen} quando
chega ao gate ENTRE ciclos enquanto a DPN está em estado de bloqueio.

`all_safe(timeout)` resolve quando:
  - o worker publicou SAFE para o `drain_epoch` atual e o sinal está fresco; OU
  - o worker NÃO está vivo (liveness/heartbeat expirou) — não trava indefinidamente.

O epoch invalida sinais SAFE de um DRAINING anterior. `reset()` é no-op (epoch-based).
"""
import json
import os
import time

from . import state as S


def safe_signal_path(state_dir, device):
    return os.path.join(state_dir, "workers", S.safe_id(device) + ".json")


def _read(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _default_alive(device):
    """Liveness real: existe bot vivo para o device (match exato via sln_proc)."""
    try:
        import sln_proc
        return bool(sln_proc.bot_processes_for_device(device))
    except Exception:
        return True  # sem psutil/sln_proc: assume vivo (conservador)


class SignalBarrier:
    def __init__(self, state_dir, dpn_id, devices, alive_fn=None, now_fn=time.time,
                 poll=0.1, stale_s=120.0):
        self.state_dir = state_dir
        self.dpn_id = dpn_id
        self.devices = list(devices)
        self.alive_fn = alive_fn or _default_alive
        self.now_fn = now_fn
        self.poll = float(poll)
        self.stale_s = float(stale_s)

    def reset(self):
        # epoch-based: nada a limpar (sinais de epoch antigo são ignorados)
        pass

    def publish_safe(self, device, instance_id=None, cycle_id=None):
        st = S.read_dpn_state(self.state_dir, self.dpn_id)
        epoch = int(st.get("drain_epoch", 0))
        p = safe_signal_path(self.state_dir, device)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        S.atomic_write_json(p, {"device": device, "safe": True, "drain_epoch": epoch,
                                "instance_id": instance_id, "cycle_id": cycle_id,
                                "last_seen": self.now_fn()})

    def _epoch(self):
        try:
            return int(S.read_dpn_state(self.state_dir, self.dpn_id).get("drain_epoch", 0))
        except Exception:
            return 0

    def _resolved(self, device, epoch, now):
        sig = _read(safe_signal_path(self.state_dir, device))
        if sig and sig.get("safe") and int(sig.get("drain_epoch", -1)) == epoch \
                and (now - float(sig.get("last_seen", 0))) <= self.stale_s:
            return True
        try:
            if not self.alive_fn(device):
                return True  # worker desapareceu -> não bloqueia
        except Exception:
            pass
        return False

    def all_safe(self, timeout_s):
        epoch = self._epoch()
        deadline = self.now_fn() + float(timeout_s)
        while True:
            now = self.now_fn()
            if all(self._resolved(d, epoch, now) for d in self.devices):
                return True
            if now >= deadline:
                return True  # timeout: NÃO trava indefinidamente
            time.sleep(self.poll)

    def snapshot(self):
        epoch = self._epoch()
        now = self.now_fn()
        return {d: self._resolved(d, epoch, now) for d in self.devices}
