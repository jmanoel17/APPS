# E3.10C/D - DPN mutations through the Device Agent pool

Status: **FINAL_TEST_READY**
Date: 2026-11-23
Interface: **not changed in this phase**.

## Architecture

```text
GroupLedgerCoordinator
        -> DeeperConnectProvider
        -> AgentPoolAtomOS (sync adapter)
        -> AgentPoolClient / DPNTransportPool
        -> selected unified Device Agent
        -> DpnOps in APK 0.5
        -> AtomOS switchNode / refreshTunnel
```

The coordinator never receives `agent_id`. The pool selects a transport by
`physical_dpn_id` and capability. The coordinator continues to own the state
machine, polling, epoch and refresh convergence.

## APK operations (version 0.5)

`DPN_SWITCH_NODE`:
- validates `expected_dpn_id`, `tunnel_code`, `current_ip`;
- performs `GET_DPN_IDENTITY` and rejects mismatch with
  `DPN_IDENTITY_MISMATCH` before any mutation;
- calls only `POST /api/smartRoute/switchNode`;
- `ipNotChanged` returns `IP_NOT_CHANGED` (not a connectivity error);
- pre-dispatch deadline -> `expired`, no execution;
- network/response loss after dispatch -> `uncertain`;
- no automatic retry.

`DPN_REFRESH_ROUTE_SET`:
- validates `expected_dpn_id` and `tunnel_code`;
- calls only `refreshTunnel(tunnelCode)`;
- success means AtomOS accepted the request (`REQUESTED`), not that the new
  route list has already converged;
- post-dispatch loss -> `uncertain`.

Both operations use the existing Device Agent command cache/server dedupe by
`command_id`. Mutations are serialized by the Device Agent mutation executor.

## Controller

- `DPNMutationLeaseManager`: one lease per `physical_dpn_id`, generation,
  operation/command/holder, TTL and explicit `UNCERTAIN` release.
- `DPNAuthCoordinator`: one auth attempt at a time per physical DPN and shared
  backoff on `DPN_UNAUTHORIZED` / `DPN_AUTH_BACKOFF`.
- `AgentPoolClient.execute_mutation()`:
  - pre-dispatch failure -> next Agent;
  - post-dispatch `uncertain` -> no failover, no retry;
  - lease busy -> `BUSY`, no dispatch;
  - auth failure -> `AUTH_BACKOFF` for the physical DPN.
- `reconcile_switch()` uses a pool read (`LIST_DPN_TUNNELS`) on another healthy
  Agent and compares `old_ip` with the active IP. It never repeats the mutation.
- Controller pool metrics are published in `state/dpn/agents_status.json`.

`transport=agent_pool` is supported by `DeeperConnectProvider` and
`provider_factory`. `transport=agent` remains the fixed-Agent legacy path.
`dpn_coordinator_run.Runner` accepts an injected `agent_pool`; it does not create
a second WSS. The controller can host the embedded coordinator when
`dpn_embed_agent_pool_coordinator=true` (test overlay).

## Configuration

```text
dpn_agent_pool_enabled=true          # dev profile
device_agent_dpn_ops_enabled=true    # dev profile
dpn_routing_enabled=true             # dev profile
dpn_group_ledger_v2_enabled=true     # dev profile
dpn_embed_agent_pool_coordinator=true
dpn_groups_file=dpn_groups_e310_agent_pool.json
dpn_mutation_lease_s=30
dpn_auth_backoff_s=300
dpn_pool_stale_s=60
dpn_agent_controller_port=8766
```

The implementation defaults remain OFF; the **dev profile** (`config_sln_v2.json`) now enables the
agent-pool/embedded-coordinator path for testing. Production groups must be supplied separately.

Test-only overlay: `config_e310_agent_pool.json` and
`dpn_groups_e310_agent_pool.json`.

## Offline tests

`tests/test_dpn_pool_mutations.py` covers:
- lease busy/expiry/generation;
- pre-dispatch failover A -> B;
- post-dispatch uncertain and no second call;
- auth failure shared backoff;
- reconcile via read;
- missing capability exclusion;
- provider/factory `agent_pool` wiring.

## Real checkpoint

DPN: `Deeper-Wire-HW-17bf3b402b4a|e6960e1a4882b589`
Tunnel: `BR`
Pool: 6 authenticated Agents available.

### B - switch

```text
command_id: 375415b87f3e46beac154edc1d2441f2
agent:      246bf499-ded9-4051-94b6-3dd95c2126c6
lease:      a1f1d215e9734b6d9020fa00e9fbf847
old_ip:     179.55.111.130
new_ip:     45.175.50.49
result:     SUCCESS
```

A health read after the mutation used another Agent (`e213...`) and observed
`45.175.50.49`.

### Refresh

```text
command_id: 8a738ad4663b4dff9edfddfae048d2b0
agent:      246bf499-ded9-4051-94b6-3dd95c2126c6
tunnel:     BR
result:     REQUESTED
```

A read after convergence observed `179.73.207.177`.

### C - pre-dispatch failover

The preferred Agent `246bf499...` was stopped before dispatch. The next Agent
(`e213554e...`) executed exactly one switch:

```text
command_id: e09a4f2f2b11439db828b2a3f1da64c5
old_ip:     179.73.207.177
new_ip:     179.55.111.130
result:     SUCCESS
```

A read by a third Agent (`39fc0ba4...`) confirmed `179.55.111.130`.

### Counters

```text
mutation_count=3
switch_success=2
refresh_requested=1
mutation_uncertain=0
lease_busy=0
auth_backoff=0
exactly_once=true
```

`UNCERTAIN` post-dispatch was validated with offline fault injection, not by
causing a real AtomOS failure.

## E2E coordinator path (real)

The embedded coordinator ran with `config_e310_agent_pool.json` and the test-only
`DPN_POOL_TEST` group (`transport=agent_pool`, tunnel `BR`).

### Coordinator rotate

```text
command:  ad17c38e074149229ab05a2968985ee5 (rotate_now)
old_ip:   179.55.111.130
new_ip:   189.27.169.40
agent:    246bf499-ded9-4051-94b6-3dd95c2126c6
result:   SUCCESS
```

### Coordinator refresh

```text
command: 471cfe0b05624ffb9b5c2951bea517c1 (refresh_routes_now)
refresh_attempt: 1
result: REQUESTED
observed_ip: 200.140.200.58
state_after: DEGRADED (test overlay max_route_refreshes=1, no route change)
```

Exactly one physical `DPN_REFRESH_ROUTE_SET` was sent for that refresh request.

### Read failover

The preferred read Agent `39fc0ba4-a045-48dc-a07c-718fb8260047` was stopped. The
next read selected `246bf499-ded9-4051-94b6-3dd95c2126c6` and succeeded.

### APK JVM tests

`gradle :app:testDebugUnitTest :app:assembleDebug` passed, covering DPN IP/tunnel
validation, `DPN_UNCERTAIN` status preservation, auth/failure classification and
`ipNotChanged` mapping.

## Deliverables

- `state/device_agents/e310_dpn_pool_report.json`
- `state/device_agents/e310_dpn_pool_commands.json`
- `state/device_agents/e310_dpn_pool_failover.json`

## Not changed

Fast Close, reward gate, FORMAT, SUB_AD_CLOSE, Store/BACK, IA, ad_session,
CYCLE_ACCOUNTING_V2, reset tree, work-unit semantics, Group Ledger epochs,
drain overshoot and the interface.
