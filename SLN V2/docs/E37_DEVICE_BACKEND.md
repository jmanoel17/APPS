# E3.7 - Device Backend Abstraction

Status: **PASS_DEFAULT_ADB_CONTRACT** (2026-11-15). Default continua ADB; Agent e opt-in e
nao foi integrado no ciclo completo.

## Contrato

`device_backend` expoe `DeviceBackend` com:

```text
get_status, get_ui_tree, capture_screen, get_foreground_app
tap, swipe, back, home, start_app, stop_app, clear_app_data
```

Modelos tipados em `device_backend/models.py`: `DeviceStatus`, `ForegroundState`, `UiTree`,
`UiNode`, `Screenshot`, `MutationResult` (`OK/ERROR/BLOCKED/UNCERTAIN`, `error_code`,
`attempts`). `DeviceBackend` tambem oferece test hooks estaticos de XML ADB -> `UiTree` e de
JSON Agent -> `UiTree` usados nos contratos.

## Backends

- `ADBBackend`: encapsula o uiautomator2/ADB atual. `get_ui_tree` usa `dump_hierarchy`; screenshot
  usa uiautomator2 e cai para `screencap` sem expor shell generico. `stop_app`/`clear_app_data`
  mantem force-stop/pm clear do reset.
- `AgentBackend`: le o Agent por um `command_fn` injetado; nunca importa ou chama ADB. Em
  `execution_mode=shadow` toda mutacao retorna `ShadowMutationBlocked`; sem fallback. Erros
  `UNCERTAIN` do Agent sao propagados, nao reexecutados.
- `make_backend("adb")` e `make_backend("agent", command_fn=...)`; Agent sem controller levanta
  `BackendError` (falha explicita).

## Configuracao

```text
DEFAULTS: device_backend="adb", execution_mode="normal"
Env:      SLN_DEVICE_BACKEND=adb|agent, SLN_EXECUTION_MODE=normal|shadow
```

Agent real requer `SLN_DEVICE_BACKEND=agent` e `SLN_EXECUTION_MODE=shadow`; nao ha fallback
automatico para ADB.

## Integracao no main

- Leituras centrais: dump medido, foreground/current app, screenshot, status/doze.
- Mutações roteadas: tap (28 chamadas), back/home (11), start app (8), stop app (12), swipe de
  movimento, clear/stop Agent. Em ADB o comportamento e o mesmo de antes; em Agent shadow as
  mutacoes sao bloqueadas antes de qualquer efeito.
- `clear` com resultado `UNCERTAIN` marca `_clear_uncertain`; `_pending_retry_step` pula a
  reexecucao automatica enquanto a incerteza existir. Reset idempotente preservado.
- Fast Close, reward gate, cycle accounting e reset semantics nao foram alterados.

## Evidencia

- Contrato: 7/7 testes (`tests/test_device_backend.py`), incluindo equivalencia E3.6.
- Regressao: 86/86 testes (68 Agent + 8 E3.6 + 3 E3.6R + 7 backend).
- ADB smoke real read-only: 74 nodes, foreground launcher, screenshot 1080x2280, 0 mutacoes.
- Agent smoke real shadow: 5 nodes, foreground Agent, screenshot 1080x2280; tap/swipe/back/
  home/start/stop/clear todos `BLOCKED`; 0 acoes executadas.
- `compileall`/`py_compile` OK para main, config, harnesses, Agent e backends.

## Limitacoes

- O main ainda usa uiautomator2 para seletores/xpath, `swipe_ext`, `d.press("power")` e
  force-stop periodico; nao ha backend para logcat/eventos.
- AgentBackend exige injecao do controller no main; nenhum ciclo completo do bot via Agent foi
  executado nesta fase.
- E3.6R continua com cobertura pendente de consent/countdown/endcard/structural-anonymous e
  rotulos de criativo.

## Artefatos

`state/device_agents/e37_report.json`, `e37_adb_smoke.json`,
`state/device_agents/e32b/e37_agent_smoke.json`.
