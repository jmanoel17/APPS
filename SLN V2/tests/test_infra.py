#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes unitários das correções de infra/medição (FASE 11).

Rodar:
    python -m unittest tests.test_infra -v
    (ou) python tests/test_infra.py
"""
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

import config_sln_v2 as CONFIG
import benchmark_host


def _clear_env():
    for k in ("SLN_CONFIG_FILE", "SLN_CONFIG_OVERLAY"):
        os.environ.pop(k, None)


class TestConfigLayering(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_overlay_preserves_dev_download_pct(self):
        os.environ["SLN_CONFIG_OVERLAY"] = "config_benchmark_sln_v2.json"
        cfg = CONFIG.load_config()
        self.assertEqual(cfg["download_apps_pct"], 5)      # preserva dev (não vaza 25)
        self.assertEqual(cfg["ia_url"], "http://127.0.0.1:8000")
        self.assertIs(cfg["fast_close_log_every_tick"], False)
        self.assertEqual(cfg["rewarded_no_countdown_diag_timeout_s"], 0)

    def test_dev_base_unchanged(self):
        cfg = CONFIG.load_config()
        self.assertEqual(cfg["download_apps_pct"], 5)
        self.assertEqual(cfg["ia_url"], "http://localhost:8000")
        self.assertIs(cfg["fast_close_log_every_tick"], True)

    def test_sln_config_file_replaces_base(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            f.write('{"download_apps_pct": 99}')
            path = f.name
        try:
            os.environ["SLN_CONFIG_FILE"] = path
            cfg = CONFIG.load_config()
            self.assertEqual(cfg["download_apps_pct"], 99)
        finally:
            os.unlink(path)

    def test_overlay_nonexistent_is_ignored(self):
        os.environ["SLN_CONFIG_OVERLAY"] = "nao_existe.json"
        cfg = CONFIG.load_config()
        self.assertEqual(cfg["download_apps_pct"], 5)      # volta ao dev


class _FakeProc:
    def __init__(self, cmdline, rss_mb=10):
        self._rss_mb = rss_mb
        self.info = {"cmdline": cmdline}   # em psutil, .info é atributo (dict), não método

    def memory_info(self):
        class _M:
            rss = self._rss_mb * 1024 * 1024
        return _M()


class TestBenchmarkHostMatcher(unittest.TestCase):
    def test_bot_count_exact_basename(self):
        procs = [
            _FakeProc(["python", "main_sln_v2.py", "1.2.3.4:5555", "0"]),
            _FakeProc(["python", "main_sln_v2_backup.py", "x", "0"]),   # NÃO deve contar
            _FakeProc(["python", "C:\\x\\main_sln_v2.py", "1.2.3.5:5555", "0"]),
        ]
        orig = benchmark_host.psutil.process_iter
        benchmark_host.psutil.process_iter = lambda attrs=None: iter(procs)
        try:
            out = benchmark_host.sample()
        finally:
            benchmark_host.psutil.process_iter = orig
        self.assertEqual(out["bot_process_count"], 2)

    def test_ai_rss_exact_basename(self):
        procs = [
            _FakeProc(["python", "aiMain_sln_v2.py"], rss_mb=17),
            _FakeProc(["python", "not_aiMain_sln_v2.py"], rss_mb=99),   # NÃO deve casar
        ]
        orig = benchmark_host.psutil.process_iter
        benchmark_host.psutil.process_iter = lambda attrs=None: iter(procs)
        try:
            out = benchmark_host.sample()
        finally:
            benchmark_host.psutil.process_iter = orig
        self.assertEqual(out["ai_process_rss_mb"], 17)


if __name__ == "__main__":
    unittest.main()
