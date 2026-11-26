"""Widgets auxiliares com comportamento de scroll seguro.

QComboBox/QSpinBox mudam de valor com a roda do mouse por padrao; dentro de
tabelas isso faz o usuario alterar valores sem querer ao rolar a lista.
Aqui a roda so age quando o dropdown esta aberto (ou o spin tem foco).
"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QSpinBox


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
