# E3.6 - Decision Parity / Shadow Bot (read-only replay)

Data: 2026-11-15
Device fixtures: `.124` (SM-G975F, Android 12/sdk31)
Authority: `main_sln_v2.py` pure fast-close detectors (no bot logic altered)
Agent: somente GET_UI_TREE / CAPTURE_SCREEN / GET_FOREGROUND_APP - **nenhuma acao executada**

## 1. Resultado executivo

O replay normalizado ADB vs Agent tem **14/14 casos decisao igual**, 0 divergencias
criticas e 0 acoes do Agent. Porem **E3.6 = PARITY_PARTIAL**, nao PASS: faltam fixtures
reais dos detectores criticos de **close estrutural/anonimo**, **SUB_AD_CLOSE**,
**countdown**, **consent** e **endcard**. Sem esses fixtures nao e possivel provar os
blockers de close authorization/SUB_AD_CLOSE exigidos pela fase.

Nao houve nenhum caso ADB=OBSERVE / Agent=CLICK e nenhum candidato semanticamente
diferente. Nada foi integrado ao main; nenhum TAP/SWIPE/BACK/HOME foi executado pelo
Agent.

## 2. Implementacao

Ferramenta: `decision_parity_replay.py`

- Importa `main_sln_v2` apenas para reutilizar funcoes puras
  (`_fc_analisar_dump`, `_fc_detectar_endcard`, `_fc_find_endcard_close_structural`,
  `_fc_find_close_btn`, `_fc_listas_antigas`, `_fc_recovery_anon_top_right`,
  `_fc_detectar_subad_close`, `_fc_detectar_countdown`). Nenhuma logica de decisao
  duplicada no APK e nenhuma alteracao no main.
- `ADBTreeAdapter`: XML uiautomator -> `NormalizedTree` + ElementTree normalizado.
- `AgentTreeAdapter`: JSON GET_UI_TREE -> mesma `NormalizedTree`/ElementTree, preservando
  hierarquia por `parent_id`, bounds, class/text/desc/rid/clickable/visible.
- `DecisionEngine` host-only: recebe `NormalizedTree + foreground + metadata` e devolve
  `DecisionResult(state, candidate, bounds, authorized, decision, reason, endcard,
  subad, countdown)`; a mesma funcao e chamada para ADB e Agent.
- Decisoes possiveis: `OBSERVE`, `CLICK`, `BACK`, `HOME`, `CONTINUE`, `COMPLETE`, `FAIL`,
  `NO_ACTION`. O Agent so calcula; nunca executa.
- Replay deterministico: cada caso gera decisao ADB, decisao Agent, match, IoU/distancia
  de centro, screenshot diff e metricas de parse/decisao.

## 3. Normalizacao

`NormalizedNode`: class, text, content_desc, resource_id, bounds, clickable, enabled,
visible, scrollable, depth, parent_id. `NormalizedTree` inclui root ElementTree e display.

Foreground comparado em `package`, `activity`, `component`; os snapshots usam
`foreground_adb` (dumpsys) e `foreground_agent` (GET_FOREGROUND_APP).

## 4. Fixtures reais

`tests/fixtures/e36/` (capturados no device, ADB XML + Agent JSON + PNGs + meta):

| Fixture | Casos | Evidencia real |
|---|---|---|
| `main_reward` | 1 | MainActivity, botao reward |
| `ad_applovin` | 2 | AppLovin activity/adFrame |
| `live_ad_0..9` | 10 | AppLovin playable/game WebView, 35 nodes, adFrame/game/game-controls |
| `store` | 1 | Play Store `com.android.vending`, transparent activity |

Observacao: o criativo playable nao expunha X/close; ele foi registrado como playable,
nao rotulado como interstitial/rewarded sem prova.

## 5. Matriz por caso

| Caso | ADB state/decisao | Agent state/decisao | Match |
|---|---|---|---|
| main_reward | main / CLICK (reward) | main / CLICK (reward) | sim |
| ad_applovin, live_ad_0..9 | applovin / OBSERVE | applovin / OBSERVE | sim (12/12) |
| store | store / BACK | store / BACK | sim (decisao/package/state) |

Metricas globais:

| Metrica | Valor |
|---|---|
| foreground package | 1.000 |
| foreground activity | 0.929 (13/14) |
| foreground component | 0.929 |
| state | 1.000 |
| candidate | 1.000 |
| authorization | 1.000 |
| decision | 1.000 |
| critical mismatches | 0 |
| ADB=OBSERVE / Agent=CLICK | 0 |
| agent actions executed | 0 |

A unica divergencia de activity foi no store: Agent reportou
`android.view.ViewGroup` via evento, ADB `...TransparentMainActivityPrivate`.
Package/state/decision coincidiram (BACK); registrado como limitacao de activity
generica no evento, sem inventar valor.

## 6. Detector map

| Detector | Fixtures | Status |
|---|---|---|
| MAIN_DETECTION | 1 | PARITY_PASS |
| REWARD_BUTTON | 1 | PARITY_PASS |
| APPLOVIN_STATE | 12 | PARITY_PASS |
| STORE_BACK | 1 | PARITY_PASS |
| SESSION (main) | 1 | PARITY_PASS |
| CLOSE_CANDIDATE (structural/anonymous) | 0 | FIXTURE_MISSING |
| SUB_AD_CLOSE | 0 | FIXTURE_MISSING |
| COUNTDOWN | 0 | FIXTURE_MISSING |
| CONSENT | 0 | FIXTURE_MISSING |
| ENDCARD | 0 | FIXTURE_MISSING |

## 7. Bounds

Criterio implementado: mesmo candidato semantico E
`center_delta <= 20 px` OU `IoU >= 0.75` (threshold inicial do protocolo, nao ajustado
para mascarar erro). Nao houve candidato close no fixture; o reward main foi comparado
com bounds identicos (teste offline) e o parser usa exatamente os mesmos bounds nos dois
adapters. Para close real, a matriz fica COVERAGE_PENDING.

## 8. Screenshots

14/14 pares com dimensoes iguais (1080x2280); diff medio **1.43** (range 0-19.36;
19.36 no frame de transicao do ad); blue-bar OpenCV classification igual em 14/14.
IA `/detect`/`/classify` estava offline; nao foi usada. Nenhuma imagem foi enviada ao
Agent ou usada para decisao.

## 9. Latencia do pipeline (replay)

| Etapa | avg |
|---|---|
| ADB parse_ms | 4,81 |
| Agent parse_ms | 2,31 |
| ADB decision_ms | 2,98 |
| Agent decision_ms | 2,53 |
| Agent tree capture_ms (fixtures) | 29,33 |

O custo de decisao e baixo; o custo dominante continua sendo a captura (E3.5: tree ~49 ms,
screenshot ~353 ms). Este replay nao mede captura ao vivo nem altera o tick do bot.

## 10. Seguranca / regras da fase

- Nenhum clique/TAP/SWIPE/BACK/HOME/STOP/CLEAR pelo Agent.
- Nenhuma alteracao no `main_sln_v2`, Fast Close, reward gate, cycle accounting ou logcat.
- Nenhum novo detector foi inventado para elevar score; fixtures ausentes ficam SKIP.
- Nenhuma integracao; nenhum resultado de replay e aplicado ao bot.

## 11. Limitacoes / cobertura pendente

1. uiautomator2 derruba AccessibilityService neste ROM; captura ADB/Agent e sequencial,
   nao simultanea (documentado desde E3.5).
2. Ausentes fixtures reais de close estrutural/anonimo, SUB_AD_CLOSE, countdown, consent,
   endcard e de um ad explicitamente rotulado interstitial/rewarded.
3. O fixture playable tem CTA Google Play, nao X; nao foi rotulado como rewarded.
4. Activity generica do Agent no store (`android.view.ViewGroup`); package/decision OK.
5. O replay valida a logica sobre snapshots, nao o tick ao vivo do bot.

## 12. Veredito

**E3.6 DECISION PARITY = PARITY_PARTIAL / COVERAGE_PENDING.**

- Bloqueadores provados nos fixtures disponiveis: MAIN/reward, AppLovin state, store/back,
  session e decision match = PASS.
- Bloqueadores **nao provados** por falta de fixture: close authorization estrutural/
  anonimo e SUB_AD_CLOSE; consequentemente nao se declara PASS.
- Nao foi iniciada integracao nem qualquer acao do Agent. **PARAR para analise/coleta
  de fixtures** conforme instrucao.

Artefatos: `state/device_agents/e36_parity.json`, `decision_parity_replay.py`,
`tests/fixtures/e36/`, testes `tests/test_e36_decision_parity.py`.
