#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diag_controls.py — SLN V2: CAPTURA DIAGNÓSTICA de controles (READ-ONLY, NÃO clica).

Investiga DUAS famílias separadas durante o Fast Close:
  A) COMPACT TOP CONTROL ">|" (topo; pode ser icon-only, sem texto 'Google Play')
  B) RIGHT-SIDE SUB-AD CLOSE (lateral direita; sub-ad/stage)

Faz polling do uiautomator dump (~1s) e:
  - extrai candidatos de cada família;
  - detecta MUDANÇA na região direita/topo (XML anterior vs atual) e salva XML+PNG;
  - registra telemetria estruturada (assinatura, bounds, duração visível, gate).

Uso:
  python diag_controls.py --device 192.168.1.44:5555 --minutes 10
"""
import argparse
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "errors", "diag_controls")
PKG = "com.flixdigital.dramaclassic"


def adb(dev, args, timeout=20):
    try:
        return subprocess.run(["adb", "-s", dev] + args, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="ignore").stdout or ""
    except Exception:
        return ""


def dump_xml(dev):
    adb(dev, ["shell", "uiautomator", "dump", "/sdcard/sln_diag.xml"], 25)
    return adb(dev, ["exec-out", "cat", "/sdcard/sln_diag.xml"], 20)


def screencap_bytes(dev):
    try:
        return subprocess.run(["adb", "-s", dev, "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=20).stdout
    except Exception:
        return b""


def bounds(s):
    m = [int(x) for x in re.findall(r"-?\d+", s or "")]
    return m if len(m) == 4 else None


def parse(xml):
    out = []
    try:
        root = ET.fromstring(xml)
    except Exception:
        return out, 1080, 2280
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        for ch in list(node):
            if ch.tag != "node":
                continue
            a = ch.attrib
            b = bounds(a.get("bounds", ""))
            if b:
                out.append({"cls": a.get("class", ""), "id": a.get("resource-id", ""),
                            "text": a.get("text", ""), "desc": a.get("content-desc", ""),
                            "click": a.get("clickable"), "en": a.get("enabled"),
                            "b": b, "depth": depth,
                            "w": b[2]-b[0], "h": b[3]-b[1],
                            "cx": (b[0]+b[2])//2, "cy": (b[1]+b[3])//2})
            stack.append((ch, depth+1))
    W = max((n["b"][2] for n in out), default=1080)
    H = max((n["b"][3] for n in out), default=2280)
    return out, W, H


def top_candidates(nodes, W, H):
    res = []
    for n in nodes:
        # topo (y ~ 0.04..0.13H), não full-width, pequeno
        if not (0.03*H <= n["cy"] <= 0.14*H):
            continue
        if n["w"] < 0.10*W or n["w"] > 0.40*W or n["h"] < 8 or n["h"] > 0.08*H:
            continue
        txt = (n["text"] + " " + n["desc"]).strip()
        is_gp = "google play" in txt.lower()
        # família topo: container/ícone pequeno à esquerda/topo (pílula ou >|)
        left = n["cx"] < 0.45*W
        if not left:
            continue
        small = n["w"] <= 0.35*W and n["h"] <= 0.07*H
        if not small:
            continue
        variant = "google_play_pill" if is_gp else ("compact_skip" if n["w"] >= 0.10*W else "top_small")
        res.append({**n, "variant": variant})
    return res


def right_candidates(nodes, W, H, seen_ids):
    res = []
    bad_words = ("instalar", "install", "google play", "playlink", "abrir", "jogar", "cancelar")
    for n in nodes:
        if n["cx"] < 0.60*W:
            continue
        if n["w"] > 0.28*W or n["h"] > 0.16*H:   # evita containers/CTAs grandes
            continue
        if n["w"] < 14 or n["h"] < 14:
            continue
        if n["w"] >= 0.9*W and n["h"] >= 0.5*H:  # raiz
            continue
        txt = (n["text"] + " " + n["desc"]).lower()
        if any(bw in txt for bw in bad_words):
            continue
        closeish = any(s in (n["id"] + n["text"] + n["desc"]).lower()
                       for s in ("close", "fechar", "dismiss", "skip", "x", "cross", "cancel"))
        clickable = n["click"] == "true"
        # assinatura conservadora: clicável OU close-ish; tamanho tipo botão
        if clickable or closeish:
            res.append({**n, "closeish": closeish})
    return res


def sig(nodes):
    return tuple(sorted((n["id"], n["b"][0], n["b"][1], n["b"][2], n["b"][3], n["text"], n["desc"]) for n in nodes))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()
    dev = args.device
    os.makedirs(OUT, exist_ok=True)
    logp = os.path.join(OUT, "diag_controls_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
    logf = open(logp, "w", encoding="utf-8")

    def log(m):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), m)
        print(line, flush=True)
        logf.write(line + "\n"); logf.flush()

    log("DIAG CONTROLS device=%s minutes=%s interval=%s -> %s" % (dev, args.minutes, args.interval, logp))
    prev_right = None
    prev_top = None
    top_seen = {}   # sig -> first ts, count
    right_seen = {}
    t0 = time.time()
    while time.time() - t0 < args.minutes * 60:
        xml = dump_xml(dev)
        nodes, W, H = parse(xml)
        if not nodes:
            time.sleep(args.interval); continue
        tops = top_candidates(nodes, W, H)
        rights = right_candidates(nodes, W, H, set())
        ts = time.strftime("%H:%M:%S")
        # topo: registra variantes (compact_skip vs google_play_pill)
        tsg = sig([{k: n[k] for k in ("id","text","desc","b")} for n in tops])
        variants = sorted(set(n["variant"] for n in tops))
        if tops and tsg != prev_top:
            info = "; ".join("%s b=%s txt=%r desc=%r" % (n["variant"], n["b"], n["text"][:16], n["desc"][:16]) for n in tops[:4])
            log("TOP n=%d variants=%s | %s" % (len(tops), variants, info))
            if "compact_skip" in variants:
                art = os.path.join(OUT, "compact_top_%s" % time.strftime("%H%M%S"))
                open(art + ".xml", "w", encoding="utf-8").write(xml)
                with open(art + ".png", "wb") as f:
                    f.write(screencap_bytes(dev))
                log("  [CAPTURE] compact_skip salvo em %s.xml/.png" % art)
        prev_top = tsg
        # direita: registra mudança (novo candidato)
        rsg = sig([{k: n[k] for k in ("id","text","desc","b")} for n in rights])
        if rights and rsg != prev_right:
            log("RIGHT n=%d | %s" % (len(rights), "; ".join(
                "%s id=%s b=%s clk=%s cl=%s desc=%r" % (n["cls"].split(".")[-1], n["id"].split("/")[-1],
                n["b"], n["click"], n["closeish"], n["desc"][:16]) for n in rights[:5])))
            art = os.path.join(OUT, "right_close_%s" % time.strftime("%H%M%S"))
            open(art + ".xml", "w", encoding="utf-8").write(xml)
            with open(art + ".png", "wb") as f:
                f.write(screencap_bytes(dev))
            log("  [CAPTURE] right_close salvo em %s.xml/.png" % art)
        prev_right = rsg
        time.sleep(args.interval)
    log("FIM")


if __name__ == "__main__":
    main()
