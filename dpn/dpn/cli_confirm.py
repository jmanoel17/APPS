"""Ferramenta CLI para confirmar endpoints da API AtomOS.

Uso (durante depuracao guiada com capturas do DevTools):

  python -m dpn.cli_confirm <chave> <METODO> <caminho> [--mutating] \
      [--payload '{"campo":"{placeholder}"}'] [--data-dir DIR]

Exemplo:
  python -m dpn.cli_confirm device_list GET /api/admin/device/list
  python -m dpn.cli_confirm node_type_set POST /api/admin/dpt/node_type \
      --mutating --payload '{"mode":"{node_type}"}'

Escreve (ou atualiza) <data_dir>/endpoints.json. Nada e salvo em claro alem
do proprio endpoint (sem segredos).
"""
from __future__ import annotations

import argparse
import json
import os

from dpn.atomos.endpoints import EndpointRegistry
from dpn.config import default_data_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Confirma um endpoint AtomOS")
    p.add_argument("key")
    p.add_argument("method")
    p.add_argument("path")
    p.add_argument("--mutating", action="store_true")
    p.add_argument("--payload", default=None, help="JSON com placeholders {campo}")
    p.add_argument("--description", default="")
    p.add_argument("--data-dir", default=None)
    args = p.parse_args(argv)

    data_dir = args.data_dir or default_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "endpoints.json")

    payload = None
    if args.payload:
        payload = json.loads(args.payload)

    reg = EndpointRegistry(path)
    reg.confirm(args.key, args.method, args.path, args.mutating, payload,
                args.description)
    reg.save(path)
    print(f"confirmado: {args.key} {args.method} {args.path} "
          f"mutating={args.mutating}")
    print(f"gravado em: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
