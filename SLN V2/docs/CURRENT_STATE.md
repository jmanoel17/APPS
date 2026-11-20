# CURRENT_STATE.md — SLN V2

> Estado atual + próximos trabalhos. **Atualize este arquivo ao final de toda mudança.**
> Última atualização: 2026-10-02 — **CYCLE_ACCOUNTING_V2** (§18; COMPLETED **e** FAILED contam 1 ciclo;
> ABORTED/sem-sessão não). Antes: RESET TREE 5/5 (§17), SUB_AD_CLOSE (§16), Ciclo (§15).

## O que está validado e NÃO deve ser redesenhado

| Fluxo | Estado | Evidência |
|---|---|---|
| Fast Close Watcher (1 dump/tick, clique por bounds) | PASS | rodadas recentes |
| FORMAT por evento (app_event > cas_event) | PASS | 0 unknown nas rodadas recentes |
| Lookup FORMAT via `logcat --pid` (~600ms) | PASS | fix do `-s` lento |
| Anti-vazamento de SHOWED antigo (piso device-clock) | PASS | fix aplicado |
| Pílula `Google Play` — SKIP no lado esquerdo | PASS | 4/4 reveals validados |
| Interstitial (sem reward gate, reveal contingência 20s) | PASS | fecham 11–40s |
| Rewarded com countdown (completion observada) | PASS | 35–70s, +10 sempre preservado |
| `REWARD_EARNED` pós-fechamento (não é gate) | PASS | confirmação ~30–60ms após MAIN |
| Endcard structural X (998,177) | PASS | 100% `structural` |
| Store/BACK orientado por estado | PASS | 1–2 backs, sem loop |
| Recovery Close Cascade | PASS | `RECOVERY_CLOSE_CONFIRMED_LIVE`, +10 |
| Reset de identidade + `pm clear` | PASS | rodada de confirmação |
| Persistent logcat (FORMAT da memória) | PASS | subprocessos −64%, unknown→0 (A/B/C §11.3) |
| Adaptive tick | PASS | dumps −52%, reconnects/errors→0 (A/B/C §11.3) |
| Watchdog/ownership (single-owner, generations, kill da árvore) | PASS | fault injection 1/5 + lifecycle 30 + C60/C90 (§12) |
| Capacidade single-host | **APPROACHING** | C60 clean (60/dup0) + C90 (90/dup0); §13 |
| QA Realistic Behavior (delay/reset quota/install 24h) | PASS | 41 testes + sim 20/50 + smoke real (§14) |
| Ciclo / AD Session (1 clique = 1 ciclo; N stages; MAIN) | PASS | `ad_session.py` + 22 testes + smoke real 1 device (§15) |
| SUB_AD_CLOSE (close estrutural topo-direito, sub-ad WebView) | PASS | `subad_close.py` + 27 testes + validação real 10.2s (§16) |
| Reset Tree model (5 árvores ao vivo) | PASS | `RESET_TREE_MODEL_VALIDATED_LIVE_5X` (§17) |

### Gaps conhecidos (aceitos, não são bugs)

- **Rewarded sem countdown** — duas classes (`MW_PLFRAME` playable com close offscreen e
  `adFrame` WebView com close anônimo topo-direito). O recovery cobre a segunda. Sem fallback
  temporal, por decisão.
- **Aparelho 192.168.1.124** — saldo pode ter sido zerado pelo último teste de `pm clear`;
  tratar como primeira execução.
- **IA confunde pílula com X (conf 0.58)** — rejeitada por `x_region`. Filtro é a proteção,
  não o problema.

## Próximos trabalhos (fila atual)

> **Fila atual de escala:** ver §13.5 — status `SINGLE_HOST_CAPACITY_APPROACHING`; próximo passo é
> **sharding** (antes de qualquer 120); **não** iniciar 120 automaticamente. Itens abaixo são históricos
> de fases anteriores (mantidos como registro).

### 1. Normalizar reset/clear — FEITO
`_clear_app_data_verified()` extraído (force-stop + `pm clear` validado + retry + medição).
`reset_identity_and_app_state()` usa o helper. Caminho independente de `clear_data_pct` usa o
mesmo helper. Flag `did_clear_this_round` impede clear duplicado no mesmo ciclo. Falha vira
estado `reset_pending`/`clear_pending` com backoff por device (não relança o app). `reset_id()`
sem ação real de reset retorna falha identificável (não `success` silencioso). `_fc_coins_last`
zerado após qualquer clear.

### 2. Tornar o visual classifier lazy — FEITO
O watcher inicia **FORMAT primeiro**; o classifier visual (`/classify`) só é disparado se o
FORMAT esgotar `applovin_format_max_tries` sem resultado. Precedência inalterada
(app_event > cas_event > visual fallback > unknown) e reward gate intacto. Métricas:
`visual_classifier_invoked`, `visual_classifier_reason`, `visual_classifier_ms`.

### 3. Corrigir startup semaphore — FEITO
`START_SEMAPHORE` agora controla APENAS o arranque: acquire → `Popen` → registro → release →
`process.wait()` fora do semáforo. Capacidade configurável por `SLN_START_CONCURRENCY`
(default 8). Stagger com jitter ao iniciar muitos devices.

### 4. Adicionar métricas de escala — FEITO
Resumo `[PERF]` a cada ~60s por processo/device (sem log por tick): dumps (count/bytes/avg/p95/max),
format lookup, subprocessos ADB, screenshots, chamadas de IA, reset/clear, reconexões, erros.

### 5. Primeiro benchmark multi-device: 5 e depois 10 — PENDENTE
Usar o perfil de benchmark (`config_benchmark_sln_v2.json`, ver `SCALING_140.md`) e o
checklist do mesmo documento. **Baseline de 1 device já medido** (ver §6).

## 6. Baseline medido (1 device) + achados do smoke real — 2026-09-25

Executado em `192.168.1.100:5555` (SM-G955U, **1920x1080 landscape**), app em estado
original, `limite=2` anúncios, config de desenvolvimento.

Resumo `[PERF]` (cadência exata de 60s, 7 amostras):

| Métrica | Valor observado |
|---|---|
| `hierarchy_dump_count` | 22 → 818 (0.35–3.0/s, depende do caminho) |
| `hierarchy_dump_bytes` | 501 KB → 33.8 MB acumulados (~41 KB/dump) |
| `dump_ms_avg` / `p95` / `max` | **130–209 / 155–268 / 314–319 ms** |
| `format_lookup_count` / `avg` / `max` | 6 / 330 ms / 350 ms |
| `adb_subprocess_count` | 18 (= 3 por lookup, contagem conferida) |
| `screenshot_count` / `bytes` | 13 / 4.1 MB |
| `ai_request_count` / `avg` | 2 / **2096 ms** |
| `errors` / `reconnect_count` | 0 / 0 |

### FASE 3 confirmada ao vivo (classificador lazy)

```
[APPLOVIN][FORMAT] unknown source=none reason=no_recent_event tries=3
[APPLOVIN][TYPE] visual_classifier_invoked=True reason=format_unknown_no_recent_event (POST em thread)
[APPLOVIN][TYPE] interstitial source=ia_classify
[FASTCLOSE][AD_SUMMARY_FORMAT] ... visual_classifier_invoked=True
     visual_classifier_reason=format_unknown_no_recent_event visual_classifier_ms=2771
```

O classificador só disparou **depois** de o FORMAT esgotar `applovin_format_max_tries=3`.

### Achados novos (importantes para a escala)

1. **`dump_ms` ≈ 200 ms neste device** (`p95` ≈ 265 ms). O tick de 300 ms tem só ~1.5x de
   folga sobre o custo do dump. É o principal risco de contenção ao subir devices.
2. **`ia_url = http://localhost:8000` custa ~2 s por chamada de IA.** A resolução de
   `localhost` no Windows tenta IPv6 (::1) antes do IPv4 e atrasa ~2 s. Medido: ~2.1 s via
   `localhost` vs **~5 ms via `127.0.0.1`**. Com o timeout de 4 s de `ia_detect_x`, a primeira
   chamada pode estourar. **Recomendação (não aplicada): usar `http://127.0.0.1:8000`**.
3. **O app EMITE `AdHelperLog`/`CAS.AI` normalmente (correção de um achado anterior).**
   Investigação objetiva A-D (2026-09-26) em 3 devices da farm esclareceu:
   - **`.102` (SM-G973F, logd running, com internet):** `logcat -d --pid=<app_pid>` mostra
     `AdHelperLog: FORMAT=INTERSTITIAL EVENT=SHOWED` **e** `CAS.AI: ... Ad Impression`, ambos
     no **mesmo PID** do app (PID 831, threads 831/859). Os eventos EXISTEM no build.
   - **`.101` (logd running, SEM internet):** `AdHelperLog`/`CAS.AI` aparecem como
     `FORMAT=X EVENT=FAILED_TO_LOAD` (`No internet connection (Code: 9)`). Tags existem, mas
     não há SHOWED porque o anúncio nunca carrega (sem internet).
   - **`.100`/`.104` (logd em `restarting`):** `logcat read failure` — **nenhum** log de
     qualquer tag (não é o app, é o serviço `logd` quebrado no device).
   Conclusão: o "0 linhas no --pid" da rodada anterior era **logd quebrado** no device e/ou
   **falta de internet**, não ausência de logs no build. O piso 127.0.0.1 não tem relação.
4. **Consequência prática (inalterada):** em devices sem SHOWED (sem internet/logd), o FORMAT
   vai ao fallback visual; sem `format_source` de evento, `inter_ok` é falso e o interstitial
   só-visual **nunca usa o reveal de contingência** → espera o timeout (90 s). Pré-existente.
5. **O recovery funciona em tela landscape**: `anonymous_top_right` achou **(1712,70)**
   (bounds `[1659,15][1766,126]`) em 1920x1080 — as frações adaptam a orientação.
6. **Invariante mantida ao vivo:** `[FASTCLOSE][REWARD_COMPLETION] not_detected ... — sem SKIP
   (nenhum fallback temporal)` nos dois anúncios.

### Smoke real: resultado do dia (2 anúncios, honesto)

**0 anúncios fechados.** Não houve nenhum `X_VISIBLE`, `ENDCARD`, `SKIP_CONTROL`, `TOP_CONTROL`
ou `REVEAL` nas 1131 linhas do log — ou seja, o X nunca apareceu nas camadas de XML locais
nesse device (consistente com a classe em que só o recovery encontra o close).

O que de fato aconteceu, por anúncio:

| Anúncio | Tipo | Resultado | Recovery |
|---|---|---|---|
| 1 | interstitial (visual) | TIMEOUT 90.2s, `last_action=none` | clicou `anonymous_top_right` (1712,70) → observe `estado=applovin` → escape |
| 2 | rewarded (visual) | TIMEOUT 180.1s (diag estendido) | clicou `anonymous_top_right` (1712,70) → observe `estado=applovin` → escape |

Contadores do run: `ARMED`=2, `DIAG_REWARD`=17, `DIAG_PLAYABLE`=1, `FLOW`=3, `RESULT closed=True`=0.
`[STAT] ads=0 ... escapes=1 falhas=1`.

Config ativa no run: `skip_to_endcard=True`, `reward_completion_required=True`,
`recovery_close_enabled=True`, `tick=300ms`, `timeout=90s`.

> Leitura: o run **não fecha anúncio nesse device**, porque (a) o app não emite evento de
> formato e (b) o X dessa classe não está no XML local — o recovery acha o candidato anônimo,
> mas o clique não levou à MainActivity. Isso é o gap §6 da memória (rewarded/interstitial sem
> pílula e sem countdown), não uma regressão desta fase.

### Efeito colateral observado da FASE 3 (sem impacto funcional conhecido)

`[FASTCLOSE][ARMED] reason=interstitial elapsed=8.52s`: como o classificador agora espera o
FORMAT esgotar 3 tentativas (~5s, `applovin_format_retry_s=2.0`) + o POST (~2.8s), o ARMED de
um interstitial classificado só visualmente passou a ocorrer por volta de 8–9s em vez de ~1–2s.
O gate **não** foi afetado: para interstitial sem `format_source` de evento, `_fc_reward_safe`
segue aplicando o completion gate (pílula/countdown). Monitorar em devices onde o FORMAT por
evento **funciona** (aí `armed` continua imediato por `ad_type=interstitial`).

## 7. Preparação do benchmark: IA url, FORMAT farm e host baseline — 2026-09-26

Rodada de preparação para o benchmark 5 devices (não iniciou 5 devices).

### 7.1 IA URL — `localhost` vs `127.0.0.1` (aplicado no perfil de benchmark)

Medição quente (mesmo host, 3 amostras por endpoint):

| Endpoint | `localhost` (ms) | `127.0.0.1` (ms) | Ganho |
|---|---|---|---|
| POST /detect | 2144 / 2188 / 2270 | 129 / 135 / 140 | **~16x** |
| POST /classify | 2079 / 2089 / 2102 | 55 / 63 / 73 | **~33x** |
| GET /health | — | 42 (frio) | — |

Causa: resolução de `localhost` no Windows tenta IPv6 (::1) antes de IPv4, atrasando ~2 s.
**Aplicado:** `config_benchmark_sln_v2.json` agora tem `"ia_url": "http://127.0.0.1:8000"`.
Confirmado `/health`, `/detect`, `/classify` via 127.0.0.1 (HTTP 200; modelo carregado).
O código mantém `ia_url` configurável (nada hardcoded).

### 7.2 FORMAT na farm — investigação A-D (resumo no §6.3 acima)

Conclusão objetiva: **os eventos existem no build** (`AdHelperLog`/`CAS.AI`, mesmo PID do app).
Falhas observadas em devices específicos:
- `.100`/`.104`: **`logd` em `restarting`** → `logcat read failure` → FORMAT indisponível.
- `.101`/`.102`: **sem internet no device** → SDK CAS `No internet (Code: 9)` →
  `EVENT=FAILED_TO_LOAD` (tags presentes, sem SHOWED).

Não foi recolocado `logcat -d -s` no hot path (só investigação). Sem heurística nova.
Nenhuma alteração de gate/limit. Para benchmark 5-device: **pré-check de `logd` e de
conectividade do SDK em cada device** antes de contar FORMAT.

### 7.3 Functional baseline farm (`.102`) + host benchmark

Rodada de ~30 min (perfil benchmark, `limite=10`, `ia_url=127.0.0.1`, `benchmark_host.py`
paralelo em `benchmarks/host_1device_*.csv`).

**Functional:** **0 anúncios fechados.** O SDK CAS no `.102` reportou
`No internet connection (Code: 9)` durante toda a rodada → banner nunca carregou → o bot ficou
no backoff de banner (falhas_seguidas 1→8, 30→120 s). `ads=0`, `errors=1`, `reconnect=0`,
`format_lookup=0` (nunca abriu anúncio). Resultado: **FARM_BASELINE_NOT_ESTABLISHED**.

**Host (336 amostras, intervalo 5 s):**

| Métrica | Valor |
|---|---|
| `host_cpu_pct` avg / max | 23.8 / 100 |
| `host_ram_used_mb` avg | 19800 (host ~60.8% usado) |
| `python_rss_total_mb` avg / max | 304 / 498 |
| `ai_process_rss_mb` | ~10–13 |
| `adb_process_count` avg | 1.1 |
| `bot_process_count` max | 2 |

**Device [PERF] (`.102`, 1681 s, somente path de banner — sem dump do watcher AppLovin):**
`dump_ms_avg=124`, `p95=163`, `max=248`; `hierarchy_dump_count=316`; `hierarchy_dump_bytes=8.1 MB`;
`format_lookup=0`, `screenshot=0`, `ai=0`.

> Leitura: a métrica de host funciona e cabe (CPU ~24% com 1 bot + IA + ADB). O resultado
> funcional foi bloqueado por `Code: 9` (device sem internet p/ o SDK CAS) — não é regressão do
> código. Requer rede válida nos devices antes do benchmark 5-device.

## Comandos úteis

```bash
# benchmark_host (host CPU/RAM/RSS, separado do bot)
python benchmark_host.py --interval 5 --duration 0 --out benchmarks/host_<ts>.csv

# sintaxe
python -m py_compile main_sln_v2.py config_sln_v2.py aiMain_sln_v2.py interface_sln_v2.py

# config efetiva
python -c "import config_sln_v2 as C; print(C.load_config())"

# config de benchmark (OVERLAY mesclado sobre a base, não sobrescreve o dev)
SLN_CONFIG_OVERLAY=config_benchmark_sln_v2.json python -c "import config_sln_v2 as C; print(C.load_config())"

# bot em 1 device
python main_sln_v2.py 192.168.1.124:5555 0 2

# servidor IA
python aiMain_sln_v2.py

# preflight (read-only) antes de benchmark
python preflight_devices.py --group NOME | python preflight_devices.py 192.168.1.124:5555

# atualizar GMS/Chrome via sideload (sem Play Store)
python sideload_gms_chrome.py --setup
python sideload_gms_chrome.py --all --limit 8
python sideload_gms_chrome.py --verify

# benchmark de N devices (bots + benchmark_host em paralelo)
python benchmark_run.py --count 10 --ads 6 --minutes 30
```

## 8. Infra/medição — correções 2026-09-28 (pré-benchmark 5-device)

Correções de infraestrutura/medição (nenhuma alteração de gate funcional):

1. **Layering de config.** `load_config()` agora mescla `DEFAULTS → config_sln_v2.json →
   SLN_CONFIG_OVERLAY`. O overlay (`config_benchmark_sln_v2.json`) **mescla SOBRE** a base e
   preserva valores que não declara (ex.: `download_apps_pct=5` do dev, antes vazava para 25).
   `SLN_CONFIG_FILE` continua existindo e SUBSTITUI a base (perfil completo).
2. **Interface passa perfil.** Novo seletor "Perfil" (dev/benchmark) em `interface_sln_v2.py`;
   benchmark injeta `SLN_CONFIG_OVERLAY=config_benchmark_sln_v2.json` no `Popen` do bot.
3. **Reset pendente por etapa.** `reset_identity_and_app_state()` agora marca `_clear_pending`
   (não `_reset_pending`) quando o ID passou e SÓ o `pm clear` falhou → o retry repete apenas o
   `pm clear`, sem re-executar `reset_id()`.
4. **Hot path do banner sem `UiObject.click()`.** `processar_home()`/`abrir_anuncio_banner()`
   usam `clicar_coordenada_por_seletor()` (bounds do dump + `d.click(x,y)`), evitando o bloqueio
   ~20 s (`-32002 method=wait`).
5. **`benchmark_host.py` matcher exato.** `bot_process_count`/`ai_process_rss_mb` usam
   `os.path.basename(...) == "main_sln_v2.py"` / `"aiMain_sln_v2.py"` (não substring).
6. **Warm-up da IA.** `aiMain_sln_v2.py` faz 1 inferência dummy no startup (cold-start ~3.7–4.9 s
   fora do caminho crítico) e expõe `warmup` no `/health`.
7. **Test guards.** `SLN_TEST_MAX_CYCLES` / `SLN_TEST_MAX_RUNTIME_S` (env) encerram o bot após N
   ciclos / N segundos (0 = ilimitado).
8. **`preflight_devices.py`** (read-only): `online`, `screen_awake`, `battery_pct`,
   `app_installed`, `ia_health` antes de um benchmark. `--group`, `--all` ou lista explícita.

## 9. Atualização da farm (GMS + Chrome), consentimento e benchmark — 2026-09-29

### 9.1 Atualização GMS + Chrome via sideload

Diagnóstico: **116 devices** na porta 5555 (57 S8/S8+ Android 9, 59 S10/S10+ Android 12).
Play Store **não serve** (12 sem conta Google; vários com sessão expirada/"autenticação
necessária"). Solução: sideload via `adb install-multiple` (sem conta).

**Variantes validadas — o que muda é o Android (todos os devices são arm64):**

| Android | Modelo | Qtd | GMS (variante/minSdk) | Chrome (minSdk) |
|---|---|---|---|---|
| 9 (sdk28) | S8/S8+ | 57 | `100400` / minSdk28 | 138 / minSdk26 |
| 12 (sdk31) | S10/S10+ | 59 | `190400` / minSdk31 | 154 / minSdk29 |

Fontes dos APKs: GMS `100400` ← `.101`; GMS `190400` ← `.124`; Chrome 138 ← `.101`; Chrome 154 ← `.124`.
GMS 190400 tem 12 splits (inclui módulos Dynamite de ads); 100400 tem 4 splits.

Resultado: **114/116 atualizados** (GMS 26.34.36 ou 26.34.67 + Chrome correto). 2 pendentes
(`.53`, `.21`) reiniciaram durante o update → precisam de USB p/ re-habilitar `adb tcpip 5555`.

### 9.2 Consentimento CAS ("YES, I AGREE")

`tratar_consentimento_cas()`: sem filtro de `package` (o SDK da CAS pode renderizar o diálogo em
outra package), alvo **sempre** "YES, I AGREE", e agora é chamado a **cada ciclo** do loop
principal (não só em `processar_home`). `cas_consent_action=accept` (default).

### 9.3 Scripts novos

- `sideload_gms_chrome.py` — atualiza GMS/Chrome via `adb install-multiple` (sem Play Store),
  casando variante por Android SDK. `--setup` / `--devices` / `--all` / `--verify`.
- `benchmark_run.py` — lança N bots (overlay benchmark + guards) + `benchmark_host.py` em paralelo.
  `--count N` / `--devices` / `--ads M` / `--minutes T`. Grava manifest em `benchmarks/`.
- `update_gms_chrome.py` — update via Play Store (abre a página + toca "Atualizar"). Só funciona
  em devices com conta Google OK; na farm está bloqueado por auth (mantido para referência).

## 10. Benchmark 30 devices funcionais — 2026-09-29

**Escopo:** varredura de ~115 devices → **105 com botão "Assistir anúncio" disponível** (o botão
não discrimina inventário). Selecionados **30** (faixa `.21`–`.55`, inclui os 5 já validados
`.26 .28 .30 .32 .34`). Perfil benchmark (overlay), `ia_url=127.0.0.1`, `--ads 6 --minutes 30`.
Artefatos: `benchmarks/manifest_20260929_020510.json`, `benchmarks/host_30device_20260929_020510.csv`.

### 10.1 Funcional (32 min)

| Métrica | Total |
|---|---|
| Ads **CLOSED** | **151** |
| TIMEOUT | 59 |
| LEFT_AD | 1 |
| Rewarded com recompensa | **70** (todos +10 → **+700 moedas**) |
| `recovery_invoked` | 104 |
| Consent CAS (`dismissed`) | 21 |
| `clear_data`/reset ok | 43 |
| FORMAT `app_event` / `cas_event` / `unknown` | **212 / 0 / 8** |

- FORMAT por evento **funciona na farm** (212 `app_event`, 0 `cas`, 8 `unknown`) — confirma que o
  "não emite" anterior era `logd`/internet, não o build.
- Taxa de TIMEOUT ≈ 28% (gap conhecido: rewarded/interstitial sem pílula/countdown → recovery/timeout).
- **8 devices sem anúncio** na janela: `.27 .29 .41 .46 .47 .52` (0) e `.21 .25` (1).
- Fortes: `.26`=18, `.32`=15, `.34`=11, `.28`=10, `.30`=10.

### 10.2 Host (`benchmark_host.py`, 341 amostras)

| Métrica | min | avg | max |
|---|---|---|---|
| `host_cpu_pct` | 15.9 | **39.4** | **100.0** |
| `host_ram_used_mb` | 20352 | 21280 | 23040 (70.7%) |
| `python_rss_total_mb` | 310 | 1592 | 2103 |
| `python_rss_max_mb` | 158 | 308 | 328 |
| `bot_process_count` | 1 | 21 | 30 |

### 10.3 Device `[PERF]` sob carga (30 vs baseline 1 device)

| Métrica | 1 device | 30 devices |
|---|---|---|
| `dump_ms_avg` | 130–209 | min 58 / avg 131 / max 244 |
| `dump_ms_p95` | 155–268 | **avg 232 / max 598** |
| `format_lookup_ms_avg` | 330 | **avg 603 / max 4502** |
| `ai_latency_ms` (warm) | 124–148 | **avg 678 / max 1464** |
| `errors` / `reconnect` | 0 | 0 |

### 10.4 Leitura para a escada de escala

- **CPU do host pico 100%** e **dump p95 até ~600 ms** (> tick 300 ms) ⇒ o **host single-process
  é o gargalo em 30 devices**. A taxa de dump/ADB cai (format_lookup max 4.5 s). **Não subir para
  50** antes de mitigar (tick adaptativo / persistent logcat — hoje proibidos).
- **RAM ok** (70% com 30 bots + IA) — não é o limite.
- **IA aguenta** 30 bots concorrentes (max 1.46 s < timeout 4 s), mas sobe de ~130 ms (1 bot) para
  ~680 ms.
- Funcionalmente **estável**: 0 reconnect/erro, 151 fechados, 700 moedas preservadas.

### 10.5 Bug corrigido

`benchmark_run.py`: o `print` final continha U+0327 (cedilha combinante, não-cp1252) → estourava
`UnicodeEncodeError` quando o stdout não era UTF-8. **Fix:** char removido ("LANCADO" ASCII).

## 11. Benchmark 113 devices (60 min) + FASE DE ESCALA — 2026-09-29

### 11.1 Benchmark 113 devices — classificação

113 devices (varredura com espera de 60s), `--ads 0`, cap 60 min, overlay benchmark, `ia_url=127.0.0.1`.

- Classificação: **`113_DEVICE_CAPACITY_FAIL`** (capacidade) **+ `DIAGNOSTIC_PASS`** (diagnóstico coletado).
- **Causa: saturação host/ADB single-process** — **NÃO é falha da máquina de estados**.
- Funcional: 1167 ads observados; **630 TIMEOUT (~46%)**; **515 FORMAT unknown (~45%)**; 51/113
  devices serviram anúncio; 62 sem inventário/bloqueados; **37 reconnects**.
- Host: CPU avg **88.6%** / peak **100%**; RAM ~79%.
- Device PERF: dump p95 agregado **~2 s**; outliers **12–91 s** (ex.: `.122` dump_avg=12.8s,
  p95=61s, max=91s, reconnect=6, errors=13 — o worker ficou preso em dumps longuíssimos, por isso o
  `SLN_TEST_MAX_RUNTIME_S` **dentro** do bot não encerrou a tempo).
- IA operacional (max ~2.5 s < timeout 4 s).
- Comparação: 30 devices ≈ 28% TIMEOUT; 113 ≈ 46% ⇒ **SINGLE-HOST CURRENT ARCHITECTURE SATURATED**.
- **Os TIMEOUTs NÃO são classificados automaticamente como falha da máquina de estados** — em 113
  devices, a maioria vem da saturação (FORMAT unknown por infra) e do inventário/criativo.

### 11.2 FASE DE ESCALA — implementado (flags default OFF; comportamento atual preservado)

- `persistent_logcat.py` (FASE 3/4) — reader contínuo por device (1 stream `adb logcat` por bot),
  ring buffer + **piso de sessão** (anti-vazamento); consulta em MEMÓRIA (sem subprocesso por
  lookup); PID do app acompanhado; métricas `logcat_*`/`format_from_memory_count`. Flag
  `persistent_logcat_enabled` (default **false**).
- `adaptive_tick.py` (FASE 5/6) — tick por ESTADO + BURST; `ticks_by_state`, `requested/actual_tick_ms`.
  Flag `adaptive_tick_enabled` (default **false**).
- `watchdog.py` (FASE 2) — heartbeat JSON atômico + supervisor externo com WORKER_STALE / restart /
  QUARANTINED; estados RUNNING/BACKOFF/RECOVERING/DISCONNECTED. Flags `heartbeat_enabled`/`watchdog_*`.
- `sharding.py` (FASE 10) — static sharding (`shards/<HOST_ID>.txt`), `SLN_HOST_ID`; `benchmark_run`
  aceita `--shard HOST_ID`. IA com URL configurável (nunca só 127.0.0.1).
- FASE 7 — backoff crescente + jitter p/ no-fill (`idle_backoff_enabled`).
- FASE 9 — `device=` na telemetria de IA.
- FASE 8 (limites globais de operações pesadas) — **documentado e adiado** (evitar arquitetura frágil).
- FASE 11 — `tests/test_scale.py`: **37/37 ok** (offline; logcat parser/piso, adaptive tick, watchdog
  stale/restart/quarantine, sharding).
- FASE 12 — **1 device com flags ON (`config_scale_test_sln_v2.json`)**: sem crash (`errors=0`,
  `reconnect=0`); `dump_rate` caiu de ~200/min (300ms fixo) p/ **~64–70/min** (adaptive 800ms);
  FORMAT **resolvido pela memória** (`lookup_ms=None`) a partir do 2º anúncio; interstitial fechou
  em 20.3s (pílula 12.1s). **Sem regressão funcional observada.**
- FASE 13 (A/B 30 devices) e 14+ (60/90/120/140): **pendentes / requerem aprovação** (não iniciar 60+).
O erro ocorria **após** lançar tudo/gravar o manifest, então nunca afetou o benchmark — mas
poluía a saída do launcher.

### 11.3 A/B/C 30 devices (30 min cada) — valores reconciliados (autoritativos dos CSVs)

A=off/off; B=persistent; C=persistent+adaptive. Mesma lista de 30 devices
(`benchmarks/ab30_devices_20260929_224929.txt`; `.40` substituído por `.56`).

| Métrica | A | B | C |
|---|---|---|---|
| CPU avg / p50 / p95 / p99 / max % | 20.6 / 19.0 / 31.5 / 40.0 / 56.9 | 26.1 / 25.5 / 35.5 / 41.0 / 46.4 | 26.6 / 25.4 / 35.8 / 43.3 / 100.0 |
| amostras CPU >80% / >90% | 0 / 0 | 0 / 0 | **1 / 1** (0.29% do tempo; pico transitório) |
| ADB subprocesses (∑) | 1580 | 576 | 539 |
| dumps/min/device | 70 | 66 | 32 |
| dump avg / p95 / max ms | 142 / 238 / 10667 | 139 / 212 / 2266 | 132 / 232 / 5162 |
| FORMAT unknown % | 0.7 | 0.0 | 0.8 |
| format_from_memory | 0 | 255 | 238 |
| reconnects | 19 | 24 | 0 |
| errors | 21 | 24 | 0 |
| AI requests / latency max ms | 88 / 294 | 65 / 4224 | 69 / 2444 |
| ads processados / close % / timeout % | 301 / 75.1 / 24.9 | 255 / 78.0 / 22.0 | 237 / 76.8 / 23.2 |
| reward_ok (#) / recovery_invoked | 101 / 114 | 89 / 126 | 81 / 118 |

**Correções textuais:**
- **CPU B→C NÃO reduziu**: 26.1% → 26.6% (**+0.5 pp / +1.9%**). O "−1.8%" anterior era erro de
  sinal. O que reduz é o **dump count**, não a CPU em 30.
- **dump max**: A 10667 → B 2266 → C **5162**. Descrição correta: **C melhor que A no máximo,
  mas com outlier maior que B** (não "C caiu vs B").

Vereditos mantidos: **`PERSISTENT_LOGCAT_PASS`** (subprocessos −64%, unknown→0, 100% memória) e
**`ADAPTIVE_TICK_PASS`** (dumps −52%, reconnects/errors→0, sem regressão).

## 12. Correção de OWNERSHIP/RESTART do watchdog + C60 limpo - 2026-09-30

### 12.1 C60 anterior (contaminado) — registro

`C60_INVALID_FOR_CAPACITY` / `FUNCTIONAL_SIGNALS_VALID`. Motivo: **WATCHDOG_DUPLICATE_WORKER_BUG**
(restart não encerrava o worker anterior de forma confiável → até **96 bots** para 60 devices,
**~227 streams `adb logcat`**, CPU artificialmente inflada: avg 61.1 / p95 89.5 / p99 93.3).
Os números anteriores **não são apagados** (evidência do comportamento sob processo duplicado),
mas **não servem para capacidade single-host**.

### 12.2 Root cause

Ao matar um bot no Windows, o **filho `adb -s <device> logcat` sobrevivia** (não é morto junto com o
pai). Após restart, o novo bot criava outro stream → acúmulo. Confirmado no host: **291 processos
`logcat` órfãos com 0 bots** antes da correção. O `restart` antigo (`taskkill /PID` pelo pid do
manifest) matava só o processo principal e, em corrida, relançava sem confirmar a morte → duplicatas.

### 12.3 Correções (infra/supervisão; lógica de anúncio intocada)

- **`sln_proc.py`** (novo): identificação EXATA de worker por `basename(argv)=="main_sln_v2.py"` **e**
  `argv[i+1]==device` (sem substring). Helpers: `bot_processes_for_device`,
  `logcat_processes_for_device`, `child_logcat_pid`, `all_bot_pids`, `orphans`,
  `terminate_worker_verified` (mata a **árvore** — bot + descendentes `adb logcat`/`cmd`; não mata o
  adb server global; confirma ZERO).
- **Invariante SINGLE OWNER**: um device = no máximo um bot. Registry do manifest é a **autoridade**;
  heartbeat é só sinal.
- **`watchdog.py` v2**: registry por device (`pid/instance_id/generation/state/restart_count/
  quarantine_reason`), `STARTING`, `STALE_HEARTBEAT_GENERATION` (heartbeat de geração antiga é
  ignorado), **grace de startup** e **grace pós-restart**, e **coalescing** (restart concorrente do
  mesmo device → 1 spawn).
- **`watchdog_run.py` v2**: **restart transacional** (RECOVERING → kill verificado → confirmar ZERO →
  só então spawn → registrar pid/instance/generation → grace), **lock por device**, **preflight**
  (`DUPLICATE_WORKER_PREFLIGHT_FAIL` se `duplicates>0`), e CSVs de métricas
  `duplicate_worker_count/orphan_worker_count/logcat_process_count`.
- **Instance/generation**: `benchmark_run` injeta `SLN_WORKER_INSTANCE_ID`/`SLN_WORKER_GENERATION`;
  o bot publica ambos no heartbeat; `generation` incrementa no restart.
- **Cleanup do logcat child**: bot encerra o próprio reader no shutdown (`atexit`); supervisor mata a
  árvore (mesmo em morte abrupta). Heartbeat expõe `logcat_child_pid`.
- **Semântica de progresso (FASE 12/13)**: novo **ticker de heartbeat** (`_hb_loop`) mantém
  `last_progress` fresco em operações legítimas — inclusive **sleeps longos de backoff/no-fill** —
  e **congela** o progresso apenas quando um `dump_hierarchy` está travado (>`_HB_DUMP_STALL_S=30s`,
  `current_operation=dump_hierarchy`). Isso corrige os falsos-stale (antes **estale_s=300 < sleep de
  backoff** reiniciava device saudável).
- **`no_fill` telemetria (FASE 17)**: o heartbeat `BACKOFF/no_fill` só era publicado se
  `idle_backoff_enabled`; agora é publicado **sempre** que o banner não carrega (find **B**:
  telemetria, não comportamento). Em C60 com o perfil atual `no_fill_backoff` era 0 por isso, e
  `main_idle≈44` era device legitimamente em MAIN.
- **AI >4s (FASE 16)**: `perf_ai_latency()` conta `ai_over_4s_count` **por requisição** (`total_ms>4000`),
  não mais via máximo cumulativo. `[PERF]` agora traz `ai_over_4s_count`.

### 12.4 Testes offline (sem devices)

- `tests/test_scale.py`: **37/37** (mantidos).
- `tests/test_ownership.py` (novo): **23/23** — matching exato, spawn 0/1/2, terminate+exit,
  force-kill, processo antigo vivo→não spawna, child logcat, orphans, geração antiga, quarantine não
  relança, restart concorrente coalescido, grace de startup.

### 12.5 Validações reais

- **Fault injection 1 device**: PASS — suspensão controlada → gen 1→2→3; sempre bots=1/logcat=1;
  teardown 0/0.
- **Fault injection 5 devices**: PASS — 3 restarts (gen 1→2); sempre 1 worker/logcat por device;
  devices saudáveis **não** foram reiniciados; teardown 0/0.
- **Lifecycle 30 devices (12 min, watchdog ON)**: PASS — CSV do watchdog: 29 amostras,
  `duplicate_worker_count=0` e `orphan_worker_count=0` **sempre**; logcat estável ~28–30 (sem
  crescimento). (O `orph=1` momentâneo no script era o restart legítimo do watchdog contado contra o
  manifest inicial — artefato do script, não do supervisor.)

### 12.6 C60 limpo — COMPLETO (30 min) - 2026-10-01

`benchmarks/manifest_20261001_010348.json`, `host_60device_20261001_010348.csv`,
`watchdog_metrics_20261001_010528.csv`. 30 min completos (t=1805s). Pré-start: 0 bots / 0 órfãos / 0
logcat órfão.

**Invariante (autoritativa: CSV do watchdog, 49 amostras):** `duplicate_worker_count=0` e
`orphan_worker_count=0` **sempre**; `bot_process_count` 60/60, `logcat_process_count` 60/61.
O watchdog reiniciou **exatamente 1 device** (`.44`, gen 1→2, logcat limpo) — sem tempestade.
(O `orph=1` que o script marcou no fim era o worker do restart legítimo contado contra o manifest
inicial — corrigido para usar o registry vivo.)

| Métrica | C30 (C) | C60 inválido | **C60 limpo (30 min)** |
|---|---|---|---|
| bot_count (avg/max) | ~30 | 73.5 / 96 | **58.6 / 60** |
| logcat_count (avg/max) | n/m | 227 / 289 | **58.6 / 62** |
| ADB proc (avg/max) | n/m | 231 / 331 | **61.3 / 84** |
| CPU avg / p50 / p95 / p99 / max % | 26.6 / 25.4 / 35.8 / 43.3 / 100.0 | 61.1 / 57.9 / 89.5 / 93.3 / 99.4 | **51.1 / 48.8 / 80.1 / 88.8 / 99.7** |
| CPU >80% / >90% | 0s / ~5s | 220s / 55s | **80s / 15s (1.0%)**; longest >90% = 10s |
| RAM avg / max MB | ~19200 | 21282 / 22718 | **18779 / 19798 (57.6%)** |
| python RSS total avg MB | n/m | 4306 | **3413** |
| active_fastclose avg/p95/max | n/m | 13.8 / 18 / 20 | **4.4 / 8 / 10** |
| dump/min/device | 32 | 29 | **22** |
| dump avg / p95(dev) / max ms | 132 / 232 / 5162 | 146 / 270 / 13098 | **115 / 168 / 1061** |
| dump buckets >5s / >10s / >30s | n/c | 8 / 2 / 0 | **0 / 0 / 0** |
| FORMAT unknown % | 0.8 | 0.80 | **1.35** |
| reconnect / errors | 0 / 0 | 2 / 2 | **0 / 0** |
| watchdog restarts | n/a | 172 | **1** |
| duplicates / orphans | n/a | >0 (bug) | **0 / 0** |
| AI requests / AI >4s (real) | 69 / n/m | 101 / (métrica errada) | **62 / 1** |
| ads / close % / timeout % | 237 / 76.8 / 23.2 | 352 / 75.6 / 23.3 | **567 / 75.1 / 24.2** |
| reward_ok (#) / recovery_invoked | 81 / 118 | 117 / 179 | **194 / 285** |

**Leitura:** com processos corretos (60/60), a CPU é avg 51% / p95 80% / p99 89%, com apenas **1% do
tempo acima de 90%** (15s; maior rajada 10s) → **sem saturação sustentada**. Métricas funcionais as
mais saudáveis da série (dump p95 168ms, **0 dumps >5s**, reconnects/errors 0, close 75%). O dump avg
caiu vs C30 (115 vs 132ms) por menos dumps concorrentes. O escalonamento 30→60 elevou p95 de 35.8%
para 80.1% (superlinear) e p99 para 88.8%.

### 12.7 Classificação e próximo passo

**`SINGLE_HOST_CAPACITY_APPROACHING`** — C60 limpo válido e saudável, mas p95/p99 (80/88.8%) e o
crescimento superlinear 30→60 indicam que o teto da single-host está sendo **aproximado** (não
atingido: >90% só 1% do tempo). `WATCHDOG_FIX = PASS`; `WATCHDOG_FIX_FAILED` descartado.
`READY_FOR_90` NÃO é reivindicado.

1. Próximo teste: **90 devices** (só se autorizado; **NÃO iniciar automaticamente**) para localizar o
   teto. Se p95>90% persistente → `SINGLE_HOST_CAPACITY_REACHED` nessa faixa.
2. Não otimizar o persistent logcat (multiplexer/pooling) antes de decidir sobre o teto/particionar
   por shards.
3. Estimativa anterior `40–50 devices/host` **descartada** (`ESTIMATE_FROM_CONTAMINATED_RUN`).

### 12.8 Ferramentas novas desta rodada

`sln_proc.py`, `fault_injection.py`, `lifecycle_30.py`, `c60_clean.py`,
`tests/test_ownership.py`.

## 13. CAPACITY PROBE C90 - 2026-10-01

### 13.1 Setup

90 devices (60 do C60 clean + 30 READY; `benchmarks/90_devices_20261001_022959.txt`; READY=103).
Perfil idêntico ao C60 clean (`config_c60_sln_v2.json`; persistent+adaptive+heartbeat; `ia_url=127.0.0.1`;
idle_backoff igual). `benchmarks/manifest_20261001_023107.json`,
`host_90device_20261001_023107.csv`, `watchdog_metrics_20261001_023357.csv`.
**Sem early stop** (30 min completos, t=1836s). Nenhuma lógica funcional alterada.

### 13.2 Ownership / lifecycle (autoridade = CSV watchdog, 38 amostras)

`duplicate_worker_count=0` e `orphan_worker_count=0` **sempre**; `bot_process_count` 87.5avg/90max;
`logcat` 87.5avg/91max; `adb` 91.3avg/132max. **Watchdog: 0 restarts / 0 stale / 0 quarantine.**
`reconnect=0`, `errors=0`.

### 13.3 Capacidade

| Métrica | C30 (C) | C60 clean | **C90** |
|---|---|---|---|
| devices | 30 | 60 | 90 |
| bot count (avg/max) | ~30 | 58.6 / 60 | **87.5 / 90** |
| logcat count (avg/max) | n/m | 58.6 / 62 | **87.5 / 91** |
| active_fastclose avg / p95 / p99 / max | n/m | 4.4 / 8 / — / 10 | **7.6 / 13 / 14 / 15** |
| CPU avg / p50 / p95 / p99 / max % | 26.6 / 25.4 / 35.8 / 43.3 / 100.0 | 51.1 / 48.8 / 80.1 / 88.8 / 99.7 | **58.4 / 56.6 / 85.2 / 93.3 / 96.6** |
| CPU >85% / >90% / >95% | ~0 / ~5s / 0 | 30s / 15s (1.0%) / 5s | **75s / 45s (3.2%) / 5s** |
| longest >85 / >90 / >95 s | — / ~10 / — | — / 10 / — | **30 / 15 / 5** |
| RAM avg / max MB | ~19200 | 18779 / 19798 | **16842 / 18960 (51.7%)** |
| python RSS total avg MB | n/m | 3413 | **4934** |
| dumps/device/min | 32 | 22 | **24** |
| dump avg / p95(dev) / max ms | 132 / 232 / 5162 | 115 / 168 / 1061 | **110 / 162 / 3184** |
| dump >1s / >2s / >5s / >10s | n/c | 11 / 2 / 0 / 0 | **43 / 4 / 0 / 0** |
| FORMAT unknown % | 0.8 | 1.35 | **0.26** |
| format_from_memory | 238 | (n/c) | **387** |
| ADB proc avg/max | n/m | 61.3 / 84 | **91.3 / 132** |
| reconnect / errors | 0 / 0 | 0 / 0 | **0 / 0** |
| watchdog stale / restart / quarantine | n/a | 0 / 1 / 0 | **0 / 0 / 0** |
| duplicates / orphans | n/a | 0 / 0 | **0 / 0** |
| AI requests / >4s | 88 / n/m | 62 / 1 | **83 / 2** |
| ads / close % / timeout % | 237 / 76.8 / 23.2 | 567 / 75.1 / 24.2 | **380 / 85.8 / 14.2** |
| reward_ok (#) / recovery_invoked | 81 / 118 | 194 / 285 | **170 / 150** |
| no_fill_backoff (avg/90) | n/m | 0 (telemetria quebrada) | **68.4** |

**Fase inicial vs final (C90):** primeiros 5 min CPU avg 72.2 / p95 93.3; últimos 5 min avg 52.6 / p95 64.3
(pico de arranque/inventário; relaxa depois).

### 13.4 Achados

- **Sem saturação sustentada** (p95 85.2 / p99 93.3; >90% só 3.2% = 45s, rajada máx 15s). Dumps
  **estáveis (0 >5s)**; watchdog/reconnect **0**; FORMAT 0.26%; AI saudável (2 >4s).
- **Curva 60→90 ACIMA do esperado para saturação:** CPU p95 +5.1pp (80.1→85.2) e avg +7.3pp
  (51.1→58.4) — MUITO menor que o salto 30→60 (p95 +44.3pp). O salto 30→60 estava inflado (fase de
  arranque/backfill). Não é um "penhasco" em 90.
- **Gargalo dominante = INVENTÁRIO, não CPU:** `no_fill_backoff` avg **68.4/90** (agora a telemetria
  de no-fill funciona). Só ~4.2 ads/device em 30 min; `active_fastclose` avg 7.6. Com inventário
  abundante a CPU subiria (mais dumps concorrentes). Ads/device caiu vs C60 (9.45 → 4.2).
- Memória: python RSS total subiu 3413→4934 MB (+~50 MB/device); RAM do host **caiu** (18779→16842,
  51.7%) — não é gargalo.
- `close%` C90 = 85.8% (> C60 75.1%) com `timeout` menor — efeito de inventário (menos anúncios, e os
  attemptados fecham melhor), não regressão.

### 13.5 Classificação / decisão

**`SINGLE_HOST_CAPACITY_APPROACHING`.** C90 completou os 30 min sem degradação funcional (dup=0,
orph=0, watchdog 0, reconnect 0, dumps estáveis), MAS CPU p95 85.2 / p99 93.3 e rajadas >90% (45s)
indicam pouca folga para picos de inventário/reset/ADB noise. **NÃO iniciar 120.**

- `MAX_OBSERVED_CAPACITY` = **90** (rodou 30 min válido e saudável).
- `RECOMMENDED_OPERATIONAL_CAPACITY` ≈ **60 devices/host** (CPU p95 ≈80, com headroom; e o inventário
  já é o limitador em 90). Faixa 50–60/host; sharding recomendado antes de 120.
- **Bottleneck remanescente:** CPU do host (p95/p99 com picos de arranque) **+ inventário de anúncios**
  (no-fill 68/90) — o segundo não escala por hardware.

**Curva (não extrapolar linearmente):** CPU p95 35.8 → 80.1 → 85.2; avg 26.6 → 51.1 → 58.4.
A inflexão não aparece até 90; o teto prático está acima de 90, limitado por inventário/headroom.

## 14. QA REALISTIC BEHAVIOR - 2026-10-01

Camada de **simulação/agendamento por device** (heterogeneidade de frota), **fora** da state machine do
anúncio. Detalhe completo em `docs/QA_REALISTIC_BEHAVIOR.md`.

### 14.1 O que é

- `qa_behavior.py` (novo): `DeviceQABehaviorState` (persistente por device), `QaConfig` (validada,
  fallback seguro), `QABehaviorScheduler` (decide WAIT/RESET_DUE/RESET_ALLOWED/INSTALL_*/NORMAL_CYCLE),
  `QACoordinator`/`FileQACoordinator` (quota/concorrência/install da frota), `build_scheduler()`.
- Estado em `state/qa_devices/<safe>.json` (atômico); coordenação em `state/qa_coord.json` (lock).
- Default **OFF** (`qa_realistic_behavior_enabled=false`). Só o perfil **QA Realistic** liga.
- O `main` só EXECUTA a ação autorizada; reset usa SEMPRE `reset_identity_and_app_state()`
  (reset+clear juntos). Nada de Fast Close/reward/FORMAT/pill/Store/recovery foi alterado.

### 14.2 Comportamentos

- **Delay** por device `10..60s` (sleep cancelável em blocos). **Reset** individual a cada `2..30` ciclos
  (novo intervalo após sucesso). **Quota** de reset `5..25%`/janela (default 30 min) + concorrência
  `qa_max_concurrent_resets=2` + jitter. **Install** `5..10%`/população elegível, **cooldown móvel 24h**
  (`last_install_at`; nunca 2 instalações <24h; falha não marca sucesso). **Seed** por device
  (`sha256(global_seed|device_id)`), reproduzível.
- Persistente entre restart de bot/watchdog/host (não sorteia novo intervalo; mantém `last_install_at`).

### 14.3 Validação

- `tests/test_qa_behavior.py`: **41/41** offline (clock injetável).
- `simulate_qa_behavior.py`: **20 e 50 devices** — intervalos 2..30, `max_frac_devices_reset_same_cycle`
  0.20/0.14 (sem sincronização), delays heterogêneos, quota/install dentro da faixa.
- Smoke real (.44/.57/.126): `.44` e `.126` executaram RESET_DUE→SELECTED→RESET_SUCCESS (reset+clear
  acoplados, novo intervalo), `dup=0`, `remaining=0`. Install não exercitado (sem intersticial de Play
  Store na janela curta) — coberto offline.
- `find_button_devices.py`: 97/115 devices exibem o botão (`btnRewardAds`/"Assistir anúncio").

### 14.4 Arquivos novos

`qa_behavior.py`, `config_qa_realistic_sln_v2.json`, `config_qa_smoke_sln_v2.json` (test-only),
`simulate_qa_behavior.py`, `qa_smoke.py`, `find_button_devices.py`, `tests/test_qa_behavior.py`,
`docs/QA_REALISTIC_BEHAVIOR.md`. Chaves `qa_*` adicionadas a `config_sln_v2.py::DEFAULTS` (OFF).

**Status:** `QA_REALISTIC_IMPLEMENTATION_PASS`. Defaults de produção inalterados; smoke usou overlay test-only.

## 15. MODELAGEM DE CICLO / AD SESSION - 2026-10-02

Correção semântica: **1 clique em "Assistir anúncio" = 1 CICLO = 1 AD SESSION**. Dentro da sessão
podem existir **N STAGES** (vídeo/playable/Store/endcard/tela intermediária/novo creative). **Fechar
um controle NÃO finaliza o ciclo.**

### 15.1 Módulo `ad_session.py`

- `CycleSession.begin()` no início real da sessão; `continue_stage()` quando fechou mas **não** voltou
  à MAIN (novo stage, **mesma** sessão); `complete()` só com **MAIN confirmada**; `fail()` em **escape
  forçado** (HOME+force-stop). `classify_outcome(on_screen, on_main)` (puro).
- **Um clique por ciclo** — nunca nova solicitação ao app a cada stage. NÃO altera reward gate, FORMAT,
  pill, Store/BACK, recovery (só contabilidade/telemetria).

### 15.2 Integração no `main`

- `begin` após o clique quando se **sai da MainActivity** (ad abriu). Enquanto `_cycle.active`, o loop
  **não** reabre o app nem clica "Assistir anúncio" (continua a sessão).
- `_cycle_after_ad()` após `processar_ciclo_anuncio()`: MAIN→COMPLETED; foreground não-MAIN→CONTINUE
  (novo stage); fora do app→FAILED (escape).
- **`qa_scheduler.cycle_completed()` só em COMPLETED** → o intervalo de reset (2–30) conta **ciclos
  completos**, não stages/telas/X. `_cycle_fail_if_active()` marca FAILED em guard de teste/exceção.

### 15.3 Telemetria

`[QA][CYCLE_START]`, `[AD_SESSION][START]`, `[AD_SESSION][STAGE] state=/format=/activity=`,
`[AD_SESSION][STAGE_CLOSED] close_layer=`, `[AD_SESSION][CONTINUE] from_stage=/to_stage=/reason=`,
`[QA][CYCLE_COMPLETED] stage_count=/duration_s=/cycles_until_reset=`, `[QA][CYCLE_FAILED] reason=`.

### 15.4 Validação

- `tests/test_ad_session.py`: **22/22** (1 stage=1 ciclo; N stages=1 ciclo; FAILED não conta; sucesso
  só com MAIN; classify; IDs incrementam). Total offline: **123/123**.
- Smoke real 1 device (.44, 7 min): **3 CYCLE_START, 2 CYCLE_COMPLETED, 1 CYCLE_FAILED** (LEFT_AD→escape
  **não** completou), **0 CONTINUE** (sessões single-stage nesta amostra). Confundidor registrado: os 2
  COMPLETED ocorreram porque o **operador fechou os anúncios manualmente** (o bot viu MAIN e concluiu);
  a lógica de gate foi validada, mas a atribuição de fechamento ao bot fica pendente de rodada sem
  intervenção. Multi-stage (CONTINUE) é coberto pelos testes offline.

**Status:** `CYCLE_SESSION_MODELING_PASS` (modelagem/contabilidade). Sem alteração de fluxo funcional.

## 16. SUB_AD_CLOSE - 2026-10-02

Correção do gap confirmado: em sub-ads WebView, o **close topo-direito** era **anônimo** e não
entrava no normal path; só o recovery `anonymous_top_right` (centro fixo 993,252) o pegava (~90s),
e o `REVEAL_TOP` abria a Play Store.

### 16.1 Assinatura (confirmada)

`SUB_AD_CLOSE_SIGNATURE_CONFIRMED` — `FrameLayout` **clicável anônimo** (sem id/text/desc),
bounds `[958,217][1028,287]` em 1080x2280 (norm ≈ [0.887,0.095,0.952,0.126]), **1 filho View de
mesmos bounds**, contexto **WebView/adFrame**. O centro estrutural (993,252) coincide com o antigo
`anonymous_top_right`. Detector `subad_close.py` (`detect_subad_close` por proporções + `authorize`);
**detecção != autorização** (respeita o gate; rewarded sem completion ⇒ observe).

### 16.2 Integração / telemetria

Adicionado ao **normal close path** em `_fc_fluxo_applovin` (antes do reveal). Telemetria
`[FASTCLOSE][SUBAD_CLOSE]` (assinatura completa, log na 1ª detecção/mudança/click/5s) e
`[FASTCLOSE][SUBAD_CLOSE_CLICK]` (`xml_candidate_to_click_ms`). Config: `applovin_subad_close_enabled`
(default True), `..._max_per_ad` (2), `..._cooldown_s` (1.0), `..._pending_timeout_s` (8.0).
**Dedupe orientado a estado** (`subad_close.dedupe_same`): após clicar, não reclica o **mesmo
candidato (mesmos bounds)** enquanto a transição não ocorre (`action=observe reason=awaiting_transition`);
libera quando o candidato **desaparece**, os **bounds mudam**, MAIN/store, ou **safety timeout** (a
árvore inteira é ruidosa em WebView, então usa-se a persistência do candidato). Corrigido também o
`SESSION_START_BUG` (`_cycle.begin` em `processar_applovin_fast` quando a sessão abre via retry de banner).

### 16.3 Validação

- Offline `tests/test_subad_close.py`: **27/27** (replay do dump real positivo + negativos reais/
  sintéticos + resolução diferente + reward gate). Total offline: **150/150**.
- Real (1 device, 3–5 min): interstitial sub-ad → detectado → autorizado → clique `(993,252)` com
  **`xml_candidate_to_click_ms=0`** → `RESULT closed=True source=subad_close elapsed=10.2s` → MAIN →
  `CYCLE_COMPLETED` (antes ~70–90s via recovery). Reward gate preservado (rewarded sem completion →
  timeout). Bug corrigido: `TypeError float-None` por `skip_at` não setado no clique do SUB_AD_CLOSE.
- **Fora de escopo (registrado):** compact top control ">|" visível mas não exposto no XML
  (`COMPACT_TOP_CONTROL_VISIBLE_NOT_IN_XML`; o **reveal** resolve); variante ALRTB com
  `resource-id=close_button` (assinatura distinta — capturar XML em rodada futura).

**Status:** `SUBAD_CLOSE_FIX_PASS` (detecção + click + latência). Não iniciar C90.

## 17. RESET TREE — validação ao vivo 5/5 - 2026-10-02

Validação do modelo de reset por árvore (intervalo individual de CYCLE_COMPLETED), preservando o
estado QA. Overlay TEST-ONLY `config_qa_reset_tree_sln_v2.json` (delay 2–5s, reset 2–5 ciclos,
população=1, **quota 100%**, `max_concurrent_resets=1`, janela 1 min). Env
`SLN_QA_TEST_TARGET_RESET_TREES=5`, hard cap 45 min. Device `.44`, **sem intervenção manual**
(`manual_intervention=false`). `state/qa_devices` e `state/qa_coord.json` **preservados** (começou com
`reset_success=2`; alvo 5).

### 17.1 Resultado

`RESET_TREE_MODEL_VALIDATED_LIVE_5X` — **5/5 árvores**. Rodada de fechamento: **3 árvores novas**
(TREE 3–5), parada no target em **20.1 min** (`reset_success=5`).

| Tree | interval_drawn | completed | failed | sessions | RESET ID | CLEAR | RESET_SUCCESS | new interval |
|---|---|---|---|---|---|---|---|---|
| 1 (prévia) | 3 | 3 | 0 | — | OK | OK | count=1 | 3 |
| 2 (prévia) | — | — | — | — | OK | OK | count=2 | 3 |
| 3 | 3 | 3 | **4** | 7 | OK | OK | count=3 | 4 |
| 4 | 4 | 4 | 0 | 4 | OK | OK | count=4 | 3 |
| 5 | 3 | 3 | 0 | 3 | OK | OK | count=5 | (target) |

- **Invariante central:** em toda árvore `cycles_completed == interval_drawn` (3=3, 4=4, 3=3).
- FAILED cycles existiram (4 na TREE 3) e **NÃO** decrementaram o contador.
- RESET ID + CLEAR **sempre juntos** (`[RESET][ID] success` → `clear_data context=reset success`);
  novo intervalo sorteado após `RESET_SUCCESS`. Nenhum reset com `cycle_active=True`.
- Consolidado (TREE 3–5): completed=10, failed=4, sessions=14, stages=0, `reset_success`=5/5.
- Draws observados: [3, 4, 3] (novas) → intervalos em 2..5.
- Ownership `dup=0`; `[FASTCLOSE][START]`(14) == `[AD_SESSION][START]`(14) == `[QA][CYCLE_START]`(14)
  → **SESSION_START íntegro** (sem START sem sessão). `[FASTCLOSE][ERROR]=0`, `DUMP_ERROR=0`.

### 17.2 SUB_AD_CLOSE dedupe (ao vivo)

Ocorrências reais de SUB_AD_CLOSE autorizado (interstitials): cada uma → `SUBAD_CLOSE_CLICK` com
`xml_candidate_to_click_ms=0` e, no tick seguinte, `action=observe reason=awaiting_transition`
(**nenhum re-click imediato**); o 2º clique só ocorreu após o **safety timeout** (8s) sem transição.
Total 10 clicks / 5 `awaiting_transition` / 0 re-click imediato. `DEDUPE_LIVE_PASS`.

### 17.3 ALRTB close_button

`ALRTB_CLOSE_CAPTURED` (artefato `errors/fastclose_playable_close_onscreen_..._103917_452.xml`):
`View id=close_button` (pai `id=main`), bounds `[120,1773][162,1815]` (**inferior-esquerdo**),
**clickable=false**, filho `Image id=close_button_svg`. **No MESMO ad coexiste** o
`FrameLayout anônimo topo-direito [958,217][1028,287]` clicável — que o SUB_AD_CLOSE já usa para
fechar. Ou seja: o criativo ALRTB **é fechável** pelo detector atual; o `close_button` nomeado
(bottom-left, não-clicável) é outro controle. Detector ALRTB dedicado **não** foi implementado
(não alterar sem decisão). O compact ">|" continua `VISIBLE_NOT_IN_XML` (reveal resolve).

**Status:** `RESET_TREE_VALIDATION_PASS`. Versão funcional congelada. Próximo: C90 Capacity Probe
(**não iniciar automaticamente**).

## 18. CYCLE_ACCOUNTING_V2 - 2026-10-02

Nova regra autoritativa: **qualquer AD SESSION iniciada que atinja resultado terminal (COMPLETED ou
FAILED) consome exatamente 1 ciclo.** ABORTED (test end/watchdog kill/crash sem resultado terminal) e
**sem sessão** (NO_FILL/FAILED_TO_LOAD/banner sem abrir) **NÃO** consomem. Idempotente por `cycle_id`.

- Regra pura `qa_behavior.should_count_cycle(session_started, terminal_result)`; contador
  `QABehaviorScheduler.record_cycle_result(...)` (não usar `cycle_completed` como contador genérico).
  Funcional (`ad_session`: COMPLETED/FAILED/ABORTED) **separado** de contabilidade (COUNTED/NOT_COUNTED).
  Telemetria `[QA][CYCLE_RESULT]` + `[QA][CYCLE_ACCOUNTED]`; `CYCLE_COMPLETED` **não** representa falha
  (`CYCLE_FAILED`/`CYCLE_ABORTED`). `_cycle_abort_if_active()` marca ABORTED em guard de teste/exceção.
- Reset tree passa a `accounted_cycles == interval_drawn`; **SUPERSEDED**: "FAILED não decrementa reset".
  RESET ID + CLEAR permanece acoplado. Summary: `cycles_started/accounted/completed/failed/aborted`.
- **Offline:** `tests/test_qa_behavior.py` (COMPLETED/FAILED contam; ABORTED/sem-sessão não;
  idempotência por cycle_id; seq 4→3→2→1→0) e `tests/test_ad_session.py` (abort). **170/170** totais.
- **Smoke 10 devices** (overlay `config_qa_10dev_accounting_sln_v2.json`): **`accounted == completed +
  failed`** (ex.: 54=37+17; reforço 35=30+5); FAILED consumiu contador (ex.: device 4 completed + 4
  failed → 2 trees); `aborted=0`; `reset_success==clear_ok==id_ok` por device; ownership íntegro.
  12 devices distintos produziram RESET TREES ao longo das validações.

### 18.1 Cobertura separada — INVENTORY_NO_SESSION != ACCOUNTING_FAILURE

A disponibilidade de anúncio é medida separadamente da correção de contagem. Métricas (runner
`accounting_smoke.py`): `devices_requested`, `devices_with_session`, `devices_with_accounted_cycle`,
`devices_with_tree`, `devices_no_inventory`. Um device **NO_INVENTORY** (0 anúncios / 0 AD_SESSION /
0 ciclos, ex.: `.27`,`.107`) **não** é falha de contabilidade — é `INVENTORY_COVERAGE`.
`FUNCTIONAL_COVERAGE` = entre os devices com sessão, `accounted == completed + failed`.

**Status:** `CYCLE_ACCOUNTING_V2_PASS` — offline **170/170**; ao vivo: COMPLETED conta 1, FAILED conta 1,
ABORTED não conta, no-session não conta, sem double-count, stages não contam individualmente,
`reset_interval` usa `cycles_accounted`, RESET ID+CLEAR acoplados, nenhum reset durante `cycle_active`,
ownership íntegro. Semântica **congelada**. Não iniciar C90; sem nova alteração funcional sem evidência
de regressão.

> **SUPERSEDED (2026-10-02):** a invariante de §17 "`cycles_completed == interval_drawn`" foi
> substituída por "`cycles_accounted == interval_drawn`" (FAILED conta). Ver §18.



## 19. DEVICE AGENT E3.2B - LIFECYCLE/REBOOT (2026-11-15)

Matriz em 1 device (`.124`, SM-G975F/Android 12, Ethernet): S7 PASS, S2 PASS (FGS), S3 PASS (start
manual 1,0 s), S4 LIMITATION (Wi-Fi nao se aplica em eth0), S4b PASS (perda de transporte 60 s,
reconexao 25,0 s), S5 PASS (controller offline 130 s, reconexao 1,5 s, OFFLINE age 126,8 s, 5
tentativas, CPU 0,0%, PSS 62.658 KB), S8 PASS (install -r, 2,1 s), S6 PASS (AUTH_REVOKED, 1
tentativa/48 s), S9 PASS (pm clear -> novo pairing, possible_reenrollment), S1 PASS (reboot +
dismiss keyguard: BOOT_RECEIVED, auto hello 51,4 s). Identidade/token persistem; `pm clear` exige
re-pair; BOOT_COMPLETED retido ate unlock (pre-unlock exigiria directBootAware+LOCKED_BOOT_COMPLETED).
Regressao 26/26. Detalhes: `docs/E32B_LIFECYCLE.md`.

## 20. DEVICE AGENT E3.3A - TAP/SWIPE (2026-11-15)

Ops `TAP`/`SWIPE` implementadas e serializadas (controller + APK), com bounds por display dinamico,
capabilities condicionais ao Accessibility e resultado apenas no callback. Matriz 18/18 PASS;
30 TAP 0 falhas (avg 65,10ms) e 30 SWIPE 0 falhas (avg 284,54ms); ADB QA tap 180,85ms/swipe
482,03ms (0 falhas). Disconnect pos-dispatch => UNCERTAIN sem reenvio; dedupe e lifecycle OK.
Rotacao nativa bloqueada pelo ROM, validada via override de resolucao (`wm size`) com TAP correto.
41/41 testes. Detalhes: `docs/E33A_GESTURES.md`.

## 21. DEVICE AGENT E3.3A.1 - HARDENING (2026-11-15)

Hardening de robustez: gesture FSM com timeout pre-dispatch sem side effect, pos-dispatch
UNCERTAIN e callback tardio isolado; SQLite com transicoes monotonas (terminal nao regride);
reconnect single-flight com geracao; RESULT vinculado (agent/device/op/protocolo) e status
allowlist; command_id UUID4; START_APP allowlist server-side; QA debug-only; Provision com gate
shell/root/self. Testes 50/50 + 14 selftests + 13/13 reais no .124, incluindo pre/post dispatch
timeouts e benchmark 30+30 sem falhas. Baseline de producao E3.3A = PASS com riscos documentados.
Doc: `docs/E33A1_HARDENING.md`.

## 22. DEVICE AGENT E3.3A.2 - FINAL BASELINE FIX (2026-11-15)

Controller passou a ser autoridade primaria de dedupe: reserva atomica do command_id no SQLite
antes de qualquer send; duplicates retornam cached/DUPLICATE_IN_PROGRESS e conflito de UUID
(agent/device/op) retorna COMMAND_ID_CONFLICT. Transicoes de comando estritas. Provision/Qa
debug-only (release merged sem ambas; debug exported=true), sem gate por referrer. Testes 59/59;
smoke .124 PASS (provisioning ADB, PING, TAP, mesmo UUID executando 1x). Baseline E3.3A = FROZEN.
Doc: `docs/E33A2_BASELINE_FIX.md`.

## 23. E3.4 - ADB PARITY AUDIT (2026-11-15)

Auditoria read-only: Agent cobre gestos e status, mas nao a percepcao (UI tree/screenshot/activity)
nem force-stop/pm clear (privilegiados). Scores 40,3% primitivas / 37,5% hot path / 21,7% chamadas
estimadas; 6 blockers. Recomendacao E3.5: GET_UI_TREE + CAPTURE_SCREEN + foreground/activity em
modo sombra, sem integrar no main; decidir device-owner vs ADB sidecar para stop/clear. Doc:
`docs/E34_ADB_PARITY_AUDIT.md`; JSON: `state/device_agents/e34_parity_inventory.json`.

## 24. E3.5 - PERCEPTION PARITY (2026-11-15)

GET_UI_TREE + CAPTURE_SCREEN + foreground/activity/status extension implementados read-only.
100/100 trees (10 nodes, avg 49ms), 30/30 screenshots (diff 0, dims/hash OK), 300ms cadence 3,33/s
sem falhas, CAPTURE 2/s OK. Shadow: Main/reward FULL; AppLovin playable/WebView 13/14 FULL +
1 transitoria, activity/package 14/14; bounds essenciais exatos (containers diferem por inset ADB).
Limitacao: uiautomator2 desabilita Accessibility, shadow paralelo impossivel; usado comparativo
sequencial sem alterar main/bot. STOP/CLEAR/logcat/FIND_ELEMENT pendentes. Testes 64/64. Doc:
`docs/E35_PERCEPTION_PARITY.md`; JSON: `state/device_agents/e32b/e35_report.json`.

## 25. E3.5B - SHIZUKU PRIVILEGED BRIDGE (2026-11-15)

Bridge Shizuku READY/UID 2000; STOP_APP e CLEAR_APP_DATA reais PASS no .124 (dedupe fisico,
UNCERTAIN pos-dispatch/bridge death, restart Shizuku/Agent/controller). UserService AIDL tipado
sem shell generico, allowlist/denylist server+agent. Permission revoke nao validado (limitacao).
Regressao 68/68. Doc: `docs/E35B_SHIZUKU_BRIDGE.md`; report: `e35b_report.json`.

## 26. E3.6 - DECISION PARITY / SHADOW BOT (2026-11-15)

Replay host-only dos snapshots ADB/Agent com os mesmos parsers do main. 14 casos reais 100%
em state/candidate/auth/decision, 0 critical/0 acoes do Agent; MAIN/reward/AppLovin/store/session
PASS. Close estrutural/anonimo, SUB_AD, countdown, consent e endcard sem fixture real =>
PARITY_PARTIAL/COVERAGE_PENDING, sem PASS e sem integracao. Testes 76/76. Doc:
`docs/E36_DECISION_PARITY.md`; JSON: `state/device_agents/e36_parity.json`.

## 27. E3.6R - REAL DEVICE DECISION PARITY (2026-11-15)

Captura real read-only 300s: 552 snapshots, shadow_actions=0. Fixtures reward/return_main/
playable/sub_ad_close/store; SUB_AD_CLOSE bounds exatos e CLICK ADB=Agent. Replay 19 casos:
state/candidate/auth/decision 100%, critical 0; CONSENT/COUNTDOWN/ENDCARD/structural-anonymous
pendentes. Veredito PASS_WITH_COVERAGE_PENDING; sem integracao; testes 79/79. Doc:
`docs/E36R_REAL_PARITY.md`.

## 28. E3.7 - DEVICE BACKEND ABSTRACTION (2026-11-15)

`DeviceBackend` tipado (`adb` default, `agent` opt-in); `AgentBackend` le via controller e
bloqueia TODA mutacao em shadow, sem fallback ADB e sem shell. Contrato 7/7 e regressao 86/86.
Smoke real: ADB 74 nodes/1080x2280 read-only; Agent 5 nodes/screenshot, 7 mutacoes BLOCKED,
0 acoes. Main roteia leituras centrais e tap/back/home/start/stop/clear; reset com clear
UNCERTAIN nao reexecuta automaticamente. Ciclo completo via Agent nao executado. Veredito
**PASS_DEFAULT_ADB_CONTRACT**. Doc: `docs/E37_DEVICE_BACKEND.md`; report:
`state/device_agents/e37_report.json`.
