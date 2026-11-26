"""SQLite: cadastro de unidades + historico + agentes (SEM segredos).

Inclui migracao que preserva cadastros e historico existentes e um backup do
banco antes da migracao.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager

from dpn.agent.registry import AgentRecord
from dpn.models import ActionLog, Device, TLSMode

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname TEXT NOT NULL,
    address TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 443,
    tls_mode TEXT NOT NULL DEFAULT 'verify',
    ca_bundle_path TEXT,
    username TEXT NOT NULL DEFAULT 'admin',
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER,
    device_nickname TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    token_hash TEXT DEFAULT '',
    name TEXT DEFAULT '',
    dpn_mac TEXT DEFAULT '',
    device_id INTEGER,
    local_ip TEXT DEFAULT '',
    paired_at REAL DEFAULT 0,
    last_seen REAL DEFAULT 0,
    observed_dpn_id TEXT DEFAULT ''
);
"""

_DEVICE_MIGRATIONS = [
    ("mac_reference", "TEXT DEFAULT ''"),
    ("android_serial", "TEXT DEFAULT ''"),
    ("agent_id", "TEXT"),
    ("expected_dpn_id", "TEXT"),
    ("last_observed_id", "TEXT"),
    ("last_observed_at", "REAL DEFAULT 0"),
    ("last_observed_origin", "TEXT DEFAULT ''"),
    ("bind_direct", "INTEGER DEFAULT 1"),
    ("transport_mode", "TEXT DEFAULT 'direct'"),
]


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._backup_once()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _backup_once(self) -> None:
        try:
            if os.path.isfile(self.db_path):
                bak = self.db_path + ".bak"
                if not os.path.isfile(bak):
                    shutil.copy2(self.db_path, bak)
        except Exception:
            pass

    def _migrate(self) -> None:
        cols = {r["name"] for r in self._conn.execute(
            "PRAGMA table_info(devices)").fetchall()}
        for name, decl in _DEVICE_MIGRATIONS:
            if name not in cols:
                self._conn.execute(f"ALTER TABLE devices ADD COLUMN {name} {decl}")

    @contextmanager
    def transaction(self):
        """Transacao serializada (RLock reentrante)."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------ devices
    def add_device(self, d: Device) -> Device:
        with self.transaction() as c:
            cur = c.execute(
                "INSERT INTO devices (nickname, address, port, tls_mode, ca_bundle_path,"
                " username, notes, mac_reference, android_serial, agent_id, expected_dpn_id,"
                " last_observed_id, bind_direct, transport_mode, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d.nickname, d.address, d.port, d.tls_mode.value, d.ca_bundle_path,
                 d.username, d.notes, d.mac_reference, d.android_serial, d.agent_id,
                 d.expected_dpn_id, d.last_observed_id, 1 if d.bind_direct else 0,
                 d.transport_mode, d.created_at, d.updated_at),
            )
            d.id = cur.lastrowid
            return d

    def update_device(self, d: Device) -> None:
        """Atualiza apenas a CONFIGURACAO do cadastro. NAO toca em agent_id,
        expected_dpn_id nem last_observed_* (evita sobrescrever observacoes/
        associacoes por objetos desatualizados — use os setters dedicados)."""
        if d.id is None:
            raise ValueError("device sem id")
        with self.transaction() as c:
            c.execute(
                "UPDATE devices SET nickname=?, address=?, port=?, tls_mode=?,"
                " ca_bundle_path=?, username=?, notes=?, mac_reference=?,"
                " transport_mode=?, updated_at=? WHERE id=?",
                (d.nickname, d.address, d.port, d.tls_mode.value, d.ca_bundle_path,
                 d.username, d.notes, d.mac_reference, d.transport_mode,
                 d.updated_at, d.id),
            )

    def set_device_transport(self, device_id: int, mode: str) -> None:
        with self.transaction() as c:
            c.execute("UPDATE devices SET transport_mode=? WHERE id=?",
                      (mode, device_id))

    def set_device_serial(self, device_id: int, serial: str) -> None:
        with self.transaction() as c:
            c.execute("UPDATE devices SET android_serial=? WHERE id=?",
                      (serial or "", device_id))

    def set_expected_identity(self, device_id: int, expected_dpn_id: str | None) -> None:
        with self.transaction() as c:
            c.execute("UPDATE devices SET expected_dpn_id=? WHERE id=?",
                      (expected_dpn_id, device_id))

    def set_observed_identity(self, device_id: int, observed_id: str | None,
                              origin: str = "", ts: float | None = None) -> None:
        import time as _t
        with self.transaction() as c:
            c.execute(
                "UPDATE devices SET last_observed_id=?, last_observed_at=?,"
                " last_observed_origin=? WHERE id=?",
                (observed_id, ts if ts is not None else _t.time(), origin, device_id),
            )

    def delete_device(self, device_id: int) -> list[str]:
        """Remove o cadastro e limpa referencias operacionais. Preserva historico.
        Retorna os agent_id que apontavam para ele (o servico invalida)."""
        with self.transaction() as c:
            rows = c.execute("SELECT agent_id FROM agents WHERE device_id=?",
                             (device_id,)).fetchall()
            agent_ids = [r["agent_id"] for r in rows]
            c.execute("UPDATE agents SET device_id=NULL WHERE device_id=?", (device_id,))
            c.execute("DELETE FROM devices WHERE id=?", (device_id,))
            return agent_ids

    def get_device(self, device_id: int) -> Device | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM devices WHERE id=?",
                                     (device_id,)).fetchone()
        return self._row_to_device(row) if row else None

    def list_devices(self) -> list[Device]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM devices ORDER BY nickname COLLATE NOCASE").fetchall()
        return [self._row_to_device(r) for r in rows]

    @staticmethod
    def _row_to_device(row: sqlite3.Row) -> Device:
        keys = row.keys()

        def g(k, default=None):
            return row[k] if k in keys else default

        return Device(
            id=row["id"],
            nickname=row["nickname"],
            address=row["address"],
            port=row["port"],
            tls_mode=TLSMode(row["tls_mode"]),
            ca_bundle_path=row["ca_bundle_path"],
            username=row["username"],
            notes=row["notes"],
            mac_reference=g("mac_reference", "") or "",
            android_serial=g("android_serial", "") or "",
            agent_id=g("agent_id"),
            expected_dpn_id=g("expected_dpn_id"),
            last_observed_id=g("last_observed_id"),
            last_observed_at=float(g("last_observed_at", 0) or 0),
            last_observed_origin=g("last_observed_origin", "") or "",
            bind_direct=bool(g("bind_direct", 1)),
            transport_mode=g("transport_mode", "direct") or "direct",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------ history
    def add_log(self, log: ActionLog) -> ActionLog:
        with self.transaction() as c:
            cur = c.execute(
                "INSERT INTO history (device_id, device_nickname, action, status,"
                " message, ts) VALUES (?,?,?,?,?,?)",
                (log.device_id, log.device_nickname, log.action, log.status,
                 log.message, log.ts),
            )
            log.id = cur.lastrowid
            return log

    def list_logs(self, device_id: int | None = None, limit: int = 200) -> list[ActionLog]:
        with self._lock:
            if device_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM history WHERE device_id=? ORDER BY id DESC LIMIT ?",
                    (device_id, limit),
                ).fetchall()
        return [ActionLog(
            id=r["id"], device_id=r["device_id"], device_nickname=r["device_nickname"],
            action=r["action"], status=r["status"], message=r["message"], ts=r["ts"],
        ) for r in rows]

    # ------------------------------------------------------------------- agents
    def upsert_agent(self, rec: AgentRecord) -> None:
        with self.transaction() as c:
            c.execute(
                "INSERT INTO agents (agent_id, token_hash, name, dpn_mac, device_id,"
                " local_ip, paired_at, last_seen, observed_dpn_id)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(agent_id) DO UPDATE SET token_hash=excluded.token_hash,"
                " name=excluded.name, dpn_mac=excluded.dpn_mac,"
                " local_ip=excluded.local_ip, last_seen=excluded.last_seen",
                (rec.agent_id, rec.token_hash, rec.name, rec.dpn_mac, rec.device_id,
                 rec.local_ip, rec.paired_at, rec.last_seen, ""),
            )

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM agents WHERE agent_id=?",
                                     (agent_id,)).fetchone()
        if not row:
            return None
        return AgentRecord(
            agent_id=row["agent_id"], token_hash=row["token_hash"], name=row["name"],
            dpn_mac=row["dpn_mac"], device_id=row["device_id"],
            local_ip=row["local_ip"], paired_at=row["paired_at"],
            last_seen=row["last_seen"],
        )

    def list_agents(self) -> list[AgentRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM agents ORDER BY paired_at").fetchall()
        return [AgentRecord(
            agent_id=r["agent_id"], token_hash=r["token_hash"], name=r["name"],
            dpn_mac=r["dpn_mac"], device_id=r["device_id"], local_ip=r["local_ip"],
            paired_at=r["paired_at"], last_seen=r["last_seen"],
        ) for r in rows]

    def associate_agent(self, agent_id: str, device_id: int | None) -> None:
        """Mantem a consistencia 1-para-1 (um agente por cadastro e vice-versa)."""
        with self.transaction() as c:
            agent = c.execute("SELECT device_id FROM agents WHERE agent_id=?",
                              (agent_id,)).fetchone()
            agent_device = agent["device_id"] if agent else None
            if device_id is None:
                # desassociar dos dois lados
                if agent_device is not None:
                    c.execute("UPDATE devices SET agent_id=NULL WHERE id=? AND agent_id=?",
                              (agent_device, agent_id))
                c.execute("UPDATE devices SET agent_id=NULL WHERE agent_id=?", (agent_id,))
                c.execute("UPDATE agents SET device_id=NULL WHERE agent_id=?", (agent_id,))
                return
            dev = c.execute("SELECT agent_id FROM devices WHERE id=?",
                            (device_id,)).fetchone()
            dev_agent = dev["agent_id"] if dev else None
            # libera o agente anteriormente ligado a este cadastro
            if dev_agent and dev_agent != agent_id:
                c.execute("UPDATE agents SET device_id=NULL WHERE agent_id=?",
                          (dev_agent,))
            # libera o cadastro anteriormente ligado a este agente
            if agent_device is not None and agent_device != device_id:
                c.execute("UPDATE devices SET agent_id=NULL WHERE id=?",
                          (agent_device,))
            c.execute("UPDATE agents SET device_id=? WHERE agent_id=?",
                      (device_id, agent_id))
            c.execute("UPDATE devices SET agent_id=?, transport_mode='agent' WHERE id=?",
                      (agent_id, device_id))

    def disassociate_agent(self, agent_id: str) -> None:
        self.associate_agent(agent_id, None)

    def set_agent_observed_id(self, agent_id: str, observed_id: str) -> None:
        with self.transaction() as c:
            c.execute("UPDATE agents SET observed_dpn_id=? WHERE agent_id=?",
                      (observed_id, agent_id))

    def touch_agent(self, agent_id: str, ts: float) -> None:
        with self.transaction() as c:
            c.execute("UPDATE agents SET last_seen=? WHERE agent_id=?",
                      (ts, agent_id))

    def delete_agent(self, agent_id: str) -> None:
        with self.transaction() as c:
            c.execute("UPDATE devices SET agent_id=NULL WHERE agent_id=?", (agent_id,))
            c.execute("DELETE FROM agents WHERE agent_id=?", (agent_id,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
