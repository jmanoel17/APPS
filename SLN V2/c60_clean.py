#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
c60_clean.py — SLN V2 (FASE 22): C60 LIMPO (capacidade) com watchdog/ownership corrigidos.

Antes do start: bot workers=0, orphan workers=0, orphan logcat=0.
Após estabilizar: bot_process_count=60, unique_bot_devices=60, duplicate_worker_count=0.
Durante TODA a rodada: duplicate_worker_count deve permanecer 0 (senão benchmark INVÁLIDO).
"""
import argparse
import glob
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
    with open(os.path.join(LOGS, "c60_clean.log"), "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), m))


def kill_orphan_logcats():
    import psutil
    k = 0
    for p in psutil.process_iter(["pid", "cmdline", "name"]):
        cl = " ".join(p.info.get("cmdline") or [])
        if "logcat" in cl and "adb" in (p.info.get("name") or "").lower():
            try:
                p.kill(); k += 1
            except Exception:
                pass
    return k


def newest_manifest(after_ts):
    best = None
    for f in os.listdir(BENCH):
        if f.startswith("manifest_") and f.endswith(".json"):
            p = os.path.join(BENCH, f)
            if os.path.getmtime(p) >= after_ts - 2 and (best is None or os.path.getmtime(p) > os.path.getmtime(best)):
                best = p
    return best


def sample(devs, expected_pids):
    bots = sln_proc.all_bot_pids()
    seen = {d: sln_proc.bot_processes_for_device(d) for d in devs}
    dup = [d for d in devs if len(seen[d]) > 1]
    # órfão = bot vivo cujo device NÃO pertence à lista (registry vivo), não o manifest congelado.
    import psutil
    orph = 0
    for pid in bots:
        try:
            cl = psutil.Process(pid).cmdline() or []
        except Exception:
            continue
        dev_arg = None
        for i, a in enumerate(cl):
            if a.endswith("main_sln_v2.py") and i + 1 < len(cl):
                dev_arg = cl[i + 1]
                break
        if dev_arg not in seen:
            orph += 1
    lc = 0
    for p in psutil.process_iter(["cmdline"]):
        cl = p.info.get("cmdline") or []
        if "logcat" in " ".join(cl) and any(d in cl for d in devs):
            lc += 1
    return {"bots": len(bots), "unique": sum(1 for d in devs if seen[d]),
            "dup": len(dup), "dup_devs": dup, "orph": orph, "logcat": lc}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(BENCH, "60_devices_20260930_010849.txt"))
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--profile", default="config_c60_sln_v2.json")
    ap.add_argument("--stale-s", type=float, default=300.0)
    args = ap.parse_args()

    devs = [l.strip() for l in open(args.file, encoding="utf-8")
            if l.strip() and not l.strip().startswith("#")][:60]
    log("C60 CLEAN devices=%d minutes=%d stale_s=%.0f" % (len(devs), args.minutes, args.stale_s))

    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
        try:
            os.remove(WD.heartbeat_path(HB, d))
        except Exception:
            pass
    killed = kill_orphan_logcats()
    time.sleep(2)
    pre = {"bots": len(sln_proc.all_bot_pids()),
           "orphans": len(sln_proc.orphans([])),
           "orphan_logcat_killed": killed}
    log("PRE-START %s" % pre)
    if pre["bots"] != 0:
        log("ABORT: bots vivos no pre-start"); sys.exit(3)

    boost = time.time()
    b = subprocess.Popen([PY, BENCH_RUN, "--devices", *devs, "--ads", "0",
                          "--minutes", str(args.minutes + 2), "--profile", args.profile],
                         cwd=BASE, stdout=open(os.path.join(LOGS, "c60_bench.log"), "a"),
                         stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    mf = None
    tw = time.time()
    while time.time() - tw < 150:
        mf = newest_manifest(boost)
        if mf:
            break
        time.sleep(2)
    if not mf:
        log("ERRO: manifest não encontrado"); sys.exit(2)
    time.sleep(10)
    man = json.load(open(mf, encoding="utf-8"))
    expected = [x["pid"] for x in man["bots"]]
    host_out = man.get("host_out")
    log("manifest=%s bots=%d host_out=%s" % (os.path.basename(mf), len(expected), os.path.basename(host_out or "")))

    wd_stdout = open(os.path.join(LOGS, "c60_watchdog.log"), "a")
    wd = subprocess.Popen([PY, WD_RUN, "--manifest", mf, "--profile", args.profile,
                           "--stale-s", str(args.stale_s), "--max", "3", "--interval", "10",
                           "--startup-grace", "240", "--post-restart-grace", "180"],
                          cwd=BASE, stdout=wd_stdout, stderr=subprocess.STDOUT,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    log("watchdog_run PID=%d" % wd.pid)

    t0 = time.time()
    invalid = False
    samples = []
    maxdup = maxorph = 0
    while time.time() - t0 < args.minutes * 60:
        s = sample(devs, expected)
        samples.append(dict(t=round(time.time() - t0, 0), **s))
        maxdup = max(maxdup, s["dup"]); maxorph = max(maxorph, s["orph"])
        log("t=%3.0fs bots=%d unique=%d dup=%d orph=%d logcat=%d" % (
            time.time() - t0, s["bots"], s["unique"], s["dup"], s["orph"], s["logcat"]))
        if s["dup"]:
            log("!! DUPLICATE_WORKERS_DETECTED %s — BENCHMARK INVÁLIDO, parando" % s["dup_devs"])
            invalid = True
            break
        time.sleep(20)

    wd.terminate(); time.sleep(2)
    try:
        subprocess.run(["taskkill", "/PID", str(b.pid), "/F"], capture_output=True)
    except Exception:
        pass
    # para bots (o watchdog já morreu; mata o restante)
    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
    time.sleep(2)

    bots_series = [x["bots"] for x in samples]
    summary = {"manifest": mf, "host_out": host_out, "device_count": len(devs),
               "minutes": args.minutes, "invalid_duplicates": invalid,
               "maxdup": maxdup, "maxorph": maxorph,
               "bots_min": min(bots_series), "bots_max": max(bots_series), "bots_last": bots_series[-1],
               "remaining_bots": len(sln_proc.all_bot_pids()),
               "remaining_logcat": sample(devs, expected)["logcat"],
               "watchdog_metrics": sorted(glob.glob(os.path.join(BENCH, "watchdog_metrics_*.csv")))[-1]}
    with open(os.path.join(BENCH, "c60_clean_summary_%s.json" % time.strftime("%Y%m%d_%H%M%S")), "w") as f:
        json.dump(dict(summary=summary, samples=samples), f, indent=2)
    log("SUMMARY %s" % summary)
    verdict = "VALID" if (not invalid and maxdup == 0 and maxorph == 0 and summary["remaining_bots"] == 0) else "INVALID"
    log("C60_CLEAN_RESULT=%s" % verdict)
    sys.exit(0 if verdict == "VALID" else 1)


if __name__ == "__main__":
    main()
