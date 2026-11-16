#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_scale.py — SLN V2 (FASE 11)

Testes OFFLINE (sem device) das peças de escala: persistent logcat (parser + piso), adaptive tick,
watchdog (heartbeat/stale/restart/quarantine) e sharding. Não matam processos reais.

Rodar:  python tests/test_scale.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import persistent_logcat as PL
import adaptive_tick as AT
import watchdog as WD
import sharding


PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  ", name)
    else:
        FAIL += 1
        print("  FAIL", name)


# ---------- persistent_logcat: parser ----------
def test_parse():
    print("[persistent_logcat] parser")
    l_app = "09-29 13:05:10.100  831  831 D AdHelperLog: FORMAT=INTERSTITIAL EVENT=SHOWED"
    l_rew = "09-29 13:05:10.100  831  831 D AdHelperLog: FORMAT=REWARDED EVENT=SHOWED"
    l_cas = "09-29 13:05:10.200  831  859 I CAS.AI  : Rewarded Ad Impression AppLovin | Value = $0.002 | CreativeId: 6863651 | UnitId: fc55"
    l_clk = "09-29 13:05:11.000  831  831 D AdHelperLog: FORMAT=REWARDED EVENT=CLICKED"
    l_rew2 = "09-29 13:05:12.000  831  831 D AdHelperLog: FORMAT=REWARDED EVENT=REWARD_EARNED"
    l_dis = "09-29 13:05:12.100  831  831 D AdHelperLog: FORMAT=REWARDED EVENT=DISMISSED"
    ev = PL.parse_event(l_app, 1.0)
    check("app SHOWED interstitial", ev and ev["kind"] == "app_showed" and ev["format"] == "interstitial")
    ev = PL.parse_event(l_rew, 1.0)
    check("app SHOWED rewarded", ev and ev["format"] == "rewarded")
    ev = PL.parse_event(l_cas, 1.0)
    check("cas impression (+creative/unit)", ev and ev["kind"] == "cas_impression" and ev["creative_id"] == "6863651")
    check("CLICKED", PL.parse_event(l_clk, 1.0)["kind"] == "clicked")
    check("REWARD_EARNED", PL.parse_event(l_rew2, 1.0)["kind"] == "reward_earned")
    check("DISMISSED", PL.parse_event(l_dis, 1.0)["kind"] == "dismissed")
    check("linha irrelevante -> None", PL.parse_event("09-29 13:05:10.000 1 1 I Other: x", 1.0) is None)


# ---------- persistent_logcat: buffer + event floor ----------
def test_buffer_floor():
    print("[persistent_logcat] buffer + event floor")
    b = PL.EventBuffer()
    b.add({"kind": "app_showed", "format": "interstitial", "pid": "1", "device_ts": "t", "recv_perf": 100.0})
    # evento ANTES do piso (SHOWED do anúncio anterior) -> ignorado
    check("SHOWED anterior ao piso ignorado", b.select_format(floor_perf=200.0, now_perf=250.0, max_age_s=30) is None)
    # evento atual (após o piso) -> aceito
    b.add({"kind": "app_showed", "format": "rewarded", "pid": "1", "device_ts": "t2", "recv_perf": 250.0})
    r = b.select_format(floor_perf=200.0, now_perf=260.0, max_age_s=30)
    check("SHOWED atual aceito", r and r["format"] == "rewarded" and r["source"] == "app_event")
    # idade > max_age -> ignorado
    check("SHOWED expirado ignorado", b.select_format(floor_perf=200.0, now_perf=1000.0, max_age_s=30) is None)
    # CAS fallback quando não há app_event válido
    b2 = PL.EventBuffer()
    b2.add({"kind": "cas_impression", "format": "interstitial", "pid": "1", "device_ts": "t", "recv_perf": 210.0})
    r2 = b2.select_format(floor_perf=200.0, now_perf=215.0, max_age_s=30)
    check("CAS fallback", r2 and r2["source"] == "cas_event")
    # app vence cas
    b2.add({"kind": "app_showed", "format": "rewarded", "pid": "1", "device_ts": "t3", "recv_perf": 220.0})
    r3 = b2.select_format(floor_perf=200.0, now_perf=230.0, max_age_s=30)
    check("app_event vence cas_event", r3 and r3["source"] == "app_event" and r3["format"] == "rewarded")
    # ring buffer limitado
    b3 = PL.EventBuffer(maxlen=5)
    for i in range(20):
        b3.add({"kind": "app_showed", "format": "x", "pid": "1", "device_ts": "t", "recv_perf": float(i)})
    check("ring limitado", len(b3.events) == 5 and b3.dropped == 15)


# ---------- adaptive_tick ----------
def test_tick():
    print("[adaptive_tick] mapeamento + burst")
    cfg = {"fast_close_tick_ms": 300}
    check("pill -> 275", AT.tick_ms_for("pill", cfg) == 275)
    check("endcard -> 275", AT.tick_ms_for("endcard", cfg) == 275)
    check("fallback default", AT.tick_ms_for("inexistente", cfg) == 300)
    check("override do cfg", AT.tick_ms_for("video", {"adaptive_tick_ms": {"video": 999}}) == 999)
    check("state pill (visivel)", AT.state_key(phase="video", pill_visible=True) == "pill")
    check("state endcard", AT.state_key(phase="endcard") == "endcard")
    check("state store", AT.state_key(phase="store_after_skip") == "store")
    check("state countdown ending", AT.state_key(phase="video", cd_state="ending") == "countdown_ending")
    check("state rewarded sem countdown", AT.state_key(phase="video", ad_type="rewarded", cd_state="unknown") == "video_rewarded")
    check("state recovery tem prioridade", AT.state_key(phase="video", in_recovery=True) == "recovery")
    bc = AT.BurstController({"adaptive_tick_burst_ms": 250, "adaptive_tick_burst_seconds": 3.0})
    ms1 = bc.effective_ms("pill", now=100.0, cfg=cfg)   # entra em pill -> burst
    ms2 = bc.effective_ms("pill", now=101.0, cfg=cfg)   # ainda burst
    ms3 = bc.effective_ms("pill", now=104.0, cfg=cfg)   # passou -> tick do estado
    check("burst ao entrar em critico", ms1 == 250 and ms2 == 250)
    check("sai do burst apos a janela", ms3 == 275)


# ---------- watchdog ----------
def test_watchdog(tmp):
    print("[watchdog] heartbeat/stale/restart/quarantine")
    os.makedirs(tmp, exist_ok=True)
    dev = "192.168.1.99:5555"
    p = WD.heartbeat_path(tmp, dev)
    WD.write_heartbeat(p, {"device": dev, "state": "RUNNING", "last_progress": 1000.0, "mono_ts": 1000.0})
    check("read heartbeat", WD.read_heartbeat(p)["state"] == "RUNNING")
    sup = WD.Supervisor(tmp, stale_s=120, restart_max=2, restart_fn=lambda d: None, now_fn=lambda: 1050.0, log_fn=lambda *a: None)
    check("progress recente -> RUNNING", sup.classify(dev, WD.read_heartbeat(p), 1050.0) == WD.RUNNING)
    check("sem stale", sup.stale_devices([dev]) == [])
    # progresso velho -> RECOVERING
    WD.write_heartbeat(p, {"device": dev, "state": "RUNNING", "last_progress": 1000.0, "mono_ts": 1200.0})
    sup.now_fn = lambda: 1400.0  # 400s > 120s
    check("progresso velho -> RECOVERING", sup.classify(dev, WD.read_heartbeat(p), 1400.0) == WD.RECOVERING)
    actions = []
    sup.restart_fn = lambda d: actions.append(d)
    sup.poll([dev]); sup.poll([dev]); sup.poll([dev])  # restart 1, 2, 3 -> quarantine no 3o
    check("restart chamado", actions.count(dev) >= 2)
    check("quarantine apos N", sup.state[dev] == WD.QUARANTINED)
    # heartbeat ausente -> DISCONNECTED
    check("sem heartbeat -> DISCONNECTED", sup.classify("192.168.1.50:5555", None, 1400.0) == WD.DISCONNECTED)


# ---------- sharding ----------
def test_sharding(tmp):
    print("[sharding] load/assign/resolve")
    devs = ["192.168.1.%d:5555" % i for i in range(1, 11)]
    p = sharding.write_shard(tmp, "hostA", devs[:4])
    check("write/load shard", sharding.load_shard(tmp, "hostA") == devs[:4])
    check("shard inexistente -> []", sharding.load_shard(tmp, "hostZ") == [])
    a = sharding.assign(devs, 0, 2); b = sharding.assign(devs, 1, 2)
    check("assign sem sobreposicao", set(a).isdisjoint(set(b)) and len(a) + len(b) == len(devs))
    r, src = sharding.resolve_devices(tmp, "hostA", discovered=devs)
    check("resolve usa shard_file", r == devs[:4] and src == "shard_file")
    r2, src2 = sharding.resolve_devices(tmp, "", discovered=devs, host_index=0, host_count=2)
    check("resolve sem shard -> assign", src2 == "assign" and len(r2) == 5)


def main():
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "sln_scale_test")
    test_parse()
    test_buffer_floor()
    test_tick()
    test_watchdog(tmp)
    test_sharding(tmp)
    print("\nRESULT: PASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
