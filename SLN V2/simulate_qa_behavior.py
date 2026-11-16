#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
simulate_qa_behavior.py — SLN V2 (QA realistic): SIMULAÇÃO OFFLINE (sem ADB).

Simula o scheduler de N devices por C ciclos com seed fixa e clock VIRTUAL, usando os MESMOS
objetos do bot (qa_behavior). Objetivo: provar distribuição (sem sincronização), intervalo 2..30,
quota de reset, faixa de install e cooldown 24h.

Uso:
  python simulate_qa_behavior.py --devices 20 --cycles 100 --seed 123
  python simulate_qa_behavior.py --devices 50 --cycles 100 --seed 123
"""
import argparse
import json
import statistics as st

import qa_behavior as QA


class VClock:
    def __init__(self, t=0.0):
        self.t = float(t)

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += max(0.0, float(dt))


def run(devices, cycles, seed, reset_duration_s=25.0, install_duration_s=30.0,
        qa_cfg_over=None):
    cfg = {
        "qa_realistic_behavior_enabled": True,
        "qa_action_delay_min_s": 10, "qa_action_delay_max_s": 60,
        "qa_reset_interval_min_cycles": 2, "qa_reset_interval_max_cycles": 30,
        "qa_reset_population_pct_min": 5, "qa_reset_population_pct_max": 25,
        "qa_max_concurrent_resets": 2,
        "qa_reset_start_jitter_min_s": 0, "qa_reset_start_jitter_max_s": 15,
        "qa_install_pct_min": 5, "qa_install_pct_max": 10,
        "qa_install_cooldown_hours": 24, "qa_random_seed": seed,
        "qa_reset_window_minutes": 30, "qa_install_window_hours": 24,
    }
    if qa_cfg_over:
        cfg.update(qa_cfg_over)
    clock = VClock(0.0)
    qcfg = QA.QaConfig(cfg, base_dir=".", log_fn=None)
    pop = len(devices)
    # coordinator compartilhado (frota); rng da frota derivado da seed global
    fleet_rng = QA.make_rng(QA.device_seed(seed, "FLEET"))
    coord = QA.QACoordinator(fleet_rng, qcfg.reset_pct_min, qcfg.reset_pct_max,
                             qcfg.max_concurrent_resets, qcfg.install_pct_min, qcfg.install_pct_max,
                             qcfg.reset_window_s, qcfg.install_window_s, now_fn=clock.now)

    devs = {}
    for d in devices:
        s_seed = QA.device_seed(seed, d)
        rng = QA.make_rng(s_seed)
        st = QA.DeviceQABehaviorState(d, s_seed)
        sched = QA.QABehaviorScheduler(d, st, rng, qcfg, coord, population_fn=lambda: pop,
                                       now_fn=clock.now, sleep_fn=clock.sleep)
        sched.ensure_initialized()
        devs[d] = {"sched": sched, "state": st, "rng": rng, "delays": [], "reset_positions": [],
                   "install_cycles": [], "reset_intervals": []}

    reset_events = []  # (cycle, t)
    install_events = []
    for c in range(cycles):
        for d in devices:
            rec = devs[d]
            sched = rec["sched"]
            st = rec["state"]
            plan = sched.begin_cycle()  # aplica delay (avança clock) + decide reset
            rec["delays"].append(st.next_action_delay_s)
            acted_reset = False
            if plan["action"] == QA.RESET_ALLOWED:
                older = st.reset_interval_cycles
                clock.sleep(reset_duration_s)  # reset+clear ocupa tempo
                sched.on_reset_result(True)
                rec["reset_intervals"].append(older)
                rec["reset_positions"].append(c)
                reset_events.append((c, clock.now()))
                acted_reset = True
            # oportunidade de instalação (1 por ciclo do device)
            if sched.install_decision():
                clock.sleep(install_duration_s)
                sched.on_install_result(True)
                rec["install_cycles"].append(c)
                install_events.append((c, clock.now()))
            sched.cycle_completed()
    return devs, reset_events, install_events, clock, coord


def analyze(devices, devs, reset_events, install_events, clock, coord, cycles):
    total_resets = len(reset_events)
    total_installs = len(install_events)
    total_days = clock.t / 86400.0 if clock.t else 1.0

    per_device_resets = [len(devs[d]["reset_positions"]) for d in devices]
    reset_intervals = [iv for d in devices for iv in devs[d]["reset_intervals"]]
    all_delays = [x for d in devices for x in devs[d]["delays"]]
    installs_per_device = [len(devs[d]["install_cycles"]) for d in devices]

    # sincronização de reset: em qual fração de devices os resets caem nos MESMOS ciclos?
    from collections import Counter
    cyc_counter = Counter(c for c, _ in reset_events)
    max_sim = (max(cyc_counter.values()) / len(devices)) if cyc_counter else 0.0
    # índice de sincronização: desvio padrão dos intervalos por device (alto = heterogêneo)
    iv_std = st.pstdev(reset_intervals) if len(reset_intervals) > 1 else 0.0
    iv_mean = st.mean(reset_intervals) if reset_intervals else 0.0
    # distância entre resets no tempo (evitar clusters): stddev dos timestamps
    tstamps = [t for _, t in reset_events]
    t_std = st.pstdev(tstamps) if len(tstamps) > 1 else 0.0

    return {
        "devices": len(devices), "cycles": cycles,
        "virtual_time_s": round(clock.t, 1), "virtual_days": round(total_days, 3),
        "total_resets": total_resets, "reset_ops_per_cycle": round(total_resets / cycles, 3),
        "resets_per_device_avg": round(st.mean(per_device_resets), 2) if per_device_resets else 0,
        "reset_interval_mean": round(iv_mean, 2), "reset_interval_stddev": round(iv_std, 2),
        "reset_interval_in_2_30": bool(all(2 <= iv <= 30 for iv in reset_intervals)),
        "max_frac_devices_reset_same_cycle": round(max_sim, 3),
        "reset_timestamp_stddev_s": round(t_std, 1),
        "total_installs": total_installs, "install_ops_per_day": round(total_installs / total_days, 2),
        "installs_per_device_avg": round(st.mean(installs_per_device), 2) if installs_per_device else 0,
        "install_frac_of_population": round(100.0 * len([x for x in installs_per_device if x > 0]) / len(devices), 1),
        "delay_min": round(min(all_delays), 2) if all_delays else 0,
        "delay_max": round(max(all_delays), 2) if all_delays else 0,
        "delay_mean": round(st.mean(all_delays), 2) if all_delays else 0,
        "delay_stddev": round(st.pstdev(all_delays), 2) if len(all_delays) > 1 else 0,
        "coord": coord.snapshot(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", type=int, default=20)
    ap.add_argument("--cycles", type=int, default=100)
    ap.add_argument("--seed", default="123")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    devices = ["192.168.1.%d:5555" % (10 + i) for i in range(args.devices)]
    devs, rev, iev, clock, coord = run(devices, args.cycles, args.seed)
    rep = analyze(devices, devs, rev, iev, clock, coord, args.cycles)
    # distribuição por device (amostra)
    rep["per_device"] = {d: {"resets": len(devs[d]["reset_positions"]),
                             "reset_positions": devs[d]["reset_positions"],
                             "installs": len(devs[d]["install_cycles"])} for d in devices}
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print("=== QA SIMULATION devices=%d cycles=%d seed=%s ===" % (args.devices, args.cycles, args.seed))
        for k in ("virtual_days", "total_resets", "reset_ops_per_cycle", "resets_per_device_avg",
                  "reset_interval_mean", "reset_interval_stddev", "reset_interval_in_2_30",
                  "max_frac_devices_reset_same_cycle", "reset_timestamp_stddev_s",
                  "total_installs", "install_ops_per_day", "installs_per_device_avg",
                  "install_frac_of_population", "delay_min", "delay_mean", "delay_max", "delay_stddev"):
            print("%-34s %s" % (k, rep[k]))
        print("coord:", rep["coord"])
        print("resets/device:", [rep["per_device"][d]["resets"] for d in devices])
        print("installs/device:", [rep["per_device"][d]["installs"] for d in devices])


if __name__ == "__main__":
    main()
