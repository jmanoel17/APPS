#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
persistent_logcat.py — SLN V2 (FASE 3/4, atrás de `persistent_logcat_enabled`, default FALSE)

Leitor de logcat contínuo por device: 1 subprocesso `adb logcat` (stream) por bot, filtrando as
tags relevantes. O watcher consulta MEMÓRIA (ring buffer), sem subprocesso por lookup.

- Acompanha o PID atual do app (`pidof`) para telemetria/associação; stream é contínuo (não deixa
  órfãos).
- EVENT FLOOR: a sessão informa um piso (perf_counter no início do anúncio); eventos recebidos
  ANTES do piso são ignorados — preserva a correção contra o SHOWED do anúncio anterior.
- Se o reader falhar, `ready()` fica False e o bot degrada para o método atual (snapshot) ou
  visual fallback, sem travar.

Partes PURAS (parse_event, EventBuffer) são testáveis offline sem device.
"""
import re
import subprocess
import threading
import time

_APP_SHOWED = re.compile(
    r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+\d+\s+\w\s+AdHelperLog\s*:\s*FORMAT=(\w+)\s+EVENT=SHOWED")
_CAS_IMPR = re.compile(
    r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+\d+\s+\w\s+CAS\.AI\s*:\s*(Interstitial|Rewarded) Ad Impression (\w+)"
    r".*?CreativeId: ([^ |]+).*?UnitId: ([^ |]+)")
_EVENTS = {
    "CLICKED": re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+\d+\s+\w\s+AdHelperLog\s*:\s*FORMAT=(\w+)\s+EVENT=CLICKED"),
    "REWARD_EARNED": re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+\d+\s+\w\s+AdHelperLog\s*:\s*FORMAT=(\w+)\s+EVENT=REWARD_EARNED"),
    "DISMISSED": re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+\d+\s+\w\s+AdHelperLog\s*:\s*FORMAT=(\w+)\s+EVENT=DISMISSED"),
}


def parse_event(line, recv_perf):
    """Parser puro: 1 linha -> dict de evento ou None. `recv_perf` = perf_counter do host ao receber."""
    m = _APP_SHOWED.match(line)
    if m:
        return {"kind": "app_showed", "device_ts": m.group(1), "pid": m.group(2),
                "format": m.group(3).lower(), "recv_perf": recv_perf}
    m = _CAS_IMPR.match(line)
    if m:
        return {"kind": "cas_impression", "device_ts": m.group(1), "pid": m.group(2),
                "format": m.group(3).lower(), "network": m.group(4),
                "creative_id": m.group(5), "unit_id": m.group(6), "recv_perf": recv_perf}
    for ev, rx in _EVENTS.items():
        m = rx.match(line)
        if m:
            return {"kind": ev.lower(), "device_ts": m.group(1), "pid": m.group(2),
                    "format": m.group(3).lower(), "recv_perf": recv_perf}
    return None


class EventBuffer:
    """Ring buffer de eventos + seleção de formato com piso de sessão. Puro/testável."""

    def __init__(self, maxlen=200):
        self.maxlen = maxlen
        self.events = []
        self.received = 0
        self.dropped = 0

    def add(self, ev):
        if ev is None:
            return
        self.events.append(ev)
        self.received += 1
        if len(self.events) > self.maxlen:
            cutoff = len(self.events) - self.maxlen
            self.events = self.events[cutoff:]
            self.dropped += cutoff

    def _last_valid(self, kind, floor_perf, now_perf, max_age_s):
        best = None
        for ev in self.events:
            if ev["kind"] != kind:
                continue
            if floor_perf is not None and ev["recv_perf"] < floor_perf:
                continue
            if not (0 <= (now_perf - ev["recv_perf"]) <= max_age_s):
                continue
            best = ev
        return best

    def select_format(self, floor_perf, now_perf, max_age_s):
        """app_event vence cas_event; ambos precisam respeitar piso e janela de idade (host perf)."""
        app = self._last_valid("app_showed", floor_perf, now_perf, max_age_s)
        cas = self._last_valid("cas_impression", floor_perf, now_perf, max_age_s)
        res = None
        if app is not None:
            res = dict(app, source="app_event")
        elif cas is not None:
            res = dict(cas, source="cas_event")
        if res is not None and cas is not None and cas["format"] == res["format"]:
            res.update({k: cas[k] for k in ("network", "creative_id", "unit_id") if k in cas})
        return res

    def last_event(self, kind):
        for ev in reversed(self.events):
            if ev["kind"] == kind:
                return ev
        return None


class LogcatReader:
    """Reader contínuo (thread + subprocesso `adb logcat`). Não bloqueia o watcher."""

    def __init__(self, serial, package="com.flixdigital.dramaclassic", buffer=None,
                 pid_check_s=3.0):
        self.serial = serial
        self.package = package
        self.buffer = buffer or EventBuffer()
        self.pid_check_s = pid_check_s
        self.proc = None
        self._thread = None
        self._pid_thread = None
        self._stop = threading.Event()
        self.ready = False
        self.app_pid = None
        self.restarts = 0
        self.errors = 0

    # ---- interno ----
    def _spawn(self):
        try:
            self.proc = subprocess.Popen(
                ["adb", "-s", self.serial, "logcat", "-v", "threadtime",
                 "-s", "AdHelperLog:D", "CAS.AI:I"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="ignore", bufsize=1)
            self.ready = True
            return True
        except Exception:
            self.proc = None
            self.ready = False
            self.errors += 1
            return False

    def _reader_loop(self):
        backoff = 1.0
        while not self._stop.is_set():
            if self.proc is None:
                if not self._spawn():
                    time.sleep(backoff); backoff = min(backoff * 2, 10.0); continue
                self.restarts += 1
                backoff = 1.0
            try:
                line = self.proc.stdout.readline()
            except Exception:
                line = ""
            if not line:
                # stream morreu (device/processo) -> reinicia
                self.ready = False
                try: self.proc.terminate()
                except Exception: pass
                self.proc = None
                time.sleep(1.0)
                continue
            self.buffer.add(parse_event(line, time.perf_counter()))

    def _pid_loop(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run(["adb", "-s", self.serial, "shell", "pidof", self.package],
                                     capture_output=True, text=True, timeout=4).stdout.strip()
                m = re.match(r"\d+", out)
                self.app_pid = m.group(0) if m else None
            except Exception:
                self.errors += 1
            time.sleep(self.pid_check_s)

    # ---- API ----
    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, name="logcat-reader", daemon=True)
        self._thread.start()
        self._pid_thread = threading.Thread(target=self._pid_loop, name="logcat-pid", daemon=True)
        self._pid_thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass
        self.proc = None
        self.ready = False

    def format_from_memory(self, floor_perf, max_age_s):
        """Consulta em memória (sem subprocesso). Retorna dict de formato ou None."""
        return self.buffer.select_format(floor_perf, time.perf_counter(), max_age_s)

    def metrics(self):
        return {
            "logcat_reader_restarts": self.restarts,
            "logcat_events_received": self.buffer.received,
            "logcat_buffer_len": len(self.buffer.events),
            "logcat_buffer_dropped": self.buffer.dropped,
            "logcat_errors": self.errors,
            "logcat_app_pid": self.app_pid,
        }
