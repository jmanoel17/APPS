#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sideload_gms_chrome.py — Atualiza GMS e Chrome via sideload (adb install-multiple), sem Play Store.

Fontes (devices já atualizados):
  GMS 100400 (32-bit) : 192.168.1.101:5555  (26.34.36)
  GMS 190400 (64-bit) : 192.168.1.124:5555  (26.34.36)
  Chrome (arm64)      : 192.168.1.124:5555  (154.0.8037.57)

Etapas:
  python sideload_gms_chrome.py --setup                 # puxa os APKs split das fontes p/ apks/
  python sideload_gms_chrome.py --devices <s1> <s2>     # instala nos devices
  python sideload_gms_chrome.py --all --limit 8         # instala em todos (concorrência)
  python sideload_gms_chrome.py --verify                # verifica versões finais

A variante de GMS de cada device é detectada pelo versionName (100400 vs 190400) e casada
com a fonte correspondente. Não altera dados do app (-r) e não usa conta Google.
"""
import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import time

ADB = "adb"
HERE = os.path.dirname(os.path.abspath(__file__))
APKS = os.path.join(HERE, "apks")
# Fontes validadas (Android SDK determina a variante):
#   Android 9 (sdk28)  -> GMS 100400 (minSdk28) + Chrome 138 (minSdk26)
#   Android 12 (sdk31) -> GMS 190400 (minSdk31) + Chrome 154 (minSdk29)
GMS_100400_SRC = "192.168.1.101:5555"   # SM-G955F Android 9
GMS_190400_SRC = "192.168.1.124:5555"   # SM-G975F Android 12
CHROME_138_SRC = "192.168.1.101:5555"   # Android 9 (última que suporta sdk28)
CHROME_154_SRC = "192.168.1.124:5555"   # Android 12
TARGET_GMS = "26.34.36"
# chrome alvo por SDK (major)
CHROME_TARGET = {"28": "138", "31": "154"}

PACKAGES = {
    "gms": "com.google.android.gms",
    "chrome": "com.android.chrome",
}


def sh(serial, *args, timeout=120):
    return subprocess.run([ADB, "-s", serial, "shell", *args],
                          capture_output=True, text=True, timeout=timeout)


def adb(serial, *args, timeout=600):
    return subprocess.run([ADB, "-s", serial, *args],
                          capture_output=True, text=True, timeout=timeout)


def adb_devices():
    out = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=10).stdout
    return [p[0] for ln in out.splitlines()[1:]
            if (p := ln.split()) and len(p) == 2 and p[1] == "device" and p[0].endswith(":5555")]


def split_paths(serial, pkg):
    r = sh(serial, "pm", "path", pkg)
    out = []
    for ln in r.stdout.splitlines():
        ln = ln.strip()
        if ln.startswith("package:"):
            out.append(ln[len("package:"):].strip())
    return out


def pull_pkg(serial, pkg, dest):
    os.makedirs(dest, exist_ok=True)
    paths = split_paths(serial, pkg)
    pulled = []
    for p in paths:
        name = os.path.basename(p)
        adb(serial, "pull", p, os.path.join(dest, name), timeout=300)
        pulled.append(name)
    return pulled


def setup():
    print("[SETUP] puxando GMS 100400 de", GMS_100400_SRC)
    g1 = pull_pkg(GMS_100400_SRC, PACKAGES["gms"], os.path.join(APKS, "gms_100400"))
    print("  gms_100400:", g1)
    print("[SETUP] puxando GMS 190400 de", GMS_190400_SRC)
    g2 = pull_pkg(GMS_190400_SRC, PACKAGES["gms"], os.path.join(APKS, "gms_190400"))
    print("  gms_190400:", g2)
    print("[SETUP] puxando Chrome 138 de", CHROME_138_SRC)
    c1 = pull_pkg(CHROME_138_SRC, PACKAGES["chrome"], os.path.join(APKS, "chrome_138"))
    print("  chrome_138:", c1)
    print("[SETUP] puxando Chrome 154 de", CHROME_154_SRC)
    c2 = pull_pkg(CHROME_154_SRC, PACKAGES["chrome"], os.path.join(APKS, "chrome_154"))
    print("  chrome_154:", c2)
    print("[SETUP] OK")


def gms_variant(serial):
    r = sh(serial, "dumpsys", "package", PACKAGES["gms"])
    m = re.search(r"versionName=(\S+)", r.stdout)
    if not m:
        return None
    v = m.group(1)
    if "190400" in v:
        return "190400", v
    if "100400" in v:
        return "100400", v
    return "?", v


def android_sdk(serial):
    r = sh(serial, "getprop", "ro.build.version.sdk")
    return r.stdout.strip()


def installed_version(serial, pkg):
    r = sh(serial, "dumpsys", "package", pkg)
    m = re.search(r"versionName=(\S+)", r.stdout)
    return m.group(1) if m else None


def _vtuple(v):
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except Exception:
        return (0,)


def _lt(a, b):
    return _vtuple(a) < _vtuple(b)


def install_splits(serial, split_dir, tries=3):
    files = sorted(os.listdir(split_dir))
    # base.apk primeiro
    files.sort(key=lambda f: 0 if f == "base.apk" else 1)
    paths = [os.path.join(split_dir, f) for f in files]
    for attempt in range(1, tries + 1):
        r = adb(serial, "install-multiple", "-r", *paths, timeout=900)
        out = (r.stdout + r.stderr).strip()
        if "Success" in out:
            return True, out.splitlines()[-1] if out.splitlines() else ""
        # erro transitório de conexão (wi-fi adb): reconecta e tenta de novo
        if "connect error" in out or "closed" in out or "offline" in out:
            time.sleep(5)
            subprocess.run([ADB, "connect", serial], capture_output=True, text=True, timeout=30)
            time.sleep(5)
            continue
        return False, out.splitlines()[-1] if out.splitlines() else out
    return False, "retries_exhausted"


def reconnect(serial):
    subprocess.run([ADB, "connect", serial], capture_output=True, text=True, timeout=30)


def needs_update(serial):
    """Retorna o que precisa instalar: gms (variante) e chrome (dir), por Android SDK."""
    res = {}
    sdk = android_sdk(serial)
    res["_sdk"] = sdk
    gv = installed_version(serial, PACKAGES["gms"])
    cv = installed_version(serial, PACKAGES["chrome"])
    # variante de GMS por SDK (validada): sdk28 -> 100400, sdk31 -> 190400
    var = "100400" if sdk == "28" else ("190400" if sdk == "31" else None)
    if var and (gv is None or _lt(gv, TARGET_GMS)):
        res["gms"] = var
    ctarget = CHROME_TARGET.get(sdk)
    if ctarget:
        cmajor = cv.split(".")[0] if cv else "0"
        if cmajor != ctarget:
            res["chrome"] = ctarget  # "138" ou "154"
    res["_gms_v"] = gv
    res["_chrome_v"] = cv
    return res


def process(serial):
    res = {"serial": serial, "gms": "skip", "chrome": "skip"}
    try:
        need = needs_update(serial)
        res["_sdk"] = need.get("_sdk")
        res["_gms_v"] = need.get("_gms_v")
        res["_chrome_v"] = need.get("_chrome_v")
        if need.get("gms"):
            d = os.path.join(APKS, "gms_" + need["gms"])
            ok, msg = install_splits(serial, d)
            res["gms"] = "ok" if ok else f"fail:{msg}"
            # update de GMS pode derrubar o adbd por instantes: reconecta e estabiliza
            # antes do Chrome (que é um push grande).
            reconnect(serial)
            time.sleep(15)
        if need.get("chrome"):
            d = os.path.join(APKS, "chrome_" + need["chrome"])
            ok, msg = install_splits(serial, d)
            res["chrome"] = "ok" if ok else f"fail:{msg}"
    except Exception as e:
        res["err"] = type(e).__name__
    return res


def verify():
    devs = adb_devices()
    gms_old = chrome_old = 0
    for s in devs:
        sdk = android_sdk(s)
        gv = installed_version(s, PACKAGES["gms"])
        cv = installed_version(s, PACKAGES["chrome"])
        gf = "OK" if gv and not _lt(gv, TARGET_GMS) else f"OLD({gv})"
        ctarget = CHROME_TARGET.get(sdk, "?")
        cf = "OK" if cv and cv.split(".")[0] == ctarget else f"OLD({cv})"
        if "OLD" in gf:
            gms_old += 1
        if "OLD" in cf:
            chrome_old += 1
        print(f"{s}  sdk={sdk}  gms={gf}  chrome={cf}")
    print(f"\nVERIFY: gms antigo={gms_old}  chrome antigo={chrome_old}  total={len(devs)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--devices", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=6)
    args = ap.parse_args()

    if args.setup:
        setup()
        return
    if args.verify:
        verify()
        return

    devs = adb_devices() if args.all else (args.devices or [])
    devs = list(dict.fromkeys(devs))
    if not devs:
        print("Nenhum device. Use --all ou --devices.")
        sys.exit(2)

    print(f"Sideload em {len(devs)} devices (limit={args.limit})...", flush=True)
    ok = fail = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.limit) as ex:
        futs = {ex.submit(process, s): s for s in devs}
        for f in concurrent.futures.as_completed(futs):
            r = f.result()
            line = f"{r['serial']}  gms={r.get('gms')}  chrome={r.get('chrome')}"
            if "_sdk" in r or "_gms_v" in r or "_chrome_v" in r:
                line += f"  [sdk={r.get('_sdk')} antes g={r.get('_gms_v')} c={r.get('_chrome_v')}]"
            if r.get("err"):
                line += f"  err={r['err']}"
            print(line, flush=True)
            for k in ("gms", "chrome"):
                if r.get(k) == "ok":
                    ok += 1
                elif r.get(k) not in ("skip",):
                    fail += 1
    print(f"\nFIM ok={ok} fail={fail}")


if __name__ == "__main__":
    main()
