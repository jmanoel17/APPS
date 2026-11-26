"""Armazenamento seguro de credenciais.

Prioridade no Windows: DPAPI do sistema (CryptProtectData) — o blob fica criptado
com a conta do usuario, sem dependencia externa. Se o pacote `keyring` estiver
instalado, usa o Windows Credential Manager.

NUNCA grava senha em texto puro. Em plataformas sem backend seguro, recusa-se a
persistir (retorna None) em vez de salvar em claro.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_from(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_to(blob: _DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _dpapi_encrypt(data: bytes) -> bytes:
    inp = _blob_from(data)
    out = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(inp), "dpn", None, None, None, 0, ctypes.byref(out)
    ):
        raise ctypes.WinError()
    try:
        return _blob_to(out)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _dpapi_decrypt(data: bytes) -> bytes:
    inp = _blob_from(data)
    out = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
    ):
        raise ctypes.WinError()
    try:
        return _blob_to(out)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _is_windows() -> bool:
    return os.name == "nt"


class CredentialStore:
    """Guarda e recupera senhas por chave (uma por unidade)."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._file = os.path.join(data_dir, "credentials.dat")
        self._cache: dict[str, str] = {}

    @staticmethod
    def _key(device_id: int) -> str:
        return f"dpn:device:{device_id}"

    def _load_blobs(self) -> dict[str, str]:
        if not os.path.isfile(self._file):
            return {}
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_blobs(self, blobs: dict[str, str]) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(blobs, f)

    def set_password(self, device_id: int, password: str) -> None:
        if not password:
            return
        key = self._key(device_id)
        if _is_windows():
            enc = base64.b64encode(_dpapi_encrypt(password.encode("utf-8"))).decode()
            blobs = self._load_blobs()
            blobs[key] = enc
            self._save_blobs(blobs)
        else:
            # Nunca grava em claro em plataforma sem DPAPI/Credential Manager.
            raise RuntimeError("sem backend seguro disponivel nesta plataforma")
        self._cache[key] = password

    def get_password(self, device_id: int) -> str | None:
        key = self._key(device_id)
        if key in self._cache:
            return self._cache[key]
        blobs = self._load_blobs()
        if key not in blobs:
            return None
        if _is_windows():
            try:
                raw = base64.b64decode(blobs[key])
                pwd = _dpapi_decrypt(raw).decode("utf-8")
            except Exception:
                return None
            self._cache[key] = pwd
            return pwd
        return None

    def delete_password(self, device_id: int) -> None:
        key = self._key(device_id)
        self._cache.pop(key, None)
        blobs = self._load_blobs()
        if key in blobs:
            del blobs[key]
            self._save_blobs(blobs)
