#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
panel_dpn.py - SLN V2: painel DPN DEDICADO (sem bot, sem outras abas).

CLIENTE do controller unificado (8766): lê `state/dpn/status.json` e
`state/dpn/agents_status.json`, publica comandos em `state/dpn/commands/`
(coordinator) e `state/dpn/agent_commands/` (controller agent pool).

Ações: pausar/retomar/rotacionar/resetar nó, ligar/desligar access control,
adicionar/remover nó (túnel), ver nós e identidades.
"""
import json
import os
import sys
import threading
import time
import tkinter as tk
import uuid
from tkinter import messagebox, ttk

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for _p in (BASE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dpn import state as S
from dpn import ui_model as UI

STATE_DIR = os.path.join(BASE, "state", "dpn")
STATUS_PATH = os.path.join(STATE_DIR, "status.json")
AGENTS_PATH = os.path.join(STATE_DIR, "agents_status.json")
GROUPS_AUTO = os.path.join(BASE, "dpn_groups_auto.json")
GROUPS_CAT = os.path.join(BASE, "dpn_groups.json")

ALLOWED = ("pause", "resume", "rotate_now", "reset_node_now")


def _read(path, default=None):
    return S.read_json(path, default)


def _physical_map():
    out = {}
    for p in (GROUPS_AUTO, GROUPS_CAT):
        g = _read(p, {})
        if not isinstance(g, dict):
            continue
        for k, spec in g.items():
            if not isinstance(spec, dict):
                continue
            c = spec.get("controller") or {}
            pid = str(c.get("expected_dpn_id") or spec.get("expected_dpn_id") or "")
            if pid:
                out.setdefault(str(k), pid)
        if out:
            break
    return out


def _publish_dpn(dpn_id, action):
    cmd = UI.make_command(dpn_id, action, requested_by="panel")
    UI.write_command(STATE_DIR, cmd)
    return cmd["command_id"]


def _publish_agent(op, physical, params, action="mutation", timeout=45):
    cid = uuid.uuid4().hex
    cmd = {"command_id": cid, "action": action, "physical_dpn_id": physical,
           "op": op, "params": params}
    cmds_dir = os.path.join(STATE_DIR, "agent_commands")
    os.makedirs(cmds_dir, exist_ok=True)
    S.atomic_write_json(os.path.join(cmds_dir, cid + ".json"), cmd)
    done_dir = os.path.join(STATE_DIR, "agent_commands_done")
    end = time.time() + timeout
    while time.time() < end:
        p = os.path.join(done_dir, cid + ".json")
        if os.path.exists(p):
            return S.read_json(p, {}) or {}
        time.sleep(0.5)
    return {"status": "timeout"}


class DpnPanel:
    def __init__(self, root):
        self.root = root
        root.title("SLN V2 - Painel DPN (dedicado)")
        root.geometry("1100x680")
        self.phys = _physical_map()
        self.last_status = {}
        self._build()
        self._tick()

    def _build(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=6)
        self.lbl_ctrl = tk.Label(top, text="Controller: verificando...", font=("Arial", 10, "bold"))
        self.lbl_ctrl.pack(side="left")
        self.lbl_agents = tk.Label(top, text="", font=("Arial", 10))
        self.lbl_agents.pack(side="left", padx=12)

        cols = ("state", "ip", "node", "online", "total", "err")
        self.tree = ttk.Treeview(self.root, columns=cols, show="tree headings", height=18)
        self.tree.heading("#0", text="DPN")
        self.tree.column("#0", width=230, anchor="w")
        for c, h, w in (("state", "Estado", 130), ("ip", "IP", 140), ("node", "Nó", 80),
                        ("online", "Online", 70), ("total", "Total", 60), ("err", "Erro", 300)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)

        bar = tk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=6)
        tk.Button(bar, text="Pausar", command=lambda: self._dpn("pause")).pack(side="left", padx=2)
        tk.Button(bar, text="Retomar", command=lambda: self._dpn("resume")).pack(side="left", padx=2)
        tk.Button(bar, text="Rotacionar", command=lambda: self._dpn("rotate_now")).pack(side="left", padx=2)
        tk.Button(bar, text="Resetar nó", command=lambda: self._dpn("reset_node_now")).pack(side="left", padx=2)
        tk.Button(bar, text="Ver nós", command=self._list_tunnels).pack(side="left", padx=2)
        tk.Button(bar, text="Atualizar identidades", command=self._refresh_ids).pack(side="left", padx=2)

        ac = tk.Frame(self.root)
        ac.pack(fill="x", padx=8, pady=2)
        tk.Label(ac, text="Access Control:").pack(side="left")
        tk.Button(ac, text="OFF", command=lambda: self._ac("off"), bg="#b71c1c", fg="white").pack(side="left", padx=2)
        tk.Button(ac, text="Aberto", command=lambda: self._ac("open"), bg="#2e7d32", fg="white").pack(side="left", padx=2)
        tk.Button(ac, text="Restrito", command=lambda: self._ac("restrict"), bg="#ef6c00", fg="white").pack(side="left", padx=2)
        tk.Button(ac, text="+ Nó", command=lambda: self._add_node()).pack(side="left", padx=8)
        tk.Button(ac, text="- Nó", command=lambda: self._del_node()).pack(side="left", padx=2)

        self.lbl_msg = tk.Label(self.root, text="", fg="#1565c0", anchor="w", justify="left", wraplength=1000)
        self.lbl_msg.pack(fill="x", padx=8, pady=(0, 6))

    # ------------------------------------------------------------ helpers
    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("DPN", "Selecione uma DPN na tabela.")
            return None
        dpn_id = str(sel[0]).split(":", 1)[0]
        pid = self.phys.get(dpn_id, "")
        return dpn_id, pid

    def _run_bg(self, fn):
        def w():
            try:
                msg = fn()
                self.root.after(0, lambda: self.lbl_msg.config(text=msg))
            except Exception as e:
                self.root.after(0, lambda: self.lbl_msg.config(text="ERRO: %s" % e))
        threading.Thread(target=w, daemon=True).start()

    def _dpn(self, action):
        sel = self._selected()
        if not sel:
            return
        dpn_id, _ = sel
        self._run_bg(lambda: "[DPN] %s -> %s (id=%s)" % (action, dpn_id, _publish_dpn(dpn_id, action)))

    def _ac(self, mode):
        sel = self._selected()
        if not sel:
            return
        _, pid = sel
        if not pid:
            messagebox.showinfo("DPN", "Sem physical_dpn_id para esta DPN.")
            return
        self._run_bg(lambda: self._fmt("AC=%s" % mode,
                                       _publish_agent("DPN_ACCESS_CONTROL_MODE", pid,
                                                      {"expected_dpn_id": pid, "mode": mode})))

    def _add_node(self):
        sel = self._selected()
        if not sel:
            return
        dpn_id, pid = sel
        if not pid:
            messagebox.showinfo("DPN", "Sem physical_dpn_id.")
            return
        code = self._prompt("Adicionar nó", "Código do nó (ex.: DE, SE, GB):", dpn_id)
        if code:
            self._run_bg(lambda: self._fmt("ADD %s" % code,
                                           _publish_agent("DPN_ADD_TUNNEL", pid,
                                                          {"expected_dpn_id": pid, "tunnel_code": code})))

    def _del_node(self):
        sel = self._selected()
        if not sel:
            return
        dpn_id, pid = sel
        if not pid:
            messagebox.showinfo("DPN", "Sem physical_dpn_id.")
            return
        code = self._prompt("Remover nó", "Código do nó (ex.: DE, SE, GB):", dpn_id)
        if code:
            self._run_bg(lambda: self._fmt("DEL %s" % code,
                                           _publish_agent("DPN_DELETE_TUNNEL", pid,
                                                          {"expected_dpn_id": pid, "tunnel_code": code})))

    def _list_tunnels(self):
        sel = self._selected()
        if not sel:
            return
        _, pid = sel
        if not pid:
            messagebox.showinfo("DPN", "Sem physical_dpn_id.")
            return
        self._run_bg(lambda: self._fmt("TUNNELS",
                                       _publish_agent("LIST_DPN_TUNNELS", pid, {"expected_dpn_id": pid},
                                                      action="read")))

    def _refresh_ids(self):
        self._run_bg(lambda: self._fmt("REFRESH",
                                       _publish_agent("GET_DPN_IDENTITY", "",
                                                      {"expected_dpn_id": ""}, action="refresh_identities")))

    def _prompt(self, title, label, dpn_id):
        dlg = tk.Toplevel(self.root)
        dlg.title("%s - %s" % (title, dpn_id))
        dlg.geometry("360x110")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text=label).pack(padx=8, pady=6)
        e = tk.Entry(dlg, width=24)
        e.pack(padx=8)
        e.focus_set()
        out = {}
        def ok():
            out["v"] = e.get().strip()
            dlg.destroy()
        tk.Button(dlg, text="OK", command=ok).pack(pady=6)
        dlg.bind("<Return>", lambda ev: ok())
        self.root.wait_window(dlg)
        return out.get("v")

    def _fmt(self, label, res):
        if not isinstance(res, dict):
            return "%s: (sem resposta)" % label
        st = res.get("status")
        if st != "ok":
            return "%s: ERRO -> %s" % (label, res.get("error") or st)
        data = res.get("data")
        if data is None:
            return "%s: OK" % label
        if isinstance(data, dict) and "tunnels" in data:
            ts = data.get("tunnels") or []
            names = sorted(t.get("tunnelCode") for t in ts if isinstance(t, dict) and t.get("tunnelCode"))
            return "%s: %d nós -> %s" % (label, len(names), ", ".join(names))
        try:
            s = json.dumps(data, ensure_ascii=False)
        except Exception:
            s = str(data)
        return "%s: OK -> %s" % (label, s[:700])

    # ------------------------------------------------------------ refresh
    def _tick(self):
        try:
            st = UI.read_status(STATUS_PATH, default=None)
            ag = UI.read_status(AGENTS_PATH, default=None)
            if isinstance(st, dict):
                online = UI.coordinator_online(st)
                self.lbl_ctrl.config(text="Controller: %s" % ("ONLINE" if online else "OFFLINE"),
                                     fg="#2e7d32" if online else "#b71c1c")
                self._apply(st)
            if isinstance(ag, dict):
                agents = ag.get("agents") or []
                auth = sum(1 for a in agents if a.get("authenticated"))
                self.lbl_agents.config(text="Agentes: %d online / %d autenticados" % (len(agents), auth))
        except Exception:
            pass
        self.root.after(1500, self._tick)

    def _apply(self, st):
        dpns = st.get("dpns") or {}
        items = {}
        for r in UI.rows(st):
            items[r["dpn_id"]] = r
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for dpn_id, d in sorted(dpns.items()):
            r = items.get(dpn_id, {})
            self.tree.insert("", "end", iid=dpn_id, text=dpn_id, values=(
                r.get("state") or d.get("state") or "",
                r.get("ip") or d.get("current_ip") or "",
                r.get("node") or d.get("node_id") or "",
                r.get("active") or (d.get("totals") or {}).get("online") or 0,
                r.get("safe") or (d.get("totals") or {}).get("total") or 0,
                r.get("error") or d.get("last_error") or "",
            ))


def main():
    root = tk.Tk()
    DpnPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
