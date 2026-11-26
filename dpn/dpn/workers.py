"""Execucao de tarefas de rede fora da thread da UI (QThreadPool).

Os callbacks (on_done/on_error/on_progress) SEMPRE rodam na thread da
QApplication: o worker emite sinais e a entrega e feita por um QObject de
despacho (com afinidade com a thread principal) via QueuedConnection. Excecoes
nos callbacks sao capturadas para nao derrubar o app.
"""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)   # resultado
    error = Signal(object)      # excecao
    progress = Signal(str)      # mensagem de progresso


class FunctionWorker(QRunnable):
    def __init__(self, fn: Callable):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(self.fn())
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(exc)


class _Job(QObject):
    """Despachante por tarefa, com afinidade com a thread principal."""

    def __init__(self, runner: "TaskRunner", sig: WorkerSignals,
                 on_done, on_error, on_progress):
        super().__init__(runner)
        self._runner = runner
        self._sig = sig
        self._on_done = on_done
        self._on_error = on_error
        self._on_progress = on_progress
        self._cleaned = False

    def _cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._runner._refs.discard(self._sig)
        self.deleteLater()

    @Slot(object)
    def _done(self, result) -> None:
        try:
            if self._on_done:
                self._on_done(result)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            self._cleanup()

    @Slot(object)
    def _error(self, exc) -> None:
        try:
            if self._on_error:
                self._on_error(exc)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            self._cleanup()

    @Slot(str)
    def _progress(self, text: str) -> None:
        try:
            if self._on_progress:
                self._on_progress(text)
        except Exception:  # noqa: BLE001
            traceback.print_exc()


class TaskRunner(QObject):
    """Pool de threads com sinais; mantem referencias para evitar GC prematuro."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        # um pouco menos que o numero de nucleos para nao sufocar
        self.pool.setMaxThreadCount(6)
        self._refs: set = set()

    def submit(
        self,
        fn: Callable,
        on_done: Callable | None = None,
        on_error: Callable | None = None,
        on_progress: Callable | None = None,
        with_progress: bool = False,
    ) -> FunctionWorker:
        worker = FunctionWorker(fn)
        if with_progress:
            # fn recebe um emissor de progresso (seguro entre threads)
            worker.fn = lambda: fn(worker.signals.progress.emit)
        sig = worker.signals
        job = _Job(self, sig, on_done, on_error, on_progress)
        self._refs.add(sig)
        # entrega garantidamente na thread do _Job (thread principal/QApplication)
        sig.finished.connect(job._done, Qt.ConnectionType.QueuedConnection)
        sig.error.connect(job._error, Qt.ConnectionType.QueuedConnection)
        sig.progress.connect(job._progress, Qt.ConnectionType.QueuedConnection)
        self.pool.start(worker)
        return worker

    def wait_for_done(self, timeout_ms: int = 2000) -> bool:
        """Espera (limitada) as tarefas terminarem — usado no shutdown."""
        return self.pool.waitForDone(timeout_ms)
