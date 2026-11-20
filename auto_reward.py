import argparse
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

APPS = {
    "1": ("com.MW.MxMotosV2", "Mx MotosV2"),
    "2": ("com.flixdigital.dramaclassic", "cas"),
    "3": ("com.MW.MxPuxouNoGrau", "Puxo No Grau"),
    "4": ("com.ManecoGames.MxBikesGrau", "Bike Mx"),
    "5": ("com.Wg.GrauBahia", "Grau Bahia"),
    "6": ("com.ManecoGames.BikesMilGrau", "Bikes Mil Grau"),
}

DEFAULT_DEVICE = "RQ8N70C636W"
ACTIVITY = ".MainActivity"
CONSENT_TEXT = "YES, I AGREE"
REWARD_ID = "{}:id/btnRewardAds"
CONSENT_ID = "{}:id/cas_consent_accept"
READY_MARK = "ASSISTIR"

CREATION_FLAGS = 0x08000000 if os.name == "nt" else 0


def adb(device, *args, timeout=30):
    cmd = ["adb", "-s", device] + list(args)
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATION_FLAGS,
        )
        return out.stdout or ""
    except subprocess.TimeoutExpired:
        return ""


def shell(device, command, timeout=30):
    return adb(device, "shell", command, timeout=timeout)


def dump_ui(device):
    shell(device, "uiautomator dump /sdcard/_auto_ui.xml")
    xml = shell(device, "cat /sdcard/_auto_ui.xml")
    start = xml.find("<?xml")
    end = xml.rfind("</hierarchy>")
    if start == -1 or end == -1:
        return None
    try:
        return ET.fromstring(xml[start : end + len("</hierarchy>")])
    except ET.ParseError:
        return None


def center(bounds):
    m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    if x2 <= x1 and y2 <= y1:
        return None
    return (x1 + x2) // 2, (y1 + y2) // 2


def node_bounds(node):
    return center(node.get("bounds"))


def is_shown(node):
    pt = node_bounds(node)
    return pt is not None


def find_node(root, resource_id=None, text=None):
    for node in root.iter("node"):
        if resource_id and node.get("resource-id") != resource_id:
            continue
        if text and text.lower() not in (node.get("text") or "").lower():
            continue
        if is_shown(node):
            return node
    return None


def tap(device, point):
    shell(device, "input tap {} {}".format(point[0], point[1]))


def launch(device, package):
    shell(device, "am force-stop {}".format(package))
    shell(device, "am start -W -n {}/{}".format(package, ACTIVITY), timeout=60)


def handle_app(device, package, label, timeout, settle):
    print("[{}] abrindo {}".format(label, package))
    launch(device, package)
    consent_done = False
    clicked = False
    deadline = time.time() + timeout
    while time.time() < deadline:
        root = dump_ui(device)
        if root is not None:
            if not consent_done:
                consent = find_node(root, CONSENT_ID.format(package)) or find_node(
                    root, text=CONSENT_TEXT
                )
                if consent is not None and consent.get("clickable") == "true":
                    pt = node_bounds(consent)
                    if pt:
                        tap(device, pt)
                        consent_done = True
                        print("[{}] consentimento aceito".format(label))
                        time.sleep(1.5)
                        continue
            button = find_node(root, REWARD_ID.format(package))
            if button is not None:
                ready = button.get("clickable") == "true" and READY_MARK in (
                    button.get("text") or ""
                ).upper()
                if ready:
                    pt = node_bounds(button)
                    if pt:
                        tap(device, pt)
                        clicked = True
                        print(
                            "[{}] botao 1 clicado ({})".format(label, button.get("text"))
                        )
                        break
        time.sleep(1.0)
    if not clicked:
        print("[{}] botao 1 nao ficou pronto em {}s".format(label, timeout))
    time.sleep(settle)
    return clicked


def resolve(selectors):
    if not selectors:
        return list(APPS.values())
    chosen = {}
    for sel in selectors:
        key = sel.strip()
        if key.lower() == "all":
            return list(APPS.values())
        if key in APPS:
            chosen[key] = APPS[key]
            continue
        match = [(k, v) for k, v in APPS.items() if v[0].lower() == key.lower()]
        if not match:
            match = [
                (k, v)
                for k, v in APPS.items()
                if key.lower() in v[0].lower() or key.lower() in v[1].lower()
            ]
        if len(match) == 1:
            chosen[match[0][0]] = match[0][1]
        else:
            print("seletor ignorado (nao encontrado ou ambiguo): {}".format(sel))
    return [chosen[k] for k in sorted(chosen, key=int)]


def ask():
    print("Apps disponiveis:")
    for key, (package, label) in APPS.items():
        print("  {}  {:<22} {}".format(key, package, label))
    raw = input("Quais apps? (ex: 1,3 4  ou  all): ").strip()
    if not raw:
        return []
    return raw.replace(",", " ").split()


def main():
    parser = argparse.ArgumentParser(
        description="Abre apps e clica no botao 1 (ASSISTIR ANUNCIO)."
    )
    parser.add_argument("apps", nargs="*", help="indices/pacotes, ou 'all'")
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--timeout", type=int, default=40)
    parser.add_argument("--settle", type=float, default=2.0)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for key, (package, label) in APPS.items():
            print("{}  {:<22} {}".format(key, package, label))
        return 0

    selectors = args.apps or ask()
    targets = resolve(selectors)
    if not targets:
        print("nenhum app selecionado")
        return 1

    print("device: {} | apps: {}".format(args.device, len(targets)))
    failures = 0
    for package, label in targets:
        if not handle_app(args.device, package, label, args.timeout, args.settle):
            failures += 1
    print("concluido: {} ok, {} falha(s)".format(len(targets) - failures, failures))
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
