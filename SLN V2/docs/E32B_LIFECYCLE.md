# E3.2B - Lifecycle / Reboot Matrix (Device Agent)

Data: 2026-11-15
Device: `192.168.1.124:5555` - SM-G975F, Android 12 (sdk 31), **Ethernet `eth0`** (nao Wi-Fi)
Controller: `wss://127.0.0.1:8766` (via `adb reverse`), store persistente `state/device_agents/e32b`
APK: `com.sln.deviceagent` 0.1 (`device_agent_apk/app/build/outputs/apk/debug/app-debug.apk`)

## Baseline (mascarado)

| Campo | Valor |
|---|---|
| android_id | `21bfb2022b031ca0` |
| agent_id (baseline) | `1acd16b1-a23b-4a8a-b834-0668b6dc7d35` |
| agent_id (pos re-provision) | `01bedbfd-cbd7-4130-9eee-55c0f46e91aa` |
| token | presente (hash no controller; nunca logado) |
| accessibility | habilitada (cap `BACK=true`) |
| agent_version / sdk | 0.1 / 31 |

## Matriz

| Evento | Resultado | Auto-recovery | Tempo | Agent ID | Token | Pairing |
|---|---|---|---|---|---|---|
| S7 Accessibility off/on | PASS | caps refresh no heartbeat | - | mantido | mantido | mantido |
| S2 `am kill` (processo) | PASS | FGS sobreviveu (pid igual) | - | mantido | mantido | mantido |
| S3 force-stop + start explicito | PASS | start manual necessario | 1.0 s | mantido | mantido | inalterado |
| S4 Wi-Fi off/on | LIMITATION | nao aplicavel: device em Ethernet | - | - | - | - |
| S4b perda de transporte 60 s (reverse removido) | PASS | self-reconnect com backoff | 25.0 s | mantido | mantido | inalterado |
| S5 controller offline 130 s | PASS | backoff reconnect | 1.5 s | mantido | mantido | inalterado |
| S8 update `install -r` | PASS | app reinicia; caps re-sincronizam | 2.1 s | mantido | mantido | inalterado |
| S6 token revogado | PASS* | AUTH_REVOKED, sem storm | 48.0 s (janela) | mantido | revogado | inalterado |
| S9 `pm clear` + re-provision | PASS | reenrollment obrigatorio | 1.0 s | **novo** | **novo** | **novo pairing** |
| S1 reboot (+ dismiss keyguard) | PASS | BOOT_COMPLETED -> FGS -> hello | 51.4 s | mantido | mantido | inalterado |

- S5: `status=OFFLINE` com age 126.8 s; 5 tentativas de conexao em 130 s; CPU 0.0% e PSS 62.658 KB (~61,2 MB) aos 30 s offline.
- S6*: `status=AUTH_REVOKED`, 1 tentativa em 48 s. O `AUTH_REVOKED_STOP` (3 falhas) nao foi alcancado dentro da janela por causa do backoff - comportamento seguro (sem loop agressivo).
- S8: apos `install -r` o Accessibility rebinda em ~2 s; `caps` pode ficar `false` nesse intervalo (medir com espera).
- S1: primeiro reboot sem dismiss de keyguard nao entregou `BOOT_COMPLETED` em 150 s (sem pid, sem boot_evt). Com `input keyevent 82` + `wm dismiss-keyguard`: `BOOT_RECEIVED`, `SERVICE_START_REQUESTED`, hello automatico em 51,4 s (boot_completed em 52,7 s), mesmo android_id/token.

## Limites e notas de implantacao

1. **Wi-Fi vs Ethernet**: `.124` usa `eth0`; desligar Wi-Fi nao afeta o path do agente. Para o cenario literal de Wi-Fi, repetir em device Wi-Fi-only. O substituto S4b removeu o `adb reverse` por 60 s e validou reconexao (STALE -> hello) sem tempestade (6 tentativas).
2. **Boot antes do unlock**: `BOOT_COMPLETED` fica retido ate o keyguard ser dispensado neste ROM. Para auto-start antes do unlock, implementar `android:directBootAware="true"` + `LOCKED_BOOT_COMPLETED` e mover prefs para device-protected storage (nao implementado).
3. **Revogacao**: o agente para de reconectar agressivamente e registra `AUTH_REVOKED`; o corte apos 3 falhas depende do backoff (leva >48 s).
4. **`pm clear`**: exige novo pairing; o registro antigo e preservado e o novo recebe `possible_reenrollment=true` (mesmo android_id/build).
5. **Force-stop**: bloqueia BootReceiver; start explicito restaura em ~1 s com identidade/token intactos.
6. Logs nao contem token; o app foi parado e o `adb reverse` removido apos a matriz; controller encerrado e porta 8766 livre.
7. Regressao: `tests.test_device_agent` **26/26 OK**; `compileall device_agent dpn` OK.

## Artefatos

- `state/device_agents/e32b/e32b_report.json` (matriz original + baseline + amostras)
- `state/device_agents/e32b/e32b_retest.json` (S8/S4/S1 reteste)
- `state/device_agents/e32b/e32b_retest2.json` (S4b + S1 final)
- Orquestradores: `%TEMP%\opencode\e32b_orchestrate.py`, `e32b_retest.py`, `e32b_s1s4.py`
