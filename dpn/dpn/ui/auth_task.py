"""Execucao autenticada de tarefas de UI (login + operacao na MESMA sessao).

Para unidades com agente Android, a senha NAO fica no controlador (fica no
Keystore do agente); o login ocorre no agente. Antes de operacoes mutaveis,
bloqueia quando a identidade/agente nao permitem.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QInputDialog, QLineEdit

from dpn.atomos.errors import (
    AuthError,
    EndpointUnconfirmedError,
    IdentityMismatchError,
    UncertainError,
)
from dpn.models import ActionStatus
from dpn.security import sanitize_error
from dpn.ui.dialogs import confirm


def ensure_password(parent, service, device) -> str | None:
    if device.transport_mode == "agent":
        return ""  # credenciais ficam no agente (Android Keystore)
    if device.id is not None and service.has_password(device.id):
        return service.get_password(device.id)
    pwd, ok = QInputDialog.getText(
        parent, "Senha", f"Senha para '{device.nickname}':",
        QLineEdit.EchoMode.Password,
    )
    if not ok or not pwd:
        return None
    if device.id is not None and confirm(
        parent, "Guardar senha", "Guardar esta senha no armazenamento seguro?"
    ):
        service.save_password(device.id, pwd)
    return pwd


def run_authenticated(
    parent,
    service,
    tasks,
    history,
    device,
    action: str,
    fn: Callable | None = None,
    apply: Callable | None = None,
    on_status: Callable | None = None,
    on_last: Callable | None = None,
    on_finish: Callable | None = None,
    log_action: bool = True,
    log_detail: str = "ok",
    mutating: bool = False,
) -> None:
    """Roda `fn(client)` apos login. `fn=None` => so o login/checagem.

    `on_finish` e chamado SEMPRE ao terminar (sucesso, erro ou cancelamento de
    senha) — usar para reabilitar a UI.
    """
    pwd = ensure_password(parent, service, device)
    if pwd is None:
        if on_finish:
            on_finish()
        return
    if on_status:
        on_status(f"executando '{action}'…", None)

    def _fn():
        if mutating:
            ok, why = service.can_mutate(device)
            if not ok:
                raise IdentityMismatchError(why)
        if fn is None:
            with service.device_session(device, pwd or None):
                return None
        try:
            with service.device_session(device, pwd or None) as client:
                if (mutating and device.expected_dpn_id
                        and not service.is_agent_transport(device)):
                    observed = service.validate_direct_identity(client, device)
                    if observed and observed != device.expected_dpn_id:
                        raise IdentityMismatchError(
                            "ID físico divergente (modo direto)")
                return fn(client)
        except AuthError:
            # NAO repetir operacoes mutaveis so por falha de autenticacao
            if mutating:
                raise
            service.invalidate(device)
            with service.device_session(device, pwd or None) as client:
                return fn(client)

    def _done(result) -> None:
        try:
            if on_status:
                on_status("online", True)
            if on_last:
                on_last(f"{action} — OK")
            if log_action:
                service.log(device, action, ActionStatus.OK, log_detail)
            if apply:
                apply(result)
            if history:
                history.refresh()
        finally:
            if on_finish:
                on_finish()

    def _err(exc) -> None:
        msg = sanitize_error(exc)
        low = msg.lower()
        if "ipnotchanged" in low:
            # a unidade respondeu; so nao havia outro IP para trocar (informativo)
            msg = "IP não mudou (a unidade já estava nesse nó)"
            status, st = (msg, None), ActionStatus.OK
        elif "toomanyattempts" in low:
            status, st = ("muitas tentativas de login — aguarde", False), ActionStatus.ERROR
        elif isinstance(exc, UncertainError):
            status, st = ("resultado incerto — verifique o estado", False), ActionStatus.ERROR
        elif isinstance(exc, IdentityMismatchError):
            status, st = (f"bloqueado: {msg}", False), ActionStatus.ERROR
        elif isinstance(exc, EndpointUnconfirmedError):
            status, st = ("endpoint não confirmado", None), ActionStatus.UNCONFIRMED
        elif isinstance(exc, AuthError):
            status, st = ("falha de autenticação", False), ActionStatus.ERROR
        else:
            status, st = ("erro/offline", False), ActionStatus.ERROR
        try:
            if on_status:
                on_status(*status)
            if on_last:
                on_last(f"{action} — {msg}")
            if log_action:
                service.log(device, action, st, f"{log_detail} :: {msg}")
            if history:
                history.refresh()
        finally:
            if on_finish:
                on_finish()

    tasks.submit(_fn, on_done=_done, on_error=_err)
