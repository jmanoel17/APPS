"""Janela unica de configuracao de uma DPN (Adicionar / Configurar / Concluir).

Reune cadastro, transporte, selecao de dispositivo Android, credencial unica,
verificacao e conclusao do vinculo numa so janela. Reutilizada pela lista
(Adicionar / Configurar) e pela aba Unidade (Concluir configuracao).
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dpn.agent.protocol import Op
from dpn.agent.provision import (
    android_ip_from_serial,
    build_install_args,
    build_provision_args,
    controller_wss_url,
    default_apk_path,
    devices_with_reference,
    rank_controller_addresses,
    run_adb,
)
from dpn.agent.qr import qr_matrix, qr_payload
from dpn.models import Device, TLSMode
from dpn.ui.dialogs import confirm
from dpn.ui.widgets import NoWheelComboBox
from dpn.workers import TaskRunner

_ACTIVE_STATES = ("device",)


@dataclass(frozen=True)
class _ConfigPlan:
    """Snapshot imutavel dos dados necessarios para configurar (sem tocar UI)."""

    operation_id: int
    device_id: int
    transport_mode: str
    selected_serial: str | None
    existing_agent_id: str | None
    atomos_url: str
    port: int
    fingerprint: str
    association_name: str
    password: str = field(default="", repr=False)   # apenas em memoria
    use_reverse: bool = False
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)


def qr_pixmap(payload: str, size: int = 240) -> QPixmap:
    m = qr_matrix(payload)
    n = len(m)
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    cell = size / n
    p.setBrush(QColor("black"))
    p.setPen(Qt.PenStyle.NoPen)
    for y, row in enumerate(m):
        for x, val in enumerate(row):
            if val:
                p.drawRect(int(x * cell), int(y * cell), int(cell) + 1, int(cell) + 1)
    p.end()
    return QPixmap.fromImage(img)


class _AndroidPicker(QDialog):
    """Lista pesquisavel de dispositivos ADB (selecao explicita)."""

    def __init__(self, service, mac_reference: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Selecionar dispositivo Android")
        self.resize(560, 420)
        self.service = service
        self.mac_reference = mac_reference
        self.serial: str | None = None

        self.search = QLineEdit()
        self.search.setPlaceholderText("filtrar por serial, modelo ou IP…")
        self.search.textChanged.connect(self._filter)
        self.btn_reload = QPushButton("Atualizar lista")
        self.btn_reload.clicked.connect(self._reload)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._accept())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.btn_reload)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        lay.addWidget(buttons)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        try:
            res = devices_with_reference(self.mac_reference)
        except Exception:
            res = {"devices": [], "reference_ip": None}
        associated = {a.device_id for a in self.service.list_agents()
                      if a.device_id is not None}
        for d in res.get("devices", []):
            serial = d.get("serial", "")
            ip = d.get("ip") or "—"
            state = d.get("state", "")
            model = d.get("model", "") or ""
            tags = []
            if state not in _ACTIVE_STATES:
                tags.append(f"ADB:{state or '?'}")   # exige autorizacao
            if d.get("matches_reference"):
                tags.append("MAC ref.")
            item = QListWidgetItem(f"{model or 'Android'}   {serial}   [{ip}]  "
                                   + " ".join(tags))
            item.setData(Qt.ItemDataRole.UserRole, serial)
            self.list.addItem(item)

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(text) and text not in it.text().lower())

    def _accept(self) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        self.serial = it.data(Qt.ItemDataRole.UserRole)
        self.accept()


class DeviceConfigWizard(QDialog):
    def __init__(self, service, manager, device: Device | None = None, parent=None):
        super().__init__(parent)
        self.service = service
        self.manager = manager
        self.device = device
        self.device_id = device.id if device else None
        self.tasks = TaskRunner(self)
        self._gen = 0
        self._running = False
        self._closed = False
        self._cancel = threading.Event()
        self.selected_serial: str | None = None
        self._agent_id = device.agent_id if device else None
        if self._agent_id is None and device is not None:
            self._agent_id = service._resolve_agent_id(device)

        self.setWindowTitle("Configurar dispositivo" if device else "Adicionar dispositivo")
        self.resize(700, 720)
        self._build()
        self._load()
        self._on_transport_changed()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        # --- identificacao ---
        self.nickname = QLineEdit()
        self.nickname.setPlaceholderText("ex.: DPN 1")
        self.mac = QLineEdit()
        self.mac.setPlaceholderText("opcional (referência)")

        idform = QFormLayout()
        idform.addRow("Nome da DPN *", self.nickname)
        idform.addRow("MAC de referência", self.mac)
        id_box = QGroupBox("Identificação")
        id_box.setLayout(idform)

        # --- transporte / conexao ---
        self.transport = NoWheelComboBox()
        self.transport.addItem("Conexão direta", "direct")
        self.transport.addItem("Agente Android", "agent")
        self.transport.currentIndexChanged.connect(self._on_transport_changed)

        self.atomos = QLineEdit("https://34.34.34.34")
        self.tls = NoWheelComboBox()
        self.tls.addItem("Verificar (sistema)", TLSMode.VERIFY.value)
        self.tls.addItem("Usar certificado da unidade", TLSMode.CA_BUNDLE.value)
        self.tls.addItem("Não verificar (autoassinado)", TLSMode.INSECURE.value)
        # unidades Deeper usam certificado autoassinado: default seguro-pratico
        self.tls.setCurrentIndex(self.tls.findData(TLSMode.INSECURE.value))
        self.ca = QLineEdit()
        self.ca.setPlaceholderText("caminho do .crt/.pem")
        self.btn_ca = QPushButton("…")
        self.btn_ca.clicked.connect(self._pick_ca)
        self.btn_fetch_cert = QPushButton("Obter da unidade")
        self.btn_fetch_cert.clicked.connect(self._fetch_cert)
        self.username = QLineEdit("admin")

        self.android = QLineEdit()
        self.android.setReadOnly(True)
        self.android.setPlaceholderText("nenhum dispositivo selecionado")
        self.btn_pick = QPushButton("Selecionar…")
        self.btn_pick.clicked.connect(self._pick_android)
        self.btn_refresh_dev = QPushButton("Atualizar")
        self.btn_refresh_dev.clicked.connect(self._refresh_android_hint)
        self.lbl_android = QLabel("")
        self.lbl_android.setWordWrap(True)
        self.lbl_android.setStyleSheet("color: #6b7280;")
        self.chk_reverse = QCheckBox(
            "Permitir fallback por ADB reverse (se a rede bloquear)")
        self.chk_reverse.setChecked(True)
        self.chk_reverse.setToolTip(
            "Tenta primeiro por WSS na rede; usa o encaminhamento ADB somente "
            "se a rede nao for alcancavel. A conexao por reverse depende do ADB.")

        self.wss = QLineEdit()
        self.wss.setReadOnly(True)

        # --- senha unica ---
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.textChanged.connect(lambda _t: self._clear_pw_error())
        self.chk_show = QCheckBox("Mostrar")
        self.chk_show.toggled.connect(
            lambda on: self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        self.lbl_pw = QLabel("")
        self.lbl_pw.setStyleSheet("color: #6b7280;")
        self.lbl_pw_err = QLabel("")
        self.lbl_pw_err.setStyleSheet("color: #b91c1c;")
        self.lbl_pw_err.setWordWrap(True)

        connform = QFormLayout()
        connform.addRow("Transporte", self.transport)
        connform.addRow("Endereço de gerenciamento AtomOS", self.atomos)
        connform.addRow("TLS", self.tls)
        ca_row = QHBoxLayout()
        ca_row.addWidget(self.ca)
        ca_row.addWidget(self.btn_ca)
        ca_row.addWidget(self.btn_fetch_cert)
        ca_w = QWidget()
        ca_w.setLayout(ca_row)
        connform.addRow("Certificado", ca_w)
        connform.addRow("Usuário", self.username)
        and_row = QHBoxLayout()
        and_row.addWidget(self.android, 1)
        and_row.addWidget(self.btn_pick)
        and_row.addWidget(self.btn_refresh_dev)
        and_w = QWidget()
        and_w.setLayout(and_row)
        self.row_android = and_w
        connform.addRow("Dispositivo Android", and_w)
        connform.addRow("", self.lbl_android)
        connform.addRow("", self.chk_reverse)
        connform.addRow("Controlador (WSS)", self.wss)
        self._conn_form = connform
        conn_box = QGroupBox("Conexão")
        conn_box.setLayout(connform)

        pw_row = QHBoxLayout()
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(self.chk_show)
        pw_w = QWidget()
        pw_w.setLayout(pw_row)
        pwform = QFormLayout()
        pwform.addRow("Senha da DPN", pw_w)
        pwform.addRow("", self.lbl_pw)
        pwform.addRow("", self.lbl_pw_err)
        pw_box = QGroupBox("Credencial")
        pw_box.setLayout(pwform)

        # --- avançado ---
        self.btn_qr = QPushButton("Mostrar QR (alternativa)")
        self.btn_qr.clicked.connect(self._show_qr)
        self.btn_unlink = QPushButton("Desvincular identidade")
        self.btn_unlink.clicked.connect(self._advanced_unlink)
        self.btn_unassign = QPushButton("Desvincular agente")
        self.btn_unassign.clicked.connect(self._advanced_unassign)
        self.btn_delete_agent = QPushButton("Remover agente")
        self.btn_delete_agent.clicked.connect(self._advanced_delete_agent)
        self.btn_diag = QPushButton("Diagnóstico ARP/ADB")
        self.btn_diag.clicked.connect(self._advanced_diag)
        self.adv_status = QLabel("")
        self.adv_status.setWordWrap(True)
        adv_lay = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(self.btn_qr)
        row.addWidget(self.btn_unlink)
        adv_lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_unassign)
        row2.addWidget(self.btn_delete_agent)
        adv_lay.addLayout(row2)
        adv_lay.addWidget(self.btn_diag)
        adv_lay.addWidget(self.adv_status)
        self.adv_box = QGroupBox("Avançado")
        self.adv_box.setCheckable(True)
        self.adv_box.setChecked(False)
        self.adv_box.setLayout(adv_lay)
        self.adv_box.toggled.connect(lambda on: [
            w.setVisible(on) for w in (self.btn_qr, self.btn_unlink, self.btn_unassign,
                                       self.btn_delete_agent, self.btn_diag,
                                       self.adv_status)])
        for w in (self.btn_qr, self.btn_unlink, self.btn_unassign,
                  self.btn_delete_agent, self.btn_diag, self.adv_status):
            w.setVisible(False)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)

        self.btn_config = QPushButton("Configurar")
        self.btn_config.clicked.connect(self._on_configure)
        self.btn_close = QPushButton("Fechar")
        self.btn_close.clicked.connect(self.reject)
        btns = QHBoxLayout()
        btns.addWidget(self.btn_config)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)

        lay = QVBoxLayout(self)
        lay.addWidget(id_box)
        lay.addWidget(conn_box)
        lay.addWidget(pw_box)
        lay.addWidget(self.adv_box)
        lay.addWidget(self.log)
        lay.addLayout(btns)

    # ------------------------------------------------------------------- dados
    def _load(self) -> None:
        try:
            self.wss.setText(
                f"wss://<IP-deste-PC>:{self.service.config.agent_port}  "
                f"(fingerprint {self.manager.fingerprint[:16] if self.manager else '—'}…)")
        except Exception:
            pass
        if self.device is None:
            self._update_pw_hint()
            return
        d = self.device
        self.nickname.setText(d.nickname)
        self.mac.setText(d.mac_reference or "")
        self.atomos.setText(d.address or "https://34.34.34.34")
        self.username.setText(d.username or "admin")
        ti = self.transport.findData(d.transport_mode)
        if ti >= 0:
            self.transport.setCurrentIndex(ti)
        tls_i = self.tls.findData(d.tls_mode.value)
        if tls_i >= 0:
            self.tls.setCurrentIndex(tls_i)
        self.ca.setText(d.ca_bundle_path or "")
        if d.android_serial:
            self.android.setText(d.android_serial)
            self.selected_serial = d.android_serial
        elif self._agent_id:
            self.android.setText(self._agent_id[:12] + " (já configurado)")
        if self._agent_id:
            a = self.service.get_agent(self._agent_id)
            if a:
                self.lbl_android.setText(f"agente {a.name or 'Android'} já associado a esta DPN")
        self._update_pw_hint()
        self._on_transport_changed()

    def _update_pw_hint(self) -> None:
        has_direct = bool(self.device and self.device.id is not None
                          and self.service.has_password(self.device.id))
        has_agent = bool(self._agent_id)
        if self.device is None:
            self.lbl_pw.setText("Informe a senha admin do AtomOS.")
        elif (self.device.transport_mode == "agent" and has_agent) or has_direct:
            self.lbl_pw.setText("Senha já configurada. Preencha apenas para substituir.")
        else:
            self.lbl_pw.setText("Informe a senha admin do AtomOS.")

    def _on_transport_changed(self) -> None:
        agent = self.transport.currentData() == "agent"
        # esconder controles do outro modo
        self.atomos.setVisible(not agent)
        self.tls.setVisible(not agent)
        self.ca.setVisible(not agent)
        self.btn_ca.setVisible(not agent)
        self.btn_fetch_cert.setVisible(not agent)
        self.username.setVisible(not agent)
        self.row_android.setVisible(agent)
        self.lbl_android.setVisible(agent)
        self.chk_reverse.setVisible(agent)
        # ajusta labels do form
        for r in range(self._conn_form.rowCount()):
            item = self._conn_form.itemAt(r, QFormLayout.ItemRole.LabelRole)
            if item and item.widget():
                lbl = item.widget()
                txt = lbl.text()
                if "Endereço de gerenciamento" in txt or "TLS" in txt or "Certificado" in txt or "Usuário" in txt:
                    lbl.setVisible(not agent)
        if not agent and self.tls.currentData() == TLSMode.VERIFY.value:
            # unidades usam certificado autoassinado: evita falha de verificacao
            self.tls.setCurrentIndex(self.tls.findData(TLSMode.INSECURE.value))
        self._update_pw_hint()

    def _pick_ca(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Certificado da unidade", "", "Certificados (*.crt *.pem *.cer);;Todos (*.*)")
        if path:
            self.ca.setText(path)

    def _fetch_cert(self) -> None:
        from urllib.parse import urlparse
        from dpn.atomos.tls import fetch_server_cert
        raw = self.atomos.text().strip() or "https://34.34.34.34"
        if "://" not in raw:
            raw = "https://" + raw
        u = urlparse(raw)
        host = u.hostname or "34.34.34.34"
        port = u.port or 443
        pem = fetch_server_cert(host, port, timeout=6)
        if not pem:
            QMessageBox.warning(self, "Certificado",
                                f"Não foi possível obter o certificado de {host}:{port}.")
            return
        path = os.path.join(self.service.config.data_dir, f"dpn-{host}.pem")
        try:
            os.makedirs(self.service.config.data_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(pem)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Certificado", f"Falha ao salvar: {exc}")
            return
        self.ca.setText(path)
        self.tls.setCurrentIndex(self.tls.findData(TLSMode.CA_BUNDLE.value))
        QMessageBox.information(
            self, "Certificado",
            "Certificado obtido e vinculado (TLS = certificado da unidade).")

    def _pick_android(self) -> None:
        dlg = _AndroidPicker(self.service, self.mac.text().strip(), self)
        if dlg.exec():
            self.selected_serial = dlg.serial
            self.android.setText(self.selected_serial or "")
            self.lbl_android.setText(
                "dispositivo selecionado — a instalação ocorre ao clicar em 'Configurar'")

    def _refresh_android_hint(self) -> None:
        self.lbl_android.setText("atualize a lista no seletor 'Selecionar…'")

    # --------------------------------------------------------------- avançado
    def _show_qr(self) -> None:
        if self.manager is None:
            return
        android_ip = (android_ip_from_serial(self.selected_serial)
                      if self.selected_serial else None)
        addrs = rank_controller_addresses(android_ip)
        if not addrs:
            QMessageBox.warning(self, "QR", "Nenhum endereço de controlador disponível.")
            return
        if android_ip:
            # mesma /24 do Android selecionado: usa sem chutar interfaces
            addr = addrs[0]
        else:
            choice, ok = QInputDialog.getItem(
                self, "Endereço do controlador",
                "Escolha o endereço que o Android alcança:", addrs, 0, False)
            if not ok or not choice:
                return
            addr = choice
        code = self.manager.open_pairing(300)
        payload = qr_payload(controller_wss_url(addr, self.service.config.agent_port),
                             self.manager.fingerprint, code)
        dlg = QDialog(self)
        dlg.setWindowTitle("QR de configuração")
        lay = QVBoxLayout(dlg)
        img = QLabel()
        img.setPixmap(qr_pixmap(payload))
        lay.addWidget(img)
        lay.addWidget(QLabel("No app: 'Ler QR de configuração' (sem senha/token)."))
        dlg.exec()

    def _advanced_unlink(self) -> None:
        if self.device_id is None:
            return
        d = self.service.get_device(self.device_id)
        if d and confirm(self, "Desvincular identidade",
                         f"Remover o vínculo de '{d.nickname}'?", danger=True):
            self.service.unbind_identity(d)
            self._load()

    def _advanced_unassign(self) -> None:
        if self._agent_id and confirm(self, "Desvincular agente",
                                      "Desvincular o agente desta DPN?", danger=True):
            self.service.disassociate_agent(self._agent_id)
            self._agent_id = None
            self.android.clear()
            self.lbl_android.setText("")
            self._update_pw_hint()

    def _advanced_delete_agent(self) -> None:
        if self._agent_id and confirm(self, "Remover agente",
                                      "Remover o pareamento do agente?", danger=True):
            self.service.delete_agent(self._agent_id)
            self._agent_id = None
            self.android.clear()
            self.lbl_android.setText("")
            self._update_pw_hint()

    def _advanced_diag(self) -> None:
        self.adv_status.setText("consultando ARP/ADB…")

        def work():
            return devices_with_reference(self.mac.text().strip())

        def done(res):
            ref = res.get("reference_ip") or "não identificado"
            devs = res.get("devices", [])
            try:
                eps = ", ".join(sorted(k for k, v in
                                       self.service.registry.confirmed_map().items() if v))
            except Exception:
                eps = "—"
            fp = self.manager.fingerprint if self.manager else "—"
            self.adv_status.setText(
                f"MAC→IP (ARP): {ref}   •   dispositivos ADB: {len(devs)}\n"
                f"WSS: {self.wss.text()}\nfingerprint controlador: {fp}\n"
                f"endpoints confirmados: {eps}")

        self.tasks.submit(work, on_done=done,
                          on_error=lambda e: self.adv_status.setText(f"falha: {e}"))

    # ---------------------------------------------------------------- configurar
    def _build_device_from_form(self, base: Device | None) -> Device:
        address = self.atomos.text().strip() or "https://34.34.34.34"
        tls = TLSMode(self.tls.currentData() or TLSMode.VERIFY.value)
        if base is None:
            return Device(nickname=self.nickname.text().strip() or "DPN",
                          address=address, tls_mode=tls,
                          ca_bundle_path=self.ca.text().strip() or None,
                          username=self.username.text().strip() or "admin",
                          mac_reference=self.mac.text().strip(),
                          transport_mode=self.transport.currentData())
        # atualiza apenas campos de configuracao; identidade/agente sao preservados
        base.nickname = self.nickname.text().strip() or base.nickname
        base.address = address
        base.tls_mode = tls
        base.ca_bundle_path = self.ca.text().strip() or None
        base.username = self.username.text().strip() or "admin"
        base.mac_reference = self.mac.text().strip()
        base.transport_mode = self.transport.currentData()
        return base

    def _on_configure(self) -> None:
        if self._running:
            return
        if not self.nickname.text().strip():
            QMessageBox.warning(self, "Nome obrigatório", "Informe o nome da DPN.")
            return
        mode = self.transport.currentData()
        if mode == "agent" and not self._agent_id and not self.selected_serial:
            QMessageBox.warning(self, "Dispositivo Android",
                                "Selecione um dispositivo Android (ou use um agente já configurado).")
            return
        # 1) persiste cadastro/config na thread da UI (rapido)
        if self.device_id is None:
            dev = self.service.add_device(self._build_device_from_form(None))
            self.device_id = dev.id
            self.device = dev
        else:
            base = self._build_device_from_form(self.service.get_device(self.device_id))
            self.service.update_device(base)
            self.device = self.service.get_device(self.device_id)
        pwd = self.password.text()
        if pwd and mode == "direct":
            self.service.save_password(self.device_id, pwd)
        # 2) snapshot IMUTAVEL + worker (sem tocar widgets)
        self._gen += 1
        gen = self._gen
        self._cancel = threading.Event()
        plan = _ConfigPlan(
            operation_id=gen, device_id=self.device_id, transport_mode=mode,
            selected_serial=self.selected_serial,
            existing_agent_id=self._agent_id,
            atomos_url=self.atomos.text().strip() or "https://34.34.34.34",
            port=int(self.service.config.agent_port),
            fingerprint=(self.manager.fingerprint if self.manager else ""),
            association_name=self.nickname.text().strip(),
            password=pwd, use_reverse=self.chk_reverse.isChecked(),
            cancel=self._cancel,
        )
        self._running = True
        self.btn_config.setEnabled(False)
        self._clear_pw_error()
        self._log("Configurando…")
        self.tasks.submit(
            lambda progress: self._work(plan, progress),
            on_done=lambda r: self._done(gen, plan, r),
            on_error=lambda e: self._err(gen, plan, e),
            on_progress=lambda t: self._on_progress(gen, t),
            with_progress=True)

    # ---------------------------------------------------------------- worker
    def _work(self, plan: _ConfigPlan, progress) -> dict:
        """Executa SOMENTE com o snapshot; nunca toca widgets nem le self.*."""
        if plan.cancel.is_set():
            raise RuntimeError("cancelado")
        if plan.transport_mode == "direct":
            d = self.service.get_device(plan.device_id)
            progress("autenticando no AtomOS (direto)…")
            client = self.service.client_for(d)
            try:
                if plan.password:
                    client.login(password=plan.password)
                res = client.get_device_id()
            finally:
                client.close()
            observed = res.get("deviceId") if isinstance(res, dict) else None
            return {"mode": "direct", "observed": observed, "link": "direct"}
        # agente
        agent_id = plan.existing_agent_id
        link = "existing"
        if plan.selected_serial:
            progress("instalando APK no Android…")
            apk = default_apk_path()
            if not os.path.isfile(apk):
                raise RuntimeError(f"APK ausente: {apk}")
            rc, out, err = run_adb(build_install_args(plan.selected_serial, apk),
                                   timeout=300)
            if rc != 0:
                raise RuntimeError(f"instalação falhou: {err.strip() or out.strip()}")
            # tenta pela REDE primeiro; adb reverse como fallback explicito
            android_ip = android_ip_from_serial(plan.selected_serial)
            candidates = [ip for ip in rank_controller_addresses(android_ip)
                          if not ip.startswith("127.")]
            attempts = [("lan", controller_wss_url(ip, plan.port)) for ip in candidates]
            if plan.use_reverse:
                attempts.append(("reverse", f"wss://127.0.0.1:{plan.port}"))
            agent_id, link = self._provision_and_wait(plan, attempts, progress)
        if not agent_id:
            raise RuntimeError("nenhum agente associado")
        if plan.password:
            progress("enviando credencial ao agente…")
            cres = self.manager.configure(agent_id, plan.atomos_url, plan.password,
                                          association_name=plan.association_name)
            if not getattr(cres, "ok", False):
                raise RuntimeError(f"configure recusado: {getattr(cres, 'error', '')}")
        if plan.cancel.is_set():
            raise RuntimeError("cancelado")
        progress("consultando ID físico no agente…")
        res = self.manager.send(agent_id, Op.GET_DEVICE_ID.value, timeout=15)
        observed = res.observed_dpn_id
        if not observed and isinstance(res.data, dict):
            observed = res.data.get("deviceId")
        if not res.ok or not self.service.valid_id(observed):
            raise RuntimeError("não foi possível obter o ID físico")
        return {"mode": "agent", "agent_id": agent_id, "observed": observed,
                "link": link}

    def _provision_and_wait(self, plan: _ConfigPlan, attempts, progress):
        last = None
        for kind, url in attempts:
            if plan.cancel.is_set():
                raise RuntimeError("cancelado")
            if kind == "reverse":
                rc, out, err = run_adb(
                    ["-s", plan.selected_serial, "reverse",
                     f"tcp:{plan.port}", f"tcp:{plan.port}"], timeout=30)
                if rc != 0:
                    last = f"adb reverse falhou: {err.strip() or out.strip()}"
                    continue
            code = self.manager.open_pairing(300)
            progress(f"provisionando ({'rede' if kind == 'lan' else 'adb reverse'}) {url}")
            rc, out, err = run_adb(
                build_provision_args(plan.selected_serial, url, plan.fingerprint,
                                     code, plan.atomos_url), timeout=60)
            if rc != 0:
                last = f"provisionamento falhou: {err.strip() or out.strip()}"
                continue
            deadline = time.time() + (30 if kind == "lan" else 60)
            while time.time() < deadline:
                if plan.cancel.is_set():
                    raise RuntimeError("cancelado")
                aid = self.manager.paired_agent_for(code)
                if aid:
                    return aid, kind
                time.sleep(1)
            last = (f"o agente não conectou por {url} "
                    f"({'rede' if kind == 'lan' else 'adb reverse'})")
        raise RuntimeError(last or "o agente não pareou (rede/firewall)")

    def _on_progress(self, gen: int, text: str) -> None:
        # executado na thread principal; descarta se a operacao foi encerrada
        if gen == self._gen and not self._closed:
            self._log(text)

    def _done(self, gen: int, plan: _ConfigPlan, r: dict) -> None:
        if gen != self._gen or self._closed:
            return   # callback de uma execucao encerrada
        self._running = False
        self.btn_config.setEnabled(True)
        if plan.selected_serial:
            self.service.set_device_serial(plan.device_id, plan.selected_serial)
        if r.get("mode") == "agent" and r.get("agent_id"):
            self._agent_id = r["agent_id"]        # atualiza estado na thread da UI
        observed = r.get("observed")
        if r.get("mode") == "agent":
            self.service.finish_agent_setup(
                plan.device_id, r["agent_id"], observed, transport="agent", bind=False)
        else:
            self.service.set_observed_identity(plan.device_id, observed, origin="direct")
        self._log(f"ID observado: {observed or '—'}")
        link = r.get("link")
        if link == "lan":
            self._log("conexão: pela rede (WSS LAN)")
        elif link == "reverse":
            self._log("conexão: temporária via ADB reverse (depende do ADB)")
        dev = self.service.get_device(plan.device_id)
        if observed and dev and not dev.expected_dpn_id:
            if confirm(self, "Concluir configuração",
                       f"Vincular o ID observado '{observed}' a '{dev.nickname}'?"):
                if self.service.bind_identity(self.service.get_device(plan.device_id)):
                    self._log("Identidade vinculada.")
        dev = self.service.get_device(plan.device_id)
        bound = bool(dev and self.service.valid_id(dev.expected_dpn_id)
                     and dev.expected_dpn_id == dev.last_observed_id)
        self._load()
        if self._closed:
            return
        if bound:
            extra = ("\nConexão temporária via ADB reverse: depende do ADB ligado."
                     if link == "reverse" else "")
            QMessageBox.information(self, "Pronto",
                                    "Dispositivo configurado e vinculado." + extra)
        else:
            QMessageBox.warning(self, "Configuração pendente",
                                "Salvo, mas o vínculo da identidade ainda não foi concluído.")
        self.accept()

    def _err(self, gen: int, plan: _ConfigPlan, exc) -> None:
        if gen != self._gen or self._closed:
            return
        self._running = False
        self.btn_config.setEnabled(True)
        msg = str(exc)
        low = msg.lower()
        if "cancelad" in low:
            self._log("Operação cancelada; configuração permanece pendente.")
            return
        if ("certificate" in low or "ssl" in low or "tls" in low
                or "issuer" in low):
            self.lbl_pw_err.setText(
                f"TLS: {msg}\n→ escolha 'Não verificar (autoassinado)' ou clique em "
                "'Obter da unidade'.")
        elif ("autentic" in low or "401" in low or "autorizado" in low
                or "senha" in low):
            self.lbl_pw_err.setText(f"Falha de autenticação: {msg}")
        else:
            self.lbl_pw_err.setText(msg)
        self._log(f"ERRO: {msg}")
        self._load()
        # configuração fica salva como pendente (nao duplica cadastro)

    def _clear_pw_error(self) -> None:
        self.lbl_pw_err.setText("")

    def _log(self, text: str) -> None:
        self.log.appendPlainText(text)

    # -------------------------------------------------------------- encerramento
    def _shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._gen += 1                 # invalida callbacks pendentes
        try:
            self._cancel.set()          # cancelamento cooperativo
        except Exception:
            pass

    def done(self, r: int) -> None:      # cobre accept(), reject() e Esc
        self._shutdown()
        super().done(r)

    def closeEvent(self, event) -> None:
        self._shutdown()                # cobre o X
        super().closeEvent(event)
