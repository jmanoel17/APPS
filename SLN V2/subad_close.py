#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
subad_close.py — SLN V2: detector estrutural CONSERVADOR do SUB_AD_CLOSE (WebView sub-ad).

Assinatura CONFIRMADA por evidência real (device 1080x2280, timeout do ciclo 4):

    android:id/content
    └─ FrameLayout [0,107][1080,2280]
       ├─ RelativeLayout > WebView > View[21,128][1060,2259] > TextView id=adFrame
       └─ FrameLayout [958,217][1028,287]  clickable=true  id/text/desc=""   <-- CLOSE
          └─ View [958,217][1028,287]

Regras (usar PROPORÇÕES, não pixels):
  - class FrameLayout, clickable=true, enabled, SEM resource-id/text/content-desc;
  - topo-direito pequeno: x0>=~0.84W, x1<=~0.99W, y0>=~0.04H, y1<=~0.20H, w<=~0.14W, h<=~0.10H;
  - exatamente 1 filho cujo bounds == (quase) o do pai;
  - contexto WebView/adFrame na MESMA tela;
  - rejeita CTA, containers grandes, off-zone, não-clicável.

Puro/testável (só stdlib + ElementTree). NÃO clica — apenas DETECTA.
"""
import re

DEFAULTS = {
    "x0_min": 0.84, "x1_max": 0.99, "y0_min": 0.04, "y1_max": 0.20,
    "w_min": 0.03, "w_max": 0.14, "h_min": 0.02, "h_max": 0.10,
    "max_w_frac": 0.40, "max_h_frac": 0.30,   # corte absoluto de "grande"
}


def _bounds(a):
    nums = re.findall(r"-?\d+", a.get("bounds", "") or "")
    if len(nums) != 4:
        return None
    x1, y1, x2, y2 = map(int, nums)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _has_text_or_cta(a, cta_re):
    txt = " ".join((a.get("resource-id", ""), a.get("text", ""), a.get("content-desc", "")))
    if a.get("resource-id", "") or a.get("text", "").strip() or a.get("content-desc", "").strip():
        return True
    if cta_re is not None and cta_re.search(txt.lower()):
        return True
    return False


def detect_subad_close(root, w, h, package, cta_re=None, params=None):
    """Retorna dict {found, bounds, center, norm, clickable, child_same_bounds, webview_context,
    reason, reject_reason}. Não clica."""
    p = dict(DEFAULTS)
    if params:
        p.update(params)
    nodes = list(root.iter("node"))
    has_wv = any("WebView" in (n.get("class", "") or "") and n.get("package", "") == package for n in nodes)
    has_adframe = any((n.get("resource-id", "") or "").endswith("adFrame") for n in nodes)
    webview_context = has_wv or has_adframe

    reject = "no_candidate"
    for n in nodes:
        a = n.attrib
        if a.get("package", "") != package:
            continue
        if not (a.get("class", "") or "").endswith("FrameLayout"):
            continue
        if a.get("clickable") != "true":
            reject = "not_clickable"
            continue
        if a.get("enabled", "true") == "false":
            reject = "not_enabled"
            continue
        if _has_text_or_cta(a, cta_re):
            reject = "has_text_or_cta"
            continue
        b = _bounds(a)
        if not b:
            reject = "no_bounds"
            continue
        x1, y1, x2, y2 = b
        bw, bh = x2 - x1, y2 - y1
        if bw > p["max_w_frac"] * w or bh > p["max_h_frac"] * h:
            reject = "too_large"
            continue
        x0n, y0n, x1n, y1n = x1 / w, y1 / h, x2 / w, y2 / h
        if not (x0n >= p["x0_min"] and x1n <= p["x1_max"]):
            reject = "not_top_right"
            continue
        if not (p["y0_min"] <= y0n and y1n <= p["y1_max"]):
            reject = "not_top_band"
            continue
        wf, hf = bw / w, bh / h
        if not (p["w_min"] <= wf <= p["w_max"] and p["h_min"] <= hf <= p["h_max"]):
            reject = "bad_size"
            continue
        kids = [c for c in n if c.tag == "node"]
        if len(kids) != 1:
            reject = "child_count"
            continue
        cb = _bounds(kids[0].attrib)
        if not cb:
            reject = "child_no_bounds"
            continue
        tol_x, tol_y = max(4, 0.01 * w), max(4, 0.01 * h)
        same = (abs(cb[0] - x1) <= tol_x and abs(cb[1] - y1) <= tol_y
                and abs(cb[2] - x2) <= tol_x and abs(cb[3] - y2) <= tol_y)
        if not same:
            reject = "child_shape_mismatch"
            continue
        if not webview_context:
            reject = "missing_webview_context"
            continue
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        return {"found": True, "bounds": (x1, y1, x2, y2), "center": (cx, cy),
                "norm": [round(x0n, 3), round(y0n, 3), round(x1n, 3), round(y1n, 3)],
                "clickable": True, "child_same_bounds": True, "webview_context": True,
                "reason": "subad_close_signature", "reject_reason": None}
    return {"found": False, "reason": "no_subad_close", "reject_reason": reject}


def authorize(gate, skip_on, clicks, max_per_ad, cooldown_ok):
    """AUTORIZAÇÃO (separada da detecção): só clica se o gate atual permitir. Reward gate fechado
    => detecta mas NÃO clica."""
    return bool(skip_on and gate and clicks < max_per_ad and cooldown_ok)


def dedupe_same(pending, bounds, last_bounds, stale):
    """True se é o MESMO candidato (mesmos bounds) ainda presente e a transição não ocorreu ->
    NÃO reclicar. Em WebView a árvore muda a cada tick (ruído), então a persistência confiável é
    a do CANDIDATO (bounds). Libera quando: candidato desaparece (o chamador limpa pending),
    bounds mudam, MAIN/store, ou `stale` (safety timeout)."""
    return bool(pending and bounds == last_bounds and not stale)
