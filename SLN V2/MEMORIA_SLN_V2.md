# SLN V2 — MEMÓRIA DE CONTEXTO / HANDOFF DE AGENTE

> Documento de "passagem de bastão" para continuar o desenvolvimento da automação ADB de fechamento de anúncios de jogos.

> ⚠️ **LEIA PRIMEIRO (autoridade atual):** `AGENTS.md` (invariantes) → `docs/CURRENT_STATE.md` (estado + fila)
> → `docs/PROJECT_CONTEXT.md` (histórico/porquê) → `docs/SCALING_140.md` (escala + riscos).
> Este documento permanece como memória detalhada de sessão; **em caso de divergência, os docs acima vencem.**

> Estado validado até: infra/medição corrigida (Fases 0–13), farm atualizada (GMS+Chrome via sideload) e benchmark 10 devices lançado — 2026-09-29.
> Última mudança: consentimento CAS sempre "YES, I AGREE" a cada ciclo; scripts `sideload_gms_chrome.py`, `benchmark_run.py`, `preflight_devices.py`.

---

## 1. O QUE É O PROJETO

Automação **Python 3.14 + ADB + uiautomator2** que roda em **aparelhos Android reais** (via `adb` TCP/IP) e fecha **anúncios de jogos** no fluxo de "assistir anúncio por recompensa". O app alvo da automação é `com.flixdigital.dramaclassic` (Drama Classic), mas a lógica é genérica de comportamento de anúncios.

Arquitetura central: **Fast Close Watcher** — 1 dump uiautomator por tick (~300ms), parse local do mesmo XML, ação imediata (nunca várias consultas uiautomator por tick), `d.click` com coordenadas derivadas de bounds (nunca `UiObject.click` no caminho crítico).

## 2. ARQUIVOS PRINCIPAIS

| Arquivo | Papel |
|---|---|
| `main_sln_v2.py` | Bot completo (~4260 linhas). Fluxos: fast_close AppLovin, interstitial, rewarded, Store/BACK, ENDCARD, recovery, reset. |
| `config_sln_v2.py` | DEFAULTS + `load_config()` (merge DEFAULTS + config_sln_v2.json). |
| `config_sln_v2.json` | Config efetiva (sobrescreve defaults). |
| `aiMain_sln_v2.py` | Servidor FastAPI de IA (YOLO). Endpoints `/detect`, `/classify`, `/health`. Porta 8000. |
| `observador.py` | Captura de sessão (XML+screenshot) independente. |
| `preflight_devices.py` | Check read-only dos devices antes de benchmark (online/awake/battery/app/IA). |
| `sideload_gms_chrome.py` | Atualiza GMS/Chrome via `adb install-multiple` (sem Play Store), casando variante por Android SDK. |
| `benchmark_run.py` | Lança N bots (overlay benchmark + guards) + `benchmark_host.py` em paralelo. |
| `update_gms_chrome.py` | Update via Play Store (abre página + toca "Atualizar"); bloqueado por auth na farm. |
| `errors/` | Artefatos PNG+XML de diagnóstico/falha. |
| `logs/` | Logs de rodadas. |

Uso: `python main_sln_v2.py <device_ip:5555> <modo> <limite_ads>` (limite 0 = ilimitado).

## 3. AMBIENTE / TESTE

- **Aparelho de teste:** `192.168.1.124:5555` (samsung SM-G975F). O bot usa `sys.argv[1]`.
- **Servidor IA:** `python aiMain_sln_v2.py` → `http://localhost:8000`. Modelo: `C:\AndroidProjects\APPS\runs\detect\train3\weights\best.pt` (existe, carregado). Health: `{"status":"ok","model_loaded":true}`.
  - Como subir desacoplado (sobrevive ao shell): `Start-Process cmd -ArgumentList '/c','cd /d "C:\AndroidProjects\APPS\SLN V2" && python aiMain_sln_v2.py > out.log 2> err.log' -WindowStyle Hidden`
  - Encerrar: `Stop-Process -Id <pid> -Force` (achou a porta: `netstat -ano | Select-String ":8000.*LISTENING"`).
- Python: 3.14. Deps instaladas (uvicorn, fastapi, ultralytics, opencv, numpy, requests, uiautomator2).
- Rodada de teste: `python main_sln_v2.py 192.168.1.124:5555 0 <N>` com saída redirecionada para `logs/*.log`. Geralmente via `cmd /c` + redirecionamento para não travar o shell.

## 3b. FARM — GMS/CHROME E VARIANTES (2026-09-29)

- **116 devices** porta 5555: **57 S8/S8+ (Android 9, sdk28)** e **59 S10/S10+ (Android 12, sdk31)**. Todos arm64.
- Play Store NÃO serve na farm (12 sem conta Google; vários com sessão expirada). Atualização feita por **sideload** (`sideload_gms_chrome.py`, `adb install-multiple`, sem conta).
- **Variante por Android (validada em 100% dos 116):**

  | Android | GMS (variante/minSdk) | Chrome (minSdk) |
  |---|---|---|
  | 9 (sdk28) | `100400` / minSdk28 | 138 / minSdk26 |
  | 12 (sdk31) | `190400` / minSdk31 | 154 / minSdk29 |

  GMS 190400 = 12 splits (inclui Dynamite ads); 100400 = 4 splits. Chrome 139+ exige Android 10.
- Fontes APK: GMS 100400 ← `.101`; GMS 190400 ← `.124`; Chrome 138 ← `.101`; Chrome 154 ← `.124`.
- Resultado: **114/116 OK**; `.53` e `.21` reiniciaram durante o update → USB p/ re-habilitar `adb tcpip 5555`.
- Consentimento CAS: clicar "YES, I AGREE" **sempre** (sem filtro de package; a cada ciclo).

## 4. FLUXOS VALIDADOS (PASS — NÃO MEXER SEM REGRESSÃO PROVADA)

### FORMAT (prioridade: app_event > cas_event > classificador > unknown)
- Lido por `adb logcat -d --pid=<app>` numa thread própria (~700–1800ms; NUNCA bloqueia o tick).
- Regex: `AdHelperLog: FORMAT=X EVENT=SHOWED` (app_event) e `CAS.AI: X Ad Impression ... CreativeId: Y UnitId: Z` (cas_event). `EVENT=LOADED` NÃO define formato.
- **Fix importante:** `adb logcat -d -s` era LENTO (~12s) e estourava `timeout=4` → trocado por `--pid` (~600ms).
- **Fix anti-vazamento:** evento SHOWED do anúncio ANTERIOR dentro da janela de 30s pode contaminar o atual → piso device-clock `_fc_fmt_session_perf` + `min_ts` no parser.

### Pílula "Google Play" (`_fc_detectar_pilula`)
- TextView com texto EXATO "Google Play", package do app, classe TextView, região superior; sobe ancestrais pequenos (<=40% largura, <=12% altura) → container.
- **SKIP:** clique no LADO ESQUERDO da pílula (ponto entre `pill_left` e `text_left`, ex.: x≈62, y≈172). NUNCA `d(text="Google Play").click()`, NUNCA centro do texto. O texto é âncora.
- Estabilidade pré-SKIP: pílula em 2 dumps consecutivos (`applovin_skip_min_pill_age_s≈0.25s`). Sem sleep extra.

### Interstitial
- FORMAT=INTERSTITIAL confirmado por evento → **sem reward gate**. Pílula visível + gate aberto = SKIP imediato.
- Sem pílula até `applovin_interstitial_control_wait_s=20s` → `REVEAL_TOP` (faixa superior, `top_reveal_y_ratio=0.075`). NUNCA 0.45h.

### Rewarded com countdown (gate = CONCLUSÃO OBSERVADA, nunca tempo)
- Detector de countdown: região inferior (>=0.80h), nó pequeno (<=12% lado), valor <=120, sequência descendente, 2 transições consecutivas, fim quando some com último valor <=2.
- `applovin_reward_completion_required=True`: gate abre SÓ com `REWARD_COMPLETED` (countdown terminou + pílula). Tempo NÃO abre gate.
- `REWARD_EARNED` chega ~30–60ms APÓS o bot ver a MainActivity → é **confirmação póstuma, NÃO gate**.

### Store/BACK (após SKIP)
- Loja até 3s após SKIP = esperada (`EXPECTED_STORE_AFTER_SKIP`). BACK orientado por estado: back inicial após 1.5s, settle 0.8s, retry 1.0s, max 3. Verifica dump antes de cada BACK. Endcard detectado = não manda outro BACK.

### ENDCARD (cascata)
1. `_fc_find_endcard_close_structural` (X anônimo: View ~quadrada topo-direito [958,135][1039,219] + Image [984,164][1013,191], centro (998,177) em 1080x2280)
2. XML `an["best"]`
3. `achar_botao_fechar_no_dump`
4. listas antigas (SELECTORS/DESCRIPTIONS/TEXTS)
5. XPath sobre a source
6. **YOLO (lazy)** só após `applovin_endcard_ai_grace_s=4.0` com tudo local falhando.

### IA
- `/detect` em screenshot real: **X verdadeiro conf=0.75** (passa filtro); **pílula detectada como X conf=0.58** mas **rejeitada por `x_region`** (exige x>=0.70w, y<=top, conf>=0.25). Esse falso positivo é esperado e protege o fluxo. **NÃO relaxar filtros.**
- IA é fallback lazy, NUNCA no caminho rápido, NUNCA no CTA do vídeo.

## 5. RECOVERY CLOSE CASCADE (NOVO, CONFIRMADO AO VIVO)

Executa SÓ quando o fluxo normal falhou/timeout, ANTES do escape HOME+force-stop. **NUNCA declara `reward_completed`** — só tenta encerrar a tela normalmente.

```
1 structural → 2 resource_id → 3 content_desc → 4 text → 5 xpath
→ 6 anonymous_top_right (View/Image pequeno anônimo, topo-direito, onscreen)
→ 7 AI/YOLO (último recurso; filtros x_region/conf)
```

- **CONFIRMADO AO VIVO** (creative `6863651`, rewarded sem countdown): TIMEOUT aos 180s → recovery clicou `anonymous_top_right` em (993,252) → voltou a MAIN → **delta +10** → `REWARD_EARNED` pós-fechamento. → `RECOVERY_CLOSE_CONFIRMED_LIVE`, `reward_preserved=True`. Sem escape.
- Configs: `recovery_close_enabled=true`, `recovery_close_ai_last_enabled=true`, `recovery_close_observe_s=5`, `recovery_close_max_attempts=1`.
- **NÃO promover o close do WebView para gate precoce** — decisão separada, para rodada futura, com nova evidência.

## 6. REWARDED SEM COUNTDOWN — INVESTIGAÇÃO (GAP ABERTO)

Duas classes estruturais distintas observadas (nunca usar CreativeId como assinatura):

- **Playable `MW_PLFRAME`** (Cocos2d/canvas): `mv_close_btn` inicialmente OFFSCREEN `[0,2280][1080,2280]`. NUNCA transicionou para onscreen em 3+ ocorrências até 180s. Sem sinal de conclusão.
- **WebView/`adFrame`** (HTML): close anônimo no topo-direito visível o tempo todo, invisível às camadas XML locais mas detectável por IA (988,253) e por `anonymous_top_right` (993,252). É a classe que o recovery fecha.

Mecânica atual: `rewarded_no_countdown_diag_timeout_s=180` estende a OBSERVAÇÃO (nunca libera SKIP por tempo). Se countdown/pílula ainda não apareceram → recovery.

**Artefatos de diagnóstico:** `_fc_diag_rewarded_sem_countdown` loga `[FASTCLOSE][DIAG_REWARD]`, `[FASTCLOSE][PLAYABLE_DIAG]`, monitora transição `mv_close_btn`, e salva PNG+XML nos tempos T+20..T+120.

**NÃO implementar fallback temporal** (`elapsed>=N → reward_completed=True`).

## 7. RESET DE ID + LIMPEZA DO APP (ÚLTIMA MUDANÇA)

Nova função `reset_identity_and_app_state()` com ORDEM OBRIGATÓRIA:

```
RESET ID (reset_id, valida conclusão)
→ [RESET][APP] force_stop
→ pm clear <package> (retry 3x, valida "Success" + exit_code 0)
→ [RESET][APP] ready_for_launch
→ somente o controlador abre o app
```

Regras:
- **RESET SEM CLEAR = CICLO INCOMPLETO.**
- Se reset falhar: `[RESET][ERROR] stage=id_reset`, não abre o app.
- Se `pm clear` falhar: `[RESET][ERROR] stage=app_data_clear`, retry 3x, não abre o app.
- Métricas logadas: `reset_id_ms`, `clear_data_ms`, `total_reset_cycle_ms`.

### 7.1 Normalização (2026-09-25) — o que mudou aqui

- **`_clear_app_data_verified(contexto, max_tent)`** é a rotina ÚNICA de limpeza: force-stop +
  `pm clear` validado (`"Success"` **e** `exit_code == 0`) + retry controlado + duração medida.
  Usada pelo reset **e** pelo caminho avulso `clear_data_pct` (que antes chamava `d.app_clear()`
  sem validação).
- **Bug corrigido:** `exit_ok = getattr(r, ...)` era avaliado fora do `try`; se `d.shell()`
  lançasse exceção, `r` não existia → `NameError`. Agora `r = None` antes do `try`.
- **`_did_clear_this_round`:** se o reset já limpou na rodada, `clear_data_pct` **não** repete a limpeza.
- **`_reset_pending` / `_clear_pending` + `_pending_retry_step()`:** falha vira pendência
  repetida com **backoff por device** (15s→30s→…→300s). `skip_launch` foi **removido**. Como cada
  bot é um processo, a pendência de um device não bloqueia os outros.
- **`reset_id()`** sem ação real de reset agora é **falha identificável**
  (`[RESET][ERROR] stage=id_reset reason=reset_action_not_found`) + artefato de diagnóstico —
  nunca mais `success` silencioso.
- `_fc_coins_last = None` após qualquer clear (reset ou avulso).
- **Validado ao vivo em `192.168.1.124`:** `pm clear` = `Success` em **844 ms** (1 tentativa),
  `total_reset_cycle_ms=9626`, `_did_clear_this_round=True`, app relançado pelo controlador superior.
- `pm clear` devolve o app ao estado de primeira execução (consents, telas iniciais, permissões, init CAS podem reaparecer). `_fc_coins_last` é zerado (saldo antigo inválido).
- **Dica técnica:** `d.shell("pm clear ...")` retorna objeto `ShellResponse` (`.output`, `.exit_code`), NÃO str — validar via `r.output.startswith("Success")`.

## 8. CONFIG EFETIVA ATUAL (chaves relevantes)

| Chave | Valor |
|---|---|
| fast_close_enabled / tick_ms / log_every_tick | true / 300 / true |
| applovin_fast_close_timeout_seconds | 90 |
| applovin_skip_to_endcard_enabled | true |
| applovin_reward_completion_required | true |
| applovin_reward_safe_mode / min_watch_seconds | true / 60 |
| applovin_interstitial_control_wait_s | 20.0 |
| applovin_skip_min_pill_age_s | 0.25 |
| applovin_skip_max_per_ad | 2 |
| top_reveal_y_ratio | 0.075 |
| applovin_store_back_settle_s / retry_interval_s / max_backs | 0.8 / 1.0 / 3 |
| applovin_endcard_ai_enabled / ai_grace_s | true / 4.0 |
| rewarded_no_countdown_diag_timeout_s | 180 |
| playable_diag_click_enabled | false |
| recovery_close_enabled / ai_last_enabled / observe_s / max_attempts | true / true / 5.0 / 1 |
| use_legacy_applovin | false |
| use_ia / ia_url | true / http://localhost:8000 |

Nunca alterar apenas porque JSON difere do default — o JSON é o efetivo.

> Perfil de benchmark (2026-09-25): `config_benchmark_sln_v2.json`, aplicado com
> `SLN_CONFIG_FILE=config_benchmark_sln_v2.json` — não sobrescreve o JSON de dev.
> `ia_url` continua `http://localhost:8000`, mas a medição recomenda `http://127.0.0.1:8000`
> (economiza ~2 s por chamada de IA). **Decisão pendente do dono do projeto.**

## 9. BUGS CORRIGIDOS (não recriar)

1. `adb logcat -d -s` lento (12.5s) > timeout 4s → sempre `TimeoutExpired`. Fix: `--pid`.
2. `from datetime import datetime` sem `timedelta` → AttributeError no FORMAT.
3. SHOWED do anúncio anterior vazando na janela de 30s → piso device-clock.
4. `_fc_eventos_pid` associava REWARD_EARNED/CLICKED de anúncios antigos → filtro por piso.
5. `_fc_tem_cta` com `parent=None` → AttributeError na camada anônima do recovery.
6. `_fc_find_close_btn` não achava close OFFSCREEN degenerado `[0,2280][1080,2280]` (bounds descartados) → parse manual + flag `onscreen`.
7. `d.shell("pm clear")` retorna `ShellResponse` (objeto) → parser corrigido.
8. `exit_ok = getattr(r, "exit_code", ...)` avaliado **fora** do `try`: se `d.shell()` lançasse
   exceção, `r` não existia → `NameError` em `_clear_app_data_verified`. Fix: `r = None` antes e
   `exit_ok` só inspeciona `r` quando há objeto.

## 10. RESULTADOS ACUMULADOS

- FORMAT: maioria `app_event`, lookup ~700–1800ms, 0 unknown nas rodadas recentes.
- Interstitial: fecham ~11–40s (pílula espontânea ou reveal aos 20s).
- Rewarded com countdown: fecham ~35–70s, recompensa +10 preservada em todas.
- Endcard: 100% `structural` (998,177).
- Store/BACK: 1–2 backs, sem loop.
- Recovery: 1 confirmação ao vivo com +10 (6863651).
- Aparelho .124: saldo de moedas pode ter sido zerado pelo último teste de `pm clear` — tratar como 0/primeira execução.

### Rodada de 2026-09-25 (baseline + smoke honesto)

Baseline 1 device (`192.168.1.100`, SM-G955U, **1920x1080 landscape**), `limite=2`, config de dev:

- `dump_ms_avg` 130–209 / **p95 155–268** / max 314–319 ms (~41 KB por dump).
- `format_lookup_ms_avg` 330 ms (3 subprocessos ADB cada; contagem conferida).
- `ai_latency_avg` **2096 ms** visto pelo bot com `ia_url=localhost` (no host, `127.0.0.1`: 124–148 ms).
- `errors=0`, `reconnect_count=0`. Cadência do `[PERF]` exata (60 s).

Smoke real em `.100`: **0 anúncios fechados**. Nenhum `X_VISIBLE`/`ENDCARD`/`SKIP`/`REVEAL` em
1131 linhas — o X não está no XML local nesse device. Os 2 anúncios (1 interstitial visual, 1
rewarded) viraram TIMEOUT (90,2 s e 180,1 s); o recovery clicou `anonymous_top_right` em
**(1712,70)** e o estado ficou `applovin` → escape. `[STAT] ads=0 ... escapes=1 falhas=1`.

> Isto **não** é regressão da rodada: é o gap conhecido (sem pílula/countdown) somado a um
> ambiente onde o app não emite evento de formato. Os fluxos que o smoke conseguiu validar ao
> vivo foram: classificador visual lazy, `[PERF]`, recovery em landscape e a invariante
> "sem fallback temporal" (`[FASTCLOSE][REWARD_COMPLETION] not_detected ... sem SKIP`).

RESET forçado em `.124`: `pm clear` `Success` em 844 ms, `total_reset_cycle_ms=9626`,
`_did_clear_this_round=True`, `_fc_coins_last=None`, relaunch do controlador → `.MainActivity`.

## 11. REGRAS GERAIS PARA QUALQUER CONTINUAÇÃO

- **NÃO** refatorar arquitetura.
- **NÃO** mexer em gates/thresholds validados sem evidência de falha real.
- **NÃO** trocar countdown gate por tempo. **NÃO** usar CreativeId como assinatura.
- **NÃO** apagar código legacy ainda (`use_legacy_applovin=false` mas código existe).
- **NÃO** mover YOLO para frente da cascata.
- Toda mudança: preservar evidência (XML/PNG), corrigir só o problema, rodar regressão dos fluxos relacionados, testar no aparelho.
- Baterias curtas: 2–6 anúncios válidos ou 30 min (nunca soak sem pedido explícito). Encerrar o servidor IA ao fim das rodadas de diagnóstico.

## 11.1 NOVO (2026-09-25) — PREPARAÇÃO PARA ESCALA

- **Classificador visual LAZY:** `processar_applovin_fast()` inicia **só** o FORMAT. O `/classify`
  é disparado **dentro do watcher**, só depois de o FORMAT esgotar `applovin_format_max_tries`.
  Precedência inalterada; reward gate intocado. Métricas: `visual_classifier_invoked`,
  `visual_classifier_reason`, `visual_classifier_ms` (saem no `[FASTCLOSE][AD_SUMMARY_FORMAT]`).
- **Telemetria `[PERF]` a cada ~60s** (nada por tick): dumps (count/bytes/avg/p95/max), format
  lookup, subprocessos ADB, screenshots, chamadas de IA, reset/clear, reconexões, erros.
  Intervalo ajustável por `SLN_PERF_INTERVAL_S`.
- **Startup semaphore** (`interface_sln_v2.py`) controla **só o arranque**; `process.wait()` roda
  fora dele. Capacidade por `SLN_START_CONCURRENCY` (default 8) + stagger com jitter.
- **Perfil de benchmark separado:** `config_benchmark_sln_v2.json` aplicado via
  `SLN_CONFIG_FILE=config_benchmark_sln_v2.json`. O `config_sln_v2.json` de dev **não** muda.
- **IA central:** inferência do `/detect` fora do event loop (`asyncio.to_thread` + lock único),
  `/health` responsivo com `queue_wait_ms`/`inference_ms`/`total_ms`, e `MODEL_PATH` aceitando
  `SLN_IA_MODEL_PATH` > `ia_model_path` (JSON) > default. Sem batching.
- **NÃO implementado de propósito:** persistent logcat, adaptive tick, centralização dos bots,
  AI batching, mudança do reward gate. Dependem do benchmark 5/10.

### Achados que afetam decisões futuras

1. `dump_ms_p95 ≈ 265 ms` em device de farm (1920x1080) contra `tick=300 ms` → pouco folga.
2. **`ia_url=http://localhost:8000` custa ~2 s por chamada** (IPv6 do Windows antes do IPv4):
   ~2096 ms via `localhost` vs ~5 ms via `127.0.0.1`. Recomendação: usar `127.0.0.1`.
3. ~~O app não emite `AdHelperLog`/`CAS.AI` nos devices da farm~~ — **SUPERSEDED (2026-09-26):**
   o app **emite** (`CURRENT_STATE.md` §6.3/§7.2). Em devices com `logd` saudável + internet,
   `logcat --pid` mostra `AdHelperLog: FORMAT=X EVENT=SHOWED` e `CAS.AI ... Ad Impression`.
   Sem internet → `FAILED_TO_LOAD`; `logd` em `restarting` → `logcat read failure` (sem FORMAT).
4. Sem `format_source` de evento, o interstitial confirmado só pelo visual **não usa o reveal**
   (`inter_ok` exige app_event/cas_event) e espera o timeout. Comportamento pré-existente.
5. O recovery adapta a orientação: em landscape achou `anonymous_top_right` em **(1712,70)**.

## 12. COMO RELER O ESTADO ATUAL

0. Ler `AGENTS.md` + `docs/CURRENT_STATE.md` (as quatro fases de 2026-09-25 estão lá).
1. `python -m py_compile main_sln_v2.py` para checar sintaxe.
2. `python -c "import config_sln_v2 as C; cfg=C.load_config(); print(cfg.get('...'))"` para config efetiva.
3. Para testes offline do parser de formato: importar `main_sln_v2` com `sys.argv=['x','FAKE:5555','0']` e chamar `_fc_parse_formato(...)`.
4. `adb -s 192.168.1.124:5555 logcat -d --pid=$(adb ... pidof com.flixdigital.dramaclassic) -v threadtime` para eventos SDK (SHOWED/CLICKED/REWARD_EARNED/DISMISSED).
5. Config de benchmark: `SLN_CONFIG_OVERLAY=config_benchmark_sln_v2.json python -c "import config_sln_v2 as C; print(C.load_config())"` (overlay mesclado SOBRE config_sln_v2.json; NÃO usa SLN_CONFIG_FILE, que substitui a base).
6. Telemetria de uma rodada: `grep -E "\[PERF\]" logs/<device>.log` (resumo a cada 60s).
7. Diagnóstico de fechamento: `grep -E "visual_classifier|AD_SUMMARY_FORMAT|RECOVERY_CLOSE|FORMAT\] unknown" logs/<device>.log`.
8. Escala/riscos e checklist: `docs/SCALING_140.md`.
9. Baseline de capacidade + supervisão: `docs/CURRENT_STATE.md` §12 (watchdog/ownership) e §13 (C90);
   `docs/PROJECT_CONTEXT.md` §15. Baseline autoritativo = **C60 clean**; status `APPROACHING`;
   testes offline: `python tests/test_scale.py` e `python tests/test_ownership.py`.
10. QA realistic (agendamento por device): `docs/QA_REALISTIC_BEHAVIOR.md` e §15 abaixo;
   testes: `python tests/test_qa_behavior.py` e `python simulate_qa_behavior.py --devices 20 --cycles 100 --seed 123`.

## 13. OWNERSHIP/RESTART do watchdog (2026-09-30)

- C60 anterior: `C60_INVALID_FOR_CAPACITY` / `FUNCTIONAL_SIGNALS_VALID` — motivo
  `WATCHDOG_DUPLICATE_WORKER_BUG` (restart não matava o worker anterior → ~96 bots / ~227 logcat).
- Invariante: **um device = no máximo um bot**. Identificação EXATA por `sln_proc.py`
  (`basename argv == main_sln_v2.py` e `argv[i+1]==device`). NUNCA substring.
- `terminate_worker_verified` mata a ÁRVORE (bot + `adb logcat`/`cmd` filhos). O filho `adb logcat`
  NÃO morre sozinho com o pai no Windows — por isso a limpeza pela árvore é obrigatória.
- `watchdog.py` v2: registry/generation/instance, grace de startup e pós-restart, coalescing,
  `STALE_HEARTBEAT_GENERATION`. `watchdog_run.py` v2: restart TRANSACIONAL + lock por device +
  preflight (`DUPLICATE_WORKER_PREFLIGHT_FAIL`) + CSV de dup/orphan.
- Progresso: ticker de heartbeat avança `last_progress` em operações legítimas (inclusive backoff)
  e congela só quando `dump_hierarchy` trava (>30s). Publica `no_fill` sempre (telemetria).
- AI>4s: `ai_over_4s_count` por requisição (não máximo cumulativo).
- Testes: `tests/test_scale.py` 37/37 + `tests/test_ownership.py` 23/23.
- Validações: fault injection **1 device PASS**, **5 devices PASS**, **lifecycle 30 devices PASS**
  (dup=0/orph=0). **C60 limpo COMPLETO (30 min)**: bots 60/60, logcat 60/61, `dup=0`/`orph=0` sempre,
  watchdog reiniciou **1** device; CPU avg 51.1 / p95 80.1 / p99 88.8 / max 99.7, **1% >90% (15s)**;
  dump p95 168ms, 0 dumps >5s, reconnects/errors 0, close 75.1% / timeout 24.2%, reward_ok 194.
  Classificação: **`SINGLE_HOST_CAPACITY_APPROACHING`** (não iniciar 90 automaticamente).
- Ferramentas: `fault_injection.py`, `lifecycle_30.py`, `c60_clean.py`.

## 14. CAPACITY C90 (2026-10-01)

- C60 clean = **baseline autoritativo**: 60/60, dup=0/orph=0, watchdog restart 1, CPU avg 51.1 /
  p95 80.1 / p99 88.8, 0 dumps >5s, reconnect 0. C60 invalid anterior = `C60_INVALID_FOR_CAPACITY`,
  `FUNCTIONAL_SIGNALS_VALID`.
- **C90** (90 = 60+30 READY; mesmo perfil; `benchmarks/90_devices_20261001_022959.txt`):
  30 min completos, sem early stop. dup=0/orph=0 sempre, watchdog **0/0/0**, reconnect 0,
  0 dumps >5s, FORMAT unknown 0.26%, close 85.8% (efeito inventário), reward 170.
  CPU avg 58.4 / **p95 85.2 / p99 93.3** (>90% 3.2% = 45s; rajada máx 15s; arranque p95 93.3).
- **Gargalo dominante = inventário** (`no_fill_backoff` avg 68.4/90; 4.2 ads/device).
- Curva CPU p95 35.8→80.1→85.2; avg 26.6→51.1→58.4 (60→90 só +5pp; sem inflexão até 90).
- **Decisão: `SINGLE_HOST_CAPACITY_APPROACHING`.** MAX_OBSERVED=90; RECOMMENDED≈**60/host**;
  NÃO iniciar 120; sharding antes de 120. Ferramentas: `select90.py`, `c90_probe.py`.

## 15. QA REALISTIC BEHAVIOR (2026-10-01)

- Camada de **simulação/agendamento por device** (heterogeneidade de frota), SEPARADA da state machine
  do anúncio. Default OFF (`qa_realistic_behavior_enabled=false`); só o perfil QA Realistic liga.
- `qa_behavior.py`: `DeviceQABehaviorState` (estado por device `state/qa_devices/<safe>.json`, atômico),
  `QaConfig` (validada), `QABehaviorScheduler` (WAIT/RESET_DUE/RESET_ALLOWED/INSTALL_*/NORMAL_CYCLE),
  `QACoordinator`/`FileQACoordinator` (quota/concorrência/install), `build_scheduler()`.
- Delay 10–60s por device (sleep cancelável); reset individual 2–30 ciclos (novo intervalo após sucesso)
  via `reset_identity_and_app_state()` (reset+clear acoplados); quota reset 5–25%/janela + concorrência
  (`qa_max_concurrent_resets=2`) + jitter; install 5–10% com **cooldown móvel 24h** (nunca <24h; falha
  não marca sucesso); seed `sha256(global_seed|device_id)` (reproduzível).
- Validação: `tests/test_qa_behavior.py` (41), `simulate_qa_behavior.py` (20/50 devices, sem sync),
  smoke real .44/.57/.126 (reset+clear acoplados; dup=0), `find_button_devices.py` (97/115 com botão).
- Arquivos: `qa_behavior.py`, `config_qa_realistic_sln_v2.json`, `config_qa_smoke_sln_v2.json` (test-only),
  `simulate_qa_behavior.py`, `qa_smoke.py`, `find_button_devices.py`, `tests/test_qa_behavior.py`,
  `docs/QA_REALISTIC_BEHAVIOR.md`. Status: `QA_REALISTIC_IMPLEMENTATION_PASS`.

## 16. CICLO / AD SESSION (2026-10-02)

- **1 clique "Assistir anúncio" = 1 CICLO = 1 AD SESSION** (N stages: vídeo/playable/Store/endcard/
  tela intermediária). **Fechar um controle NÃO finaliza o ciclo.**
- `ad_session.py` (`CycleSession`): `begin` (sessão iniciou), `continue_stage` (fechou, não-MAIN → novo
  stage, mesmo ciclo), `complete` (só MAIN), `fail` (escape forçado ≠ conclusão), `classify_outcome`.
- Integração no loop: `begin` ao sair da MAIN; enquanto `_cycle.active`, **não** reabre/clicka de novo;
  `_cycle_after_ad` decide COMPLETED/CONTINUE/FAILED. **`qa.cycle_completed()` só em COMPLETED** → o
  intervalo de reset (2–30) conta **ciclos completos**, não stages/telas/X.
- Telemetria: `[QA][CYCLE_START]`, `[AD_SESSION][START|STAGE|STAGE_CLOSED|CONTINUE]`,
  `[QA][CYCLE_COMPLETED|CYCLE_FAILED]`. Testes: `tests/test_ad_session.py` (22). Total offline **123**.
- Smoke 1 device (.44): 3 CYCLE_START / 2 COMPLETED / 1 FAILED (LEFT_AD→escape NÃO completou); 0 CONTINUE.
  Confundidor: os 2 COMPLETED vieram de **fechamento manual do operador**. Status:
  `CYCLE_SESSION_MODELING_PASS`. Não altera reward/FORMAT/pill/Store/BACK/recovery.

## 17. SUB_AD_CLOSE (2026-10-02)

- `SUB_AD_CLOSE_SIGNATURE_CONFIRMED`: em sub-ad WebView o close é um **FrameLayout anônimo clicável**
  no topo-direito `[958,217][1028,287]` (norm ≈ [0.887,0.095,0.952,0.126]), **1 filho View de mesmos
  bounds**, contexto WebView/adFrame. `subad_close.py` (`detect_subad_close`/`authorize`) +
  `_fc_detectar_subad_close`. **DETECÇÃO != AUTORIZAÇÃO** (usa o gate; rewarded sem completion = observe).
- Motivo: não entrava no normal path; só o recovery `anonymous_top_right` (993,252 = centro real) aos
  ~90s; e disparava `REVEAL_TOP` que abria a Play Store. Telemetria `[FASTCLOSE][SUBAD_CLOSE]` (assinatura
  completa, throttled) e `[FASTCLOSE][SUBAD_CLOSE_CLICK]`.
- Validado ao vivo (1 device): interstitial sub-ad → detectado no XML → autorizado → clique →
  `closed=True source=subad_close elapsed=10.2s` → MAIN → CYCLE_COMPLETED (antes ~70–90s).
- Bug corrigido: após o clique, `phase="skipping_to_endcard"` sem `skip_at` → `TypeError float-None`;
  fix: setar `fl["skip_at"]=t_c` no clique do SUB_AD_CLOSE. Testes: `tests/test_subad_close.py` (27).
  Total offline **150**. Config `applovin_subad_close_enabled` (default True) + max/cooldown.
- Fora de escopo: compact ">|" (VISIBLE_NOT_IN_XML — reveal resolve) e variante ALRTB
  `resource-id=close_button` (diferente assinatura; capturar XML para futura rodada). Também corrigido
  o `SESSION_START_BUG` (`_cycle.begin` em `processar_applovin_fast` quando a sessão abre via retry de
  banner). Status: `SUBAD_CLOSE_FIX_PASS` (detecção+click+latência); **não** iniciar C90.

## 18. RESET TREE — 5/5 ao vivo (2026-10-02)

- `RESET_TREE_MODEL_VALIDATED_LIVE_5X`. Overlay TEST-ONLY `config_qa_reset_tree_sln_v2.json`
  (delay 2–5s, reset 2–5 CYCLE_COMPLETED, pop=1, quota 100%, janela 1 min) + env
  `SLN_QA_TEST_TARGET_RESET_TREES=5` (cap 45 min), device .44, **sem toque manual**. Parou no target em
  **20.1 min** (`reset_success=5`; 3 árvores novas sobre as 2 já validadas). Estado preservado.
- TREE 3/4/5: interval=3/4/3, completed=3/4/3 (== interval), failed=4/0/0, RESET ID+CLEAR juntos,
  RESET_SUCCESS=3/4/5, novo intervalo 4/3/(target). Invariantes OK.
- SUB_AD_CLOSE dedupe ao vivo: 10 clicks / 5 `awaiting_transition` / 0 re-click imediato (2º clique só
  após safety timeout 8s). `DEDUPE_LIVE_PASS`. `[FASTCLOSE][ERROR]=0`; START==AD_SESSION==CYCLE_START
  (SESSION_START íntegro); dup=0.
- `ALRTB_CLOSE_CAPTURED`: `View id=close_button` bottom-left `[120,1773][162,1815]`, **não-clicável**,
  filho `Image close_button_svg`; coexiste com o FrameLayout anônimo topo-direito (já fechável pelo
  detector). Detector ALRTB dedicado NÃO implementado. Compact ">|" segue VISIBLE_NOT_IN_XML.
- Status geral: `RESET_TREE_VALIDATION_PASS`; **versão funcional congelada**; próximo = **C90**
  (não iniciar automaticamente). Ferramentas: `config_qa_reset_tree_sln_v2.json`, `diag_controls.py`.

## 19. CYCLE_ACCOUNTING_V2 (2026-10-02)

- Nova regra autoritativa: **AD SESSION iniciada + resultado terminal (COMPLETED ou FAILED) = 1 ciclo.
  ABORTED (test end/watchdog/crash) e sem-sessão (NO_FILL antes de abrir) NÃO contam.** Idempotente por
  `cycle_id`. **SUPERSEDED:** "FAILED não decrementa reset".
- `qa_behavior.should_count_cycle(session_started, terminal_result)` (regra pura) +
  `QABehaviorScheduler.record_cycle_result(...)` (contador). Funcional (`ad_session` complete/fail/abort)
  separado de contabilidade. Telemetria `[QA][CYCLE_RESULT]`/`[QA][CYCLE_ACCOUNTED]`; `CYCLE_COMPLETED`
  não representa falha. Reset tree = `accounted == interval_drawn`.
- Offline: `tests/test_qa_behavior.py` (accounting) + `tests/test_ad_session.py` (abort) → **170/170**.
- Smoke 10 devices (`config_qa_10dev_accounting_sln_v2.json`): `acct == completed+failed` (54=37+17;
  reforço 35=30+5), FAILED conta, `aborted=0`, reset_success==clear==id por device; 12 devices distintos
  produziram trees. **INVENTORY_NO_SESSION != ACCOUNTING_FAILURE** (.27/.107 = 0 anúncio → cobertura de
  inventário, não falha). Status **`CYCLE_ACCOUNTING_V2_PASS`**; semântica congelada.
  Ferramentas: `accounting_smoke.py`, `config_qa_10dev_accounting_sln_v2.json`. Não iniciar C90.


## 20. DEVICE AGENT E3.2B - LIFECYCLE/REBOOT (2026-11-15)

- Controller WSS real 8766 + store persistente `state/device_agents/e32b`; APK: retry reset somente em
  HELLO_OK, AUTH_REVOKED em UNAUTHORIZED, caps refresh por heartbeat, PAIRING_REQUIRED sem token e
  `server.status=AUTH_REVOKED`.
- Device `.124` (SM-G975F, Android 12, **Ethernet eth0**). Matriz: S7 PASS; S2 PASS (FGS sobrevive);
  S3 PASS (start manual 1,0 s); S4 LIMITATION (Wi-Fi nao se aplica em eth0); S4b PASS (perda de
  transporte 60 s, reconexao 25,0 s); S5 PASS (offline 130 s, reconexao 1,5 s, OFFLINE age 126,8 s,
  5 tentativas, CPU 0,0%, PSS 62.658 KB); S8 PASS (install -r, 2,1 s); S6 PASS (AUTH_REVOKED, 1
  tentativa/48 s); S9 PASS (pm clear -> novo pairing, possible_reenrollment); S1 PASS (reboot +
  dismiss keyguard: BOOT_RECEIVED, auto hello 51,4 s).
- Identidade persiste em kill/force-stop/update/reboot; `pm clear` exige re-pair. BOOT_COMPLETED fica
  retido ate dismiss keyguard; para pre-unlock: directBootAware + LOCKED_BOOT_COMPLETED (nao implementado).
- Regressao 26/26 device_agent + compileall OK. Doc: `docs/E32B_LIFECYCLE.md`.

## 21. DEVICE AGENT E3.3A - TAP/SWIPE (2026-11-15)

- Protocolo sln-device/1 ganhou `TAP {x,y,duration_ms}` e `SWIPE {x1,y1,x2,y2,duration_ms}`,
  mutacoes serializadas no controller e no APK. Faixas TAP 10-1000ms, SWIPE 10-3000ms; bounds
  validados contra `display{width_px,height_px,rotation,density_dpi}` (HELLO/heartbeat + GET_STATUS/
  GET_DEVICE_INFO). Resultado apenas no callback real (`GESTURE_CANCELLED`/`GESTURE_TIMEOUT`);
  `command_id` deduplicado; disconnect pos-dispatch => UNCERTAIN sem reenvio.
- APK 0.2: `canPerformGestures=true`, executor unico para TAP/SWIPE/BACK/HOME/START_APP, caps
  dinamicas (Accessibility + display), refresh em config change/heartbeat. QA Activity propria com
  botao/contador/scrolls (`QA_LAYOUT`/`QA_STATE`).
- Matriz 18/18 PASS (TAP-1..8, SWIPE-1..8, caps off/on, lifecycle). 30 TAP: 0 falhas, total avg
  65,10ms (p95 75,97; transport 16,10; queue 1,43; gesture 47,57). 30 SWIPE: 0 falhas, avg 284,54ms
  (p95 294,61; gesture 266,93). ADB QA: tap 180,85ms avg (0/30 falhas), swipe 482,03ms (0/30).
- Rotacao nativa bloqueada pelo ROM; validada via `wm size 1920x1080` -> rotation=3/layout
  1080x1920 e TAP no novo centro (restaurado 1080x2280). Accessibility habilitada ao final.
- Testes 41/41 device_agent + compileall OK. Doc: `docs/E33A_GESTURES.md`.

## 22. DEVICE AGENT E3.3A.1 - HARDENING (2026-11-15)

- Gesture state machine propria (NOT_DISPATCHED/DISPATCHING/DISPATCHED/COMPLETED/CANCELLED/
  UNKNOWN_AFTER_DISPATCH): timeout pre-dispatch nunca despacha; pos-dispatch sem callback =>
  UNCERTAIN; callback tardio nao muda terminal; lane segura ate duration+1.5s; job isolado.
- SQLite monotono: UPDATE condicional `status NOT IN (terminais)` + INSERT OR IGNORE; terminal
  nunca regride. RESULT validado contra agent/device/op/protocolo; status allowlist
  (success/failure/expired/uncertain/duplicate); codigos tipados. command_id UUID4 no controller
  e APK; invalido rejeitado. START_APP com `allowed_packages` server-side (default nega).
- WsClient single-flight com ConnectionGuard (estados + connectionGeneration, 1 handshake e 1
  reconnect agendado, callbacks antigos ignorados). Provision exported com gate shell/root/self
  via referrer/callingPackage e URL wss; QA Activity agora debug-only (release merged sem QA).
  Server separa `timeout` de espera do TTL (`deadline_ms`).
- Testes: 50/50 Python; 14/14 selftests on-device (gesture/guard/APK validation). Real .124:
  13/13 PASS, incluindo PRE_DISPATCH sem efeito visual, POST_DISPATCH UNCERTAIN sem retry,
  dedupe, concorrentes, disconnect, a11y e resize. Benchmark limpo 30 TAP (p50 62,46; p99 76,82)
  e 30 SWIPE (p50 276,80; p99 301,15), 0 falhas. Risco: stalls ocasionais de I/O do host (~2 s p99
  no run completo), sem perda de corretude. Doc: `docs/E33A1_HARDENING.md`.

## 23. DEVICE AGENT E3.3A.2 - FINAL BASELINE FIX (2026-11-15)

- Reserva atomica de command_id no controller: INSERT OR IGNORE antes de qualquer send; se nao
  inseriu, valida binding (agent/device/op) e retorna cached terminal / DUPLICATE_IN_PROGRESS /
  COMMAND_ID_CONFLICT. `_send_and_wait` nao salva mais; somente inserted=true envia. UUID expirada
  e reservada terminal (idempotente). Transicoes SQLite estritas (QUEUED->SENT->terminal; sem
  volta/QUEUED->QUEUED).
- Superficies: ProvisionActivity/QaActivity apenas no manifest/sourceSet debug (ambas exported=true);
  release merged sem ambas. Provision nao usa mais referrer como autenticacao; bootstrap ADB debug
  preservado. Merged manifests debug/release validados.
- Testes 59/59 (56 controller + 3 manifestos); smoke real .124 PASS: provisioning ADB, PING,
  1 TAP, mesmo UUID => counter 0->1 e duas respostas success (uma execucao). Benchmark nao repetido
  (TAP/SWIPE inalterados). Doc: `docs/E33A2_BASELINE_FIX.md`.
- Status: **E3.3A PRODUCTION BASELINE = FROZEN**.

## 24. E3.4 - ADB PARITY AUDIT (2026-11-15)

- Auditoria read-only de todo o projeto: 55 itens inventariados (31 operacionais). Device Agent cobre
  TAP/SWIPE/BACK/HOME/START_APP/display/identidade/IP, mas falta a camada de percepcao (UI tree,
  screenshot, foreground com activity) e lifecycle privilegiado (force-stop/pm clear).
- Scores: primitivas operacionais 40,3% (9 C/7 P/15 M), hot path 37,5% (16 etapas), chamadas
  estimadas 21,7% (modelo por ciclo; dump de UI domina e esta 0%). 6 blockers: GET_UI_TREE,
  FIND_ELEMENT/TAP_ELEMENT, CAPTURE_SCREEN, foreground activity, STOP_APP/force-stop,
  CLEAR_APP_DATA. Logcat/READ_LOGS e importante com fallback visual; POWER opcional.
- Viabilidade: UI tree/screenshot via Accessibility (sem root); force-stop/pm clear exigem ADB, root
  ou device-owner. Bootstrap ADB permanece (install/provision/reverse/discovery/sideload/diagnostico).
- Nenhuma feature/integracao/alteracao de codigo nesta fase. Artefatos:
  `docs/E34_ADB_PARITY_AUDIT.md` e `state/device_agents/e34_parity_inventory.json`.

## 25. E3.5 - PERCEPTION PARITY (2026-11-15)

- Agent 0.3: GET_UI_TREE e CAPTURE_SCREEN read-only (Accessibility rootInActiveWindow/
  takeScreenshot), foreground package+activity/component/window, GET_STATUS estendido
  (device_time/elapsed/keyguard). Limites de depth/nodes/text/payload, timeout, redaction de senha,
  rate limits e logs somente de metricas. UsageStats fallback para activity (appop GET_USAGE_STATS).
- Device .124: UI tree 100/100 (10 nodes, avg 49ms, 3KB); ADB dump 2.36s. CAPTURE 30/30 (1/s),
  p50 350ms, PNG/SHA/dims corretos e diff 0. 300ms tree sustentavel (3,33/s, 0 falhas; PSS
  41,7-64MB); CAPTURE 2/s 16/16. Senha redigida; payload 799B/2 nodes truncado.
- Shadow: MainActivity+reward FULL; AppLovin playable/WebView 13/14 FULL + 1 PARTIAL transitoria,
  activity/package 14/14, WebView FULL 13/PARTIAL 1, screenshots 15/15 dims e diff medio 0,21.
  RIDs p50 1.0; bounds de reward/game-field/game-controls exatos; containers full-height diferem
  por inset de nav bar do uiautomator.
- Limitacao critica: shadow paralelo impossivel no ROM (uiautomator2/UiAutomation desabilita o
  AccessibilityService; 136/136 tree failures). Comparacao sequencial/manual sem alterar main/bot;
  nenhuma acao do Agent. STOP/CLEAR/logcat e FIND_ELEMENT continuam pendentes. Testes 64/64.
- Docs: `docs/E35_PERCEPTION_PARITY.md`; report: `state/device_agents/e32b/e35_report.json`.

## 26. E3.5B - SHIZUKU PRIVILEGED BRIDGE (2026-11-15)

- Shizuku oficial 13.1.5 API/provider; provider no manifest; bridge com ping/permission/listeners/
  UserService oficial. READY somente com binder vivo + permissao + UserService + UID 2000.
- UserService AIDL minimo (getUid/getProcessName/stopApp/clearAppData/destroy), sem shell/argv
  remoto; comandos internos fixos am force-stop/pm clear com package validado. Controller allowlist
  + denylist Agent/Shizuku/sistema; CLEAR mutating/dedupe/deadline.
- Device .124: READY/UID 2000, Shizuku 13.6.0; STOP_APP PASS (333ms, pid/foreground saiu),
  START_APP PASS, CLEAR_APP_DATA PASS (8.248s, marcador removido/pid encerrado), dedupe fisico 1
  por UUID, disconnect pos-dispatch e bridge morto => UNCERTAIN sem retry. BINDER_DEAD caps false;
  Shizuku reiniciado via libshizuku.so --apk=... => READY. Agent/controller restart READY.
- Permission revoke nao validado (switch do gerenciador nao revogou). Sem logcat/main integration.
  Regressao 68/68. Docs: `docs/E35B_SHIZUKU_BRIDGE.md`; report `e35b_report.json`.

## 27. E3.6 - DECISION PARITY / SHADOW BOT (2026-11-15)

- Criado `decision_parity_replay.py`: adapters ADB XML/Agent JSON -> NormalizedTree e decision
  engine host-only reutilizando as funcoes puras de fast-close do main para os DOIS lados.
  Nenhum clique do Agent; nenhum main_sln_v2 alterado.
- Fixtures reais: main_reward, ad_applovin, live_ad_0..9 (playable/WebView), store. 14 casos:
  package 100%, state 100%, candidate 100%, authorization 100%, decision 100%, 0 critical,
  0 OBSERVE->CLICK, 0 acoes do Agent. Activity 92,9% (store ViewGroup vs TransparentActivity).
- Detectores MAIN/reward/APPLOVIN/store/session PARITY_PASS. CLOSE_CANDIDATE, SUB_AD_CLOSE,
  COUNTDOWN, CONSENT, ENDCARD = FIXTURE_MISSING. Screenshots 14/14 dims, diff medio 1,43,
  classificacao OpenCV igual; IA offline. Testes 76/76.
- Veredito: **PARITY_PARTIAL / COVERAGE_PENDING**; nao PASS, sem integracao. Doc:
  `docs/E36_DECISION_PARITY.md`; JSON `state/device_agents/e36_parity.json`.

## 28. E3.6R - REAL DEVICE DECISION PARITY (2026-11-15)

- `e36_real_capture.py` read-only: 300 s de captura real, 552 snapshots (522 success/30 transicoes),
  tree p50 50,26ms, decision 0,68ms, PSS 63.336KB, shadow_actions=0; QA ADB separado (5 acoes) e
  reward somente quando clickable/enabled/visible.
- Fixtures reais: reward, return_main, playable, sub_ad_close, store. SUB_AD_CLOSE real com
  bounds [958,217,1028,287], ADB/Agent ambos CLICK/authorized, sem clique do Agent.
- Replay 19 casos: package 100%, activity 94,7% (store ViewGroup benigno), state/candidate/
  authorization/decision 100%, 0 critical, 0 OBSERVE->CLICK. Faltam CONSENT, COUNTDOWN, ENDCARD,
  structural/anonymous e rotulo INTERSTITIAL/REWARDED reais.
- Veredito: **PARITY_PASS_WITH_COVERAGE_PENDING** (criticos PASS, cobertura parcial); sem PASS
  completo e sem integracao. Testes 79/79. Doc: `docs/E36R_REAL_PARITY.md`; report:
  `state/device_agents/e36r_report.json`.

## 29. E3.7 - DEVICE BACKEND ABSTRACTION (2026-11-15)

- Criado `device_backend` com `DeviceBackend`, modelos tipados e implementacoes `ADBBackend`
  (uiautomator2/ADB legado) e `AgentBackend` (controller injetado, sem ADB). Flag
  `device_backend=adb|agent` e `execution_mode=normal|shadow`; env `SLN_DEVICE_BACKEND` /
  `SLN_EXECUTION_MODE`. Default adb; Agent sem controller falha explicitamente; sem fallback.
- Agent shadow bloqueia tap/swipe/back/home/start/stop/clear (`ShadowMutationBlocked`) e propaga
  UNCERTAIN. Main roteia dump/foreground/screenshot/status e 28 taps, 11 back/home, 8 start,
  12 stop, swipe de movimento e clear Agent; reset com clear UNCERTAIN nao reexecuta.
- Equivalencia E3.6 normalizada (containers diferem por inset ADB; bounds do reward exatos).
  Contrato 7/7; regressao 86/86; compile OK. Smoke real ADB 74 nodes/1080x2280 read-only;
  Agent 5 nodes/screenshot, 7/7 mutacoes BLOCKED, 0 acoes.
- Limitacoes: seletores/xpath, `swipe_ext`, power e force-stop periodico ainda u2 direto; sem
  logcat/eventos; nenhum ciclo completo via Agent. Veredito: **PASS_DEFAULT_ADB_CONTRACT**.
  Doc: `docs/E37_DEVICE_BACKEND.md`; report: `state/device_agents/e37_report.json`.

## 30. E3.8 - AGENT FULL CYCLE (2026-11-20)

- Perfil `config_e38_agent_single.json` (`agent/live`), 1 device, max 3 ciclos/900s. Preflight
  real PASS: Agent online, Accessibility, Shizuku READY/UID 2000, 10/10 capabilities.
- Teste A: 1 started/1 accounted/1 COMPLETED (68,7s); 5 mutacoes Agent; sem erros/uncertain.
- Teste B (IA server aberto): 3 started/3 accounted, 1 COMPLETED + 2 FAILED; ambos FAILED por
  timeout de criativo (IA no_detection/deteccao rejeitada), recovery esgotada. Um timeout foi
  recuperado por `anonymous_top_right` e retornou a MAIN.
- Backend B1: 989 reads/13 mutations (START_APP/TAP/BACK/HOME/STOP_APP), todas success; 0
  uncertain/erros/reconnects/duplicatas. Legacy 10 leituras de logcat (30 subprocessos ADB),
  0 mutacoes ADB. IA server `/health` OK/model loaded; 2 consultas.
- Accounting cumulativo 7/7, sem double-count; reset/clear 0. Invariantes: sem duplicata,
  fallback silencioso, reset repetido, pending orfao ou reconnect storm.
- Veredito **E3.8_AGENT_BACKEND_PARTIAL**: backend PASS, funcional parcial, ciclo ainda depende
  de leitura ADB de FORMAT/eventos. Nao declarar ADB-free. Doc:
  `docs/E38_AGENT_FULL_CYCLE.md`; reports: `e38_report.json`, `e38_commands.json`,
  `e38_legacy_fallbacks.json`.

## 31. MULTI-APP / RUN_APPS (2026-11-20)

- `main_sln_v2.py` usa `SLN_APP_PACKAGE` (default `com.flixdigital.dramaclassic`) e
  `SLN_APP_LABEL`; `package` alimenta deteccao de MAIN, seletores e filtros. Sem env, comportamento
  identico ao Drama Classic.
- Interface: “Apps a rodar” com 6 apps (`Mx MotosV2`, `cas`, `Puxo No Grau`, `Bike Mx`,
  `Grau Bahia`, `Bikes Mil Grau`), todos marcados por default. 1 app -> `main_sln_v2.py` direto;
  2+ -> runner `run_apps.py` na raiz.
- Campos da interface: quota min/max de anuncios por app (default 5..15), passo min/max
  (1..3), grandes ciclos (`0`=infinito), delay. Apos cada grande ciclo o runner faz reset de ID
  (`force-stop` + `pm clear`). Cada passo roda o bot com `SLN_TEST_MAX_CYCLES=chunk`.
- `run_apps.py`: seletores `1..6`/package/label/`all`; `--cycles-min/max`, `--chunk-min/max`,
  `--rounds`, `--runtime`, `--delay`, `--mode`, `--list`. Logs
  `logs/apps_<package>_<timestamp>.log`; aborta no-fill cedo e tem watchdog de 60s no primeiro
  anuncio. Console fecha quando o processo termina (limite), nao por crash automatico.
- `auto_reward.py` (raiz) e um helper separado que lista os mesmos apps e clica no botao
  `ASSISTIR ANUNCIO`; nao e o bot completo. `exe.bat` inicia interface + IA.
- Pendente: validacao funcional de quota/passos/reset no device para os 6 apps.


## 32. DPN E2.6 - GROUP LEDGER / EGRESS EPOCH (2026-11-20)

- Flag `dpn_group_ledger_v2_enabled=false`. Contador agregado por route_group + egress epoch;
  WorkUnitEvent idempotente por host|instance|cycle no inbox; coordinator e single writer.
- Inbox `work_unit_events.jsonl`; ledger/export `group_ledger.json`, `epochs.json`,
  `route_generations.json`, `work_units.jsonl`; ip_history global preservado.
- Threshold usa `dpn_work_units_per_ip` como work units por epoch; gate bloqueia todos os membros;
  ciclos ativos terminam; overshoot fica fora da quota; DRAIN_TIMEOUT explicito.
- Candidate ledger por generation; reject same IP/cooldown/unavailable; availability probe
  `AVAILABLE/UNAVAILABLE/INCONCLUSIVE` com retries; refresh de rotas separado de reset node;
  `refreshTunnel` mapeado para refresh_route_set; max refresh -> DEGRADED.
- Runner v2 integrado, status compativel com UI e botao ?Refresh Rotas?. Console: cada transicao DPN e anexada ao log de cada device do grupo e a `logs/dpn_<group>.log` (`dpn_console_log_enabled=true`). Testes novos:
  group ledger 13, route generation 5, availability 3; suite DPN **136/136 OK**. Smoke real Deeper
  pendente. Doc: `docs/DPN_GROUP_LEDGER_V2.md`.

## 33. E3.10A/B - UNIFIED DPN AGENT + TRANSPORT POOL (2026-11-22)

- Device Agent APK 0.4: DpnOps read-only em `DpnClient.kt` (identity/status/tunnels/mode/
  nodeLbMode/access-control); credenciais AtomOS no Keystore; Provision debug aceita
  atomos_url/fp/password sem logar senha. Protocolo Python e capabilities atualizados.
- Heartbeat inclui `dpn_presence` (reachable, physical_dpn_id, latency, authenticated,
  capabilities, observed_at_ms) a cada ~20s, probe assincrono; server aceita PresenceRegistry.
- `dpn/transport_pool.py`: pool por physical_dpn_id, preferencia deterministico por latencia,
  stale/offline/capability filter, read failover pre-dispatch, UNCERTAIN sem retry.
- APK BUILD SUCCESSFUL; suite 252/252. Checkpoint real bloqueado: Device Agent nao instalado em
  .50/.99/.127 e os tres DPNs sao distintos; nenhuma mutation. Flag OFF; sem alteracao Fast Close.
