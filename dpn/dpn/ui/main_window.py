"""Janela principal."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtWidgets import QLabel

from dpn.models import Device
from dpn.ui.access_control_panel import AccessControlPanel
from dpn.ui.device_detail_panel import DeviceDetailPanel
from dpn.ui.device_list_panel import DeviceListPanel
from dpn.ui.history_panel import HistoryPanel
from dpn.ui.tunnels_panel import TunnelsPanel


class MainWindow(QMainWindow):
    def __init__(self, service, task_runner, manager=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.tasks = task_runner
        self.manager = manager
        self.selected_device_id: int | None = None
        self.setWindowTitle("DPN Control")
        self.resize(1240, 780)

        self.history = HistoryPanel(service)
        self.detail = DeviceDetailPanel(service, task_runner, self.history, manager)
        self.access = AccessControlPanel(service, task_runner, self.history, manager)
        self.tunnels = TunnelsPanel(service, task_runner, self.history, manager)
        self.devices = DeviceListPanel(service, task_runner, manager)

        self.devices.deviceSelected.connect(self._on_device_selected)
        self.devices.devicesChanged.connect(self._on_devices_changed)

        self.header = QLabel("Nenhuma unidade selecionada")
        self.header.setStyleSheet(
            "padding: 6px 10px; background: #1f2937; color: #e5e7eb; "
            "font-weight: bold;")

        tabs = QTabWidget()
        tabs.addTab(self.detail, "Unidade")
        tabs.addTab(self.access, "Access Control")
        tabs.addTab(self.tunnels, "Túneis")

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.header)
        rl.addWidget(tabs, 1)
        rl.addWidget(self.history)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.devices)
        splitter.addWidget(right)
        splitter.setSizes([340, 900])

        self.setCentralWidget(splitter)
        self._on_devices_changed()

        if manager is not None:
            manager.agentsChanged.connect(self._on_agents_changed)

        self._monitor = QTimer(self)
        self._monitor.setInterval(5000)
        self._monitor.timeout.connect(self.devices.periodic_check)
        self._monitor.start()

    # ------------------------------------------------------------- selecao
    def _on_device_selected(self, device: Device | None) -> None:
        self.selected_device_id = device.id if device else None
        self.detail.set_device(device)
        self.access.set_device(device)
        self.tunnels.set_device(device)
        self.history.set_device(device.id if device else None)
        self._update_header()

    def _on_devices_changed(self) -> None:
        devices = self.service.list_devices()
        self.history.set_devices(devices)

    def _on_agents_changed(self) -> None:
        # atualizacao INCREMENTAL: nao reconstroi a lista (preserva selecao/foco)
        self.devices.refresh_incremental()
        self.detail.refresh_agent()
        self._update_header()

    def _update_header(self) -> None:
        did = self.selected_device_id
        d = self.service.get_device(did) if did is not None else None
        if d is None:
            self.header.setText("Nenhuma unidade selecionada")
            return
        if d.transport_mode == "agent":
            addr = d.android_serial or None
            aid = self.service._resolve_agent_id(d)
            reverse = False
            if aid and self.manager is not None:
                rt = self.manager.runtime(aid)
                if rt.observed_ip and not addr:
                    addr = rt.observed_ip
                reverse = (rt.observed_ip == "127.0.0.1")
            transport = "Via agente" + (" (adb reverse)" if reverse else "")
            left = f"{d.nickname} | Android: {addr or 'não identificado'} | {transport}"
        else:
            left = f"{d.nickname} | direto: {d.address}"
        state = self.service.identity_state(d)
        self.header.setText(f"{left} | {state}")

    def ensure_firewall_async(self) -> None:
        from dpn.agent.firewall import ensure_firewall_rule
        port = self.service.config.agent_port

        def work():
            return ensure_firewall_rule(port, data_dir=self.service.config.data_dir)

        def done(res):
            ok, msg = res
            try:
                self.statusBar().showMessage(f"Firewall (porta {port}): {msg}", 20000)
            except Exception:
                pass
            if not ok:
                QMessageBox.warning(
                    self, "Firewall",
                    f"Não foi possível liberar automaticamente a porta {port}.\n{msg}")

        self.tasks.submit(work, on_done=done, on_error=lambda e: None)

    def notify_agent_server_unavailable(self, detail: str = "") -> None:
        """Avisa que a porta do agente nao abriu (provavel 2a instancia aberta)."""
        port = self.service.config.agent_port
        msg = (
            f"Não foi possível abrir a porta {port} do servidor de agentes.\n\n"
            "Isso geralmente acontece quando já existe outra instância do DPN "
            "Control aberta (ou outro programa usando essa porta). Enquanto isso, "
            "os agentes aparecerão OFFLINE nesta janela.\n\n"
            "Feche a outra instância e abra novamente."
        )
        if detail:
            msg += f"\n\nDetalhe: {detail}"
        try:
            self.statusBar().showMessage(
                f"Servidor de agentes indisponível (porta {port}).", 30000)
        except Exception:
            pass
        try:
            self.header.setText(
                f"ATENÇÃO: porta {port} em uso — verifique outra instância aberta")
        except Exception:
            pass
        QMessageBox.warning(self, "Servidor de agentes", msg)

    def closeEvent(self, event) -> None:
        # ordem deterministica de encerramento
        self._monitor.stop()
        # invalida callbacks de UI dos paineis (respostas atrasadas descartadas)
        for panel in (self.detail, self.access, self.tunnels):
            try:
                panel._ctx.select(None, None)
            except Exception:
                pass
        # para o servidor de agentes antes de fechar sessoes/banco
        if self.manager is not None:
            try:
                self.manager.stop()
            except Exception:
                pass
        # espera limitada pelos workers em voo (nao congela a UI)
        try:
            self.tasks.wait_for_done(2500)
        except Exception:
            pass
        self.service.close()
        super().closeEvent(event)
