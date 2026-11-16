#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_qa_behavior.py — SLN V2 (QA realistic behavior, OFFLINE)

Sem ADB, sem processos reais. Clock injetável. Cobre delay/reset/install/persistência.

Rodar:  python tests/test_qa_behavior.py
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import qa_behavior as QA

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


def base_cfg(**over):
    cfg = {"qa_realistic_behavior_enabled": True,
           "qa_action_delay_min_s": 10, "qa_action_delay_max_s": 60,
           "qa_reset_interval_min_cycles": 2, "qa_reset_interval_max_cycles": 30,
           "qa_reset_population_pct_min": 5, "qa_reset_population_pct_max": 25,
           "qa_max_concurrent_resets": 2,
           "qa_reset_start_jitter_min_s": 0, "qa_reset_start_jitter_max_s": 15,
           "qa_install_pct_min": 5, "qa_install_pct_max": 10,
           "qa_install_cooldown_hours": 24, "qa_random_seed": None}
    cfg.update(over)
    return cfg


def fast_clock(start=1_000_000.0):
    """Relógio virtual: now_fn avança via sleep_fn (para os sleeps não serem reais)."""
    c = {"t": float(start)}
    return (lambda: c["t"]), (lambda dt: c.__setitem__("t", c["t"] + dt)), c


# ---------------- delay ----------------
def test_delay(tmp):
    print("[delay] faixa + reprodutibilidade + distribuição por device")
    qcfg = QA.QaConfig(base_cfg(qa_random_seed=123), base_dir=tmp)
    scheds = {}
    for dev in ("D1", "D2", "D3"):
        seed = QA.device_seed("123", dev)
        rng = QA.make_rng(seed)
        st = QA.DeviceQABehaviorState(dev, seed)
        scheds[dev] = QA.QABehaviorScheduler(dev, st, rng, qcfg, QA.QACoordinator(rng, 5, 25, 2, 5, 10, 1800, 86400))
    seqs = {d: [s.rng.uniform(qcfg.delay_min_s, qcfg.delay_max_s) for _ in range(8)] for d, s in scheds.items()}
    allvals = [v for s in seqs.values() for v in s]
    check("delay sempre >= min", min(allvals) >= qcfg.delay_min_s)
    check("delay sempre <= max", max(allvals) <= qcfg.delay_max_s)
    check("devices diferentes -> sequencias diferentes", seqs["D1"] != seqs["D2"] and seqs["D2"] != seqs["D3"])
    # reprodutibilidade
    rngA = QA.make_rng(QA.device_seed("123", "D1"))
    rngB = QA.make_rng(QA.device_seed("123", "D1"))
    check("mesma seed/device reproduz", [round(rngA.uniform(10, 60), 6) for _ in range(5)] ==
          [round(rngB.uniform(10, 60), 6) for _ in range(5)])
    # seed diferente muda
    check("seed global diferente muda sequencia",
          [QA.make_rng(QA.device_seed("999", "D1")).uniform(10, 60) for _ in range(3)] !=
          [QA.make_rng(QA.device_seed("123", "D1")).uniform(10, 60) for _ in range(3)])


# ---------------- reset ----------------
def test_reset(tmp):
    print("[reset] intervalo 2..30 + contador + due + novo intervalo")
    qcfg = QA.QaConfig(base_cfg(qa_random_seed=7), base_dir=tmp)
    rng = QA.make_rng(QA.device_seed("7", "R1"))
    st = QA.DeviceQABehaviorState("R1", QA.device_seed("7", "R1"))
    now_fn, sleep_fn, _ = fast_clock()
    coord = QA.QACoordinator(rng, 5, 25, 2, 5, 10, 1800, 86400, now_fn=now_fn)
    s = QA.QABehaviorScheduler("R1", st, rng, qcfg, coord, population_fn=lambda: 10,
                               now_fn=now_fn, sleep_fn=sleep_fn)
    s.ensure_initialized()
    check("intervalo inicial em 2..30", 2 <= st.reset_interval_cycles <= 30)
    check("cycles_until_reset == intervalo", st.cycles_until_reset == st.reset_interval_cycles)
    n = st.cycles_until_reset
    for i in range(n):
        s.record_cycle_result(True, "COMPLETED", cycle_id=i + 1)
    check("decrementa ate due", st.reset_due() and st.cycles_until_reset <= 0)
    # reset concedido + sucesso -> novo intervalo 2..30
    plan = s.begin_cycle()
    check("due -> RESET_ALLOWED (quota ok)", plan["action"] == QA.RESET_ALLOWED)
    s.on_reset_result(True)
    check("apos sucesso: novo intervalo 2..30", 2 <= st.reset_interval_cycles <= 30)
    check("apos sucesso: remaining reinicia", st.cycles_until_reset == st.reset_interval_cycles)
    check("reset_success contado", st.reset_success == 1)


def test_reset_quota(tmp):
    print("[reset] quota nao forca + nao-selecionado permanece due + concorrencia")
    qcfg = QA.QaConfig(base_cfg(qa_random_seed=1, qa_max_concurrent_resets=1), base_dir=tmp)
    rng = QA.make_rng(1)
    now_fn, sleep_fn, _ = fast_clock()
    coord = QA.QACoordinator(rng, 5, 5, 1, 5, 10, 1800, 86400, now_fn=now_fn)  # pct fixo 5%
    # population 20 -> quota = round(20*0.05)=1
    pop = 20
    cn = []
    for i in range(10):
        dev = "Q%d" % i
        rngi = QA.make_rng(i)
        st = QA.DeviceQABehaviorState(dev, i)
        st.cycles_until_reset = 0
        s = QA.QABehaviorScheduler(dev, st, rngi, qcfg, coord, population_fn=lambda: pop,
                                   now_fn=now_fn, sleep_fn=sleep_fn)
        cn.append(s.begin_cycle())
    allowed = [c for c in cn if c["action"] == QA.RESET_ALLOWED]
    due = [c for c in cn if c["action"] == QA.RESET_DUE]
    check("quota limita (<=1)", len(allowed) <= 1)
    check("nao-forcado: muitos permanecem DUE", len(due) >= 1)
    # concorrencia: com 1 in-flight e max=1, novo pedido negado
    rng2 = QA.make_rng(2)
    coord2 = QA.QACoordinator(rng2, 5, 25, 1, 5, 10, 1800, 86400)
    check("1o reset concedido", coord2.try_reset("A", 100, 1000) is True)
    check("concorrencia bloqueia 2o", coord2.try_reset("B", 100, 1000) is False)
    coord2.reset_finished("A")
    check("apos finished libera", coord2.try_reset("B", 100, 1000) is True)


def test_reset_due_blocks(tmp):
    print("[reset] reset_due respeita bloqueio por quota (permanece due)")
    qcfg = QA.QaConfig(base_cfg(qa_random_seed=3), base_dir=tmp)
    rng = QA.make_rng(3)
    now_fn, sleep_fn, _ = fast_clock()
    coord = QA.QACoordinator(rng, 0, 0, 2, 5, 10, 1800, 86400, now_fn=now_fn)  # pct 0% -> quota 0
    st = QA.DeviceQABehaviorState("Z", 3)
    st.cycles_until_reset = 0
    s = QA.QABehaviorScheduler("Z", st, rng, qcfg, coord, population_fn=lambda: 50,
                               now_fn=now_fn, sleep_fn=sleep_fn)
    plan = s.begin_cycle()
    check("quota 0 -> RESET_DUE (nao executa)", plan["action"] == QA.RESET_DUE)
    check("continua due", st.reset_due())


# ---------------- install ----------------
def test_cycle_accounting(tmp):
    print("[accounting] regra pura + contabilidade + idempotencia")
    check("COMPLETED conta", QA.should_count_cycle(True, "COMPLETED") is True)
    check("FAILED conta", QA.should_count_cycle(True, "FAILED") is True)
    check("ABORTED nao conta", QA.should_count_cycle(True, "ABORTED") is False)
    check("sem sessao nao conta", QA.should_count_cycle(False, "COMPLETED") is False)
    qcfg = QA.QaConfig(base_cfg(qa_random_seed=1), base_dir=tmp)
    rng = QA.make_rng(1)
    coord = QA.QACoordinator(rng, 5, 25, 2, 5, 10, 1800, 86400)
    st = QA.DeviceQABehaviorState("AC1", 1)
    st.cycles_until_reset = 4
    st.reset_interval_cycles = 4
    s = QA.QABehaviorScheduler("AC1", st, rng, qcfg, coord, population_fn=lambda: 10,
                               sleep_fn=lambda sec: None)
    # sequencia obrigatoria: CLOSED, TIMEOUT, LEFT_AD, CLOSED -> 4 -> 3 -> 2 -> 1 -> 0
    s.record_cycle_result(True, "COMPLETED", 1)
    check("CLOSED -> 3", st.cycles_until_reset == 3)
    s.record_cycle_result(True, "FAILED", 2)
    check("FAILED -> 2", st.cycles_until_reset == 2)
    s.record_cycle_result(True, "FAILED", 3)
    check("LEFT_AD(FAILED) -> 1", st.cycles_until_reset == 1)
    s.record_cycle_result(True, "COMPLETED", 4)
    check("CLOSED -> 0 (RESET_DUE)", st.reset_due() and st.cycles_until_reset == 0)
    check("contadores 2 completed / 2 failed", st.cycles_completed == 2 and st.cycles_failed == 2)
    # idempotencia
    before = st.cycles_until_reset
    s.record_cycle_result(True, "FAILED", 2)   # mesmo cycle_id -> nao conta de novo
    check("mesmo cycle_id nao decrementa 2x", st.cycles_until_reset == before)
    # ABORTED nao conta
    st.cycles_until_reset = 5
    s.record_cycle_result(True, "ABORTED", 99)
    check("ABORTED nao decrementa", st.cycles_until_reset == 5 and st.cycles_aborted >= 1)
    # sem sessao nao conta
    s.record_cycle_result(False, "COMPLETED", 100)
    check("sem sessao nao decrementa", st.cycles_until_reset == 5)


def test_install(tmp):
    print("[install] pct 5..10 + cooldown 24h + falha nao marca sucesso")
    # cooldown com clock injetável
    clock = {"t": 1_000_000.0}
    qcfg = QA.QaConfig(base_cfg(qa_random_seed=5), base_dir=tmp)
    rng = QA.make_rng(5)
    coord = QA.QACoordinator(rng, 5, 25, 2, 5, 10, 1800, 86400, now_fn=lambda: clock["t"])
    st = QA.DeviceQABehaviorState("I1", 5)
    s = QA.QABehaviorScheduler("I1", st, rng, qcfg, coord, population_fn=lambda: 50,
                               now_fn=lambda: clock["t"])
    check("elegivel na 1a vez", st.install_eligible_now(clock["t"], 24))
    # seleciona e sucesso
    check("install selecionado (quota)", s.install_decision() is True)
    s.on_install_result(True)
    check("install_success contado", st.install_success == 1)
    check("last_install_at gravado", st.last_install_at == clock["t"])
    # tentar de novo <24h -> NUNCA
    clock["t"] += 3600  # +1h
    check("cooldown bloqueia 2a instalacao (<24h)", s.install_decision() is False)
    clock["t"] += 24 * 3600  # +24h -> elegivel
    check("apos 24h volta elegivel", st.install_eligible_now(clock["t"], 24) is True)
    # falha nao marca sucesso
    st2 = QA.DeviceQABehaviorState("I2", 5)
    st2.on_install_result(False, clock["t"])
    check("falha nao atualiza last_install_at", st2.last_install_at is None)
    check("falha incrementa install_fail", st2.install_fail == 1 and st2.install_success == 0)
    # quota 5..10%: population 50 -> 3..5
    rng3 = QA.make_rng(11)
    coord3 = QA.QACoordinator(rng3, 5, 25, 2, 5, 10, 1800, 86400)
    q = coord3.install_quota_for(50, clock["t"])
    check("quota install em 5..10% de 50 (3..5)", q in (3, 4, 5))


# ---------------- persistência ----------------
def test_persistence(tmp):
    print("[persistence] save/load + corrompido + incompleto + atômico")
    d = os.path.join(tmp, "persist")
    os.makedirs(d, exist_ok=True)
    p = QA.state_path(d, "P:1")
    st = QA.DeviceQABehaviorState("P:1", 42)
    st.cycles_until_reset = 3
    st.last_install_at = 123456.0
    st.cycle_count = 9
    check("save ok", QA.save_state(p, st) is True)
    st2 = QA.load_state(p, "P:1")
    check("load preserva cycles_until_reset=3 (restart)", st2 and st2.cycles_until_reset == 3)
    check("load preserva last_install_at", st2 and st2.last_install_at == 123456.0)
    check("load preserva cycle_count", st2 and st2.cycle_count == 9)
    # arquivo corrompido
    with open(p, "w", encoding="utf-8") as f:
        f.write("{nao eh json")
    check("json corrompido -> None", QA.load_state(p, "P:1") is None)
    # arquivo incompleto (dict sem algumas chaves)
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"device_id": "P:1", "cycle_count": 2}')
    st3 = QA.load_state(p, "P:1")
    check("arquivo incompleto carrega defaults", st3 and st3.cycle_count == 2 and st3.cycles_until_reset == 0)
    # ausente
    check("ausente -> None", QA.load_state(os.path.join(d, "nope.json"), "X") is None)


def test_watchdog_restart_persistence(tmp):
    print("[persistence] restart do watchdog continua cycles_until_reset e last_install_at")
    d = os.path.join(tmp, "wd")
    os.makedirs(d, exist_ok=True)
    qcfg = QA.QaConfig(base_cfg(qa_random_seed=1), base_dir=d)
    rng = QA.make_rng(1)
    coord = QA.QACoordinator(rng, 5, 25, 2, 5, 10, 1800, 86400)
    st = QA.DeviceQABehaviorState("W1", 1)
    st.cycles_until_reset = 3
    st.last_install_at = 777.0
    s = QA.QABehaviorScheduler("W1", st, rng, qcfg, coord, population_fn=lambda: 10)
    s.save()
    # "restart": recarrega do disco
    st_loaded = QA.load_state(QA.state_path(d, "W1"), "W1")
    check("cycles_until_reset continua 3", st_loaded.cycles_until_reset == 3)
    check("last_install_at continua", st_loaded.last_install_at == 777.0)


def test_config_validation(tmp):
    print("[config] min<=max + fallback seguro")
    bad = base_cfg(qa_action_delay_min_s=90, qa_action_delay_max_s=10)
    qcfg = QA.QaConfig(bad, base_dir=tmp)
    check("min>max -> fallback [10,60]", qcfg.delay_min_s == 10 and qcfg.delay_max_s == 60)
    qcfg2 = QA.QaConfig(base_cfg(), base_dir=tmp)
    check("defaults validos", qcfg2.delay_min_s == 10 and qcfg2.delay_max_s == 60)


def test_seed_null(tmp):
    print("[random] seed null -> entropia (devices diferentes)")
    qcfg = QA.QaConfig(base_cfg(qa_random_seed=None), base_dir=tmp)
    check("device_seed(None) -> None", QA.device_seed(None, "D1") is None)
    a = QA.make_rng(None).uniform(0, 1)
    b = QA.make_rng(None).uniform(0, 1)
    check("sem seed: dois rng diferem (quase certamente)", a != b)


def main():
    tmp = os.path.join(tempfile.gettempdir(), "sln_qa_test")
    os.makedirs(tmp, exist_ok=True)
    test_delay(tmp)
    test_reset(tmp)
    test_cycle_accounting(tmp)
    test_reset_quota(tmp)
    test_reset_due_blocks(tmp)
    test_install(tmp)
    test_persistence(tmp)
    test_watchdog_restart_persistence(tmp)
    test_config_validation(tmp)
    test_seed_null(tmp)
    print("\nRESULT: PASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
