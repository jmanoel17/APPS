#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watchdog_run.py — SLN V2 (FASE 5 / v2 — RESTART TRANSACIONAL + OWNERSHIP)

Supervisor EXTERNO. Invariante: UM DEVICE = NO MÁXIMO UM BOT ATIVO.

Regras:
  - registry (manifest) é a AUTORIDADE da sessão; heartbeat só sinal.
  - restart é TRANSAÇÃO: marcar RECOVERING -> kill VERIFICADO (árvore) -> confirmar ZERO para o
    device -> só então spawn -> registrar pid/instance/generation -> confirmar heartbeat.
  - lock por device (restart concorrente é coalescido).
  - preflight: duplicates>0 -> DUPLICATE_WORKER_PREFLIGHT_FAIL (não inicia).
  - métricas: duplicates/orphans/logcat amostrados durante toda a rodada.

Uso:
  python watchdog_run.py --manifest benchmarks/manifest_<ts>.json \
      --profile config_c60_sln_v2.json --stale-s 300 --max 3
"""
import argparse
import json
import os
import subprocess
import threading
import time
import uuid

import watchdog as WD
import sln_proc

BASE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\joao_\AppData\Local\Programs\Python\Python313\python.exe"
BOT = os.path.join(BASE, "main_sln_v2.py")
LOGS = os.path.join(BASE, "logs")
BENCH = os.path.join(BASE, "benchmarks")
HB_DIR = os.path.join(BASE, "heartbeats")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(LOGS, "watchdog.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _find(device, find=None):
    return list((find or sln_proc.bot_processes_for_device)(device))


def duplicate_devices(devices, find=None):
    """Devices com >1 bot VIVO (match exato). Invariante deseja []."""
    return [d for d in devices if len(_find(d, find)) > 1]


def preflight(devices, find=None, expected_pids=None):
    """(duplicates, orphans). orphans só se expected_pids for informado."""
    dup = duplicate_devices(devices, find)
    orph = sln_proc.orphans(expected_pids) if expected_pids is not None else []
    return dup, orph


def metrics_snapshot(expected_devices, registry):
    """Contadores de ownership. duplicate = device com >1 bot vivo; orphan = bot fora do registry."""
    bots = sln_proc.all_bot_pids()
    seen = {}
    for dev in expected_devices:
        seen[dev] = sln_proc.bot_processes_for_device(dev)
    dup = [d for d, ps in seen.items() if len(ps) > 1]
    expected_pids = [registry[d]["pid"] for d in expected_devices if registry.get(d, {}).get("pid")]
    orph = sln_proc.orphans(expected_pids)
    logcat = 0
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            cl = p.info.get("cmdline") or []
            if "logcat" in " ".join(cl) and any(d in cl for d in expected_devices):
                logcat += 1
    except Exception:
        logcat = -1
    return {
        "expected_device_count": len(expected_devices),
        "bot_process_count": len(bots),
        "unique_bot_devices": sum(1 for d in expected_devices if seen[d]),
        "duplicate_worker_count": len(dup),
        "duplicate_devices": dup,
        "orphan_worker_count": len(orph),
        "logcat_process_count": logcat,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--profile", default="config_c60_sln_v2.json")
    ap.add_argument("--stale-s", type=float, default=300.0)
    ap.add_argument("--max", type=int, default=3)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=0)
    ap.add_argument("--post-restart-grace", type=float, default=0.0)
    ap.add_argument("--startup-grace", type=float, default=120.0)
    ap.add_argument("--no-preflight", action="store_true")
    args = ap.parse_args()

    man = json.load(open(os.path.join(BASE, args.manifest), encoding="utf-8"))
    registry = {}
    for b in man["bots"]:
        registry[b["device"]] = {"pid": b.get("pid"), "log": b.get("log"),
                                 "instance_id": b.get("instance_id"), "generation": b.get("generation", 1)}
    devices = list(registry.keys())
    os.makedirs(HB_DIR, exist_ok=True)

    # ---------- PREFLIGHT (FASE 8/9) ----------
    if not args.no_preflight:
        exp_pids = [registry[d]["pid"] for d in devices if registry[d].get("pid")]
        dup, orph = preflight(devices, expected_pids=exp_pids)
        log("[WATCHDOG] PREFLIGHT devices=%d duplicates=%s orphans=%s" % (len(devices), dup, orph))
        if dup:
            log("[WATCHDOG] DUPLICATE_WORKER_PREFLIGHT_FAIL %s" % (dup,))
            raise SystemExit(3)

    locks = {d: threading.Lock() for d in devices}
    stop = threading.Event()

    def launch(dev, generation):
        inst = uuid.uuid4().hex
        env = os.environ.copy()
        env["SLN_CONFIG_OVERLAY"] = args.profile
        env["SLN_TEST_MAX_RUNTIME_S"] = os.environ.get("SLN_RESTART_RUNTIME_S", "0")
        env.pop("SLN_TEST_MAX_CYCLES", None)
        env["SLN_WORKER_INSTANCE_ID"] = inst
        env["SLN_WORKER_GENERATION"] = str(generation)
        logf = open(registry[dev].get("log") or os.path.join(
            LOGS, "bench_" + dev.replace(":", "_").replace(".", "_") + ".log"), "a", encoding="utf-8")
        p = subprocess.Popen([PY, BOT, dev, "0", "0"], env=env, cwd=BASE,
                             stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return p, inst

    def restart(dev):
        """Transação de restart: kill verificado -> ZERO -> spawn -> registro."""
        with locks[dev]:
            r = registry[dev]
            old = r.get("pid")
            s = sln_proc.terminate_worker_verified(dev, timeout=12.0, kill_logcat=True)
            if not s["ok"]:
                log("[WATCHDOG] %s RESTART_ABORT (workers ainda vivos: bots=%s logcat=%s)" %
                    (dev, s["remaining_bots"], s["remaining_logcat"]))
                return
            leftover = sln_proc.bot_processes_for_device(dev)
            if leftover:
                log("[WATCHDOG] %s RESTART_ABORT duplicate/leftover=%s" % (dev, leftover))
                return
            gen = int(r.get("generation") or 1) + 1
            p, inst = launch(dev, gen)
            r.update({"pid": p.pid, "instance_id": inst, "generation": gen,
                      "last_pid": old, "restarts": int(r.get("restarts", 0)) + 1})
            sup.register(dev, pid=p.pid, instance_id=inst, generation=gen)
            log("[WATCHDOG] %s RESTART ok old_pid=%s -> pid=%s gen=%d (logcat limpo=%s)" %
                (dev, old, p.pid, gen, s["killed_pids"] != []))

    sup = WD.Supervisor(HB_DIR, stale_s=args.stale_s, restart_max=args.max,
                        restart_fn=restart, log_fn=log, now_fn=time.monotonic,
                        post_restart_grace_s=args.post_restart_grace,
                        startup_grace_s=args.startup_grace)
    for dev in devices:
        sup.register(dev, pid=registry[dev].get("pid"), instance_id=registry[dev].get("instance_id"),
                     generation=registry[dev].get("generation", 1))

    metrics_path = os.path.join(BENCH, "watchdog_metrics_%s.csv" % time.strftime("%Y%m%d_%H%M%S"))
    with open(metrics_path, "w", encoding="utf-8") as f:
        f.write("ts,expected_device_count,bot_process_count,unique_bot_devices,"
                "duplicate_worker_count,orphan_worker_count,logcat_process_count\n")
    log("[WATCHDOG] start devices=%d stale_s=%.0f max=%d interval=%.0fs startup_grace=%.0fs "
        "grace=%.0fs metrics=%s" % (
        len(devices), args.stale_s, args.max, args.interval, args.startup_grace,
        args.post_restart_grace, metrics_path))

    t0 = time.time()
    last_metrics = 0.0
    try:
        while not stop.is_set():
            if args.duration and time.time() - t0 >= args.duration:
                break
            for dev, action, prev in sup.poll(devices):
                hb = WD.read_heartbeat(WD.heartbeat_path(HB_DIR, dev)) or {}
                log("[WATCHDOG] %s action=%s prev=%s op=%s age=%.0fs" % (
                    dev, action, prev, hb.get("current_operation"),
                    (time.monotonic() - hb.get("last_progress", time.monotonic())) if hb else -1))
            if time.time() - last_metrics >= 15.0:
                m = metrics_snapshot(devices, registry)
                last_metrics = time.time()
                with open(metrics_path, "a", encoding="utf-8") as f:
                    f.write("%s,%d,%d,%d,%d,%d,%d\n" % (time.strftime("%H:%M:%S"),
                            m["expected_device_count"], m["bot_process_count"], m["unique_bot_devices"],
                            m["duplicate_worker_count"], m["orphan_worker_count"], m["logcat_process_count"]))
                if m["duplicate_worker_count"] or m["orphan_worker_count"]:
                    log("[WATCHDOG] INVARIANTE violada: duplicates=%d orphans=%d %s" % (
                        m["duplicate_worker_count"], m["orphan_worker_count"], m.get("duplicate_devices")))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    log("[WATCHDOG] FIM")


if __name__ == "__main__":
    main()
