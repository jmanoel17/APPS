#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
find_button_devices.py — SLN V2: descobre QUAIS devices exibem o botão de reward.

Botão alvo (mesma detecção do bot do projeto):
  - resource-id: com.flixdigital.dramaclassic:id/btnRewardAds
  - texto:       "Assistir anúncio"

Rotaciona pelos devices online: abre o app, dá alguns swipes (o botão às vezes exige scroll),
dumpa a UI via adb (uiautomator) e marca quem mostra o botão. READ-ONLY quanto ao fluxo de anúncio
(não clica no botão).

Uso:
  python find_button_devices.py                 # todos os online
  python find_button_devices.py --devices <d1> <d2>
"""
import argparse
import concurrent.futures
import os
import subprocess
import time

PKG = "com.flixdigital.dramaclassic"
BTN_ID = "com.flixdigital.dramaclassic:id/btnRewardAds"
BTN_TXT = "Assistir anúncio"
BASE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(BASE, "benchmarks")


def adb(dev, args, timeout=20):
    try:
        return subprocess.run(["adb", "-s", dev] + args, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="ignore").stdout or ""
    except Exception:
        return ""


def online():
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10).stdout
    return [ln.split("\t")[0] for ln in out.strip().split("\n")[1:]
            if ln.strip() and len(ln.split()) == 2 and ln.split()[1] == "device" and ln.split()[0].endswith(":5555")]


def ui_dump(dev):
    # dump para arquivo e lê (mais robusto que /dev/tty em algumas versões)
    adb(dev, ["shell", "uiautomator", "dump", "/sdcard/sln_ui.xml"], timeout=25)
    return adb(dev, ["exec-out", "cat", "/sdcard/sln_ui.xml"], timeout=20)


def cur_activity(dev):
    o = adb(dev, ["shell", "dumpsys", "window"], timeout=15)
    for ln in o.splitlines():
        if "mCurrentFocus" in ln or "mFocusedApp" in ln:
            return ln.strip()
    return ""


def check(dev):
    adb(dev, ["shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1"], timeout=20)
    time.sleep(2.5)
    found = False
    cas = False
    for _ in range(4):
        xml = ui_dump(dev)
        if BTN_ID in xml or BTN_TXT in xml:
            found = True
            break
        if "YES, I AGREE" in xml.upper():
            cas = True
        adb(dev, ["shell", "input", "swipe", "540", "1600", "540", "800", "300"], timeout=10)
        time.sleep(0.8)
    return dev, found, cas, cur_activity(dev)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    devs = args.devices or online()
    print("devices:", len(devs), flush=True)

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for dev, found, cas, act in ex.map(check, devs):
            results[dev] = (found, cas, act)
            print("  %-22s button=%-5s cas=%-5s %s" % (dev, found, cas, act[:70]), flush=True)

    found_devs = sorted(d for d in devs if results[d][0])
    os.makedirs(BENCH, exist_ok=True)
    f = os.path.join(BENCH, "button_devices_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("# devices com o botao (btnRewardAds / 'Assistir anúncio') em %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        for d in found_devs:
            fh.write(d + "\n")
    print("\nCOM BOTAO: %d/%d" % (len(found_devs), len(devs)), flush=True)
    print("lista:", found_devs, flush=True)
    print("salvo em:", f, flush=True)


if __name__ == "__main__":
    main()
