# DPN E2.6 - Group Ledger / Egress Epoch / Route Availability

Status: **OFFLINE_PASS / REAL_PROVIDER_SMOKE_PENDING**
Default: **OFF** (`dpn_group_ledger_v2_enabled=false`).

A camada DPN v2 e isolada do bot funcional. Fast Close, reward gate, FORMAT, SUB_AD_CLOSE,
Store/BACK, IA, reset tree e CYCLE_ACCOUNTING_V2 nao foram alterados.

## 1. RouteGroup

`dpn_groups.json` antigo continua valido. O modelo novo adiciona, opcionalmente:

```text
group_id, controller_id, provider, tunnel_code, expected_dpn_id, members[], enabled
```

Sem `group_id`, a chave do JSON (ex.: `DPN_01`) vira o group_id; `members` cai para `devices`.
Assim os grupos atuais continuam funcionando quando a flag v2 e ligada.

## 2. WorkUnitEvent

Arquivo de entrada: `state/dpn/work_unit_events.jsonl`.

Campos: `event_id`, `host_id`, `instance_id`, `device_id`, `agent_id`, `cycle_id`,
`app_package`, `terminal_result`, `accounted`, `finished_at_ms`, `route_group_id`, `source`.

- `event_id = sha256(host_id|instance_id|cycle_id|WORK_UNIT)`.
- O coordinator consome o inbox e grava o ledger `work_units.jsonl`.
- O mesmo evento pode chegar varias vezes; `seen_events` impede dupla contagem.
- Somente `accounted=true` (COMPLETED/FAILED terminal) e candidato a work unit.
- Stage, clique, tempo, screenshot, recovery e fechamento de X nao geram unidade.

## 3. EgressEpoch

Arquivo exportado: `state/dpn/epochs.json`.

Cada IP aceito cria um epoch novo com `accepted_work_units=0`. O contador nunca e herdado
do epoch anterior. Estados: `STARTING`, `RUNNING`, `CLOSED`, `FAILED`; o estado do grupo
carrega `DRAINING`, `ROTATING`, `VERIFYING_EGRESS`, `DEGRADED`, etc.

## 4. Contador e threshold

`dpn_work_units_per_ip` passa a significar **work_units_per_egress_epoch** quando v2=true.
O contador e do grupo/epoch, nao do device:

```text
A=5 + B=7 + C=8 = accepted_work_units 20 -> LIMIT_REACHED -> DRAINING
```

`gate()` bloqueia novos ciclos de todos os members quando o estado do grupo esta em
`BLOCK_STATES` ou quando `accepted_work_units >= threshold`. Ciclos ja iniciados terminam.

Ciclos terminais depois do threshold continuam validos no CYCLE_ACCOUNTING_V2, mas no ledger
v2 viram `accepted_for_epoch_quota=false` e `drain_overshoot`; o contador fica clampado.

## 5. Safe barrier

`GroupSignalBarrier` usa `state/dpn/workers/<device>.json`, com `group_id` + `drain_epoch`.
O coordinator so vai a `ROTATING` quando todos os members relevantes estao safe. Worker morto
nao trava o drain. Se o timeout expirar com membros ativos nao-safe, o estado explicito e
`DRAIN_TIMEOUT` e nao ha rotacao silenciosa.

## 6. RouteGeneration / candidates

`state/dpn/route_generations.json`:

- `route_generation_id`, `refresh_index`, `fingerprint`, `candidates_seen`,
  `candidates_rejected`, `candidates{}`.
- Candidato: `public_ip`, `tunnel_code`, `status`, `attempt_count`, `failure_reason`.
- Status: `UNKNOWN`, `VERIFYING`, `AVAILABLE`, `REJECTED`, `COOLDOWN`, `EXHAUSTED`, `USED`.
- O mesmo IP rejeitado nao e aceito novamente na mesma generation.
- Quando todos os candidatos conhecidos ficam `REJECTED/COOLDOWN/EXHAUSTED/USED`, o estado
  vira `ROUTE_GENERATION_EXHAUSTED`.

## 7. Rotacao e verificacao

```text
DRAINING -> all safe -> ROTATING -> VERIFYING_EGRESS
```

A verificacao aplica, nesta ordem: IP mudou? health? cooldown global? candidato ja rejeitado?
AvailabilityProbe (quando ligado)? Se aceito: novo EgressEpoch em `RUNNING`; se rejeitado:
proximo candidato/rotacao. `ROTATE_NOW` manual passa pelo mesmo caminho, sem atalho.

## 8. Refresh de rotas

`REFRESH_ROUTE_SET` e separado de reset de node:

- provider `refresh_route_set()`; no Deeper mapeia `refreshTunnel`, nunca `reset_node`.
- `REFRESHING_ROUTES -> WAIT_REFRESH -> DISCOVERING_ROUTES -> nova generation -> ROTATING`.
- `dpn_route_refresh_settle_s` aguarda a atualizacao assincrona.
- Fingerprint igual => `REFRESH_NO_CHANGE` e `refresh_attempt++`.
- `dpn_max_route_refreshes` esgotado => **DEGRADED**; gate bloqueia novos ciclos e nao ha
  loop infinito. `reset_node` nao e fallback automatico.
- `REFRESH_ROUTES_NOW` manual disponivel na DPN tab (botao “Refresh Rotas”).

## 9. AvailabilityProbe

Contrato em `dpn/availability.py`:

```text
probe(route_group_id, epoch_candidate, timeout_s) -> AVAILABLE | UNAVAILABLE | INCONCLUSIVE
```

- Desligado por default (`dpn_availability_probe_enabled=false`): health e cooldown bastam.
- Quando ligado: `dpn_availability_probe_retries` e `..._interval_s` controlam tentativas.
- `UNAVAILABLE` => candidate rejeitado (`WORKLOAD_UNAVAILABLE`) e proximo egress.
- `INCONCLUSIVE` nao e sucesso; depois dos retries o candidato e rejeitado de forma explicita
  (`PROBE_INCONCLUSIVE`), sem loop.
- Nao toca Fast Close nem accounting.

## 10. Config

```text
dpn_group_ledger_v2_enabled=false
dpn_availability_probe_enabled=false
dpn_availability_probe_retries=2
dpn_availability_probe_interval_s=5
dpn_max_route_refreshes=2
dpn_route_refresh_settle_s=5
dpn_route_refresh_backoff_s=10
```

Mantidas: `dpn_work_units_per_ip`, `dpn_ip_reuse_cooldown_s`, `dpn_drain_timeout_s`,
`dpn_rotation_poll_s`, `dpn_rotation_timeout_s`, `dpn_duplicate_rotations_before_node_reset`.

## 11. Storage e single writer

`state/dpn/group_ledger.json` (autoridade), `epochs.json`, `route_generations.json`,
`work_units.jsonl`, `work_unit_events.jsonl` (inbox do worker) e `ip_history.json`.
Escritas atomicas com tmp/fsync/replace; locks interprocesso. O coordinator e o writer
autoritativo; o Device Agent apenas podera store-and-forward no futuro.

## 12. Runner / interface

`dpn_coordinator_run.py` usa `GroupLedgerCoordinator` quando a flag v2 esta ligada. O status
e publicado em `status.json` mantendo as chaves da UI e adicionando `group_id`, epoch, accepted
units, threshold, generation, candidates, refresh/degraded e safe/blocked devices. A UI ganhou
o botao manual `Refresh Rotas`.

### Console por aparelho

`dpn_console_log_enabled=true` (default): cada transicao do coordinator e escrita em:

- `logs/<device_safe>.log` de **cada membro** do route group;
- `logs/dpn_<group_id>.log` (visao agregada do grupo).

Formato:

```text
[HH:MM:SS.mmm] [DPN][STATE] group=G1 state=RUNNING ip=1.1.1.1 epoch=G1:epoch:1 accepted=0/20
[DPN][STATE] group=G1 state=DRAINING ...
[DPN][STATE] group=G1 state=ROTATING ...
[DPN][STATE] group=G1 state=VERIFYING_EGRESS ...
[DPN][CANDIDATE_REJECTED] ip=... status=COOLDOWN reason=...
```

Assim o console do bot mostra a troca de IP sem precisar abrir o log do coordinator. O
`run_apps.py`/viewer da interface continuam mostrando o mesmo arquivo de device.

## 13. Testes offline

- `tests/test_dpn_group_ledger.py` (13): agregacao 5+7+8, dedupe, grupo alheio, threshold,
  overshoot, barrier, drain timeout, epoch novo, mesmo IP, restart, gate v2, comando duplicado.
- `tests/test_dpn_route_generation.py` (5): exhaustion, refresh nova generation, no-change,
  refresh max -> DEGRADED.
- `tests/test_dpn_availability.py` (3): status, inconclusive, probe desligado.
- Suite DPN completa: **136/136 OK**.

Smoke controlado com `RefreshProvider`/`FakeBarrier`: timeline IP1 aceito -> 5 work units ->
draining -> IP2 unavailable -> IP3 cooldown -> IP4 unavailable -> refresh -> IP5 aceito ->
novo epoch com contador 0. Nenhum anuncio/Fast Close foi usado.

## 14. Limitacoes / proximo passo

- Nenhum smoke real em DPN Deeper nesta fase; `refresh_route_set` real nao foi exercitado.
- AvailabilityProbe real ainda nao existe; testes usam `SequenceAvailabilityProbe`, producao
  usa `NullAvailabilityProbe` quando desligada.
- A UI mostra o snapshot v2, mas painel web/VPS e store-and-forward do Agent ficam para fase
  posterior.
- Flag permanece **OFF**; nao ativar globalmente sem smoke real controlado.
