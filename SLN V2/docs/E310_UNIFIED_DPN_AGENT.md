# E3.10A/B - Unified Device Agent + DPN Transport Pool

Status: **OFFLINE_PASS / REAL_CHECKPOINT_BLOCKED**  
Scope completed: E3.10A read-only DpnOps in the unified Device Agent + E3.10B presence registry/read pool.  
Not started by design: E3.10C/D/E/F (mutation lease, provider pool, SLN DPN UI, deprecation).

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
- Probe cadence: 20 s, cached and asynchronous (never blocks the heartbeat/UI).
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

## Evidence

- APK debug: `device_agent_apk/app/build/outputs/apk/debug/app-debug.apk`, BUILD SUCCESSFUL.
- Python tests: **252/252 OK**, including `test_dpn_transport_pool.py` (7) and
  `test_device_agent_presence.py` (4).
- Pool mock: 3 Agents (`31/17/24 ms`) -> preferred B; A offline -> B; A read timeout -> B with
  `transport_failover`; wrong/blank physical ID excluded.
- No Agent mutations, no DPN mutation, no Fast Close/accounting changes.

## Real checkpoint status

The required real checkpoint is **not passed**:

- The unified Device Agent APK is not installed on `.50`, `.99`, `.127`.
- `%LOCALAPPDATA%\dpn\dpn.db` maps those three Android serials to **three distinct DPNs**
  (`expected_dpn_id` differs), not three Agents of the same physical DPN.
- Therefore there is no 3-Agent same-DPN pool to test yet. `GET_DPN_IDENTITY` was not exercised
  live on 3 same-DPN devices.
- DPN mutations were deliberately not executed.

## Config

```text
device_agent_dpn_ops_enabled=false
dpn_agent_pool_enabled=false
dpn_presence_interval_s=20
dpn_presence_stale_s=60
dpn_mutation_lease_s=30
```

Defaults remain OFF; legacy DPN Agent/transport remains rollback.
