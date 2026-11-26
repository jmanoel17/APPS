"""AgentManager: liga o servidor WSS (thread asyncio) a interface Qt.

O servidor chama callbacks de outro thread; aqui empilhamos eventos e drenamos
com um QTimer no thread da UI, emitindo sinais. Os comandos sao enviados de
threads de trabalho (run_authenticated) e sao thread-safe.
"""
from __future__ import annotations

import queue
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from dpn.agent.certs import ensure_server_cert
from dpn.agent.protocol import Command
from dpn.agent.provision import ensure_adb_reverse
from dpn.agent.registry import AgentRecord, AgentRuntime, PairingManager, hash_token, new_token
from dpn.agent.server import AgentServer

# Motivos que indicam falha de autenticacao na unidade (nao de rede).
_AUTH_REASONS = ("wrongField", "wrongPassword", "tooManyAttempts", "invalidToken")
# Motivos que indicam que a unidade RESPONDEU (erro de negocio), ou seja,
# continua alcancavel — nao devem marcar o estado como "unreachable".
_REACHABLE_REASONS = ("ipNotChanged",)


def _atomos_status_for_failure(reason: str) -> str:
    """Estado do AtomOS quando a operacao falhou, a partir do `reason`."""
    if reason in _AUTH_REASONS:
        return "auth_failed"
    if reason in _REACHABLE_REASONS:
        return "connected"      # unidade respondeu; nao "envenena" o status
    return "unreachable"


class AgentManager(QObject):
    agentsChanged = Signal()
    event = Signal(str, dict)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        cfg = service.config
        self.cert_path, self.key_path, self.fingerprint = ensure_server_cert(cfg.data_dir)
        self.pairing = PairingManager()
        self.runtimes: dict[str, AgentRuntime] = {}
        self._paired_by_code: dict[str, str] = {}
        self._queue: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._started = False
        self._reverse_inflight = False
        self.last_server_error = ""

        self.server = AgentServer(
            host=cfg.agent_host, port=cfg.agent_port,
            cert_path=self.cert_path, key_path=self.key_path,
            authenticate=self._authenticate, on_pair=self._on_pair,
            on_event=self._on_event,
        )
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._drain)
        self._timer.start()
        # self-heal do tunel adb reverse (redes que bloqueiam conexao iniciada
        # pelo aparelho): recria periodicamente para agentes offline.
        self._reverse_timer = QTimer(self)
        self._reverse_timer.setInterval(15000)
        self._reverse_timer.timeout.connect(self._ensure_reverses)

    # ------------------------------------------------------------------ ciclo
    def start(self) -> bool:
        if self._started:
            return not self.last_server_error
        ok = self.server.start()
        self._started = True
        self.last_server_error = "" if ok else (self.server.start_error or "porta indisponível")
        if ok:
            self._ensure_reverses()
            self._reverse_timer.start()
        return ok

    def stop(self) -> None:
        self._timer.stop()
        self._reverse_timer.stop()
        if self._started:
            self.server.stop()
            self._started = False

    def _ensure_reverses(self) -> None:
        """Recria o `adb reverse` dos agentes offline (idempotente, fora da UI).

        So e necessario quando a rede bloqueia conexoes iniciadas pelo aparelho;
        nesse cenario o agente alcanca o controlador apenas por 127.0.0.1, que so
        funciona com o mapeamento `adb reverse` ativo. Rodamos a cada 15s apenas
        para quem esta offline, evitando custo quando tudo esta conectado.
        """
        if not self._started or self._reverse_inflight:
            return
        try:
            devices = self.service.list_devices()
        except Exception:  # noqa: BLE001
            return
        port = self.server.port
        targets: list[str] = []
        for dev in devices:
            if dev.transport_mode != "agent":
                continue
            serial = (dev.android_serial or "").strip()
            if not serial:
                continue
            rt = self.runtimes.get(dev.agent_id) if dev.agent_id else None
            if rt is not None and rt.online:
                continue
            targets.append(serial)
        if not targets:
            return

        def work() -> None:
            try:
                for serial in targets:
                    try:
                        ensure_adb_reverse(serial, port)
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                self._reverse_inflight = False

        self._reverse_inflight = True
        threading.Thread(target=work, daemon=True, name="agent-reverse").start()

    # -------------------------------------------------------------- callbacks
    def _authenticate(self, agent_id: str, token: str) -> bool:
        rec = self.service.get_agent(agent_id)
        return bool(rec and rec.token_hash and rec.token_hash == hash_token(token))

    def _on_pair(self, agent_id: str, code: str, info: dict) -> str | None:
        if not self.pairing.consume(code):
            return None
        token = new_token()
        self.service.upsert_agent(AgentRecord(
            agent_id=agent_id, token_hash=hash_token(token),
            name=str(info.get("name", "")), local_ip=str(info.get("local_ip", "")),
            paired_at=time.time(), last_seen=time.time(),
        ))
        # correlaciona a credencial de provisionamento ao agent_id autenticado
        self._paired_by_code[code] = agent_id
        return token

    def paired_agent_for(self, code: str) -> str | None:
        return self._paired_by_code.get(code)

    def _on_event(self, event: str, data: dict) -> None:
        self._queue.put((event, data))

    def _drain(self) -> None:
        changed = False
        while True:
            try:
                event, data = self._queue.get_nowait()
            except queue.Empty:
                break
            aid = data.get("agent_id")
            rt = self.runtimes.setdefault(aid, AgentRuntime(agent_id=aid)) if aid else None
            if event == "agent_online" and rt:
                rt.online = True
                rt.observed_ip = str(data.get("observed_ip", ""))
                if data.get("local_ip"):
                    rt.local_ip = str(data["local_ip"])
                rt.last_heartbeat = time.time()
                self.service.touch_agent(aid)
            elif event == "agent_offline" and rt:
                rt.online = False
                rt.last_error = "agente desconectado"
            elif event == "heartbeat" and rt:
                rt.last_heartbeat = time.time()
                info = data.get("info") or {}
                # heartbeat NAO valida identidade (pode ser valor historico):
                # apenas atualiza o runtime para exibicao.
                if info.get("observed_dpn_id"):
                    rt.observed_dpn_id = str(info["observed_dpn_id"])
                if info.get("atomos_status"):
                    rt.atomos_status = str(info["atomos_status"])
                self.service.touch_agent(aid)
            changed = True
            self.event.emit(event, data)
        if changed:
            self.agentsChanged.emit()

    def _update_identity(self, agent_id: str, observed_id: str) -> None:
        rt = self.runtimes.setdefault(agent_id, AgentRuntime(agent_id=agent_id))
        rt.observed_dpn_id = observed_id
        if not self.service.valid_id(observed_id):
            return
        rec = self.service.get_agent(agent_id)
        if rec is None:
            return
        # persiste SEMPRE (mesmo repetido) para reparar cadastros sem ID
        self.service.set_agent_observed_id(agent_id, str(observed_id).strip())
        did = rec.device_id
        if did is None:
            return
        dev = self.service.get_device(did)
        # propaga apenas se o agente for o transporte ATIVO do cadastro
        if dev is not None and dev.transport_mode == "agent":
            self.service.set_observed_identity(
                did, observed_id, origin=f"agent:{agent_id}", ts=time.time())

    # ------------------------------------------------------------------ envio
    def runtime(self, agent_id: str) -> AgentRuntime:
        return self.runtimes.setdefault(agent_id, AgentRuntime(agent_id=agent_id))

    def send(self, agent_id: str, op: str, params: dict | None = None,
             expected_dpn_id: str | None = None, timeout: float = 20.0):
        cmd = Command(op=op, agent_id=agent_id, expected_dpn_id=expected_dpn_id,
                      params=params or {}, deadline=time.time() + timeout)
        rt = self.runtime(agent_id)
        rt.last_op = op
        try:
            result = self.server.send_command_blocking(cmd, timeout)
        except Exception as exc:  # noqa: BLE001
            rt.last_result_status = "failure"
            rt.atomos_status = "agent_unreachable"
            rt.last_error = str(exc)
            from dpn.atomos.errors import ConnectionError as _ConnErr
            raise _ConnErr(f"agente indisponível: {exc}") from exc
        rt.last_result_status = result.status
        if result.observed_dpn_id:
            self._update_identity(agent_id, result.observed_dpn_id)
        if result.ok:
            rt.atomos_status = "connected"
            rt.last_error = ""
        else:
            rt.last_error = result.error or result.reason
            rt.atomos_status = _atomos_status_for_failure(result.reason)
        return result

    # -------------------------------------------------------------- pareamento
    def open_pairing(self, ttl: float = 300.0) -> str:
        return self.pairing.open(ttl)

    def pairing_consumed(self, code: str) -> bool:
        return self.pairing.is_consumed(code)

    def configure(self, agent_id: str, atomos_url: str, password: str,
                  association_name: str = ""):
        """Envia AtomOS URL + senha (uma vez) pelo canal WSS apos o pareamento."""
        return self.server.send_configure_blocking(
            agent_id, atomos_url, password, association_name)
