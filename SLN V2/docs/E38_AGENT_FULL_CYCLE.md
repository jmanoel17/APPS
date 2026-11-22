# E3.8 - Agent Full Cycle (device real)

Data: 2026-11-20
Device: `192.168.1.124:5555` (SM-G975F, Android 12/sdk31)
Perfil: `config_e38_agent_single.json` (`device_backend=agent`, `execution_mode=live`)
Limites: 1 device, `SLN_TEST_MAX_CYCLES=3`, `SLN_TEST_MAX_RUNTIME_S=900`.

Nenhuma lógica de reward gate, Fast Close, cycle accounting ou reset foi alterada.

## 1. Preflight

PASS antes do primeiro ciclo:

- Agent `01bedbfd-cbd7-4130-9eee-55c0f46e91aa` ONLINE.
- Accessibility habilitada; tela ligada; keyguard livre.
- Shizuku `READY`, `effective_uid=2000`.
- Capabilities obrigatorias 10/10: `GET_UI_TREE`, `CAPTURE_SCREEN`, `GET_FOREGROUND_APP`,
  `TAP`, `SWIPE`, `BACK`, `HOME`, `START_APP`, `STOP_APP`, `CLEAR_APP_DATA`.
- Leitura real `GET_UI_TREE`, `CAPTURE_SCREEN` e `GET_FOREGROUND_APP` OK.

O bootstrap usa ADB apenas para reverse/accessibility/start do Agent. O `uiautomator2` **nao** e
conectado quando o backend e Agent: neste ROM ele desabilita o AccessibilityService.

## 2. Auditoria dos gaps legados

| Callsite | Operacao | Motivo | Hot path? | Esperado no E3.8? | Fallback permitido? |
|---|---|---|---|---|---|
| `main_sln_v2.py:2738` | `adb shell date/pidof/logcat` | FORMAT por evento do app | sim | sim, leitura | nao; apenas leitura |
| `main_sln_v2.py:3137` | `adb shell pidof/date/logcat` | eventos CLICKED/REWARD/DISMISSED | pos-fechamento | sim, leitura | nao; apenas leitura |
| `d.shell("dumpsys ...")` | ADB read | so quando `BACKEND is None` | nao | nao | nao |
| `d.shell("am start ADS_PRIVACY")` | ADB mutation | reset de ID | nao | nao (reset=0) | nao |
| `d.shell("pm clear ...")` | ADB mutation | caminho ADB legado | nao | **nao**; proibido apos Agent | nao |
| `d.swipe_ext` | ADB input | recuperacao de banner | eventual | nao acionado | sim, gap explicito |
| `d.press("power")` | ADB keyevent | tela apagada | nao | nao | sim, gap explicito |
| selectors/xpath `d(...)` | Agent tree via facade | caminho legado de seletores | home/endcard | sim | sem ADB |
| `d.window_size/screenshot/app_current/dump` | AgentBackend | leitura normal | sim | sim | sem ADB |
| `d.click/start/stop/back/home` | AgentBackend | mutacao normal | sim | sim | sem ADB |
| periodic force-stop | `backend.stop_app()` | memoria periodica | nao | nao acionado | duplicata removida |

O `d.shell("am force-stop")` periodico duplicado foi removido; agora existe apenas
`_backend_stop_app()`, sem executar dois force-stops.

## 3. Teste A - 1 ciclo

Resultado: **COMPLETED**.

- `cycle_id=1`, 1 stage, duracao 68,7 s.
- Fast Close `closed=True` via `reveal_top`/structural em 64,6 s.
- 5 mutacoes Agent: `START_APP`, `TAP` no reward, `BACK` de Store, `TAP` no endcard e `TAP` no
  reveal superior.
- 0 erros Agent, 0 uncertain, 0 reconnect, 0 duplicata.
- Accounting: 1 started, 1 accounted, 1 completed, 0 failed/aborted.

## 4. Teste B - ate 3 ciclos

### B0 (IA server fechado)

3 started / 3 accounted: **1 COMPLETED, 2 FAILED**. As falhas foram timeout de criativo
(90 s) com recuperacao esgotada. IA ausente apareceu como `classification_failed` e
`RECOVERY_CLOSE][AI] no_detection`.

### B1 (IA server aberto)

IA server iniciado; `/health` OK, modelo carregado. Reexecucao solicitada para separar backend
de disponibilidade da IA.

3 started / 3 accounted: **1 COMPLETED, 2 FAILED**.

| Ciclo | Resultado | Evidencia |
|---|---|---|
| 1 | FAILED | timeout ~97 s; IA sem deteccao; recovery esgotada |
| 2 | COMPLETED | timeout 90,2 s; recovery `anonymous_top_right` clicou e voltou a MAIN |
| 3 | FAILED | timeout ~99 s; IA detectou `x=82,y=174` conf=0,59, mas rejeitado; recovery esgotada |

As falhas sao **funcionais/criativas**, nao falha do AgentBackend: nao houve erro de leitura,
`UNCERTAIN`, reconnect storm, duplicata ou fallback ADB de mutacao. O reward gate permaneceu
intacto (sem SKIP por tempo).

## 5. Ciclo/accounting

- Teste A: `accounted == started` (1/1), 1 terminal.
- Teste B1: `accounted == started` (3/3), 1 completed + 2 failed, 0 aborted.
- QA cumulativo: 7 started / 7 accounted = 3 completed + 4 failed; sem double-count.
- Reset/clear: 0 execucoes nesta fase (perfil; E3.5B ja validou CLEAR real).
- `cycles_until_reset` 93; nenhum reset pendente.

## 6. AgentBackend - metricas do B1

1002 comandos: **989 reads + 13 mutations**.

| Operacao | N | avg ms | p95 ms |
|---|---:|---:|---:|
| GET_UI_TREE | 921 | 69,7 | 97,0 |
| GET_FOREGROUND_APP | 50 | 42,8 | 53,6 |
| CAPTURE_SCREEN | 12 | 936,2 | 2145,7 |
| GET_STATUS | 6 | 46,8 | 49,7 |
| START_APP | 3 | 52,8 | 56,7 |
| TAP | 6 | 103,4 | 122,8 |
| STOP_APP | 2 | 428,6 | 465,4 |
| HOME | 2 | 39,7 | 43,1 |

Mutacoes: todas `success`; 0 `UNCERTAIN`, 0 erros, 0 `command_id` duplicado, 0 reconnect.

## 7. Legacy telemetry

`e38_legacy_fallbacks.json`:

- `legacy_reads=10`, `legacy_mutations=0`, `adb_subprocesses=30`.
- 9 bundles de FORMAT (3 subprocessos cada) e 1 bundle de eventos (3 subprocessos).
- Todos sao **leituras de logcat**; nenhum fallback de TAP/SWIPE/HOME/START/STOP/CLEAR.
- `format_source=none` em todos os anuncios B1; o logcat nao retornou evento recente.
- Nao e correto declarar "ADB free" nesta fase.

## 8. IA

Servidor iniciado apos o B0 e mantido durante B1:

- `/health`: `model_loaded=true`, warmup OK.
- Duas consultas de recuperacao: 1 sem deteccao; 1 com deteccao rejeitada (`x=82,y=174`,
  conf=0,59). A IA nao foi autoridade de reward gate.

## 9. Invariantes

| Invariante | Resultado |
|---|---|
| duplicate physical mutation | 0 |
| silent ADB mutation fallback | 0 |
| OBSERVE -> CLICK divergence observada | 0 |
| cycle double-count | 0 |
| reset ID duplicado | 0 (reset nao executado) |
| orphan pending command | 0 |
| Agent reconnect storm | 0 |
| stop condition triggered | nao |

## 10. Veredito

**E3.8_AGENT_BACKEND_PARTIAL**

- `BACKEND_RESULT=PASS`: backend Agent executou leituras/mutacoes reais, sem erro/uncertain/
  duplicata/reconnect.
- `FUNCTIONAL_RESULT=PARTIAL`: Test A COMPLETED; B1 1/3 COMPLETED, falhas de criativo/timeout
  com recuperacao esgotada.
- O ciclo ainda depende de **leituras ADB de logcat** (FORMAT/eventos). Nao ha dependencia de
  mutacao ADB nem de uiautomator2 durante o ciclo.

## 11. Limitacoes e proximo passo

- Criativos sem X/countdown/pill continuam podendo esgotar a recuperacao; IA pode nao detectar
  ou ter deteccao rejeitada. Nao e falha de backend.
- `GET_EVENTS` continua nao implementado; FORMAT/eventos usam logcat ADB.
- O perfil E3.8 desativou reset/clear/install para isolar o ciclo; esses caminhos ja foram
  validados em E3.5B.
- Proximo passo recomendado: repetir B1 com criativos que tenham controle visivel e avaliar
  portar `FORMAT/events` para o Agent antes de remover o gap de logcat.

## Artefatos

- `state/device_agents/e38_report.json`
- `state/device_agents/e38_commands.json`
- `state/device_agents/e38_legacy_fallbacks.json`
- `state/device_agents/e38_test_a.log`
- `state/device_agents/e38_test_b_ai.log`
