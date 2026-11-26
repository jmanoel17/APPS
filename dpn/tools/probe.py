"""Sonda ao vivo para verificacao guiada de uma unidade DPN.

Uso:
    python tools/probe.py [host]

Ex.:  python tools/probe.py 34.34.34.34

Pede usuario/senha (senha via getpass, nao fica no historico do terminal) e
executa: login, device_info, device_list, node_type e exit_ip (tunneis).
Nunca imprime senha, token nem cookies.
"""
from __future__ import annotations

import getpass
import os
import sys

# garante a raiz do projeto no sys.path ao rodar "python tools/probe.py"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dpn.atomos.client import AtomOSClient
from dpn.atomos.endpoints import EndpointRegistry
from dpn.models import Device, TLSMode


def _ok(res) -> bool:
    if isinstance(res, dict) and "success" in res:
        return bool(res["success"])
    return res is not None


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv
    host = argv[1] if len(argv) > 1 else "34.34.34.34"
    username = input("Usuario [admin]: ").strip() or "admin"
    password = getpass.getpass("Senha: ")

    dev = Device(nickname="probe", address=host, tls_mode=TLSMode.INSECURE,
                 username=username)
    reg = EndpointRegistry(None)
    client = AtomOSClient(device=dev, password_provider=lambda: password,
                          registry=reg, request_timeout=10, connect_timeout=5)

    try:
        print("\n== login ==")
        res = client.login()
        print("success:", _ok(res))

        print("\n== device_info (id/modelo/versao/rede) ==")
        print(client.get_device_info())

        print("\n== device_list (dispositivos conectados) ==")
        print(client.list_devices())

        print("\n== node_type (modo DPN) ==")
        print(client.get_node_type())

        print("\n== exit_ip (tunneis / IP ativo) ==")
        print(client.get_exit_ip())
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
