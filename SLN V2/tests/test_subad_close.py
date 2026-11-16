#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_subad_close.py — SLN V2 (detector estrutural SUB_AD_CLOSE, OFFLINE)

Replay dos dumps REAIS + negativos + sintéticos. Não clica.

Rodar:  python tests/test_subad_close.py
"""
import os
import sys
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import subad_close as SC

PASS = 0
FAIL = 0
PKG = "com.flixdigital.dramaclassic"
ERR = os.path.join(BASE, "errors")


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  ", name)
    else:
        FAIL += 1
        print("  FAIL", name)


def load(fn):
    p = os.path.join(ERR, fn)
    return ET.fromstring(open(p, "rb").read().decode("utf-8", "ignore")) if os.path.exists(p) else None


def detect(root, w=1080, h=2280):
    return SC.detect_subad_close(root, w, h, PKG)


POS = "fastclose_timeout_192_168_1_44_5555_20261002_010347_147.xml"


def test_positive():
    print("[replay] POSITIVO (ciclo 4)")
    root = load(POS)
    if root is None:
        check("dump positivo presente", False); return
    r = detect(root)
    check("found=True", r["found"] is True)
    check("bounds=[958,217][1028,287]", r.get("bounds") == (958, 217, 1028, 287))
    check("center=(993,252)", r.get("center") == (993, 252))
    check("norm ~ [0.887,0.095,0.952,0.126]", r.get("norm") == [0.887, 0.095, 0.952, 0.126])
    check("clickable/child_same/webview=True",
          r.get("clickable") and r.get("child_same_bounds") and r.get("webview_context"))


def test_negatives_real():
    print("[replay] NEGATIVOS (dumps reais)")
    cases = [
        ("install interstitial (00:34)", "fastclose_diag_reward_sem_countdown_192_168_1_44_5555_20261002_003401_213.xml"),
        ("store (01:02:37)", "fastclose_store_192_168_1_44_5555_20261002_010237_663.xml"),
        ("rewarded timeout recovery-exhausted (00:32:50)", "fastclose_recovery_exhausted_192_168_1_44_5555_20261002_003250_327.xml"),
        ("rewarded t40 (00:31:50)", "fastclose_diag_t40_192_168_1_44_5555_20261002_003150_797.xml"),
    ]
    for name, fn in cases:
        root = load(fn)
        if root is None:
            check("presente: " + name, False); continue
        r = detect(root)
        check("NÃO casa: " + name + " (reject=%s)" % r.get("reject_reason"), r["found"] is False)


def _mk(bounds, clickable="true", cls="android.widget.FrameLayout", rid="", text="", desc="",
        child_bounds=None, webview=True):
    def node(attrs):
        a = " ".join('%s="%s"' % (k, v) for k, v in attrs.items())
        return a
    kids = ""
    if child_bounds is not None:
        kids = '<node class="android.view.View" bounds="%s" />' % child_bounds
    wv = '<node class="android.webkit.WebView" package="%s" bounds="[0,0][1080,2280]" />' % PKG if webview else ""
    main = '<node class="%s" package="%s" bounds="%s" clickable="%s" resource-id="%s" text="%s" content-desc="%s">%s</node>' % (
        cls, PKG, bounds, clickable, rid, text, desc, kids)
    xml = '<hierarchy>%s%s</hierarchy>' % (wv, main)
    return ET.fromstring(xml)


def test_synthetic():
    print("[replay] NEGATIVOS sintéticos")
    check("positive synthetic ok",
          detect(_mk("[958,217][1028,287]", child_bounds="[958,217][1028,287]"))["found"] is True)
    check("clickable=false -> reject",
          detect(_mk("[958,217][1028,287]", clickable="false", child_bounds="[958,217][1028,287]"))["found"] is False)
    check("too_large -> reject",
          detect(_mk("[100,100][900,900]", child_bounds="[100,100][900,900]"))["found"] is False)
    check("wrong region (left) -> reject",
          detect(_mk("[50,217][120,287]", child_bounds="[50,217][120,287]"))["found"] is False)
    check("no webview/adFrame context -> reject",
          detect(_mk("[958,217][1028,287]", child_bounds="[958,217][1028,287]", webview=False))["found"] is False)
    check("CTA text -> reject",
          detect(_mk("[958,217][1028,287]", text="Instalar", child_bounds="[958,217][1028,287]"))["found"] is False)
    check("resource-id presente -> reject",
          detect(_mk("[958,217][1028,287]", rid="x:y", child_bounds="[958,217][1028,287]"))["found"] is False)
    check("child bounds mismatch -> reject",
          detect(_mk("[958,217][1028,287]", child_bounds="[0,0][10,10]"))["found"] is False)
    check("child count 0 -> reject",
          detect(_mk("[958,217][1028,287]", child_bounds=None))["found"] is False)
    check("root FrameLayout full-screen -> reject",
          detect(_mk("[0,0][1080,2280]", child_bounds="[0,0][1080,2280]"))["found"] is False)


def test_different_screen_size():
    print("[replay] resolução diferente (720x1520, escala ~0.667x)")
    # escala 958,217,1028,287 por 720/1080=0.6667 (x) e 1520/2280=0.6667 (y)
    b = "[%d,%d][%d,%d]" % (round(958*720/1080), round(217*1520/2280), round(1028*720/1080), round(287*1520/2280))
    r = detect(_mk(b, child_bounds=b), w=720, h=1520)
    check("encontra em 720x1520", r["found"] is True)
    check("center ~ (662,168)", abs(r["center"][0]-662) <= 3 and abs(r["center"][1]-168) <= 3)


def test_reward_gate_authorization():
    print("[auth] reward gate fechado -> detecta mas NÃO autoriza")
    r = detect(_mk("[958,217][1028,287]", child_bounds="[958,217][1028,287]"))
    check("detecta", r["found"] is True)
    check("gate fechado -> authorize False", SC.authorize(gate=False, skip_on=True, clicks=0, max_per_ad=2, cooldown_ok=True) is False)
    check("gate aberto + skip_on -> authorize True", SC.authorize(gate=True, skip_on=True, clicks=0, max_per_ad=2, cooldown_ok=True) is True)
    check("max cliques -> False", SC.authorize(gate=True, skip_on=True, clicks=2, max_per_ad=2, cooldown_ok=True) is False)
    check("cooldown -> False", SC.authorize(gate=True, skip_on=True, clicks=0, max_per_ad=2, cooldown_ok=False) is False)
    check("skip_on False -> False", SC.authorize(gate=True, skip_on=False, clicks=0, max_per_ad=2, cooldown_ok=True) is False)


def test_dedupe():
    print("[dedupe] mesmo candidato (mesmos bounds) -> nao reclica; some/muda/stale -> libera")
    b = (958, 217, 1028, 287)
    check("mesmo bounds + pending + nao-stale -> NAO clicar (awaiting)",
          SC.dedupe_same(True, b, b, False) is True)
    check("candidato sumiu (pending limpo) -> libera",
          SC.dedupe_same(False, b, b, False) is False)
    check("novo candidato com bounds diferentes -> libera",
          SC.dedupe_same(True, (900, 200, 970, 270), b, False) is False)
    check("safety timeout (stale) -> libera",
          SC.dedupe_same(True, b, b, True) is False)
    check("gate bloqueou (sem clique) -> pending False -> dedupe False",
          SC.dedupe_same(False, b, None, False) is False)


def main():
    test_positive()
    test_negatives_real()
    test_synthetic()
    test_different_screen_size()
    test_reward_gate_authorization()
    test_dedupe()
    print("\nRESULT: PASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
