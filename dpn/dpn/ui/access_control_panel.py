"""Aba de Access Control (equivalente a /admin/access-control).

Lista Online/Offline com edicao por dispositivo: DPN Mode, tunnel, HTTPS filter,
bypass e limite de banda. Cada alteracao envia /api/accessControl/setOne.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dpn.atomos.regions import country_label, region_label
from dpn.models import Device, NodeType
from dpn.ui.auth_task import run_authenticated
from dpn.ui.context import SelectionContext
from dpn.ui.dialogs import BypassDialog
from dpn.ui.widgets import NoWheelComboBox, NoWheelSpinBox

_COLUMNS = ["MAC Address", "Device Name", "IP Address", "DPN Mode", "Túnel",
            "HTTPS Filter", "Bypass", "Banda (Mbps)"]


class AccessControlPanel(QWidget):
    def __init__(self, service, task_runner, history_panel, manager=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.tasks = task_runner
        self.history = history_panel
        self.manager = manager
        self.device: Device | None = None
        self._loading = False
        self._ctx = SelectionContext()
        self._rows_token = None
        self._tunnel_options: list[tuple[str, str]] = []  # (label, tunnelCode)

        self.lbl_title = QLabel("Dispositivos conectados")
        self.lbl_title.setStyleSheet("font-weight: bold;")

        self.chk_switch = QCheckBox("Access Control")
        self.btn_reload = QPushButton("Atualizar")
        self.lbl_status = QLabel("sem unidade selecionada")
        self.lbl_last = QLabel("Última ação: —")
        self.lbl_last.setWordWrap(True)

        self.tabs = QTabWidget()
        self.table_online = self._make_table()
        self.table_offline = self._make_table()
        self.tabs.addTab(self.table_online, "Online Device")
        self.tabs.addTab(self.table_offline, "Offline Device")

        top = QHBoxLayout()
        top.addWidget(self.chk_switch)
        top.addStretch(1)
        top.addWidget(self.btn_reload)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.lbl_title)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.lbl_last)

        self.chk_switch.toggled.connect(self._on_switch)
        self.btn_reload.clicked.connect(self.reload)

        self.set_device(None)

    def _make_table(self) -> QTableWidget:
        t = QTableWidget(0, len(_COLUMNS))
        t.setHorizontalHeaderLabels(_COLUMNS)
        t.horizontalHeader().setStretchLastSection(True)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.verticalHeader().setVisible(False)
        return t

    # ------------------------------------------------------------------ estado
    def set_device(self, device: Device | None) -> None:
        self.device = device
        if device is None:
            self._ctx.select(None, None)
        else:
            self._ctx.select(device.id,
                             (device.transport_mode,
                              self.service._resolve_agent_id(device)))
        self._rows_token = None
        self._loading = False
        enabled = device is not None
        self.chk_switch.setEnabled(enabled)
        self.btn_reload.setEnabled(enabled)
        # limpa contexto visual anterior imediatamente
        self.table_online.setRowCount(0)
        self.table_offline.setRowCount(0)
        if device is None:
            self.lbl_title.setText("Dispositivos conectados")
            self.lbl_status.setText("sem unidade selecionada")
            return
        self.lbl_title.setText(f"Dispositivos conectados à {device.nickname}")
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
        """A linha ainda pertence ao contexto/geracao atuais?"""
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

        def _fetch(client):
            return {
                "switch": client.get_access_control_switch(),
                "tunnels": client.get_exit_ip(),
                "devices": client.list_devices(),
            }

        def _on_status(text: str, ok=None) -> None:
            if self._ctx.valid(token):
                self._set_status(text, ok)
                self.btn_reload.setEnabled(self.device is not None)

        def _on_last(text: str) -> None:
            if self._ctx.valid(token):
                self._set_last(text)

        def _apply(result) -> None:
            if self._ctx.valid(token):
                self._rows_token = token     # contexto das linhas renderizadas
                self._render_all(result)

        run_authenticated(
            self, self.service, self.tasks, self.history, dev,
            "accessControl.list", _fetch, apply=_apply,
            on_status=_on_status, on_last=_on_last,
            log_action=False,
        )

    def _render_all(self, r) -> None:
        self._loading = True
        try:
            # switch geral
            sw = r.get("switch") if isinstance(r, dict) else None
            value = None
            if isinstance(sw, dict):
                for k in ("switch", "value", "enabled", "status"):
                    if k in sw:
                        value = sw[k]
                        break
            elif isinstance(sw, bool):
                value = sw
            if value is not None:
                self.chk_switch.setChecked(bool(value))

            # opcoes de tunnel
            tunnels = (r.get("tunnels") if isinstance(r, dict) else None) or []
            self._tunnel_options = []
            if isinstance(tunnels, list):
                for t in tunnels:
                    if isinstance(t, dict) and t.get("tunnelCode"):
                        label = (f"{region_label(t.get('regionCode'))} - "
                                 f"{country_label(t.get('countryCode'))} - "
                                 f"{t.get('activeNum', '?')}")
                        self._tunnel_options.append((label, t["tunnelCode"]))

            # dispositivos
            devs = (r.get("devices") if isinstance(r, dict) else None) or {}
            online = devs.get("online") if isinstance(devs, dict) else None
            offline = devs.get("offline") if isinstance(devs, dict) else None
            self._render_table(self.table_online, online or [])
            self._render_table(self.table_offline, offline or [])
            if self.device is not None:
                n = len(online or []) + len(offline or [])
                self.lbl_status.setText(
                    f"{self.device.nickname}: {n} dispositivo(s)" if n
                    else f"{self.device.nickname}: consulta concluída (nenhum)")
                self.btn_reload.setEnabled(True)
        finally:
            self._loading = False

    def _render_table(self, table: QTableWidget, records: list) -> None:
        table.setRowCount(0)
        for rec in records:
            if not isinstance(rec, dict):
                continue
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(str(rec.get("mac", ""))))
            table.setItem(row, 1, QTableWidgetItem(str(rec.get("name", ""))))
            table.setItem(row, 2, QTableWidgetItem(str(rec.get("ip", ""))))

            # DPN Mode
            mode = NoWheelComboBox()
            for m in NodeType.ALL:
                mode.addItem(NodeType.SHORT_LABELS[m], m)
            idx = mode.findData(rec.get("routeMode"))
            if idx >= 0:
                mode.setCurrentIndex(idx)
            mode.currentIndexChanged.connect(
                lambda _i, r=rec, w=mode: self._on_field(r, "routeMode", w.currentData())
            )
            table.setCellWidget(row, 3, mode)

            # Tunnel (valor salvo em regionCode)
            tunnel = NoWheelComboBox()
            tunnel.addItem("—", "")
            for label, code in self._tunnel_options:
                tunnel.addItem(label, code)
            tidx = tunnel.findData(rec.get("regionCode") or "")
            if tidx >= 0:
                tunnel.setCurrentIndex(tidx)
            tunnel.setEnabled(rec.get("routeMode") == NodeType.FULL)
            tunnel.currentIndexChanged.connect(
                lambda _i, r=rec, w=tunnel: self._on_field(r, "regionCode", w.currentData())
            )
            table.setCellWidget(row, 4, tunnel)

            # HTTPS filter
            https = QCheckBox()
            https.setChecked(bool(rec.get("httpsFilter")))
            https.toggled.connect(
                lambda checked, r=rec: self._on_field(r, "httpsFilter", checked)
            )
            table.setCellWidget(row, 5, https)

            # Bypass
            bypass_btn = QPushButton(self._bypass_label(rec.get("bypass")))
            bypass_btn.clicked.connect(lambda _=False, r=rec, b=bypass_btn: self._on_bypass(r, b))
            table.setCellWidget(row, 6, bypass_btn)

            # Banda
            bw = NoWheelSpinBox()
            bw.setRange(0, 100000)
            bw.setSuffix(" Mbps")
            try:
                bw.setValue(int(rec.get("bwLimit") or 0))
            except (TypeError, ValueError):
                bw.setValue(0)
            bw.editingFinished.connect(
                lambda r=rec, w=bw: self._on_field(r, "bwLimit", w.value())
            )
            table.setCellWidget(row, 7, bw)
        table.resizeColumnsToContents()

    @staticmethod
    def _bypass_label(bypass) -> str:
        if not bypass:
            return "Selecionar…"
        return ", ".join(str(b) for b in bypass)

    # ------------------------------------------------------------------ edicao
    def _on_field(self, record: dict, field: str, value) -> None:
        if self._loading or self._reject_stale():
            return
        dev = self.service.get_device(self.device.id) or self._device()
        token = self._ctx.token()
        on_status, on_last = self._callbacks(token)
        payload = dict(record)
        payload[field] = value
        run_authenticated(
            self, self.service, self.tasks, self.history, dev,
            f"accessControl.setOne({field})",
            lambda c: c.set_access_control_item(payload),
            apply=(lambda _r: self.reload()) if self._ctx.valid(token) else None,
            on_status=on_status, on_last=on_last,
            mutating=True,
            log_detail=f"{record.get('mac', '?')} {field}={value}",
        )

    def _on_bypass(self, record: dict, button: QPushButton) -> None:
        if self._loading or self._reject_stale():
            return
        dlg = BypassDialog(self, selected=list(record.get("bypass") or []))
        if not dlg.exec():
            return
        if self._reject_stale():
            return
        dev = self.service.get_device(self.device.id) or self._device()
        token = self._ctx.token()
        on_status, on_last = self._callbacks(token)
        payload = dict(record)
        payload["bypass"] = dlg.selected_values()
        run_authenticated(
            self, self.service, self.tasks, self.history, dev,
            "accessControl.setOne(bypass)",
            lambda c: c.set_access_control_item(payload),
            apply=lambda _r: self.reload(),
            on_status=on_status, on_last=on_last,
            mutating=True,
            log_detail=f"{record.get('mac', '?')} bypass={payload['bypass']}",
        )

    def _on_switch(self, checked: bool) -> None:
        if self._loading:
            return
        dev = self.service.get_device(self.device.id) or self._device()
        token = self._ctx.token()
        on_status, on_last = self._callbacks(token)
        run_authenticated(
            self, self.service, self.tasks, self.history, dev,
            "accessControl.switch",
            lambda c: c.set_access_control_switch(bool(checked)),
            apply=lambda _r: self.reload(),
            on_status=on_status, on_last=on_last,
            mutating=True,
            log_detail=f"switch={checked}",
        )
