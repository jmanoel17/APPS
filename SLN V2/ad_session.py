#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ad_session.py — SLN V2: MODELAGEM de CICLO / AD SESSION (contabilidade).

SEMÂNTICA (correta):
    1 CLIQUE em "Assistir anúncio" = 1 CYCLE = 1 AD SESSION
Dentro da mesma sessão podem existir VÁRIOS STAGES (vídeo, playable, endcard, Store,
tela intermediária, novo creative...). FECHAR UM CONTROLE NÃO FINALIZA O CICLO.

Regras:
  - CYCLE_START quando a sessão de anúncio realmente inicia (após o clique).
  - Cada passagem por um conteúdo = um STAGE. Fechar um stage que NÃO volta à MAIN =>
    STAGE_CLOSED + CONTINUE (mesma sessão), stage_index += 1.
  - CYCLE_COMPLETED somente quando: houve clique, a sessão iniciou, a cadeia foi processada,
    NÃO houve escape forçado e a MAIN foi confirmada ao final.
  - Retorno forçado (HOME + force-stop após recovery exhausted/timeout) => CYCLE_FAILED
    (mesmo que o app seja reaberto depois).
  - `reset interval` (2..30) conta CICLOS COMPLETOS, não stages/telas/X.

Este módulo NÃO altera Fast Close/reward/FORMAT/pill/Store/BACK/recovery: é só contabilidade
e telemetria. Não emite nova solicitação ao app (um clique por ciclo).
"""
import time

# telemetria (prefixos pedidos)
CYCLE_START = "[QA][CYCLE_START]"
CYCLE_COMPLETED = "[QA][CYCLE_COMPLETED]"
CYCLE_FAILED = "[QA][CYCLE_FAILED]"
CYCLE_ABORTED = "[QA][CYCLE_ABORTED]"
SESSION_START = "[AD_SESSION][START]"
SESSION_STAGE = "[AD_SESSION][STAGE]"
SESSION_STAGE_CLOSED = "[AD_SESSION][STAGE_CLOSED]"
SESSION_CONTINUE = "[AD_SESSION][CONTINUE]"

# resultados de fim de ciclo
COMPLETED = "COMPLETED"
FAILED = "FAILED"
ABORTED = "ABORTED"


class CycleSession:
    """Estado de UM ciclo/ad-session. Uma instância por bot (reutilizada)."""

    def __init__(self, device, log_fn=None):
        self.device = device
        self.log = log_fn or (lambda *a, **k: None)
        self.active = False
        self.cycle_id = 0
        self.stage_index = 0
        self.start_ts = None
        self.clicked = False
        self.stages = []          # [{stage, activity, format, close_layer}]

    # ---------- ciclo ----------
    def begin(self, activity="", ad_format=None):
        """Inicia o ciclo/sessão APÓS o clique e a abertura real do anúncio."""
        self.active = True
        self.clicked = True
        self.cycle_id += 1
        self.stage_index = 1
        self.start_ts = time.time()
        self.stages = [{"stage": 1, "activity": activity, "format": ad_format, "close_layer": None}]
        self.log("%s cycle_id=%d device=%s" % (CYCLE_START, self.cycle_id, self.device))
        self.log("%s cycle_id=%d device=%s" % (SESSION_START, self.cycle_id, self.device))
        self._log_stage(activity, ad_format)

    def _log_stage(self, activity, ad_format):
        self.log("%s cycle_id=%d stage_index=%d state=open format=%s activity=%s" % (
            SESSION_STAGE, self.cycle_id, self.stage_index, ad_format, activity))

    def continue_stage(self, activity="", ad_format=None, reason="nao_main", close_layer=None):
        """Fechou um controle mas NÃO voltou à MAIN: continua a MESMA sessão, novo stage."""
        if not self.active:
            return
        from_stage = self.stage_index
        self.stages[-1]["close_layer"] = close_layer
        self.log("%s cycle_id=%d stage_index=%d close_layer=%s" % (
            SESSION_STAGE_CLOSED, self.cycle_id, from_stage, close_layer))
        self.stage_index += 1
        self.stages.append({"stage": self.stage_index, "activity": activity, "format": ad_format,
                            "close_layer": None})
        self.log("%s cycle_id=%d from_stage=%d to_stage=%d reason=%s" % (
            SESSION_CONTINUE, self.cycle_id, from_stage, self.stage_index, reason))
        self._log_stage(activity, ad_format)

    def complete(self, close_layer=None, cycles_until_reset=None):
        """MAIN confirmada ao final (sem escape): o ciclo é COMPLETED."""
        if not self.active:
            return None
        self.stages[-1]["close_layer"] = close_layer
        self.log("%s cycle_id=%d stage_index=%d close_layer=%s" % (
            SESSION_STAGE_CLOSED, self.cycle_id, self.stage_index, close_layer))
        dur = (time.time() - self.start_ts) if self.start_ts else 0.0
        n = self.stage_index
        cid = self.cycle_id
        self.active = False
        extra = (" cycles_until_reset=%s" % cycles_until_reset) if cycles_until_reset is not None else ""
        self.log("%s cycle_id=%d stage_count=%d duration_s=%.1f%s" % (
            CYCLE_COMPLETED, cid, n, dur, extra))
        return {"cycle_id": cid, "stage_count": n, "duration_s": dur, "result": COMPLETED}

    def fail(self, reason="", cycles_until_reset=None):
        """Resultado TERMINAL de falha da sessão (timeout/LEFT_AD/recovery exhausted/escape).
        functional=FAILED — a contagem é decidida por qa.record_cycle_result()."""
        if not self.active:
            return None
        n = self.stage_index
        cid = self.cycle_id
        self.active = False
        extra = (" cycles_until_reset=%s" % cycles_until_reset) if cycles_until_reset is not None else ""
        self.log("%s cycle_id=%d stage_count=%d reason=%s%s" % (
            CYCLE_FAILED, cid, n, reason, extra))
        return {"cycle_id": cid, "stage_count": n, "reason": reason, "result": FAILED}

    def abort(self, reason=""):
        """Sessão interrompida SEM resultado terminal (test end/watchdog/crash): functional=ABORTED.
        NÃO conta ciclo."""
        if not self.active:
            return None
        n = self.stage_index
        cid = self.cycle_id
        self.active = False
        self.log("%s cycle_id=%d stage_count=%d reason=%s" % (CYCLE_ABORTED, cid, n, reason))
        return {"cycle_id": cid, "stage_count": n, "reason": reason, "result": ABORTED}


def classify_outcome(app_on_screen, on_main):
    """Classifica o estado após processar um trecho da sessão.
    COMPLETED: MAIN confirmada. CONTINUE: app em tela (outro stage). FAILED: fora (escape/stop)."""
    if on_main:
        return COMPLETED
    if app_on_screen:
        return "CONTINUE"
    return FAILED
