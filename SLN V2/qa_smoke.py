#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qa_smoke.py — SLN V2: smoke QA realistic em poucos devices (3-5).

Usa o overlay TEST-ONLY config_qa_smoke_sln_v2.json (delays/cooldown reduzidos), SLN_QA_POPULATION=N,
e valida a telemetria [QA][...] por device + a invariante de ownership (1 worker/device).

Uso:
  python qa_smoke.py --devices 192.168.1.124:5555 192.168.1.123:5555 192.168.1.122:5555 --minutes 6
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

import sln_proc
import watchdog as WD

BASE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\joao_\AppData\Local\Programs\Python\Python313\python.exe"
BENCH_RUN = os.path.join(BASE, "benchmark_run.py")
BENCH = os.path.join(BASE, "benchmarks")
HB = os.path.join(BASE, "heartbeats")
LOGS = os.path.join(BASE, "logs")
PROFILE = "config_qa_smoke_sln_v2.json"


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def parse_qa(dev):
    p = os.path.join(LOGS, "bench_" + dev.replace(":", "_").replace(".", "_") + ".log")
    try:
        tx = open(p, "rb").read().decode("latin-1", "ignore")
    except Exception:
        return {}
    out = {"delays": [], "reset_schedule": 0, "reset_due": 0, "reset_selected": 0,
           "reset_success": 0, "reset_failed": 0, "install_eligible": 0, "install_selected": 0,
           "install_success": 0, "install_failed": 0, "profile_line": ""}
    for ln in tx.splitlines():
        m = re.search(r"\[QA\]\[DELAY\] device=\S+ seconds=([\d.]+)", ln)
        if m:
            out["delays"].append(float(m.group(1)))
        for key, token in (("reset_schedule", "[QA][RESET_SCHEDULE]"), ("reset_due", "[QA][RESET_DUE]"),
                           ("reset_selected", "[QA][RESET_SELECTED]"), ("reset_success", "[QA][RESET_SUCCESS]"),
                           ("reset_failed", "[QA][RESET_FAILED]"), ("install_eligible", "[QA][INSTALL_ELIGIBLE]"),
                           ("install_selected", "[QA][INSTALL_SELECTED]"), ("install_success", "[QA][INSTALL_SUCCESS]"),
                           ("install_failed", "[QA][INSTALL_FAILED]")):
            if token in ln:
                out[key] += 1
        if "[QA][PROFILE]" in ln:
            out["profile_line"] = ln.strip()
    # RESET_SCHEDULE aparece 1x no init + 1x por reset -> intervalos = count-1
    out["reset_intervals_scheduled"] = max(0, out["reset_schedule"] - 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", nargs="+", required=True)
    ap.add_argument("--minutes", type=int, default=6)
    ap.add_argument("--profile", default=PROFILE)
    args = ap.parse_args()
    devs = list(dict.fromkeys(args.devices))[:5]
    profile = args.profile
    log("QA SMOKE devices=%d minutes=%d profile=%s" % (len(devs), args.minutes, profile))

    # limpa workers + estado QA anterior desses devices
    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
        try:
            os.remove(WD.heartbeat_path(HB, d))
        except Exception:
            pass
        try:
            os.remove(os.path.join(BASE, "state", "qa_devices", d.replace(":", "_").replace(".", "_") + ".json"))
        except Exception:
            pass
    try:
        os.remove(os.path.join(BASE, "state", "qa_coord.json"))
    except Exception:
        pass

    env = os.environ.copy()
    env["SLN_QA_POPULATION"] = str(len(devs))
    b = subprocess.Popen([PY, BENCH_RUN, "--devices", *devs, "--ads", "0",
                          "--minutes", str(args.minutes), "--profile", profile],
                         env=env, cwd=BASE, stdout=open(os.path.join(LOGS, "qa_smoke_bench.log"), "a"),
                         stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    log("benchmark_run PID=%d" % b.pid)

    t0 = time.time()
    maxdup = 0
    while time.time() - t0 < args.minutes * 60:
        time.sleep(20)
        dup = 0
        for d in devs:
            if len(sln_proc.bot_processes_for_device(d)) > 1:
                dup += 1
        maxdup = max(maxdup, dup)
        log("t=%3.0fs dup=%d" % (time.time() - t0, dup))

    try:
        subprocess.run(["taskkill", "/PID", str(b.pid), "/F"], capture_output=True)
    except Exception:
        pass
    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
    time.sleep(2)

    report = {}
    for d in devs:
        report[d] = parse_qa(d)
    log("=== QA SMOKE REPORT ===")
    for d in devs:
        r = report[d]
        dl = r["delays"] or [0]
        log("%s: delays=%d [%.1f..%.1f] reset_sched=%d due=%d selected=%d ok=%d fail=%d | "
            "install_elig=%d sel=%d ok=%d fail=%d" % (
                d, len(r["delays"]), min(dl), max(dl), r["reset_intervals_scheduled"], r["reset_due"],
                r["reset_selected"], r["reset_success"], r["reset_failed"],
                r["install_eligible"], r["install_selected"], r["install_success"], r["install_failed"]))
    summary = {"devices": devs, "maxdup": maxdup, "report": report,
               "remaining_bots": len(sln_proc.all_bot_pids())}
    out = os.path.join(BENCH, "qa_smoke_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    json.dump(summary, open(out, "w", encoding="utf-8"), indent=2)
    log("summary -> %s" % out)
    log("remaining_bots=%d maxdup=%d" % (summary["remaining_bots"], maxdup))
    ok = (maxdup == 0 and summary["remaining_bots"] == 0)
    log("QA_SMOKE=%s" % ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
