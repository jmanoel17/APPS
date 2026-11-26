"""Painel esquerdo: lista de unidades + acoes minimas.

A edicao fica na janela unificada (DeviceConfigWizard), aberta pela aba Unidade
("Configurar") ou por duplo clique na lista.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dpn.agent.protocol import Op
from dpn.models import Device, IdentityState
from dpn.ui.dialogs import confirm

_MONITOR_MIN = 30
_MONITOR_MAX = 600


class DeviceListPanel(QWidget):
    deviceSelected = Signal(object)
    devicesChanged = Signal()

    def __init__(self, service, task_runner, manager=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.tasks = task_runner
        self.manager = manager
        self._devices: list[Device] = []
        self._in_flight: set[int] = set()
        self._fails: dict[int, int] = {}
        self._next_due: dict[int, float] = {}
        self._monitor_interval = int(service.config.monitor_interval)

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        self.list.itemDoubleClicked.connect(lambda _i: self.configure_selected())

        self.btn_add = QPushButton("Adicionar")
        self.btn_remove = QPushButton("Remover")
        self.btn_check = QPushButton("Verificar todas")

        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_remove.clicked.connect(self._on_remove)
        self.btn_check.clicked.connect(self.check_all)

        top = QHBoxLayout()
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_remove)
        top.addStretch(1)
        top.addWidget(self.btn_check)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.lbl_info)

        self.reload()

    # ------------------------------------------------------------------- dados
    def reload(self) -> None:
        """Reconstrucao completa preservando selecao (por ID) e rolagem."""
        sel = self.selected_device_id()
        scroll = self.list.verticalScrollBar().value()
        self._devices = self.service.list_devices()
        self.list.blockSignals(True)
        self.list.clear()
        for d in self._devices:
            item = QListWidgetItem(self._item_text(d))
            item.setData(Qt.ItemDataRole.UserRole, d.id)
            self._apply_color(item, d)
            self.list.addItem(item)
        if sel is not None:
            self._set_current_by_id(sel)
        self.list.blockSignals(False)
        self.list.verticalScrollBar().setValue(scroll)

    def refresh_incremental(self) -> None:
        """Atualiza itens existentes sem reconstruir (heartbeats/status)."""
        scroll = self.list.verticalScrollBar().value()
        current = self.selected_device_id()
        self._devices = self.service.list_devices()
        by_id = {d.id: d for d in self._devices}
        # remove os que sumiram
        for i in reversed(range(self.list.count())):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) not in by_id:
                self.list.takeItem(i)
        # atualiza / insere
        existing = {}
        for i in range(self.list.count()):
            existing[self.list.item(i).data(Qt.ItemDataRole.UserRole)] = i
        for idx, d in enumerate(self._devices):
            if d.id in existing:
                item = self.list.item(existing[d.id])
                item.setText(self._item_text(d))
                self._apply_color(item, d)
            else:
                item = QListWidgetItem(self._item_text(d))
                item.setData(Qt.ItemDataRole.UserRole, d.id)
                self._apply_color(item, d)
                self.list.insertItem(min(idx, self.list.count()), item)
        if current is not None:
            self._set_current_by_id(current)
        self.list.verticalScrollBar().setValue(scroll)

    def _set_current_by_id(self, device_id) -> bool:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == device_id:
                self.list.setCurrentRow(i)
                return True
        return False

    def select_device(self, device_id, emit: bool = True) -> bool:
        if device_id is None:
            self.list.clearSelection()
            self.list.setCurrentItem(None)
            if emit:
                self.deviceSelected.emit(None)
            return True
        ok = self._set_current_by_id(device_id)
        if ok:
            self.list.scrollToItem(self.list.currentItem(),
                                   QAbstractItemView.ScrollHint.EnsureVisible)
            if emit:
                self.deviceSelected.emit(self.selected_device())
        return ok

    def _item_text(self, d: Device) -> str:
        state = self.service.identity_state(d)
        if d.transport_mode == "agent":
            addr = d.android_serial or None
            aid = self.service._resolve_agent_id(d)
            if not addr and aid and self.manager is not None:
                rt = self.manager.runtime(aid)
                addr = rt.observed_ip or rt.local_ip or None
            conn = f"Android: {addr or 'não identificado'}"
        else:
            conn = f"direto: {d.address}"
        return f"{d.nickname}  ({conn})\n{state}"

    def _apply_color(self, item: QListWidgetItem, d: Device) -> None:
        state = self.service.identity_state(d)
        if state == IdentityState.ID_VALIDATED:
            color = QColor(21, 128, 61)
        elif state == IdentityState.CONFLICT:
            color = QColor(190, 24, 93)
        elif state in (IdentityState.ID_MISMATCH, IdentityState.AUTH_FAILED):
            color = QColor(185, 28, 28)
        elif state in (IdentityState.AGENT_UNAVAILABLE, IdentityState.ATOMOS_UNREACHABLE):
            color = QColor(180, 83, 9)
        elif state == IdentityState.AGENT_CONNECTED:
            color = QColor(37, 99, 235)
        else:
            color = QColor(107, 114, 128)
        item.setForeground(color)

    def update_device_display(self, device_id: int) -> None:
        d = self.service.get_device(device_id)   # dados atuais, nao objeto antigo
        if d is None:
            return
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == device_id:
                item.setText(self._item_text(d))
                self._apply_color(item, d)
                return

    def selected_device_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_device(self) -> Device | None:
        did = self.selected_device_id()
        for d in self._devices:
            if d.id == did:
                return d
        return None

    def _on_selection_changed(self) -> None:
        self.deviceSelected.emit(self.selected_device())

    # --------------------------------------------------------------- verificacao
    def check_all(self) -> None:
        for d in self._devices:
            self._check_device(d, force=True)

    def periodic_check(self) -> None:
        now = time.time()
        for d in self._devices:
            if now >= self._next_due.get(d.id, 0):
                self._check_device(d, force=False)

    def _check_device(self, d: Device, force: bool) -> None:
        if d.id in self._in_flight:
            return
        self._in_flight.add(d.id)
        device_id = d.id
        transport = d.transport_mode     # capturado no inicio da consulta

        aid = self.service._resolve_agent_id(d) if transport == "agent" else None
        if transport == "agent" and self.manager is not None and aid:
            def _fn():
                return self.manager.send(aid, Op.GET_DEVICE_ID.value, timeout=15)
        else:
            def _fn():
                client = self.service.client_for(d)
                try:
                    return client.check_online()
                finally:
                    client.close()

        def _finish_ok(result) -> None:
            current = self.service.get_device(device_id)
            # transporte mudou durante a consulta -> nao interpretar o antigo
            if current is None or current.transport_mode != transport:
                self.update_device_display(device_id)
                return
            ok = bool(result.ok) if transport == "agent" and hasattr(result, "ok") \
                else bool(result)
            self._fails[device_id] = 0 if ok else self._fails.get(device_id, 0) + 1
            self._schedule_next(device_id)
            self.update_device_display(device_id)

        def _done(result) -> None:
            self._in_flight.discard(device_id)
            _finish_ok(result)

        def _err(_e) -> None:
            self._in_flight.discard(device_id)
            self._fails[device_id] = self._fails.get(device_id, 0) + 1
            self._schedule_next(device_id)
            self.update_device_display(device_id)

        self.tasks.submit(_fn, on_done=_done, on_error=_err)

    def _schedule_next(self, device_id: int) -> None:
        fails = self._fails.get(device_id, 0)
        interval = min(_MONITOR_MAX, self._monitor_interval * (2 ** min(fails, 4)))
        self._next_due[device_id] = time.time() + interval

    # ------------------------------------------------------------------ acoes
    def _on_add(self) -> None:
        if self.manager is None:
            return
        from dpn.ui.device_config_wizard import DeviceConfigWizard
        dlg = DeviceConfigWizard(self.service, self.manager, None, self)
        if dlg.exec():
            self.reload()
            self.devicesChanged.emit()
            if dlg.device_id is not None:
                self.deviceSelected.emit(self.service.get_device(dlg.device_id))

    def configure_selected(self) -> None:
        dev = self.selected_device()
        if dev is None or self.manager is None:
            return
        from dpn.ui.device_config_wizard import DeviceConfigWizard
        dlg = DeviceConfigWizard(self.service, self.manager, dev, self)
        dlg.exec()
        self.reload()
        self.devicesChanged.emit()
        self.deviceSelected.emit(self.service.get_device(dev.id))

    def _on_remove(self) -> None:
        dev = self.selected_device()
        if dev is None:
            return
        if confirm(self, "Remover unidade",
                   f"Remover '{dev.nickname}'? A senha salva e o pareamento do agente "
                   "serão removidos (o histórico é preservado).", danger=True):
            self.service.delete_device(dev.id)
            self.reload()
            self.devicesChanged.emit()
            self.deviceSelected.emit(None)
