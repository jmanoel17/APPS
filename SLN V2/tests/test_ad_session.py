#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_ad_session.py — SLN V2 (modelagem de CICLO / AD SESSION, OFFLINE)

Prova: 1 clique = 1 ciclo = 1 sessão com N stages; fechar controle NÃO finaliza;
só COMPLETED em MAIN; escape forçado = FAILED; telemetria correta.

Rodar:  python tests/test_ad_session.py
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import ad_session as AD

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  ", name)
    else:
        FAIL += 1
        print("  FAIL", name)


class Cap:
    def __init__(self):
        self.lines = []

    def __call__(self, msg, *a, **k):
        self.lines.append(str(msg))

    def has(self, token):
        return any(token in l for l in self.lines)

    def find(self, token):
        return [l for l in self.lines if token in l]


def test_single_stage_complete():
    print("[ad_session] 1 stage -> MAIN = 1 ciclo COMPLETED")
    cap = Cap()
    s = AD.CycleSession("D1", cap)
    s.begin(activity="applovin", ad_format="interstitial")
    check("cycle_id=1", s.cycle_id == 1 and s.active and s.stage_index == 1)
    check("telemetria CYCLE_START", cap.has("[QA][CYCLE_START]"))
    check("telemetria AD_SESSION START", cap.has("[AD_SESSION][START]"))
    st = s.complete(close_layer="structural", cycles_until_reset=4)
    check("COMPLETED active=False", (not s.active) and st["result"] == AD.COMPLETED)
    check("stage_count=1", st["stage_count"] == 1)
    check("telemetria CYCLE_COMPLETED + cycles_until_reset", cap.has("[QA][CYCLE_COMPLETED]") and cap.has("cycles_until_reset=4"))


def test_multi_stage_one_cycle():
    print("[ad_session] N stages na MESMA sessao = 1 ciclo")
    cap = Cap()
    s = AD.CycleSession("D2", cap)
    s.begin(activity="video", ad_format="rewarded")
    s.continue_stage(activity="store", ad_format="rewarded", reason="store")
    s.continue_stage(activity="endcard", ad_format="rewarded", reason="endcard")
    check("stage_index=3", s.stage_index == 3)
    check("cycle_id continua 1 (nao cria novo ciclo)", s.cycle_id == 1)
    check("telemetria CONTINUE x2", len(cap.find("[AD_SESSION][CONTINUE]")) == 2)
    check("telemetria STAGE_CLOSED x2", len(cap.find("[AD_SESSION][STAGE_CLOSED]")) == 2)
    st = s.complete(close_layer="endcard_x")
    check("1 CYCLE_COMPLETED com stage_count=3", len(cap.find("[QA][CYCLE_COMPLETED]")) == 1 and st["stage_count"] == 3)
    check("nao houve CYCLE_START extra", len(cap.find("[QA][CYCLE_START]")) == 1)


def test_fail_not_counted():
    print("[ad_session] escape forcado -> CYCLE_FAILED (nao conta ciclo)")
    cap = Cap()
    s = AD.CycleSession("D3", cap)
    s.begin(activity="rewarded", ad_format="rewarded")
    s.continue_stage(activity="rewarded", ad_format="rewarded", reason="nao_main")
    st = s.fail(reason="escape_or_force_stop")
    check("FAILED", st["result"] == AD.FAILED and not s.active)
    check("CYCLE_FAILED emitido", cap.has("[QA][CYCLE_FAILED]"))
    check("NAO houve CYCLE_COMPLETED", not cap.has("[QA][CYCLE_COMPLETED]"))
    check("stage_count=2 no fail", st["stage_count"] == 2)


def test_classify_outcome():
    print("[ad_session] classify_outcome")
    check("MAIN -> COMPLETED", AD.classify_outcome(True, True) == AD.COMPLETED)
    check("app foreground nao-main -> CONTINUE", AD.classify_outcome(True, False) == "CONTINUE")
    check("fora do app -> FAILED", AD.classify_outcome(False, False) == AD.FAILED)


def test_stage_after_complete_ignored():
    print("[ad_session] continue/fail sem sessao ativa sao no-op")
    cap = Cap()
    s = AD.CycleSession("D4", cap)
    s.begin()
    s.complete()
    before = len(cap.lines)
    s.continue_stage(); s.fail("x"); s.complete()
    check("sem efeito apos completed", len(cap.lines) == before)


def test_cycle_ids_increment_between_sessions():
    print("[ad_session] sessoes distintas -> cycle_id incrementa")
    cap = Cap()
    s = AD.CycleSession("D5", cap)
    s.begin(); s.complete()
    s.begin(); s.complete()
    check("cycle_id=2 na 2a sessao", s.cycle_id == 2)
    check("2 CYCLE_START / 2 CYCLE_COMPLETED",
          len(cap.find("[QA][CYCLE_START]")) == 2 and len(cap.find("[QA][CYCLE_COMPLETED]")) == 2)


def test_abort():
    print("[ad_session] abort (sem resultado terminal) -> CYCLE_ABORTED")
    cap = Cap()
    s = AD.CycleSession("D6", cap)
    s.begin()
    st = s.abort(reason="test_terminated_while_ad_active")
    check("ABORTED e active=False", st["result"] == AD.ABORTED and not s.active)
    check("CYCLE_ABORTED emitido", cap.has("[QA][CYCLE_ABORTED]"))
    check("NÃO emite CYCLE_COMPLETED/FAILED",
          not cap.has("[QA][CYCLE_COMPLETED]") and not cap.has("[QA][CYCLE_FAILED]"))


def main():
    test_single_stage_complete()
    test_multi_stage_one_cycle()
    test_fail_not_counted()
    test_classify_outcome()
    test_stage_after_complete_ignored()
    test_cycle_ids_increment_between_sessions()
    test_abort()
    print("\nRESULT: PASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
