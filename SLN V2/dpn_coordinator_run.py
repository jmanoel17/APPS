#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn_coordinator_run.py — SLN V2: RUNNER standalone do DPN (autoridade única por host).

- NÃO inicia worker Android, NÃO controla main_sln_v2, NÃO depende da interface.
- Sai imediatamente se `dpn_routing_enabled=false`.
- Singleton por host: `state/dpn/coordinator_host.lock` (InterProcessFileLock).
- Um DPNCoordinator por DPN de `dpn_groups.json`; provider por controller.type (factory).
- Loop leve (thread por DPN + tick configurável, `dpn_coordinator_tick_s`); erro de uma DPN
  NÃO derruba as demais.
- Reconcilia estado persistido no startup.
- Publica `state/dpn/status.json` (snapshot atômico) para a interface.
- Command bus: `state/dpn/commands/*.json` -> executa -> grava resultado em `commands_done/`
  (idempotente por command_id).
- Shutdown limpo (providers fechados; lock de host liberado).

Uso:
  python dpn_coordinator_run.py [--tick 1.0] [--duration 0] [--once] [--state-dir DIR] [--groups FILE]
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import config_sln_v2 as CONFIG
from dpn import state as S
from dpn.coordinator import DPNCoordinator
from dpn.barrier import SignalBarrier
from dpn.provider_factory import build_provider, UnknownProviderError
from dpn.locks import InterProcessFileLock

HOST_LOCK = "coordinator_host.lock"


def host_id(cfg):
    return cfg.get("host_id") or os.environ.get("SLN_HOST_ID") or socket.gethostname()


def _resolve(base, p):
    return p if os.path.isabs(p) else os.path.join(base, p)


def acquire_host_lock(state_dir, timeout=1.0):
    """Retorna o lock adquirido ou None (já existe runner ativo no host)."""
    lock = InterProcessFileLock(os.path.join(state_dir, HOST_LOCK), timeout=timeout)
    return lock if lock.acquire() else None


class Runner:
    """Autoridade DPN por host. Importável para testes (step/publish/process sem threads)."""

    def __init__(self, cfg, groups, base_dir, state_dir, tick_s=1.0, log_fn=print,
                 now_fn=time.time, alive_fn=None):
        self.cfg = cfg
        self.groups = groups if isinstance(groups, dict) else {}
        self.base_dir = base_dir
        self.state_dir = state_dir
        self.tick_s = float(tick_s)
        self.log = log_fn
        self.now_fn = now_fn
        self.alive_fn = alive_fn
        self.dpns = {}
        self._errs = {}
        self._prev = {}
        self._last_trans = {}
        self._lock = threading.Lock()
        os.makedirs(self.state_dir, exist_ok=True)
        self._build()

    def _build(self):
        limit = int(self.cfg.get("dpn_work_units_per_ip", 20) or 20)
        for dpn_id, grp in self.groups.items():
            if str(dpn_id).startswith("_"):
                continue
            if not isinstance(grp, dict):
                continue
            devices = list(grp.get("devices") or [])
            controller = grp.get("controller") or {}
            ctype = controller.get("type")
            entry = {"dpn_id": dpn_id, "devices": devices, "error": None}
            try:
                provider = build_provider(ctype, controller, dpn_id=dpn_id)
            except UnknownProviderError:
                entry["error"] = "unknown_provider:%s" % ctype
                self.dpns[dpn_id] = entry
                self._errs[dpn_id] = entry["error"]
                self.log("[DPN][ERROR] dpn=%s unknown_provider=%s" % (dpn_id, ctype))
                continue
            except Exception as e:
                entry["error"] = "provider_build:%s" % type(e).__name__
                self.dpns[dpn_id] = entry
                self._errs[dpn_id] = entry["error"]
                continue
            barrier = SignalBarrier(self.state_dir, dpn_id, devices,
                                    alive_fn=self.alive_fn, now_fn=self.now_fn)
            coord = DPNCoordinator(
                self.state_dir, dpn_id, provider, limit=limit,
                cooldown_s=float(self.cfg.get("dpn_ip_reuse_cooldown_s", 3600)),
                dup_before_reset=int(self.cfg.get("dpn_duplicate_rotations_before_node_reset", 5)),
                settle_s=float(self.cfg.get("dpn_network_settle_s", 5)),
                rotation_poll_s=float(self.cfg.get("dpn_rotation_poll_s", 2)),
                rotation_timeout_s=float(self.cfg.get("dpn_rotation_timeout_s", 60)),
                drain_timeout_s=float(self.cfg.get("dpn_drain_timeout_s", 120)),
                barrier=barrier, now_fn=self.now_fn, log_fn=self.log)
            entry.update({"coord": coord, "provider": provider, "barrier": barrier})
            # reconcile no startup (crash/restart)
            try:
                coord.reconcile()
            except Exception as e:
                self._errs[dpn_id] = "reconcile:%s" % type(e).__name__
            self.dpns[dpn_id] = entry

    # ---- ticks ----
    def _track(self, dpn_id):
        cur = S.read_dpn_state(self.state_dir, dpn_id).get("state")
        if self._prev.get(dpn_id) != cur:
            self._prev[dpn_id] = cur
            self._last_trans[dpn_id] = self.now_fn()

    def tick_one(self, dpn_id):
        e = self.dpns.get(dpn_id)
        if not e or "coord" not in e:
            return
        try:
            e["coord"].tick()
            self._track(dpn_id)
            with self._lock:
                self._errs.pop(dpn_id, None)
        except Exception as ex:
            with self._lock:
                self._errs[dpn_id] = "tick:%s" % type(ex).__name__
            self.log("[DPN][ERROR] dpn=%s tick %s: %s" % (dpn_id, type(ex).__name__, ex))

    def step_all(self):
        for d in list(self.dpns.keys()):
            self.tick_one(d)

    # ---- status snapshot ----
    def _cooldown_remaining(self, ip):
        if not ip:
            return 0
        try:
            status, info = S.ip_available(
                self.state_dir, ip, float(self.cfg.get("dpn_ip_reuse_cooldown_s", 3600)),
                now=self.now_fn())
            return round(float(info.get("remaining_s", 0)), 0) if status == "COOLDOWN" else 0
        except Exception:
            return 0

    def _safe_active(self, e):
        devs = e.get("devices") or []
        barrier = e.get("barrier")
        snap = barrier.snapshot() if barrier else {}
        safe = sum(1 for v in snap.values() if v)
        try:
            active = sum(1 for d in devs if (self.alive_fn(d) if self.alive_fn else True))
        except Exception:
            active = len(devs)
        return safe, active

    def snapshot(self):
        with self._lock:
            errs = dict(self._errs)
        limit = int(self.cfg.get("dpn_work_units_per_ip", 20) or 20)
        dpns = {}
        for dpn_id, e in self.dpns.items():
            if "coord" not in e:
                dpns[dpn_id] = {
                    "state": "ERROR", "node_id": None, "current_ip": None, "work_units": 0,
                    "limit": limit, "duplicate_rotations": 0, "safe_devices": 0,
                    "active_devices": len(e.get("devices") or []), "last_transition_at": None,
                    "last_error": e.get("error") or errs.get(dpn_id), "cooldown_remaining_s": 0,
                }
                continue
            st = S.read_dpn_state(self.state_dir, dpn_id)
            safe, active = self._safe_active(e)
            dpns[dpn_id] = {
                "state": st.get("state"),
                "node_id": (st.get("node") or {}).get("node_id"),
                "current_ip": st.get("current_ip"),
                "work_units": int(st.get("current_ip_work_units", 0)),
                "limit": limit,
                "duplicate_rotations": int(st.get("duplicate_rotations", 0)),
                "safe_devices": safe, "active_devices": active,
                "last_transition_at": self._last_trans.get(dpn_id),
                "last_error": errs.get(dpn_id) or st.get("last_error"),
                "cooldown_remaining_s": self._cooldown_remaining(st.get("current_ip")),
            }
        return {"updated_at": self.now_fn(), "runner_pid": os.getpid(),
                "host_id": host_id(self.cfg), "dpns": dpns}

    def publish_status(self):
        S.atomic_write_json(os.path.join(self.state_dir, "status.json"), self.snapshot())

    # ---- command bus ----
    def _commands_dir(self):
        return os.path.join(self.state_dir, "commands")

    def _done_dir(self):
        return os.path.join(self.state_dir, "commands_done")

    def _apply_command(self, cmd):
        dpn_id = cmd.get("dpn_id")
        action = cmd.get("action")
        e = self.dpns.get(dpn_id)
        if not e or "coord" not in e:
            raise ValueError("unknown dpn %r" % (dpn_id,))
        c = e["coord"]
        if action == "pause":
            c.pause()
        elif action == "resume":
            c.resume()
        elif action == "rotate_now":
            c.rotate_now()
        elif action == "reset_node_now":
            c.reset_node_now()
        else:
            raise ValueError("invalid action %r" % (action,))

    def process_commands(self):
        cdir = self._commands_dir()
        ddir = self._done_dir()
        os.makedirs(ddir, exist_ok=True)
        try:
            names = sorted(os.listdir(cdir))
        except Exception:
            return []
        applied = []
        for n in names:
            if not n.endswith(".json"):
                continue
            p = os.path.join(cdir, n)
            cmd = S.read_json(p, None)
            if not isinstance(cmd, dict):
                try:
                    os.replace(p, os.path.join(ddir, n))
                except Exception:
                    pass
                continue
            cid = cmd.get("command_id") or n[:-5]
            done = os.path.join(ddir, cid + ".json")
            if os.path.exists(done):          # idempotência por command_id
                try:
                    os.unlink(p)
                except Exception:
                    pass
                continue
            res = {"command_id": cid, "dpn_id": cmd.get("dpn_id"), "action": cmd.get("action"),
                   "status": "ok", "at": self.now_fn()}
            try:
                self._apply_command(cmd)
            except Exception as ex:
                res["status"] = "error"
                res["error"] = "%s" % type(ex).__name__
            S.atomic_write_json(done, res)
            try:
                os.unlink(p)
            except Exception:
                pass
            applied.append(res)
        return applied

    # ---- lifecycle ----
    def close(self):
        for e in self.dpns.values():
            p = e.get("provider")
            try:
                if p and hasattr(p, "close"):
                    p.close()
            except Exception:
                pass

    def run(self, duration=0, use_threads=True):
        stop = threading.Event()
        threads = []
        if use_threads:
            for dpn_id, e in self.dpns.items():
                if "coord" in e:
                    t = threading.Thread(target=self._dpn_loop, args=(dpn_id, stop), daemon=True)
                    t.start()
                    threads.append(t)
        t0 = time.time()
        try:
            while not stop.is_set():
                if duration and (time.time() - t0) >= duration:
                    break
                try:
                    self.process_commands()
                except Exception as ex:
                    self.log("[DPN][ERROR] commands %s: %s" % (type(ex).__name__, ex))
                try:
                    self.publish_status()
                except Exception as ex:
                    self.log("[DPN][ERROR] status %s: %s" % (type(ex).__name__, ex))
                stop.wait(self.tick_s)
        finally:
            stop.set()
            for t in threads:
                t.join(timeout=3.0)
            self.close()

    def _dpn_loop(self, dpn_id, stop):
        while not stop.is_set():
            self.tick_one(dpn_id)
            stop.wait(self.tick_s)


def main(argv=None):
    ap = argparse.ArgumentParser(description="SLN V2 — runner standalone do DPN.")
    ap.add_argument("--tick", type=float, default=None)
    ap.add_argument("--duration", type=float, default=0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--groups", default=None)
    args = ap.parse_args(argv)

    cfg = CONFIG.load_config()
    if not cfg.get("dpn_routing_enabled", False):
        print("[DPN][RUNNER] dpn_routing_enabled=false — nada a fazer (saindo).")
        return 0

    state_dir = _resolve(BASE, args.state_dir or cfg.get("dpn_state_dir", os.path.join("state", "dpn")))
    os.makedirs(state_dir, exist_ok=True)

    lock = acquire_host_lock(state_dir)
    if lock is None:
        print("[DPN][RUNNER_ALREADY_ACTIVE]")
        return 3
    try:
        gp = _resolve(BASE, args.groups or cfg.get("dpn_groups_file", "dpn_groups.json"))
        groups = S.read_json(gp, {}) or {}
        tick = args.tick if args.tick is not None else float(cfg.get("dpn_coordinator_tick_s", 1.0) or 1.0)
        runner = Runner(cfg, groups, BASE, state_dir, tick_s=tick)
        if args.once:
            runner.step_all()
            runner.process_commands()
            runner.publish_status()
            runner.close()
            return 0
        runner.run(duration=args.duration)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
