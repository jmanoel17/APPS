#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_ownership.py — SLN V2 (FASE 18 — OWNERSHIP/RESTART, OFFLINE)

Testes SEM processos reais: um psutil FAKE é injetado em sln_proc. Cobre a invariante
"UM DEVICE = NO MÁXIMO UM BOT" e a transação de restart.

Rodar:  python tests/test_ownership.py
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import sln_proc as SP
import watchdog as WD

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


# ---------------- psutil FAKE ----------------
class FakeProc:
    def __init__(self, mgr, pid, cmdline, ppid=0, mode="ok"):
        self.mgr = mgr
        self.pid = pid
        self._cmdline = list(cmdline)
        self.ppid = ppid
        self.mode = mode  # ok | refuse_terminate | unkillable
        self.info = {"pid": pid, "name": "python.exe", "cmdline": list(cmdline), "ppid": ppid}

    def cmdline(self):
        return list(self._cmdline)

    def children(self, recursive=False):
        out = [p for p in list(self.mgr.procs.values()) if p.ppid == self.pid]
        if recursive:
            for c in list(out):
                out += c.children(True)
        return out

    def terminate(self):
        if self.mode == "ok":
            self.mgr.kill(self.pid)

    def kill(self):
        if self.mode != "unkillable":
            self.mgr.kill(self.pid)


class FakePsutil:
    def __init__(self):
        self.procs = {}
        self._next = 1000

    def add(self, cmdline, ppid=0, mode="ok", pid=None):
        pid = pid or self._next
        self._next = max(self._next, pid) + 1
        self.procs[pid] = FakeProc(self, pid, cmdline, ppid, mode)
        return pid

    def kill(self, pid):
        self.procs.pop(pid, None)

    def process_iter(self, attrs=None):
        return list(self.procs.values())

    def Process(self, pid):
        if pid not in self.procs:
            raise Exception("no such process %s" % pid)
        return self.procs[pid]

    def wait_procs(self, procs, timeout=None):
        gone = [p for p in procs if p.pid not in self.procs]
        alive = [p for p in procs if p.pid in self.procs]
        return gone, alive


def bot_cmd(dev):
    return [r"C:\py\python.exe", r"C:\apps\SLN V2\main_sln_v2.py", dev, "0", "0"]


def logcat_cmd(dev):
    return ["adb", "-s", dev, "logcat", "-v", "threadtime", "-s", "AdHelperLog:D"]


def with_fake(fn):
    real = SP.psutil
    SP.psutil = FakePsutil()
    try:
        return fn(SP.psutil)
    finally:
        SP.psutil = real


# ---------------- casos ----------------
def test_exact_matching():
    print("[ownership] exact device matching")
    def run(ps):
        a = ps.add(bot_cmd("192.168.1.1:5555"))
        ps.add(bot_cmd("192.168.1.11:5555"))
        ps.add(bot_cmd("192.168.1.1:5555"))      # duplicado exato
        ps.add([r"C:\py\python.exe", r"C:\x\other.py", "192.168.1.1:5555"])  # script errado
        got = SP.bot_processes_for_device("192.168.1.1:5555")
        check("2 bots exatos do mesmo device", len(got) == 2)
        check("device vizinho (11) nao casa com 1", len(SP.bot_processes_for_device("192.168.1.11:5555")) == 1)
        check("script diferente excluido", all(ps.procs[p]._cmdline[1].endswith("main_sln_v2.py") for p in got))
        check("device sem bot -> []", SP.bot_processes_for_device("10.0.0.9:5555") == [])
    with_fake(run)


def test_spawn_permission():
    print("[ownership] spawn: 0 -> ok, 1 -> negado, 2 -> duplicate")
    import watchdog_run as WR
    def run(ps):
        ps.add(bot_cmd("D1:5555"))
        ps.add(bot_cmd("D2:5555"))
        ps.add(bot_cmd("D2:5555"))
        find = SP.bot_processes_for_device
        check("0 workers -> spawn permitido", len(find("D0:5555")) == 0)
        check("1 worker -> spawn negado", len(find("D1:5555")) == 1)
        check("2 workers -> duplicate detectado", WR.duplicate_devices(["D2:5555"], find=find) == ["D2:5555"])
        dup, orph = WR.preflight(["D1:5555", "D2:5555"], find=find, expected_pids=[])
        check("preflight reporta duplicate", dup == ["D2:5555"])
    with_fake(run)


def test_terminate_and_exit():
    print("[ownership] terminate + exit")
    def run(ps):
        bot = ps.add(bot_cmd("D3:5555"))
        res = SP.terminate_worker_verified("D3:5555", timeout=1)
        check("ok=True", res["ok"] is True)
        check("zero bots apos terminate", SP.bot_processes_for_device("D3:5555") == [])
    with_fake(run)


def test_terminate_fail_force_kill():
    print("[ownership] terminate falha -> force kill")
    def run(ps):
        ps.add(bot_cmd("D4:5555"), mode="refuse_terminate")  # terminate nao mata; kill mata
        res = SP.terminate_worker_verified("D4:5555", timeout=1)
        check("force kill limpou", res["ok"] is True and SP.bot_processes_for_device("D4:5555") == [])
    with_fake(run)


def test_old_process_alive_no_spawn():
    print("[ownership] processo antigo resiste -> NAO spawnar")
    def run(ps):
        ps.add(bot_cmd("D5:5555"), mode="unkillable")
        res = SP.terminate_worker_verified("D5:5555", timeout=1)
        check("ok=False (ainda vivo)", res["ok"] is False)
        check("restart deve abortar (leftover!=[])", SP.bot_processes_for_device("D5:5555") != [])
    with_fake(run)


def test_child_logcat_cleanup():
    print("[ownership] child logcat cleanup")
    def run(ps):
        bot = ps.add(bot_cmd("D6:5555"))
        ps.add(logcat_cmd("D6:5555"), ppid=bot)
        check("child logcat visivel", len(SP.child_logcat_pid(bot)) == 1)
        ps.kill(bot)  # bot morre SEM limpar o filho -> orfao
        check("logcat orfao apos bot morto", len(SP.logcat_processes_for_device("D6:5555")) == 1)
        res = SP.terminate_worker_verified("D6:5555", timeout=1)  # zera ambos
        check("terminate zera logcat orfao", res["ok"] is True and SP.logcat_processes_for_device("D6:5555") == [])
    with_fake(run)


def test_orphan_detection():
    print("[ownership] orphan detection")
    def run(ps):
        p1 = ps.add(bot_cmd("D7:5555"))
        p2 = ps.add(bot_cmd("D8:5555"))   # fora do registry
        check("orphan encontrado", SP.orphans([p1]) == [p2])
        check("sem orphan quando registry completo", SP.orphans([p1, p2]) == [])
    with_fake(run)


def test_generation_ignored():
    print("[watchdog] heartbeat generation antiga ignorada")
    tmp = os.path.join(tempfile.gettempdir(), "sln_own_test")
    os.makedirs(tmp, exist_ok=True)
    dev = "D9:5555"
    p = WD.heartbeat_path(tmp, dev)
    sup = WD.Supervisor(tmp, stale_s=120, restart_max=3, restart_fn=lambda d: None,
                        now_fn=lambda: 1000.0, log_fn=lambda *a: None)
    sup.register(dev, pid=1, instance_id="aaa", generation=2)
    WD.write_heartbeat(p, {"device": dev, "generation": 1, "state": "RUNNING",
                           "last_progress": 1000.0, "mono_ts": 1000.0})
    check("hb gen=1 ignorado (esperado gen=2)",
          sup.classify(dev, WD.read_heartbeat(p), 1000.0) == WD.STALE_HEARTBEAT_GENERATION)
    WD.write_heartbeat(p, {"device": dev, "generation": 2, "instance_id": "bbb", "state": "RUNNING",
                           "last_progress": 1000.0, "mono_ts": 1000.0})
    check("hb gen=2 aceito -> RUNNING", sup.classify(dev, WD.read_heartbeat(p), 1000.0) == WD.RUNNING)


def test_quarantine_no_relaunch():
    print("[watchdog] quarantine nao relanca")
    tmp = os.path.join(tempfile.gettempdir(), "sln_own_test2")
    os.makedirs(tmp, exist_ok=True)
    dev = "DA:5555"
    p = WD.heartbeat_path(tmp, dev)
    calls = []
    now = {"t": 5000.0}
    sup = WD.Supervisor(tmp, stale_s=10, restart_max=1, restart_fn=lambda d: calls.append(d),
                        now_fn=lambda: now["t"], log_fn=lambda *a: None)
    WD.write_heartbeat(p, {"device": dev, "state": "RUNNING", "last_progress": 1000.0, "mono_ts": 1000.0})
    sup.poll([dev])                 # restart 1
    sup.poll([dev])                 # >max -> quarantine
    before = len(calls)
    sup.poll([dev]); sup.poll([dev])  # nao deve chamar de novo
    check("nao relanca apos quarantine", len(calls) == before and sup.state[dev] == WD.QUARANTINED)


def test_concurrent_restart_coalesced():
    print("[watchdog] restart concorrente coalescido (1 spawn)")
    tmp = os.path.join(tempfile.gettempdir(), "sln_own_test3")
    os.makedirs(tmp, exist_ok=True)
    dev = "DB:5555"
    p = WD.heartbeat_path(tmp, dev)
    WD.write_heartbeat(p, {"device": dev, "state": "RUNNING", "last_progress": 1000.0, "mono_ts": 1000.0})
    spawns = []
    reentered = {"n": 0}
    now = {"t": 5000.0}

    def restart(d):
        spawns.append(d)
        # se o supervisor tentasse reentrar durante o restart, _restarting impediria outro spawn
        sup.poll([dev])
    sup = WD.Supervisor(tmp, stale_s=10, restart_max=9, restart_fn=restart,
                        now_fn=lambda: now["t"], log_fn=lambda *a: None)
    sup.poll([dev])
    check("exatamente 1 spawn apesar de reentrada", len(spawns) == 1)


def test_startup_grace():
    print("[watchdog] grace de startup evita falso stale")
    tmp = os.path.join(tempfile.gettempdir(), "sln_own_test4")
    os.makedirs(tmp, exist_ok=True)
    calls = []
    sup = WD.Supervisor(tmp, stale_s=10, restart_max=3, restart_fn=lambda d: calls.append(d),
                        now_fn=lambda: 1000.0, log_fn=lambda *a: None, startup_grace_s=300.0)
    sup.poll(["DC:5555"])  # sem heartbeat, mas dentro do grace
    check("nao age no grace inicial", calls == [] and len(sup.registry) == 0)


def main():
    test_exact_matching()
    test_spawn_permission()
    test_terminate_and_exit()
    test_terminate_fail_force_kill()
    test_old_process_alive_no_spawn()
    test_child_logcat_cleanup()
    test_orphan_detection()
    test_generation_ignored()
    test_quarantine_no_relaunch()
    test_concurrent_restart_coalesced()
    test_startup_grace()
    print("\nRESULT: PASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
