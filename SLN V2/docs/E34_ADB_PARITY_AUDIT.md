# E3.4 - ADB Parity Audit

Data: 2026-11-15
Escopo: auditoria read-only do SLN V2. Nenhuma feature, integracao ou alteracao no bot foi feita.
Fonte primaria: `main_sln_v2.py`, `interface_sln_v2.py`, scripts de bootstrap/QA, `dpn/`, `device_agent/`.
Artefato completo: `state/device_agents/e34_parity_inventory.json` (55 itens).

## 1. Executive summary

O Device Agent cobre bem as primitivas de acao e de sistema basico (TAP, SWIPE, BACK, HOME,
START_APP, display, identidade, bateria, IP), mas **nao consegue reproduzir o bot atual sem ADB no
hot path**. O bloqueio nao esta no clique: esta na **percepcao** (arvore de UI, screenshot, package
+ activity) e no **ciclo de vida privilegiado** (force-stop/pm clear).

Resposta objetiva: faltam `GET_UI_TREE`, `CAPTURE_SCREEN`, foreground com activity, busca/clique
semantico tipado, `STOP_APP`/force-stop, `CLEAR_APP_DATA` e observacao de eventos/logcat. Destes,
UI tree e screenshot sao implementaveis via AccessibilityService sem root; force-stop/pm clear
exigem ADB, root ou device-owner (o app comum nao pode executar esses comandos contra outro pacote).

Scores (metodologia na secao 9):

| Metrica | Valor |
|---|---|
| Primitivas operacionais distintas cobertas | **40,3%** (9 COVERED, 7 PARTIAL, 15 MISSING de 31) |
| Hot path coberto (16 etapas, peso 1/0,5/0) | **37,5%** |
| Chamadas estimadas cobertas (modelo por ciclo) | **21,7%** |
| Blockers restantes | **6** |

TAP/SWIPE tem paridade funcional validada (E3.3A), mas sem UI tree/screenshot o bot nao sabe
**onde** clicar nem **se** fechou o anuncio: a cobertura de acao nao compensa a falta de percepcao.
ADB continua necessario para bootstrap (install/provision/reverse/descoberta), para force-stop/pm
clear ate existir device-owner, e para logcat se `READ_LOGS` nao for concedido.

## 2. Inventario

### 2.1 Primitivas operacionais (hot path)

Legenda de cobertura: C=COVERED, P=PARTIAL, M=MISSING. Mut.: RW=mutating, R=read-only.

| ID | Primitiva / evidencia | Backend | Freq | Hot | Mut | Cobertura | Substituto proposto |
|---|---|---|---|---|---|---|---|
| A01 | tap por coordenada (`d.click` 513/3469/3514/...; `clicar_*`) | uiautomator2 | VERY_HIGH | sim | RW | C | TAP |
| A02 | swipe 4 direcoes (`d.swipe` 3970-3976) | uiautomator2 | MEDIUM | sim | RW | C | SWIPE |
| A03 | `d.swipe_ext("up")` (4433/4489) | uiautomator2 | MEDIUM | sim | RW | C | SWIPE |
| A04 | BACK (`d.press("back")` 712/3383/3415/4137/...) | uiautomator2 | HIGH | sim | RW | C | BACK |
| A05 | HOME (`d.press("home")` 3669/3947/4051/4193) | uiautomator2 | MEDIUM | sim | RW | C | HOME |
| A06 | POWER (`d.press("power")` 426, is_doze) | uiautomator2 | LOW | sim | RW | M | WAKE_SCREEN (limitado/privilegiado) |
| B01 | start app (`d.app_start` 3721/3878/4360/4967/...) | uiautomator2 | HIGH | sim | RW | C | START_APP |
| B02 | stop app (`d.app_stop` 730/3671/.../5060) | uiautomator2 | HIGH | sim | RW | M | STOP_APP (privilegiado) |
| B03 | force-stop (`d.shell("am force-stop")` 4244/5061) | uiautomator2 shell | MEDIUM | sim | RW | M | STOP_APP (device-owner/ADB) |
| B04 | clear data (`d.shell("pm clear")` 4249) | uiautomator2 shell | LOW | sim | RW | M | CLEAR_APP_DATA (device-owner) |
| B06 | instalar app via Play Store (dump+click 4100-4104) | uiautomator2 | LOW | sim | RW | P | TAP + GET_UI_TREE |
| B08 | abrir activity/action especifica (`am start` 4175; VIEW) | uiautomator2 shell | LOW | sim | RW | P | OPEN_APP_ACTIVITY allowlist |
| C01 | screenshot (`d.screenshot` 569/4550) | uiautomator2 | HIGH | sim | R | M | CAPTURE_SCREEN |
| C02 | screencap fallback (`adb exec-out screencap` 576) | ADB | LOW | sim | R | M | CAPTURE_SCREEN |
| C03 | OpenCV barra azul (4540-4567) | host OpenCV | MEDIUM | sim | R | M | CAPTURE_SCREEN + analise no controller |
| C04 | IA `/detect`/`/classify` (584/617/1993) | screenshot + HTTP | HIGH | sim | R | M | CAPTURE_SCREEN |
| D01 | `d.dump_hierarchy` (364, choke point) | uiautomator2 | VERY_HIGH | sim | R | M | GET_UI_TREE |
| D02 | selectors/exists/wait (`d(resourceId/text/...).exists/click`) | uiautomator2 | HIGH | sim | R | M | FIND_ELEMENT/TAP_ELEMENT |
| D03 | XPath (`d.xpath` 525/554) | uiautomator2 | MEDIUM | sim | R | M | FIND_ELEMENT (sem XPath arbitrario) |
| D04 | bounds do dump -> click (469-519) | uiautomator2 | HIGH | sim | RW | P | GET_UI_TREE + TAP |
| D05 | app_current package+activity (447/1493/3316) | uiautomator2 | HIGH | sim | R | P | GET_FOREGROUND_APP + activity |
| D06 | window_size (646/743/3124/3965) | uiautomator2 | HIGH | sim | R | C | GET_STATUS display |
| D07 | saldo/countdown/texto do dump (1823/2360/2995/3344) | uiautomator2 dump | HIGH | sim | R | M | GET_UI_TREE + filtro no controller |
| E01 | dumpsys power/window keyguard/doze (405/408) | uiautomator2 shell | HIGH | sim | R | P | GET_STATUS keyguard/doze |
| E04 | display/metricas (`window_size`) | uiautomator2 | HIGH | sim | R | C | GET_STATUS display |
| E05 | pacote instalado/versao (`pm list` preflight) | uiautomator2 shell | BOOTSTRAP | sim | R | P | GET_APP_INFO (opcional) |
| F01 | logcat FORMAT/eventos (2647/3024) | ADB subprocess | HIGH | sim | R | M | GET_EVENTS/LOG_QUERY ou fallback visual |
| F03 | pidof/date do app (2633/2641/3018/3022) | ADB subprocess | HIGH | sim | R | M | GET_STATUS app_pid/device_time |
| F04 | ANR dialog selectors (4367-4374) | uiautomator2 | LOW | sim | R | P | GET_UI_TREE + TAP |
| F05 | artefatos erro screenshot+xml (4061/1536) | uiautomator2 | MEDIUM | sim | R | M | CAPTURE_SCREEN + GET_UI_TREE |
| G11 | IP/MAC (`GET_NETWORK_INFO`) | Device Agent | MEDIUM | sim | R | C | GET_NETWORK_INFO |

### 2.2 Bootstrap / diagnostico / fora do hot path

| ID | Primitiva | Evidencia | Cobertura |
|---|---|---|---|
| B05 | `adb install-multiple` (sideload GMS/Chrome) | `sideload_gms_chrome.py:142` | KEEP_ADB_BOOTSTRAP |
| B07 | uninstall de APK | nao usado no bot | KEEP_ADB_BOOTSTRAP |
| E02 | `dumpsys activity` (observador) | `observador.py:64` | KEEP_ADB_BOOTSTRAP |
| E03 | battery/pm/getprop (preflight/select) | `preflight_devices.py:89/99`, `select60.py:18` | KEEP (status basico ja coberto no Agent) |
| E06 | getprop/dumpsys readiness | `select60.py:18-21` | KEEP_ADB_BOOTSTRAP |
| E07 | `wm size/density` | nao no main (so QA E3.3A) | DO_NOT_PORT |
| E08 | `settings get/put` | nao no main | DO_NOT_PORT |
| F02 | logcat persistente | `persistent_logcat.py:127/164` | MISSING (default off; diagnostico) |
| F06 | metricas psutil/adb process | `benchmark_host.py:71/137` | KEEP (host) |
| G01 | `adb devices`/connect/scan | `interface_sln_v2.py:432`, `connect.py:37` | KEEP_ADB_BOOTSTRAP |
| G02 | `adb reverse` (DPN agent) | `dpn/agent_core/provision.py:24` | KEEP_ADB_BOOTSTRAP |
| G03 | install/update GMS/Chrome | `sideload_gms_chrome.py:142`, `update_gms_chrome.py` | KEEP_ADB_BOOTSTRAP |
| G04 | `uiautomator dump` (diag/find/observador) | `diag_controls.py:39`, `find_button_devices.py:47` | KEEP_ADB_BOOTSTRAP |
| G05 | `screencap` diagnostico | `diag_controls.py:45`, `observador.py:138` | KEEP_ADB_BOOTSTRAP |
| G06 | monkey/`input swipe` (QA) | `find_button_devices.py:60/71` | KEEP_ADB_BOOTSTRAP |
| G07 | `pm list packages` preflight | `preflight_devices.py:99` | KEEP_ADB_BOOTSTRAP |
| G08 | WiFi/airplane toggle | nao usado no main | DO_NOT_PORT |
| G09 | reboot (DPN AtomOS API) | `dpn/atomos_core/client.py:288` | DO_NOT_PORT |
| G10 | shell generico | nao existe como op; usos especificos acima | DO_NOT_PORT |
| G12 | `u2.connect`/`d.info` | `main_sln_v2.py:384` | KEEP_ADB_BOOTSTRAP |
| G13 | DPN network/reboot API | `dpn/atomos_core/client.py` | DO_NOT_PORT |

Buscados no `main_sln_v2.py` e **nao encontrados**: `getprop`, `wm size`, `wm density`,
`settings get/put`, `svc wifi`, `airplane`, `reboot`, `input text`, `keyevent`, `app_clear`,
`click_exists`, `long_click`, `cv2.matchTemplate`, `crop`. `.exists(timeout)` existe.
Nao ha `EXEC_SHELL` no Device Agent.

## 3. Parity matrix (resumo)

| Status | Primitivas | Consequencia |
|---|---|---|
| COVERED | TAP, SWIPE, BACK, HOME, START_APP, display, battery/uptime/IDs, IP | Acoes funcionam, mas dependem de coordenadas/alvos externos. |
| PARTIAL | foreground package+activity, doze/keyguard, Play Store install, activity/action, pacote instalado, ANR | Falta activity/component, keyguard, alvo semantico. |
| MISSING | UI tree, selectors, XPath, texto/estado, screenshot, CV/IA frame, logcat, pid/date, stop/force-stop, clear, POWER, artefatos | Bloqueiam reproducao sem ADB. |
| KEEP_ADB_BOOTSTRAP | install/update/provision, adb reverse, descoberta/connect, sideload, pm list, QA diagnostics | Permanecem fora do hot path. |
| DO_NOT_PORT | shell generico, wm/settings, WiFi/airplane, reboot, DPN routing | Seguranca/arquitetura; nao expor remotamente. |

## 4. Hot path atual (derivado do codigo)

| # | Etapa | Primitivas | Cobertura |
|---|---|---|---|
| 1 | Gate DPN/QA, `is_doze` | dumpsys power/window; POWER | 0,5 |
| 2 | Consentimento CAS | dump + TAP | 0,5 |
| 3 | Relancar app | app_start | 1,0 |
| 4 | Reward button: saldo/scroll/tap | dump + swipe_ext + TAP | 0,5 |
| 5 | Esperar sair da MainActivity | app_current activity | 0,5 |
| 6 | Banner em recarga/retry | dump_signature + app_start + swipe + TAP | 0,5 |
| 7 | Fast Close tick (default 300ms) | dump_hierarchy + parse | 0,0 |
| 8 | Fechar X/reveal/store BACK | dump + TAP + BACK (+HOME/STOP no escape) | 0,5 |
| 9 | FORMAT/eventos AppLovin | logcat/pidof/date | 0,0 |
| 10 | Classificacao IA/barra | screenshot/OpenCV/HTTP | 0,0 |
| 11 | Fechamento geral/legacy | selectors/xpath/bounds + TAP | 0,0 |
| 12 | Play Store (install/overlay) | dump + selectors + TAP + BACK | 0,5 |
| 13 | Reset ID (Google Ads Privacy) | am start + text selector + TAP + HOME | 0,5 |
| 14 | Reset/clear | force-stop + pm clear | 0,0 |
| 15 | Relancar apos reset | app_start | 1,0 |
| 16 | Limpeza periodica (30 ciclos) | app_stop + force-stop | 0,0 |

Sequencia sem invencao: vem de `processar_ciclo_anuncio`, `_fc_pre_anuncio`,
`fast_close_watcher_applovin`, `processar_applovin_fast`, `reset_identity_and_app_state` e do loop
principal (`main_sln_v2.py:4910-5065`).

## 5. Missing blockers e call graph

### 5.1 GET_UI_TREE (D01/D02/D07) - BLOCKER

```
_dump_hierarchy_medido
  <- get_hierarchy_data
       <- processar_home, _fc_pre_anuncio, aguardar_x_recompensa, aguardar_barra_progresso,
          clicar_coordenada_por_seletor, eh_anuncio_applovin, fechar_anuncio_*,
          tratar_play_store, tratar_consentimento_cas, tocar_para_revelar_x,
          sair_de_playable_travado, _fc_log_reward
  <- fast_close_watcher_applovin (1 dump/tick)
  <- dump_signature, _fc_salvar_artefato, _fc_recovery_observe, salvar_error_screenshot
```
Sem isso, todo o fast close, deteccao de X/reward/countdown, consentimento, Play Store e artefatos
param. Viabilidade: `AccessibilityService.getRootInActiveWindow()` com `canRetrieveWindowContent=true`
(hoje false). Nao e identico ao dump uiautomator, mas fornece class/text/desc/resource-id/bounds/
clickable. WebViews/anuncios podem limitar a arvore; necessario medir cobertura.

### 5.2 CAPTURE_SCREEN (C01/C03/C04/F05) - BLOCKER

```
screenshot_bytes
  <- ia_detect_x, ia_classify_type, _fc_classificar_async
  <- _fc_salvar_artefato, salvar_error_screenshot
tem_barra_progresso_azul -> d.screenshot (OpenCV)
```
Sem screenshot: IA de X/tipo, fallback da barra azul e artefatos de erro nao funcionam.
Viabilidade: `AccessibilityService.takeScreenshot()` (API 30; config `canTakeScreenshot`), sem root,
com rate limit; alternativa MediaProjection exige consentimento (pior para operacao).

### 5.3 FIND_ELEMENT/TAP_ELEMENT (D02/D03/D04) - BLOCKER

```
clicar_coordenada_por_seletor <- processar_home, abrir_anuncio_banner
clicar_por_atributo <- fechar_anuncio_*, tratar_play_store
tentar_clicar_botao_vazio/textview/xpath <- fechar_anuncio_*
d(resourceId/text/...).exists/click <- consent, ANR, reset, Play Store, reward
```
TAP cobre o ato fisico; falta o alvo semantico e bounds. Proposta: filtros tipados sobre a arvore;
nao portar XPath arbitrario.

### 5.4 Foreground package+activity (D05) - BLOCKER

`cur_app()`/`d.app_current()` alimentam 15+ funcoes; o bot compara `.MainActivity`, `vending`,
`finsky`, `HsdpAlias`, browser e launcher. O Agent entrega apenas `foreground_package`.
Viabilidade: eventos `TYPE_WINDOW_STATE_CHANGED` (className/package) e/ou UsageStats; expor
`package` + `activity/component`.

### 5.5 STOP_APP/force-stop (B02/B03) e CLEAR_APP_DATA (B04) - BLOCKERS privilegiados

Usados em escape de playable, falha de fast close, reset e limpeza periodica. Um app comum **nao**
executa `am force-stop` nem `pm clear` em outro pacote. Opcoes:
- manter ADB no hot path de reset/clear;
- provisionar o Device Agent como **device owner** e usar `DevicePolicyManager.clearApplicationUserData`
  (limpa e mata o app; nao e exatamente force-stop sem clear);
- root (rejeitado para o baseline).
Sem uma dessas, a paridade de lifecycle/reset e impossivel no modo normal.

### 5.6 Eventos/logcat (F01/F03) - IMPORTANT (nao blocker do fechamento)

`adb logcat -d --pid` e `pidof/date` dao FORMAT e timestamps de CLICKED/REWARD_EARNED/DISMISSED.
App comum nao le logcat de outro pacote (`READ_LOGS` e signature/privileged/development). O codigo
tem fallback visual, mas perde a autoridade de formato/reward. Alternativa: manter ADB para logcat
ou conceder READ_LOGS no bootstrap (validar por Android/ROM) e expor `GET_EVENTS` tipado.

### 5.7 POWER/keyguard (A06/E01) - OPTIONAL

`is_doze` usa dumpsys power/window e tenta POWER. `screen_on` ja existe no GET_STATUS; faltam
`keyguard_locked`/`dreaming`. Acordar a tela por app comum e limitado; pode permanecer ADB/optional.

## 6. Typed ops propostas (nao implementar nesta fase)

| Op | Params | Response | Capability/API | Root? | Fallback | Risco |
|---|---|---|---|---|---|---|
| GET_UI_TREE | max_depth, include_invisible | package, activity, nodes[{class,text,desc,resource_id,bounds,clickable,enabled,scrollable,password}] | Accessibility `canRetrieveWindowContent` | nao | ADB uiautomator dump | PII/tela; arvore incompleta em WebView |
| CAPTURE_SCREEN | format, quality, max_dim | png/jpeg base64/chunked, width/height | `takeScreenshot` API30 + `canTakeScreenshot` | nao | ADB screencap | conteudo sensivel; rate limit |
| GET_FOREGROUND_APP (ext) | - | package, activity/component, window_id | Accessibility events/UsageStats | nao | ADB dumpsys activity | baixo |
| FIND_ELEMENT | by text/id/desc/class, match, visible, index | node_id/bounds/center, snapshot_id | GET_UI_TREE | nao | controller filtra tree | alvo stale; sem XPath |
| TAP_ELEMENT | snapshot_id, node_id ou bounds | success/expired | TAP + tree | nao | TAP por coordenada | clique errado/stale |
| STOP_APP | package, mode=best_effort | stopped/best_effort/failed | ActivityManager killBackground (parcial); device-owner/ADB p/ full | parcial | ADB am force-stop | app em foreground nao morre; semantica fraca |
| CLEAR_APP_DATA | package | success/failure | DevicePolicyManager device owner (API 24+) | nao (device owner) | ADB pm clear | destrutivo; exige provisionamento privilegiado |
| OPEN_APP_ACTIVITY | action/component/uri allowlist | started | PackageManager/Intent | nao | ADB am start | intent injection; allowlist obrigatoria |
| GET_EVENTS | tags/pid, since_cursor, limit | eventos tipados | READ_LOGS (signature/dev) | nao (se concedido) | ADB logcat; visual fallback | privacidade/volume; ROM pode bloquear |
| GET_STATUS (ext) | - | app_pid, device_time_ms, keyguard_locked, dreaming, accessibility | APIs do app | nao | ADB pidof/date/dumpsys | baixo |
| WAKE_SCREEN | - | success/limited | full-screen intent/device admin | nao em geral | ADB keyevent POWER | baixo/limitado |
| GET_APP_INFO | package | installed, version, label | PackageManager | nao | ADB pm list | enumeracao; opcional |

Nao propor `EXEC_SHELL` generico, `SCREENSHOT` bruto sem limite, OCR ou XPath remoto.

## 7. O que continua no ADB

- instalacao/atualizacao do APK, pairing/provisioning (debug), `adb reverse` do Agent/DPN;
- descoberta/conexao de devices (`adb devices`, scan TCP, `adb connect`);
- sideload GMS/Chrome e updates por APK; uninstall;
- force-stop/pm clear enquanto nao houver device-owner (reset/clear do bot);
- logcat/eventos se READ_LOGS nao for concedido;
- reboot e toggles de rede/WiFi/airplane (nao usados no main; DPN tem API propria);
- diagnostico/QA (`observador`, `find_button_devices`, `diag_controls`, preflight, screenshots);
- recovery/reconexao e supervisao host (watchdog/psutil).

## 8. Prioridade de implementacao

1. **P0** GET_UI_TREE read-only + extensao de foreground/activity + GET_STATUS keyguard/pid/time.
2. **P0** CAPTURE_SCREEN (Accessibility) e validar rate limit/qualidade vs uiautomator2.
3. **P1** FIND_ELEMENT/TAP_ELEMENT tipados (ou filtro de tree no controller) e modo sombra do
   fast close comparando decisoes ADB vs Agent, sem agir.
4. **P1** decidir lifecycle: device-owner (clearApplicationUserData) ou ADB sidecar para stop/clear.
5. **P2** GET_EVENTS/logcat (validar READ_LOGS) com fallback visual; WAKE_SCREEN opcional.
6. **P3** GET_APP_INFO/OPEN_APP_ACTIVITY allowlist; bootstrap/docs.

## 9. Score de paridade

Metodologia:

- **Primitivas distintas**: 31 linhas operacionais (hot_path=true, exclui bootstrap/diagnostico/
  DO_NOT_PORT). C=1, P=0,5, M=0. Resultado: 9 C, 7 P, 15 M => (9+3,5)/31 = **40,3%**.
- **Hot path**: 16 etapas da secao 4, peso 1/0,5/0 conforme cobertura. Soma 6,0/16 = **37,5%**.
- **Chamadas estimadas**: modelo por ciclo nominal (nao medido): 1 anuncio AppLovin com 15s de
  fast close a 300ms (~50 dumps), ~8s de watch, dumps gerais 15, TAP 5, screenshot 2, app_current
  20, logcat 4, selectors 5, BACK 2, app_start 1, stop/force/clear 0,68, etc. Peso total 123,01;
  cobertura ponderada **21,7%**. O dump de UI domina (65/123) e esta 0%.
- Sensibilidade: se o anuncio fast close durar menos ticks, a fracao de chamadas cobertas sobe;
  ainda assim UI tree/screenshot faltam. Os numeros sao modelo explicito, nao telemetria.

Bootstrap/adb puro nao entra no score de paridade do hot path; ~20 linhas KEEP/DO_NOT_PORT foram
inventariadas separadamente no JSON.

## 10. Proxima fase recomendada

**E3.5 - Perception Parity (sem integracao no main)**: implementar somente GET_UI_TREE,
CAPTURE_SCREEN, foreground activity e extensoes GET_STATUS no Device Agent (debug), e rodar modo
sombra comparando, por 1 device, as decisoes do fast close atual (ADB) contra a arvore/screenshot
do Agent. Medir cobertura de nodes, latencia e divergencias de X/reward/countdown. Nao integrar no
`main_sln_v2` e nao portar STOP/CLEAR ate a decisao de device-owner/ADB sidecar.

**Resposta final:** o que falta para reproduzir o bot sem ADB no hot path e a camada de percepcao
(UI tree, screenshot, foreground/activity, find/click semantico), a observacao de eventos, e uma
estrategia privilegiada para force-stop/clear; sem isso, o Agent executa gestos mas nao fecha
anuncios nem reseta o app.
