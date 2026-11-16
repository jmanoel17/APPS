# SCALING_140.md — SLN V2: de 1 para 140 devices

> Documento de arquitetura e plano de escala. **Já medido** até 90 devices (C30/C60/C90) — ver
> `docs/CURRENT_STATE.md` §11–§13 e a seção "C60 CLEAN + C90 PROBE" no fim deste arquivo.
> Status: `SINGLE_HOST_CAPACITY_APPROACHING` (MAX observado 90; recomendado ≈60/host).

## Arquitetura (baseline, não mudar sem medição)

```
interface_sln_v2.py  (Tkinter, 1 por operador)
   └── 1 processo Python por device:  python main_sln_v2.py <device_id> 0 <limite>
          ├── Fast Close Watcher (1 dump/tick adaptativo)
          ├── LogcatReader persistente (1 stream adb logcat/device, opt-in)
          ├── watchdog.py in-process? NÃO: supervisão é EXTERNA (watchdog_run.py)
          └── telemetria [PERF] a cada 60s

watchdog_run.py  (supervisor EXTERNO; ownership/generation; restart transacional)
aiMain_sln_v2.py  (serviço CENTRAL de IA, 1 modelo, NÃO 1 por device)
   ├── POST /detect     (YOLO)
   ├── POST /classify   (heurística)
   └── GET  /health
```

Um processo por device continua sendo o baseline. **Não centralizar os bots em um único processo.**

## Escada de escala (medida)

`1 → 5 → 30 → 60 → 90` (próximo degrau: 120, **só sob pedido**; **sharding** recomendado antes).
Regra: só sobe um degrau quando o atual passa no checklist sem regressão de fechamento/recompensa e
com `duplicate=0`/`orphan=0`. Medições: C30/C60/C90 (seção final).

## Riscos conhecidos (medidos até 90)

| Risco | Por quê | O que medir | Estado / mitigação |
|---|---|---|---|
| Watcher de 300ms | 1 dump/tick × N devices ≈ centenas de dumps/s na mesma rede USB/Wi-Fi | `hierarchy_dump_count`, `dump_ms_avg/p95/max` | **implementado (opt-in)**: `adaptive_tick_enabled` (dumps −52%; 0 dumps >5s em C60/C90) |
| Subprocessos ADB de FORMAT | cada lookup de formato abre 2–3 `adb` (`date`, `pidof`, `logcat`) | `format_lookup_count`, `adb_subprocess_count`, `format_lookup_ms_max` | **implementado (opt-in)**: `persistent_logcat_enabled` (1 stream `adb logcat`/device; subprocessos −64%; unknown→0) |
| Screenshot/classifier desnecessário | `/classify` faz screenshot + POST mesmo quando o FORMAT explícito já resolve | `visual_classifier_invoked` vs total de anúncios | já mitigado: classifier é lazy |
| Logging por tick | `fast_close_log_every_tick=true` = centenas de linhas/s × N | tamanho de `logs/`, I/O | `false` no perfil de benchmark |
| IA concorrente | N bots batendo em 1 modelo YOLO | `ai_request_count`, `ai_latency_avg/max`, `ai_over_4s_count` | fila/lock já existe; C60/C90: poucos >4s; batching **não** implementado |
| Reset/`pm clear` simultâneo | dezenas de devices limpando dados ao mesmo tempo | `reset_count`, `reset_ms`, `clear_count`, `clear_ms` | backoff por device; `reset_id_pct`/`clear_data_pct` baixos |
| Artefatos | PNG+XML por evento/anúncio em `errors/` | tamanho de `errors/` | medir crescimento; `save_errors=false` só se o disco virar gargalo |
| Ownership/duplicatas | restart deixando o filho `adb logcat` órfão → duplicatas | `duplicate_worker_count`, `orphan_worker_count` | **resolvido**: `sln_proc.py` + restart transacional (`docs/CURRENT_STATE.md` §12) |
| Inventário de anúncios | devices em `no_fill` aguardando banner | `no_fill_backoff`, ads/device | **limitador dominante em 90** (`no_fill≈68/90`, 4.2 ads/device) — não escala por hardware |

## Perfil de benchmark

`config_benchmark_sln_v2.json` (perfil **separado**; não sobrescreve `config_sln_v2.json`).
Aplicar via **`SLN_CONFIG_OVERLAY`** (mescla SOBRE a base — é o que `benchmark_run.py` injeta;
**não** usar `SLN_CONFIG_FILE`, que substitui a base):

```bash
SLN_CONFIG_OVERLAY=config_benchmark_sln_v2.json python main_sln_v2.py <device> 0 0
```

Para os benchmarks de capacidade C60/C90 usou-se `config_c60_sln_v2.json`, que adiciona
`persistent_logcat_enabled=true`, `adaptive_tick_enabled=true`, `heartbeat_enabled=true` (default OFF).

Diferenças em relação ao padrão de desenvolvimento:

| Chave | Dev | Benchmark | Motivo |
|---|---|---|---|
| `fast_close_log_every_tick` | true | **false** | I/O de log domina a medição |
| `rewarded_no_countdown_diag_timeout_s` | 180 | **0** | diagnóstico desligado em produção |
| `top_control_experiment_mode` | off | off | experimento fora |
| `playable_diag_click_enabled` | false | false | produção |
| `use_legacy_applovin` | false | false | Fast Close é o controlador |
| `recovery_close_enabled` | true | **true** | não perder anúncio travado |
| `applovin_reward_completion_required` | true | **true** | gate inalterado |
| `applovin_skip_to_endcard_enabled` | true | **true** | fluxo inalterado |

O perfil **não** altera nenhum gate funcional — só telemetria/diagnóstico.

> Aplicar o perfil + `SLN_PERF_INTERVAL_S` (default 60s) e `SLN_START_CONCURRENCY`
> (default 8) para controlar a telemetria e a concorrência de arranque durante o benchmark.

## Concorrência de arranque

`SLN_START_CONCURRENCY` (default **8**) limita quantos bots **iniciam** ao mesmo tempo. O
semáforo cobre só o arranque (`Popen` + registro); `process.wait()` roda fora dele, então
o número total de bots rodando NÃO é limitado por este semáforo.

Início de muitos devices usa stagger com jitter para não sincronizar os dumps.

## Baseline medido — 1 device (2026-09-25, SM-G955U 1920x1080)

| Métrica | Medido |
|---|---|
| `dump_ms_avg` / `p95` / `max` | 130–209 / 155–268 / **314–319 ms** |
| `hierarchy_dump_bytes` por dump | ~41 KB |
| `format_lookup_ms_avg` / `max` | 330 / 350 ms (3 subprocessos ADB cada) |
| `screenshot_bytes` | ~315 KB por screenshot |
| IA `/detect` (mesmo host, `127.0.0.1`) | warm: 124–148 ms wall, inferência 79–131 ms |
| IA `/detect` cold (1ª chamada após start) | inferência ~3.7–4.9 s |
| `ai_latency` vista pelo BOT (`ia_url=localhost`) | **~2096 ms** (resolução IPv6 do `localhost`) |
| `reconnect_count` / `errors` | 0 / 0 |

**Conclusão nº1:** `dump_ms_p95 ≈ 265 ms` contra `tick=300 ms` → pouca folga. Se a contenção
por device crescer, o tick efetivo cai e a taxa de dumps cai junto.

**Conclusão nº2:** trocar `ia_url` para `http://127.0.0.1:8000` derruba ~2 s de latência por
chamada de IA (medido: ~5 ms vs ~2050 ms). Com `timeout=4` no cliente, isso é margem de erro.

**Conclusão nº3:** ~~nos devices da farm o app **não emite** `AdHelperLog`/`CAS.AI`~~ —
**SUPERSEDED (2026-09-26):** ver `CURRENT_STATE.md` §6.3/§7.2. O app **emite** `AdHelperLog`/
`CAS.AI` normalmente (`.102` com internet: `SHOWED`; `.101` sem internet: `FAILED_TO_LOAD`;
`.100`/`.104` com `logd` em `restarting`: `logcat read failure`). FORMAT por evento existe, mas
só é confiável em devices com `logd` saudável + internet — medir em 5 devices quantos resolvem
por evento antes de assumir o caminho rápido.

**Conclusão nº4 (medida em 30 devices, 2026-09-29):** com `logd` + internet, o FORMAT por evento
**funciona**: 212 `app_event` / 0 `cas_event` / 8 `unknown` no benchmark de 30 devices.

## Benchmark medido — 30 devices funcionais (2026-09-29)

**Escopo:** varredura de ~115 devices → **105 com botão disponível**; selecionados **30**
(faixa `.21`–`.55`). Overlay benchmark, `ia_url=127.0.0.1`, `--ads 6 --minutes 30` (~32 min).
Artefatos: `benchmarks/manifest_20260929_020510.json`, `benchmarks/host_30device_20260929_020510.csv`.

**Funcional:** CLOSED **151**, TIMEOUT 59, LEFT_AD 1; rewarded com recompensa **70** (+700 moedas);
`recovery_invoked` 104; consent CAS 21; clears 43. FORMAT 212/0/8. 8 devices sem inventário na
janela (`.27 .29 .41 .46 .47 .52`; `.21 .25` só 1).

**Host (341 amostras):** CPU min/avg/max **15.9 / 39.4 / 100%**; RAM usada 21.3 GB avg (65%,
max 70.7%); `python_rss_total` avg 1.6 GB / max 2.1 GB; bots 1→30.

**Device `[PERF]` sob carga (30 vs 1 baseline):**

| Métrica | 1 device | 30 devices |
|---|---|---|
| `dump_ms_avg` | 130–209 | min 58 / avg 131 / max 244 |
| `dump_ms_p95` | 155–268 | **avg 232 / max 598** |
| `format_lookup_ms_avg` | 330 | **avg 603 / max 4502** |
| `ai_latency_ms` (warm) | 124–148 | **avg 678 / max 1464** |
| `errors` / `reconnect` | 0 / 0 | 0 / 0 |

**Conclusão nº5 (escala):** **CPU do host pico 100%** e **dump p95 até ~600 ms** (> tick 300 ms)
⇒ o **host single-process é o gargalo em 30 devices**; a taxa de dump/ADB cai (`format_lookup`
max 4.5 s). **Não subir para 50** antes de mitigar (tick adaptativo / persistent logcat — hoje
proibidos). **RAM ok**; **IA aguenta** (max 1.46 s < timeout 4 s). Funcionalmente estável (0 erro).

## Checklist do benchmark 5-device

Antes:
- [x] `python -m py_compile` nos arquivos alterados.
- [x] Baseline 1 device medido (`dump_ms_avg`, `format_lookup_ms_avg`, IA, subprocessos ADB).
- [x] Servidor IA de pé e `/health` = `{"status":"ok","model_loaded":true}`.
- [x] ~120 devices `adb devices` = `device` (nenhum `offline`/`unauthorized`).
- [ ] `logs/` e `errors/` limpos ou anotados (para medir crescimento).
- [x] Consumo de CPU/RSS por processo medido (`benchmark_host.py`; host medido em 30 devices).
- [x] `ia_url` decidido: **`http://127.0.0.1:8000`** (aplicado no overlay de benchmark).

Durante (30 min ou 2–6 anúncios por device):
- [ ] Nenhum device caiu do ADB (`reconnect_count` estável).
- [ ] Nenhum anúncio preso > timeout.
- [ ] Bloco `[PERF]` presente e coerente em cada `logs/<device>.log`.
- [ ] `dump_ms_p95` não cresceu vs baseline 1 device (indicador de contenção).
- [ ] `ai_latency_max` dentro do timeout de 4s do cliente.
- [ ] Recompensa preservada (delta de `txtMoedas` positivo nos rewarded).
- [ ] `visual_classifier_invoked` ≈ 0 quando o FORMAT explícito resolve.

Depois:
- [ ] Comparar 5 vs 1: dumps/s, latência de SKIP, taxa de fechamento.
- [ ] Registrar resultados neste documento.
- [ ] Só então subir para 10.

## NÃO implementar nesta fase

- persistent logcat stream;
- adaptive watcher tick;
- centralização dos 140 bots em um único processo;
- AI batching;
- grande refatoração do `main_sln_v2.py`;
- mudança do reward gate.

Todos dependem dos números do benchmark 5/10.

## Benchmark 113 devices (60 min) — SINGLE-HOST SATURATED (2026-09-29)

Resultado: **`113_DEVICE_CAPACITY_FAIL`** + **`DIAGNOSTIC_PASS`**. Causa: **saturação host/ADB
single-process** (não é falha da máquina de estados).

| Métrica | 113 devices |
|---|---|
| Ads observados / TIMEOUT | 1167 / **630 (~46%)** |
| FORMAT unknown | **515 (~45%)** |
| devices que serviram / sem inventário | 51 / 62 |
| reconnects | **37** |
| CPU host avg / peak | **88.6% / 100%** |
| RAM host | ~79% |
| dump p95 agregado | **~2 s** |
| outliers | **12–91 s** (`.122`: avg 12.8s, p95 61s, max 91s) |
| IA | operacional (max ~2.5 s < 4 s) |

Os TIMEOUTs **não** são classificados automaticamente como falha da máquina de estados — a maioria
vem da saturação (FORMAT unknown por infra) e do inventário.

## FASE DE ESCALA — mitigação (features atrás de flags, default OFF)

Autorizadas nesta fase (via flags): persistent logcat, adaptive tick, supervisor watchdog,
sharding readiness. Ainda proibidos: AI batching, 1 processo p/ todos, mudança de reward gate,
fallback temporal de Rewarded, grande rewrite.

| FASE | Entrega | Flag |
|---|---|---|
| 2 | `watchdog.py`: heartbeat + supervisor WORKER_STALE/restart/quarantine | `heartbeat_enabled` / `watchdog_enabled` |
| 3/4 | `persistent_logcat.py`: stream contínuo, ring + piso de sessão, métricas | `persistent_logcat_enabled` |
| 5/6 | `adaptive_tick.py`: tick por estado + burst | `adaptive_tick_enabled` |
| 7 | backoff + jitter p/ no-fill | `idle_backoff_enabled` |
| 10 | `sharding.py`: `shards/<HOST_ID>.txt`, `--shard` | `SLN_HOST_ID` |

Testes: `tests/test_scale.py` (37/37). FASE 12 (1 device, flags ON): sem regressão funcional;
`dump_rate` **~200/min → ~64–70/min**; FORMAT resolvido pela memória.

Próximo passo (com aprovação, não automático): **FASE 13 A/B 30 devices** para atribuir ganho
(persistent logcat vs adaptive tick), depois 60 → 90 → 120 → 140, parando entre etapas.

## A/B/C 30 devices (30 min) — reconciliado (2026-09-29/30)

A=off/off; B=persistent; C=persistent+adaptive. Valores autoritativos dos CSVs
(`benchmarks/host_30device_20260929_225410|232838.csv`, `...20260930_000243.csv`):

| | A | B | C |
|---|---|---|---|
| CPU avg/p50/p95/p99/max % | 20.6/19.0/31.5/40.0/**56.9** | 26.1/25.5/35.5/41.0/46.4 | 26.6/25.4/35.8/43.3/**100.0** |
| amostras >90% | 0 | 0 | **1** (0.29% do tempo) |
| ADB subprocesses | 1580 | 576 | 539 |
| dumps/min/device | 70 | 66 | **32** |
| dump avg/p95/max ms | 142/238/10667 | 139/212/2266 | 132/232/**5162** |
| format_from_memory | 0 | 255 | 238 |
| FORMAT unknown % | 0.7 | **0.0** | 0.8 |
| reconnects / errors | 19/21 | 24/24 | **0/0** |
| AI req / max ms | 88/294 | 65/**4224** | 69/2444 |
| close % / timeout % | 75.1/24.9 | 78.0/22.0 | 76.8/23.2 |

**Correção textual:** CPU **B→C aumentou** (26.1→26.6%, +1.9%) — não reduziu; o que cai é o
**dump count** (−52%). **dump max**: C (5162) é **melhor que A** (10667) mas **pior que B** (2266).
Vereditos: `PERSISTENT_LOGCAT_PASS`, `ADAPTIVE_TICK_PASS` (sem regressão). **READY_FOR_60**.

---

## C60 CLEAN (baseline definitivo) + C90 PROBE — 2026-10-01

Watchdog/ownership corrigidos (single-owner, restart transacional, kill da árvore, generations,
progresso em operações legítimas). Detalhe em `docs/CURRENT_STATE.md` §12–§13.

**C60 clean (30 min, autoritativo):** 60/60 bots, logcat ≈60, **dup=0/orph=0 sempre**, watchdog
restart **1**, reconnect **0**, 0 dumps >5s. CPU avg 51.1 / p95 80.1 / p99 88.8, >90% ≈1% (15s).
→ `SINGLE_HOST_CAPACITY_APPROACHING`.

**C90 probe (30 min, sem early stop):** 90 devices (60 do C60 + 30 READY). **dup=0/orph=0 sempre**,
watchdog **0/0/0**, reconnect **0**, 0 dumps >5s, FORMAT unknown 0.26%. CPU avg 58.4 / p95 85.2 /
p99 93.3, >90% 3.2% (45s; rajada máx 15s; pico de arranque p95 93.3 nos 1ºs 5 min).
`no_fill_backoff` avg **68.4/90** → **inventário é o gargalo dominante** (4.2 ads/device).

**Curva CPU p95:** 35.8 → 80.1 → 85.2; **avg:** 26.6 → 51.1 → 58.4. O salto 30→60 estava inflado; a
inflexão **não** aparece até 90 (60→90 só +5pp de p95). Não extrapolar linearmente.

**Decisão:** `SINGLE_HOST_CAPACITY_APPROACHING`. `MAX_OBSERVED_CAPACITY`=90;
`RECOMMENDED_OPERATIONAL_CAPACITY`≈**60/host** (headroom; inventário já limita em 90).
**NÃO** iniciar 120; **sharding** recomendado antes de 120. Estimativa antiga 25–40/host **descartada**.

---

## QA Realistic Behavior (separado da capacidade)

Camada **independente** de simulação/agendamento por device (delay aleatório, reset por intervalo
individual 2–30 ciclos, quota populacional de reset, install com cooldown móvel 24h). Default **OFF**;
**não** altera a state machine do anúncio nem os resultados de capacidade (C60/C90 rodaram com
`qa_realistic_behavior_enabled=false`). Detalhe em `docs/QA_REALISTIC_BEHAVIOR.md` e
`docs/CURRENT_STATE.md` §14. **Não** ligar QA Realistic em benchmarks de capacidade sem intenção.

