#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/locks.py — SLN V2: lock multi-processo REAL (exclusão mútua de verdade).

Motivo: o lock do QA (`qa_behavior.FileQACoordinator`) usa O_EXCL + deleção após 5s, o que
QUEBRA a exclusão mútua sob contenção. A contabilidade de work-unit do DPN exige um lock cuja
exclusão seja garantida pelo SO (o invariante "nunca 21 quando limite=20" depende disso).

Implementação:
  - Windows: msvcrt.locking (região de 1 byte no offset 0) — exclusão real de SO, não-advisory.
  - POSIX:   fcntl.flock(LOCK_EX) — advisory, mas suficiente (mesmo dataset).
  - Fallback sem nenhum backend: exclusão apenas em processo (documentado; não recomendado).

PID/timestamp/host são gravados num arquivo `.meta` (best effort, DIAGNÓSTICO apenas). O lock
NÃO depende de deleção/tempo: ele vive enquanto o fd estiver aberto (SO libera na morte do dono).
"""
import json
import os
import time

try:  # Windows
    import msvcrt as _msvcrt
except Exception:  # pragma: no cover
    _msvcrt = None

try:  # POSIX
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None


class InterProcessFileLock:
    """Lock de exclusão mútua entre processos (context manager).

        with InterProcessFileLock(path, timeout=10):
            ... read-modify-write ...
    """

    def __init__(self, path, timeout=10.0, poll=0.02):
        self.path = path
        self.timeout = float(timeout)
        self.poll = float(poll)
        self._fd = None
        self._locked = False

    # ---- fd / região ----
    def _open(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        # garante >=1 byte (msvcrt tranca uma REGIÃO existente)
        try:
            os.lseek(self._fd, 0, os.SEEK_END)
            if os.lseek(self._fd, 0, os.SEEK_CUR) == 0:
                os.write(self._fd, b"\0")
        except Exception:
            pass
        os.lseek(self._fd, 0, os.SEEK_SET)

    def _try_lock(self):
        if _msvcrt is not None:
            os.lseek(self._fd, 0, os.SEEK_SET)
            _msvcrt.locking(self._fd, _msvcrt.LK_NBLCK, 1)
        elif _fcntl is not None:
            _fcntl.flock(self._fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        # sem backend: assume adquirido (fallback de processo único)

    def _unlock(self):
        if self._fd is None:
            return
        try:
            if _msvcrt is not None:
                os.lseek(self._fd, 0, os.SEEK_SET)
                _msvcrt.locking(self._fd, _msvcrt.LK_UNLCK, 1)
            elif _fcntl is not None:
                _fcntl.flock(self._fd, _fcntl.LOCK_UN)
        except Exception:
            pass

    # ---- API ----
    def acquire(self):
        if self._fd is None:
            self._open()
        deadline = time.time() + self.timeout
        while True:
            try:
                self._try_lock()
                self._locked = True
                self._write_meta()
                return True
            except (OSError, IOError):
                if time.time() >= deadline:
                    self._close()
                    return False
                time.sleep(self.poll)

    def release(self):
        if self._fd is not None and self._locked:
            self._unlock()
            self._locked = False
        self._close()

    def _close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None

    def _write_meta(self):
        """Diagnóstico (não participa da exclusão). Best effort."""
        try:
            with open(self.path + ".meta", "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "ts": time.time()}, f)
        except Exception:
            pass

    def read_meta(self):
        try:
            with open(self.path + ".meta", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError("InterProcessFileLock timeout: %s" % self.path)
        return self

    def __exit__(self, *exc):
        self.release()
        return False
