"""Provisionamento de agentes por ADB (instalacao, update e pareamento).

Regras:
- Sempre dirigido a um serial explicito (`adb -s <serial>`); nunca instala em
  todos nem escolhe o primeiro automaticamente.
- Atualiza com `install -r` (preserva dados e o agent_id existente).
- NUNCA passa senha AtomOS nem token permanente em argumentos de shell.
"""
from __future__ import annotations

import os
import subprocess

from dpn.discovery import get_local_ips, list_adb_devices, normalize_mac, parse_arp_table

PACKAGE = "com.dpn.agent"
PROVISION_COMPONENT = "com.dpn.agent/.ProvisionActivity"

# chaves que jamais podem aparecer na linha de comando de provisionamento
_FORBIDDEN_ARGS = ("password", "senha", "pwd", "token")


def default_apk_path() -> str:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(here, "android-agent", "app", "build", "outputs",
                        "apk", "debug", "app-debug.apk")


def android_ip_from_serial(serial: str) -> str | None:
    s = (serial or "").strip()
    if ":" in s:
        host = s.split(":", 1)[0]
        if host.count(".") == 3:
            return host
    if s.count(".") == 3:
        return s
    return None


def rank_controller_addresses(android_ip: str | None, ips: list[str] | None = None) -> list[str]:
    """Ordena IPs locais priorizando a mesma /24 do Android, sem escolher so o 1o."""
    ips = ips if ips is not None else get_local_ips()
    if not android_ip or android_ip.count(".") != 3:
        return list(ips)
    subnet = ".".join(android_ip.split(".")[:3])
    same = [ip for ip in ips if ".".join(ip.split(".")[:3]) == subnet]
    others = [ip for ip in ips if ip not in same]
    return same + others


def controller_wss_url(ip: str, port: int) -> str:
    return f"wss://{ip}:{port}"


def build_install_args(serial: str, apk_path: str) -> list[str]:
    """`adb -s <serial> install -r <apk>` (atualiza preservando dados)."""
    if not serial:
        raise ValueError("serial obrigatório")
    return ["-s", serial, "install", "-r", apk_path]


def build_uninstall_args(serial: str) -> list[str]:
    if not serial:
        raise ValueError("serial obrigatório")
    return ["-s", serial, "uninstall", PACKAGE]


def build_provision_args(serial: str, controller_url: str, fingerprint: str,
                         pairing_code: str, atomos_url: str) -> list[str]:
    """`am start` no ProvisionActivity com URL/fp/codigo/atomos — SEM segredos."""
    if not serial:
        raise ValueError("serial obrigatório")
    args = [
        "-s", serial, "shell", "am", "start", "-n", PROVISION_COMPONENT,
        "--es", "controller_url", controller_url,
        "--es", "controller_fp", fingerprint,
        "--es", "pairing_code", pairing_code,
        "--es", "atomos_url", atomos_url,
    ]
    assert_no_secrets(args)
    return args


def build_reverse_args(serial: str, port: int) -> list[str]:
    """`adb -s <serial> reverse tcp:<port> tcp:<port>` (tunel agente<->host)."""
    if not serial:
        raise ValueError("serial obrigatório")
    return ["-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"]


def ensure_adb_reverse(serial: str, port: int, timeout: int = 30) -> tuple[bool, str]:
    """(Re)cria o mapeamento `adb reverse` (idempotente). Retorna (ok, msg).

    Necessario em redes que bloqueiam conexoes iniciadas pelo aparelho: o agente
    so alcanca o controlador pelo tunel do ADB (endereco 127.0.0.1 no aparelho).
    """
    if not serial:
        return False, "sem serial ADB"
    rc, out, err = run_adb(build_reverse_args(serial, port), timeout=timeout)
    return rc == 0, (err or out or "").strip()


def assert_no_secrets(args: list[str]) -> None:
    joined = " ".join(a.lower() for a in args)
    for bad in _FORBIDDEN_ARGS:
        if f"--es {bad}" in joined or f"{bad}=" in joined:
            raise ValueError(f"argumento proibido em provisionamento: {bad}")


def run_adb(args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["adb", *args], capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "adb não encontrado no PATH"
    except subprocess.TimeoutExpired:
        return 124, "", "adb expirou"


def devices_with_reference(mac_ref: str) -> dict:
    """Lista ADB + ARP; marca se o serial/IP casa com o MAC de referencia."""
    adb = list_adb_devices()
    arp = parse_arp_table()
    target = normalize_mac(mac_ref)
    arp_ip = next((r["ip"] for r in arp if r["mac"] == target), None)
    for d in adb:
        ip = android_ip_from_serial(d.get("serial", ""))
        d["ip"] = ip
        d["matches_reference"] = bool(ip and arp_ip and ip == arp_ip)
    return {"devices": adb, "reference_ip": arp_ip}
