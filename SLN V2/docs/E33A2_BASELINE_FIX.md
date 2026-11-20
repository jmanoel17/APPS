# E3.3A.2 - Final Baseline Fix

Data: 2026-11-15
Escopo: apenas os dois blockers restantes (reserva atomica de command_id e superficie de
Provision), sem features, sem benchmark repetido (TAP/SWIPE inalterados).

## 1. Arquivos alterados

- `device_agent/errors.py` (`COMMAND_ID_CONFLICT`)
- `device_agent/server.py` (reserva atomica + binding; remocao do save em `_send_and_wait`)
- `device_agent/state.py` (transicoes estritas opcionais)
- `tests/test_device_agent.py` (56 testes)
- `tests/test_device_agent_apk_manifest.py` (3 testes de superficie)
- `device_agent_apk/app/src/main/AndroidManifest.xml` (sem Qa/Provision)
- `device_agent_apk/app/src/debug/AndroidManifest.xml` (Qa + Provision `exported=true`)
- `ProvisionActivity.kt` movida para `src/debug/java` (sem gate por referrer)

## 2. P1 - Reserva atomica do command_id (controller e autoridade)

Fluxo atual de `send_command`:

1. UUID valido; leitura otimista de `get_command` (se existe, valida binding e retorna cached/
   conflict/duplicate, sem send).
2. Valida op, agente/capabilities, params/bounds, allowlist e deadline.
3. Cria `CommandRecord` QUEUED (ou EXPIRED, se deadline ja venceu) e chama
   `store.save_command` (**INSERT OR IGNORE**, atomico no SQLite).
4. Se `inserted == false`: recarrega o registro e aplica:
   - binding (`agent_id`, `device_id`, `op`) divergente => `COMMAND_ID_CONFLICT`;
   - terminal => resultado persistido;
   - nao-terminal => `DUPLICATE_IN_PROGRESS`.
5. Somente `inserted == true` chega a `session.send()`.

`_send_and_wait` nao cria mais registro e nao pode salvar duas vezes; ele apenas envia, marca
SENT e aguarda. Duas chamadas concorrentes com o mesmo UUID: exatamente uma insere e envia;
a outra recebe DUPLICATE_IN_PROGRESS. Depois de terminal (SUCCESS/FAILURE/EXPIRED/UNCERTAIN/
CANCELLED), a mesma UUID sempre retorna o resultado persistido, sem send, independente do cache
do APK.

Deadline expirado tambem reserva um registro terminal EXPIRED (idempotente): a UUID nao pode ser
reutilizada depois para executar.

Binding e validado em toda leitura existente; UUID de outro agente/device/op => conflict.

## 3. State machine SQLite estrita (small hardening)

```
QUEUED -> SENT
QUEUED -> SUCCESS | FAILURE | EXPIRED | UNCERTAIN | CANCELLED
SENT   -> SUCCESS | FAILURE | EXPIRED | UNCERTAIN | CANCELLED
```

Proibidos (retornam False, sem update): `SENT -> QUEUED`, `QUEUED -> QUEUED` e qualquer transicao
a partir de terminal. Update continua condicional (`WHERE command_id=? AND status=?`) e retorna
bool. Inserts continuam via `INSERT OR IGNORE`.

## 4. P2 - Provision Activity security

- `main/AndroidManifest.xml`: sem `ProvisionActivity` e sem `QaActivity`.
- `debug/AndroidManifest.xml`: `QaActivity exported=true` e `ProvisionActivity exported=true`
  (bootstrap ADB preservado no debug).
- Release merged: **nenhuma** das duas atividades (verificado em
  `merged_manifests/release/processReleaseManifest/AndroidManifest.xml`).
- Debug merged: ambas presentes e `exported=true`.
- `ProvisionActivity` nao usa mais `referrer`/`callingPackage` como autenticacao; permanece apenas
  no sourceSet debug (nao existe em release). Validacao de URL `wss://` mantida como input check.

Tabela de superficies validada:

| Manifest | QaActivity | ProvisionActivity |
|---|---|---|
| debug merged | presente, exported=true | presente, exported=true |
| release merged | ausente | ausente |

## 5. Testes novos (P1/P2)

- A. 2 chamadas simultaneas mesmo UUID/TAP: 1 success + 1 DUPLICATE_IN_PROGRESS; agent executa 1x.
- B. mesmo UUID apos SUCCESS: cached success, sem send.
- C. mesmo UUID apos UNCERTAIN: uncertain persistido, sem send.
- D. mesmo UUID em outro agente: `COMMAND_ID_CONFLICT`; outro agente executa 0.
- E. mesmo UUID com outro op: `COMMAND_ID_CONFLICT`.
- F. perda de cache do APK: novo agente/mesma UUID recebe cached success sem executar.
- SQLite: QUEUED->QUEUED e SENT->QUEUED proibidos; terminal nao regride.
- Manifest: main sem Qa/Provision; debug ambos exported; merged debug/release quando presentes.

Regressao total: **59/59 OK** (`tests.test_device_agent` + `tests.test_device_agent_apk_manifest`).
`compileall device_agent`: OK.

## 6. Smoke real .124

- Debug provisioning via ADB: `am start ProvisionActivity --es controller_url wss://127.0.0.1:8766`
  -> log `PROVISION agent=01bedbfd-... url=wss://127.0.0.1:8766` (debug exported).
- Controller/agent conectado; `PING success`; QA Activity debug; 1 TAP success.
- Mesmo UUID repetido: ambas respostas `success`; contador QA **0 -> 1** (uma unica execucao).
- Resultado: `SMOKE PASS` (`state/device_agents/e32b/e33a2_smoke.json`).

Nao foi repetido benchmark 30+30: TAP/SWIPE e caminho de gesture nao foram alterados nesta fase.

## 7. Criterio final

1. Controller e autoridade primaria de dedupe: **SIM** (reserva antes de qualquer send).
2. Nenhum duplicate chega duas vezes ao APK: **SIM** (inserted unico; demais cached/duplicate/conflict).
3. Conflito de UUID detectado por agent/device/op: **SIM** (`COMMAND_ID_CONFLICT`).
4. Release sem ProvisionActivity exportada: **SIM** (ausente no merged release).
5. Regressoes PASS: **SIM** (59/59 + smoke real).

**E3.3A PRODUCTION BASELINE = FROZEN**

## 8. Riscos residuais

- Reserva ocorre apos validacao de sessao/fila: tentativa offline/QUEUE_FULL nao consome a UUID
  (pode ser reutilizada depois), por design.
- Cheque de `queue_cap` e `inflight` nao e atomico com a reserva (pre-existente; nao afeta dedupe).
- Mesma UUID + mesmo op com params diferentes retorna o resultado persistido (binding exige apenas
  agent/device/op, conforme especificado).
- Release APK completo continua dependendo de `lint-gradle` fora do cache offline; manifests
  merged e source sets foram validados, e o bootstrap operacional nao depende de Provision exposta.
