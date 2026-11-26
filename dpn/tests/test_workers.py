"""Testes do TaskRunner: callbacks SEMPRE na thread da QApplication."""
import gc
import threading
import time

from PySide6.QtCore import QCoreApplication

from dpn.workers import TaskRunner

# app unica, mantida viva durante todo o processo de teste
_app = QCoreApplication.instance() or QCoreApplication([])


def _pump(cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        _app.processEvents()
        time.sleep(0.02)
    _app.processEvents()


def _run(runner, *args, **kwargs):
    try:
        return runner.submit(*args, **kwargs)
    finally:
        pass


def test_done_runs_on_main_thread():
    main = threading.current_thread()
    seen = {}
    runner = TaskRunner()
    try:
        runner.submit(lambda: 42, on_done=lambda r: seen.setdefault(
            "done", (r, threading.current_thread())))
        _pump(lambda: "done" in seen)
        assert seen.get("done") and seen["done"][0] == 42
        assert seen["done"][1] is main
    finally:
        runner.wait_for_done(2000)
        _app.processEvents()
        del runner
        gc.collect()


def test_progress_and_error_runs_on_main_thread():
    main = threading.current_thread()
    seen = {}

    def work(progress):
        progress("p1")
        raise ValueError("boom")

    runner = TaskRunner()
    try:
        runner.submit(
            work, with_progress=True,
            on_progress=lambda t: seen.setdefault(
                "prog", (t, threading.current_thread())),
            on_error=lambda e: seen.setdefault(
                "err", (str(e), threading.current_thread())),
        )
        _pump(lambda: "prog" in seen and "err" in seen)
        assert seen["prog"][0] == "p1" and seen["prog"][1] is main
        assert seen["err"][0] == "boom" and seen["err"][1] is main
    finally:
        runner.wait_for_done(2000)
        _app.processEvents()
        del runner
        gc.collect()
