#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
benchmark_host.py — SLN V2

Ferramenta SEPARADA do bot para medir o HOST durante um benchmark (1+ devices).
Não é importada pelo watcher; roda em paralelo e grava CSV/JSONL.

Uso:
    python benchmark_host.py [--interval 5] [--out benchmarks/host_1device_<ts>.csv] [--pid BOT_PID]

Métricas por amostra (timestamp incluído):
    host_cpu_pct, host_ram_used_mb, host_ram_pct,
    python_process_count, python_rss_total_mb, python_rss_avg_mb, python_rss_max_mb,
    adb_process_count, bot_process_count, ai_process_rss_mb,
    disk_write_bytes/s (best effort; None se indisponível)

Sem quebrar se alguma métrica não existir no Windows.
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def sample(bot_pid=None):
    """Coleta uma amostra do host. Nunca levanta exceção — métrica ausente vira None."""
    out = {"ts": _now()}
    if psutil is None:
        return out
    try:
        vm = psutil.virtual_memory()
        out["host_ram_used_mb"] = round(vm.used / (1024 * 1024), 1)
        out["host_ram_pct"] = vm.percent
    except Exception:
        pass
    try:
        out["host_cpu_pct"] = psutil.cpu_percent(interval=None)
    except Exception:
        pass
    try:
        py_procs = [p for p in psutil.process_iter(["pid", "name", "cmdline"]) if p.info.get("name", "").lower().startswith("python")]
        out["python_process_count"] = len(py_procs)
        rss = []
        for p in py_procs:
            try:
                rss.append(p.memory_info().rss / (1024 * 1024))
            except Exception:
                pass
        if rss:
            out["python_rss_total_mb"] = round(sum(rss), 1)
            out["python_rss_avg_mb"] = round(sum(rss) / len(rss), 1)
            out["python_rss_max_mb"] = round(max(rss), 1)
        else:
            out["python_rss_total_mb"] = out["python_rss_avg_mb"] = out["python_rss_max_mb"] = None
    except Exception:
        pass
    try:
        out["adb_process_count"] = sum(1 for p in psutil.process_iter(["name"])
                                       if p.info.get("name", "").lower() in ("adb.exe", "adb"))
    except Exception:
        pass
    try:
        # bot_process_count: processos python cujo cmdline contém o SCRIPT main_sln_v2.py (basename exato,
        # não substring — evita contar paths como main_sln_v2_backup.py ou o próprio benchmark_host).
        out["bot_process_count"] = sum(1 for p in psutil.process_iter(["cmdline"])
                                       if p.info.get("cmdline") and
                                       any(os.path.basename(c) == "main_sln_v2.py" for c in p.info["cmdline"]))
    except Exception:
        pass
    try:
        ai = None
        for p in psutil.process_iter(["cmdline"]):
            try:
                if p.info.get("cmdline") and any(os.path.basename(c) == "aiMain_sln_v2.py" for c in p.info["cmdline"]):
                    ai = p
                    break
            except Exception:
                continue
        out["ai_process_rss_mb"] = round(ai.memory_info().rss / (1024 * 1024), 1) if ai else None
    except Exception:
        pass
    if bot_pid:
        try:
            out["bot_target_rss_mb"] = round(psutil.Process(bot_pid).memory_info().rss / (1024 * 1024), 1)
        except Exception:
            out["bot_target_rss_mb"] = None
    return out


def concurrency(hb_dir):
    """Conta devices por operação a partir dos heartbeats (sem ADB adicional)."""
    out = {"running_bot_count": 0, "active_fastclose_count": 0, "main_idle_count": 0,
           "no_fill_backoff_count": 0, "reset_count_active": 0, "store_count": 0, "recovery_count": 0}
    try:
        import glob as _glob
        for p in _glob.glob(os.path.join(hb_dir, "hb_*.json")):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    hb = json.load(f)
            except Exception:
                continue
            op = str(hb.get("current_operation", "")).lower()
            stt = str(hb.get("state", "")).upper()
            # running = heartbeat recente (last_progress dentro de ~3x o intervalo); simplificado: arquivo existe
            out["running_bot_count"] += 1
            if op == "fastclose":
                out["active_fastclose_count"] += 1
            elif op in ("main", "cycle_start", "idle", ""):
                out["main_idle_count"] += 1
            elif op in ("no_fill", "backoff") or stt == "BACKOFF":
                out["no_fill_backoff_count"] += 1
            elif op == "reset":
                out["reset_count_active"] += 1
            elif op == "store":
                out["store_count"] += 1
            elif op == "recovery":
                out["recovery_count"] += 1
    except Exception:
        pass
    return out


def logcat_proc_count():
    """Nº de processos adb cujo cmdline contém 'logcat' (os streams persistentes)."""
    try:
        n = 0
        for p in psutil.process_iter(["name", "cmdline"]):
            nm = (p.info.get("name") or "").lower()
            cl = p.info.get("cmdline") or []
            if nm in ("adb.exe", "adb") and any("logcat" in str(c) for c in cl):
                n += 1
        return n
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Benchmark do host (CPU/RAM/RSS) durante 1+ devices.")
    ap.add_argument("--interval", type=float, default=5.0, help="segundos entre amostras (default 5)")
    ap.add_argument("--out", default=None, help="caminho CSV/JSONL (default: benchmarks/host_<ts>.csv)")
    ap.add_argument("--pid", type=int, default=None, help="PID do bot para RSS alvo")
    ap.add_argument("--duration", type=float, default=0, help="duração em s (0 = até Ctrl+C)")
    ap.add_argument("--host-id", default="", help="HOST_ID (FASE 10)")
    ap.add_argument("--device-count", type=int, default=0, help="nº de devices do benchmark")
    ap.add_argument("--profile", default="", help="perfil (overlay) em uso")
    ap.add_argument("--hb-dir", default="heartbeats", help="dir de heartbeats (concorrência)")
    args = ap.parse_args()

    if args.out:
        base = args.out
    else:
        os.makedirs("benchmarks", exist_ok=True)
        base = os.path.join("benchmarks", "host_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    ext = os.path.splitext(base)[1].lower()
    is_json = ext in (".json", ".jsonl")

    # flags de escala (do perfil) só para correlacionar no CSV
    pl = at = None
    try:
        import json as _json
        prof = args.profile if os.path.isabs(args.profile) else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), args.profile)
        with open(prof, "r", encoding="utf-8") as _f:
            _d = _json.load(_f)
        pl = bool(_d.get("persistent_logcat_enabled", False))
        at = bool(_d.get("adaptive_tick_enabled", False))
    except Exception:
        pass
    consts = {"host_id": args.host_id, "device_count": args.device_count,
              "profile": args.profile, "persistent_logcat": pl, "adaptive_tick": at}

    campos = ["ts", "host_id", "device_count", "profile", "persistent_logcat", "adaptive_tick",
              "host_cpu_pct", "host_ram_used_mb", "host_ram_pct",
              "python_process_count", "python_rss_total_mb", "python_rss_avg_mb", "python_rss_max_mb",
              "adb_process_count", "logcat_process_count", "bot_process_count", "ai_process_rss_mb",
              "disk_write_bytes_s", "bot_target_rss_mb",
              "running_bot_count", "active_fastclose_count", "main_idle_count",
              "no_fill_backoff_count", "reset_count_active", "store_count", "recovery_count"]

    fh = open(base, "w", newline="", encoding="utf-8")
    writer = None
    if not is_json:
        writer = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
        writer.writeheader()
        fh.flush()

    print("[HOST] gravando em %s (json=%s interval=%.1fs)" % (base, is_json, args.interval), flush=True)
    t_inicio = time.time()
    prev_disk = None
    prev_t = None
    try:
        while True:
            if args.duration and time.time() - t_inicio >= args.duration:
                break
            s = sample(args.pid)
            s.update(consts)
            s["logcat_process_count"] = logcat_proc_count()
            hb_dir = args.hb_dir if os.path.isabs(args.hb_dir) else os.path.join(
                os.path.dirname(os.path.abspath(__file__)), args.hb_dir)
            s.update(concurrency(hb_dir))
            if psutil is not None:
                try:
                    cnt = psutil.disk_io_counters()
                    if cnt is not None and prev_disk is not None and prev_t is not None:
                        dt = time.time() - prev_t
                        if dt > 0:
                            s["disk_write_bytes_s"] = int(max(0, cnt.write_bytes - prev_disk.write_bytes) / dt)
                    prev_disk = cnt
                    prev_t = time.time()
                except Exception:
                    pass
            if is_json:
                import json
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
            else:
                writer.writerow(s)
            fh.flush()
            print("[HOST] %s" % " ".join("%s=%s" % (k, v) for k, v in s.items() if v is not None), flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        fh.close()
    print("[HOST] FIM")

if __name__ == "__main__":
    main()