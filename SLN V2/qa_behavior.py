#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qa_behavior.py — SLN V2: camada de SIMULAÇÃO QA / agendamento por device.

OBJETIVO (fora da state machine do anúncio): dar a cada device um "usuário" heterogêneo e
persistente — delays aleatórios por device, intervalos de reset individuais (2..30 ciclos),
quota populacional de reset, instalação com cooldown móvel de 24h — tudo reproduzível por seed
e persistente entre restarts (bot/watchdog/host).

NÃO toca em: reward gate, FORMAT, pill, Store/BACK, recovery, Fast Close, IA, persistent logcat,
adaptive tick, watchdog ownership.

Arquitetura:
    DeviceQABehaviorState      estado persistente por device (state/qa_devices/<safe>.json)
    QaConfig                   config validada (fallbacks seguros)
    QABehaviorScheduler        decide WAIT/RESET_DUE/RESET_ALLOWED/INSTALL_*/NORMAL_CYCLE
    QACoordinator (in-memory)  quota/concorrência/instalação da frota (testes/simulação)
    FileQACoordinator          idem, multi-processo (arquivo + lock), para o bot real
    build_scheduler(...)       monta o scheduler do device (None se desabilitado)

Randomização: `seed = sha256(global_seed + '|' + device_id)` quando `qa_random_seed` não é null;
caso contrário usa entropia do sistema. NUNCA um único sorteio global para todos os devices.
"""
import hashlib
import json
import os
import random
import tempfile
import time

# ---- estados de decisão ----
WAIT = "WAIT"
NORMAL_CYCLE = "NORMAL_CYCLE"
RESET_DUE = "RESET_DUE"
RESET_ALLOWED = "RESET_ALLOWED"
INSTALL_ELIGIBLE = "INSTALL_ELIGIBLE"
INSTALL_SELECTED = "INSTALL_SELECTED"

# ---- resultados terminais de sessão (functional) ----
COMPLETED = "COMPLETED"
FAILED = "FAILED"
ABORTED = "ABORTED"       # sessão sem resultado terminal (test end/watchdog/crash)


def should_count_cycle(session_started, terminal_result):
    """REGRA PURA de contabilidade: uma AD SESSION iniciada que atingiu resultado TERMINAL
    (COMPLETED ou FAILED) consome exatamente 1 ciclo. ABORTED / sem sessão NÃO conta."""
    return bool(session_started and terminal_result in (COMPLETED, FAILED))

STATE_SUBDIR = os.path.join("state", "qa_devices")
COORD_FILE = os.path.join("state", "qa_coord.json")
COORD_LOCK = os.path.join("state", "qa_coord.lock")


def safe_device_id(device_id):
    return str(device_id).replace(":", "_").replace(".", "_").replace("/", "_").replace("\\", "_")


def device_seed(global_seed, device_id):
    """Seed determinística por device (None => entropia do sistema)."""
    if global_seed is None or global_seed == "":
        return None
    s = "%s|%s" % (global_seed, device_id)
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:16], 16)


def make_rng(seed):
    """random.Random(seed) ou Random() sem seed (entropia)."""
    return random.Random(seed) if seed is not None else random.Random()


def _validate_range(lo, hi, dlo, dhi, log_fn=None, name=""):
    try:
        lo = float(lo); hi = float(hi)
        if lo > hi:
            raise ValueError("min>max")
        return lo, hi
    except Exception:
        if log_fn:
            log_fn("[QA][CONFIG] valor invalido em %s -> fallback [%s,%s]" % (name, dlo, dhi))
        return float(dlo), float(dhi)


class QaConfig:
    """Config QA validada com fallbacks seguros."""

    __slots__ = ("enabled", "delay_min_s", "delay_max_s", "reset_min_cycles", "reset_max_cycles",
                 "reset_pct_min", "reset_pct_max", "max_concurrent_resets",
                 "jitter_min_s", "jitter_max_s", "install_pct_min", "install_pct_max",
                 "install_cooldown_h", "seed", "state_dir", "reset_window_s", "install_window_s")

    def __init__(self, cfg, base_dir=".", log_fn=None):
        self.enabled = bool(cfg.get("qa_realistic_behavior_enabled", False))
        self.delay_min_s, self.delay_max_s = _validate_range(
            cfg.get("qa_action_delay_min_s", 10), cfg.get("qa_action_delay_max_s", 60), 10, 60,
            log_fn, "qa_action_delay")
        self.reset_min_cycles, self.reset_max_cycles = _validate_range(
            cfg.get("qa_reset_interval_min_cycles", 2), cfg.get("qa_reset_interval_max_cycles", 30),
            2, 30, log_fn, "qa_reset_interval")
        self.reset_pct_min, self.reset_pct_max = _validate_range(
            cfg.get("qa_reset_population_pct_min", 5), cfg.get("qa_reset_population_pct_max", 25),
            5, 25, log_fn, "qa_reset_population_pct")
        self.max_concurrent_resets = max(1, int(cfg.get("qa_max_concurrent_resets", 2) or 2))
        self.jitter_min_s, self.jitter_max_s = _validate_range(
            cfg.get("qa_reset_start_jitter_min_s", 0), cfg.get("qa_reset_start_jitter_max_s", 15),
            0, 15, log_fn, "qa_reset_start_jitter")
        self.install_pct_min, self.install_pct_max = _validate_range(
            cfg.get("qa_install_pct_min", 5), cfg.get("qa_install_pct_max", 10), 5, 10,
            log_fn, "qa_install_pct")
        self.install_cooldown_h = float(cfg.get("qa_install_cooldown_hours", 24) or 24)
        if self.install_cooldown_h <= 0:
            self.install_cooldown_h = 24.0
        seed = cfg.get("qa_random_seed", None)
        self.seed = None if (seed is None or seed == "") else str(seed)
        self.state_dir = base_dir  # base do projeto; state_path() adiciona STATE_SUBDIR
        self.reset_window_s = float(cfg.get("qa_reset_window_minutes", 30) or 30) * 60.0
        self.install_window_s = float(cfg.get("qa_install_window_hours", 24) or 24) * 3600.0


class DeviceQABehaviorState:
    """Estado QA persistente por device. Escrita atômica; tolerante a arquivo corrompido."""

    def __init__(self, device_id, seed=None):
        self.device_id = device_id
        self.seed = seed
        self.cycle_count = 0
        self.next_action_delay_s = 0.0
        self.reset_interval_cycles = 0
        self.cycles_until_reset = 0
        self.last_reset_at = None
        self.reset_count = 0
        self.reset_attempts = 0
        self.reset_success = 0
        self.reset_fail = 0
        # contabilidade de ciclos (functional vs counted)
        self.cycles_started = 0
        self.cycles_accounted = 0
        self.cycles_completed = 0
        self.cycles_failed = 0
        self.cycles_aborted = 0
        self.last_install_at = None
        self.install_count = 0
        self.install_eligible = False
        self.daily_install_day = None
        self.install_attempts = 0
        self.install_success = 0
        self.install_fail = 0
        self.delay_sum = 0.0
        self.delay_count = 0
        self.created_at = time.time()
        self.updated_at = self.created_at

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__dict__}

    @classmethod
    def from_dict(cls, d, device_id):
        st = cls(device_id)
        for k, v in (d or {}).items():
            if hasattr(st, k):
                setattr(st, k, v)
        st.device_id = device_id
        return st

    # ---- decisões ----
    def schedule_reset_interval(self, rng, lo, hi):
        self.reset_interval_cycles = rng.randint(int(lo), int(hi))
        self.cycles_until_reset = self.reset_interval_cycles
        return self.reset_interval_cycles

    def cycle_completed(self):
        self.cycle_count += 1
        if self.cycles_until_reset > 0:
            self.cycles_until_reset -= 1

    def reset_due(self):
        return self.cycles_until_reset <= 0

    def on_reset_result(self, success, now):
        self.reset_attempts += 1
        if success:
            self.reset_success += 1
            self.reset_count += 1
            self.last_reset_at = now
        else:
            self.reset_fail += 1

    def install_eligible_now(self, now, cooldown_h):
        if self.last_install_at is None:
            return True
        return (now - float(self.last_install_at)) >= float(cooldown_h) * 3600.0

    def record_delay(self, seconds):
        self.next_action_delay_s = float(seconds)
        self.delay_sum += float(seconds)
        self.delay_count += 1

    def on_install_result(self, success, now):
        self.install_attempts += 1
        if success:
            self.install_success += 1
            self.install_count += 1
            self.last_install_at = now
        else:
            self.install_fail += 1

    def avg_delay_s(self):
        return (self.delay_sum / self.delay_count) if self.delay_count else 0.0


# ---------------- persistência ----------------
def state_path(base_dir, device_id):
    return os.path.join(base_dir, STATE_SUBDIR, safe_device_id(device_id) + ".json")


def save_state(path, state):
    """Escrita ATÔMICA (tmp no mesmo diretório + os.replace). Nunca levanta exceção."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        state.updated_at = time.time()
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def load_state(path, device_id):
    """Carrega estado; arquivo ausente/corrompido => None (o chamador recria)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return None
        return DeviceQABehaviorState.from_dict(d, device_id)
    except Exception:
        return None


# ---------------- coordinator (frota) ----------------
class QACoordinator:
    """Coordenação de quota/concorrência/instalação da frota. EM MEMÓRIA (testes/simulação).

    Janelas por tempo (bucket): reset_window_s e install_window_s. Quota = round(pop*pct), nunca
    forçada acima do nº de elegíveis. Seleção aleatória aproximada pela ordem de chegada + jitter
    (o bot aplica qa_reset_start_jitter_s antes de pedir)."""

    def __init__(self, rng, reset_pct_lo, reset_pct_hi, max_concurrent_resets,
                 install_pct_lo, install_pct_hi, reset_window_s, install_window_s, now_fn=None):
        self.rng = rng
        self.rp_lo, self.rp_hi = reset_pct_lo, reset_pct_hi
        self.max_conc = max_concurrent_resets
        self.ip_lo, self.ip_hi = install_pct_lo, install_pct_hi
        self.reset_window_s = reset_window_s
        self.install_window_s = install_window_s
        self.now_fn = now_fn or time.time
        self._rw = None
        self._iw = None
        self.reset_quota = 0
        self.reset_granted = 0
        self.resets_in_flight = 0
        self.install_quota = 0
        self.install_granted = 0
        self.reset_pct_used = 0.0
        self.install_pct_used = 0.0

    def _win(self, now, size):
        return int(now // size)

    def _roll_reset(self, now):
        w = self._win(now, self.reset_window_s)
        if self._rw != w:
            self._rw = w
            self.reset_granted = 0
            self.resets_in_flight = 0
            self.reset_pct_used = self.rng.uniform(self.rp_lo, self.rp_hi)
            self.reset_quota = None  # definido na 1ª consulta com population

    def _roll_install(self, now):
        w = self._win(now, self.install_window_s)
        if self._iw != w:
            self._iw = w
            self.install_granted = 0
            self.install_pct_used = self.rng.uniform(self.ip_lo, self.ip_hi)
            self.install_quota = None

    def reset_quota_for(self, population, now):
        self._roll_reset(now)
        if self.reset_quota is None:
            self.reset_quota = int(round(int(population) * self.reset_pct_used / 100.0))
        return self.reset_quota

    def try_reset(self, device, population, now):
        """True se concedido (quota + concorrência). Nunca força quota acima dos devidos."""
        self._roll_reset(now)
        if self.reset_quota is None:
            self.reset_quota = int(round(int(population) * self.reset_pct_used / 100.0))
        if self.reset_granted >= self.reset_quota:
            return False
        if self.resets_in_flight >= self.max_conc:
            return False
        self.reset_granted += 1
        self.resets_in_flight += 1
        return True

    def reset_finished(self, device):
        if self.resets_in_flight > 0:
            self.resets_in_flight -= 1

    def install_quota_for(self, population, now):
        self._roll_install(now)
        if self.install_quota is None:
            self.install_quota = int(round(int(population) * self.install_pct_used / 100.0))
        return self.install_quota

    def try_install(self, device, population, now):
        self._roll_install(now)
        if self.install_quota is None:
            self.install_quota = int(round(int(population) * self.install_pct_used / 100.0))
        if self.install_granted >= self.install_quota:
            return False
        self.install_granted += 1
        return True

    def snapshot(self):
        return {"reset_window": self._rw, "reset_quota": self.reset_quota,
                "reset_granted": self.reset_granted, "reset_pct": round(self.reset_pct_used, 2),
                "resets_in_flight": self.resets_in_flight,
                "install_window": self._iw, "install_quota": self.install_quota,
                "install_granted": self.install_granted, "install_pct": round(self.install_pct_used, 2)}


class FileQACoordinator(QACoordinator):
    """Coordenação multi-processo via `state/qa_coord.json` + lock simples (arquivo O_EXCL)."""

    def __init__(self, base_dir, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.coord_file = os.path.join(base_dir, COORD_FILE)
        self.lock_file = os.path.join(base_dir, COORD_LOCK)
        os.makedirs(os.path.dirname(self.coord_file), exist_ok=True)

    def _lock(self, timeout=5.0):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.close(fd)
                return True
            except FileExistsError:
                if time.time() - t0 > timeout:
                    try:
                        os.remove(self.lock_file)  # lock velho
                    except Exception:
                        pass
                    t0 = time.time()
                time.sleep(0.05)
            except Exception:
                return False

    def _unlock(self):
        try:
            os.remove(self.lock_file)
        except Exception:
            pass

    def _load(self):
        try:
            with open(self.coord_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _store(self, d):
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.coord_file), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, self.coord_file)
        except Exception:
            pass

    def try_reset(self, device, population, now):
        if not self._lock():
            return False
        try:
            d = self._load()
            w = self._win(now, self.reset_window_s)
            if d.get("reset_window") != w:
                d = {"reset_window": w, "reset_quota": int(round(int(population) * self.rng.uniform(self.rp_lo, self.rp_hi) / 100.0)),
                     "reset_granted": 0, "resets_in_flight": 0,
                     "install_window": d.get("install_window"), "install_quota": d.get("install_quota", 0),
                     "install_granted": d.get("install_granted", 0), "reset_devices": []}
            if device in d.get("reset_devices", []):
                return False
            if d.get("reset_granted", 0) >= d.get("reset_quota", 0):
                return False
            if d.get("resets_in_flight", 0) >= self.max_conc:
                return False
            d["reset_granted"] = d.get("reset_granted", 0) + 1
            d["resets_in_flight"] = d.get("resets_in_flight", 0) + 1
            d.setdefault("reset_devices", []).append(device)
            self._store(d)
            return True
        finally:
            self._unlock()

    def reset_finished(self, device):
        if not self._lock():
            return
        try:
            d = self._load()
            if d.get("resets_in_flight", 0) > 0:
                d["resets_in_flight"] -= 1
            self._store(d)
        finally:
            self._unlock()

    def try_install(self, device, population, now):
        if not self._lock():
            return False
        try:
            d = self._load()
            w = self._win(now, self.install_window_s)
            if d.get("install_window") != w:
                d["install_window"] = w
                d["install_quota"] = int(round(int(population) * self.rng.uniform(self.ip_lo, self.ip_hi) / 100.0))
                d["install_granted"] = 0
                d["install_devices"] = []
            if device in d.get("install_devices", []):
                return False
            if d.get("install_granted", 0) >= d.get("install_quota", 0):
                return False
            d["install_granted"] = d.get("install_granted", 0) + 1
            d.setdefault("install_devices", []).append(device)
            self._store(d)
            return True
        finally:
            self._unlock()


# ---------------- scheduler por device ----------------
class QABehaviorScheduler:
    """Decide o que o device faz a cada ciclo. O main só executa a ação autorizada."""

    def __init__(self, device_id, state, rng, qcfg, coordinator, population_fn=None,
                 log_fn=None, now_fn=None, sleep_fn=None):
        self.device_id = device_id
        self.state = state
        self.rng = rng
        self.qcfg = qcfg
        self.coord = coordinator
        self.population_fn = population_fn or (lambda: 1)
        self.log = log_fn or (lambda *a, **k: None)
        self.now_fn = now_fn or time.time
        self.sleep_fn = sleep_fn or time.sleep
        self._accounted_ids = set()   # idempotência por cycle_id

    # ---- init ----
    def ensure_initialized(self):
        if self.state.reset_interval_cycles <= 0:
            iv = self.state.schedule_reset_interval(self.rng, self.qcfg.reset_min_cycles,
                                                    self.qcfg.reset_max_cycles)
            self.log("[QA][RESET_SCHEDULE] device=%s interval_cycles=%d remaining=%d" % (
                self.device_id, iv, self.state.cycles_until_reset))

    # ---- delay ----
    def draw_delay(self):
        d = self.rng.uniform(self.qcfg.delay_min_s, self.qcfg.delay_max_s)
        self.state.record_delay(d)
        self.log("[QA][DELAY] device=%s seconds=%.2f" % (self.device_id, d))
        return d

    def sleep_delay(self, stop_check=None, block=0.5):
        """Sleep CANCELÁVEL em blocos (não usar sleep gigante não interrompível)."""
        d = self.draw_delay()
        return self.sleep_seconds(d, stop_check=stop_check, block=block)

    def sleep_seconds(self, seconds, stop_check=None, block=0.5):
        end = self.now_fn() + float(seconds)
        while self.now_fn() < end:
            if stop_check is not None and stop_check():
                return False
            self.sleep_fn(min(block, max(0.0, end - self.now_fn())))
        return True

    # ---- reset ----
    def begin_cycle(self, stop_check=None):
        """Chamado ANTES de abrir o app. Aplica delay e decide reset.
        Retorna dict {action, delay_s, reset_interval_cycles, remaining}."""
        self.sleep_delay(stop_check=stop_check)
        if self.state.reset_due():
            self.log("[QA][RESET_DUE] device=%s remaining=%d" % (self.device_id, self.state.cycles_until_reset))
            # jitter para randomizar a ordem de chegada (evita sincronização)
            self.sleep_seconds(self.rng.uniform(self.qcfg.jitter_min_s, self.qcfg.jitter_max_s),
                               stop_check=stop_check)
            pop = int(self.population_fn() or 1)
            quota = self.coord.reset_quota_for(pop, self.now_fn())
            if self.coord.try_reset(self.device_id, pop, self.now_fn()):
                self.log("[QA][RESET_SELECTED] device=%s population_pct=%.1f quota=%d" % (
                    self.device_id, self.coord.reset_pct_used, quota))
                return {"action": RESET_ALLOWED, "delay_s": self.state.next_action_delay_s,
                        "remaining": self.state.cycles_until_reset}
            # não selecionado: permanece DUE
            return {"action": RESET_DUE, "delay_s": self.state.next_action_delay_s,
                    "remaining": self.state.cycles_until_reset}
        return {"action": NORMAL_CYCLE, "delay_s": self.state.next_action_delay_s,
                "remaining": self.state.cycles_until_reset}

    def on_reset_result(self, success):
        now = self.now_fn()
        self.state.on_reset_result(success, now)
        self.coord.reset_finished(self.device_id)
        if success:
            self.log("[QA][RESET_SUCCESS] device=%s count=%d" % (self.device_id, self.state.reset_success))
            iv = self.state.schedule_reset_interval(self.rng, self.qcfg.reset_min_cycles,
                                                    self.qcfg.reset_max_cycles)
            self.log("[QA][RESET_SCHEDULE] device=%s interval_cycles=%d remaining=%d" % (
                self.device_id, iv, self.state.cycles_until_reset))
        else:
            self.log("[QA][RESET_FAILED] device=%s fail=%d" % (self.device_id, self.state.reset_fail))

    def cycle_completed(self):
        # (legado) NÃO usar como contador genérico — ver record_cycle_result().
        self.state.cycle_completed()

    def mark_session_started(self):
        self.state.cycles_started += 1

    def record_cycle_result(self, session_started, terminal_result, cycle_id=None, reason=""):
        """Contabilidade: conta 1 ciclo se a sessão iniciou e atingiu resultado terminal
        (COMPLETED/FAILED). Idempotente por cycle_id. Retorna (counted, remaining)."""
        counted = should_count_cycle(session_started, terminal_result)
        if not counted:
            if terminal_result == ABORTED:
                self.state.cycles_aborted += 1
            self.log("[QA][CYCLE_RESULT] cycle_id=%s session_started=%s functional_result=%s counted=False reason=%s"
                     % (cycle_id, session_started, terminal_result, reason or terminal_result))
            return False, self.state.cycles_until_reset
        if cycle_id is not None and cycle_id in self._accounted_ids:
            self.log("[QA][CYCLE_RESULT] cycle_id=%s session_started=True functional_result=%s counted=False "
                     "reason=already_accounted" % (cycle_id, terminal_result))
            return False, self.state.cycles_until_reset
        if cycle_id is not None:
            self._accounted_ids.add(cycle_id)
        before = self.state.cycles_until_reset
        self.state.cycle_completed()          # decrementa (conta 1 ciclo)
        self.state.cycles_accounted += 1
        if terminal_result == COMPLETED:
            self.state.cycles_completed += 1
        elif terminal_result == FAILED:
            self.state.cycles_failed += 1
        self.log("[QA][CYCLE_RESULT] cycle_id=%s session_started=True functional_result=%s counted=True reason=%s"
                 % (cycle_id, terminal_result, reason or terminal_result))
        self.log("[QA][CYCLE_ACCOUNTED] cycle_id=%s result=%s cycles_until_reset_before=%d "
                 "cycles_until_reset_after=%d" % (cycle_id, terminal_result, before, self.state.cycles_until_reset))
        return True, self.state.cycles_until_reset

    # ---- install ----
    def install_decision(self):
        """True se ESTE device foi selecionado para instalar agora (quota + cooldown 24h)."""
        now = self.now_fn()
        if not self.state.install_eligible_now(now, self.qcfg.install_cooldown_h):
            return False
        self.state.install_eligible = True
        self.log("[QA][INSTALL_ELIGIBLE] device=%s" % self.device_id)
        pop = int(self.population_fn() or 1)
        if self.coord.try_install(self.device_id, pop, now):
            self.log("[QA][INSTALL_SELECTED] device=%s population_pct=%.1f" % (
                self.device_id, self.coord.install_pct_used))
            return True
        return False

    def on_install_result(self, success):
        now = self.now_fn()
        self.state.on_install_result(success, now)
        if success:
            self.log("[QA][INSTALL_SUCCESS] device=%s count=%d" % (self.device_id, self.state.install_success))
        else:
            self.log("[QA][INSTALL_FAILED] device=%s fail=%d" % (self.device_id, self.state.install_fail))

    # ---- resumo ----
    def summary_dict(self):
        s = self.state
        nxt = None
        if s.last_install_at is not None:
            nxt = s.last_install_at + self.qcfg.install_cooldown_h * 3600.0
        return {
            "device": self.device_id, "cycles": s.cycle_count, "avg_delay_s": round(s.avg_delay_s(), 2),
            "cycles_started": s.cycles_started, "cycles_accounted": s.cycles_accounted,
            "cycles_completed": s.cycles_completed, "cycles_failed": s.cycles_failed,
            "cycles_aborted": s.cycles_aborted,
            "reset_due": s.reset_due(), "reset_attempts": s.reset_attempts,
            "reset_success": s.reset_success, "reset_fail": s.reset_fail,
            "reset_interval_cycles": s.reset_interval_cycles, "cycles_until_reset": s.cycles_until_reset,
            "install_eligible": s.install_eligible, "install_attempts": s.install_attempts,
            "install_success": s.install_success, "install_fail": s.install_fail,
            "last_install_at": s.last_install_at, "next_install_eligible_at": nxt,
        }

    def emit_summary(self):
        self.log("[QA_SUMMARY] " + " ".join("%s=%s" % (k, v) for k, v in self.summary_dict().items()))

    def save(self):
        save_state(state_path(self.qcfg.state_dir, self.device_id), self.state)


def build_scheduler(device_id, cfg, base_dir=".", log_fn=None, population_fn=None,
                    coordinator=None, now_fn=None, sleep_fn=None):
    """Monta o scheduler do device (persistente). Retorna (scheduler, qcfg) ou (None, qcfg) se off.

    O estado é carregado de disco se existir (persiste entre restarts). A seed por device garante
    reprodutibilidade quando qa_random_seed está definido."""
    qcfg = QaConfig(cfg, base_dir=base_dir, log_fn=log_fn)
    if not qcfg.enabled:
        return None, qcfg
    p = state_path(qcfg.state_dir, device_id)
    st = load_state(p, device_id)
    seed = device_seed(qcfg.seed, device_id)
    if st is None:
        st = DeviceQABehaviorState(device_id, seed=seed)
    if seed is not None:
        st.seed = seed
    rng = make_rng(st.seed if st.seed is not None else seed)
    if coordinator is None:
        coordinator = FileQACoordinator(base_dir, rng, qcfg.reset_pct_min, qcfg.reset_pct_max,
                                        qcfg.max_concurrent_resets, qcfg.install_pct_min,
                                        qcfg.install_pct_max, qcfg.reset_window_s,
                                        qcfg.install_window_s, now_fn=now_fn)
    sched = QABehaviorScheduler(device_id, st, rng, qcfg, coordinator,
                                population_fn=population_fn, log_fn=log_fn, now_fn=now_fn,
                                sleep_fn=sleep_fn)
    return sched, qcfg
