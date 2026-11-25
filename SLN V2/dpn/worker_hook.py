#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/worker_hook.py — SLN V2: cliente FINO usado pelo main_sln_v2 (camada DPN).

O main NÃO conhece o contador DPN. Ele só chama:
  - gate(device, instance_id)                  -> True (pode iniciar NOVO ciclo) / False (bloqueado)
  - on_terminal_cycle(device, instance_id, cycle_id, result)  -> contabiliza 1 work unit

Responsabilidades AQUI:
  - resolver a DPN do device (dpn_groups.json);
  - seq persistido / idempotência / account_cycle / clamp / drain_overshoot (via dpn.state);
  - sinalizar DRAINING quando o limite do IP é atingido;
  - publicar SAFE_TO_ROTATE quando bloqueado no gate ENTRE ciclos.

Default OFF (`dpn_routing_enabled=false`): gate=True imediato; on_terminal_cycle=None;
NENHUM arquivo/lock em state/dpn é criado.
"""
import json
import os
import time

from . import state as S

# Estados em que o worker NÃO inicia nova work unit (req. 7) + PAUSED/ERROR (segurança).
BLOCK_STATES = {
    S.DRAINING, S.ROTATION_PENDING, S.ROTATING, S.VERIFYING_IP, S.IP_COOLDOWN,
    S.RESET_NODE_PENDING, S.RESETTING_NODE, S.WAITING_NEW_NODE, S.NODE_EXHAUSTED,
    S.PAUSED, S.ERROR,
}

_CFG = None


def _cfg():
    global _CFG
    if _CFG is None:
        try:
            import config_sln_v2 as C
            _CFG = C.load_config()
        except Exception:
            _CFG = {}
    return _CFG


def _base():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def state_dir(cfg=None):
    cfg = cfg or _cfg()
    p = cfg.get("dpn_state_dir", os.path.join("state", "dpn"))
    return p if os.path.isabs(p) else os.path.join(_base(), p)


def groups_path(cfg=None):
    cfg = cfg or _cfg()
    p = cfg.get("dpn_groups_file", "dpn_groups.json")
    return p if os.path.isabs(p) else os.path.join(_base(), p)


def load_groups(cfg=None):
    try:
        with open(groups_path(cfg), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_dpn(device, cfg=None):
    for dpn_id, grp in load_groups(cfg).items():
        if str(dpn_id).startswith("_"):
            continue
        if isinstance(grp, dict) and device in (grp.get("devices") or []):
            return dpn_id
    return None


def publish_safe(sd, dpn_id, device, instance_id=None, cycle_id=None, now=None):
    st = S.read_dpn_state(sd, dpn_id)
    epoch = int(st.get("drain_epoch", 0))
    p = os.path.join(sd, "workers", S.safe_id(device) + ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    S.atomic_write_json(p, {"device": device, "safe": True, "drain_epoch": epoch,
                            "instance_id": instance_id, "cycle_id": cycle_id,
                            "last_seen": (now if now is not None else time.time())})


def v2_enabled(cfg=None):
    cfg = cfg or _cfg()
    return bool(cfg.get("dpn_group_ledger_v2_enabled", False))


def resolve_route_group(device, cfg=None):
    from .group_ledger import route_groups_from_config
    for gid, grp in route_groups_from_config(load_groups(cfg)).items():
        if device in (grp.get("members") or []):
            return gid
    return None


def _group_state(cfg, group_id):
    from .group_ledger import GroupLedgerStore
    return GroupLedgerStore(state_dir(cfg)).load().get("groups", {}).get(group_id) or {}


def _publish_group_safe(sd, group_id, device, instance_id, cycle_id, cfg):
    from .group_ledger import GroupSignalBarrier, route_groups_from_config
    grp = route_groups_from_config(load_groups(cfg)).get(group_id) or {}
    try:
        GroupSignalBarrier(sd, group_id, grp.get("members") or [device]).publish_safe(
            device, instance_id=instance_id, cycle_id=cycle_id)
    except Exception:
        pass


def gate(device, instance_id=None, cycle_id=None, cfg=None):
    """True = pode iniciar NOVO ciclo; False = DPN bloqueia (o caller deve esperar/continuar).

    Só deve ser chamado ENTRE ciclos (sem CycleSession ativa) e fora de reset/clear pendente.
    Ao bloquear, publica SAFE_TO_ROTATE (o worker está num ponto seguro entre ciclos)."""
    cfg = cfg or _cfg()
    if not cfg.get("dpn_routing_enabled", False):
        return True
    sd = state_dir(cfg)
    if v2_enabled(cfg):
        group_id = resolve_route_group(device, cfg)
        if not group_id:
            return True
        g = _group_state(cfg, group_id)
        from .group_ledger import BLOCK_STATES as V2_BLOCK
        limit = int(cfg.get("dpn_work_units_per_ip", 20) or 20)
        blocked = g.get("state") in V2_BLOCK or int(g.get("accepted_work_units", 0)) >= limit
        if blocked:
            _publish_group_safe(sd, group_id, device, instance_id, cycle_id, cfg)
            return False
        return True
    dpn_id = resolve_dpn(device, cfg)
    if not dpn_id:
        return True
    st = S.read_dpn_state(sd, dpn_id)
    if st.get("state") in BLOCK_STATES:
        try:
            publish_safe(sd, dpn_id, device, instance_id, cycle_id)
        except Exception:
            pass
        return False
    return True


def ip_epoch(device_id, cfg=None):
    """Identidade do IP atual da DPN do device (muda quando a unidade troca de IP).

    Usado pelo main para, ao detectar mudanca de IP, resetar o ID de anuncio.
    Retorna None quando DPN desligada/sem grupo/IP pronto."""
    cfg = cfg or _cfg()
    if not cfg.get("dpn_routing_enabled", False):
        return None
    if v2_enabled(cfg):
        group_id = resolve_route_group(device_id, cfg)
        if not group_id:
            return None
        g = _group_state(cfg, group_id)
        ip = str(g.get("current_public_ip") or "")
        ep = str(g.get("current_epoch_id") or g.get("route_generation_id") or "")
        if not ip and not ep:
            return None
        return ip + "|" + ep
    dpn_id = resolve_dpn(device_id, cfg)
    if not dpn_id:
        return None
    st = S.read_dpn_state(state_dir(cfg), dpn_id)
    ip = str(st.get("current_ip") or "")
    return ip or None


def on_terminal_cycle(device_id, instance_id, cycle_id, result, cfg=None):
    """Contabiliza UM ciclo terminal (COMPLETED/FAILED). Retorna o dict do account (ou None)."""
    cfg = cfg or _cfg()
    if not cfg.get("dpn_routing_enabled", False):
        return None
    if result not in ("COMPLETED", "FAILED"):
        return None
    sd = state_dir(cfg)
    if v2_enabled(cfg):
        group_id = resolve_route_group(device_id, cfg)
        if not group_id:
            return None
        from .group_ledger import GroupLedgerStore, make_work_unit_event
        ev = make_work_unit_event(
            cfg.get("host_id") or os.environ.get("SLN_HOST_ID") or "host",
            instance_id, device_id, cycle_id, result,
            app_package=os.environ.get("SLN_APP_PACKAGE", ""),
            route_group_id=group_id, source="worker")
        ev["accepted_for_epoch_quota"] = None
        GroupLedgerStore(sd).append_inbox(ev)
        return ev
    dpn_id = resolve_dpn(device_id, cfg)
    if not dpn_id:
        return None
    limit = int(cfg.get("dpn_work_units_per_ip", 20) or 20)
    r = S.account_cycle(sd, dpn_id, device_id, limit, cycle_id=cycle_id,
                        instance_id=instance_id, result=result)
    try:
        if int(r.get("work_units", 0)) >= limit:
            S.maybe_begin_draining(sd, dpn_id, limit)
    except Exception:
        pass
    return r
