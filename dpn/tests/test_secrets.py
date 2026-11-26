"""Testes do armazenamento seguro de credenciais (com DPAPI simulado)."""
import base64
import tempfile

from dpn.storage import secrets as s


def test_roundtrip_with_simulated_dpapi(monkeypatch):
    monkeypatch.setattr(s, "_is_windows", lambda: True)
    monkeypatch.setattr(
        s, "_dpapi_encrypt",
        lambda data: base64.b64encode(data),
    )
    monkeypatch.setattr(
        s, "_dpapi_decrypt",
        lambda data: base64.b64decode(data),
    )

    with tempfile.TemporaryDirectory() as tmp:
        store = s.CredentialStore(tmp)
        store.set_password(42, "senha-secreta")
        assert store.get_password(42) == "senha-secreta"

        # a senha nunca fica em claro no arquivo
        with open(store._file, "r", encoding="utf-8") as f:
            raw = f.read()
        assert "senha-secreta" not in raw

        store.delete_password(42)
        assert store.get_password(42) is None


def test_missing_password_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        store = s.CredentialStore(tmp)
        assert store.get_password(999) is None
