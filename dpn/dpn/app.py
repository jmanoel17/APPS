"""Bootstrap da aplicacao desktop."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from dpn.agent.manager import AgentManager
from dpn.config import Config
from dpn.service import DPNService
from dpn.ui.main_window import MainWindow
from dpn.workers import TaskRunner


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv
    config = Config.load()
    app = QApplication(argv)
    app.setApplicationName("DPN Control")

    service = DPNService(config)
    manager = AgentManager(service)
    service.attach_agent_manager(manager)
    server_ok = manager.start()

    runner = TaskRunner()
    window = MainWindow(service, runner, manager)
    window.show()
    window.ensure_firewall_async()   # libera a porta do agente automaticamente
    if not server_ok:
        window.notify_agent_server_unavailable(manager.last_server_error)

    code = app.exec()
    manager.stop()
    service.close()
    return code
