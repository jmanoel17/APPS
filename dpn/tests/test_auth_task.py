"""Regressao: run_authenticated deve chamar on_finish SEMPRE (reabilita a UI).

Sem isso, um erro nao-fatal (ex.: ipNotChanged, que vem com ok=None) deixava os
botoes da UI travados apos o switch.
"""
import contextlib
import time
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from dpn.atomos.errors import UnexpectedResponseError
from dpn.models import Device
from dpn.ui.auth_task import run_authenticated
from dpn.workers import TaskRunner

_app = QCoreApplication.instance() or QCoreApplication([])


def _pump(cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        _app.processEvents()
        time.sleep(0.02)
    _app.processEvents()


def _fake_service():
    @contextlib.contextmanager
    def device_session(device, pwd=None):
        yield SimpleNamespace()
    return SimpleNamespace(
        device_session=device_session,
        can_mutate=lambda d: (True, ""),
        log=lambda *a, **k: None,
        is_agent_transport=lambda d: True,
        validate_direct_identity=lambda c, d: "ID",
    )


def _device():
    return Device(nickname="DPN 1", address="34.34.34.34",
                  transport_mode="agent")


def test_on_finish_called_on_business_error():
    runner = TaskRunner()
    seen = {}

    def fn(client):
        raise UnexpectedResponseError("falha: ipNotChanged")

    try:
        run_authenticated(None, _fake_service(), runner, None, _device(),
                          "switchNode", fn, mutating=True,
                          on_finish=lambda: seen.setdefault("finish", True))
        _pump(lambda: "finish" in seen)
        assert seen.get("finish") is True
    finally:
        runner.wait_for_done(2000)
        _app.processEvents()


def test_on_finish_called_on_success():
    runner = TaskRunner()
    seen = {}
    try:
        run_authenticated(None, _fake_service(), runner, None, _device(),
                          "switchNode", lambda c: {"ok": True}, mutating=True,
                          on_finish=lambda: seen.setdefault("finish", True))
        _pump(lambda: "finish" in seen)
        assert seen.get("finish") is True
    finally:
        runner.wait_for_done(2000)
        _app.processEvents()
