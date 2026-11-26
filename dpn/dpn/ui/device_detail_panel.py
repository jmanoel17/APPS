"""Aba Unidade: estado e acoes do dia a dia.

A configuracao (transporte, agente, identidade, QR, desvinculos) fica na janela
unificada DeviceConfigWizard, aberta por "Configurar" / "Concluir configuracao".
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dpn.models import ActionStatus, Device, IdentityState, NodeType
from dpn.ui.auth_task import run_authenticated
from dpn.ui.context import SelectionContext
from dpn.ui.dialogs import confirm
from dpn.ui.widgets import NoWheelComboBox

_REBOOT_MAX_SECONDS = 900
_REBOOT_MAX_POLLS = 200


class DeviceDetailPanel(QWidget):
    def __init__(self, service, task_runner, history_panel, manager=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.tasks = task_runner
        self.history = history_panel
        self.manager = manager
        self.device: Device | None = None
        self._ctx = SelectionContext()

        self.lbl_nickname = QLabel("—")
        self.lbl_nickname.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.lbl_status = QLabel("sem unidade selecionada")
        self.lbl_address = QLabel("")
        self.lbl_conn = QLabel("conexão: —")
        self.lbl_conn.setWordWrap(True)
        self.lbl_identity = QLabel("ID esperado: —   •   ID observado: —")
        self.lbl_validation = QLabel("")

        self.node_combo = NoWheelComboBox()
        for value in NodeType.ALL:
            self.node_combo.addItem(NodeType.LABELS[value], value)
        self.tunnel_combo = NoWheelComboBox()
        self.tunnel_combo.addItem("—", "")
        self.kill_check = QCheckBox("Kill switch")

        self.exit_summary = QLabel("Túneis: —")
        self.exit_summary.setWordWrap(True)
        self.exit_summary.setStyleSheet("color: #555;")

        self.btn_connect = QPushButton("Atualizar")
        self.btn_finish = QPushButton("Configurar")
        self.btn_apply_mode = QPushButton("Aplicar modo DPN")
        self.btn_reconnect = QPushButton("Reconectar agente (reverse)")
        self.btn_reconnect.setToolTip(
            "Recria o túnel adb reverse (agente → controlador). Útil quando a rede "
            "bloqueia conexões iniciadas pelo aparelho e o agente aparece offline.")
        self.btn_reboot = QPushButton("Reiniciar")

        self.lbl_last = QLabel("Última ação: —")
        self.lbl_last.setWordWrap(True)
        self.lbl_endpoints = QLabel("")
        self.lbl_endpoints.setWordWrap(True)
        self.lbl_endpoints.setStyleSheet("color: #9ca3af;")

        self._build()
        self._connect_signals()

        self._reboot_timer = QTimer(self)
        self._reboot_timer.setInterval(3000)
        self._reboot_timer.timeout.connect(self._poll_reboot)
        self._reboot_dev: Device | None = None
        self._reboot_phase: str | None = None
        self._reboot_deadline = 0.0
        self._reboot_polls = 0
        self._reboot_busy = False

        self.set_device(None)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        header = QGridLayout()
        header.addWidget(self.lbl_nickname, 0, 0)
        header.addWidget(self.lbl_status, 0, 1, Qt.AlignmentFlag.AlignRight)
        header.addWidget(self.lbl_address, 1, 0, 1, 2)
        header.addWidget(self.lbl_conn, 2, 0, 1, 2)
        header.addWidget(self.lbl_identity, 3, 0, 1, 2)
        header.addWidget(self.lbl_validation, 4, 0, 1, 2)
        layout.addLayout(header)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

        node_row = QHBoxLayout()
        node_row.addWidget(QLabel("Modo DPN geral:"))
        node_row.addWidget(self.node_combo, 1)
        node_row.addWidget(self.btn_apply_mode)
        layout.addLayout(node_row)
        tun_row = QHBoxLayout()
        tun_row.addWidget(QLabel("Túnel (modo Full):"))
        tun_row.addWidget(self.tunnel_combo, 1)
        tun_row.addWidget(self.kill_check)
        layout.addLayout(tun_row)

        acts = QHBoxLayout()
        acts.addWidget(self.btn_connect)
        acts.addWidget(self.btn_finish)
        acts.addWidget(self.btn_reboot)
        layout.addLayout(acts)
        reconnect_row = QHBoxLayout()
        reconnect_row.addWidget(self.btn_reconnect)
        reconnect_row.addStretch(1)
        layout.addLayout(reconnect_row)

        layout.addWidget(self.exit_summary)
        layout.addWidget(self.lbl_last)
        layout.addWidget(self.lbl_endpoints)
        layout.addStretch(1)

    def _connect_signals(self) -> None:
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_finish.clicked.connect(self._on_finish)
        self.btn_apply_mode.clicked.connect(self._on_apply_mode)
        self.btn_reconnect.clicked.connect(self._on_reconnect)
        self.btn_reboot.clicked.connect(self._on_reboot)
        self.node_combo.currentIndexChanged.connect(self._on_mode_changed)

    # ------------------------------------------------------------------ estado
    def set_device(self, device: Device | None) -> None:
        self.device = device
        if device is None:
            self._ctx.select(None, None)
        else:
            self._ctx.select(device.id,
                             (device.transport_mode,
                              self.service._resolve_agent_id(device)))
        enabled = device is not None
        for w in (self.node_combo, self.tunnel_combo, self.kill_check,
                  self.btn_connect, self.btn_finish, self.btn_apply_mode,
                  self.btn_reboot):
            w.setEnabled(enabled)
        self._update_reconnect_enabled()
        if device is None:
            self.lbl_nickname.setText("—")
            self.lbl_status.setText("sem unidade selecionada")
            self.lbl_address.setText("")
            self.lbl_conn.setText("conexão: —")
            self.lbl_identity.setText("ID esperado: —   •   ID observado: —")
            self.lbl_validation.setText("")
            self.exit_summary.setText("Túneis: —")
            self.lbl_endpoints.setText("")
        else:
            self.lbl_nickname.setText(device.nickname)
            self.lbl_address.setText(device.address)
            self.lbl_status.setText("aguardando consulta")
            self._render(device)
        self._refresh_endpoint_flags()
        if device is not None and device.id is not None and (
                device.transport_mode == "agent" or self.service.has_password(device.id)):
            self._on_connect()

    def _render(self, device: Device) -> None:
        self._render_conn(device)
        self._render_identity(device)
        self._render_finish_label(device)

    def _render_finish_label(self, device: Device) -> None:
        pending = (not self.service.valid_id(device.expected_dpn_id)
                   or not self.service.valid_id(device.last_observed_id))
        self.btn_finish.setText("Concluir configuração" if pending else "Configurar")

    def _render_conn(self, device: Device) -> None:
        if device.transport_mode == "direct":
            self.lbl_conn.setText(
                f"Transp. direto   •   Gerenciamento AtomOS: {device.address}")
            return
        android = device.android_serial or "não identificado"
        aid = self.service._resolve_agent_id(device)
        if not aid:
            self.lbl_conn.setText(
                f"Android selecionado: {android}   •   sem agente associado")
            return
        rt = self.manager.runtime(aid) if self.manager is not None else None
        online = "online" if (rt and rt.online) else "offline"
        atomos = (rt.atomos_status if rt and rt.atomos_status else "—")
        observed = (rt.observed_ip if rt and rt.observed_ip else "—")
        reverse = " (conexão encaminhada via adb reverse)" \
            if rt and rt.observed_ip == "127.0.0.1" else ""
        self.lbl_conn.setText(
            f"Android selecionado: {android}   •   Gerenciamento AtomOS: {device.address}\n"
            f"controlador: {online}   •   AtomOS: {atomos}   •   remoto observado: "
            f"{observed}{reverse}")

    def _render_identity(self, device: Device) -> None:
        exp = device.expected_dpn_id
        obs = device.last_observed_id
        if self.service.valid_id(exp) and exp == obs:
            self.lbl_identity.setText(f"Identidade validada: {exp}")
        else:
            self.lbl_identity.setText(
                f"ID esperado: {exp or '—'}   •   ID observado: {obs or '—'}")
        state = self.service.identity_state(device)
        self.lbl_validation.setText(f"Validação: {state}")
        if state == IdentityState.ID_VALIDATED:
            css = "color: #15803d; font-weight: bold;"
        elif state == IdentityState.CONFLICT:
            css = "color: #be185d; font-weight: bold;"
        elif state in (IdentityState.ID_MISMATCH, IdentityState.AUTH_FAILED):
            css = "color: #b91c1c; font-weight: bold;"
        elif state in (IdentityState.AGENT_UNAVAILABLE, IdentityState.ATOMOS_UNREACHABLE):
            css = "color: #b45309;"
        elif state == IdentityState.AGENT_CONNECTED:
            css = "color: #2563eb;"
        else:
            css = "color: #6b7280;"
        self.lbl_validation.setStyleSheet(css)

    def refresh_agent(self) -> None:
        if self.device is not None:
            fresh = self.service.get_device(self.device.id) or self.device
            self.device = fresh
            self._render(fresh)

    def _refresh_endpoint_flags(self) -> None:
        self.lbl_endpoints.setText(
            "Endpoints confirmados, URLs WSS e fingerprints: veja Configurar → Avançado")

    def _set_status(self, text: str, ok: bool | None = None) -> None:
        self.lbl_status.setText(text)
        if ok is True:
            self.lbl_status.setStyleSheet("color: #15803d;")
        elif ok is False:
            self.lbl_status.setStyleSheet("color: #b91c1c;")
        else:
            self.lbl_status.setStyleSheet("")

    def _set_last(self, text: str) -> None:
        self.lbl_last.setText(f"Última ação: {text}")

    def _device(self) -> Device:
        if self.device is None:
            raise RuntimeError("nenhuma unidade selecionada")
        return self.device

    # ------------------------------------------------------------------ execucao
    def _run(self, action: str, fn=None, apply=None, mutating: bool = False,
             log_detail: str = "ok", log_action: bool = True) -> None:
        # captura o cadastro ORIGINAL (para historico) e um token de contexto
        dev = self.service.get_device(self._device().id) or self._device()
        token = self._ctx.token()

        def on_status(text: str, ok: bool | None = None) -> None:
            if self._ctx.valid(token):
                self._set_status(text, ok)

        def on_last(text: str) -> None:
            if self._ctx.valid(token):
                self._set_last(text)

        def _apply(result) -> None:
            if self._ctx.valid(token) and apply is not None:
                apply(result)

        run_authenticated(
            self, self.service, self.tasks, self.history, dev, action, fn,
            apply=_apply if apply else None, on_status=on_status, on_last=on_last,
            mutating=mutating, log_detail=log_detail, log_action=log_action,
        )

    def _on_mode_changed(self) -> None:
        self.tunnel_combo.setEnabled(self.node_combo.currentData() == NodeType.FULL)

    # ---------------------------------------------------------------- operacoes
    def _on_connect(self) -> None:
        def _safe(fn):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                return {"_error": str(exc)}

        def _fetch(client):
            return {
                "device": _safe(client.get_device_id),
                "info": _safe(client.get_device_info),
                "mode": _safe(client.get_node_type),
                "tunnels": _safe(client.get_exit_ip),
            }

        def _apply(r):
            dev = self._device()
            info = r.get("info") or {}
            if isinstance(info, dict) and "_error" not in info:
                ver = info.get("softwareVersion") or "—"
                self.lbl_address.setText(
                    f"Gerenciamento AtomOS: {dev.address}   •   "
                    f"IP informado pelo AtomOS: {info.get('ip') or '—'}   •   versão {ver}")
            mode = r.get("mode") or {}
            if isinstance(mode, dict) and "_error" not in mode:
                cur = mode.get("dpnMode")
                idx = self.node_combo.findData(cur)
                if idx >= 0:
                    self.node_combo.setCurrentIndex(idx)
                tun = mode.get("fullTunnel") or mode.get("smartTunnel") or ""
                self._reload_tunnels(r.get("tunnels"), tun)
            fresh = self.service.get_device(dev.id)
            if (fresh is not None and self.device is not None
                    and self.device.id == dev.id):
                self.device = fresh
            self._render(self.service.get_device(dev.id) or dev)

        self._run("conectar/atualizar", _fetch, apply=_apply, log_action=False)

    def _reload_tunnels(self, tunnels, selected: str) -> None:
        self.tunnel_combo.blockSignals(True)
        self.tunnel_combo.clear()
        self.tunnel_combo.addItem("—", "")
        names = []
        if isinstance(tunnels, list):
            for t in tunnels:
                if isinstance(t, dict) and t.get("tunnelCode"):
                    from dpn.atomos.regions import country_label
                    label = f"{t['tunnelCode']} - {country_label(t.get('countryCode'))}"
                    self.tunnel_combo.addItem(label, t["tunnelCode"])
                    names.append(f"{t['tunnelCode']}={t.get('activeIp', '?')}")
        idx = self.tunnel_combo.findData(selected or "")
        if idx >= 0:
            self.tunnel_combo.setCurrentIndex(idx)
        self.tunnel_combo.blockSignals(False)
        if self.device is not None and self.device.id is not None and isinstance(tunnels, list):
            self.service.set_device_tunnel_ips(
                self.device.id, [t.get("activeIp") for t in tunnels if isinstance(t, dict)])
        self.exit_summary.setText("Túneis (IP ativo): " +
                                  (", ".join(names[:8]) + ("…" if len(names) > 8 else "")
                                   if names else "—"))
        self._on_mode_changed()

    def _on_apply_mode(self) -> None:
        dev = self._device()
        mode = self.node_combo.currentData()
        tunnel = self.tunnel_combo.currentData() or None
        kill = self.kill_check.isChecked()
        if mode == NodeType.FULL and not tunnel:
            QMessageBox.information(self, "Modo DPN", "O modo Full exige um túnel.")
            return
        if not confirm(self, "Aplicar modo DPN",
                       f"Aplicar modo '{mode}' em '{dev.nickname}'"
                       + (f" (túnel {tunnel})" if tunnel else "") + "?",
                       danger=True):
            return
        self._run("set_dpn_mode",
                  lambda c: c.set_node_type(mode, tunnel, kill),
                  apply=lambda r: self._on_connect(), mutating=True,
                  log_detail=f"mode={mode} tunnel={tunnel or '-'} kill={kill}")

    def _on_finish(self) -> None:
        if self.manager is None:
            return
        dev = self._device()
        from dpn.ui.device_config_wizard import DeviceConfigWizard
        dlg = DeviceConfigWizard(self.service, self.manager, dev, self)
        dlg.exec()
        self.set_device(self.service.get_device(dev.id))

    # -------------------------------------------------- reconexao por adb reverse
    def _can_reconnect(self) -> bool:
        dev = self.device
        return bool(
            dev is not None
            and dev.transport_mode == "agent"
            and (dev.android_serial or "").strip()
            and self.manager is not None
        )

    def _update_reconnect_enabled(self) -> None:
        self.btn_reconnect.setEnabled(self._can_reconnect())

    def _on_reconnect(self) -> None:
        dev = self._device()
        serial = (dev.android_serial or "").strip()
        if not self._can_reconnect():
            self._set_status(
                "reconexão por adb só está disponível no modo agente", False)
            return
        port = self.service.config.agent_port
        token = self._ctx.token()
        self.btn_reconnect.setEnabled(False)
        self._set_status(f"recriando túnel adb reverse para {serial}…")
        self._set_last(f"adb reverse {serial} → :{port}")
        self.service.log(dev, "adb_reverse", ActionStatus.OK,
                         f"tentando reverse {serial}:{port}")

        def work():
            from dpn.agent.provision import ensure_adb_reverse
            return ensure_adb_reverse(serial, port)

        def done(res) -> None:
            ok, msg = res if isinstance(res, tuple) else (False, str(res))
            if self._ctx.valid(token):
                if ok:
                    self._set_status(f"túnel adb reverse ativo ({serial})", True)
                    self._set_last(f"adb reverse {serial} → :{port} (ok)")
                else:
                    self._set_status(
                        f"falha no adb reverse: {msg or 'sem detalhes'}", False)
                    self._set_last(f"adb reverse {serial} → :{port} (falhou)")
            self._update_reconnect_enabled()
            if ok:
                # só força um refresh de dados se o agente já estiver online;
                # caso contrário ele reconecta sozinho e a UI atualiza via sinal
                aid = dev.agent_id
                rt = (self.manager.runtime(aid)
                      if self.manager is not None and aid else None)
                if rt is not None and rt.online:
                    self._on_connect()

        def err(exc) -> None:
            if self._ctx.valid(token):
                self._set_status(f"falha no adb reverse: {exc}", False)
            self._update_reconnect_enabled()

        self.tasks.submit(work, on_done=done, on_error=err)

    # ---------------------------------------------------- acompanhamento do reboot
    def _on_reboot(self) -> None:
        dev = self._device()
        if not confirm(self, "Confirmar reinício",
                       f"Reiniciar a unidade '{dev.nickname}' ({dev.address})?\n"
                       "A sessão será perdida e a unidade ficará offline por alguns minutos.",
                       danger=True):
            return
        self._run("reboot", lambda c: c.reboot(), mutating=True,
                  apply=lambda _r: self._start_reboot_tracking(dev),
                  log_detail=dev.address)

    def _start_reboot_tracking(self, dev: Device) -> None:
        self._reboot_dev = dev
        self._reboot_phase = "waiting_offline"
        self._reboot_deadline = time.time() + _REBOOT_MAX_SECONDS
        self._reboot_polls = 0
        self._set_status("reiniciando… aguardando desconexão")
        self._reboot_timer.start()

    def _poll_reboot(self) -> None:
        dev = self._reboot_dev
        if dev is None:
            self._reboot_timer.stop()
            return
        if time.time() > self._reboot_deadline or self._reboot_polls > _REBOOT_MAX_POLLS:
            self._reboot_timer.stop()
            self._reboot_dev = None
            self._set_status("reboot: sem confirmação de volta (verifique)", False)
            return
        if self._reboot_busy:
            return
        self._reboot_busy = True
        self._reboot_polls += 1

        def _fn():
            client = self.service.client_for(dev)
            try:
                return client.check_online()
            finally:
                client.close()

        self.tasks.submit(_fn, on_done=self._reboot_poll_done,
                          on_error=lambda _e: self._reboot_poll_done(False))

    def _reboot_poll_done(self, online: bool) -> None:
        self._reboot_busy = False
        if self._reboot_phase == "waiting_offline":
            if not online:
                self._reboot_phase = "waiting_online"
                self._set_status("offline — aguardando a unidade voltar")
        elif self._reboot_phase == "waiting_online":
            if online:
                self._reboot_phase = None
                self._reboot_dev = None
                self._reboot_timer.stop()
                self._set_status("online (reinicada)", True)
                self._set_last("reboot — unidade voltou online")
                if self.device is not None:
                    self._on_connect()
