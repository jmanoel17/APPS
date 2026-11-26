"""Painel de Túneis (equivalente a /admin/tunnel).

Lista Regiao, Pais, nº de IPs ativos e IP ativo, com acoes por linha
("Switch active IP" e "Refresh") e o Node Selection Mode.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dpn.atomos.regions import country_label, region_label
from dpn.models import Device, NodeLbMode
from dpn.ui.auth_task import run_authenticated
from dpn.ui.context import SelectionContext
from dpn.ui.dialogs import confirm
from dpn.ui.widgets import NoWheelComboBox


class TunnelsPanel(QWidget):
    def __init__(self, service, task_runner, history_panel, manager=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.tasks = task_runner
        self.history = history_panel
        self.manager = manager
        self.device: Device | None = None
        self._tunnels: list[dict] = []
        self._ctx = SelectionContext()
        self._rows_token = None
        self._busy = False

        self.lbl_title = QLabel("Túneis")
        self.lbl_title.setStyleSheet("font-weight: bold;")

        self.mode_combo = NoWheelComboBox()
        for m in NodeLbMode.ALL:
            self.mode_combo.addItem(NodeLbMode.LABELS[m], m)
        self.btn_apply_mode = QPushButton("Aplicar modo")
        self.btn_reload = QPushButton("Atualizar lista")

        self.lbl_status = QLabel("sem unidade selecionada")
        self.lbl_shared = QLabel("")
        self.lbl_shared.setWordWrap(True)
        self.lbl_shared.setStyleSheet("color: #b45309;")
        self.lbl_last = QLabel("Última ação: —")
        self.lbl_last.setWordWrap(True)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Região", "País", "# IPs ativos", "IP ativo", "Switch IP", "Refresh"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)

        top = QHBoxLayout()
        top.addWidget(QLabel("Node Selection Mode:"))
        top.addWidget(self.mode_combo)
        top.addWidget(self.btn_apply_mode)
        top.addStretch(1)
        top.addWidget(self.btn_reload)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.lbl_title)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.lbl_shared)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.lbl_last)

        self.btn_apply_mode.clicked.connect(self._on_apply_mode)
        self.btn_reload.clicked.connect(self.reload)

        self.set_device(None)

    # ------------------------------------------------------------------ estado
    def set_device(self, device: Device | None) -> None:
        self.device = device
        enabled = device is not None
        for w in (self.mode_combo, self.btn_apply_mode, self.btn_reload):
            w.setEnabled(enabled)
        if device is None:
            self._ctx.select(None, None)
        else:
            self._ctx.select(device.id,
                             (device.transport_mode,
                              self.service._resolve_agent_id(device)))
        self._rows_token = None
        # limpa contexto anterior imediatamente
        self.table.setRowCount(0)
        self._tunnels = []
        self.lbl_shared.setText("")
        if device is None:
            self.lbl_title.setText("Túneis")
            self.lbl_status.setText("sem unidade selecionada")
            return
        self.lbl_title.setText(f"Túneis da {device.nickname}")
        self.lbl_status.setText(f"Carregando dados de {device.nickname}…")
        if device.transport_mode == "agent" or self.service.has_password(device.id):
            self.reload()
        else:
            self.lbl_status.setText(
                f"{device.nickname}: sem credenciais (use Configurar)")

    def _device(self) -> Device:
        if self.device is None:
            raise RuntimeError("nenhuma unidade selecionada")
        return self.device

    def _row_ok(self) -> bool:
        t = self._rows_token
        return bool(t) and self._ctx.valid(t)

    def _reject_stale(self) -> bool:
        if self._row_ok():
            return False
        QMessageBox.information(
            self, "Dados desatualizados",
            "Os dados desta linha estão desatualizados. Atualize a lista.")
        return True

    def _callbacks(self, token):
        def on_status(text, ok=None):
            if self._ctx.valid(token):
                self._set_status(text, ok)

        def on_last(text):
            if self._ctx.valid(token):
                self._set_last(text)
        return on_status, on_last

    def _end_busy(self) -> None:
        """Reabilita as linhas ao fim de QUALQUER operacao (sucesso/erro)."""
        self._busy = False
        self._set_rows_enabled(True)

    def _set_rows_enabled(self, enabled: bool) -> None:
        """Habilita/desabilita os botoes por linha (evita switches em rajada)."""
        for row in range(self.table.rowCount()):
            for col in (4, 5):
                w = self.table.cellWidget(row, col)
                if w is not None:
                    w.setEnabled(enabled)

    def _set_status(self, text: str, ok: bool | None = None) -> None:
        self.lbl_status.setText(text)

    def _set_last(self, text: str) -> None:
        self.lbl_last.setText(f"Última ação: {text}")

    # ------------------------------------------------------------------ carregar
    def reload(self) -> None:
        if self.device is None:
            return
        dev = self.service.get_device(self.device.id) or self._device()
        token = self._ctx.token()

        def _on_status(text: str, ok=None) -> None:
            if self._ctx.valid(token):
                self._set_status(text, ok)
                self.btn_reload.setEnabled(self.device is not None)

        def _on_last(text: str) -> None:
            if self._ctx.valid(token):
                self._set_last(text)

        def _apply_tunnels(r) -> None:
            if self._ctx.valid(token):
                self._rows_token = token
                self._render_tunnels(r)

        def _apply_mode(r) -> None:
            if self._ctx.valid(token):
                self._render_mode(r)

        run_authenticated(
            self, self.service, self.tasks, self.history, dev,
            "tunnel_list", lambda c: c.get_exit_ip(),
            apply=_apply_tunnels, on_status=_on_status, on_last=_on_last,
            log_action=False,
        )
        run_authenticated(
            self, self.service, self.tasks, self.history, dev,
            "node_lb_mode_get", lambda c: c.get_node_lb_mode(),
            apply=_apply_mode,
            log_action=False,
        )

    def _render_tunnels(self, r) -> None:
        if isinstance(r, dict):
            lst = r.get("list") or r.get("tunnels") or []
        elif isinstance(r, list):
            lst = r
        else:
            lst = []
        self._tunnels = [t for t in lst if isinstance(t, dict)]
        self.table.setRowCount(0)
        for t in self._tunnels:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(region_label(t.get("regionCode"))))
            self.table.setItem(row, 1, QTableWidgetItem(country_label(t.get("countryCode"))))
            self.table.setItem(row, 2, QTableWidgetItem(str(t.get("activeNum", "?"))))
            self.table.setItem(row, 3, QTableWidgetItem(str(t.get("activeIp") or "—")))

            b_switch = QPushButton("Switch active IP")
            b_switch.clicked.connect(lambda _=False, tt=t: self._on_switch(tt))
            self.table.setCellWidget(row, 4, b_switch)

            b_refresh = QPushButton("Refresh")
            b_refresh.clicked.connect(lambda _=False, tt=t: self._on_refresh(tt))
            self.table.setCellWidget(row, 5, b_refresh)
        self.table.resizeColumnsToContents()
        if self.device is not None and self.device.id is not None:
            self.service.set_device_tunnel_ips(
                self.device.id, [t.get("activeIp") for t in self._tunnels])
        self._update_shared_label()
        if self.device is not None:
            n = len(self._tunnels)
            self.lbl_status.setText(
                f"{self.device.nickname}: {n} túnel(is)" if n
                else f"{self.device.nickname}: consulta concluída (nenhum túnel)")
            self.btn_reload.setEnabled(True)

    def _update_shared_label(self) -> None:
        shared = self.service.shared_tunnel_ips()
        if not shared:
            self.lbl_shared.setText("")
            return
        names = {d.id: d.nickname for d in self.service.list_devices()}
        parts = []
        for ip, ids in shared.items():
            labels = [names.get(i, f"ID do cadastro {i}") for i in ids]
            parts.append(f"{ip} — " + " e ".join(labels))
        self.lbl_shared.setText("Alerta — IP de túnel compartilhado: " + "; ".join(parts))

    def _render_mode(self, r) -> None:
        if isinstance(r, dict) and r.get("mode"):
            idx = self.mode_combo.findData(r["mode"])
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------ acoes
    def _on_switch(self, t: dict) -> None:
        if self._reject_stale():
            return
        if self._busy:
            return
        code = t.get("tunnelCode")
        ip = t.get("activeIp")
        if not code or not ip:
            QMessageBox.information(self, "Switch IP", "Túnel sem IP ativo.")
            return
        dev = self.service.get_device(self.device.id) or self._device()
        token = self._ctx.token()
        on_status, on_last = self._callbacks(token)
        self._busy = True
        self._set_rows_enabled(False)
        run_authenticated(
            self, self.service, self.tasks, self.history, dev,
            "switchNode", lambda c: c.switch_node(code, ip),
            apply=self._after_switch,
            on_status=on_status, on_last=on_last,
            on_finish=self._end_busy,
            mutating=True,
            log_detail=f"{code} {ip}",
        )

    def _after_switch(self, r) -> None:
        if isinstance(r, dict) and r.get("activeIp"):
            self._set_last(f"switchNode — novo IP {r['activeIp']}")
        self.reload()

    def _on_refresh(self, t: dict) -> None:
        if self._reject_stale():
            return
        if self._busy:
            return
        code = t.get("tunnelCode")
        if not code:
            return
        if not confirm(self, "Confirmar refresh",
                       f"Gerar novos IPs para o túnel "
                       f"{country_label(t.get('countryCode'))} ({code})?\n"
                       "Leva alguns segundos.", danger=True):
            return
        dev = self.service.get_device(self.device.id) or self._device()
        token = self._ctx.token()
        on_status, on_last = self._callbacks(token)
        self._busy = True
        self._set_rows_enabled(False)
        run_authenticated(
            self, self.service, self.tasks, self.history, dev,
            "refreshTunnel", lambda c: c.refresh_tunnel(code),
            apply=lambda _r: self.reload(),
            on_status=on_status, on_last=on_last,
            on_finish=self._end_busy,
            mutating=True,
            log_detail=code,
        )

    def _on_apply_mode(self) -> None:
        if self._busy:
            return
        mode = self.mode_combo.currentData()
        if not confirm(self, "Confirmar modo",
                       f"Definir Node Selection Mode = "
                       f"{NodeLbMode.LABELS.get(mode, mode)}?", danger=True):
            return
        dev = self.service.get_device(self.device.id) or self._device()
        token = self._ctx.token()
        on_status, on_last = self._callbacks(token)
        self._busy = True
        run_authenticated(
            self, self.service, self.tasks, self.history, dev,
            "nodeLbMode", lambda c: c.set_node_lb_mode(mode),
            apply=lambda _r: self.reload(),
            on_status=on_status, on_last=on_last,
            on_finish=self._end_busy,
            mutating=True,
            log_detail=mode,
        )
