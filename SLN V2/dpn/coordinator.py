#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/coordinator.py — SLN V2: máquina de estados por DPN (acima dos workers).

Regras (FASE 1 aprovada):
  - work_units pertencem a DPN_ID + IP_PÚBLICO_ATUAL (counter clampado em `limit`).
  - cooldown GLOBAL de IP (state/ip_history.json): AVAILABLE se nunca usado ou >= cooldown_s.
  - rotação só quando o IP atinge o limite (ou manualmente). 1 rotação/reset por DPN por vez.
  - após rotate_ip(): NÃO assumir sucesso — poll get_public_ip() até mudar (timeout).
  - IP novo ainda em cooldown GLOBAL (de outro node/DPN) => rejeitar e rotacionar de novo.
  - N rotações consecutivas sem IP elegível => NODE_EXHAUSTED -> reset_node -> novo node.
  - novo node NÃO valida IP antigo em cooldown (histórico global é independente).
  - crash/restart: reconcile() consulta o provider real antes de decidir.

Não toca Fast Close/reward/SUB_AD_CLOSE/CycleSession/CYCLE_ACCOUNTING_V2.
A classe é "stepwise" (`tick()` faz UM passo) para ser testável offline e determinística.
"""
import time

from . import state as S


class NullBarrier:
    """Barreira trivial (sem workers): sempre 'todos safe'."""

    def reset(self):
        pass

    def all_safe(self, timeout_s):
        return True

    def snapshot(self):
        return {"safe": 0, "active": 0}


class FakeBarrier:
    """Barreira determinística para testes.

    safe_sequence: lista de bools consumidos a cada all_safe(); após esgotar, repete o último.
    """

    def __init__(self, safe_sequence=None, default=True):
        self._seq = list(safe_sequence) if safe_sequence is not None else [default]
        self._i = 0
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1

    def all_safe(self, timeout_s):
        v = self._seq[min(self._i, len(self._seq) - 1)]
        if self._i < len(self._seq) - 1:
            self._i += 1
        return bool(v)

    def snapshot(self):
        return {"safe": "?", "active": "?"}


def _default_log(msg):
    print(msg, flush=True)


class DPNCoordinator:
    def __init__(self, state_dir, dpn_id, provider, limit=20, cooldown_s=3600,
                 dup_before_reset=5, settle_s=5, rotation_poll_s=2, rotation_timeout_s=60,
                 drain_timeout_s=120, barrier=None, now_fn=time.time, sleep_fn=time.sleep,
                 log_fn=None):
        self.state_dir = state_dir
        self.dpn_id = dpn_id
        self.provider = provider
        self.limit = int(limit)
        self.cooldown_s = float(cooldown_s)
        self.dup_before_reset = int(dup_before_reset)
        self.settle_s = float(settle_s)
        self.rotation_poll_s = float(rotation_poll_s)
        self.rotation_timeout_s = float(rotation_timeout_s)
        self.drain_timeout_s = float(drain_timeout_s)
        self.barrier = barrier or NullBarrier()
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn
        self.log = log_fn or _default_log

    # ---------------------------------------------------------------- util
    def _st(self):
        return S.read_dpn_state(self.state_dir, self.dpn_id)

    def _save(self, st):
        return S.save_dpn_state(self.state_dir, st)

    def _set(self, st, state, **fields):
        st["state"] = state
        for k, v in fields.items():
            st[k] = v
        self._save(st)

    def _log(self, tag, **kw):
        extra = " ".join("%s=%s" % (k, v) for k, v in kw.items())
        self.log("[DPN][%s] dpn=%s %s" % (tag, self.dpn_id, extra))

    # ---------------------------------------------------------------- reconcile
    def reconcile(self):
        """Após restart/crash: reconcilia estado transitório com a realidade do provider."""
        st = self._st()
        if st["state"] == S.ROTATING:
            old = st.get("_old_ip") or st.get("current_ip")
            cur = self.provider.get_public_ip()
            if cur is not None and cur != old:
                self._set(st, S.VERIFYING_IP, _pending_ip=cur)
                self._log("RECONCILE", from_="ROTATING", note="ip_changed")
            else:
                self._set(st, S.ROTATION_PENDING)
                self._log("RECONCILE", from_="ROTATING", note="ip_unchanged")
        elif st["state"] == S.RESETTING_NODE:
            self._set(st, S.WAITING_NEW_NODE)
            self._log("RECONCILE", from_="RESETTING_NODE")
        return self._st()["state"]

    # ---------------------------------------------------------------- passos
    def tick(self):
        """Executa UM passo da máquina de estados. Retorna um rótulo curto do que fez."""
        st = self._st()
        state = st["state"]
        if state in (S.PAUSED, S.ERROR):
            return state.lower()
        if state in (S.INIT, S.WAITING_NEW_NODE, S.DISCOVERING_IP):
            return self._step_discover(st)
        if state == S.VERIFYING_IP:
            return self._step_verify(st, st.get("_pending_ip"))
        if state in (S.READY, S.RUNNING):
            if int(st.get("current_ip_work_units", 0)) >= self.limit:
                return self._step_begin_drain(st)
            return "ready"
        if state == S.IP_LIMIT_REACHED:
            return self._step_begin_drain(st)
        if state == S.DRAINING:
            return self._step_drain(st)
        if state == S.ROTATION_PENDING:
            return self._step_rotate(st)
        if state == S.ROTATING:
            return self._step_after_rotate(st)
        if state == S.IP_COOLDOWN:
            return self._step_rotate(st)
        if state in (S.NODE_EXHAUSTED, S.RESET_NODE_PENDING):
            return self._step_reset_node(st)
        if state == S.RESETTING_NODE:
            self._set(st, S.WAITING_NEW_NODE)
            return "waiting_new_node"
        return "idle"

    def run(self, max_steps=100):
        stop = {"ready", "paused", "error", "node_exhausted"}
        for _ in range(max_steps):
            r = self.tick()
            if r in stop:
                return r
        return "max_steps"

    def _step_discover(self, st):
        self._set(st, S.DISCOVERING_IP)
        try:
            ip = self.provider.get_public_ip()
            node = self.provider.get_node_id()
        except Exception as e:  # provider tipado (TunnelNotFound/Connection/auth...) -> ERROR da DPN
            self._log("ERROR", reason="discover:%s" % type(e).__name__)
            self._set(st, S.ERROR, last_error="discover:%s" % type(e).__name__)
            return "error"
        if ip is None:
            self._log("ERROR", reason="no_public_ip")
            self._set(st, S.ERROR, last_error="no_public_ip")
            return "error"
        st.setdefault("node", {})
        st["node"]["node_id"] = node
        st["node"]["created_at"] = st["node"].get("created_at") or self.now_fn()
        if ip not in st["node"].get("ips_seen", []):
            st["node"].setdefault("ips_seen", []).append(ip)
        self._log("DISCOVERING_IP", ip=ip, node=node)
        self._set(st, S.VERIFYING_IP, _pending_ip=ip)
        return "discovering"

    def _step_verify(self, st, ip):
        if ip is None:
            self._set(st, S.DISCOVERING_IP)
            return "verifying"
        status, info = S.ip_available(self.state_dir, ip, self.cooldown_s, now=self.now_fn())
        if status == "AVAILABLE":
            if self.settle_s > 0:
                self.sleep_fn(self.settle_s)   # estabilização de rede antes do health
            try:
                healthy = self.provider.health()
                node = self.provider.get_node_id()
            except Exception as e:
                self._log("ERROR", reason="verify:%s" % type(e).__name__, ip=ip)
                self._set(st, S.ERROR, last_error="verify:%s" % type(e).__name__)
                return "error"
            if not healthy:
                self._log("ERROR", reason="unhealthy_after_rotate", ip=ip)
                self._set(st, S.ERROR, last_error="unhealthy_after_rotate")
                return "error"
            S.set_current_ip(self.state_dir, self.dpn_id, ip, node_id=node, now=self.now_fn())
            self._log("READY", ip=ip, node=node)
            return "ready"
        # cooldown: rotação sem IP elegível => conta duplicata; pode esgotar o node
        dup = int(st.get("duplicate_rotations", 0)) + 1
        self._log("IP_COOLDOWN", ip=ip, duplicate=dup,
                  remaining_s=round(info.get("remaining_s", 0), 1))
        if dup >= self.dup_before_reset:
            self._log("NODE_EXHAUSTED", ip=ip, duplicates=dup)
            self._set(st, S.NODE_EXHAUSTED, duplicate_rotations=dup)
            return "node_exhausted"
        self._set(st, S.IP_COOLDOWN, duplicate_rotations=dup)
        return "cooldown"

    def _step_begin_drain(self, st):
        self.barrier.reset()
        st["drain_epoch"] = int(st.get("drain_epoch", 0)) + 1
        self._log("IP_LIMIT", ip=st.get("current_ip"),
                  count=int(st.get("current_ip_work_units", 0)), limit=self.limit)
        self._set(st, S.DRAINING)
        return "draining"

    def _step_drain(self, st):
        if self.barrier.all_safe(self.drain_timeout_s):
            self._log("DRAIN", result="all_safe")
            self._set(st, S.ROTATION_PENDING)
            return "rotating"
        self._log("DRAIN", result="waiting")
        return "draining"

    def _step_rotate(self, st):
        old = st.get("current_ip")
        self._log("ROTATE_START", mode="REGULAR", old_ip=old)
        try:
            self.provider.rotate_ip("REGULAR")
        except Exception as e:
            self._log("ROTATE_RESULT", ok=False, error=type(e).__name__)
            self._set(st, S.ERROR)
            return "error"
        self._set(st, S.ROTATING, _old_ip=old)
        return "rotating"

    def _poll_ip_change(self, old_ip):
        deadline = self.now_fn() + self.rotation_timeout_s
        while True:
            ip = self.provider.get_public_ip()
            if ip is not None and ip != old_ip:
                return ip
            if self.now_fn() >= deadline:
                return None
            self.sleep_fn(self.rotation_poll_s)

    def _step_after_rotate(self, st):
        old = st.get("_old_ip")
        try:
            new = self._poll_ip_change(old)
        except Exception as e:
            self._log("ERROR", reason="verify_poll:%s" % type(e).__name__)
            self._set(st, S.ERROR, last_error="verify_poll:%s" % type(e).__name__)
            return "error"
        if new is None:
            dup = int(st.get("duplicate_rotations", 0)) + 1
            self._log("ROTATE_RESULT", ok=False, reason="ip_unchanged", duplicate=dup)
            if dup >= self.dup_before_reset:
                self._log("NODE_EXHAUSTED", duplicates=dup)
                self._set(st, S.NODE_EXHAUSTED, duplicate_rotations=dup)
                return "node_exhausted"
            self._set(st, S.ROTATION_PENDING, duplicate_rotations=dup)
            return "rotating"
        self._log("ROTATE_RESULT", ok=True, new_ip=new)
        st.setdefault("node", {})
        if new not in st["node"].get("ips_seen", []):
            st["node"].setdefault("ips_seen", []).append(new)
        self._set(st, S.VERIFYING_IP, _pending_ip=new)
        return "verifying"

    def _step_reset_node(self, st):
        st["duplicate_rotations"] = 0
        self._log("NODE_RESET", mode="REGULAR")
        self._set(st, S.RESETTING_NODE)
        try:
            self.provider.reset_node()
        except Exception as e:
            self._log("NODE_RESET", ok=False, error=type(e).__name__)
            self._set(st, S.ERROR)
            return "error"
        self._set(st, S.WAITING_NEW_NODE)
        self._log("NODE_RESET", ok=True)
        return "node_reset"

    # ---------------------------------------------------------------- controle manual
    def pause(self):
        self._set(self._st(), S.PAUSED)
        self._log("PAUSE")

    def resume(self):
        st = self._st()
        if st["state"] == S.PAUSED:
            self._set(st, S.RUNNING if st.get("current_ip") else S.INIT)
        self._log("RESUME")

    def rotate_now(self):
        st = self._st()
        if st["state"] not in (S.PAUSED, S.ERROR):
            self._set(st, S.ROTATION_PENDING)
        self._log("ROTATE_NOW")

    def reset_node_now(self):
        st = self._st()
        if st["state"] not in (S.PAUSED, S.ERROR):
            self._set(st, S.RESET_NODE_PENDING)
        self._log("RESET_NODE_NOW")

    def snapshot(self):
        st = self._st()
        return {
            "dpn_id": self.dpn_id, "state": st["state"], "current_ip": st.get("current_ip"),
            "node_id": (st.get("node") or {}).get("node_id"),
            "work_units": int(st.get("current_ip_work_units", 0)), "limit": self.limit,
            "duplicate_rotations": int(st.get("duplicate_rotations", 0)),
            "drain_overshoot": int(st.get("drain_overshoot", 0)),
            "epoch": int(st.get("epoch", 0)),
        }
