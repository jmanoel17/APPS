#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
select90.py — monta o set de 90 devices (C60 clean + 30 READY), com preflight READY.

Preserva os 60 do C60 clean quando READY; adiciona READY até 90; registra substituições.
Salva benchmarks/90_devices_<ts>.txt.
"""
import concurrent.futures
import subprocess
import time

PKG = "com.flixdigital.dramaclassic"
BASE = r"C:\AndroidProjects\APPS\SLN V2"
C60 = [l.strip() for l in open(BASE + r"\benchmarks\60_devices_20260930_010849.txt", encoding="utf-8")
       if l.strip() and not l.startswith("#")]


def adb(dev, args, timeout=15):
    try:
        return subprocess.run(["adb", "-s", dev] + args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def online():
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10).stdout
    return [ln.split("\t")[0] for ln in out.strip().split("\n")[1:]
            if ln.strip() and len(ln.split()) == 2 and ln.split()[1] == "device" and ln.split()[0].endswith(":5555")]


def check(dev):
    logd = adb(dev, ["shell", "getprop", "init.svc.logd"], 8).strip()
    wake = "mWakefulness=Awake" in adb(dev, ["shell", "dumpsys", "power"], 10)
    pkg = PKG in adb(dev, ["shell", "pm", "list", "packages"], 12)
    conn = adb(dev, ["shell", "dumpsys", "connectivity"], 12)
    net = ("VALIDATED" in conn) or ("INTERNET" in conn and "Active default network" in conn)
    ready = (logd == "running") and wake and pkg and net
    return dev, ready, logd, wake, pkg, net


def main():
    devs = online()
    print("online:", len(devs), flush=True)
    res = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for dev, ready, logd, wake, pkg, net in ex.map(check, devs):
            res[dev] = (ready, logd, wake, pkg, net)
    ready = [d for d in devs if res[d][0]]
    print("READY:", len(ready), flush=True)

    chosen = [d for d in C60 if d in res and res[d][0]]
    sub = [d for d in C60 if d not in chosen]
    if sub:
        print("C60 nao-READY (substituidos):", sub, flush=True)
    added = []
    for d in sorted(ready):
        if d not in chosen and len(chosen) < 90:
            chosen.append(d); added.append(d)
    chosen = chosen[:90]
    ts = time.strftime("%Y%m%d_%H%M%S")
    f = BASE + r"\benchmarks\90_devices_%s.txt" % ts
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("# 90-device set; READY(real)=%d; base=C60(%d de 60)\n" % (len(ready), len([d for d in C60 if d in res and res[d][0]])))
        if sub:
            fh.write("# substituicoes (C60 nao-READY): %s\n" % ",".join(sub))
        fh.write("# adicionados (READY): %s\n" % ",".join(added))
        for d in chosen:
            fh.write(d + "\n")
    print("escolhidos:", len(chosen), "->", f, flush=True)
    print("added:", len(added), flush=True)
    if len(chosen) < 90:
        print("AVISO: menos de 90 READY (%d) — rodar com o numero real." % len(chosen), flush=True)


if __name__ == "__main__":
    main()
