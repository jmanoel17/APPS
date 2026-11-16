# QA_REALISTIC_BEHAVIOR.md — SLN V2

Camada de **simulação QA / agendamento por device** (heterogeneidade de frota). **NÃO** faz parte
da state machine do anúncio.

## Objetivo

Simular uma frota heterogênea de usuários/dispositivos — sem todos clicarem/limpando/resetando/
instalando ao mesmo tempo, com delays diferentes — para: teste de robustez, antifraude, comportamento
distribuído, resiliência, cobertura de estados, benchmark e segurança do aplicativo.

Cada device tem um **estado QA persistente e independente**. A aleatoriedade é configurável,
reproduzível por seed, distribuída por device, persistente entre restarts e observável por telemetria.

## Arquitetura

```
qa_behavior.py
    QaConfig                 config validada (fallbacks seguros)
    DeviceQABehaviorState    estado persistente por device (state/qa_devices/<safe>.json)
    QABehaviorScheduler      decide WAIT/RESET_DUE/RESET_ALLOWED/INSTALL_*/NORMAL_CYCLE
    QACoordinator            quota/concorrência/install da frota — EM MEMÓRIA (testes/simulação)
    FileQACoordinator        idem, multi-processo (state/qa_coord.json + lock), para o bot real
    build_scheduler(...)     monta o scheduler do device (None se desabilitado)
```

Fluxo (o solver/main só EXECUTA a ação autorizada):

```
DEVICE SCHEDULER  ── decide quando iniciar ciclo / reset / install
        ↓
APP FLOW → AD FLOW → resultado
        ↓
scheduler atualiza ciclo/estado (cycle_completed/on_reset_result/on_install_result)
```

O main só chama `qa_scheduler.begin_cycle()` (delay + decisão de reset) antes de abrir o app e
`cycle_completed()` após o ciclo. **Nada** da state machine do Fast Close é alterado.

## Estado por device

`state/qa_devices/<safe_device_id>.json` (escrita atômica `tmp + os.replace`), campos:
`device_id, seed, cycle_count, next_action_delay_s, reset_interval_cycles, cycles_until_reset,
last_reset_at, reset_count/attempts/success/fail, last_install_at, install_count/attempts/success/fail,
install_eligible, daily_install_day, created_at, updated_at, delay_sum/delay_count`.

Persistente entre: restart do bot, restart do watchdog, fechar interface, restart do host. Arquivo
ausente/corrompido ⇒ recria com defaults (sem perder o device).

## Random seed

`seed = sha256(qa_random_seed + "|" + device_id)`. `qa_random_seed=null` ⇒ entropia do sistema.
**Nunca** um único sorteio global para todos. Com seed definido, a execução é reproduzível por device.

## Delay entre ciclos

`qa_action_delay_min_s`..`qa_action_delay_max_s` (default 10..60). Sorteado por device a cada ciclo
(ex.: A=13.4s, B=47.8s, C=22.1s). Sleep em **blocos canceláveis** (não um sleep gigante) — interrompível
por shutdown/watchdog/kill. Telemetria `[QA][DELAY]`.

## Semântica de ciclo / AD SESSION (o que conta como "1 ciclo")

**1 clique em "Assistir anúncio" = 1 CICLO = 1 AD SESSION** (`ad_session.py`). Uma sessão pode conter
**N STAGES** (vídeo, playable, Store, endcard, tela intermediária, novo creative). **Fechar um controle
não finaliza o ciclo.** Só conta ciclo quando a **MAIN é confirmada** ao final, após toda a cadeia, e
**sem** escape forçado (HOME+force-stop). Retorno forçado = `CYCLE_FAILED` (não decrementa o contador).

- `begin` no início real da sessão; `continue_stage` quando fechou mas não voltou à MAIN (mesmo ciclo,
  novo stage); `complete` só com MAIN; `fail` em escape.
- **Contagem (CYCLE_ACCOUNTING_V2):** `record_cycle_result(session_started, terminal_result, cycle_id)`
  conta **1 ciclo** para **COMPLETED ou FAILED**; **ABORTED** (test end/watchdog/crash) e **sem sessão**
  (NO_FILL antes de abrir) **não contam**; idempotente por `cycle_id`. `cycles_until_reset -= 1` só via
  `record_cycle_result` (não usar `cycle_completed` como contador genérico). **SUPERSEDED:** "FAILED não
  decrementa reset".
- **Um clique por ciclo** (nunca nova solicitação por stage).
- Telemetria: `[AD_SESSION][START|STAGE|STAGE_CLOSED|CONTINUE]`, `[QA][CYCLE_START]`,
  `[QA][CYCLE_COMPLETED]`, `[QA][CYCLE_FAILED]`.

## Reset scheduling (reset id + clear SEMPRE juntos)

- Intervalo **individual** por device: `randint(qa_reset_interval_min_cycles, qa_reset_interval_max_cycles)`
  (default 2..30); `cycles_until_reset` decrementa por **CICLO COMPLETO** (ver semântica acima); `<=0` ⇒ **RESET_DUE**.
- A cada ciclo COMPLETED: `cycle_completed()`.
- Ao ficar due: aplica `qa_reset_start_jitter` e pede autorização (quota+concorrência). Se autorizado
  ⇒ **RESET_ALLOWED** ⇒ executa **`reset_identity_and_app_state()`** (o helper consolidado:
  RESET ID → force-stop → `pm clear` validado → relaunch). **Nunca** reset sem clear.
- Após sucesso: sorteia **novo** intervalo `randint(2,30)`. Isso evita periodicidade idêntica
  (A: 7→22→4; B: 29→11→18).

## Reset population quota
`qa_reset_population_pct_min`..`_max` (default 5..25) é **quota/limite** por janela
(`qa_reset_window_minutes`, default 30), **não** probabilidade por ciclo. Fluxo: devices ficam
RESET_DUE individualmente → a quota da janela é `round(população*pct)` → são concedidos até a quota
(ordem randomizada pelo jitter). Se 8 estão due e a quota é 5 ⇒ 5 executam, 3 permanecem DUE.
**Nunca** força quota acima dos elegíveis. Concorrência limitada por `qa_max_concurrent_resets`
(default 2). **Não** usar `if random()<pct: reset()`.

## Install scheduling + cooldown 24h

- `qa_install_pct_min`..`_max` (default 5..10) por janela (`qa_install_window_hours`, default 24):
  `target = round(população_elegível * pct/100)`, seleção aleatória via quota (jitter/ordem).
- **Regra absoluta:** no máximo **1 instalação por device em janela MÓVEL de 24h**
  (`qa_install_cooldown_hours`). Usa `last_install_at` persistido: elegível ⟺
  `now - last_install_at >= 24h`. **Não** usa "mudou a data" (evita 23:59→00:01).
- Revalida `last_install_at` (via `FileQACoordinator`, com lock) antes de iniciar; só grava
  `last_install_at` quando a instalação é **confirmada** (botão 'Abrir'/'Instalado'). Falha ⇒
  `INSTALL_FAILED` (não marca sucesso). Estados: `INSTALL_ATTEMPTED/SUCCESS/FAILED`.

## Telemetria

`[QA][DELAY] device= seconds=`, `[QA][RESET_SCHEDULE] device= interval_cycles= remaining=`,
`[QA][RESET_DUE]`, `[QA][RESET_SELECTED] population_pct= quota=`, `[QA][RESET_SUCCESS]`,
`[QA][RESET_FAILED]`, `[QA][INSTALL_ELIGIBLE]`, `[QA][INSTALL_SELECTED] population_pct=`,
`[QA][INSTALL_SUCCESS]`, `[QA][INSTALL_FAILED]`, e `[QA_SUMMARY] device= cycles= avg_delay_s=
reset_due= reset_attempts= reset_success= reset_fail= avg_reset_interval_cycles= install_eligible=
install_selected= install_success= install_fail= last_install_at= next_install_eligible_at=`
(emitido no shutdown via `atexit`).

## Config / perfil

Overlay de produção: `config_qa_realistic_sln_v2.json` (via `SLN_CONFIG_OVERLAY`). Chaves:
`qa_realistic_behavior_enabled` (default **false**), `qa_action_delay_min_s/max_s`,
`qa_reset_interval_min/max_cycles`, `qa_reset_population_pct_min/max`, `qa_max_concurrent_resets`,
`qa_reset_start_jitter_min/max_s`, `qa_install_pct_min/max`, `qa_install_cooldown_hours`,
`qa_random_seed`, `qa_reset_window_minutes`, `qa_install_window_hours`.
Validação `min<=max` com **fallback seguro + log** (`[QA][CONFIG]`).

Perfis preservados: development / benchmark / scale. `qa_realistic_behavior_enabled=false` neles
(padrão). Só o perfil **QA Realistic** liga. Env de frota: `SLN_QA_POPULATION` (nº de devices ativos,
para o denominador da quota).

## Persistência e restart/watchdog

Estado é do **device**, não do host (arquivos por device; sharding pode mover o device levando seu
arquivo). Após restart do watchdog/bot, `cycles_until_reset` e `last_install_at` continuam (não
sorteia novo intervalo). Ownership inalterado: **1 device = 1 worker**.

## Testes

- `tests/test_qa_behavior.py` (offline, clock injetável) — delay/faixa/reprodutibilidade, reset
  (intervalo 2..30, due, novo intervalo, quota não força, não-selecionado permanece due, concorrência),
  install (pct, cooldown 24h, falha não marca sucesso, restart preserva), persistência
  (save/load/corrompido/incompleto/atômico).
- `simulate_qa_behavior.py` (offline, sem ADB) — distribuição/sincronização:
  `python simulate_qa_behavior.py --devices 20 --cycles 100 --seed 123`
  (e `--devices 50`). Prova ausência de sincronização (max frac de resets no mesmo ciclo) e intervalos
  2..30.
- `qa_smoke.py` (real, 3–5 devices, overlay TEST-ONLY `config_qa_smoke_sln_v2.json`) — valida a
  telemetria e ownership no bot real.
- `find_button_devices.py` — rotaciona devices e lista os que exibem o botão (`btnRewardAds` /
  'Assistir anúncio').

## Limitações

- A seleção "aleatória entre os RESET_DUE" é **aproximada** pela ordem de chegada randomizada pelo
  jitter (arquitetura 1 processo/device; sem coordenador central). A quota/concorrência são reais.
- A janela de reset é por tempo configurável (default 30 min), não por "rodada" explícita.
- Install no smoke real depende de aparecer um intersticial de Play Store (pode não ocorrer numa
  janela curta); a lógica temporal é coberta por testes offline.
- Não há banco distribuído (arquivos por device + `qa_coord.json` bastam nesta fase).

## Validação ao vivo do RESET TREE (5/5)

`RESET_TREE_MODEL_VALIDATED_LIVE_5X` (2026-10-02). Overlay TEST-ONLY `config_qa_reset_tree_sln_v2.json`
(delay 2–5s, reset 2–5 CYCLE_COMPLETED, população=1, quota **100%**, janela 1 min) + env
`SLN_QA_TEST_TARGET_RESET_TREES=5` (hard cap 45 min), sem intervenção manual: **5/5 árvores**;
`cycles_completed == interval_drawn` em todas; FAILED não decrementou; RESET ID + CLEAR sempre juntos;
novo intervalo sorteado após success; `SESSION_START` íntegro; ownership dup=0. O overlay **não**
altera os defaults de produção (delay 10–60, reset 2–30, quota 5–25%). Detalhe: `docs/CURRENT_STATE.md §17`.

## Contagem de ciclo (CYCLE_ACCOUNTING_V2) e cobertura

Conta **1 ciclo** para **COMPLETED ou FAILED** (sessão iniciada + resultado terminal); **ABORTED**
(test end/watchdog/crash) e **sem sessão** (NO_FILL antes de abrir) **não contam**; idempotente por
`cycle_id`. `reset_interval` usa **`cycles_accounted`**. `CYCLE_ACCOUNTING_V2_PASS` (offline 170/170;
smoke `accounted == completed+failed`). **INVENTORY_NO_SESSION != ACCOUNTING_FAILURE:** device sem
anúncio (0 AD_SESSION) é `INVENTORY_COVERAGE`, não falha de contagem. Métricas (runner
`accounting_smoke.py`): `devices_requested`, `with_session`, `with_accounted`, `with_tree`, `no_inventory`.
