# E3.5B - Shizuku Privileged Bridge MVP

Data: 2026-11-15
Device: `.124` (SM-G975F, Android 12/sdk31), agent `com.sln.deviceagent`
Shizuku: `moe.shizuku.privileged.api` 13.6.0.r1086.2650830c, API 13, servidor iniciado via ADB
Escopo: bridge privilegiado read/write tipado (STOP_APP/CLEAR_APP_DATA), sem EXEC_SHELL,
sem integracao no `main_sln_v2`, sem logcat.

## 1. Implementacao

Controller/protocolo:
- Novas ops: `GET_PRIVILEGED_BRIDGE_STATUS` (read), `REQUEST_SHIZUKU_PERMISSION` (read),
  `STOP_APP` e `CLEAR_APP_DATA` (mutating).
- `STOP_APP`/`CLEAR_APP_DATA` exigem package valido, allowlist explicita
  (`allowed_privileged_packages`) e denylist server-side; sem parametros de comando/argv.
- Reserva atomica de `command_id`, dedupe e deadline do baseline preservados.

APK:
- Dependencias oficiais `dev.rikka.shizuku:api:13.1.5` e `provider:13.1.5`; `ShizukuProvider`
  declarado no manifest.
- `PrivilegedBridge` usa `pingBinder`, `checkSelfPermission`, `requestPermission`, listeners de
  binder recebido/morto/resultado e `bindUserService` oficial. `READY` so com binder vivo,
  permissao concedida, UserService conectado e `effective_uid == 2000`.
- AIDL `IPrivilegedService`: `destroy()` (transaction 16777114), `getUid()`,
  `getProcessName()`, `stopApp(String)`, `clearAppData(String)`. Sem comando/argv/script.
- UserService roda como shell (UID 2000) e executa internamente operacoes fixas
  `/system/bin/am force-stop <pkg>` e `/system/bin/pm clear <pkg>` (argv fixo, package validado).
- `PrivilegedPolicy`: regex de package, denylist do proprio Agent, Shizuku, Shizuku manager,
  `android`, `com.android`, `com.google.android`, `com.samsung.android`.
- Capabilities `STOP_APP`/`CLEAR_APP_DATA` somente quando bridge READY; atualizadas por
  broadcast/heartbeat/reconnect sem storm.
- `Bot` nao foi alterado; nenhuma integracao no main.

## 2. Bridge state (real)

| Campo | Valor |
|---|---|
| backend | shizuku |
| available / binder_alive | true |
| authorized | true |
| user_service_ready | true |
| effective_uid | **2000** |
| process_name | `com.sln.deviceagent:privileged` |
| api_version | 13 |
| shizuku_version | 13.6.0.r1086.2650830c |
| state | **READY** |

Antes da autorizacao: `AVAILABLE_NOT_AUTHORIZED`, `STOP_APP/CLEAR_APP_DATA=false`.
A solicitacao abriu o dialogo oficial Shizuku; a autorizacao QA foi concedida via UI
(“Permitir o tempo todo”) em nome do operador. Depois: READY/UID 2000.

## 3. Provas reais no `.124`

Target QA: `com.sln.prepositions` (terceiro; permitido explicitamente; Agent/Shizuku negados).

| Passo | Resultado |
|---|---|
| START_APP target | success; foreground do target; pid ativo |
| STOP_APP target | success; stopped=true; verified_stopped=true; UID 2000; 333 ms; target pid vazio; foreground saiu; Agent e Shizuku vivos |
| START_APP novamente | success; target relancado |
| CLEAR_APP_DATA target | success; cleared=true; verified_stopped=true; UID 2000; 8.248 s; target pid vazio |
| Marcador de dados | `E35_MARKER` em external files antes; removido apos clear; app inicia limpo |
| START_APP pos-clear | success |
| Dedupe STOP (mesmo UUID) | 2 respostas success; **1 execucao fisica** (`STOP pkg` log=1) |
| Dedupe CLEAR (mesmo UUID) | 2 respostas success; **1 clear fisico** (`CLEAR pkg` log=1) |
| Disconnect pos-dispatch | `uncertain/CONNECTION_LOST`; retry `uncertain`; 1 clear fisico; sem reexecucao |
| Bridge morto durante CLEAR | `uncertain/PRIVILEGED_BRIDGE_LOST`; retry apos restart `uncertain`; 1 operacao fisica |
| Agent restart | reconnect; bridge READY/UID 2000 |
| Controller restart | reconnect; bridge READY/UID 2000 |

## 4. Lifecycle Shizuku

| Cenario | Resultado |
|---|---|
| A. READY | READY/UID 2000 |
| B. Shizuku parado (`kill shizuku_server`) | `BINDER_DEAD`; available/binder false; UID null; caps STOP/CLEAR false |
| Comando durante BINDER_DEAD | `CAPABILITY_NOT_SUPPORTED` no controller; nada ao APK |
| C. permissao revogada | **nao validado**: o switch do gerenciador nao revogou neste teste (status permaneceu READY) |
| D. binder morto | mesmo estado BINDER_DEAD; bridge sem storm |
| E. Shizuku reiniciado (`libshizuku.so --apk=...`) | starter exit 0; binder recebido; READY/UID 2000; caps true |
| F. Agent reiniciado | READY/UID 2000 |
| G. Controller reiniciado | READY/UID 2000 |

O servidor reiniciado permaneceu funcional; permissao persistiu atraves do restart.
Nao houve loop de bind/reconnect (listener + single-flight/backoff).

## 5. Seguranca (source audit)

- Nenhum `EXEC_SHELL`, `Runtime.exec` remoto, argv/command do Controller ou stdin arbitrario.
- UserService expoe apenas package em metodos tipados; comandos internos fixos.
- Allowlist Controller obrigatoria + denylist Agent/UserService do proprio Agent, Shizuku,
  Shizuku manager e prefixes de sistema.
- `STOP_APP`/`CLEAR_APP_DATA` sao mutating e usam o executor serializado/dedupe/deadline.
- `CLEAR_APP_DATA` e destrutivo e nunca e automaticamente reexecutado apos UNCERTAIN.
- Binder/UserService check de UID != 2000 => ERROR, capabilities false.

## 6. Testes/regressao

- `tests.test_device_agent` + manifest tests: **68/68 OK**.
- Cobertura offline: bridge status mock, allowlist/denylist, self/Shizuku/system blocked,
  STOP/CLEAR capability, dedupe, deadline pre-dispatch, disconnect => UNCERTAIN.
- `compileall device_agent`: OK.
- Provas reais: STOP/CLEAR/dedupe/UNCERTAIN/bridge death/restarts acima.

## 7. Limitacoes restantes

1. Revogacao de permissao nao foi validada no device (UI switch nao alterou o estado nesta rodada).
2. Parar Shizuku exige reinicio manual/script externo (usado `libshizuku.so`); Shizuku nao
   auto-restarta. O bootstrap/instalacao/inicio/authorization continua fora do Agent.
3. `CLEAR_APP_DATA` via `pm clear` levou ~8,2 s; STOP ~333 ms.
4. Logcat/eventos continuam fora desta fase.
5. Nenhuma integracao no `main_sln_v2`; operacoes privilegiadas seguem opt-in por allowlist.
6. CLEAR destroi dados do alvo; a UI/Controller deve tratar como acao de alto risco.

## Artefatos

- `state/device_agents/e35b_report.json`
- `state/device_agents/e35b_lifecycle.json`
- `state/device_agents/e35b_dedupe.json`
- `state/device_agents/e35b_shizuku_lifecycle.json`
- `state/device_agents/e35b_bridge_death.json`

**Status: E3.5B SHIZUKU BRIDGE = PASS** no device real (READY/UID 2000, STOP/CLEAR reais,
dedupe, UNCERTAIN, bridge death/restart; ressalvas documentadas).
