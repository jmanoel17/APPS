#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
benchmark_run.py — Lança um benchmark de N devices (SLN V2).

Faz:
  1. Seleciona devices: arquivo de shard (static sharding) OU descoberta adb (--count).
  2. Lança benchmark_host.py em paralelo (mede CPU/RAM do host).
  3. Lança 1 bot por device com overlay benchmark + guards de teste.

Guards por bot:
  - argv[3] = limite de anúncios (TEST_LIMIT_ADS); 0 = ilimitado
  - SLN_TEST_MAX_RUNTIME_S = cap de tempo (wall-clock)
  - SLN_CONFIG_OVERLAY = perfil de benchmark (default config_benchmark_sln_v2.json)
  - SLN_HOST_ID / SLN_DEVICE_GROUP = identificação do host (FASE 10)

Uso:
  python benchmark_run.py --count 10
  python benchmark_run.py --devices 192.168.1.100:5555 ... --ads 6 --minutes 30
  python benchmark_run.py --shard hostA --ads 0 --minutes 60      # lê shards/hostA.txt
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid

import sharding

PY = r"C:\Users\joao_\AppData\Local\Programs\Python\Python313\python.exe"
BASE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(BASE, "main_sln_v2.py")
HOST = os.path.join(BASE, "benchmark_host.py")
DEFAULT_PROFILE = "config_benchmark_sln_v2.json"
LOGS = os.path.join(BASE, "logs")
BENCH = os.path.join(BASE, "benchmarks")


def adb_devices():
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10).stdout
    return [p[0] for ln in out.splitlines()[1:]
            if (p := ln.split()) and len(p) == 2 and p[1] == "device" and p[0].endswith(":5555")]


def read_flags(profile):
    """Lê as flags de escala do perfil (overlay) para registrar no manifest."""
    flags = {"persistent_logcat": False, "adaptive_tick": False}
    try:
        p = profile if os.path.isabs(profile) else os.path.join(BASE, profile)
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        flags["persistent_logcat"] = bool(d.get("persistent_logcat_enabled", False))
        flags["adaptive_tick"] = bool(d.get("adaptive_tick_enabled", False))
    except Exception:
        pass
    return flags


def launch_bot(device, ads, runtime_s, profile, host_id, generation=1, instance_id=None):
    env = os.environ.copy()
    env["SLN_CONFIG_OVERLAY"] = profile
    env["SLN_TEST_MAX_RUNTIME_S"] = str(runtime_s)
    env.pop("SLN_TEST_MAX_CYCLES", None)
    if host_id:
        env["SLN_HOST_ID"] = host_id
    inst = instance_id or uuid.uuid4().hex
    env["SLN_WORKER_INSTANCE_ID"] = inst
    env["SLN_WORKER_GENERATION"] = str(generation)
    log = os.path.join(LOGS, "bench_" + device.replace(":", "_").replace(".", "_") + ".log")
    f = open(log, "a", encoding="utf-8")
    p = subprocess.Popen([PY, BOT, device, "0", str(ads)], env=env, cwd=BASE,
                         stdout=f, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return p, log, inst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", nargs="*", default=None)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--ads", type=int, default=6, help="anúncios por bot (0 = ilimitado)")
    ap.add_argument("--minutes", type=int, default=30, help="cap de tempo por bot")
    ap.add_argument("--shard", default=None, help="HOST_ID: lê shards/<HOST_ID>.txt")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="perfil (overlay) do benchmark")
    args = ap.parse_args()

    os.makedirs(LOGS, exist_ok=True)
    os.makedirs(BENCH, exist_ok=True)

    host_id = args.shard or sharding.host_id()
    if args.devices:
        devs = list(dict.fromkeys(args.devices))
    elif host_id and sharding.load_shard(BASE, host_id):
        devs = sharding.load_shard(BASE, host_id)
        print(f"[BENCH] shard '{host_id}' -> {len(devs)} devices", flush=True)
    else:
        devs = adb_devices()[:args.count]
    devs = [d for d in devs if d]
    if not devs:
        print("Nenhum device online.")
        sys.exit(2)

    flags = read_flags(args.profile)
    print(f"[BENCH] {len(devs)} devices | ads/bot={args.ads} | cap={args.minutes}min | "
          f"host_id={host_id or '-'} | profile={args.profile} | persistent_logcat={flags['persistent_logcat']} "
          f"adaptive_tick={flags['adaptive_tick']}", flush=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    host_out = os.path.join(BENCH, f"host_{len(devs)}device_{ts}.csv")
    host_env = os.environ.copy()
    host_env["SLN_HOST_ID"] = host_id or ""
    host_p = subprocess.Popen([PY, HOST, "--interval", "5", "--duration", str(args.minutes * 60),
                               "--out", host_out, "--host-id", host_id or "",
                               "--device-count", str(len(devs)), "--profile", args.profile],
                              env=host_env, cwd=BASE,
                              stdout=open(os.path.join(LOGS, "benchmark_host.log"), "a"),
                              stderr=subprocess.STDOUT,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    print(f"[BENCH] benchmark_host PID {host_p.pid} -> {host_out}", flush=True)

    runtime_s = args.minutes * 60
    manifest = []
    for d in devs:
        p, log, inst = launch_bot(d, args.ads, runtime_s, args.profile, host_id)
        manifest.append({"device": d, "pid": p.pid, "log": log,
                         "instance_id": inst, "generation": 1})
        print(f"[BENCH] bot {d} PID {p.pid} -> {log}", flush=True)
        time.sleep(1.0)

    mf = os.path.join(BENCH, f"manifest_{ts}.json")
    with open(mf, "w") as f:
        json.dump({"host_out": host_out, "host_pid": host_p.pid, "host_id": host_id,
                   "profile": args.profile, "flags": flags, "device_count": len(devs),
                   "bots": manifest, "started": ts}, f, indent=2)
    print(f"[BENCH] manifest -> {mf}", flush=True)
    print("[BENCH] LANCADO. Monitore: Get-Content logs\\bench_*.log -Wait", flush=True)


if __name__ == "__main__":
    main()
