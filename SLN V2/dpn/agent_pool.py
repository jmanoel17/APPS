#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Controller-side unified Device Agent pool for DPN mode.

The controller never owns a DPN host. It indexes every connected Agent by the
physical DPN identity reported by that Agent, then lets any Agent in the same
physical DPN carry a typed read. Credentials stay in the Android Keystore.
"""
from __future__ import annotations

import time

from device_agent import protocol as P

from .transport_pool import (DPNTransportPool, PresenceRegistry, ReadFailover,
                             TransportUncertain)
from .mutation_lease import DPNMutationLeaseManager, LeaseBusy
from .auth import AuthBackoff, AUTH_CODES, DPNAuthCoordinator


class AgentPoolClient:
    def __init__(self, server, presence, pool=None, cfg=None, log_fn=None,
                 lease=None, auth=None):
        self.server = server
        self.presence = presence
        self.cfg = cfg or {}
        self.pool = pool or DPNTransportPool(
            presence, stale_s=float(self.cfg.get("dpn_pool_stale_s",
                                                self.cfg.get("dpn_presence_stale_s", 60)) or 60),
            log_fn=log_fn)
        self.log = log_fn or (lambda *a, **k: None)
        self.lease = lease or DPNMutationLeaseManager(
            state_dir=(presence.path and __import__("os").path.dirname(presence.path)),
            ttl_s=float(self.cfg.get("dpn_mutation_lease_s", 30) or 30))
        self.auth = auth or DPNAuthCoordinator(
            backoff_s=float(self.cfg.get("dpn_auth_backoff_s", 300) or 300))
        self.loop = None
        self._mutation_results = {}
        self._mutation_uncertain = set()
        self.metrics = {"mutation_count": 0, "mutation_uncertain": 0,
                        "lease_busy": 0, "auth_backoff": 0,
                        "switch_success": 0, "refresh_requested": 0,
                        "identity_mismatch": 0, "read_failovers": 0}
        self.dpn_metrics = {}

    def _metric(self, physical_dpn_id, key, amount=1):
        d = self.dpn_metrics.setdefault(str(physical_dpn_id), {})
        d[key] = int(d.get(key, 0)) + int(amount)
        self.metrics[key] = int(self.metrics.get(key, 0)) + int(amount)

    def _timeout(self):
        return float(self.cfg.get("dpn_agent_request_timeout_s", 25) or 25)

    def _max_refresh(self):
        return max(1, int(self.cfg.get("dpn_agent_refresh_max", 8) or 8))

    def _capable(self, rec):
        caps = rec.get("capabilities") or {}
        return any(bool(caps.get(op)) for op in P.DPN_READ_OPS)

    async def refresh_identities(self, agent_ids=None, max_agents=None):
        """Read GET_DPN_IDENTITY from online Agents and index the physical DPN.

        This is explicit/on-demand (not a heartbeat fan-out) to avoid AtomOS
        login throttling. Returns a compact per-Agent result list.
        """
        snap = self.server.snapshot()
        wanted = {str(a) for a in (agent_ids or []) if a}
        rows = []
        agents = []
        for rec in snap.get("agents") or []:
            aid = str(rec.get("agent_id") or "")
            if not aid or (wanted and aid not in wanted):
                continue
            if str(rec.get("status") or "").upper() != "ONLINE":
                rows.append({"agent_id": aid, "status": "offline"})
                continue
            if not self._capable(rec):
                rows.append({"agent_id": aid, "status": "no_dpn_credentials"})
                continue
            known = self.presence.get(aid)
            if known is not None and self.auth.is_blocked(known.physical_dpn_id):
                rows.append({"agent_id": aid, "status": "auth_backoff"})
                continue
            agents.append(rec)
        agents.sort(key=lambda r: float(r.get("last_seen") or 0), reverse=True)
        limit = max(1, int(max_agents or self._max_refresh()))
        for rec in agents[:limit]:
            aid = str(rec.get("agent_id"))
            try:
                r = await self.server.send_command(
                    aid, P.GET_DPN_IDENTITY, timeout=self._timeout())
                if r.get("status") != "success":
                    code = self._auth_error(r)
                    known = self.presence.get(aid)
                    if code and known is not None:
                        self.auth.note_failure(known.physical_dpn_id, code)
                    rows.append({"agent_id": aid, "status": r.get("status") or "failure",
                                 "error": r.get("error") or ""})
                    continue
                data = r.get("data") or {}
                physical = str(data.get("physical_dpn_id") or "")
                if not physical:
                    rows.append({"agent_id": aid, "status": "no_physical_id"})
                    continue
                self.presence.upsert(
                    aid, device_id=aid, physical_dpn_id=physical,
                    latency_ms=float(data.get("latency_ms") or 0),
                    authenticated=True,
                    capabilities=set(P.DPN_READ_OPS) | set(P.DPN_MUTATING_OPS),
                    connection_state="ONLINE", last_seen=time.time())
                rows.append({"agent_id": aid, "status": "ok",
                             "physical_dpn_id": physical,
                             "latency_ms": data.get("latency_ms", 0)})
            except Exception as e:  # noqa: BLE001
                self.presence.mark_failure(aid)
                rows.append({"agent_id": aid, "status": "error",
                             "error": type(e).__name__})
        return rows

    async def _send(self, agent_id, op):
        r = await self.server.send_command(agent_id, op, timeout=self._timeout())
        status = str(r.get("status") or "").lower()
        if status == "success":
            return r.get("data")
        code = self._auth_error(r)
        if code:
            raise ReadFailover(code)
        if status == "uncertain":
            raise TransportUncertain(r.get("error") or "uncertain")
        raise ReadFailover(r.get("error") or status or "read_failed")

    async def read(self, physical_dpn_id, op, capability=None):
        if op not in P.DPN_READ_OPS:
            raise ValueError("op DPN read-only invalida: %r" % (op,))
        before = len(self.pool.failovers)
        try:
            return await self.pool.read_async(
                physical_dpn_id, op,
                lambda c: self._send(c.agent_id, op),
                capability=capability)
        finally:
            delta = len(self.pool.failovers) - before
            if delta > 0:
                self._metric(physical_dpn_id, "read_failovers", delta)

    def _auth_error(self, result):
        err = str((result or {}).get("error") or "")
        if err in AUTH_CODES or "AUTH" in err.upper() or "UNAUTHORIZED" in err.upper():
            return err or "AUTH_ERROR"
        return ""

    async def execute_mutation(self, physical_dpn_id, operation, params,
                               required_capability, command_id=None):
        if operation not in P.DPN_MUTATING_OPS:
            raise ValueError("op mutating DPN invalida: %r" % (operation,))
        if not physical_dpn_id:
            raise ValueError("physical_dpn_id obrigatorio")
        command_id = str(command_id or P.new_command_id())
        if command_id in self._mutation_results:
            return self._mutation_results[command_id]
        if command_id in self._mutation_uncertain:
            raise TransportUncertain("MUTATION_ALREADY_UNCERTAIN")
        if self.auth.is_blocked(physical_dpn_id):
            self._metric(physical_dpn_id, "auth_backoff")
            self.log("[DPN_AUTH][BACKOFF] dpn=%s reason=blocked" % physical_dpn_id)
            raise AuthBackoff("AUTH_BACKOFF:%s" % physical_dpn_id)
        candidates = self.pool.get_candidates(physical_dpn_id, required_capability)
        if not candidates:
            raise ReadFailover("NO_MUTATION_TRANSPORT")
        last = None
        for candidate in candidates:
            if self.auth.is_blocked(physical_dpn_id):
                self.metrics["auth_backoff"] += 1
                raise AuthBackoff("AUTH_BACKOFF:%s" % physical_dpn_id)
            try:
                lease = self.lease.acquire(physical_dpn_id, candidate.agent_id,
                                           operation, command_id)
            except LeaseBusy:
                self._metric(physical_dpn_id, "lease_busy")
                self.log("[DPN_LEASE][BUSY] dpn=%s op=%s" % (physical_dpn_id, operation))
                raise
            self.log("[DPN_POOL][SELECT] dpn=%s op=%s agent=%s latency=%s" % (
                physical_dpn_id, operation, candidate.agent_id, candidate.latency_ms))
            self.log("[DPN_LEASE][ACQUIRED] dpn=%s lease=%s agent=%s op=%s gen=%s" % (
                physical_dpn_id, lease["lease_id"], candidate.agent_id, operation,
                lease["generation"]))
            try:
                async with self.auth.lock(physical_dpn_id):
                    r = await self.server.send_command(
                        candidate.agent_id, operation, params,
                        command_id=P.new_command_id(), timeout=self._timeout())
                status = str(r.get("status") or "").lower()
                if status == "success":
                    self.auth.note_success(physical_dpn_id)
                    self.lease.release(lease["lease_id"], "RELEASED")
                    self.log("[DPN_LEASE][RELEASED] dpn=%s lease=%s state=RELEASED" % (
                        physical_dpn_id, lease["lease_id"]))
                    self._metric(physical_dpn_id, "mutation_count")
                    if operation == P.DPN_SWITCH_NODE:
                        self._metric(physical_dpn_id, "switch_success")
                    elif operation == P.DPN_REFRESH_ROUTE_SET:
                        self._metric(physical_dpn_id, "refresh_requested")
                    self.log("[DPN_MUTATION][%s] dpn=%s agent=%s result=SUCCESS" % (
                        operation, physical_dpn_id, candidate.agent_id))
                    out = (r.get("data") or {}, candidate)
                    self._mutation_results[command_id] = out
                    return out
                code = self._auth_error(r)
                if code:
                    self.auth.note_failure(physical_dpn_id, code)
                    self._metric(physical_dpn_id, "auth_backoff")
                    self.log("[DPN_AUTH][BACKOFF] dpn=%s reason=%s" % (
                        physical_dpn_id, code))
                    self.lease.release(lease["lease_id"], "UNCERTAIN")
                    raise AuthBackoff(code)
                if status == "uncertain":
                    self._metric(physical_dpn_id, "mutation_uncertain")
                    self._mutation_uncertain.add(command_id)
                    self.lease.release(lease["lease_id"], "UNCERTAIN")
                    self.log("[DPN_MUTATION][UNCERTAIN] dpn=%s op=%s agent=%s" % (
                        physical_dpn_id, operation, candidate.agent_id))
                    raise TransportUncertain(r.get("error") or "uncertain")
                # Pre-dispatch safe failure: another Agent may try.
                self.lease.release(lease["lease_id"], "RELEASED")
                err = str(r.get("error") or status or "mutation_failed")
                if err == "DPN_IDENTITY_MISMATCH":
                    self._metric(physical_dpn_id, "identity_mismatch")
                    self.log("[DPN_MUTATION][IDENTITY_MISMATCH] dpn=%s agent=%s" % (
                        physical_dpn_id, candidate.agent_id))
                last = ReadFailover(err)
                self.log("[DPN_POOL][FAILOVER] dpn=%s op=%s from=%s reason=%s" % (
                    physical_dpn_id, operation, candidate.agent_id,
                    r.get("error") or status))
            except (TransportUncertain, AuthBackoff):
                raise
            except Exception as e:  # noqa: BLE001
                self.lease.release(lease["lease_id"], "RELEASED")
                last = e
        raise last or ReadFailover("NO_MUTATION_TRANSPORT")

    async def reconcile_switch(self, physical_dpn_id, tunnel_code, old_ip):
        data, chosen = await self.read(physical_dpn_id, P.LIST_DPN_TUNNELS)
        tunnels = data.get("tunnels") if isinstance(data, dict) else data
        current = None
        for t in tunnels or []:
            if isinstance(t, dict) and t.get("tunnelCode") == tunnel_code:
                current = t.get("activeIp")
                break
        if current and str(current) != str(old_ip):
            result = "APPLIED"
        else:
            result = "NOT_APPLIED_OR_NO_CHANGE"
        self.log("[DPN_RECONCILE] dpn=%s tunnel=%s old=%s current=%s result=%s agent=%s" % (
            physical_dpn_id, tunnel_code, old_ip, current, result, chosen.agent_id))
        return {"result": result, "old_ip": old_ip, "current_ip": current,
                "agent_id": chosen.agent_id}

    def _dpn_metric_snapshot(self, dpns, rows):
        out = {}
        for pid, d in dpns.items():
            m = dict(self.dpn_metrics.get(pid, {}))
            m.update({"pool_agents_total": d.get("agents", 0),
                      "pool_agents_ready": d.get("authenticated", 0),
                      "mutation_agent": d.get("best_agent_id", ""),
                      "read_failovers": m.get("read_failovers", 0),
                      "identity_mismatch": m.get("identity_mismatch", 0)})
            out[pid] = m
        return out

    def status(self):
        snap = self.server.snapshot()
        presence = {c.agent_id: c for c in self.presence.all()}
        rows = []
        for rec in snap.get("agents") or []:
            aid = str(rec.get("agent_id") or "")
            c = presence.get(aid)
            rows.append({
                "agent_id": aid,
                "physical_dpn_id": c.physical_dpn_id if c else "",
                "status": str(rec.get("status") or "OFFLINE"),
                "authenticated": bool(c and c.authenticated),
                "dpn_ready": self._capable(rec),
                "latency_ms": round(float(c.latency_ms), 1) if c else 0,
                "last_seen": float(rec.get("last_seen") or 0),
                "lan_ip": rec.get("lan_ip") or "",
                "error": "" if c else ("identity_pending" if self._capable(rec) else "no_dpn_credentials"),
                "agent_version": rec.get("agent_version") or "",
            })
        rows.sort(key=lambda r: (r["physical_dpn_id"], r["agent_id"]))
        dpns = {}
        for c in presence.values():
            d = dpns.setdefault(c.physical_dpn_id, {
                "physical_dpn_id": c.physical_dpn_id, "agents": 0, "online": 0,
                "authenticated": 0, "best_agent_id": "", "latency_ms": 0})
            d["agents"] += 1
            st = (snap.get("agents") or [])
            live = next((x for x in st if x.get("agent_id") == c.agent_id), None)
            if live and str(live.get("status") or "").upper() == "ONLINE":
                d["online"] += 1
            if c.authenticated:
                d["authenticated"] += 1
            if c.authenticated and (not d["best_agent_id"] or c.latency_ms < d["latency_ms"]):
                d["best_agent_id"] = c.agent_id
                d["latency_ms"] = round(float(c.latency_ms), 1)
        return {
            "updated_at": time.time(),
            "controller_pid": None,
            "agents": rows,
            "dpns": dpns,
            "server": {
                "connections": snap.get("connections", 0),
                "pending": snap.get("pending", 0),
                "metrics": snap.get("metrics", {}),
            },
            "pool_metrics": dict(self.metrics),
            "dpn_metrics": self._dpn_metric_snapshot(dpns, rows),
            "leases": self.lease.snapshot(),
            "auth_backoff": self.auth.snapshot(),
        }
