#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adaptive_tick.py — SLN V2 (FASE 5/6, atrás de `adaptive_tick_enabled`, default FALSE)

Tick do watcher variável por ESTADO do anúncio. NÃO usa CPU/delay do host — só o estado local
do anúncio. Quando desligado, o bot mantém o tick fixo atual (`fast_close_tick_ms`).

Lógica pura e testável offline. Sem dependências.
"""

# Período inicial por estado (ms). Configurável por cfg["adaptive_tick_ms"] (dict).
DEFAULT_TICK_MS = {
    "main": 1200,
    "idle": 1500,
    "backoff": 2000,
    "no_fill": 2000,
    "video": 800,
    "video_rewarded": 1000,
    "countdown_active": 800,
    "countdown_ending": 300,
    "pill": 275,
    "reveal": 275,
    "store": 400,
    "endcard": 275,
    "recovery": 400,
}

# Estados que disparam BURST (frequência alta por poucos segundos).
_BURST_TRIGGERS = {"pill", "reveal", "countdown_ending", "store", "endcard", "recovery"}


def tick_ms_for(state_key, cfg):
    """Período (ms) do estado. Usa cfg['adaptive_tick_ms'] se houver, senão DEFAULT_TICK_MS;
    fallback final: `fast_close_tick_ms` (300)."""
    table = cfg.get("adaptive_tick_ms") or DEFAULT_TICK_MS
    try:
        return int(table.get(state_key, cfg.get("fast_close_tick_ms", 300)))
    except Exception:
        return 300


def state_key(phase=None, ad_type="unknown", cd_state=None, pill_visible=False,
              has_control=False, in_recovery=False, no_fill=False):
    """Mapeia o estado funcional do anúncio -> chave de tick. Puro/testável.

    - recovery tem prioridade (contingência pós-falha)
    - endcard/store/endcard-closing
    - pílula visível / reveal
    - countdown ending
    - rewarded sem countdown -> observação mais lenta
    - no_fill/idle/backoff -> bem lento
    """
    if in_recovery:
        return "recovery"
    if no_fill:
        return "no_fill"
    ph = (phase or "video")
    if ph in ("endcard", "closing_endcard"):
        return "endcard"
    if ph == "store_after_skip":
        return "store"
    if pill_visible:
        return "pill"
    if ph == "skipping_to_endcard":
        return "video"  # aguardando a loja/endcard; ainda "video" moderado
    if cd_state == "ending":
        return "countdown_ending"
    if ad_type == "rewarded":
        return "video_rewarded"
    return "video"


class BurstController:
    """BURST: ao ENTRAR num estado crítico, usa tick de burst (ex.: 250ms) por N segundos;
    depois volta ao tick do estado. Evita 300ms permanente."""

    def __init__(self, cfg):
        self.burst_ms = int(cfg.get("adaptive_tick_burst_ms", 250))
        self.burst_s = float(cfg.get("adaptive_tick_burst_seconds", 3.0))
        self._prev = None
        self._burst_until = None
        self.bursts = 0

    def effective_ms(self, state_key_, now, cfg):
        if state_key_ in _BURST_TRIGGERS and state_key_ != self._prev:
            self._burst_until = now + self.burst_s
            self.bursts += 1
        self._prev = state_key_
        if self._burst_until is not None and now < self._burst_until:
            return self.burst_ms
        return tick_ms_for(state_key_, cfg)
