#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sln_proc.py — SLN V2 (OWNERSHIP / PROCESS SUPERVISION)

Invariante: UM DEVICE = NO MÁXIMO UM BOT SLN ATIVO.

Identificação EXATA (não substring genérica):
  - bot: basename(argv) == "main_sln_v2.py"  E  argv seguinte == device (exato)
  - logcat child: processo adb cujo argv contém o device e 'logcat'

Helpers puros de descoberta + terminação verificada (árvore de processos).
Windows: mata descendentes (evita adb logcat/cmd órfãos). NÃO mata o adb server global.
"""
import os
import time

try:
    import psutil
except Exception:
    psutil = None

BOT_SCRIPT = "main_sln_v2.py"


def _iter_procs():
    if psutil is None:
        return []
    try:
        return list(psutil.process_iter(["pid", "name", "cmdline", "ppid"]))
    except Exception:
        return []


def bot_processes_for_device(device):
    """PIDs dos bots que rodam ESTE device (match exato de script + device)."""
    out = []
    for p in _iter_procs():
        cl = p.info.get("cmdline") or []
        for i, a in enumerate(cl):
            if os.path.basename(a).lower() == BOT_SCRIPT:
                if i + 1 < len(cl) and cl[i + 1] == device:
                    out.append(p.info["pid"])
                break
    return out


def logcat_processes_for_device(device):
    """PIDs de `adb ... -s <device> ... logcat ...` (o stream persistente daquele device)."""
    out = []
    for p in _iter_procs():
        cl = p.info.get("cmdline") or []
        if not cl:
            continue
        joined = " ".join(cl)
        if "logcat" in joined and device in cl:
            out.append(p.info["pid"])
    return out


def all_bot_pids():
    out = []
    for p in _iter_procs():
        cl = p.info.get("cmdline") or []
        if any(os.path.basename(a).lower() == BOT_SCRIPT for a in cl):
            out.append(p.info["pid"])
    return out


def orphans(expected_bot_pids):
    """Bots vivos fora do conjunto esperado (registry da sessão)."""
    exp = set(expected_bot_pids)
    return [pid for pid in all_bot_pids() if pid not in exp]


def _terminate_tree(pid, timeout=10.0):
    """Termina o processo e TODOS os descendentes. Retorna True se nada sobrou."""
    if psutil is None:
        return False
    try:
        proc = psutil.Process(pid)
    except Exception:
        return True
    try:
        children = proc.children(recursive=True)
    except Exception:
        children = []
    for c in children:
        try:
            c.terminate()
        except Exception:
            pass
    try:
        proc.terminate()
    except Exception:
        pass
    gone, alive = psutil.wait_procs(children + [proc], timeout=timeout)
    for a in alive:
        try:
            a.kill()
        except Exception:
            pass
    psutil.wait_procs(alive, timeout=5.0)
    return True


def terminate_worker_verified(device, timeout=10.0, kill_logcat=True):
    """Encerra o(s) bot(s) e o(s) logcat(s) DESTE device e confirma ZERO.

    Retorna dict {ok, killed_pids, remaining_bots, remaining_logcat}."""
    pids = bot_processes_for_device(device)
    logcats = logcat_processes_for_device(device) if kill_logcat else []
    for pid in set(pids + logcats):
        _terminate_tree(pid, timeout=timeout)
    # espera e re-verifica
    for _ in range(int(timeout) + 2):
        rb = bot_processes_for_device(device)
        rl = logcat_processes_for_device(device) if kill_logcat else []
        if not rb and not rl:
            return {"ok": True, "killed_pids": sorted(set(pids + logcats)),
                    "remaining_bots": [], "remaining_logcat": []}
        for pid in set(rb + rl):
            try:
                psutil.Process(pid).kill()
            except Exception:
                pass
        time.sleep(1.0)
    return {"ok": False, "killed_pids": sorted(set(pids + logcats)),
            "remaining_bots": bot_processes_for_device(device),
            "remaining_logcat": logcat_processes_for_device(device) if kill_logcat else []}


def child_logcat_pid(bot_pid):
    """PID do(s) process(os) logcat filho(s) direto(s) do bot (telemetria/limpeza)."""
    if psutil is None:
        return []
    try:
        return [c.pid for c in psutil.Process(bot_pid).children(recursive=True)
                if "logcat" in " ".join(c.cmdline() or [])]
    except Exception:
        return []
