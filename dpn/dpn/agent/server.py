"""Servidor WSS do controlador (conexao persistente com os agentes).

Roda em um thread proprio (asyncio) e expoe uma API thread-safe para a UI.
Nunca executa shell; apenas entrega comandos tipados ao agente autenticado.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from websockets.asyncio.server import serve

from dpn.agent.protocol import (
    PROTOCOL_VERSION,
    Command,
    MsgType,
    Result,
    ResultStatus,
    validate_command,
)


class AgentNotConnected(Exception):
    pass


@dataclass
class _Conn:
    agent_id: str
    ws: object
    observed_ip: str
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_hb: float = 0.0


class AgentServer:
    def __init__(
        self,
        host: str,
        port: int,
        cert_path: str,
        key_path: str,
        authenticate: Callable[[str, str], bool],
        on_pair: Callable[[str, str, dict], str | None],
        on_event: Callable[[str, dict], None],
        on_command_result: Callable[[str, str, Result], None] | None = None,
    ):
        self.host = host
        self.port = port
        self.cert_path = cert_path
        self.key_path = key_path
        self._authenticate = authenticate
        self._on_pair = on_pair
        self._on_event = on_event
        self._on_command_result = on_command_result

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None
        self._connections: dict[str, _Conn] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._handled_ids: dict[str, float] = {}
        self._ready = threading.Event()
        self._start_error = ""

    # -------------------------------------------------------------- ciclo de vida
    def start(self) -> bool:
        """Inicia o servidor. Retorna False se nao conseguiu abrir a porta/Cert."""
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="agent-server")
        self._thread.start()
        self._ready.wait(timeout=10)
        return self._ready.is_set() and not self._start_error

    @property
    def start_error(self) -> str:
        return self._start_error

    def stop(self) -> None:
        if self._loop is None:
            return

        async def _shutdown():
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
        try:
            if not self._loop.is_closed() and self._server is not None:
                fut = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
                fut.result(timeout=5)
        except Exception:
            pass
        try:
            if not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except RuntimeError:
            pass  # loop parado no shutdown
        except Exception:
            pass
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.cert_path, self.key_path)
            self._server = await serve(
                self._handler, self.host, self.port, ssl=ctx,
                ping_interval=20, ping_timeout=20, max_size=4 * 1024 * 1024,
            )
        except Exception as exc:  # porta em uso / certificado invalido
            self._start_error = str(exc)
            self._ready.set()
            return
        self._ready.set()
        await asyncio.Future()

    # ------------------------------------------------------------------- handler
    async def _handler(self, ws) -> None:
        peer = ws.remote_address
        observed_ip = peer[0] if peer else ""
        agent_id: str | None = None
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    await ws.send(json.dumps({"type": MsgType.ERROR.value,
                                              "error": "json inválido"}))
                    continue
                mtype = msg.get("type")

                if agent_id is None:
                    if mtype == MsgType.PAIR_REQUEST.value:
                        agent_id = await self._handle_pair(ws, msg, observed_ip)
                        continue
                    if mtype == MsgType.HELLO.value:
                        aid = str(msg.get("agent_id", ""))
                        token = str(msg.get("token", ""))
                        if not aid or not self._authenticate(aid, token):
                            await ws.send(json.dumps({
                                "type": MsgType.ERROR.value,
                                "error": "não autorizado"}))
                            await ws.close()
                            return
                        agent_id = aid
                        self._connections[agent_id] = _Conn(aid, ws, observed_ip,
                                                            last_hb=time.time())
                        await ws.send(json.dumps({"type": MsgType.HELLO_OK.value,
                                                  "protocol": PROTOCOL_VERSION,
                                                  "agent_id": aid}))
                        self._on_event("agent_online", {"agent_id": aid,
                                                        "observed_ip": observed_ip})
                        continue
                    await ws.send(json.dumps({"type": MsgType.ERROR.value,
                                              "error": "handshake esperado"}))
                    continue

                # autenticado
                if mtype == MsgType.HEARTBEAT.value:
                    conn = self._connections.get(agent_id)
                    if conn:
                        conn.last_hb = time.time()
                    self._on_event("heartbeat", {"agent_id": agent_id,
                                                 "info": msg.get("info") or {}})
                elif mtype == MsgType.RESULT.value:
                    await self._handle_result(agent_id, msg)
                else:
                    await ws.send(json.dumps({"type": MsgType.ERROR.value,
                                              "error": "mensagem inesperada"}))
        except Exception:
            pass
        finally:
            if agent_id and self._connections.get(agent_id, None) is not None:
                conn = self._connections.get(agent_id)
                if conn is not None and conn.ws is ws:
                    self._connections.pop(agent_id, None)
                    self._on_event("agent_offline", {"agent_id": agent_id})

    async def _handle_pair(self, ws, msg: dict, observed_ip: str) -> str | None:
        aid = str(msg.get("agent_id", ""))
        code = str(msg.get("pairing_code", ""))
        info = msg.get("info") or {}
        token = self._on_pair(aid, code, info)
        if not token:
            await ws.send(json.dumps({"type": MsgType.PAIR_DENIED.value,
                                      "error": "código inválido ou expirado"}))
            await ws.close()
            return None
        self._connections[aid] = _Conn(aid, ws, observed_ip, last_hb=time.time())
        await ws.send(json.dumps({"type": MsgType.PAIR_OK.value,
                                  "protocol": PROTOCOL_VERSION,
                                  "agent_id": aid, "token": token}))
        self._on_event("agent_online", {"agent_id": aid, "observed_ip": observed_ip,
                                        "local_ip": info.get("local_ip", "")})
        return aid

    async def _handle_result(self, agent_id: str, msg: dict) -> None:
        rid = str(msg.get("request_id", ""))
        self._handled_ids[rid] = time.time()
        # limpeza oportunista
        if len(self._handled_ids) > 2000:
            cutoff = time.time() - 600
            self._handled_ids = {k: v for k, v in self._handled_ids.items() if v > cutoff}
        fut = self._pending.pop(rid, None)
        if fut is not None and not fut.done():
            fut.set_result(Result.from_dict(msg))
        if self._on_command_result:
            try:
                self._on_command_result(agent_id, rid, Result.from_dict(msg))
            except Exception:
                pass

    # ------------------------------------------------------------------ comandos
    def is_online(self, agent_id: str) -> bool:
        return agent_id in self._connections

    def send_configure_blocking(self, agent_id: str, atomos_url: str,
                                atomos_password: str,
                                association_name: str = "",
                                timeout: float = 15.0) -> Result:
        """Envia configuracao sensivel (senha AtomOS) pelo canal WSS protegido.

        A senha NUNCA vai para logs nem argumentos de shell/ADB.
        """
        from dpn.agent.protocol import Command, Op
        cmd = Command(op=Op.CONFIGURE.value, agent_id=agent_id,
                      params={"atomos_url": atomos_url,
                              "atomos_password": atomos_password,
                              "association_name": association_name},
                      deadline=time.time() + timeout)
        return self.send_command_blocking(cmd, timeout)

    def online_agents(self) -> list[str]:
        return list(self._connections.keys())

    def send_command_blocking(self, cmd: Command, timeout: float) -> Result:
        if self._loop is None:
            raise AgentNotConnected("servidor não iniciado")
        fut = asyncio.run_coroutine_threadsafe(self._send_and_wait(cmd), self._loop)
        return fut.result(timeout + 2.0)

    async def _send_and_wait(self, cmd: Command) -> Result:
        conn = self._connections.get(cmd.agent_id)
        if conn is None:
            raise AgentNotConnected(f"agente {cmd.agent_id} offline")
        reason = validate_command(cmd, cmd.agent_id)
        if reason:
            return Result(cmd.request_id, cmd.agent_id, cmd.op,
                          ResultStatus.EXPIRED.value if "expirado" in reason
                          else ResultStatus.FAILURE.value, error=reason)
        loop_fut: asyncio.Future = self._loop.create_future()
        self._pending[cmd.request_id] = loop_fut
        try:
            async with conn.send_lock:
                await conn.ws.send(json.dumps(cmd.to_dict()))
            remaining = max(0.5, cmd.deadline - time.time())
            return await asyncio.wait_for(loop_fut, timeout=remaining)
        except asyncio.TimeoutError:
            return Result(cmd.request_id, cmd.agent_id, cmd.op,
                          ResultStatus.UNCERTAIN.value,
                          error="sem confirmação (timeout)")
        finally:
            self._pending.pop(cmd.request_id, None)
