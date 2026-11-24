#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DPN Agent Controller (read-only pool checkpoint).

Runs the unified Device Agent WSS controller (sln-device/1), indexes Agents by
physical DPN identity and exposes a small file command bus for the SLN UI.

- Any Agent with the same physical DPN can carry a read; no DPN host is required.
- AtomOS credentials stay on Android (Keystore); the controller never receives them.
- No DPN mutation is implemented in this checkpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import config_sln_v2 as CONFIG
from dpn import state as S
from dpn.agent_pool import AgentPoolClient
from dpn.transport_pool import PresenceRegistry, TransportUncertain
from dpn.locks import InterProcessFileLock
from device_agent import protocol as P
from device_agent.models import AgentRecord
from device_agent.pairing import PairingManager
from device_agent.registry import AgentRegistry
from device_agent.server import DeviceAgentServer
from device_agent.state import DeviceAgentStore

HOST_LOCK = "agent_controller.lock"


def _resolve(base, path):
    return path if os.path.isabs(path) else os.path.join(base, path)


def _online_serials():
    try:
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True,
                             timeout=8).stdout
    except Exception:  # noqa: BLE001
        return []
    serials = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _ensure_reverse_all(port):
    from dpn.agent_core.provision import ensure_adb_reverse
    for serial in _online_serials():
        try:
            ensure_adb_reverse(serial, port)
        except Exception:
            pass


class ControllerRuntime:
    def __init__(self, cfg, base_dir, state_dir, store_dir, host="0.0.0.0",
                 port=None, cert_path=None, key_path=None, log_fn=print):
        self.cfg = cfg
        self.base_dir = base_dir
        self.state_dir = state_dir
        self.store_dir = store_dir
        self.host = host
        self.port = int(port or cfg.get("dpn_agent_controller_port", 8766) or 8766)
        self.cert_path = cert_path or _resolve(base_dir, cfg.get(
            "dpn_agent_controller_cert", "state/device_agents/tls/device-controller-cert.pem"))
        self.key_path = key_path or _resolve(base_dir, cfg.get(
            "dpn_agent_controller_key", "state/device_agents/tls/device-controller-key.pem"))
        self.log = log_fn
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.start_error = ""
        self._thread = None
        self._loop = None
        self._store = None
        self._server = None
        self._presence = None
        self._client = None
        self._pairing = None
        self._reverse_thread = None
        self._coord_stop = threading.Event()
        self._coord_thread = None
        self._status_path = os.path.join(state_dir, "agents_status.json")
        self._commands = os.path.join(state_dir, "agent_commands")
        self._done = os.path.join(state_dir, "agent_commands_done")

    # ---------------------------------------------------------------- lifecycle
    def start(self, wait_s=12.0):
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.store_dir, exist_ok=True)
        if not (os.path.isfile(self.cert_path) and os.path.isfile(self.key_path)):
            self.start_error = "cert/key ausente"
            self.log("[AGENT][ERROR] %s (%s / %s)" % (
                self.start_error, self.cert_path, self.key_path))
            return False
        self._thread = threading.Thread(target=self._thread_main, daemon=True,
                                        name="agent-controller")
        self._thread.start()
        self.ready.wait(wait_s)
        if not self.ready.is_set() or self.start_error:
            return False
        self._reverse_thread = threading.Thread(target=self._reverse_loop, daemon=True,
                                                name="agent-reverse")
        self._reverse_thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        t = self._thread
        if t is not None:
            t.join(timeout=8.0)

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception as e:  # noqa: BLE001
            self.start_error = "%s: %s" % (type(e).__name__, e)
            self.log("[AGENT][ERROR] %s" % self.start_error)
        finally:
            self.ready.set()
            try:
                loop.close()
            except Exception:
                pass

    def _reverse_loop(self):
        interval = float(self.cfg.get("dpn_agent_reverse_interval_s", 30) or 30)
        while not self.stop_event.wait(interval):
            _ensure_reverse_all(self.port)

    async def _main(self):
        self._store = DeviceAgentStore(self.store_dir)
        self._presence = PresenceRegistry(self.state_dir)
        self._pairing = PairingManager(self._store)
        self._server = DeviceAgentServer(
            self._store, registry=AgentRegistry(self._store), pairing=self._pairing,
            presence_registry=self._presence,
            heartbeat_stale_s=float(self.cfg.get("dpn_presence_stale_s", 60) or 60))
        self._client = AgentPoolClient(self._server, self._presence, cfg=self.cfg,
                                       log_fn=self.log)
        self._client.loop = asyncio.get_running_loop()
        try:
            await self._server.start_wss(self.host, self.port, self.cert_path, self.key_path)
        except Exception as e:  # noqa: BLE001
            self.start_error = "wss: %s" % e
            self.log("[AGENT][ERROR] %s" % self.start_error)
            self.ready.set()
            self._store.close()
            return
        _ensure_reverse_all(self.port)
        self.log("[AGENT][CTRL] listening wss://%s:%d" % (self.host, self.port))
        self._start_embedded_coordinator()
        self.ready.set()
        try:
            while not self.stop_event.is_set():
                try:
                    await self._process_commands()
                except Exception as e:  # noqa: BLE001
                    self.log("[AGENT][ERROR] commands %s: %s" % (type(e).__name__, e))
                try:
                    self._publish_status()
                except Exception as e:  # noqa: BLE001
                    self.log("[AGENT][ERROR] status %s: %s" % (type(e).__name__, e))
                await asyncio.sleep(1.0)
        finally:
            self._coord_stop.set()
            if self._coord_thread is not None:
                self._coord_thread.join(timeout=4.0)
                self._coord_thread = None
            try:
                await self._server.stop_wss()
            except Exception:
                pass
            try:
                await self._server.shutdown()
            except Exception:
                pass
            self._server = None
            self._store = None

    def _auto_groups(self, fallback):
        if not self.cfg.get("dpn_auto_register_enabled", False):
            return fallback
        try:
            from dpn.auto_register import build_groups, load_catalog, write_groups
            catalog_path = _resolve(self.base_dir, self.cfg.get(
                "dpn_auto_catalog_file", "dpn_groups.json"))
            out_path = _resolve(self.base_dir, self.cfg.get(
                "dpn_auto_groups_file", "dpn_groups_auto.json"))
            catalog = load_catalog(catalog_path)
            expected_ids = set()
            for spec in catalog.values():
                if isinstance(spec, dict):
                    c = spec.get("controller") or {}
                    pid = c.get("expected_dpn_id") or spec.get("expected_dpn_id")
                    if pid:
                        expected_ids.add(str(pid))
            deadline = time.time() + 60.0
            last_sig = None
            stable = 0
            groups, meta = {}, {}
            while not self._coord_stop.is_set() and time.time() < deadline:
                groups, meta = build_groups(self._client.status(), catalog)
                sig = tuple(sorted((gid, tuple(g.get("members") or []))
                                   for gid, g in groups.items()))
                # Wait for all currently discoverable known groups and for a
                # membership snapshot that is unchanged for a few heartbeats.
                have = sum(1 for g in groups.values() if g.get("members"))
                enough = (not expected_ids) or have >= min(3, len(expected_ids))
                if enough and meta.get("members") and sig == last_sig:
                    stable += 1
                else:
                    stable = 0
                last_sig = sig
                if stable >= 5:
                    break
                time.sleep(1.0)
            if groups and meta.get("members"):
                write_groups(out_path, groups)
            self.log("[DPN][AUTO] groups=%d known=%d unknown=%d members=%d file=%s" % (
                len(groups), meta.get("known", 0), meta.get("unknown", 0),
                meta.get("members", 0), out_path))
            return groups or fallback
        except Exception as e:  # noqa: BLE001
            self.log("[DPN][AUTO][ERROR] %s: %s" % (type(e).__name__, e))
            return fallback

    def _start_embedded_coordinator(self):
        if not self.cfg.get("dpn_embed_agent_pool_coordinator", False):
            return
        try:
            gp = self.cfg.get("dpn_groups_file", "dpn_groups.json")
            gp = _resolve(self.base_dir, gp)
            groups = S.read_json(gp, {}) or {}
            has_pool = any(
                str((g.get("controller") or {}).get("transport") or "").lower() == "agent_pool"
                for g in groups.values() if isinstance(g, dict))
            if not has_pool and not self.cfg.get("dpn_auto_register_enabled", False):
                self.log("[DPN][COORD] skip: nenhum grupo transport=agent_pool")
                return
        except Exception as e:
            self.log("[DPN][COORD] skip: %s" % type(e).__name__)
            return
        def _ready():
            try:
                snap = self._server.snapshot() if self._server is not None else {}
                live = {a.get("agent_id") for a in (snap.get("agents") or [])
                        if str(a.get("status") or "").upper() == "ONLINE"}
                expected = []
                for g in groups.values():
                    c = (g.get("controller") or {}) if isinstance(g, dict) else {}
                    pid = c.get("expected_dpn_id")
                    if pid:
                        expected.append(str(pid))
                members = set()
                for g in groups.values():
                    if isinstance(g, dict):
                        members.update(str(m) for m in (g.get("members") or []) if m)
                if members:
                    live_ips = set()
                    for a in (snap.get("agents") or []):
                        if str(a.get("status") or "").upper() == "ONLINE":
                            ip = str(a.get("lan_ip") or "").split(":", 1)[0]
                            if ip:
                                live_ips.add(ip)
                    needed = {m.split(":", 1)[0] for m in members}
                    if not needed.issubset(live_ips):
                        return False
                for pid in expected:
                    if any(c.agent_id in live for c in self._presence.by_dpn(pid)):
                        return True
                if not expected:
                    return any(c.agent_id in live for c in self._presence.all())
                return False
            except Exception:
                return False

        def loop():
            try:
                deadline = time.time() + 90.0
                while not self._coord_stop.is_set() and not _ready() and time.time() < deadline:
                    time.sleep(1.0)
                if not _ready():
                    self.log("[DPN][COORD] skip: nenhum Agent pronto para o grupo")
                    return
                auto_groups = self._auto_groups(groups)
                if not auto_groups:
                    self.log("[DPN][COORD] skip: nenhum grupo descoberto")
                    return
                from dpn_coordinator_run import Runner
                runner = Runner(self.cfg, auto_groups, self.base_dir, self.state_dir,
                                tick_s=float(self.cfg.get("dpn_coordinator_tick_s", 1) or 1),
                                log_fn=self.log, agent_pool=self._client)
                self.log("[DPN][COORD] embedded agent_pool iniciado")
                while not self._coord_stop.is_set():
                    try:
                        runner.step_all()
                        runner.process_commands()
                        runner.publish_status()
                    except Exception as e:
                        self.log("[DPN][COORD][ERROR] %s: %s" % (type(e).__name__, e))
                    self._coord_stop.wait(1.0)
                runner.close()
            except Exception as e:
                self.log("[DPN][COORD][ERROR] start: %s: %s" % (type(e).__name__, e))
        self._coord_thread = threading.Thread(target=loop, daemon=True,
                                              name="dpn-embedded-coordinator")
        self._coord_thread.start()

    # ---------------------------------------------------------------- status
    def _publish_status(self):
        if self._client is None:
            return
        data = self._client.status()
        data["controller_pid"] = os.getpid()
        data["host"] = self.host
        data["port"] = self.port
        S.atomic_write_json(self._status_path, data)

    # ---------------------------------------------------------------- commands
    async def _process_commands(self):
        os.makedirs(self._done, exist_ok=True)
        try:
            names = sorted(os.listdir(self._commands))
        except Exception:
            return
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(self._commands, name)
            cmd = S.read_json(path, None)
            if not isinstance(cmd, dict):
                self._finish(path, {"status": "error", "error": "invalid_command"})
                continue
            cid = str(cmd.get("command_id") or name[:-5])
            done_path = os.path.join(self._done, cid + ".json")
            if os.path.exists(done_path):
                try:
                    os.unlink(path)
                except Exception:
                    pass
                continue
            result = {"command_id": cid, "action": cmd.get("action"),
                      "status": "ok", "at": time.time()}
            try:
                result.update(await self._apply_command(cmd))
            except Exception as e:  # noqa: BLE001
                result["status"] = "error"
                result["error"] = "%s: %s" % (type(e).__name__, e)
            S.atomic_write_json(done_path, result)
            try:
                os.unlink(path)
            except Exception:
                pass
            self.log("[AGENT][CMD] action=%s status=%s" % (cmd.get("action"), result["status"]))

    def _finish(self, path, result):
        try:
            S.atomic_write_json(path + ".done", result)
        except Exception:
            pass
        try:
            os.unlink(path)
        except Exception:
            pass

    async def _apply_command(self, cmd):
        action = str(cmd.get("action") or "")
        if action == "refresh_identities":
            agent_ids = cmd.get("agent_ids") or None
            rows = await self._client.refresh_identities(
                agent_ids=agent_ids, max_agents=cmd.get("max_agents"))
            return {"results": rows}
        if action == "read":
            physical = str(cmd.get("physical_dpn_id") or "")
            op = str(cmd.get("op") or "")
            if not physical or op not in P.DPN_READ_OPS:
                raise ValueError("read exige physical_dpn_id e op DPN read-only")
            data, chosen = await self._client.read(physical, op)
            return {"physical_dpn_id": physical, "op": op, "agent_id": chosen.agent_id,
                    "data": data}
        if action in ("switch_node", "refresh_route_set"):
            physical = str(cmd.get("physical_dpn_id") or "")
            tunnel = str(cmd.get("tunnel_code") or "")
            op = P.DPN_SWITCH_NODE if action == "switch_node" else P.DPN_REFRESH_ROUTE_SET
            params = {"expected_dpn_id": physical, "tunnel_code": tunnel}
            if action == "switch_node":
                params["current_ip"] = str(cmd.get("current_ip") or "")
            data, chosen = await self._client.execute_mutation(
                physical, op, params, op, command_id=cmd.get("command_id"))
            return {"physical_dpn_id": physical, "op": op,
                    "agent_id": chosen.agent_id, "data": data}
        if action == "reconcile_switch":
            return await self._client.reconcile_switch(
                str(cmd.get("physical_dpn_id") or ""),
                str(cmd.get("tunnel_code") or ""),
                str(cmd.get("old_ip") or ""))
        if action == "open_pairing":
            code = self._pairing.open(intent=str(cmd.get("intent") or "agent"),
                                      ttl_s=float(cmd.get("ttl_s") or 300))
            return {"pairing_code": code, "ttl_s": float(cmd.get("ttl_s") or 300)}
        raise ValueError("acao invalida: %r" % action)


class Runner:
    def __init__(self, cfg, base_dir, state_dir=None, store_dir=None, **kw):
        self.cfg = cfg
        self.base_dir = base_dir
        self.state_dir = _resolve(base_dir, state_dir or cfg.get(
            "dpn_state_dir", os.path.join("state", "dpn")))
        self.store_dir = _resolve(base_dir, store_dir or cfg.get(
            "device_agent_state_dir", os.path.join("state", "device_agents")))
        self.runtime = ControllerRuntime(cfg, base_dir, self.state_dir, self.store_dir,
                                         **kw)

    def start(self):
        return self.runtime.start()

    def stop(self):
        self.runtime.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(description="DPN unified Agent controller (read-only pool).")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--store-dir", default=None)
    ap.add_argument("--duration", type=float, default=0)
    args = ap.parse_args(argv)

    cfg = CONFIG.load_config()
    state_dir = args.state_dir or cfg.get("dpn_state_dir", os.path.join("state", "dpn"))
    state_dir = _resolve(BASE, state_dir)
    os.makedirs(state_dir, exist_ok=True)
    lock = InterProcessFileLock(os.path.join(state_dir, HOST_LOCK), timeout=1.0)
    if not lock.acquire():
        print("[AGENT][RUNNER_ALREADY_ACTIVE]")
        return 3
    runtime = ControllerRuntime(
        cfg, BASE, state_dir,
        _resolve(BASE, cfg.get("device_agent_state_dir", os.path.join("state", "device_agents"))),
        host=args.host or cfg.get("dpn_agent_controller_host", "0.0.0.0"),
        port=args.port or cfg.get("dpn_agent_controller_port", 8766))
    try:
        if not runtime.start():
            print("[AGENT][ERROR] %s" % (runtime.start_error or "start failed"))
            return 1
        print("[AGENT][RUNNER] pid=%d port=%d" % (os.getpid(), runtime.port))
        if args.duration and args.duration > 0:
            time.sleep(args.duration)
        else:
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
