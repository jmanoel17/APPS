"""Painel de historico local de acoes (sem segredos)."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dpn.models import ActionLog, Device


class HistoryPanel(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._device_id: int | None = None

        self.filter = QComboBox()
        self.filter.addItem("Todas as unidades", None)
        self.filter.currentIndexChanged.connect(self._on_filter_changed)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Quando", "Unidade", "Ação", "Resultado"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)

        top = QHBoxLayout()
        top.addWidget(QLabel("Histórico:"))
        top.addWidget(self.filter)
        top.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)

    def set_devices(self, devices: list[Device]) -> None:
        current = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("Todas as unidades", None)
        for d in devices:
            self.filter.addItem(d.nickname, d.id)
        idx = self.filter.findData(current)
        self.filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.filter.blockSignals(False)

    def set_device(self, device_id: int | None) -> None:
        self._device_id = device_id
        idx = self.filter.findData(device_id)
        if idx >= 0:
            self.filter.blockSignals(True)
            self.filter.setCurrentIndex(idx)
            self.filter.blockSignals(False)
        self.refresh()

    def _on_filter_changed(self) -> None:
        self._device_id = self.filter.currentData()
        self.refresh()

    def refresh(self) -> None:
        logs: list[ActionLog] = self.service.history(self._device_id, limit=300)
        self.table.setRowCount(0)
        for log in logs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            ts = datetime.fromtimestamp(log.ts).strftime("%d/%m/%Y %H:%M:%S")
            self.table.setItem(row, 0, QTableWidgetItem(ts))
            self.table.setItem(row, 1, QTableWidgetItem(log.device_nickname))
            self.table.setItem(row, 2, QTableWidgetItem(log.action))
            item = QTableWidgetItem(log.message)
            if log.status != "ok":
                item.setForeground(Qt.GlobalColor.darkRed)
            self.table.setItem(row, 3, item)
        self.table.resizeColumnsToContents()
