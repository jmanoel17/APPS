#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn — Camada de ORQUESTRAÇÃO de DPN/IP do SLN V2.

Camada INDEPENDENTE acima dos workers (não toca Fast Close / reward gate / SUB_AD_CLOSE /
CycleSession / CYCLE_ACCOUNTING_V2). Default OFF (`dpn_routing_enabled=false`).

Módulos:
  locks.py      InterProcessFileLock (exclusão real multi-processo; msvcrt no Windows)
  state.py      persistência atômica (state/dpn/): estado por DPN + histórico GLOBAL de IP
  provider.py   DPNProvider (ABC) + FakeDPNProvider (testes offline determinísticos)
  coordinator.py  (Etapa B) state machine por DPN
  worker_hook.py  (Etapa C) cliente fino usado pelo main_sln_v2
"""

__all__ = ["locks", "state", "provider"]
