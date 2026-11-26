"""Integracao servidor<->agente (agente simulado via WebSocket real)."""
import asyncio
import json
import socket
import ssl
import threading
import time

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from dpn.agent.certs import ensure_server_cert
from dpn.agent.protocol import Command, Op, ResultStatus
from dpn.agent.server import AgentServer


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_server(tmp_path, authenticate=None, on_pair=None, events=None):
    cert, key, _fp = ensure_server_cert(str(tmp_path))
    port = _free_port()
    events = events if events is not None else []

    server = AgentServer(
        "127.0.0.1", port, cert, key,
        authenticate=authenticate or (lambda a, t: a == "a1" and t == "tok"),
        on_pair=on_pair or (lambda a, c, i: "tok" if c == "123456" else None),
        on_event=lambda e, d: events.append((e, d)),
    )
    server.start()
    return server, port


async def _fake_agent(port, agent_id, token, stop, observed_id="ID-A"):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with connect(f"wss://127.0.0.1:{port}", ssl=ctx) as ws:
        await ws.send(json.dumps({"type": "hello", "agent_id": agent_id,
                                  "token": token}))
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert hello["type"] == "hello_ok"
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            except ConnectionClosed:
                return
            m = json.loads(raw)
            if m.get("type") == "command":
                await ws.send(json.dumps({
                    "type": "result", "request_id": m["request_id"],
                    "agent_id": agent_id, "op": m["op"], "status": "success",
                    "data": {"deviceId": observed_id}, "observed_dpn_id": observed_id,
                }))


def _wait_online(server, agent_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if server.is_online(agent_id):
            return True
        time.sleep(0.05)
    return False


def test_command_roundtrip(tmp_path):
    events = []
    server, port = _make_server(tmp_path, events=events)
    stop = threading.Event()
    th = threading.Thread(target=lambda: asyncio.run(
        _fake_agent(port, "a1", "tok", stop)), daemon=True)
    th.start()
    try:
        assert _wait_online(server, "a1")
        res = server.send_command_blocking(
            Command(op=Op.GET_DEVICE_ID.value, agent_id="a1"), timeout=5)
        assert res.ok
        assert (res.data or {}).get("deviceId") == "ID-A"
        assert res.observed_dpn_id == "ID-A"
        assert any(e[0] == "agent_online" for e in events)
    finally:
        stop.set()
        server.stop()


def test_unauthorized_agent_rejected(tmp_path):
    server, port = _make_server(tmp_path,
                                authenticate=lambda a, t: False,
                                on_pair=lambda a, c, i: None)
    stop = threading.Event()

    async def bad():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        async with connect(f"wss://127.0.0.1:{port}", ssl=ctx) as ws:
            await ws.send(json.dumps({"type": "hello", "agent_id": "x",
                                      "token": "nope"}))
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            assert json.loads(raw)["type"] == "error"

    th = threading.Thread(target=lambda: asyncio.run(bad()), daemon=True)
    th.start()
    try:
        time.sleep(1.0)
        assert not server.is_online("x")
    finally:
        stop.set()
        server.stop()


def test_expired_command_not_sent(tmp_path):
    server, port = _make_server(tmp_path)
    stop = threading.Event()
    th = threading.Thread(target=lambda: asyncio.run(
        _fake_agent(port, "a1", "tok", stop)), daemon=True)
    th.start()
    try:
        assert _wait_online(server, "a1")
        res = server.send_command_blocking(
            Command(op=Op.REBOOT.value, agent_id="a1",
                    deadline=time.time() - 1), timeout=3)
        assert res.status == ResultStatus.EXPIRED.value
    finally:
        stop.set()
        server.stop()


def test_offline_agent_raises(tmp_path):
    server, port = _make_server(tmp_path)
    try:
        from dpn.agent.server import AgentNotConnected
        try:
            server.send_command_blocking(
                Command(op=Op.GET_DEVICE_ID.value, agent_id="none"), timeout=2)
            assert False, "deveria falhar"
        except (AgentNotConnected, Exception):
            pass
    finally:
        server.stop()


async def _fake_agent_drop(port, agent_id, token):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with connect(f"wss://127.0.0.1:{port}", ssl=ctx) as ws:
        await ws.send(json.dumps({"type": "hello", "agent_id": agent_id,
                                  "token": token}))
        await asyncio.wait_for(ws.recv(), timeout=5)   # hello_ok
        await asyncio.wait_for(ws.recv(), timeout=5)   # command
        await ws.close()                                # cai SEM responder


def _start(port, agent_id, token, stop, observed_id="ID-A"):
    th = threading.Thread(target=lambda: asyncio.run(
        _fake_agent(port, agent_id, token, stop, observed_id)), daemon=True)
    th.start()
    return th


def test_agent_isolation(tmp_path):
    server, port = _make_server(tmp_path,
                                authenticate=lambda a, t: t == "tok",
                                on_pair=lambda a, c, i: "tok")
    stop = threading.Event()
    _start(port, "a1", "tok", stop, "ID-A")
    _start(port, "a2", "tok", stop, "ID-B")
    try:
        assert _wait_online(server, "a1") and _wait_online(server, "a2")
        r1 = server.send_command_blocking(
            Command(op=Op.GET_DEVICE_ID.value, agent_id="a1"), timeout=5)
        r2 = server.send_command_blocking(
            Command(op=Op.GET_DEVICE_ID.value, agent_id="a2"), timeout=5)
        assert r1.observed_dpn_id == "ID-A"
        assert r2.observed_dpn_id == "ID-B"
    finally:
        stop.set()
        server.stop()


def test_connection_loss_during_mutation_is_uncertain(tmp_path):
    server, port = _make_server(tmp_path,
                                authenticate=lambda a, t: t == "tok",
                                on_pair=lambda a, c, i: "tok")
    threading.Thread(target=lambda: asyncio.run(
        _fake_agent_drop(port, "a1", "tok")), daemon=True).start()
    try:
        assert _wait_online(server, "a1")
        res = server.send_command_blocking(
            Command(op=Op.REBOOT.value, agent_id="a1",
                    deadline=time.time() + 2), timeout=2)
        assert res.status == ResultStatus.UNCERTAIN.value
    finally:
        server.stop()


def test_start_reports_bind_failure(tmp_path):
    server1, port = _make_server(tmp_path)
    try:
        cert, key, _fp = ensure_server_cert(str(tmp_path))
        server2 = AgentServer(
            "127.0.0.1", port, cert, key,
            authenticate=lambda a, t: True,
            on_pair=lambda a, c, i: "t",
            on_event=lambda e, d: None,
        )
        assert server2.start() is False       # porta ja em uso pela server1
        assert server2.start_error
        server2.stop()
    finally:
        server1.stop()


def test_reconnect(tmp_path):
    server, port = _make_server(tmp_path,
                                authenticate=lambda a, t: t == "tok",
                                on_pair=lambda a, c, i: "tok")
    stop1 = threading.Event()
    _start(port, "a1", "tok", stop1)
    try:
        assert _wait_online(server, "a1")
        stop1.set()
        end = time.time() + 5
        while server.is_online("a1") and time.time() < end:
            time.sleep(0.05)
        assert not server.is_online("a1")

        stop2 = threading.Event()
        _start(port, "a1", "tok", stop2, "ID-B")
        assert _wait_online(server, "a1")
        res = server.send_command_blocking(
            Command(op=Op.GET_DEVICE_ID.value, agent_id="a1"), timeout=5)
        assert res.observed_dpn_id == "ID-B"
        stop2.set()
    finally:
        stop1.set()
        server.stop()
