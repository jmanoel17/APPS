# E3.3A - TAP/SWIPE via DeviceAccessibilityService

Data: 2026-11-15
Device: `192.168.1.124:5555` - SM-G975F, Android 12 (sdk 31)
Agent: `com.sln.deviceagent` 0.2 (`agent_id 01bedbfd-cbd7-4130-9eee-55c0f46e91aa`)
Display: `1080x2280 @420dpi rotation=0` (override); controller `wss://127.0.0.1:8766`
QA Activity: `com.sln.deviceagent/.QaActivity` (botao + contador + ScrollView + HorizontalScrollView)

## Protocolo

- Ops novas: `TAP`, `SWIPE`; ambas em `MUTATING_OPS` no controller e no executor unico do APK.
- `TAP {x, y, duration_ms}`; `SWIPE {x1, y1, x2, y2, duration_ms}`.
- Faixas: TAP `10..1000 ms`; SWIPE `10..3000 ms`.
- Validacao: numericos, bounds do display atual (`0 <= x < width_px`, `0 <= y < height_px`),
  display conhecido, deadline ainda valido. APK revalida antes de despachar.
- `HELLO` anuncia `TAP`/`SWIPE` apenas com Accessibility conectado, `dispatchGesture` disponivel
  (sdk>=24) e display metrics validas; Accessibility desligado => ambos `false`.
- Resultado somente apos callback real: `onCompleted` => success; `onCancelled` =>
  failure/`GESTURE_CANCELLED`; sem callback => `GESTURE_TIMEOUT`.
- `GET_STATUS`/`GET_DEVICE_INFO` expoem `display{width_px,height_px,rotation,density_dpi}`
  (e campos flat). Mudanca de configuracao/heartbeat dispara novo HELLO.
- `command_id` obrigatorio; server e APK deduplicam. Queda apos dispatch => `UNCERTAIN`,
  sem reenvio automatico.

## Matriz (18 casos)

| Caso | Resultado | Evidencia |
|---|---|---|
| TAP-1 centro | PASS | success; contador +1; gesture 70ms |
| TAP-2 borda | PASS | x=25; success; contador +1 |
| TAP-3 negativo | PASS | BAD_PARAMS; contador inalterado |
| TAP-4 > display | PASS | BAD_PARAMS |
| TAP-5 accessibility off | PASS | caps TAP=false; CAPABILITY_NOT_SUPPORTED |
| TAP-5b accessibility on | PASS | caps TAP=true |
| TAP-6 command_id duplicado | PASS | 2 respostas success; contador +1 |
| TAP-7 2 TAPs concorrentes | PASS | ambos success; 0,514s (serial) |
| TAP-8 rotacao/display antes do TAP | PASS | rot=3, layout 1080x1920; TAP 445,384 success |
| SWIPE-1 vertical | PASS | scrollY 0 -> 1229 |
| SWIPE-2 horizontal | PASS | scrollX 0 -> 1402 |
| SWIPE-3 curta 40ms | PASS | success |
| SWIPE-4 longa 1500ms | PASS | success |
| SWIPE-5 coordenadas invalidas | PASS | origem/destino BAD_PARAMS |
| SWIPE-6 accessibility off | PASS | CAPABILITY_NOT_SUPPORTED |
| SWIPE-6b accessibility on | PASS | caps SWIPE=true |
| SWIPE-7 2 swipes concorrentes | PASS | ambos success; 0,522s (serial) |
| SWIPE-8 disconnect durante gesture | PASS | uncertain/CONNECTION_LOST; retry uncertain; scroll mudou 1x |

Lifecycle: disconnect/reconnect PASS; Accessibility true; `TAP`/`SWIPE` reanunciados;
dedupe apos reconnect PASS (mesmo `command_id` nao reexecuta).

Rotacao: `settings/wm user-rotation` e ignorado neste ROM (mRotation permaneceu 0). Foi usado
`wm size 1920x1080`, que gerou `rotation=3` e layout `1080x1920`; o TAP no novo centro passou.
Depois `wm size 1080x2280` restaurou o estado. Limitacao documentada (nao e rotacao por sensor).

## Benchmark 30 + 30 (sem falhas)

`total` = SENT -> RESULT no controller; `transport` = total - queue - gesture (residual RTT);
`queue` = APK recebeu -> dispatch; `gesture` = dispatch -> callback.

| Metrica | TAP avg | p50 | p95 | p99 | SWIPE avg | p50 | p95 | p99 |
|---|---|---|---|---|---|---|---|---|
| total_ms | 65,10 | 64,74 | 75,97 | 78,33 | 284,54 | 284,88 | 294,61 | 295,20 |
| transport_ms | 16,10 | 15,59 | 24,14 | 30,97 | 16,44 | 15,27 | 25,18 | 25,26 |
| queue_ms | 1,43 | 1,00 | 2,00 | 3,00 | 1,17 | 1,00 | 3,00 | 4,00 |
| gesture_ms | 47,57 | 46,00 | 56,00 | 61,00 | 266,93 | 267,00 | 278,00 | 278,00 |
| wall_ms | 84,29 | 83,36 | 103,03 | 107,54 | 309,93 | 309,11 | 318,19 | 324,00 |

- Falhas: TAP 0/30; SWIPE 0/30. Contador de TAP +30 exato.
- Duracao de gesture acompanha o pedido (TAP 40-60ms; SWIPE 250ms) com overhead baixo.

## Comparacao ADB (QA opcional, mesmo app)

| Primitiva | n | falhas | avg_ms | p50 | p95 | p99 | evidencia |
|---|---|---|---|---|---|---|---|
| ADB `input tap` | 30 | 0 | 180,85 | 176,47 | 201,34 | 208,04 | contador +30 |
| Agent TAP | 30 | 0 | 65,10 | 64,74 | 75,97 | 78,33 | contador +30 |
| ADB `input swipe` | 30 | 0 | 482,03 | 415,43 | 447,83 | 2381,90 | scroll +6371 |
| Agent SWIPE | 30 | 0 | 284,54 | 284,88 | 294,61 | 295,20 | success 30/30 |

ADB inclui overhead de processo/shell; comparacao apenas QA, sem mudanca de implementacao.

## Testes e seguranca

- `tests.test_device_agent`: **41/41 OK** (26 anteriores + 15 de TAP/SWIPE).
- `compileall device_agent`: OK.
- Sem shell remoto, SCREENSHOT, STOP_APP, execucao paralela de gestos ou token em log.
- Bounds recusados no controller e no APK; fila/BUSY e dedupe preservados.

## Artefatos

- `state/device_agents/e32b/e33a_report.json` (matriz, benchmark, ADB, lifecycle)
- APK: `device_agent_apk/app/build/outputs/apk/debug/app-debug.apk`
- QA: `QaActivity.kt`; orquestrador: `%TEMP%\opencode\e33a_orchestrate.py`

## Estado final

- App force-stopped, `adb reverse` removido, controller encerrado, porta 8766 livre.
- Display restaurado `1080x2280`; Accessibility **habilitada** ao final.
