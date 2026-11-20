# E3.6R - Real Device Decision Parity (read-only shadow)

Data: 2026-11-15
Device: `.124` (SM-G975F, Android 12/sdk31), Agent `com.sln.deviceagent`
Preflight: Agent ONLINE, Accessibility true, GET_UI_TREE/CAPTURE_SCREEN true, Shizuku READY,
UID 2000, display 1080x2280.

## 1. Resultado

**E3.6 REAL PARITY COVERAGE = PASS para os detectores críticos observados**, com cobertura
pendente de cenários sem fixture real.

Veredito do replay: **PARITY_PASS_WITH_COVERAGE_PENDING** - não é PASS de cobertura completa.
Nenhum caso `ADB=OBSERVE / Agent=CLICK`, nenhum candidato semanticamente diferente e
`shadow_actions_executed = 0`.

## 2. Segurança da captura

- Agent somente `GET_UI_TREE`, `CAPTURE_SCREEN`, `GET_FOREGROUND_APP`, `GET_STATUS`,
  `GET_DEVICE_INFO`; nenhuma decisão shadow foi executada.
- Ações para alcançar estados: **ADB QA explícito** (`am`, `input tap`, `uiautomator dump`,
  `screencap`), nunca TAP/BACK/HOME do Agent.
- O QA driver corrigido só toca o reward quando `clickable=true`, `enabled != false` e
  `visible-to-user != false` (o botão “Carregando anúncio...” não é tateado enquanto bloqueado).
- UA/ADB dump bruto não usa uiautomator2, preservando o AccessibilityService.
- `shadow_actions_executed = 0`; `qa_actions = 5` (explícitas, não shadow).

## 3. Captura real

Rodada de retomada: **300 s**; a primeira janela foi interrompida antes do relatório, mas as
fixtures foram preservadas.

| Metrica | Valor |
|---|---|
| snapshots_total | 552 |
| trees_success | 522 |
| trees_failure | 30 (transições/force-stop QA; não erros de decisão) |
| screenshots_total | 15 |
| shadow_decisions | 522 |
| shadow_actions_executed | **0** |
| QA actions | 5 |
| tree latency p50/p95/p99 | 50,26 / 76,85 / 140,08 ms |
| decision latency avg/p95/p99 | 0,68 / 1,25 / 2,75 ms |
| payload p50/p95 | 9.143 / 9.144 bytes |
| Agent CPU/PSS | 0,0% / 63.336 KB |
| foreground transitions | registradas (MAIN→ad→launcher→MAIN etc.) |

A latência de decisão é desprezível; o custo dominante permanece captura de árvore (~50 ms p50)
e screenshot (E3.5 ~353 ms).

## 4. Fixtures reais criadas

`tests/fixtures/e36_real/` (ADB XML/PNG + Agent JSON/PNG + foreground + decision + metadata):

| Fixture | Estado real |
|---|---|
| `reward` | MainActivity com reward (botão em carregamento observado) |
| `return_main` | retorno à MainActivity após transição |
| `playable` | AppLovin playable/game (35 nodes) |
| `sub_ad_close` | **SUB_AD_CLOSE real: bounds [958,217,1028,287], centro (993,252)** |
| `store` | Play Store (`com.android.vending`) |
| `applovin`/`webview` | removidos quando a validação cruzada detectou transição ADB/Agent inconsistente |

Cada fixture contém `agent_tree.json`, `agent_screen.png`, `foreground.json`,
`decision.json`, `metadata.json`; aliases `agent.json`/`meta.json` permitem replay pelo
harness E3.6. Nenhuma fixture antiga foi sobrescrita.

## 5. Evidência crítica: SUB_AD_CLOSE

O snapshot real `sub_ad_close` foi processado pela **mesma** função pura `subad_close.detect_subad_close`:

| Lado | state | candidate | bounds | authorized | decision |
|---|---|---|---|---|---|
| ADB | applovin | subad_close | [958,217,1028,287] | true | CLICK |
| Agent | applovin | subad_close | [958,217,1028,287] | true | CLICK |

Nenhum clique foi executado pelo Agent. Isso prova a assinatura WebView/close anônimo no
caminho Agent com bounds idênticos ao ADB.

## 6. Replay atualizado

`state/device_agents/e36_parity.json` contém **19 casos** (fixtures E3.6 + E3.6R):

| Metrica | Valor |
|---|---|
| foreground package | 1.000 |
| foreground activity | 0.947 (store: ViewGroup vs TransparentActivity; package/state/decision iguais) |
| foreground component | 0.947 |
| state | 1.000 |
| candidate | 1.000 |
| authorization | 1.000 |
| decision | 1.000 |
| critical mismatches | **0** |
| ADB=OBSERVE / Agent=CLICK | **0** |
| agent actions | **0** |
| screenshots | 19/19 dims; diff médio 5,90 (0–92,12 em transição) |

## 7. Detector map

| Detector | Offline | Real | Fixtures | Decision matches | Critical | Status |
|---|---|---|---|---|---|---|
| MAIN_DETECTION | PASS | PASS | 2 | 2 | 0 | PARITY_PASS |
| REWARD_BUTTON | PASS | PASS | 2 | 2 | 0 | PARITY_PASS |
| APPLOVIN_STATE | PASS | PASS | 14 | 14 | 0 | PARITY_PASS |
| CLOSE_CANDIDATE | FIXTURE_MISSING | PASS (subad) | 1 | 1 | 0 | PARITY_PASS |
| SUB_AD_CLOSE | FIXTURE_MISSING | PASS | 1 | 1 | 0 | PARITY_PASS |
| STORE_BACK | PASS | PASS | 2 | 2 | 0 | PARITY_PASS |
| SESSION | PASS | PASS | 3 | 3 | 0 | PARITY_PASS |
| CONSENT | FIXTURE_MISSING | FIXTURE_MISSING | 0 | 0 | 0 | FIXTURE_MISSING |
| COUNTDOWN | FIXTURE_MISSING | FIXTURE_MISSING | 0 | 0 | 0 | FIXTURE_MISSING |
| ENDCARD | FIXTURE_MISSING | FIXTURE_MISSING | 0 | 0 | 0 | FIXTURE_MISSING |

Cobertura pendente adicional: close estrutural/anônimo genérico (diferente do SUB_AD_CLOSE)
e um criativo explicitamente rotulado INTERSTITIAL/REWARDED. O criativo real observado foi
playable/game; não foi rotulado como rewarded sem evidência.

## 8. Divergências

- Store: Agent reportou `android.view.ViewGroup`; ADB `TransparentMainActivityPrivate`.
  Classificado como **BENIGN_ACTIVITY_DIFFERENCE** apenas porque package, state e decision
  (BACK) coincidem; a diferença fica registrada, não foi escondida.
- Fixtures `applovin`/`webview` capturadas durante force-stop/QA foram **rejeitadas** pela
  validação de pair ADB/Agent (não entram no replay).
- 30 tree failures são transições/reboot de app durante QA; nenhuma decisão shadow foi
  executada ou aplicada.

## 9. Veredito e limites

- **Detectores críticos observados: PASS** (MAIN, REWARD, CLOSE via SUB_AD_CLOSE,
  SUB_AD_CLOSE, STORE_BACK, SESSION) com 0 divergência perigosa e 0 ação do Agent.
- **Cobertura pendente**: CONSENT, COUNTDOWN, ENDCARD, close estrutural/anônimo genérico e
  rótulo real INTERSTITIAL/REWARDED. Estes são `FIXTURE_NOT_OBSERVED`, não FAIL.
- Portanto: **E3.6 REAL PARITY COVERAGE = PASS (críticos) / PARITY_PASS_WITH_COVERAGE_PENDING
  (global)**. Não se declara cobertura completa nem se inicia integração.

Artefatos: `docs/E36R_REAL_PARITY.md`, `state/device_agents/e36r_report.json`,
`state/device_agents/e36_parity.json`, `tests/fixtures/e36_real/`,
`e36_real_capture.py`, `tests/test_e36r_real_fixtures.py`.
