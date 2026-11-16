#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preflight_devices.py — SLN V2

Verificação READ-ONLY dos devices antes de um benchmark (1+ aparelhos).
NÃO altera estado: não clica, não instala, não limpa dados, não reseta ID, não force-stop.

Checagens por device:
  online          — adb get-state == "device"
  screen_awake    — wakefulness == Awake (não Dozing/Asleep)
  battery_pct     — nível de bateria (não bloqueia; só reporta)
  app_installed   — com.flixdigital.dramaclassic presente em pm list packages
  ia_health       — servidor IA /health respondendo (modelo carregado)

Uso:
  python preflight_devices.py [device ...]
  python preflight_devices.py --group NOME         (lê groups.json)
  python preflight_devices.py --all                (adb devices)

Saída: tabela + código de saída (0 = todos os checks bloqueantes OK; 1 = algum FAIL).
Checks "bloqueantes": online, screen_awake, app_installed, ia_health.
"""
import argparse
import json
import os
import sys

import requests
import uiautomator2 as u2

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "com.flixdigital.dramaclassic"
DEFAULT_IA_URL = "http://localhost:8000"

BLOCKING = {"online", "screen_awake", "app_installed", "ia_health"}


def _adb_devices():
    import subprocess
    try:
        out = subprocess.check_output("adb devices", shell=True, text=True, timeout=5)
        return [ln.split("\t")[0] for ln in out.strip().split("\n")[1:] if "device" in ln]
    except Exception:
        return []


def _resolve_devices(args):
    if args.device:
        return args.device
    if args.group:
        path = os.path.join(SCRIPT_DIR, "groups.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    groups = json.load(f)
                return groups.get(args.group, [])
            except Exception:
                return []
        return []
    return _adb_devices()


def _shell(d, cmd):
    try:
        r = d.shell(cmd)
        return str(getattr(r, "output", r) or "")
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def check_device(serial):
    res = {}
    try:
        d = u2.connect(serial)
        info = d.info      # dict se online/reachável
        res["online"] = bool(info)
    except Exception:
        res["online"] = False
    if not res["online"]:
        res["screen_awake"] = False
        res["battery_pct"] = None
        res["app_installed"] = False
        res["detail"] = "offline"
        return res

    pw = _shell(d, "dumpsys power")
    res["screen_awake"] = ("mWakefulness=Awake" in pw)
    bat = _shell(d, "dumpsys battery")
    level = None
    for token in bat.split("\n"):
        t = token.strip()
        if t.startswith("level:"):
            try:
                level = int(t.split(":", 1)[1].strip())
            except Exception:
                level = None
    res["battery_pct"] = level
    res["app_installed"] = PACKAGE in _shell(d, "pm list packages")
    win = _shell(d, "dumpsys window")
    mf = ""
    for token in win.split("\n"):
        if "mCurrentFocus" in token or "mFocusedApp" in token:
            mf = token.strip()
            break
    res["detail"] = mf[:120]
    return res


def check_ia(url):
    try:
        r = requests.get(f"{url.rstrip('/')}/health", timeout=3)
        if r.status_code != 200:
            return False, f"http={r.status_code}"
        j = r.json()
        loaded = bool(j.get("model_loaded"))
        return loaded, ("loaded" if loaded else "model_not_loaded")
    except Exception as e:
        return False, f"ERR:{type(e).__name__}"


def main():
    ap = argparse.ArgumentParser(description="Preflight READ-ONLY dos devices (SLN V2).")
    ap.add_argument("device", nargs="*", help="seriais/endereços dos devices")
    ap.add_argument("--group", default=None, help="nome do grupo em groups.json")
    ap.add_argument("--all", action="store_true", help="usa adb devices")
    ap.add_argument("--ia-url", default=os.environ.get("SLN_IA_URL", DEFAULT_IA_URL))
    args = ap.parse_args()

    if args.all:
        devs = _adb_devices()
    else:
        devs = _resolve_devices(args)
    devs = list(dict.fromkeys(devs))  # dedupe, preserva ordem

    if not devs:
        print("[PREFLIGHT] Nenhum device encontrado.")
        sys.exit(2)

    ia_ok, ia_detail = check_ia(args.ia_url)
    print(f"[PREFLIGHT] IA /health: {'OK' if ia_ok else 'FAIL'} ({ia_detail})")

    header = f"{'DEVICE':22} {'ONLINE':7} {'AWAKE':6} {'BATT':5} {'APP':5} {'IA':3}  DETAIL"
    print(header)
    print("-" * len(header))

    all_ok = ia_ok
    for serial in devs:
        r = check_device(serial)
        row = {
            "online": "OK" if r["online"] else "FAIL",
            "screen_awake": "OK" if r["screen_awake"] else "FAIL",
            "battery_pct": str(r["battery_pct"]) if r["battery_pct"] is not None else "?",
            "app_installed": "OK" if r["app_installed"] else "FAIL",
            "ia_health": "OK" if ia_ok else "FAIL",
        }
        for k in BLOCKING:
            if k != "ia_health" and row.get(k) == "FAIL":
                all_ok = False
        print(f"{serial:22} {row['online']:7} {row['screen_awake']:6} {row['battery_pct']:5} "
              f"{row['app_installed']:5} {row['ia_health']:3}  {r.get('detail','')}")

    print()
    print("[PREFLIGHT]", "PASS" if all_ok else "FAIL",
          "(checks bloqueantes: online, screen_awake, app_installed, ia_health)")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
