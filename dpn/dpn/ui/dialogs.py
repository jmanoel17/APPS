"""Dialogos auxiliares: confirmacao e bypass do HTTPS filter.

O cadastro/configuracao agora vive em dpn/ui/device_config_wizard.py (janela
unica). Este modulo mantem apenas utilitarios compartilhados.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QVBoxLayout,
)


def confirm(parent, title: str, text: str, danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    ok = box.addButton("Confirmar", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(ok)
    box.exec()
    return box.clickedButton() is ok


class BypassDialog(QDialog):
    """Multi-selecao simples das opcoes de bypass do HTTPS filter."""

    OPTIONS = (("youtube", "YouTube"), ("spotify", "Spotify"))

    def __init__(self, parent=None, selected: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Bypass do HTTPS filter")
        selected = selected or []
        self._boxes: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        for key, label in self.OPTIONS:
            cb = QCheckBox(label)
            cb.setChecked(key in selected)
            self._boxes[key] = cb
            layout.addWidget(cb)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_values(self) -> list[str]:
        return [k for k, cb in self._boxes.items() if cb.isChecked()]
