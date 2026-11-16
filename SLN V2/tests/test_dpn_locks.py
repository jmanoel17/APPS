#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etapa A — InterProcessFileLock: exclusão mútua REAL entre processos."""
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn.locks import InterProcessFileLock


def _inc_worker(args):
    counter_path, lock_path, iters = args
    for _ in range(iters):
        with InterProcessFileLock(lock_path, timeout=60.0):
            try:
                with open(counter_path, "r", encoding="utf-8") as f:
                    n = json.load(f).get("n", 0)
            except Exception:
                n = 0
            n += 1
            time.sleep(0.0003)  # alarga a janela crítica
            with open(counter_path, "w", encoding="utf-8") as f:
                json.dump({"n": n}, f)


class TestLock(unittest.TestCase):
    def test_mutual_exclusion_multiprocess(self):
        with tempfile.TemporaryDirectory() as d:
            counter = os.path.join(d, "counter.json")
            with open(counter, "w", encoding="utf-8") as f:
                json.dump({"n": 0}, f)
            lockp = os.path.join(d, "test.lock")
            nproc, iters = 4, 50
            ctx = mp.get_context("spawn")
            ps = [ctx.Process(target=_inc_worker, args=((counter, lockp, iters),)) for _ in range(nproc)]
            for p in ps:
                p.start()
            for p in ps:
                p.join(60)
            with open(counter, "r", encoding="utf-8") as f:
                final = json.load(f)["n"]
            # sem exclusão real, o valor seria < nproc*iters
            self.assertEqual(final, nproc * iters)

    def test_timeout_when_held(self):
        with tempfile.TemporaryDirectory() as d:
            lockp = os.path.join(d, "x.lock")
            a = InterProcessFileLock(lockp, timeout=1.0)
            self.assertTrue(a.acquire())
            b = InterProcessFileLock(lockp, timeout=0.3)
            t0 = time.time()
            self.assertFalse(b.acquire())
            self.assertGreaterEqual(time.time() - t0, 0.25)
            a.release()

    def test_reacquire_after_release(self):
        with tempfile.TemporaryDirectory() as d:
            lockp = os.path.join(d, "y.lock")
            for _ in range(3):
                with InterProcessFileLock(lockp, timeout=5.0):
                    pass


if __name__ == "__main__":
    unittest.main()
