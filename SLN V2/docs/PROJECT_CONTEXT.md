# PROJECT_CONTEXT.md — SLN V2 (histórico consolidado)

Por que cada regra existe. Se você for mexer em algo, leia aqui primeiro.

---

## 1. Origem do Fast Close

O fechamento de anúncios era feito por um controlador legado que fazia várias consultas
uiautomator por ciclo, usava `UiObject.click()` (com `must_wait` de até 20s) e espaçava as
tentativas em 1s. O resultado era lento, imprevisível e cheio de cliques sem evidência.

O **Fast Close Watcher** substituiu isso:

- **1 dump uiautomator por tick** (~300ms, `fast_close_tick_ms`);
- **parse local do MESMO XML** (nenhuma consulta extra no dispositivo);
- **ação imediata** com `d.click` em coordenadas derivadas de `bounds`;
- `UiObject.click()` **nunca** no caminho crítico.

Rollback: `fast_close_enabled=false` + `use_legacy_applovin=true`. O código legacy foi
mantido de propósito.

## 2. Pílula `Google Play` e o SKIP no lado esquerdo

A pílula "▶| Google Play" no topo é o controle de fechamento/avanço do anúncio. Ao clicar
no **texto**, o bot abre a Play Store (o texto é o CTA do anunciante).

Solução: tratar o texto como **âncora geométrica**. O clique vai no **lado esquerdo** da
pílula, num ponto entre `pill_left` e `text_left` (ex.: x≈62, y≈172 em 1080x2280). O
container é encontrado subindo ancestrais pequenos (<=40% largura, <=12% altura).

Nunca: `d(text="Google Play").click()`, nem o centro do texto.

Estabilidade: a pílula precisa estar em **2 dumps consecutivos**
(`applovin_skip_min_pill_age_s≈0.25s`) antes do SKIP. Sem sleep extra.

## 3. Reveal = contingência, não rotina

Alguns anúncios não mostram a pílula espontaneamente. O *reveal* é um toque proporcional na
**faixa superior** (`top_reveal_y_ratio=0.075`, nunca 0.45h) depois que a pílula já foi vista
neste anúncio e sumiu.

Regra crítica: **reveal antes da primeira pílula abriu a Play Store** num criativo com
contagem regressiva (o toque caiu no anúncio). Por isso o reveal só roda se:

- a pílula já apareceu neste anúncio e sumiu, **ou**
- a recompensa foi concluída (countdown terminou e a pílula não veio em `pill_wait_s`), **ou**
- é interstitial **confirmado por evento** e passou `applovin_interstitial_control_wait_s=20s`
  (contingência; pílulas espontâneas foram observadas em T+10..18s).

Se o reveal abrir a loja, `reveal_blocked=True` para o resto do anúncio.

## 4. FORMAT SHOWED e a correção do logcat lento

O tipo real do anúncio vem do **evento do SDK**, não de heurística visual.

- `app_event`: `AdHelperLog: FORMAT=X EVENT=SHOWED`;
- `cas_event`: `CAS.AI: X Ad Impression ... CreativeId: Y UnitId: Z`.

`EVENT=LOADED` **não** define formato.

**Bug 1 — logcat lento:** `adb logcat -d -s TAG:D` levava ~12.5s e estourava o timeout de 4s
(sempre `TimeoutExpired`). Fix: `adb logcat -d --pid=<pid do app>` → ~600ms.

**Bug 2 — evento antigo vazando:** um `SHOWED` do anúncio ANTERIOR caía na janela de 30s e
contaminava o atual. Fix: piso de relógio do aparelho (`_fc_fmt_session_perf` + `min_ts` no
parser) — eventos anteriores ao início da sessão são descartados.

Mesmo problema existia em `_fc_eventos_pid` (associava `REWARD_EARNED`/`CLICKED` de anúncios
antigos) → filtro por piso.

## 5. Interstitial validado

Com `FORMAT=INTERSTITIAL` confirmado por evento, **não há reward gate**: pílula visível +
gate aberto = SKIP imediato. Sem pílula espontânea até `applovin_interstitial_control_wait_s=20s`
→ `REVEAL_TOP` como recuperação.

## 6. Rewarded/countdown validado

O gate abre por **conclusão observada**, nunca por tempo:

- **candidato**: nó do app com texto só numérico (<=120), região inferior (y >= 0.80h), nó
  pequeno (<=12% do lado), visível;
- **confirmação**: 2 transições descendentes consecutivas (3 valores distintos), queda de
  1..3 unidades, intervalo entre 0.5s e 4.0s; valor repetido não conta;
- **fim**: confirmado + ausente por 3 ticks + último valor <=2;
- **COMPLETED**: fim + pílula espontânea, ou fim + `pill_wait_s` esgotado.

`applovin_reward_safe_min_watch_seconds=60` é telemetria quando
`applovin_reward_completion_required=true`. Tempo NÃO abre gate.

## 7. `REWARD_EARNED` é pós-fechamento

`REWARD_EARNED` chega ~30–60ms **depois** do bot já ver a MainActivity. É confirmação
póstuma, não gate. Nunca usar como sinal de que "pode fechar".

## 8. Classes de rewarded sem countdown (gap conhecido)

Duas classes estruturais distintas (nunca usar `CreativeId` como assinatura):

- **Playable `MW_PLFRAME`** (Cocos2d/canvas): `mv_close_btn` existe no XML mas OFFSCREEN
  `[0,2280][1080,2280]`; nunca transicionou para onscreen em 3+ ocorrências até 180s. Sem
  sinal de conclusão.
- **WebView/`adFrame`** (HTML): close anônimo no topo-direito visível todo o tempo — invisível
  às camadas XML locais, mas detectável por IA (988,253) e por `anonymous_top_right` (993,252).
  É a classe que o recovery fecha.

`rewarded_no_countdown_diag_timeout_s=180` **estende a observação** (nunca libera SKIP por
tempo). `_fc_diag_rewarded_sem_countdown` loga `[FASTCLOSE][DIAG_REWARD]` /
`[FASTCLOSE][PLAYABLE_DIAG]`, monitora a transição de `mv_close_btn` e salva PNG+XML em
T+20..T+120. `playable_diag_click_enabled=false` em produção.

**Proibido:** fallback temporal (`elapsed>=N → reward_completed=True`).

## 9. Recovery Close Cascade

Executa **só quando o fluxo normal falhou/timeout**, antes do escape HOME+force-stop.
**Nunca declara `reward_completed`.**

Camadas: `structural → resource_id → content_desc → text → XPath → anonymous_top_right → AI`.

- `RECOVERY_CLOSE_CONFIRMED_LIVE`: creative `6863651`, rewarded sem countdown → TIMEOUT aos
  180s → recovery clicou `anonymous_top_right` em **(993,252)** → voltou à MAIN → saldo
  **delta +10** → `REWARD_EARNED` pós-fechamento → `reward_preserved=True`. Sem escape.
- Configs: `recovery_close_enabled=true`, `recovery_close_ai_last_enabled=true`,
  `recovery_close_observe_s=5.0`, `recovery_close_max_attempts=1`.

**Não promover** o close do WebView para gate precoce — decisão separada, com nova evidência.

## 10. Store/BACK

A loja abrir até 3s após o SKIP é **esperada** (`applovin_expected_store_window_s=3.0`,
`EXPECTED_STORE_AFTER_SKIP`) — não é falso clique. BACK orientado por estado: back inicial
após 1.5s, settle 0.8s, retry 1.0s, máximo 3 backs. O dump é a verificação: só manda outro
BACK se AINDA está na loja. Endcard detectado → nenhum BACK.

## 11. Structural endcard X

O X do endcard foi observado como estrutura **anônima**: View ~quadrada no topo-direito
`[958,135][1039,219]` + Image `[984,164][1013,191]` → centro **(998,177)** em 1080x2280.
Nenhum id/text/desc. Por isso a camada `_fc_find_endcard_close_structural` é a principal e a
cascata completa (XML `best` → `achar_botao_fechar_no_dump` → listas antigas → XPath) só roda
se ela falhar. YOLO só após `applovin_endcard_ai_grace_s=4.0` com tudo local falhando.

Baseline: **100% dos endcards fechados por `structural`**.

## 12. IA detecta o X, mas confunde a pílula (filtro protege)

Em `/detect` sobre screenshot real: o **X verdadeiro vem com conf=0.75** (passa o filtro). A
**pílula é detectada como X com conf=0.58** e é **rejeitada por `x_region`** (exige
`x>=0.70w`, `y<=top`, `conf>=0.25`). Esse falso positivo é esperado e **protege** o fluxo.

**Não relaxar os filtros.** IA é fallback lazy: nunca no caminho rápido, nunca no CTA do vídeo.

## 13. Reset obrigatório: RESET ID → FORCE STOP → PM CLEAR → RELAUNCH

Ordem inegociável, implementada em `reset_identity_and_app_state()`:

```
RESET ID (reset_id, valida conclusão)
→ [RESET][APP] force_stop
→ pm clear <package> (retry 3x, valida "Success" + exit_code 0)
→ [RESET][APP] ready_for_launch
→ somente o controlador superior abre o app
```

Regras:

- **RESET SEM CLEAR = CICLO INCOMPLETO.**
- Reset falhou → `[RESET][ERROR] stage=id_reset`, app não abre.
- `pm clear` falhou → `[RESET][ERROR] stage=app_data_clear`, retry 3x, app não abre.
- `skip_launch=True` impede abrir o app em ciclo com reset falho.
- Métricas: `reset_id_ms`, `clear_data_ms`, `total_reset_cycle_ms`.
- `pm clear` devolve o app ao estado de primeira execução (consents, telas iniciais,
  permissões, init CAS podem reaparecer). `_fc_coins_last` é zerado (saldo antigo inválido).
- `d.shell("pm clear ...")` retorna `ShellResponse` (`.output`, `.exit_code`), **não** `str`:
  validar via `r.output.startswith("Success")`.

## 14. Outros bugs corrigidos (não recriar)

1. `adb logcat -d -s` lento (12.5s) > timeout 4s → `TimeoutExpired`. Fix: `--pid`.
2. `from datetime import datetime` sem `timedelta` → `AttributeError` no FORMAT.
3. `SHOWED` do anúncio anterior vazando na janela de 30s → piso device-clock.
4. `_fc_eventos_pid` associando eventos de anúncios antigos → filtro por piso.
5. `_fc_tem_cta` com `parent=None` → `AttributeError` na camada anônima do recovery.
6. `_fc_find_close_btn` não achava close OFFSCREEN degenerado `[0,2280][1080,2280]`
   (bounds descartados) → parse manual + flag `onscreen`.
7. `d.shell("pm clear")` retorna `ShellResponse` (objeto) → parser corrigido.

## 15. Escala (persistent logcat, adaptive tick, watchdog) e capacidade

Contexto: a farm tem ~115 devices. O baseline é **1 processo Python por device** + 1 serviço central
de IA. A escada medida: 30 → 60 → 90.

### 15.1 Persistent logcat (default OFF)

Motivo: cada lookup de FORMAT abria 2–3 `adb` (`date`/`pidof`/`logcat`) → centenas de subprocessos/s
em escala. O **LogcatReader** mantém **1 stream `adb -s <dev> logcat` por bot** e responde o FORMAT
**da memória** (ring buffer), com **piso de sessão** por `perf_counter` (mesmo anti-vazamento de
SHOWED). Resultado A/B/C (30 dev): subprocessos 1580→576 (−64%), FORMAT unknown 0.7%→0. **Custo:**
+1 processo `adb logcat` por device (≈60 em 60, ≈90 em 90) + CPU.

### 15.2 Adaptive tick (default OFF)

Motivo: 1 dump/tick (~300ms) × N devices ≈ centenas de dumps/s. O tick varia por **estado** da máquina
(pill/endcard/countdown) com **burst** ao entrar em estado crítico. Resultado: dumps/min/device 66→32
(−52%), reconnects/errors→0, **sem** redução de CPU (o que cai é o nº de dumps). Em C60/C90 o dump p95
ficou **saudável** (168/162 ms; **0 dumps >5s**).

### 15.3 Watchdog / single-owner (o bug que invalidou o 1º C60)

Sintoma: C60 com **~96 bots** e **~227 streams logcat**. Causa-raiz: ao matar um bot no Windows, o
**filho `adb logcat` não morre com o pai** → órfãos; e o restart antigo (`taskkill /PID` pelo pid do
manifest) matava só o principal, relançando em corrida. Confirmado: **291 logcat órfãos com 0 bots**.

Correção (infra, sem tocar no fluxo de anúncio):
- `sln_proc.py`: identificação **exata** (`basename=="main_sln_v2.py"` E `argv[i+1]==device`),
  `terminate_worker_verified` mata a **árvore** e confirma ZERO.
- `watchdog.py`/`watchdog_run.py`: registry/generation/instance, restart **transacional**, lock por
  device, grace de startup/pós-restart, preflight (`DUPLICATE_WORKER_PREFLIGHT_FAIL`).
- **Progresso funcional** ≠ só fechar anúncio: o ticker avança `last_progress` em operações legítimas
  (inclusive sleeps longos de backoff/no-fill) e **congela** só com `dump_hierarchy` travado (>30s).
  Sem isso, `stale_s < sleep de backoff` reiniciava device saudável (falso positivo).
- Telemetria: `no_fill` publicado **sempre** (antes só com `idle_backoff_enabled`); `ai_over_4s_count`
  por requisição (não máximo cumulativo).

Validação: fault injection **1** e **5** devices (gen incrementa, 1 worker/1 logcat sempre), lifecycle
**30** (dup=0/orph=0), e **C60/C90** com `duplicate=0`/`orphan=0` do início ao fim.

### 15.4 Capacidade (C60 clean = baseline; C90 = probe)

- **C60 clean (30 min):** 60/60 bots, dup/orph=0, watchdog restart 1, reconnect 0, 0 dumps >5s.
  CPU avg 51.1 / p95 80.1 / p99 88.8, >90% ≈1%.
- **C90 (30 min):** 90/90, dup/orph=0, watchdog **0/0/0**, reconnect 0, 0 dumps >5s, FORMAT 0.26%,
  close 85.8% (efeito de inventário), reward preservado. CPU avg 58.4 / **p95 85.2 / p99 93.3**,
  >90% 3.2% (45s). **Sem early stop.**
- **Curva CPU p95:** 35.8 → 80.1 → 85.2; **avg:** 26.6 → 51.1 → 58.4. O salto 30→60 estava inflado
  (arranque/backfill); **a inflexão não aparece até 90** (60→90 só +5pp de p95).
- **Gargalo dominante = inventário** (`no_fill_backoff` avg **68.4/90**; só 4.2 ads/device), não CPU.
- **Decisão:** `SINGLE_HOST_CAPACITY_APPROACHING`. `MAX_OBSERVED=90`; `RECOMMENDED≈60/host`;
  **sharding** antes de 120; **não** iniciar 120 automaticamente. Hipótese antiga 25–40/host descartada.
- C60 anterior (com o bug de duplicação): `C60_INVALID_FOR_CAPACITY`, `FUNCTIONAL_SIGNALS_VALID`.

## 16. QA Realistic Behavior (simulação de frota heterogênea)

Contexto: para teste de robustez, antifraude, comportamento distribuído, resiliência, cobertura de
estados e benchmark, queremos uma frota que **não** clique/limpe/resete/instale toda ao mesmo tempo.

**Por que separado:** essa camada decide *quando* o device age (delay, reset, install) — é
**agendamento/simulação**, não a lógica do anúncio. Fica **fora** da state machine do Fast Close
(não toca reward gate/FORMAT/pill/Store/recovery/IA/persistent logcat/adaptive tick/watchdog).

Decisões de design:
- **Estado por device** (`state/qa_devices/<safe>.json`, atômico) — pertence ao device, não ao host
  (permite mover entre shards). Persistente entre restart de bot/watchdog/host.
- **Seed por device** (`sha256(global_seed|device_id)`) — cada device tem sequência própria;
  reproduzível quando `qa_random_seed` é definido. Nunca um sorteio global.
- **Delay individual** (10–60s) — com sleep **cancelável** (blocos), não sleep gigante.
- **Reset por intervalo individual** (2–30 ciclos), não por probabilidade/ciclo. `RESET` usa o helper
  consolidado `reset_identity_and_app_state()` (RESET ID + `pm clear` **acoplados**).
- **Quota populacional** (5–25%/janela) como **limite** (não probabilidade), com concorrência limitada
  e jitter — evita sincronização. "Aleatório entre os DUE" é aproximado pela ordem randomizada (1
  processo/device não tem coordenador central); quota/concorrência são reais.
- **Install** (5–10%) com **cooldown móvel de 24h** (`last_install_at`), revalidado atomicamente;
  sucesso só é gravado com instalação **confirmada**.

Validação: `tests/test_qa_behavior.py` (41), `simulate_qa_behavior.py` (20/50 devices — sem
sincronização), smoke real 3 devices (reset+clear acoplados) e `find_button_devices.py` (97/115 com o
botão). Detalhe em `docs/QA_REALISTIC_BEHAVIOR.md`. Default **OFF**; perfis benchmark/scale não ativam.

## 17. Ciclo / AD Session (modelagem de contabilidade)

Correção de semântica: **1 clique em "Assistir anúncio" = 1 CICLO = 1 AD SESSION** — não "1 tela de
anúncio", e não "Ad1→Ad2 = duas solicitações". Uma sessão pode conter **N STAGES** (vídeo, playable,
endcard, Store, tela intermediária, novo creative). **Fechar um controle NÃO finaliza o ciclo.**

Motivo: a contagem anterior incrementava o ciclo a cada passagem do loop, tratando stages da mesma
sessão como ciclos distintos — o que inflava o contador usado pelo intervalo de reset do QA
(`qa_reset_interval_*`) e deturpava a telemetria.

Regras (em `ad_session.py`, integrado no loop):
- `begin` quando a sessão realmente inicia (após o clique, ao sair da MAIN).
- `continue_stage` quando fechou um controle mas **não** voltou à MAIN → mesmo ciclo, novo stage.
- `complete` **só** com MAIN confirmada ao final; `fail` em **escape forçado** (HOME+force-stop) —
  retorno forçado **não** equivale a conclusão (mesmo que o app seja reaberto depois).
- `qa.cycle_completed()` (e o decremento de `cycles_until_reset`) **só** em COMPLETED.
- **Um clique por ciclo** (nunca nova solicitação por stage). Não altera reward/FORMAT/pill/Store/BACK/
  recovery — é modelagem/telemetria. Testes: `tests/test_ad_session.py`.

> **SUPERSEDED (2026-10-02):** a regra "só COMPLETED decrementa; FAILED não decrementa" foi
> substituída. Ver §20 (contagem: COMPLETED **e** FAILED contam 1 ciclo; ABORTED/sem-sessão não contam).

## 18. SUB_AD_CLOSE (WebView sub-ad): assinatura estrutural confirmada

`SUB_AD_CLOSE_SIGNATURE_CONFIRMED`. Em sub-ads WebView, o controle de fechamento aparece no
**topo-direito** como um **FrameLayout anônimo clicável**:

```
android:id/content > FrameLayout > FrameLayout
   ├─ RelativeLayout > WebView > View > TextView resource-id=adFrame
   └─ FrameLayout [958,217][1028,287] clickable=true  id/text/desc=""   <-- CLOSE
      └─ View [958,217][1028,287]
```

Assinatura: `clickable anonymous FrameLayout` + geometria pequena topo-direito
(norm ≈ [0.887,0.095,0.952,0.126] em 1080x2280) + **1 filho View de mesmos bounds** + contexto
WebView/adFrame. Detector em `subad_close.py` (`detect_subad_close`, proporções, tolerâncias
conservadoras) + wrapper `_fc_detectar_subad_close`. **DETECÇÃO != AUTORIZAÇÃO**: usa o gate atual
(rewarded sem countdown ⇒ `observe`, não clique).

Motivo: antes, esse close **não entrava no normal path**; só o recovery `anonymous_top_right`
(o centro fixo **993,252** = exatamente o centro estrutural do botão real) o pegava, ~90s depois.
Também disparava `REVEAL_TOP` (x=540,y=171) que abria a Play Store.

Resultado validado (1 device): interstitial sub-ad detectado → autorizado → clique no centro
(`xml_candidate_to_click_ms=0`) → `RESULT closed=True source=subad_close elapsed=10.2s` → MAIN →
`CYCLE_COMPLETED` (antes ~70–90s). Config: `applovin_subad_close_enabled` (default True),
`applovin_subad_close_max_per_ad`, `applovin_subad_close_cooldown_s`. Telemetria
`[FASTCLOSE][SUBAD_CLOSE]` (assinatura completa) e `[FASTCLOSE][SUBAD_CLOSE_CLICK]`. Testes:
`tests/test_subad_close.py` (replay real positivo/negativos + sintéticos). **Não** cobre o
compact top control ">|" (VISIBLE_NOT_IN_XML, tratado por reveal) nem eventual variante com
`resource-id=close_button` (ALRTB) — investigação futura.

## 19. RESET TREE — modelo validado ao vivo 5/5

`RESET_TREE_MODEL_VALIDATED_LIVE_5X` (2026-10-02). O contador de reset do QA conta **CICLOS
COMPLETOS** (não stages/telas/X). Uma árvore: intervalo sorteado (2..30; TEST 2..5) → exatamente N
`CYCLE_COMPLETED` → `remaining=0` → RESET_DUE → RESET_SELECTED (quota) → RESET ID → `pm clear`
validado → RESET_SUCCESS → **novo intervalo sorteado**. FAILED não decrementa.

Validação (device `.44`, overlay TEST-ONLY `config_qa_reset_tree_sln_v2.json`, população=1, quota
100%, env `SLN_QA_TEST_TARGET_RESET_TREES=5`, cap 45 min, sem intervenção manual): **5/5 árvores**;
em todas `cycles_completed == interval_drawn`; RESET ID+CLEAR sempre juntos; novo intervalo após
success; `[FASTCLOSE][START]==[AD_SESSION][START]==[QA][CYCLE_START]` (SESSION_START íntegro);
ownership dup=0. `SUB_AD_CLOSE` dedupe ao vivo (`awaiting_transition`, sem re-click imediato).
`ALRTB_CLOSE_CAPTURED`: o `close_button` ALRTB é bottom-left `[120,1773][162,1815]`, **não-clicável**,
e coexiste com o FrameLayout anônimo topo-direito (que o detector já fecha). Ver `docs/CURRENT_STATE.md §17`.

## 20. Contagem de ciclo — CYCLE_ACCOUNTING_V2 (2026-10-02)

Nova regra autoritativa: **qualquer AD SESSION iniciada que alcance resultado terminal, COMPLETED
ou FAILED, consome exatamente 1 ciclo.** ABORTED (test end/watchdog kill/crash no meio) e "sem
sessão" (NO_FILL/FAILED_TO_LOAD antes de abrir) **NÃO** consomem. Idempotente por `cycle_id`.

- Regra pura: `qa_behavior.should_count_cycle(session_started, terminal_result)`. Contador:
  `QABehaviorScheduler.record_cycle_result(...)` (não usar `cycle_completed` como contador genérico).
- SEPARAÇÃO: funcional (COMPLETED/FAILED/ABORTED via `ad_session`) × contabilidade (COUNTED/NOT_COUNTED).
  Telemetria `[QA][CYCLE_RESULT]` + `[QA][CYCLE_ACCOUNTED]`; `CYCLE_COMPLETED` **não** representa falha.
- Reset tree: `accounted_cycles == interval_drawn` (FAILED conta). Reset ID + CLEAR permanece acoplado.
- Summary: `cycles_started/accounted/completed/failed/aborted`.
- **SUPERSEDED:** "FAILED não decrementa reset".
- Validação offline: `tests/test_qa_behavior.py` (accounting) + `tests/test_ad_session.py` (abort).
- **`CYCLE_ACCOUNTING_V2_PASS`** (offline 170/170; smoke 10 devices com `accounted == completed+failed`
  = 54=37+17 e 35=30+5; FAILED conta; ABORTED/no-session não; sem double-count). **INVENTORY_NO_SESSION
  != ACCOUNTING_FAILURE:** device sem anúncio (0 AD_SESSION) é `INVENTORY_COVERAGE`, não falha de
  contagem. Cobertura medida em eixos separados: `devices_requested / with_session / with_accounted /
  with_tree / no_inventory`. Semântica **congelada** (sem alteração funcional sem evidência de regressão).
