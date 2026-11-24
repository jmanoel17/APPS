# E3.10A/B/D/E - Unified Device Agent + DPN Transport Pool

Status: **MUTATION_REAL_PASS / IDENTITY_THROTTLE_OBSERVED**
Scope completed: E3.10A read-only DpnOps, E3.10B presence/read pool, and the read-only controller/interface slice of E3.10D/E.
Not implemented: E3.10C mutation lease, DPN mutations through unified Agents, deprecation.

## E3.10A - DpnOps in Device Agent

APK version `0.4` adds read-only operations to `sln-device/1`:

- `GET_DPN_IDENTITY`
- `GET_DPN_STATUS`
- `LIST_DPN_TUNNELS`
- `GET_DPN_MODE`
- `GET_DPN_NODE_LB_MODE`
- `LIST_DPN_ACCESS_CONTROL`

Implementation: `device_agent_apk/.../DpnClient.kt`, wired through `CommandExecutor.kt`.
No raw shell, raw HTTP, generic endpoint or generic JSON-RPC is exposed. AtomOS user/password
stays in Android Keystore (`Prefs.atomosPassword`); password is never logged or sent over WSS.

`GET_DPN_IDENTITY` reads the real AtomOS `deviceId`/hardware/software/mode; `physical_dpn_id`
is not the Android agent_id/android_id/MAC.

Provisioning (debug only) accepts `atomos_url`, `atomos_fp`, `atomos_password` extras and logs
only whether a password exists.

## E3.10B - Presence + Transport Pool

- APK heartbeat now includes `info.dpn_presence` when AtomOS is configured:
  `reachable`, `physical_dpn_id`, `latency_ms`, `authenticated`, `capabilities`, `observed_at_ms`.
- Optional probe cadence: one cached probe per 5 min (success) / 15 min (failure), gated by `dpn_auto_probe` (default off); it never blocks the heartbeat/UI.
- Controller `device_agent/server.py` accepts an optional `PresenceRegistry` and indexes
  heartbeat presence by `physical_dpn_id`.
- `dpn/transport_pool.py`:
  - `PresenceRegistry` (atomic JSON `state/dpn/presence.json`).
  - `DPNTransportPool.select_read_transport(physical_dpn_id, capability)`.
  - Deterministic order: online -> authenticated -> capability -> lowest latency -> last success.
  - Stale heartbeat (`dpn_presence_stale_s`) is excluded.
  - `read()` failover across Agents only on pre-dispatch failures; `TransportUncertain` is never
    retried. Failovers are recorded with from/to Agent.
- No mutation lease or mutation path is implemented in this checkpoint.

## E3.10D/E (read-only) - Controller + interface

- `dpn/agent_pool.py`: controller-side `AgentPoolClient` indexes online unified Agents,
  resolves physical DPN identities on demand, and carries typed read failover by
  `physical_dpn_id`.
- `dpn_agent_controller.py`: WSS controller (`sln-device/1`) on `8766`, with the
  matching `state/device_agents/tls` certificate, presence registry, pairing-code
  support, ADB reverse maintenance, `agents_status.json`, and a file command bus
  (`state/dpn/agent_commands/`).
- `dpn/ui_model.py`: pure rows for Agent/DPN summaries.
- `interface_sln_v2.py`: **Agentes DPN** tab with controller status, Agent rows,
  `Atualizar identidades`, `Gerar codigo` and `Iniciar Agent Controller`.
- `device_agent/server.py`: heartbeat refreshes liveness for a known Agent without
  forcing another AtomOS login.

Commands are explicit/on-demand; there is no automatic AtomOS probe fan-out.

### E3.10C/D - Mutations

`DPN_SWITCH_NODE` and `DPN_REFRESH_ROUTE_SET` are implemented in APK 0.5 and routed through
`AgentPoolClient` with a per-`physical_dpn_id` mutation lease, auth backoff, pre-dispatch failover,
post-dispatch `UNCERTAIN`, and read-only reconciliation. `DeeperConnectProvider` supports
`transport=agent_pool`; legacy `transport=agent` remains unchanged. See
`docs/E310_DPN_POOL_MUTATIONS.md` for the real switch/refresh/failover evidence.

## Evidence (2026-11-23)

- Unified APK installed on the current farm: **58/58** successful installs.
- Same AtomOS profile (URL/fingerprint/password) provisioned on **58/58**;
  controller pairing completed for **58/58**.
- Controller status: **58 Agents online**, WSS listener `0.0.0.0:8766`.
- `refresh_identities` is bounded (`dpn_agent_refresh_max=8`) and was tested with
  one Agent; AtomOS returned `DPN_UNAUTHORIZED`/previous `tooManyAttempts`, so the
  real multi-Agent identity checkpoint remains blocked by login throttling.
- No DPN mutation, no CAS cycle, no raw shell/HTTP endpoint.

## Evidence

- APK debug: `device_agent_apk/app/build/outputs/apk/debug/app-debug.apk`, BUILD SUCCESSFUL.
- Python tests: **252/252 OK**, including `test_dpn_transport_pool.py` (7) and
  `test_device_agent_presence.py` (4).
- Pool mock: 3 Agents (`31/17/24 ms`) -> preferred B; A offline -> B; A read timeout -> B with
  `transport_failover`; wrong/blank physical ID excluded.
- No Agent mutations, no DPN mutation, no Fast Close/accounting changes.

## Real checkpoint status

The required real checkpoint is **not passed**:

- The farm now has 58 unified Agents online, but AtomOS login remains throttled
  (`tooManyAttempts` / `DPN_UNAUTHORIZED`) after the earlier bulk authentication test.
- Physical DPN grouping is therefore only partially populated; no 3-Agent same-DPN
  identity pool has been proven live yet.
- DPN mutations were deliberately not executed.

## Config

```text
device_agent_dpn_ops_enabled=false
dpn_agent_pool_enabled=false
dpn_presence_interval_s=20
dpn_presence_stale_s=60
dpn_mutation_lease_s=30
dpn_agent_controller_host=0.0.0.0
dpn_agent_controller_port=8766
dpn_agent_refresh_max=8
dpn_agent_request_timeout_s=25
dpn_agent_reverse_interval_s=30
```

Defaults remain OFF; legacy DPN Agent/transport remains rollback.
