#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lifecycle_30.py — SLN V2 (FASE 21): validação de LIFECYCLE com 30 devices, watchdog ON.

Objetivo NÃO é capacidade: é provar que
  duplicates=0, orphans=0 e NÃO há crescimento progressivo de bot/logcat processes.

Lança benchmark_run (30 devices) + watchdog_run (artefato real) e amostra a invariante.
"""
import argparse
import json
import os
import subprocess
import sys
import time

import sln_proc
import watchdog as WD

BASE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\joao_\AppData\Local\Programs\Python\Python313\python.exe"
BENCH_RUN = os.path.join(BASE, "benchmark_run.py")
WD_RUN = os.path.join(BASE, "watchdog_run.py")
BENCH = os.path.join(BASE, "benchmarks")
HB = os.path.join(BASE, "heartbeats")
LOGS = os.path.join(BASE, "logs")


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def newest_manifest(after_ts):
    best = None
    for f in os.listdir(BENCH):
        if f.startswith("manifest_") and f.endswith(".json"):
            p = os.path.join(BENCH, f)
            if os.path.getmtime(p) >= after_ts - 2 and (best is None or os.path.getmtime(p) > os.path.getmtime(best)):
                best = p
    return best


def metrics(devs, expected_pids):
    bots = sln_proc.all_bot_pids()
    seen = {d: sln_proc.bot_processes_for_device(d) for d in devs}
    dup = sum(1 for d in devs if len(seen[d]) > 1)
    orph = len(sln_proc.orphans(expected_pids))
    lc = 0
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            cl = p.info.get("cmdline") or []
            if "logcat" in " ".join(cl) and any(d in cl for d in devs):
                lc += 1
    except Exception:
        lc = -1
    return {"bots": len(bots), "unique": sum(1 for d in devs if seen[d]), "dup": dup,
            "orph": orph, "logcat": lc}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(BENCH, "ab30_devices_20260929_224929.txt"))
    ap.add_argument("--minutes", type=float, default=12.0)
    ap.add_argument("--profile", default="config_c60_sln_v2.json")
    ap.add_argument("--stale-s", type=float, default=300.0)
    args = ap.parse_args()

    devs = [l.strip() for l in open(args.file, encoding="utf-8")
            if l.strip() and not l.strip().startswith("#")][:30]
    log("LIFECYCLE 30 devices=%d duration=%.0fmin stale_s=%.0f" % (len(devs), args.minutes, args.stale_s))

    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
        try:
            os.remove(WD.heartbeat_path(HB, d))
        except Exception:
            pass
    log("clean: bot_pids=%d" % len(sln_proc.all_bot_pids()))

    boost = time.time()
    b = subprocess.Popen([PY, BENCH_RUN, "--devices", *devs, "--ads", "0",
                          "--minutes", str(int(args.minutes)), "--profile", args.profile],
                         cwd=BASE, stdout=open(os.path.join(LOGS, "lifecycle_bench.log"), "a"),
                         stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    log("benchmark_run PID=%d" % b.pid)
    mf = None
    tw = time.time()
    while time.time() - tw < 90:
        mf = newest_manifest(boost)
        if mf:
            break
        time.sleep(2)
    if not mf:
        log("ERRO: manifest não encontrado"); sys.exit(2)
    time.sleep(8)
    man = json.load(open(mf, encoding="utf-8"))
    expected = [x["pid"] for x in man["bots"]]
    log("manifest=%s bots=%d" % (os.path.basename(mf), len(expected)))

    wd = subprocess.Popen([PY, WD_RUN, "--manifest", mf, "--profile", args.profile,
                           "--stale-s", str(args.stale_s), "--max", "3", "--interval", "10",
                           "--startup-grace", "180", "--post-restart-grace", "120"],
                          cwd=BASE, stdout=open(os.path.join(LOGS, "lifecycle_watchdog.log"), "a"),
                          stderr=subprocess.STDOUT,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    log("watchdog_run PID=%d" % wd.pid)

    t0 = time.time()
    samples = []
    maxdup = maxorph = 0
    while time.time() - t0 < args.minutes * 60:
        m = metrics(devs, expected)
        samples.append(m)
        maxdup = max(maxdup, m["dup"]); maxorph = max(maxorph, m["orph"])
        if m["dup"] or m["orph"]:
            log("INVARIANTE VIOLADA %s" % m)
        time.sleep(20)
        el = time.time() - t0
        log("t=%3.0fs bots=%d unique=%d dup=%d orph=%d logcat=%d" % (
            el, m["bots"], m["unique"], m["dup"], m["orph"], m["logcat"]))

    # teardown
    wd.terminate(); time.sleep(2)
    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
    try:
        subprocess.run(["taskkill", "/PID", str(b.pid), "/F"], capture_output=True)
    except Exception:
        pass
    time.sleep(2)
    remaining = len(sln_proc.all_bot_pids())
    logcat_final = metrics(devs, expected)["logcat"]
    bots_series = [s["bots"] for s in samples]
    growth = (bots_series[-1] - bots_series[min(3, len(bots_series) - 1)]) if bots_series else 0
    verdict = "PASS" if (maxdup == 0 and maxorph == 0 and growth <= 0 and remaining == 0) else "FAIL"
    log("SUMMARY samples=%d bots[min=%d max=%d last=%d] maxdup=%d maxorph=%d growth=%d remaining_bots=%d logcat_final=%d" % (
        len(samples), min(bots_series), max(bots_series), bots_series[-1], maxdup, maxorph, growth, remaining, logcat_final))
    log("RESULT: %s" % verdict)
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
