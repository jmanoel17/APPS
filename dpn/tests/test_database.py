"""Testes do banco SQLite (cadastro + historico, sem segredos)."""
import os
import tempfile

from dpn.models import ActionLog, ActionStatus, Device, TLSMode
from dpn.storage.database import Database


def test_device_crud():
    d = Device(nickname="DPN 1", address="192.168.1.50", tls_mode=TLSMode.VERIFY)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        saved = db.add_device(d)
        assert saved.id is not None

        devices = db.list_devices()
        assert len(devices) == 1
        assert devices[0].nickname == "DPN 1"
        assert devices[0].tls_mode is TLSMode.VERIFY

        saved.nickname = "DPN 1 renomeada"
        db.update_device(saved)
        assert db.get_device(saved.id).nickname == "DPN 1 renomeada"

        db.delete_device(saved.id)
        assert db.list_devices() == []
        db.close()


def test_history_logging():
    d = Device(nickname="DPN 2", address="10.0.0.2")
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        saved = db.add_device(d)
        db.add_log(ActionLog(
            device_id=saved.id, device_nickname=saved.nickname,
            action="login", status=ActionStatus.OK.value, message="ok",
        ))
        db.add_log(ActionLog(
            device_id=saved.id, device_nickname=saved.nickname,
            action="reboot", status=ActionStatus.UNCONFIRMED.value,
            message="endpoint nao confirmado",
        ))
        logs = db.list_logs(saved.id)
        assert len(logs) == 2
        assert logs[0].action == "reboot"  # ordem decrescente de id
        db.close()


def test_no_password_stored_in_db():
    """O SQLite nao deve ter colunas de senha/token/cookie."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        cols = [r["name"] for r in db._conn.execute("PRAGMA table_info(devices)").fetchall()]
        for forbidden in ("password", "token", "cookie", "secret"):
            assert forbidden not in cols
        db.close()
