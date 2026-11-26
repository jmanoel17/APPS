"""Liberacao automatica da porta do controlador no Windows Firewall.

Idempotente: cria a regra de entrada apenas se ela nao existir. Se o processo
nao for administrador, eleva via UAC (Start-Process -Verb RunAs) — o usuario ve
apenas o prompt padrao do Windows. Escopo restrito a rede local (LocalSubnet).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time

RULE_NAME = "DPN Control Agent Server"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def rule_exists(name: str = RULE_NAME) -> bool:
    try:
        p = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
            capture_output=True, text=True, timeout=15,
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0 and "no rules match" not in out.lower()
    except Exception:
        return False


def _add_rule_cmd(port: int, remote: str) -> str:
    return (f"New-NetFirewallRule -DisplayName '{RULE_NAME}' -Direction Inbound "
            f"-Protocol TCP -LocalPort {port} -Action Allow -Profile Any "
            f"-RemoteAddress '{remote}'")


def ensure_firewall_rule(port: int, remote: str = "LocalSubnet",
                         data_dir: str | None = None,
                         elevate: bool = True) -> tuple[bool, str]:
    """Garante a regra de entrada TCP <port>. Retorna (ok, mensagem)."""
    if rule_exists():
        return True, "regra de firewall já existe"
    cmd = _add_rule_cmd(port, remote)

    if is_admin():
        try:
            p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                               capture_output=True, text=True, timeout=60)
            if p.returncode == 0:
                return True, "regra criada"
            return False, f"falha ao criar: {(p.stderr or '').strip()[:200]}"
        except Exception as exc:
            return False, f"falha ao criar: {exc}"

    if not elevate:
        return False, "sem permissão de administrador"

    # eleva via UAC
    try:
        base = data_dir or os.path.join(os.environ.get("LOCALAPPDATA", ""), "dpn")
        os.makedirs(base, exist_ok=True)
        script = os.path.join(base, "firewall_setup.ps1")
        with open(script, "w", encoding="utf-8") as f:
            f.write(cmd + "\n")
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Start-Process powershell -Verb RunAs -ArgumentList "
             f"'-NoProfile','-ExecutionPolicy','Bypass','-File','{script}'"],
            timeout=30,
        )
        for _ in range(12):
            time.sleep(0.7)
            if rule_exists():
                return True, "regra criada (via UAC)"
        return False, "não foi possível confirmar (UAC cancelado?)"
    except Exception as exc:
        return False, f"elevação falhou: {exc}"
