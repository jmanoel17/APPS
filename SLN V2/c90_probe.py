#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
c90_probe.py — SLN V2 (CAPACITY PROBE C90)

90 devices, MESMA config/comportamento do C60 clean. Alvo 30 min com EARLY STOP obrigatório:
  CPU >90% contínuo >=120s | CPU >95% contínuo >=60s | fração >90% alta em 5 min
  | dumps >10s reaparecendo em múltiplos devices | reconnect storm | ADB caindo em massa.

NÃO altera lógica funcional. Se duplicate>0 -> C90_INVALID (parar).
"""
import argparse
import glob
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
WD_RUN = os.path.join(BASE, "watchdog_run.py")
BENCH = os.path.join(BASE, "benchmarks")
HB = os.path.join(BASE, "heartbeats")
LOGS = os.path.join(BASE, "logs")


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)
    with open(os.path.join(LOGS, "c90_probe.log"), "a", encoding="utf-8") as f:
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


def proc_metrics(devs):
    import psutil
    bots = sln_proc.all_bot_pids()
    seen = {d: sln_proc.bot_processes_for_device(d) for d in devs}
    dup = [d for d in devs if len(seen[d]) > 1]
    orph = 0
    for pid in bots:
        try:
            cl = psutil.Process(pid).cmdline() or []
        except Exception:
            continue
        dev_arg = None
        for i, a in enumerate(cl):
            if a.endswith("main_sln_v2.py") and i + 1 < len(cl):
                dev_arg = cl[i + 1]; break
        if dev_arg not in seen:
            orph += 1
    adb = lc = 0
    for p in psutil.process_iter(["cmdline", "name"]):
        cl = p.info.get("cmdline") or []
        if "adb" in (p.info.get("name") or "").lower():
            adb += 1
            if "logcat" in " ".join(cl) and any(d in cl for d in devs):
                lc += 1
    ram = psutil.virtual_memory()
    return {"bots": len(bots), "unique": sum(1 for d in devs if seen[d]), "dup": len(dup),
            "dup_devs": dup, "orph": orph, "logcat": lc, "adb": adb,
            "ram_mb": round((ram.total - ram.available) / 1048576.0, 0), "ram_pct": ram.percent}


def scan_dumps(devs):
    """Soma (cumulativa) de dump_gt_5s/10s/30s do último [PERF] de cada device."""
    g5 = g10 = g30 = 0
    devices_g10 = 0
    for d in devs:
        p = os.path.join(LOGS, "bench_" + d.replace(":", "_").replace(".", "_") + ".log")
        try:
            with open(p, "rb") as f:
                tail = f.read()[-20000:].decode("latin-1", "ignore")
        except Exception:
            continue
        m = None
        for mm in re.finditer(r"dump_gt_5s=(\d+).*?dump_gt_10s=(\d+).*?dump_gt_30s=(\d+)", tail, re.S):
            m = mm
        if m:
            a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
            g5 += a; g10 += b; g30 += c
            if b > 0:
                devices_g10 += 1
    return g5, g10, g30, devices_g10


def scan_reconnect(devs):
    tot = 0
    for d in devs:
        p = os.path.join(LOGS, "bench_" + d.replace(":", "_").replace(".", "_") + ".log")
        try:
            with open(p, "rb") as f:
                tail = f.read()[-20000:].decode("latin-1", "ignore")
        except Exception:
            continue
        ms = re.findall(r"reconnect_count=(\d+)", tail)
        if ms:
            tot += int(ms[-1])
    return tot


def read_host_cpu(host_csv):
    cpu = []
    rows = 0
    try:
        with open(host_csv, "r", encoding="utf-8") as f:
            r = list(__import__("csv").DictReader(f))
        rows = len(r)
        for x in r:
            try:
                cpu.append(float(x.get("host_cpu_pct", "")))
            except Exception:
                pass
    except Exception:
        pass
    return cpu, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--profile", default="config_c60_sln_v2.json")
    ap.add_argument("--stale-s", type=float, default=300.0)
    args = ap.parse_args()

    devs = [l.strip() for l in open(args.file, encoding="utf-8")
            if l.strip() and not l.strip().startswith("#")][:90]
    log("C90 PROBE devices=%d minutes=%d profile=%s" % (len(devs), args.minutes, args.profile))

    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
        try:
            os.remove(WD.heartbeat_path(HB, d))
        except Exception:
            pass
    killed = kill_orphan_logcats()
    time.sleep(2)
    pre = proc_metrics(devs)
    log("PRE-START bots=%d orph=%d dup=%d orphan_logcat_killed=%d" % (pre["bots"], pre["orph"], pre["dup"], killed))
    if pre["bots"] != 0 or pre["dup"] != 0:
        log("ABORT pre-start não limpo"); sys.exit(3)

    boost = time.time()
    b = subprocess.Popen([PY, BENCH_RUN, "--devices", *devs, "--ads", "0",
                          "--minutes", str(args.minutes + 2), "--profile", args.profile],
                         cwd=BASE, stdout=open(os.path.join(LOGS, "c90_bench.log"), "a"),
                         stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    mf = None
    tw = time.time()
    while time.time() - tw < 180:
        mf = newest_manifest(boost)
        if mf:
            break
        time.sleep(2)
    if not mf:
        log("ERRO: manifest não encontrado"); sys.exit(2)
    time.sleep(15)
    man = json.load(open(mf, encoding="utf-8"))
    host_out = man.get("host_out")
    expected = [x["pid"] for x in man["bots"]]
    log("manifest=%s bots=%d host=%s" % (os.path.basename(mf), len(expected), os.path.basename(host_out or "")))

    wd = subprocess.Popen([PY, WD_RUN, "--manifest", mf, "--profile", args.profile,
                           "--stale-s", str(args.stale_s), "--max", "3", "--interval", "10",
                           "--startup-grace", "240", "--post-restart-grace", "180"],
                          cwd=BASE, stdout=open(os.path.join(LOGS, "c90_watchdog.log"), "a"),
                          stderr=subprocess.STDOUT,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    log("watchdog_run PID=%d" % wd.pid)

    t0 = time.time()
    samples = []
    invalid = False
    stop_reason = None
    base_g10 = None
    last_reconn = 0
    last_dump_scan = 0.0
    maxdup = maxorph = 0
    while time.time() - t0 < args.minutes * 60:
        s = proc_metrics(devs)
        s["t"] = round(time.time() - t0, 0)
        samples.append(s)
        maxdup = max(maxdup, s["dup"]); maxorph = max(maxorph, s["orph"])
        cpu, rows = read_host_cpu(host_out) if host_out else ([], 0)
        # early stop CPU
        iv = 5.0
        def runlen(t):
            n = 0
            for x in reversed(cpu):
                if x > t: n += 1
                else: break
            return n * iv
        recent = cpu[-60:] if len(cpu) > 60 else cpu
        frac90 = (sum(1 for x in recent if x > 90) / len(recent)) if recent else 0
        cpu_txt = ("cpu avg=%.1f p95=%.1f max=%.1f >90run=%.0fs frac90_5min=%.2f" % (
            (sum(cpu) / len(cpu)) if cpu else 0,
            sorted(cpu)[min(len(cpu) - 1, int(0.95 * (len(cpu) - 1)))] if cpu else 0,
            max(cpu) if cpu else 0, runlen(90), frac90)) if cpu else "cpu n/a"
        log("t=%3.0fs bots=%d unique=%d dup=%d orph=%d logcat=%d adb=%d ram=%.0fMB %s" % (
            time.time() - t0, s["bots"], s["unique"], s["dup"], s["orph"], s["logcat"], s["adb"], s["ram_mb"], cpu_txt))
        if s["dup"]:
            invalid = True; stop_reason = "DUPLICATE_WORKERS_DETECTED %s" % s["dup_devs"]; break
        if cpu:
            if runlen(90) >= 120:
                stop_reason = "CPU>90%% por >=120s"; break
            if runlen(95) >= 60:
                stop_reason = "CPU>95%% por >=60s"; break
            if len(cpu) >= 60 and frac90 > 0.6:
                stop_reason = "CPU>90%% em >60%% dos ultimos 5min"; break
        # dump / reconnect checks a cada 60s
        if time.time() - last_dump_scan >= 60:
            last_dump_scan = time.time()
            g5, g10, g30, dev10 = scan_dumps(devs)
            if base_g10 is None:
                base_g10 = g10
            rc = scan_reconnect(devs)
            log("  dumps>10s total=%d (devices=%d) dumps>5s=%d reconn_total=%d" % (g10, dev10, g5, rc))
            if g10 > base_g10 and dev10 >= 2:
                stop_reason = "dumps>10s reaparecendo em %d devices" % dev10; break
            if rc - last_reconn >= 25:
                stop_reason = "reconnect storm (+%d em 60s)" % (rc - last_reconn); break
            last_reconn = rc
        time.sleep(20)

    # snapshot no stop
    snap = proc_metrics(devs)
    cpu, rows = read_host_cpu(host_out) if host_out else ([], 0)
    g5, g10, g30, dev10 = scan_dumps(devs)
    rc = scan_reconnect(devs)
    snap.update({"cpu_series_len": len(cpu), "cpu_last": cpu[-1] if cpu else None,
                 "dump_gt_5s_total": g5, "dump_gt_10s_total": g10, "dump_gt_30s_total": g30,
                 "reconnect_total": rc, "stop_reason": stop_reason, "invalid": invalid,
                 "elapsed_s": round(time.time() - t0, 0), "device_count": len(devs)})
    log("STOP reason=%s snapshot=%s" % (stop_reason or "30min completo", snap))

    wd.terminate(); time.sleep(2)
    try:
        subprocess.run(["taskkill", "/PID", str(b.pid), "/F"], capture_output=True)
    except Exception:
        pass
    for d in devs:
        sln_proc.terminate_worker_verified(d, timeout=8)
    time.sleep(2)
    final = proc_metrics(devs)

    summary = {"manifest": mf, "host_out": host_out, "device_count": len(devs),
               "stop_reason": stop_reason, "invalid": invalid, "elapsed_s": snap["elapsed_s"],
               "maxdup": maxdup, "maxorph": maxorph, "stop_snapshot": snap,
               "remaining_bots": final["bots"], "remaining_logcat": final["logcat"],
               "watchdog_metrics": sorted(glob.glob(os.path.join(BENCH, "watchdog_metrics_*.csv")))[-1]}
    out = os.path.join(BENCH, "c90_probe_summary_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(summary=summary, samples=samples), f, indent=2)
    log("SUMMARY %s" % summary)
    verdict = "C90_INVALID" if invalid else ("EARLY_STOP" if stop_reason else "C90_COMPLETE")
    log("C90_RESULT=%s" % verdict)
    sys.exit(0 if not invalid else 1)


if __name__ == "__main__":
    main()
