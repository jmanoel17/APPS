# E3.3A.1 - Hardening do Device Agent

Data: 2026-11-15
Device real: `.124` (SM-G975F, Android 12/sdk31), agent 0.2 (`01bedbfd-...`)
Escopo: robustez/consistencia apenas. Sem features novas, sem screenshot/OCR, sem shell,
sem integracao main/DPN.

## 1. Arquivos alterados

Controller/store:
- `device_agent/protocol.py` (UUID4, `is_command_id`)
- `device_agent/errors.py` (novos codigos)
- `device_agent/state.py` (transicoes monotonas)
- `device_agent/server.py` (RESULT binding/allowlist, START_APP allowlist, wait budget)
- `tests/test_device_agent.py` (50 testes)

APK:
- `GestureLifecycle.kt` (novo: state machine por gesto)
- `DeviceAccessibilityService.kt` (usa GestureLifecycle)
- `ConnectionGuard.kt` (novo: single-flight/generation)
- `WsClient.kt` (usa ConnectionGuard)
- `CommandExecutor.kt` (validacao local, UNCERTAIN, cache terminal)
- `ProvisionActivity.kt` (gate de origem + wss)
- `QaActivity.kt` movida para `src/debug/java` + `src/debug/AndroidManifest.xml`
- `src/main/AndroidManifest.xml` (QA removida do build operacional)

Docs: este arquivo + MEMORIA/CURRENT_STATE.

## 2. Correcoes por prioridade

P1 Gesture lifecycle: estados explicitos e timeout sem race; nunca despacha apos resultado
NOT_DISPATCHED; callback tardio nao altera terminal; caminho fisico unico por lane.
P2 Command monotonic: `UPDATE ... WHERE status NOT IN (terminais)` + `INSERT OR IGNORE`;
retorno bool; terminal nunca regride.
P3 Reconnect single-flight: `ConnectionGuard` com estados e `connectionGeneration`; um unico
handshake/reconnect agendado; callbacks antigos ignorados; NetworkCallback so antecipa.
P4 RESULT binding: valida protocolo, agent autenticado, `device_id`, `op`; divergencia gera
evento tipado e nao altera Store/Future.
P5 Status allowlist: apenas `success/failure/expired/uncertain/duplicate`; desconhecido =>
`INVALID_RESULT_STATUS`. `duplicate` mapeia conservadoramente para `UNCERTAIN`.
P6 command_id: UUID4 textual no controller e APK; invalido => `INVALID_COMMAND_ID` sem executar.
P7 START_APP: `allowed_packages` obrigatorio no server (default vazio = nega); fora da lista
=> `PACKAGE_NOT_ALLOWED`, sem envio. APK mantem `PACKAGE_NOT_FOUND/NOT_LAUNCHABLE`.
P8 Superficies: QA apenas debug (`exported=true` so no manifest debug). Provision continua
`exported=true` para bootstrap ADB, mas aceita apenas shell/root (`referrer=android-app://com.android.shell`
e `callingPackage=null`) ou self; exige `wss://`. Decisao documentada; bootstrap testado.
P9 APK: valida protocolo, type, UUID, agent/device, op allowlisted, deadline e params/bounds/
duration antes de executar; selftest on-device cobre 7 casos.
P10 Server: `timeout` de espera separado do TTL (`deadline_ms`); antes ele encurtava o deadline,
impedindo teste pos-dispatch correto.

## 3. State machine do gesto

```
NOT_DISPATCHED --dispatch ok--> DISPATCHING --dispatch true--> DISPATCHED
      |                               |                            |
 timeout antes                   dispatch false               onCompleted -> COMPLETED
      v                               v                            onCancelled -> CANCELLED
 NOT_DISPATCHED(EXPIRED,        CANCELLED(GESTURE_CANCELLED)   timeout -> UNKNOWN_AFTER_DISPATCH(UNCERTAIN)
 side_effect=false)             side_effect=false
```

Regras implementadas:
- timeout antes do Runnable => `NOT_DISPATCHED`/EXPIRED; Runnable ve resultado e nao despacha;
- `dispatchGesture=true` + sem callback => `UNKNOWN_AFTER_DISPATCH`/`GESTURE_UNCERTAIN`;
- callback tardio muda apenas estado observavel, nunca o resultado terminal;
- cada job tem token/proprio latch/estado; callback de um job nao toca outro;
- lane mutavel so libera apos callback ou safety timeout `duration + 1.5s` (UNCERTAIN).

## 4. State machine do comando

```
QUEUED -> SENT -> {SUCCESS, FAILURE, EXPIRED, UNCERTAIN, CANCELLED}
```

- `TERMINAL_STATUSES` congelados no SQLite; update condicional retorna bool.
- `INSERT OR IGNORE` impede reabrir comando existente; `UNCERTAIN -> SENT` e proibido.
- RESULT so resolve Future se binding e status forem validos e o estado atual nao terminal.

## 5. State machine do reconnect

```
DISCONNECTED -> CONNECTING -> AUTHENTICATING -> CONNECTED
      ^             |               |              |
      +-- falha/close (geracao atual) ------------+
STOPPED (stop/revogacao)
```

- `beginConnect` so sai de DISCONNECTED; gera `connectionGeneration`;
- no maximo 1 handshake e 1 reconnect Runnable; agendar cancela o anterior;
- `NetworkCallback.onAvailable`/`reconnectNow` nunca abrem segunda conexao;
- callbacks de geracao antiga sao ignorados (nao derrubam conexao nova);
- retry/backoff com jitter mantidos; reset apenas em HELLO_OK.

## 6. Testes novos

Python/controller (50/50):
- store monotono (UNCERTAIN nao vira SENT; SUCCESS/FAILURE nao regridem; duplicate nao reabre);
- UUID invalido; RESULT agent/device/op/protocol mismatch; status invalido;
- START_APP permitido/negado/inexistente; dedupe/disconnect continuam.

On-device selftest (QaActivity debug; 14/14 PASS):
- gesture: timeout pre-dispatch sem dispatch; timeout pos-dispatch UNCERTAIN; callback tardio;
  isolamento entre gestures;
- connection: single-flight (3 triggers = 1 connect); geracao antiga ignorada; um reconnect agendado;
- APK: protocolo, agent, device, UUID, op, deadline invalidos + comando valido.

## 7. Regressoes

- `tests.test_device_agent`: **50/50 OK**.
- `compileall device_agent`: OK.
- Debug APK build: OK. Release merged manifest: `QaActivity` ausente (`QA=False`);
  empacotamento release completo bloqueado apenas por `lint-gradle` nao cacheado offline.
- Bootstrap ADB: `PROVISION_CALLER ... allowed=true`; URL nao-wss rejeitada no codigo.

## 8. Resultado real .124

| Caso | Resultado | Evidencia |
|---|---|---|
| SELFTEST | PASS 14/14 | gesture 4, guard 3, APK 7 |
| TAP basico | PASS | taps 0->1 |
| SWIPE vertical | PASS | scrollY 0->1229 |
| DEDUPE | PASS | 2 respostas success; taps 1->2 |
| CONCORRENTES | PASS | 2 TAPs; 0,547s (serial) |
| A11Y off/on | PASS | caps false -> CAPABILITY_NOT_SUPPORTED -> caps true |
| DISPLAY RESIZE | PASS | `wm size 1920x1080` -> rotation 3; TAP 445,384 success; restaurado |
| PRE_DISPATCH | PASS | `expired/EXPIRED`; counter 5->5; swipe longo success |
| POST_DISPATCH | PASS | `uncertain/COMMAND_TIMEOUT`; retry uncertain; scroll 741->1455->1455 |
| DISCONNECT | PASS | `uncertain/CONNECTION_LOST`; retry uncertain; scroll 1455->2136 |
| BENCH TAP | PASS | 30/30, 0 falhas, counter +30 |
| BENCH SWIPE | PASS | 30/30, 0 falhas |

Benchmark limpo (run isolado, mesmo device):

| | avg | p50 | p95 | p99 | falhas |
|---|---|---|---|---|---|
| TAP total | 62,94 | 62,46 | 73,46 | 76,82 | 0/30 |
| TAP transport | 14,68 | 13,53 | 22,90 | 32,82 | - |
| TAP queue/gesture | 0,97 / 47,30 | 1 / 47 | 2 / 52 | 2 / 54 | - |
| SWIPE total | 278,08 | 276,80 | 294,77 | 301,15 | 0/30 |
| SWIPE transport | 14,45 | 13,44 | 20,02 | 20,08 | - |
| SWIPE queue/gesture | 1,37 / 262,27 | 1 / 259 | 3 / 281 | 4 / 281 | - |

No run completo (com cenarios antes), 0 falhas tambem; um stall de transporte (~2 s) apareceu
1x/30 no TAP e 1x/60 no conjunto, associado a I/O do host/SQLite (uma escrita de 264 ms foi
observada), nao ao caminho de gesture. Benchmark limpo acima e a referencia de latencia.

## 9. Zero feature nova

Nao foram adicionadas ops, screenshot, OCR, shell, STOP_APP, worker, DPN ou integracao main.
TAP/SWIPE existentes foram apenas endurecidos; selftests sao debug-only.

## 10. Riscos restantes

1. Stalls de I/O do host/SQLite podem gerar p99 de transporte ocasional (~2 s), sem perda de
   corretude; futuro: WAL/commit assincrono (nao feito).
2. `allowed_packages` default vazio: START_APP fica negado ate configurar a allowlist.
3. Provision depende de `referrer`/`callingPackage`; shell/root e self sao aceitos, apps comuns
   rejeitados. Ambientes com `am start` sem referrer podem exigir ajuste (neste ROM: OK).
4. Se o sistema nunca entregar callback e mantiver o gesto ativo alem da duracao declarada, a lane
   libera em `duration + 1.5s` como UNCERTAIN (premissa: stroke termina na duracao).
5. Duplicata concorrente do mesmo UUID antes do primeiro insert ainda depende do dedupe do APK;
   estado terminal nao regride, mas nao ha reserva in-flight no controller.
6. Build release completo offline nao fecha por falta de `lint-gradle` no cache; manifest release
   e compiled source set confirmam QA ausente.

## Artefatos

- `state/device_agents/e32b/e33a1_report.json` (13 casos + 14 selftests)
- `state/device_agents/e32b/e33a1_bench.json` (benchmark limpo)
- APK debug: `device_agent_apk/app/build/outputs/apk/debug/app-debug.apk`

## Criterio de aprovacao

- Gesture nao executa apos NOT_DISPATCHED: selftest + PRE_DISPATCH real PASS.
- Timeout pos-dispatch UNCERTAIN: selftest + POST_DISPATCH real PASS.
- Terminal nao regride: testes SQLite PASS.
- Reconnect single-flight: selftest + reconnect real PASS.
- RESULT vinculado ao comando autenticado: 4 testes PASS.
- Testes de race: PASS; regressao 50/50.

**Status: E3.3A PRODUCTION BASELINE = PASS** (riscos acima documentados).
