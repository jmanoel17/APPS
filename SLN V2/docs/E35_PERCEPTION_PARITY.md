# E3.5 - Perception Parity (GET_UI_TREE / CAPTURE_SCREEN / Foreground)

Data: 2026-11-15
Device: `.124` (SM-G975F, Android 12/sdk31), agent 0.3
Escopo: camada read-only de percepcao no Device Agent + comparacao com ADB/uiautomator2.
Sem alteracao no `main_sln_v2`, sem FIND_ELEMENT/TAP_ELEMENT, sem STOP/CLEAR, sem logcat, sem OCR.

## 1. Implementacao

Controller/protocolo:
- Ops novas read-only: `GET_UI_TREE`, `CAPTURE_SCREEN`; `GET_FOREGROUND_APP` estendido.
- Validacao de limites no controller e no APK; erros tipados (`UI_TREE_*`, `SCREENSHOT_*`,
  `RATE_LIMITED`).
- Capabilities dinamicas por Accessibility/serviceInfo/sdk.

APK:
- `accessibility_service_config.xml`: `canRetrieveWindowContent=true`, `canTakeScreenshot=true`,
  `canPerformGestures=true`.
- `onServiceConnected`: adiciona `FLAG_REPORT_VIEW_IDS` e `FLAG_INCLUDE_NOT_IMPORTANT_VIEWS`
  (resource-ids/containers similares ao uiautomator).
- `DeviceAccessibilityService`: traversal iterativo com limites/timeout/cycle guard/payload cap,
  redaction de senha, `takeScreenshot`, rate limit e logs **somente de metricas**.
- Foreground: `TYPE_WINDOW_STATE_CHANGED` + fallback read-only `UsageStatsManager` (appop
  `GET_USAGE_STATS`, concedida no bootstrap ADB) para activity quando o ROM/evento nao entrega
  `className`; package fallback via `rootInActiveWindow`; activity desconhecida fica `""`.
- `GET_STATUS`: `device_time_ms`, `elapsed_realtime_ms`, `screen_on`, `keyguard_locked` (KeyguardManager),
  foreground activity/component/window.
- Sem `EXEC_SHELL`, sem acao por node, sem FIND_ELEMENT.

Testes: `tests.test_device_agent` + manifest **64/64 OK**; `compileall` OK.

## 2. Capabilities (device real)

Com Accessibility ligado e appop concedida:
`GET_UI_TREE=true`, `CAPTURE_SCREEN=true`, `GET_FOREGROUND_APP=true` (package+activity),
BACK/HOME/TAP/SWIPE=true.
Com Accessibility desligado ou durante o bot uiautomator2: `GET_UI_TREE/CAPTURE_SCREEN=false`
(capabilities refletem o servico indisponivel). Em API<30 `CAPTURE_SCREEN=false` por codigo
(validado no controller via mock; device e sdk31).

## 3. UI tree schema final

```json
{
  "snapshot_id": "uuid",
  "captured_at_ms": 0,
  "package": "...",
  "activity": "...|",
  "window_id": 0,
  "display": {"width_px":1080,"height_px":2280,"rotation":0,"density_dpi":420},
  "nodes": [{
    "node_id":"n0","parent_id":null,"depth":0,
    "class":"android.widget.FrameLayout","text":"","content_desc":"",
    "resource_id":"","bounds":[0,0,1080,2280],
    "clickable":false,"enabled":true,"visible":true,"scrollable":false,
    "password":false,"child_count":0
  }],
  "truncated": false,
  "capture_ms": 7,
  "payload_bytes": 3065
}
```

Limites: `max_depth<=64` (default 40), `max_nodes<=3000` (default 1500),
`max_text_length<=500` (default 160), `max_payload_bytes<=2.000.000` (default 1.000.000).
`node_id` e estavel apenas dentro do snapshot. `password=true` => `text=""` e `content_desc=""`.

## 4. Screenshot schema final

```json
{
  "captured_at_ms": 0, "width":1080, "height":2280, "rotation":0,
  "format":"png", "size_bytes":49918, "sha256":"...", "payload":"<base64 PNG>",
  "capture_ms":28, "encoding_ms":248
}
```
API `AccessibilityService.takeScreenshot` (sdk>=30); `max_dim<=4096`; `max_payload_bytes<=3.000.000`
(default 2.000.000); rate limit 200 ms; PNG preterido; `SCREENSHOT_TOO_LARGE` ou
`SCREENSHOT_FAILED:<code>`; nenhum payload em log.

## 5. Matriz ADB vs Agent (shadow)

| Cenario | Amostras | Nodes Agent/ADB | RID Jaccard | Text Jaccard | Activity/Package | Cobertura |
|---|---|---|---|---|---|---|
| MainActivity + botao reward | 1 | 10 / 10 | 1.0 | 1.0 | 1/1 ; 1/1 | FULL 1/1 |
| AppLovin playable/WebView | 14 | mediana 35 / 35 | 0.95 (p50 1.0) | 0.93 (p50 1.0) | 14/14 ; 14/14 | FULL 13, PARTIAL 1 (transicao) |
| WebView coverage | 14 | - | - | - | - | FULL 13, PARTIAL 1, EMPTY 0 |

Screenshot shadow: **15/15** capturas, dimensoes 15/15, diff medio **0.21** (p95 1.0; 0..255),
bytes/SHA validos. Nenhuma acao do Agent; comparacao manual/sequencial.

Bounds:
- MainActivity `btnRewardAds` Agent = ADB = `[251,1137,829,1295]`; `txtMoedas` idem.
- Ad playable: `game-field` e `game-controls` Agent = ADB exatamente; `root/playable/game`
  diferem apenas no fundo (Agent `...2280` vs uiautomator `...2047`, que exclui nav bar).
  Agent usa coordenadas de tela (compativeis com TAP); screenshot identico confirma.
- Nao houve no criativo observado um close/CTA clicavel exposto pelos dois lados; prova de bounds
  de fechamento anonimo fica pendente.

## 6. Cobertura por cenario

Observados: **MAIN_REWARD**, **AD_APPLOVIN (playable WebView)**.
Nao observados nesta captura controlada: CONSENT, STORE, REWARDED_COUNTDOWN, ENDCARD, SUB_AD
separado, retorno a Main depois do anuncio. O bot separado abriu rewarded e interstitial reais
(`e35_bot.log`, 2 anuncios; rewarded com countdown 20..1 e endcard structural X), mas nao foi
possivel shadow paralelo (ver secao 10). Nao inventar cenarios.

## 7. WebView coverage

No criativo playable observado: 13/14 FULL, 1 PARTIAL em transicao (13 nodes vs 35; webview
presente). Resource-ids/texts essenciais coincidiram; arvore contem WebView->adFrame, textos e
bounds de jogo. Criativos diferentes podem reduzir rids/nodes (observado em outro Ad 32/35 e
subset de rids); cobertura WebView e variavel por criativo.

## 8. Latencia (MainActivity estavel)

| Metrica | avg | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| GET_UI_TREE (100) wall_ms | 49,42 | 48,13 | 65,94 | 75,90 | 84,13 |
| GET_UI_TREE capture_ms | ~7-9 | 7 | 11 | 13 | 13 |
| ADB uiautomator dump (20) | 2364,75 | 2362,84 | 2393,17 | 2394,23 | 2394,23 |
| CAPTURE_SCREEN (30) total_ms | 353,24 | 349,66 | 370,22 | 428,57 | 428,57 |
| ADB screencap (30) total_ms | 402,66 | 395,03 | 446,73 | 525,37 | 525,37 |

- UI tree: 100/100 success, 10 nodes, 3.065 bytes, 0 truncated, 0 failure.
- Screenshot: 30/30 success, dimensoes corretas, PNG/SHA validos, diff pixel **0.0** (estavel).
- `CAPTURE_SCREEN` adiciona ~250 ms de encoding PNG (comprimido); payload ~50-930 KB conforme tela.

## 9. Throughput / stress

| Cadencia tree | req | req/s | bytes/s | wall avg | capture avg | falhas |
|---|---|---|---|---|---|---|
| 1000 ms | 12 | 1,0 | 3.023 | 56,66 ms | 8,58 ms | 0 |
| 500 ms | 24 | 2,0 | 6.048 | 53,75 ms | 7,67 ms | 0 |
| 300 ms | 40 | 3,33 | 10.070 | 54,46 ms | 7,25 ms | 0 |

- 300 ms e sustentavel: 3,33 dumps/s, 0 falhas; PSS 41,7-64,3 MB; `top -p` no instante amostrado
  mostrou 0,0% (abaixo da resolucao); controller 1,5-5,7% CPU. O `dumpsys cpuinfo` antigo mostrou
  linha de PID obsoleto (45%) e foi descartado.
- CAPTURE: 1/s 8/8; **2/s 16/16**, ~359 ms/frame, PSS ~55-56 MB. Acima de 2/s nao validado.

## 10. Divergencias e limitacao critica

1. **Shadow paralelo impossivel neste ROM**: ao iniciar o bot (uiautomator2/UiAutomation), o
   AccessibilityService do Agent e desabilitado (`enabled=false`), mesmo reabilitando
   `settings put secure enabled_accessibility_services`; 136 samples => 136 falhas de tree.
   Com o bot parado, o servico volta. Portanto comparacao foi **sequencial/manual**: bot abriu
   anuncio real em run separado; para captura do mesmo estado usou-se drive ADB manual sem
   uiautomator2, com Agent ativo. Nenhuma decisao do bot foi alterada; nenhuma acao do Agent.
2. Activity: o ROM nao entrega `className` no evento em varios casos; fallback UsageStats resolve
   com appop `GET_USAGE_STATS` (grant ADB). Sem appop, `activity=""` honestamente.
3. Bounds de containers full-screen diferem do uiautomator por inset de nav bar (Agent screen coords
   vs ADB content); elementos clicaveis essenciais batem. Close anonimo nao observado no criativo.
4. Nodes/rids variam por criativo (WebView virtual nodes); cobertura FULL no playable observado,
   mas nao universal.
5. IA offline no host; comparacao visual usou OpenCV (barra azul) e diff de pixels, nao o
   classificador IA de producao.
6. Screenshots nao foram persistidos (apenas metricas/hashes), por privacidade.

## 11. Blockers restantes

- `STOP_APP`/force-stop e `CLEAR_APP_DATA`: continuam fora do Agent comum (ADB/device-owner/root).
- `GET_EVENTS`/logcat (`READ_LOGS`) continua fora; fallback visual existe, mas perde FORMAT/reward.
- Activity sem appop fica vazia; appop exige bootstrap ADB.
- FIND_ELEMENT/TAP_ELEMENT nao implementados (filtro fica no host).
- Cobertura WebView/fechamento anonimo precisa de mais criativos.
- CAPTURE acima de 2/s nao validado; payload maximo depende do criativo.
- Shadow paralelo bloqueado por uiautomator2; migracao deve ser Agent-only ou usar outro device.

## 12. Recomendacao E3.6

1. Implementar somente `FIND_ELEMENT`/`TAP_ELEMENT` tipados sobre GET_UI_TREE (sem XPath livre)
   e rodar um **decision-shadow Agent-only** (ADB fora do loop) sobre cenarios reproduziveis.
2. Capturar rewarded/countdown/endcard/store/consent em sequencia dedicada e mais criativos,
   comparando com dumps ADB gravados previamente (nao simultaneos).
3. Resolver lifecycle: device-owner (`clearApplicationUserData`) vs sidecar ADB para STOP/CLEAR.
4. So depois considerar integracao no `main_sln_v2`; E3.5 nao integra.

**Status: E3.5 PERCEPTION PARITY = PASS para as ops read-only observadas (UI tree/screenshot/
foreground), com as ressalvas acima. Nao e paridade completa do bot.**
