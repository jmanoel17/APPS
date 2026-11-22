# AGENTS.md — SLN V2

Guia curto para **qualquer agente** que for editar este projeto.

## LEIA ANTES DE EDITAR (obrigatório)

Nesta ordem:

1. `AGENTS.md` (este arquivo) — invariantes. Não mude isto sem evidência.
2. `docs/CURRENT_STATE.md` — o que está validado hoje e qual é o próximo trabalho.
3. `docs/PROJECT_CONTEXT.md` — histórico consolidado (por que cada regra existe).
4. `docs/SCALING_140.md` — plano de escala, riscos e o que **ainda não** foi medido.
5. `MEMORIA_SLN_V2.md` — memória detalhada de sessão (complementar).

Só depois de ler os cinco, edite código.

## MÉTODO (não pule etapas)

```
OBSERVE → MEASURE → MINIMAL FIX → REGRESSION → REAL DEVICE
```

Explicar abaixo está o que cada etapa significa: observar sem tocar, medir antes de mudar, corrigir só o problema, rodar regressão dos fluxos relacionados, e validar no aparelho real. Nenhuma mudança entra "porque parece melhor".

## INVARIANTES FUNCIONAIS

- **FORMAT explícito é autoridade.** `AdHelperLog: FORMAT=X EVENT=SHOWED` (app_event) e `CAS.AI ... Ad Impression` (cas_event) vencem o classificador visual. Precedência: app_event > cas_event > visual fallback > unknown.
- **`EVENT=LOADED` não define o anúncio atual.** Só SHOWED/impression definem formato.
- **`Google Play` é âncora da pílula, não alvo do clique.** O SKIP é no lado ESQUERDO da pílula (entre `pill_left` e `text_left`). Nunca clicar no texto nem no centro.
- **Interstitial confirmado pode usar SKIP imediatamente** quando a pílula está estável (2 dumps consecutivos, `applovin_skip_min_pill_age_s`). Interstitial confirmado = sem reward gate.
- **Rewarded normal continua usando countdown completion.** O gate abre com `REWARD_COMPLETED` (countdown confirmado + terminou + pílula), nunca por tempo.
- **`REWARD_EARNED` é confirmação pós-fechamento**, não gate.
- **Não criar fallback `elapsed >= N => reward_completed`.** Proibido. Tempo nunca abre o gate.
- **Recovery é locator pós-falha, não reward gate.** Ele NUNCA declara `reward_completed`; apenas tenta encerrar a tela antes do escape HOME+force-stop.
- **Store/BACK é orientado por estado.** Verifica o dump antes de cada BACK; settle entre BACKs; nunca sequência cega. Endcard detectado = não manda outro BACK.
- **Structural X é a camada principal do endcard.** A cascata (structural → XML → legado → XPath → YOLO lazy) existe para o caso de falha, não o contrário.
- **IA é fallback.** Nunca no caminho rápido, nunca no CTA do vídeo, nunca à frente das camadas locais.
- **Reset de identidade exige limpeza dos dados antes do relaunch.** RESET ID → force-stop → `pm clear` validado → estabilização → relaunch pelo controlador superior. RESET SEM CLEAR = CICLO INCOMPLETO.
- **Legacy não roda junto com Fast Close.** Um único controlador AppLovin por anúncio; sem reentrada.
- **Nenhum clique cego.** Todo clique tem evidência no dump atual (bounds) ou é um reveal proporcional validado.
- **Consentimento CAS: clicar "YES, I AGREE" sempre que aparecer.** O diálogo "Improve Your Ad
  Experience" bloqueia o reward; é tratado a cada ciclo do loop, sem filtro de `package`, alvo
  fixo no botão de aceite. `cas_consent_action=accept`.
- **Novas evidências devem atualizar os docs** (`CURRENT_STATE.md` primeiro).

## INVARIANTE DE SUPERVISÃO (escala)

- **SINGLE OWNER:** um device = **no máximo um bot SLN ativo**. Identificação EXATA via
  `sln_proc.py` (`basename(argv)=="main_sln_v2.py"` **e** `argv[i+1]==device`; nunca substring).
- **Restart é transação:** marcar RECOVERING → `terminate_worker_verified` (mata a **árvore**, incl.
  o filho `adb -s <dev> logcat`) → confirmar ZERO worker para o device → só então spawn → registrar
  `instance_id`/`generation` (+1). Nunca `terminate()` seguido de `Popen` imediato.
- **Heartbeat tem geração/instância.** Heartbeat de geração antiga é ignorado
  (`STALE_HEARTBEAT_GENERATION`). O registry/manifest é a autoridade; o arquivo de heartbeat é sinal.
- **Progresso funcional ≠ só "fechou anúncio".** O ticker avança `last_progress` em operações
  legítimas (inclusive backoff/no-fill) e **congela** apenas com `dump_hierarchy` travado (>30s).
- `duplicate_worker_count > 0` a qualquer momento invalida o benchmark (`C60/C90_INVALID`).

## QA REALISTIC BEHAVIOR (camada de simulação/agendamento — NÃO é o Fast Close)

- `qa_behavior.py` é uma camada **separada** de agendamento/heterogeneidade por device: delay
  aleatório por device, intervalo de reset individual (2..30 ciclos), quota populacional de reset,
  instalação com cooldown móvel de 24h. Default **OFF** (`qa_realistic_behavior_enabled=false`).
- **NÃO misturar** com a state machine do anúncio: reward gate, FORMAT, pill, Store/BACK, recovery,
  Fast Close, IA, persistent logcat, adaptive tick e watchdog ownership ficam **intocados**.
- O scheduler **decide** (WAIT/RESET_DUE/RESET_ALLOWED/INSTALL_*/NORMAL_CYCLE); o `main` só **executa**
  a ação autorizada. Reset programado USA SEMPRE `reset_identity_and_app_state()` (reset+clear juntos).
- Estado **por device** em `state/qa_devices/<safe>.json` (atômico); coordenação da frota em
  `state/qa_coord.json`. Persistente entre restarts (bot/watchdog/host). Ver `docs/QA_REALISTIC_BEHAVIOR.md`.

## CICLO / AD SESSION (modelagem — não é o fluxo do anúncio)

- **1 clique em "Assistir anúncio" = 1 CICLO = 1 AD SESSION** (`ad_session.py`). Uma sessão pode ter
  **N stages** (vídeo, playable, Store, endcard, tela intermediária, novo creative). **Fechar um
  controle NÃO finaliza o ciclo.**
- Fronteira: `begin()` no início real da sessão; `continue_stage()` quando fechou mas **não** voltou à
  MAIN (novo stage, mesma sessão); `complete()` só com **MAIN confirmada**; `fail()` em **escape
  forçado** (HOME+force-stop) — **retorno forçado ≠ conclusão**.
- `qa_scheduler.record_cycle_result(session_started, terminal_result, cycle_id)` é a **autoridade de
  contagem** (regra pura `should_count_cycle`). Conta **1 CICLO** quando a sessão iniciou e atingiu
  resultado terminal: **COMPLETED ou FAILED**. **ABORTED** (test end/watchdog/crash, sem resultado) e
  **sem sessão** (NO_FILL/FAILED_TO_LOAD antes de abrir) **NÃO contam**. Idempotente por `cycle_id`
  (`counted_cycles_by_id <= 1`).
- **SUPERSEDED:** a regra anterior "só CYCLE_COMPLETED decrementa / FAILED não decrementa" **não vale
  mais**. O intervalo de reset (2–30) conta **CICLOS EXECUTADOS/accounted** (completed+failed), não
  stages/telas/X. Resultado funcional (COMPLETED/FAILED/ABORTED) ≠ contabilidade (COUNTED/NOT_COUNTED).
- **Cobertura de smoke (separar eixos):** `FUNCTIONAL_COVERAGE` (entre devices com sessão,
  `accounted == completed + failed`) ≠ `INVENTORY_COVERAGE` (devices sem anúncio/0 AD_SESSION).
  **INVENTORY_NO_SESSION != ACCOUNTING_FAILURE.** Métricas: `devices_requested / with_session /
  with_accounted / with_tree / no_inventory` (`accounting_smoke.py`).
- Telemetria: `[QA][CYCLE_START]`, `[AD_SESSION][START|STAGE|STAGE_CLOSED|CONTINUE]`,
  `[QA][CYCLE_COMPLETED|CYCLE_FAILED]`.
- **Um clique por ciclo:** nunca criar nova solicitação ao app a cada stage. NÃO altera reward gate,
  FORMAT, pill, Store/BACK ou recovery. Testes: `tests/test_ad_session.py`.

## O QUE NÃO FAZER

- Não refatorar arquitetura (um processo Python por device é o baseline).
- Não alterar gates/thresholds validados sem evidência de falha real.
- Não trocar o countdown gate por tempo.
- Não usar `CreativeId` como assinatura de tipo de anúncio.
- Não apagar código legacy (existe para rollback; `use_legacy_applovin=false`).
- Não mover YOLO para frente da cascata.
- Não introduzir validações externas que não façam parte da state machine funcional.
- **Não alterar comportamento funcional durante rodadas de capacidade.** Persistent logcat, adaptive
  tick, thresholds do watchdog, idle backoff, FORMAT, reward gate, pílula, Store/BACK, endcard,
  recovery, IA e timeouts só mudam com evidência e incremento de versão — comparar sempre contra o
  baseline (**C60 clean**).
- **Não otimizar persistent logcat** (multiplexer/pooling), batching de IA ou centralização dos bots
  sem medição. Em 90 devices o gargalo é **inventário** (`no_fill≈68/90`) + CPU com pouca folga;
  **sharding** antes de empurrar o single-host.
- **Não iniciar 120 devices automaticamente** (só sob pedido explícito).

## CONFIG

- `config_sln_v2.json` é o **efetivo**. `config_sln_v2.py::DEFAULTS` são apenas defaults.
- Nunca alterar comportamento só porque o JSON difere do default.
- Layering: `DEFAULTS → config_sln_v2.json → SLN_CONFIG_OVERLAY`.
  - `SLN_CONFIG_OVERLAY=config_benchmark_sln_v2.json` **mescla SOBRE** a base (preserva o que não declara, ex.: `download_apps_pct=5`). É o que a interface injeta no perfil "benchmark".
  - `SLN_CONFIG_FILE` SUBSTITUI a base (perfil completo). Não usar para o benchmark.
- Guards de teste (env): `SLN_TEST_MAX_CYCLES` / `SLN_TEST_MAX_RUNTIME_S` (0 = ilimitado).
- Preflight read-only antes de benchmark: `python preflight_devices.py --group NOME` (ver `docs/CURRENT_STATE.md` §8).
- **Flags de escala (default OFF na base):** `persistent_logcat_enabled`, `adaptive_tick_enabled`,
  `heartbeat_enabled`/`watchdog_stale_s`/`watchdog_restart_max`, `idle_backoff_enabled`.
  C60/C90 usaram `config_c60_sln_v2.json` (persistent+adaptive+heartbeat; `ia_url=127.0.0.1`).
- Env de ownership (injetado pelo runner): `SLN_WORKER_INSTANCE_ID` / `SLN_WORKER_GENERATION`.

## SCRIPTS UTILITÁRIOS

- `preflight_devices.py` — check read-only dos devices (online/awake/battery/app/IA).
- `sideload_gms_chrome.py` — atualiza GMS/Chrome via `adb install-multiple` (sem Play Store).
  Variantes por Android: sdk28→GMS 100400 + Chrome 138; sdk31→GMS 190400 + Chrome 154.
- `sln_proc.py` — ownership/processos: identificação exata, kill verificado da árvore, orphans.
- `watchdog.py` + `watchdog_run.py` — supervisão externa (heartbeat/generation, restart transacional,
  preflight de duplicatas, CSVs de dup/orphan/logcat).
- `benchmark_run.py` + `benchmark_host.py` — lança N bots (overlay) + telemetria do host.
- `select60.py` / `select90.py` — preflight READY + monta o set de N devices (registra substituições).
- `c60_clean.py` / `c90_probe.py` / `lifecycle_30.py` / `fault_injection.py` — benchmarks de
  capacidade/lifecycle/falha controlada.
- `sharding.py` — static sharding por host (`SLN_HOST_ID`/`shards/<id>.txt`).
- `qa_behavior.py` — camada QA realistic (estado por device, scheduler, quota/concorrência/install).
- `ad_session.py` — modelagem de CICLO/AD SESSION (1 clique = 1 ciclo; N stages; MAIN/escape).
- `subad_close.py` — detector estrutural do SUB_AD_CLOSE (sub-ad WebView topo-direito, anônimo).
- `simulate_qa_behavior.py` / `qa_smoke.py` / `find_button_devices.py` — simulação offline (N devices),
  smoke real 3–5 devices, e descoberta de devices com o botão de reward.
- `manage_sln.py` — `start_ia` / `stop_bot` / `status`.
- `update_gms_chrome.py` — update via Play Store (só com conta Google OK; bloqueado por auth na farm).

## TESTES MÍNIMOS APÓS QUALQUER MUDANÇA

1. `python -m py_compile` em todo `.py` alterado.
2. Carregar a config efetiva.
3. Replay/smoke dos fluxos afetados.
4. Se mexeu em reset: testar RESET forçado em 1 device (RESET → force-stop → `pm clear` success → relaunch).
5. Baterias curtas: 2–6 anúncios válidos ou 30 min. Nunca soak sem pedido explícito.
6. **Offline (sem devices):** `python tests/test_scale.py` (37) e `python tests/test_ownership.py` (23).
7. **QA behavior (offline):** `python tests/test_qa_behavior.py` e `python simulate_qa_behavior.py --devices 20 --cycles 100 --seed 123`.
8. **Ciclo/ad-session (offline):** `python tests/test_ad_session.py`.
9. **SUB_AD_CLOSE (offline):** `python tests/test_subad_close.py`.

## SUB_AD_CLOSE (normal close path — camada estrutural)

- Sub-ads WebView têm um **close anônimo no topo-direito** (`FrameLayout` clicável, sem id/text/desc,
  bounds ≈ [958,217][1028,287] em 1080x2280, 1 filho View de mesmos bounds, contexto WebView/adFrame).
  Detector `subad_close.py` (`_fc_detectar_subad_close`), **por proporções**.
- **DETECÇÃO != AUTORIZAÇÃO:** usa o gate atual. Interstitial confirmado ⇒ clique; **rewarded** sem
  completion ⇒ `[FASTCLOSE][SUBAD_CLOSE] action=observe authorization=reward_gate` (NÃO clicar, NÃO
  revelar). Processado **antes** do `REVEAL_TOP`. Fechar o controle **não** completa o ciclo por si —
  MAIN confirmada é que completa (ver `ad_session.py`).
- Não cobre o compact ">|" (VISIBLE_NOT_IN_XML; o reveal resolve) nem a variante ALRTB
  `resource-id=close_button`. Não usar o XPath `content/FrameLayout` como botão (é container).


## MULTI-APP / RUN_APPS

- `main_sln_v2.py` usa `SLN_APP_PACKAGE` (default `com.flixdigital.dramaclassic`) e
  `SLN_APP_LABEL`. Nao hardcodar novo package: use a env e mantenha o default.
- Apps atuais: `1` Mx MotosV2, `2` cas (`com.flixdigital.dramaclassic`), `3` Puxo No Grau,
  `4` Bike Mx, `5` Grau Bahia, `6` Bikes Mil Grau.
- Interface: "Apps a rodar" marca todos por default; **1 app** roda `main_sln_v2.py` direto;
  **2+** usam `run_apps.py` (raiz do repo). "Anuncios/app" e "por passo" sao quota/passo
  aleatorios; `Grandes=0` e loop infinito com reset de ID entre grandes ciclos.
- `run_apps.py` aceita chaves/package/label/`all`, `--cycles-min/max`, `--chunk-min/max`,
  `--rounds`, `--runtime`, `--delay`, `--mode`, `--list`. Logs em
  `logs/apps_<package>_<timestamp>.log`; no-fill aborta cedo; watchdog de 60s no 1o anuncio.
- Console do bot fecha quando o processo termina (limite/quota/falha), nao por crash automatico;
  `Headless` desmarcado abre console por device.
- `auto_reward.py` e helper de abertura/clique no botao 1, nao substitui o bot completo.
