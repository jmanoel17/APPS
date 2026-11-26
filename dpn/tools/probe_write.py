"""Validacao ao vivo das operacoes de ESCRITA (sem reboot).

Uso:
    python tools/probe_write.py [host] [tunnel]

Ex.:  python tools/probe_write.py 34.34.34.34 US

Faz, na ordem, com antes/depois:
  1) setDpnMode  -> reenvia o modo atual (valida o payload sem mudar o estado)
  2) switchNode  -> troca o IP ativo do tunnel alvo
  3) refreshTunnel -> gera novos IPs para o tunnel alvo

NAO executa reboot. Pede usuario/senha (senha via getpass).
"""
from __future__ import annotations

import getpass
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dpn.atomos.client import AtomOSClient
from dpn.atomos.endpoints import EndpointRegistry
from dpn.models import Device, TLSMode


def find_tunnel(tunnels, code):
    for t in tunnels:
        if isinstance(t, dict) and t.get("tunnelCode") == code:
            return t
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv
    host = argv[1] if len(argv) > 1 else "34.34.34.34"
    tunnel = argv[2] if len(argv) > 2 else "US"
    username = input("Usuario [admin]: ").strip() or "admin"
    password = getpass.getpass("Senha: ")

    dev = Device(nickname="probe", address=host, tls_mode=TLSMode.INSECURE,
                 username=username)
    reg = EndpointRegistry(None)
    client = AtomOSClient(device=dev, password_provider=lambda: password,
                          registry=reg, request_timeout=10, connect_timeout=5)
    try:
        print("\n== login ==")
        client.login()
        print("ok")

        print("\n== 1) setDpnMode (reenviando o modo atual) ==")
        cur = client.get_node_type()
        print("atual:", json.dumps(cur, ensure_ascii=False))
        mode = (cur or {}).get("dpnMode", "disabled")
        res = client.set_node_type(mode)
        print("resposta:", json.dumps(res, ensure_ascii=False))
        print("depois:", json.dumps(client.get_node_type(), ensure_ascii=False))

        print(f"\n== 2) switchNode (tunnel {tunnel}) ==")
        tunnels = client.get_exit_ip() or []
        t = find_tunnel(tunnels, tunnel)
        if not t or not t.get("activeIp"):
            print(f"tunnel {tunnel} inexistente ou sem IP ativo; pulando.")
        else:
            print("antes:", json.dumps(t, ensure_ascii=False))
            res = client.switch_node(tunnel, t["activeIp"])
            print("resposta:", json.dumps(res, ensure_ascii=False))
            after = find_tunnel(client.get_exit_ip() or [], tunnel)
            print("depois:", json.dumps(after, ensure_ascii=False))

        print(f"\n== 3) refreshTunnel (tunnel {tunnel}) ==")
        t = find_tunnel(client.get_exit_ip() or [], tunnel)
        print("antes:", json.dumps(t, ensure_ascii=False))
        res = client.refresh_tunnel(tunnel)
        print("resposta:", json.dumps(res, ensure_ascii=False))
        after = find_tunnel(client.get_exit_ip() or [], tunnel)
        print("depois:", json.dumps(after, ensure_ascii=False))

        print("\n== 4) nodeLbMode (Node Selection Mode) ==")
        mode = client.get_node_lb_mode()
        print("atual:", json.dumps(mode, ensure_ascii=False))
        cur_mode = (mode or {}).get("mode", "none")
        res = client.set_node_lb_mode(cur_mode)
        print("set (mesmo valor):", json.dumps(res, ensure_ascii=False))
        print("depois:", json.dumps(client.get_node_lb_mode(), ensure_ascii=False))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
