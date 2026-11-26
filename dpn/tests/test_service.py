"""Testes da camada de servico (sessao autenticada reutilizada)."""
import tempfile

from dpn.config import Config
from dpn.models import Device, TLSMode
from dpn.service import DPNService


class FakeClient:
    def __init__(self):
        self.logins = 0
        self.closed = 0

    def login(self, password=None):
        self.logins += 1

    def close(self):
        self.closed += 1


def make_service():
    cfg = Config()
    cfg.data_dir = tempfile.mkdtemp()
    cfg.resolve()
    return DPNService(cfg)


def test_session_is_reused(monkeypatch):
    svc = make_service()
    dev = svc.add_device(Device(nickname="x", address="1.2.3.4",
                                tls_mode=TLSMode.INSECURE))
    fake = FakeClient()
    monkeypatch.setattr(svc, "client_for", lambda dev: fake)

    c1 = svc.get_session(dev, "pwd")
    c2 = svc.get_session(dev, "pwd")
    assert c1 is c2
    assert fake.logins == 1          # login apenas uma vez
    svc.close()


def test_invalidate_forces_new_login(monkeypatch):
    svc = make_service()
    dev = svc.add_device(Device(nickname="x", address="1.2.3.4",
                                tls_mode=TLSMode.INSECURE))
    clients = []

    def _make(dev):
        c = FakeClient()
        clients.append(c)
        return c

    monkeypatch.setattr(svc, "client_for", _make)
    svc.get_session(dev, "pwd")
    svc.invalidate(dev)
    svc.get_session(dev, "pwd")
    assert len(clients) == 2
    assert clients[0].closed == 1
    svc.close()
