#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
device_agent/server.py — controller asyncio (transport-agnóstico) do sln-device/1.

NÃO cria thread por device. Mantém connections[agent_id], pending[command_id],
heartbeats[agent_id]. Sessões são objetos com `async send(text)` e `close()`
(loopback nos testes; WSS real via `serve_wss`, importado LAZILY).

Regras: HELLO autentica (token hash constant-time), capabilities obrigatórias,
deadline absoluto, dedupe por command_id, mutações serializadas por agent,
backpressure por fila (QUEUE_FULL), timeout após envio => UNCERTAIN.
"""
from __future__ import annotations

import asyncio
import json
import time

from . import errors, protocol as P

PRIVILEGED_DENY_EXACT = frozenset((
    "com.sln.deviceagent", "moe.shizuku.privileged.api", "moe.shizuku.manager"))
PRIVILEGED_DENY_PREFIX = ("android", "com.android", "com.google.android", "com.samsung.android")

RESULT_STATUS_MAP = {
    "success": P.SUCCESS,
    "failure": P.FAILURE,
    "expired": P.EXPIRED,
    "uncertain": P.UNCERTAIN,
    "duplicate": P.UNCERTAIN,
}
from .models import AgentRecord, CommandRecord
from .pairing import PairingManager
from .registry import AgentRegistry


class DeviceAgentServer:
    def __init__(self, store, registry=None, pairing=None, queue_cap=32,
                 heartbeat_stale_s=45.0, heartbeat_offline_s=120.0,
                 duplicate_policy="takeover", allowed_packages=None,
                 allowed_privileged_packages=None):
        self.store = store
        self.registry = registry or AgentRegistry(store)
        self.pairing = pairing or PairingManager(store)
        self.queue_cap = int(queue_cap)
        self.heartbeat_stale_s = float(heartbeat_stale_s)
        self.heartbeat_offline_s = float(heartbeat_offline_s)
        self.duplicate_policy = duplicate_policy
        self.allowed_packages = frozenset(allowed_packages or ())
        self.allowed_privileged_packages = frozenset(allowed_privileged_packages or ())
        self.connections = {}
        self.pending = {}
        self._pending_agent = {}
        self.hearts = {}
        self.inflight = {}
        self._locks = {}
        self._closed = False
        self._wss_server = None
        self.metrics = {"commands_ok": 0, "commands_failed": 0, "reconnects": 0,
                        "results_late": 0, "results_invalid": 0, "auth_fail": 0}

    # ------------------------------------------------------------ helpers
    def _lock(self, agent_id):
        lk = self._locks.get(agent_id)
        if lk is None:
            lk = asyncio.Lock()
            self._locks[agent_id] = lk
        return lk

    async def _send(self, session, obj):
        try:
            await session.send(json.dumps(obj))
        except Exception:
            pass

    def _fail_pending_for(self, agent_id, error="COMMAND_TIMEOUT"):
        for cid, aid in list(self._pending_agent.items()):
            if aid != agent_id:
                continue
            fut = self.pending.pop(cid, None)
            self._pending_agent.pop(cid, None)
            self.store.update_command(cid, status=P.UNCERTAIN, finished_at=time.time(), error=error)
            if fut is not None and not fut.done():
                fut.set_result({"command_id": cid, "status": "uncertain", "data": None,
                                "error": error, "agent_id": agent_id})

    # ------------------------------------------------------------ lifecycle
    async def handle_message(self, session, msg):
        if not isinstance(msg, dict):
            return
        mtype = msg.get("type")
        try:
            if mtype == P.PAIR_REQUEST:
                await self._handle_pair(session, msg)
                return
            if mtype == P.HELLO:
                await self._handle_hello(session, msg)
                return
            aid = getattr(session, "agent_id", None)
            if not aid or self.connections.get(aid) is not session:
                await self._send(session, {"type": P.ERROR, "error": errors.UNAUTHORIZED})
                return
            if mtype == P.HEARTBEAT:
                info = msg.get("info") or {}
                self.hearts[aid] = (info, time.time())
                self.store.update_agent_meta(aid, last_seen=time.time(),
                                             display=info.get("display"))
            elif mtype == P.RESULT:
                self._handle_result(aid, msg)
            else:
                await self._send(session, {"type": P.ERROR, "error": errors.UNEXPECTED_MESSAGE})
        except Exception:
            pass

    async def _handle_pair(self, session, msg):
        if msg.get("protocol") != P.PROTOCOL_VERSION:
            await self._send(session, {"type": P.ERROR, "error": errors.PROTOCOL_MISMATCH})
            return
        agent_id = str(msg.get("agent_id") or "")
        code = str(msg.get("pairing_code") or "")
        info = msg.get("info") or {}
        res = self.pairing.consume(code, agent_id, info)
        if not res.get("ok"):
            await self._send(session, {"type": P.PAIR_DENIED, "error": res.get("error")})
            return
        session.agent_id = agent_id
        self.connections[agent_id] = session
        await self._send(session, {"type": P.PAIR_OK, "protocol": P.PROTOCOL_VERSION,
                                   "agent_id": agent_id, "token": res["token"],
                                   "possible_reenrollment": res.get("possible_reenrollment", False)})

    async def _handle_hello(self, session, msg):
        if msg.get("protocol") != P.PROTOCOL_VERSION:
            await self._send(session, {"type": P.ERROR, "error": errors.PROTOCOL_MISMATCH})
            return
        agent_id = str(msg.get("agent_id") or "")
        token = str(msg.get("token") or "")
        if not agent_id or not self.registry.authenticate(agent_id, token):
            self.metrics["auth_fail"] += 1
            await self._send(session, {"type": P.ERROR, "error": errors.UNAUTHORIZED})
            try:
                session.close()
            except Exception:
                pass
            return
        existing = self.connections.get(agent_id)
        if existing is not None and existing is not session:
            if self.duplicate_policy == "takeover":
                self.metrics["reconnects"] += 1
                self.close_session(existing, "takeover")
                self.store.add_event(agent_id, "takeover")
            else:
                await self._send(session, {"type": P.ERROR, "error": errors.DUPLICATE_CONN_REJECTED})
                try:
                    session.close()
                except Exception:
                    pass
                return
        info = msg.get("info") or {}
        caps = msg.get("capabilities") or {}
        self.registry.apply_hello(
            agent_id, capabilities=caps,
            agent_version=msg.get("agent_version") or info.get("agent_version"),
            sdk=info.get("sdk"), android_id=info.get("android_id"),
            build_fingerprint=info.get("build_fingerprint"),
            manufacturer=info.get("manufacturer"), model=info.get("model"),
            display=info.get("display"))
        session.agent_id = agent_id
        self.connections[agent_id] = session
        self.store.add_event(agent_id, "hello")
        await self._send(session, {"type": P.HELLO_OK, "protocol": P.PROTOCOL_VERSION,
                                   "agent_id": agent_id, "server_time_ms": P.now_ms()})

    def _reject_result(self, agent_id, cid, code):
        self.metrics["results_invalid"] += 1
        self.store.add_event(agent_id, "result_rejected", code)

    def _handle_result(self, agent_id, msg):
        cid = str(msg.get("command_id") or "")
        if msg.get("protocol") != P.PROTOCOL_VERSION:
            self._reject_result(agent_id, cid, errors.RESULT_PROTOCOL_MISMATCH)
            return
        rec = self.store.get_command(cid)
        if rec is None:
            self.metrics["results_late"] += 1
            return
        if rec.agent_id != agent_id or str(msg.get("agent_id") or "") != agent_id:
            self._reject_result(agent_id, cid, errors.RESULT_AGENT_MISMATCH)
            return
        if str(msg.get("device_id") or "") != rec.device_id:
            self._reject_result(agent_id, cid, errors.RESULT_DEVICE_MISMATCH)
            return
        if str(msg.get("op") or "") != rec.op:
            self._reject_result(agent_id, cid, errors.RESULT_OP_MISMATCH)
            return
        raw = str(msg.get("status") or "").lower()
        mapped = RESULT_STATUS_MAP.get(raw)
        if mapped is None:
            self._reject_result(agent_id, cid, errors.INVALID_RESULT_STATUS)
            return
        if rec.status in P.TERMINAL:
            self.metrics["results_late"] += 1
            return
        data = msg.get("data")
        error = str(msg.get("error") or "")
        if not self.store.update_command(cid, status=mapped, finished_at=time.time(),
                                         error=error, data=data):
            self.metrics["results_late"] += 1
            return
        fut = self.pending.pop(cid, None)
        self._pending_agent.pop(cid, None)
        if mapped == P.SUCCESS:
            self.metrics["commands_ok"] += 1
        else:
            self.metrics["commands_failed"] += 1
        if fut is not None and not fut.done():
            fut.set_result({"command_id": cid, "status": mapped.lower(), "data": data,
                            "error": error, "agent_id": agent_id})

    # ------------------------------------------------------------ commands
    def _existing_response(self, rec, agent_id, device_id, op):
        if rec.agent_id != agent_id or rec.device_id != (device_id or agent_id) or rec.op != op:
            return {"command_id": rec.command_id, "status": "failure",
                    "error": errors.COMMAND_ID_CONFLICT}
        if rec.status in P.TERMINAL:
            return {"command_id": rec.command_id, "status": rec.status.lower(),
                    "data": rec.data, "error": rec.error, "agent_id": rec.agent_id}
        return {"command_id": rec.command_id, "status": "failure",
                "error": "DUPLICATE_IN_PROGRESS"}

    async def send_command(self, agent_id, op, params=None, deadline_ms=None, timeout=None,
                           command_id=None, device_id=None):
        if self._closed:
            return {"command_id": command_id, "status": "failure", "error": errors.AGENT_OFFLINE}
        params = params or {}
        command_id = command_id or P.new_command_id()
        if not P.is_command_id(command_id):
            return {"command_id": command_id, "status": "failure",
                    "error": errors.INVALID_COMMAND_ID}
        existing = self.store.get_command(command_id)
        if existing is not None:
            return self._existing_response(existing, agent_id, device_id, op)
        if op not in P.OPS:
            return {"command_id": command_id, "status": "failure", "error": errors.UNKNOWN_OP}
        rec = self.store.get_agent(agent_id)
        if rec is None or rec.revoked:
            return {"command_id": command_id, "status": "failure", "error": errors.AGENT_OFFLINE}
        if not (rec.capabilities or {}).get(op, False):
            return {"command_id": command_id, "status": "failure",
                    "error": errors.CAPABILITY_NOT_SUPPORTED}
        reason = P.validate_params(op, params, display=rec.display)
        if reason:
            return {"command_id": command_id, "status": "failure", "error": errors.BAD_PARAMS}
        if op == P.START_APP and params.get("package") not in self.allowed_packages:
            return {"command_id": command_id, "status": "failure",
                    "error": errors.PACKAGE_NOT_ALLOWED}
        if op in (P.STOP_APP, P.CLEAR_APP_DATA):
            pkg = str(params.get("package") or "")
            if pkg in PRIVILEGED_DENY_EXACT or any(
                    pkg == p or pkg.startswith(p + ".") for p in PRIVILEGED_DENY_PREFIX):
                return {"command_id": command_id, "status": "failure",
                        "error": errors.PRIVILEGED_PACKAGE_DENIED}
            if pkg not in self.allowed_privileged_packages:
                return {"command_id": command_id, "status": "failure",
                        "error": errors.PACKAGE_NOT_ALLOWED}
        now = P.now_ms()
        deadline = int(deadline_ms if deadline_ms is not None else now + P.DEFAULT_TTL_MS)
        wait_ms = int(timeout * 1000) if timeout is not None else None
        mutating = op in P.MUTATING_OPS
        dev = device_id or agent_id
        if now > deadline:
            rec_exp = CommandRecord(command_id, agent_id, dev, op, params, P.EXPIRED,
                                    time.time(), deadline_ms=deadline, mutating=mutating,
                                    error=errors.DEADLINE_EXPIRED)
            if not self.store.save_command(rec_exp):
                cur = self.store.get_command(command_id)
                if cur is not None:
                    return self._existing_response(cur, agent_id, device_id, op)
            return {"command_id": command_id, "status": "expired", "error": errors.DEADLINE_EXPIRED}
        session = self.connections.get(agent_id)
        if session is None:
            return {"command_id": command_id, "status": "failure", "error": errors.AGENT_OFFLINE}
        if self.inflight.get(agent_id, 0) >= self.queue_cap:
            return {"command_id": command_id, "status": "failure", "error": errors.QUEUE_FULL}

        reserved = CommandRecord(command_id, agent_id, dev, op, params, P.QUEUED,
                                 time.time(), deadline_ms=deadline, mutating=mutating)
        if not self.store.save_command(reserved):
            cur = self.store.get_command(command_id)
            if cur is None:
                return {"command_id": command_id, "status": "failure",
                        "error": "DUPLICATE_IN_PROGRESS"}
            return self._existing_response(cur, agent_id, device_id, op)

        self.inflight[agent_id] = self.inflight.get(agent_id, 0) + 1
        try:
            if mutating:
                lock = self._lock(agent_id)
                await lock.acquire()
                try:
                    return await self._send_and_wait(agent_id, session, op, params, deadline,
                                                     command_id, device_id, mutating, wait_ms)
                finally:
                    lock.release()
            return await self._send_and_wait(agent_id, session, op, params, deadline,
                                             command_id, device_id, mutating, wait_ms)
        finally:
            self.inflight[agent_id] = max(0, self.inflight.get(agent_id, 0) - 1)

    async def _send_and_wait(self, agent_id, session, op, params, deadline,
                             command_id, device_id, mutating, wait_ms=None):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending[command_id] = fut
        self._pending_agent[command_id] = agent_id
        cmd = P.make_command(agent_id, op, params, deadline, command_id, device_id)
        try:
            await session.send(json.dumps(cmd))
        except Exception:
            self.pending.pop(command_id, None)
            self._pending_agent.pop(command_id, None)
            self.store.update_command(command_id, status=P.FAILURE, finished_at=time.time(),
                                      error=errors.AGENT_OFFLINE)
            return {"command_id": command_id, "status": "failure", "error": errors.AGENT_OFFLINE}
        self.store.update_command(command_id, status=P.SENT, sent_at=time.time())
        remaining = max(0.001, (deadline - P.now_ms()) / 1000.0)
        budget = remaining if wait_ms is None else max(0.001, min(remaining, wait_ms / 1000.0))
        try:
            return await asyncio.wait_for(fut, timeout=budget)
        except asyncio.TimeoutError:
            self.pending.pop(command_id, None)
            self._pending_agent.pop(command_id, None)
            self.metrics["commands_failed"] += 1
            self.store.update_command(command_id, status=P.UNCERTAIN, finished_at=time.time(),
                                      error=errors.COMMAND_TIMEOUT)
            return {"command_id": command_id, "status": "uncertain", "error": errors.COMMAND_TIMEOUT}

    # ------------------------------------------------------------ connections
    def close_session(self, session, reason=""):
        aid = getattr(session, "agent_id", None)
        if aid and self.connections.get(aid) is session:
            del self.connections[aid]
            self._fail_pending_for(aid, "CONNECTION_LOST")
        try:
            session.close()
        except Exception:
            pass

    def status(self, agent_id):
        now = time.time()
        rec = self.registry.get(agent_id)
        if rec is not None and rec.revoked:
            return "AUTH_REVOKED"
        hb = self.hearts.get(agent_id)
        seen = hb[1] if hb else (rec.last_seen if rec else 0)
        if self.inflight.get(agent_id, 0) > 0 and agent_id in self.connections:
            return "BUSY"
        if agent_id in self.connections and (now - seen) <= self.heartbeat_stale_s:
            return "ONLINE"
        if seen and (now - seen) <= self.heartbeat_offline_s:
            return "STALE"
        return "OFFLINE"

    def snapshot(self):
        agents = []
        for rec in self.store.list_agents():
            hb = self.hearts.get(rec.agent_id)
            info = hb[0] if hb else {}
            agents.append({
                "agent_id": rec.agent_id, "status": self.status(rec.agent_id),
                "agent_version": rec.agent_version, "sdk": rec.sdk,
                "capabilities": rec.capabilities, "revoked": rec.revoked,
                "possible_reenrollment": rec.possible_reenrollment,
                "last_seen": max(rec.last_seen, hb[1] if hb else 0),
                "foreground_package": info.get("foreground_package", ""),
                "lan_ip": info.get("lan_ip", ""),
                "last_command_status": info.get("last_command_status", ""),
            })
        agents.sort(key=lambda a: a["agent_id"])
        return {"agents": agents, "connections": len(self.connections),
                "pending": len(self.pending), "metrics": dict(self.metrics)}

    async def shutdown(self):
        self._closed = True
        for aid, sess in list(self.connections.items()):
            self._fail_pending_for(aid, "SHUTDOWN")
            try:
                sess.close()
            except Exception:
                pass
        self.connections.clear()
        for cid, fut in list(self.pending.items()):
            if not fut.done():
                fut.set_result({"command_id": cid, "status": "uncertain", "error": "SHUTDOWN"})
        self.pending.clear()
        self._pending_agent.clear()
        self.store.close()

    async def _ws_handler(self, ws):
        import ssl  # noqa: F401

        class _WSS:
            def __init__(self, sock):
                self.ws = sock
                self.agent_id = None

            async def send(self, text):
                await self.ws.send(text)

            def close(self):
                try:
                    asyncio.get_running_loop().create_task(self.ws.close())
                except Exception:
                    pass

        sess = _WSS(ws)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                await self.handle_message(sess, msg)
        except Exception:
            pass
        finally:
            self.close_session(sess, "disconnect")

    async def start_wss(self, host, port, cert_path, key_path):
        """Sobe o servidor WSS real (Python 3.14 + websockets). Retorna o server object."""
        import ssl
        from websockets.asyncio.server import serve
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)
        self._wss_server = await serve(self._ws_handler, host, port, ssl=ctx,
                                       ping_interval=20, ping_timeout=20,
                                       max_size=4 * 1024 * 1024)
        return self._wss_server

    async def stop_wss(self):
        srv = self._wss_server
        self._wss_server = None
        if srv is not None:
            srv.close()
            try:
                await srv.wait_closed()
            except Exception:
                pass

    async def serve_wss(self, host, port, cert_path, key_path):
        srv = await self.start_wss(host, port, cert_path, key_path)
        try:
            await asyncio.Future()
        finally:
            self._wss_server = None
            try:
                srv.close()
                await srv.wait_closed()
            except Exception:
                pass
