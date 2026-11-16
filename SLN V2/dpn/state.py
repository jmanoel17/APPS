#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/state.py — SLN V2: persistência atômica do DPN (estado por DPN + histórico GLOBAL de IP).

Arquivos (default `state/dpn/`):
  dpn_<id>.json        estado de UMA DPN (node, current_ip, work_units, state, devices{seq}, ...)
  .dpn_<id>.lock       lock de exclusão da DPN (InterProcessFileLock)
  ip_history.json      histórico GLOBAL de IPs (sobrevive a reset/troca de node)
  .ip_history.lock     lock do histórico global

Escrita ATÔMICA: tmp no mesmo diretório -> flush -> fsync -> os.replace.

Contabilidade (authoritative seq):
  `account_cycle()` faz read-modify-write SOB o lock da DPN. A chave AUTORITATIVA é o seq
  persistido por (DPN, device). `instance_id` + `cycle_id` são metadata e servem de token de
  IDEMPOTÊNCIA (retry do MESMO ciclo não conta duas vezes).
  Clamp do quota counter em `limit`: ciclos já em voo NÃO passam de `limit` no counter; são
  registrados como `drain_overshoot` e `accepted_for_ip_quota=False`. NÃO altera Accounting V2.

Cooldown GLOBAL de IP:
  IP nunca usado -> AVAILABLE; now-last_used_at >= cooldown_s -> AVAILABLE; senão COOLDOWN.
"""
import os
import tempfile
import time

from .locks import InterProcessFileLock

STATE_VERSION = 1
HISTORY_VERSION = 1

DEFAULT_STATE_DIR = os.path.join("state", "dpn")

# estados (mesma nomenclatura da spec)
INIT = "INIT"
DISCOVERING_IP = "DISCOVERING_IP"
READY = "READY"
RUNNING = "RUNNING"
IP_LIMIT_REACHED = "IP_LIMIT_REACHED"
DRAINING = "DRAINING"
ROTATION_PENDING = "ROTATION_PENDING"
ROTATING = "ROTATING"
VERIFYING_IP = "VERIFYING_IP"
IP_COOLDOWN = "IP_COOLDOWN"
NODE_EXHAUSTED = "NODE_EXHAUSTED"
RESET_NODE_PENDING = "RESET_NODE_PENDING"
RESETTING_NODE = "RESETTING_NODE"
WAITING_NEW_NODE = "WAITING_NEW_NODE"
ERROR = "ERROR"
PAUSED = "PAUSED"


# ---------------------------------------------------------------- utilidades
def safe_id(s):
    return str(s).replace(":", "_").replace(".", "_").replace("/", "_").replace("\\", "_")


def _now(now=None):
    return time.time() if now is None else float(now)


def atomic_write_json(path, obj):
    """tmp no mesmo dir -> write -> flush -> fsync -> os.replace. Nunca deixa arquivo parcial."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            import json
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False


def read_json(path, default=None):
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ---------------------------------------------------------------- caminhos
def dpn_state_path(state_dir, dpn_id):
    return os.path.join(state_dir, "dpn_%s.json" % safe_id(dpn_id))


def dpn_lock_path(state_dir, dpn_id):
    return os.path.join(state_dir, ".dpn_%s.lock" % safe_id(dpn_id))


def ip_history_path(state_dir):
    return os.path.join(state_dir, "ip_history.json")


def ip_history_lock_path(state_dir):
    return os.path.join(state_dir, ".ip_history.lock")


# ---------------------------------------------------------------- estado DPN
def init_dpn_state(dpn_id, now=None):
    return {
        "version": STATE_VERSION,
        "dpn_id": dpn_id,
        "node": {"node_id": None, "created_at": None, "ips_seen": []},
        "current_ip": None,
        "current_ip_work_units": 0,
        "state": INIT,
        "duplicate_rotations": 0,
        "epoch": 0,
        "drain_epoch": 0,
        "drain_overshoot": 0,
        "devices": {},          # device -> {"seq": int}
        "accounted_keys": [],   # tokens de idempotência (LRU limitada)
        "updated_at": _now(now),
    }


def load_dpn_state(state_dir, dpn_id, now=None):
    st = read_json(dpn_state_path(state_dir, dpn_id), None)
    if not isinstance(st, dict) or st.get("dpn_id") != dpn_id:
        return init_dpn_state(dpn_id, now)
    # sane defaults para campos ausentes (migração leve)
    st.setdefault("node", {"node_id": None, "created_at": None, "ips_seen": []})
    st["node"].setdefault("ips_seen", [])
    st.setdefault("devices", {})
    st.setdefault("accounted_keys", [])
    st.setdefault("duplicate_rotations", 0)
    st.setdefault("epoch", 0)
    st.setdefault("drain_epoch", 0)
    st.setdefault("drain_overshoot", 0)
    return st


def save_dpn_state(state_dir, st):
    st["updated_at"] = _now()
    return atomic_write_json(dpn_state_path(state_dir, st["dpn_id"]), st)


def _lru_push(keys, token, cap=4000):
    if token in keys:
        return
    keys.append(token)
    if len(keys) > cap:
        del keys[: len(keys) - cap]


# ---------------------------------------------------------------- contabilidade
def account_cycle(state_dir, dpn_id, device_id, limit, cycle_id, instance_id,
                  result="COMPLETED", now=None, overshoot_cap=100000):
    """Contabiliza UM ciclo terminal (COMPLETED/FAILED) — 1 vez.

    Idempotente por token de metadata `(instance_id, cycle_id)`. O `seq` persistido por
    (dpn, device) é a chave AUTORITATIVA (ordinal). Retorna dict:
      {counted, duplicate, accepted_for_ip_quota, work_units, limit, over_limit, seq,
       current_ip, state}
    """
    now = _now(now)
    token = "%s|%s" % (instance_id if instance_id is not None else "", cycle_id)
    out = {"counted": False, "duplicate": False, "accepted_for_ip_quota": False,
           "work_units": 0, "limit": int(limit), "over_limit": False,
           "seq": None, "current_ip": None, "state": INIT}
    lock = InterProcessFileLock(dpn_lock_path(state_dir, dpn_id), timeout=15.0)
    accepted_ip = None
    with lock:
        st = load_dpn_state(state_dir, dpn_id, now)
        out["current_ip"] = st.get("current_ip")
        out["state"] = st.get("state")

        if token in st["accounted_keys"]:
            out["duplicate"] = True
            out["work_units"] = int(st.get("current_ip_work_units", 0))
            out["over_limit"] = out["work_units"] >= int(limit)
            return out

        # novo ciclo: claim do seq AUTORITATIVO (persistido) + token de idempotência
        dev = st["devices"].setdefault(str(device_id), {"seq": 0})
        seq = int(dev.get("seq", 0)) + 1
        dev["seq"] = seq
        _lru_push(st["accounted_keys"], token)
        out["counted"] = True
        out["seq"] = seq

        ip = st.get("current_ip")
        units = int(st.get("current_ip_work_units", 0))
        if ip is not None and units < int(limit):
            units += 1
            st["current_ip_work_units"] = units
            out["accepted_for_ip_quota"] = True
            accepted_ip = ip
            if units >= int(limit):
                st["state"] = IP_LIMIT_REACHED
                out["state"] = IP_LIMIT_REACHED
        else:
            # clamp: counter nunca passa do limite; ciclo em voo vira overshoot
            if int(st.get("drain_overshoot", 0)) < overshoot_cap:
                st["drain_overshoot"] = int(st.get("drain_overshoot", 0)) + 1
            out["accepted_for_ip_quota"] = False
            out["over_limit"] = True

        out["work_units"] = int(st.get("current_ip_work_units", 0))
        out["current_ip"] = st.get("current_ip")
        out["over_limit"] = out["over_limit"] or out["work_units"] >= int(limit)
        save_dpn_state(state_dir, st)

    if accepted_ip is not None:
        node_id = None
        try:
            node_id = (load_dpn_state(state_dir, dpn_id, now).get("node") or {}).get("node_id")
        except Exception:
            node_id = None
        record_ip_use(state_dir, accepted_ip, dpn_id, node_id, now)
    return out


# ---------------------------------------------------------------- histórico global de IP
def load_ip_history(state_dir):
    h = read_json(ip_history_path(state_dir), None)
    if not isinstance(h, dict) or "ips" not in h:
        return {"version": HISTORY_VERSION, "ips": {}}
    h.setdefault("ips", {})
    return h


def record_ip_use(state_dir, ip, dpn_id=None, node_id=None, now=None, inc_units=1):
    """Registra USO do IP (last_used_at, total_work_units, dpns, nodes) no histórico GLOBAL."""
    if not ip:
        return
    now = _now(now)
    lock = InterProcessFileLock(ip_history_lock_path(state_dir), timeout=15.0)
    with lock:
        h = load_ip_history(state_dir)
        e = h["ips"].setdefault(str(ip), {
            "first_seen": now, "last_seen": now, "last_used_at": None,
            "total_work_units": 0, "dpns": [], "nodes": [],
        })
        e["last_seen"] = now
        e["last_used_at"] = now
        e["total_work_units"] = int(e.get("total_work_units", 0)) + int(inc_units)
        if dpn_id and dpn_id not in e["dpns"]:
            e["dpns"].append(dpn_id)
        if node_id and node_id not in e["nodes"]:
            e["nodes"].append(node_id)
        atomic_write_json(ip_history_path(state_dir), h)


def ip_available(state_dir, ip, cooldown_s, now=None):
    """(status, info). status: 'AVAILABLE' | 'COOLDOWN'. info: dict com remaining_s/last_used_at."""
    now = _now(now)
    h = load_ip_history(state_dir)
    e = h["ips"].get(str(ip))
    if not e:
        return "AVAILABLE", {"ip": ip, "reason": "never_used"}
    lu = e.get("last_used_at")
    if lu is None:
        return "AVAILABLE", {"ip": ip, "reason": "never_used"}
    age = now - float(lu)
    if age >= float(cooldown_s):
        return "AVAILABLE", {"ip": ip, "reason": "cooldown_elapsed", "age_s": age}
    return "COOLDOWN", {"ip": ip, "reason": "within_cooldown",
                        "remaining_s": float(cooldown_s) - age, "last_used_at": lu}


# ---------------------------------------------------------------- helpers de transição
def set_current_ip(state_dir, dpn_id, ip, node_id=None, now=None, reset_units=True):
    """Define o IP atual e prepara a DPN para RUNNING (novo epoch). Usado pelo coordinator."""
    now = _now(now)
    lock = InterProcessFileLock(dpn_lock_path(state_dir, dpn_id), timeout=15.0)
    with lock:
        st = load_dpn_state(state_dir, dpn_id, now)
        st["current_ip"] = ip
        if reset_units:
            st["current_ip_work_units"] = 0
        st["state"] = RUNNING
        st["epoch"] = int(st.get("epoch", 0)) + 1
        st["last_error"] = None   # transição saudável limpa o erro anterior
        if node_id is not None:
            st["node"]["node_id"] = node_id
            st["node"]["created_at"] = st["node"].get("created_at") or now
            if ip not in st["node"]["ips_seen"]:
                st["node"]["ips_seen"].append(ip)
        save_dpn_state(state_dir, st)
    return st


def set_state(state_dir, dpn_id, state, now=None, **fields):
    """Muda o estado da DPN (+ campos extras), sob lock. Puro (sem provider)."""
    now = _now(now)
    lock = InterProcessFileLock(dpn_lock_path(state_dir, dpn_id), timeout=15.0)
    with lock:
        st = load_dpn_state(state_dir, dpn_id, now)
        st["state"] = state
        for k, v in fields.items():
            st[k] = v
        save_dpn_state(state_dir, st)
    return st


def read_dpn_state(state_dir, dpn_id, now=None):
    return load_dpn_state(state_dir, dpn_id, now)


# ---------------------------------------------------------------- draining (barrier)
def begin_draining(state_dir, dpn_id, now=None):
    """Transição atômica para DRAINING + bump do drain_epoch (invalida safes antigos)."""
    now = _now(now)
    lock = InterProcessFileLock(dpn_lock_path(state_dir, dpn_id), timeout=15.0)
    with lock:
        st = load_dpn_state(state_dir, dpn_id, now)
        st["state"] = DRAINING
        st["drain_epoch"] = int(st.get("drain_epoch", 0)) + 1
        save_dpn_state(state_dir, st)
    return st


def maybe_begin_draining(state_dir, dpn_id, limit, now=None):
    """Se o IP atingiu o limite e a DPN está em READY/RUNNING/IP_LIMIT_REACHED, entra em DRAINING.
    Retorna True se transitou. (Chamado pelo worker_hook ao contabilizar o ciclo que fecha a quota.)"""
    now = _now(now)
    lock = InterProcessFileLock(dpn_lock_path(state_dir, dpn_id), timeout=15.0)
    with lock:
        st = load_dpn_state(state_dir, dpn_id, now)
        if st.get("state") not in (READY, RUNNING, IP_LIMIT_REACHED):
            return False
        if int(st.get("current_ip_work_units", 0)) < int(limit):
            return False
        st["state"] = DRAINING
        st["drain_epoch"] = int(st.get("drain_epoch", 0)) + 1
        save_dpn_state(state_dir, st)
        return True
