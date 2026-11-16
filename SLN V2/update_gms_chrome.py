#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_gms_chrome.py — Atualiza com.google.android.gms e com.android.chrome via Play Store (adb).

Para cada device, abre a página do app no Play Store e, se houver botão "Atualizar"/"Instalar",
toca nele e aguarda concluir. Reporta o resultado por device/app.

Uso:
  python update_gms_chrome.py --devices 192.168.1.100:5555 192.168.1.101:5555
  python update_gms_chrome.py --all
  python update_gms_chrome.py --limit 8   (concorrência)

Não altera nada além de iniciar o update no Play Store (que precisa de conta Google autenticada).
"""
import argparse
import concurrent.futures
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import uiautomator2 as u2

APPS = {
    "gms": "com.google.android.gms",
    "chrome": "com.android.chrome",
}
URLS = {
    "gms": "https://play.google.com/store/apps/details?id=com.google.android.gms&hl=pt_BR",
    "chrome": "https://play.google.com/store/apps/details?id=com.android.chrome&hl=pt_BR",
}
UPDATE_TEXTS = {"Atualizar", "Update", "Instalar", "Install", "Mettre à jour", "Installer",
                "Actualizar", "Instal·lar", "Aggiorna", "Installa", "Aktualisieren", "Installieren"}
OPEN_TEXTS = {"Abrir", "Open", "Ouvrir", "Desinstalar", "Uninstall", "Désinstaller",
              "Remova atualiz.", "Remove updates", "Supprimer les mises à jour",
              "Desativar", "Disable", "Désactiver", "Ativar", "Enable"}
AUTH_TEXTS = {"autenticação é necessária", "authentication is required", "authentification",
              "É necessário fazer login", "You need to sign in"}


def _adb_devices():
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10).stdout
    devs = []
    for ln in out.splitlines()[1:]:
        p = ln.split()
        if len(p) == 2 and p[1] == "device" and p[0].endswith(":5555"):
            devs.append(p[0])
    return devs


def _bounds_center(node):
    b = node.attrib.get("bounds", "")
    nums = list(map(int, re.findall(r"\d+", b)))
    if len(nums) != 4:
        return None
    x1, y1, x2, y2 = nums
    return (x1 + x2) // 2, (y1 + y2) // 2


def _find_action(root):
    """Retorna (kind, cx, cy). kind: update/install/open/auth/none."""
    texts = []
    for node in root.iter("node"):
        t = node.attrib.get("text", "")
        if not t:
            continue
        texts.append(t)
        if t in UPDATE_TEXTS:
            c = _bounds_center(node)
            if c:
                return ("update", c[0], c[1])
    for node in root.iter("node"):
        t = node.attrib.get("text", "")
        if t in OPEN_TEXTS:
            c = _bounds_center(node)
            if c:
                return ("open", c[0], c[1])
    blob = " ".join(texts).lower()
    for a in AUTH_TEXTS:
        if a in blob:
            return ("auth", 0, 0)
    return ("none", 0, 0)


def update_app(d, url):
    d.shell(["am", "start", "-a", "android.intent.action.VIEW", "-d", url])
    time.sleep(7)
    for _ in range(8):
        try:
            xml = d.dump_hierarchy()
            root = ET.fromstring(xml)
        except Exception:
            time.sleep(4)
            continue
        kind, cx, cy = _find_action(root)
        if kind == "update":
            d.click(cx, cy)
            time.sleep(5)
            continue
        if kind == "open":
            return "up_to_date"
        if kind == "auth":
            return "auth_required"
        time.sleep(4)
    return "timeout"


def process(serial):
    res = {"serial": serial}
    try:
        d = u2.connect(serial)
        if not d.info:
            return {**res, "gms": "offline", "chrome": "offline"}
        res["gms"] = update_app(d, URLS["gms"])
        res["chrome"] = update_app(d, URLS["chrome"])
    except Exception as e:
        res["gms"] = res.get("gms", "ERR")
        res["chrome"] = res.get("chrome", "ERR")
        res["err"] = type(e).__name__
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()

    devs = _adb_devices() if args.all else (args.devices or [])
    devs = list(dict.fromkeys(devs))
    if not devs:
        print("Nenhum device. Use --all ou --devices.")
        sys.exit(2)

    print(f"Atualizando {len(devs)} devices (concorrência={args.limit})...", flush=True)
    ok = fail = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.limit) as ex:
        futs = {ex.submit(process, s): s for s in devs}
        for f in concurrent.futures.as_completed(futs):
            r = f.result()
            print(f"{r['serial']}  gms={r.get('gms')}  chrome={r.get('chrome')}  {r.get('err','')}", flush=True)
            for k in ("gms", "chrome"):
                if r.get(k) in ("up_to_date", "update"):
                    ok += 1
                else:
                    fail += 1
    print(f"\nFIM ok={ok} fail={fail}")

if __name__ == "__main__":
    main()
