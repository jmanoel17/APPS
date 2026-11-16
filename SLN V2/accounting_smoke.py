#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
accounting_smoke.py — SLN V2: smoke 10 devices da NOVA semantica de contagem de ciclo.

Conta por device: cycles_started/accounted/completed/failed/aborted, reset_success/trees.
Para quando cada device atinge TARGET trees (env SLN_QA_TEST_TARGET_RESET_TREES) ou cap.

Uso:
  python accounting_smoke.py --devices <10> --minutes 30 --target 2
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
LOGS = os.path.join(BASE, "logs")
HB = os.path.join(BASE, "heartbeats")
STATE = os.path.join(BASE, "state", "qa_devices")
PROFILE = "config_qa_10dev_accounting_sln_v2.json"


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def safe(dev):
    return dev.replace(":", "_").replace(".", "_")


def read_state(dev):
    p = os.path.join(STATE, safe(dev) + ".json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def log_tokens(dev, tokens):
    p = os.path.join(LOGS, "bench_" + safe(dev) + ".log")
    try:
        tx = open(p, "rb").read().decode("latin-1", "ignore")
    except Exception:
        return {t: 0 for t in tokens}
    # considera apenas o bloco do smoke (ultimo [QA][PROFILE])
    idx = tx.rfind("[QA][PROFILE]")
    blk = tx[idx:] if idx >= 0 else tx
    return {t: blk.count(t) for t in tokens}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", nargs="+", required=True)
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--target", type=int, default=2)
    args = ap.parse_args()
    devs = list(dict.fromkeys(args.devices))[:10]
    log("ACCOUNTING SMOKE devices=%d target=%d trees/device profile=%s" % (len(devs), args.target, PROFILE))

    # limpa workers + estado QA destes devices (teste novo) + coord
    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
        for f in (WD.heartbeat_path(HB, d), os.path.join(STATE, safe(d) + ".json")):
            try:
                os.remove(f)
            except Exception:
                pass
    try:
        os.remove(os.path.join(BASE, "state", "qa_coord.json"))
    except Exception:
        pass

    env = os.environ.copy()
    env["SLN_QA_POPULATION"] = str(len(devs))
    env["SLN_QA_TEST_TARGET_RESET_TREES"] = str(args.target)
    b = subprocess.Popen([PY, BENCH_RUN, "--devices", *devs, "--ads", "0",
                          "--minutes", str(args.minutes), "--profile", PROFILE],
                         env=env, cwd=BASE, stdout=open(os.path.join(LOGS, "acct_smoke_bench.log"), "a"),
                         stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    log("benchmark_run PID=%d" % b.pid)

    t0 = time.time()
    while time.time() - t0 < args.minutes * 60:
        time.sleep(20)
        states = {d: read_state(d) for d in devs}
        trees = sum(int(s.get("reset_success", 0) or 0) for s in states.values())
        done = sum(1 for s in states.values() if int(s.get("reset_success", 0) or 0) >= args.target)
        log("t=%3.0fs trees_total=%d devices_done=%d/%d" % (time.time() - t0, trees, done, len(devs)))
        if done >= len(devs):
            break

    try:
        subprocess.run(["taskkill", "/PID", str(b.pid), "/F"], capture_output=True)
    except Exception:
        pass
    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
    time.sleep(2)

    print("\n%-22s %5s %5s %5s %5s %5s %5s %5s" % (
        "device", "draws", "start", "acct", "compl", "fail", "abort", "trees"))
    agg = {"start": 0, "acct": 0, "compl": 0, "fail": 0, "abort": 0, "trees": 0}
    reports = {}
    for d in devs:
        s = read_state(d)
        tok = log_tokens(d, ["[QA][RESET_DUE]", "clear_data success", "[RESET][ID] success",
                             "SUBAD_CLOSE_CLICK", "awaiting_transition"])
        row = {"device": d, "reset_success": int(s.get("reset_success", 0) or 0),
               "cycles_started": int(s.get("cycles_started", 0) or 0),
               "cycles_accounted": int(s.get("cycles_accounted", 0) or 0),
               "cycles_completed": int(s.get("cycles_completed", 0) or 0),
               "cycles_failed": int(s.get("cycles_failed", 0) or 0),
               "cycles_aborted": int(s.get("cycles_aborted", 0) or 0),
               "reset_interval": s.get("reset_interval_cycles"), "reset_due": tok["[QA][RESET_DUE]"],
               "clear_ok": tok["clear_data success"], "id_ok": tok["[RESET][ID] success"],
               "subad_click": tok["SUBAD_CLOSE_CLICK"], "subad_awaiting": tok["awaiting_transition"]}
        reports[d] = row
        for k, kk in (("cycles_started", "start"), ("cycles_accounted", "acct"), ("cycles_completed", "compl"),
                      ("cycles_failed", "fail"), ("cycles_aborted", "abort"), ("reset_success", "trees")):
            agg[kk] += row[k]
        print("%-22s %5s %5d %5d %5d %5d %5d %5d" % (
            d, row["reset_interval"], row["cycles_started"], row["cycles_accounted"],
            row["cycles_completed"], row["cycles_failed"], row["cycles_aborted"], row["reset_success"]))
    print("\nAGG start=%d acct=%d completed=%d failed=%d aborted=%d trees=%d" % (
        agg["start"], agg["acct"], agg["compl"], agg["fail"], agg["abort"], agg["trees"]))
    # invariantes
    inv_ok = True
    for d in devs:
        r = reports[d]
        if r["cycles_accounted"] != r["cycles_completed"] + r["cycles_failed"]:
            inv_ok = False
            print("  !! %s: accounted(%d) != completed+failed(%d)" % (d, r["cycles_accounted"], r["cycles_completed"] + r["cycles_failed"]))
        if r["reset_success"] != r["clear_ok"] or r["reset_success"] != r["id_ok"]:
            print("  !! %s: reset_success=%d clear_ok=%d id_ok=%d" % (d, r["reset_success"], r["clear_ok"], r["id_ok"]))
            inv_ok = False
    out = os.path.join(BASE, "benchmarks", "accounting_smoke_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    coverage = {
        "devices_requested": len(devs),
        "devices_with_session": sum(1 for r in reports.values() if r["cycles_started"] > 0),
        "devices_with_accounted_cycle": sum(1 for r in reports.values() if r["cycles_accounted"] > 0),
        "devices_with_tree": sum(1 for r in reports.values() if r["reset_success"] >= 1),
        "devices_no_inventory": sum(1 for r in reports.values() if r["cycles_started"] == 0),
        "accounting_fail": any(r["cycles_accounted"] != r["cycles_completed"] + r["cycles_failed"]
                               for r in reports.values()),
    }
    json.dump({"aggregate": agg, "devices": reports, "coverage": coverage, "invariants_ok": inv_ok,
               "remaining_bots": len(sln_proc.all_bot_pids())}, open(out, "w", encoding="utf-8"), indent=2)
    log("summary -> %s" % out)
    devs_requested = len(devs)
    devs_with_session = sum(1 for r in reports.values() if r["cycles_started"] > 0)
    devs_with_accounted = sum(1 for r in reports.values() if r["cycles_accounted"] > 0)
    devs_with_tree = sum(1 for r in reports.values() if r["reset_success"] >= 1)
    devs_no_inventory = sum(1 for r in reports.values() if r["cycles_started"] == 0)
    # INVENTORY_NO_SESSION != ACCOUNTING_FAILURE: devices sem sessão são INVENTORY_COVERAGE, não falha.
    accounting_fail = any(
        r["cycles_accounted"] != r["cycles_completed"] + r["cycles_failed"] for r in reports.values())
    log("FUNCTIONAL_COVERAGE: requested=%d with_session=%d with_accounted=%d with_tree=%d | "
        "INVENTORY_COVERAGE: no_inventory=%d | accounting_fail=%s" % (
            devs_requested, devs_with_session, devs_with_accounted, devs_with_tree,
            devs_no_inventory, accounting_fail))
    # PASS = nenhuma falha de contabilidade nos devices com sessão (invariante acct==compl+fail).
    # Devices NO_INVENTORY são excluídos (não são accounting failure).
    verdict = "PASS" if (inv_ok and not accounting_fail and
                         agg["acct"] == agg["compl"] + agg["fail"]) else "FAIL"
    log("devices_with>=1_tree=%d devices_with>=2_tree=%d invariants_ok=%s -> ACCOUNTING_SMOKE=%s" % (
        devs_with_tree, sum(1 for r in reports.values() if r["reset_success"] >= 2), inv_ok, verdict))
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
