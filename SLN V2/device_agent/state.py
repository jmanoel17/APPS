#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
device_agent/state.py — persistência SQLite do controller Device Agent.

Arquivo: state/device_agents/agents.db
  agents(agent_id PK, token_hash, revoked, epoch, android_id, build_fingerprint, manufacturer,
         model, sdk, agent_version, capabilities(JSON), last_seen, created_at, aliases(JSON),
         possible_reenrollment)
  pairing(code_hash PK, intent, created_at, expires_at, used, used_by)
  commands(command_id PK, agent_id, device_id, op, params(JSON), status, created_at, sent_at,
           finished_at, error, data(JSON), deadline_ms, mutating)
  events(id, ts, agent_id, kind, detail)
  device_map(agent_id PK, dpn_id, note, updated_at)

Nunca guarda token bruto (apenas token_hash). Escrita transacional; thread-safe por lock.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from .models import AgentRecord, CommandRecord

DEFAULT_DIR = os.path.join("state", "device_agents")
TERMINAL_STATUSES = ("SUCCESS", "FAILURE", "EXPIRED", "UNCERTAIN", "CANCELLED")
ALLOWED_TRANSITIONS = {
    "QUEUED": frozenset(("SENT",) + TERMINAL_STATUSES),
    "SENT": frozenset(TERMINAL_STATUSES),
}


class DeviceAgentStore:
    def __init__(self, base_dir=None):
        base_dir = base_dir or DEFAULT_DIR
        os.makedirs(base_dir, exist_ok=True)
        self.db_path = os.path.join(base_dir, "agents.db")
        self._lock = threading.RLock()
        self._con = sqlite3.connect(self.db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self):
        with self._lock:
            self._con.executescript("""
            CREATE TABLE IF NOT EXISTS agents(
              agent_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, revoked INTEGER DEFAULT 0,
              epoch INTEGER DEFAULT 1, android_id TEXT DEFAULT '', build_fingerprint TEXT DEFAULT '',
              manufacturer TEXT DEFAULT '', model TEXT DEFAULT '', sdk INTEGER DEFAULT 0,
              agent_version TEXT DEFAULT '', capabilities TEXT DEFAULT '{}',
              last_seen REAL DEFAULT 0, created_at REAL DEFAULT 0, aliases TEXT DEFAULT '{}',
              display TEXT DEFAULT '{}',
              possible_reenrollment INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS pairing(
              code_hash TEXT PRIMARY KEY, intent TEXT DEFAULT '', created_at REAL, expires_at REAL,
              used INTEGER DEFAULT 0, used_by TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS commands(
              command_id TEXT PRIMARY KEY, agent_id TEXT, device_id TEXT, op TEXT,
              params TEXT DEFAULT '{}', status TEXT, created_at REAL, sent_at REAL DEFAULT 0,
              finished_at REAL DEFAULT 0, error TEXT DEFAULT '', data TEXT DEFAULT 'null',
              deadline_ms INTEGER DEFAULT 0, mutating INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, agent_id TEXT, kind TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS device_map(
              agent_id TEXT PRIMARY KEY, dpn_id TEXT, note TEXT DEFAULT '', updated_at REAL);
            """)
            cols = {r[1] for r in self._con.execute("PRAGMA table_info(agents)").fetchall()}
            if "display" not in cols:
                self._con.execute("ALTER TABLE agents ADD COLUMN display TEXT DEFAULT '{}'")
            self._con.commit()

    def close(self):
        with self._lock:
            try:
                self._con.close()
            except Exception:
                pass

    # ---------------- agents ----------------
    def _row_to_agent(self, r):
        if r is None:
            return None
        return AgentRecord(
            agent_id=r["agent_id"], token_hash=r["token_hash"], revoked=bool(r["revoked"]),
            epoch=int(r["epoch"] or 1), android_id=r["android_id"] or "",
            build_fingerprint=r["build_fingerprint"] or "", manufacturer=r["manufacturer"] or "",
            model=r["model"] or "", sdk=int(r["sdk"] or 0), agent_version=r["agent_version"] or "",
            capabilities=json.loads(r["capabilities"] or "{}"), last_seen=float(r["last_seen"] or 0),
            created_at=float(r["created_at"] or 0), aliases=json.loads(r["aliases"] or "{}"),
            display=json.loads((r["display"] if "display" in r.keys() else "{}") or "{}"),
            possible_reenrollment=bool(r["possible_reenrollment"]))

    def get_agent(self, agent_id):
        with self._lock:
            r = self._con.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return self._row_to_agent(r)

    def list_agents(self):
        with self._lock:
            rows = self._con.execute("SELECT * FROM agents").fetchall()
        return [self._row_to_agent(r) for r in rows]

    def put_agent(self, rec: AgentRecord):
        with self._lock:
            self._con.execute(
                "INSERT INTO agents(agent_id,token_hash,revoked,epoch,android_id,build_fingerprint,"
                "manufacturer,model,sdk,agent_version,capabilities,last_seen,created_at,aliases,"
                "display,possible_reenrollment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(agent_id) DO UPDATE SET token_hash=excluded.token_hash,"
                "revoked=excluded.revoked,epoch=excluded.epoch,android_id=excluded.android_id,"
                "build_fingerprint=excluded.build_fingerprint,manufacturer=excluded.manufacturer,"
                "model=excluded.model,sdk=excluded.sdk,agent_version=excluded.agent_version,"
                "capabilities=excluded.capabilities,last_seen=excluded.last_seen,aliases=excluded.aliases,"
                "display=excluded.display,possible_reenrollment=excluded.possible_reenrollment",
                (rec.agent_id, rec.token_hash, int(rec.revoked), int(rec.epoch), rec.android_id,
                 rec.build_fingerprint, rec.manufacturer, rec.model, int(rec.sdk), rec.agent_version,
                 json.dumps(rec.capabilities), rec.last_seen, rec.created_at,
                 json.dumps(rec.aliases), json.dumps(rec.display),
                 int(rec.possible_reenrollment)))
            self._con.commit()

    def update_agent_meta(self, agent_id, capabilities=None, agent_version=None, sdk=None,
                          android_id=None, build_fingerprint=None, manufacturer=None, model=None,
                          last_seen=None, display=None):
        rec = self.get_agent(agent_id)
        if rec is None:
            return
        if capabilities is not None:
            rec.capabilities = capabilities
        if agent_version is not None:
            rec.agent_version = agent_version
        if sdk is not None:
            rec.sdk = int(sdk)
        if android_id is not None:
            rec.android_id = android_id
        if build_fingerprint is not None:
            rec.build_fingerprint = build_fingerprint
        if manufacturer is not None:
            rec.manufacturer = manufacturer
        if model is not None:
            rec.model = model
        if last_seen is not None:
            rec.last_seen = float(last_seen)
        if display is not None:
            rec.display = display
        self.put_agent(rec)

    def set_revoked(self, agent_id, revoked=True):
        with self._lock:
            self._con.execute("UPDATE agents SET revoked=? WHERE agent_id=?", (int(revoked), agent_id))
            self._con.commit()

    def find_by_alias(self, android_id, build_fingerprint):
        if not android_id and not build_fingerprint:
            return []
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM agents WHERE (android_id<>'' AND android_id=?) "
                "OR (build_fingerprint<>'' AND build_fingerprint=? AND android_id=?)",
                (android_id, build_fingerprint, android_id)).fetchall()
        return [self._row_to_agent(r) for r in rows]

    # ---------------- pairing ----------------
    def put_pairing(self, code_hash, intent, created_at, expires_at):
        with self._lock:
            self._con.execute(
                "INSERT INTO pairing(code_hash,intent,created_at,expires_at,used,used_by) "
                "VALUES(?,?,?,?,0,'')", (code_hash, intent, created_at, expires_at))
            self._con.commit()

    def get_pairing(self, code_hash):
        with self._lock:
            r = self._con.execute("SELECT * FROM pairing WHERE code_hash=?", (code_hash,)).fetchone()
        return dict(r) if r else None

    def mark_pairing_used(self, code_hash, agent_id):
        with self._lock:
            self._con.execute("UPDATE pairing SET used=1, used_by=? WHERE code_hash=?",
                              (agent_id, code_hash))
            self._con.commit()

    # ---------------- commands ----------------
    def _row_to_command(self, r):
        if r is None:
            return None
        return CommandRecord(
            command_id=r["command_id"], agent_id=r["agent_id"], device_id=r["device_id"],
            op=r["op"], params=json.loads(r["params"] or "{}"), status=r["status"],
            created_at=float(r["created_at"] or 0), sent_at=float(r["sent_at"] or 0),
            finished_at=float(r["finished_at"] or 0), error=r["error"] or "",
            data=json.loads(r["data"] or "null"), deadline_ms=int(r["deadline_ms"] or 0),
            mutating=bool(r["mutating"]))

    def save_command(self, cmd: CommandRecord):
        with self._lock:
            cur = self._con.execute(
                "INSERT OR IGNORE INTO commands(command_id,agent_id,device_id,op,params,status,"
                "created_at,sent_at,finished_at,error,data,deadline_ms,mutating) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ",
                (cmd.command_id, cmd.agent_id, cmd.device_id, cmd.op, json.dumps(cmd.params),
                 cmd.status, cmd.created_at, cmd.sent_at, cmd.finished_at, cmd.error,
                 json.dumps(cmd.data), int(cmd.deadline_ms), int(cmd.mutating)))
            self._con.commit()
            return cur.rowcount > 0

    def get_command(self, command_id):
        with self._lock:
            r = self._con.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
        return self._row_to_command(r)

    def update_command(self, command_id, status=None, sent_at=None, finished_at=None,
                       error=None, data=None):
        sets = []
        args = []
        if status is not None:
            sets.append("status=?"); args.append(status)
        if sent_at is not None:
            sets.append("sent_at=?"); args.append(float(sent_at))
        if finished_at is not None:
            sets.append("finished_at=?"); args.append(float(finished_at))
        if error is not None:
            sets.append("error=?"); args.append(error)
        if data is not None:
            sets.append("data=?"); args.append(json.dumps(data))
        if not sets:
            return False
        with self._lock:
            row = self._con.execute("SELECT status FROM commands WHERE command_id=?",
                                    (command_id,)).fetchone()
            if row is None:
                return False
            current = row["status"]
            if current in TERMINAL_STATUSES:
                return False
            if status is not None:
                if status == current:
                    return False
                allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
                if status not in allowed:
                    return False
            args.append(command_id)
            args.append(current)
            cur = self._con.execute(
                f"UPDATE commands SET {', '.join(sets)} WHERE command_id=? AND status=?", args)
            self._con.commit()
            return cur.rowcount > 0

    # ---------------- events / mapping ----------------
    def add_event(self, agent_id, kind, detail=""):
        with self._lock:
            self._con.execute("INSERT INTO events(ts,agent_id,kind,detail) VALUES(?,?,?,?)",
                              (time.time(), agent_id, kind, detail))
            self._con.commit()

    def set_device_map(self, agent_id, dpn_id, note=""):
        with self._lock:
            self._con.execute(
                "INSERT INTO device_map(agent_id,dpn_id,note,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(agent_id) DO UPDATE SET dpn_id=excluded.dpn_id, note=excluded.note,"
                "updated_at=excluded.updated_at",
                (agent_id, dpn_id, note, time.time()))
            self._con.commit()

    def get_device_map(self, agent_id):
        with self._lock:
            r = self._con.execute("SELECT * FROM device_map WHERE agent_id=?", (agent_id,)).fetchone()
        return dict(r) if r else None

    def list_device_maps(self):
        with self._lock:
            rows = self._con.execute("SELECT * FROM device_map").fetchall()
        return [dict(r) for r in rows]
