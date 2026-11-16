#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault_injection.py — SLN V2 (FASE 19/20): valida OWNERSHIP/RESTART com STALE CONTROLADO.

Estratégia: SEGURA (suspender) o bot de um device -> o heartbeat congela -> o supervisor externo
(watchdog_run.py, artefato real) detecta WORKER_STALE, faz o restart TRANSACIONAL (kill verificado
da árvore + spawn) e incrementa a GERAÇÃO. Verificamos a invariante a cada passo:
    bot_process_count(device) == 1  e  logcat_process_count(device) == 1.

Uso:
  python fault_injection.py --devices 192.168.1.124:5555 --rounds 3
  python fault_injection.py --devices <5 devs> --rounds 2

Nunca deve existir >1 worker por device. Se detectar duplicata, PARA e preserva evidência.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid

import sln_proc
import watchdog as WD

BASE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\joao_\AppData\Local\Programs\Python\Python313\python.exe"
BOT = os.path.join(BASE, "main_sln_v2.py")
WD_RUN = os.path.join(BASE, "watchdog_run.py")
LOGS = os.path.join(BASE, "logs")
BENCH = os.path.join(BASE, "benchmarks")
HB = os.path.join(BASE, "heartbeats")
EV = os.path.join(BENCH, "fault_injection_evidence")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(LOGS, "fault_injection.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def clean_device(dev):
    res = sln_proc.terminate_worker_verified(dev, timeout=10, kill_logcat=True)
    try:
        os.remove(WD.heartbeat_path(HB, dev))
    except Exception:
        pass
    return res


def launch(dev, profile, ads=0, runtime_s=1800):
    env = os.environ.copy()
    env["SLN_CONFIG_OVERLAY"] = profile
    env["SLN_TEST_MAX_RUNTIME_S"] = str(runtime_s)
    env.pop("SLN_TEST_MAX_CYCLES", None)
    inst = uuid.uuid4().hex
    env["SLN_WORKER_INSTANCE_ID"] = inst
    env["SLN_WORKER_GENERATION"] = "1"
    logf = os.path.join(LOGS, "bench_" + dev.replace(":", "_").replace(".", "_") + ".log")
    f = open(logf, "a", encoding="utf-8")
    p = subprocess.Popen([PY, BOT, dev, "0", str(ads)], env=env, cwd=BASE,
                         stdout=f, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return p, inst, logf


def read_hb(dev):
    return WD.read_heartbeat(WD.heartbeat_path(HB, dev))


def invariant(dev, phase, tries=10):
    """Verifica bots==1 e logcat==1 com estabilização (tolera transiente pós-spawn).
    Duplicata (bots>1) é violação imediata."""
    last = None
    for _ in range(tries):
        bots = sln_proc.bot_processes_for_device(dev)
        lc = sln_proc.logcat_processes_for_device(dev)
        last = (bots, lc)
        if len(bots) > 1:
            break
        if len(bots) == 1 and len(lc) == 1:
            log("  INV %-18s bots=%d logcat=%d -> OK" % (phase, len(bots), len(lc)))
            return True
        time.sleep(1.0)
    bots, lc = last
    log("  INV %-18s bots=%d logcat=%d -> VIOLADA" % (phase, len(bots), len(lc)))
    os.makedirs(EV, exist_ok=True)
    with open(os.path.join(EV, "violation_%s.json" % time.strftime("%Y%m%d_%H%M%S")), "w") as f:
        json.dump({"device": dev, "phase": phase, "bots": bots, "logcat": lc}, f, indent=2)
    return False


def wait_stable_pid(dev, timeout=20.0):
    """Espera um heartbeat com PID vivo e pertencente ao device (device estabilizado)."""
    import psutil
    tw = time.time()
    while time.time() - tw < timeout:
        hb = read_hb(dev) or {}
        pid = hb.get("pid")
        if pid and psutil.pid_exists(pid) and pid in sln_proc.bot_processes_for_device(dev):
            return pid, int(hb.get("generation") or 1)
        time.sleep(1.0)
    hb = read_hb(dev) or {}
    return hb.get("pid"), int(hb.get("generation") or 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", nargs="+", required=True)
    ap.add_argument("--profile", default="config_c60_sln_v2.json")
    ap.add_argument("--stale-s", type=float, default=20.0)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--startup-wait", type=float, default=90.0)
    args = ap.parse_args()
    devs = list(dict.fromkeys(args.devices))
    os.makedirs(HB, exist_ok=True)
    os.makedirs(EV, exist_ok=True)

    log("=== FAULT INJECTION devices=%d rounds=%d stale_s=%.0f ===" % (len(devs), args.rounds, args.stale_s))

    # 1) limpa e lança 1 worker por device
    registry = {}
    for dev in devs:
        clean_device(dev)
        p, inst, logf = launch(dev, args.profile)
        registry[dev] = {"pid": p.pid, "instance_id": inst, "generation": 1, "log": logf}
        log("[%s] launched pid=%s inst=%s gen=1" % (dev, p.pid, inst[:8]))

    # 2) manifest + supervisor externo (artefato real)
    ts = time.strftime("%Y%m%d_%H%M%S")
    mf = os.path.join(BENCH, "manifest_fault_%s.json" % ts)
    with open(mf, "w", encoding="utf-8") as f:
        json.dump({"profile": args.profile, "device_count": len(devs),
                   "bots": [dict(device=d, **{k: registry[d][k] for k in ("pid", "log", "instance_id", "generation")})
                            for d in devs]}, f, indent=2)
    wd_log = open(os.path.join(LOGS, "fault_watchdog.log"), "a", encoding="utf-8")
    sup = subprocess.Popen([PY, WD_RUN, "--manifest", mf, "--profile", args.profile,
                            "--stale-s", str(args.stale_s), "--max", "10", "--interval", "3",
                            "--post-restart-grace", "25", "--startup-grace", "20",
                            "--no-preflight"],
                           cwd=BASE, stdout=wd_log, stderr=subprocess.STDOUT,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    log("watchdog_run.py PID=%s stale_s=%.0f" % (sup.pid, args.stale_s))

    # 3) espera o 1º heartbeat (gen=1)
    t0 = time.time()
    for dev in devs:
        while time.time() - t0 < args.startup_wait:
            hb = read_hb(dev)
            if hb and hb.get("generation") == 1:
                break
            time.sleep(1.0)
        log("[%s] heartbeat gen=%s op=%s" % (dev, (read_hb(dev) or {}).get("generation"),
                                             (read_hb(dev) or {}).get("current_operation")))
    for dev in devs:
        invariant(dev, "pre-injection")

    # 4) rodadas de stale controlado (suspender UM device por rodada, round-robin)
    aborted = False
    for r in range(args.rounds):
        dev = devs[r % len(devs)]
        old_pid, old_gen = wait_stable_pid(dev)
        log("[round %d/%d] %s suspend pid=%s gen=%d" % (r + 1, args.rounds, dev, old_pid, old_gen))
        try:
            import psutil
            psutil.Process(old_pid).suspend()
        except Exception as e:
            log("  suspend falhou: %s" % e)
            continue
        # espera restart: gen incrementa E bots continua ==1
        ok = False
        tw = time.time()
        while time.time() - tw < max(90, args.stale_s * 6):
            hb2 = read_hb(dev) or {}
            g2 = int(hb2.get("generation") or 1)
            if g2 > old_gen and hb2.get("pid") != old_pid:
                ok = True
                break
            if len(sln_proc.bot_processes_for_device(dev)) > 1:
                log("  !! DUPLICATE durante restart — abortando")
                aborted = True
                break
            time.sleep(1.0)
        hb2 = read_hb(dev) or {}
        log("  -> gen=%s pid=%s restarted=%s" % (hb2.get("generation"), hb2.get("pid"), ok))
        if aborted or not invariant(dev, "round%d" % (r + 1)):
            aborted = True
            break

    # 5) checks finais
    final_ok = True
    for dev in devs:
        final_ok &= invariant(dev, "final")
        hb = read_hb(dev) or {}
        if int(hb.get("generation") or 0) <= 1:
            log("[%s] AVISO: geração final=%s (sem restart observado)" % (dev, hb.get("generation")))

    # 6) teardown
    sup.terminate()
    time.sleep(2)
    zeros = True
    for dev in devs:
        clean_device(dev)
        zeros &= (sln_proc.bot_processes_for_device(dev) == [] and
                  sln_proc.logcat_processes_for_device(dev) == [])
    log("teardown: zero bots/logcat=%s" % zeros)
    log("RESULT: %s" % ("FAIL" if (aborted or not final_ok or not zeros) else "PASS"))
    sys.exit(1 if (aborted or not final_ok or not zeros) else 0)


if __name__ == "__main__":
    main()
