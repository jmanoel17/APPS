#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/atomos_core — camada AtomOS VENDORIZADA (mínima, sem Qt) do projeto DPN Control.

Origem: C:\\AndroidProjects\\dpn (atomos/client.py, endpoints.py, tls.py, errors.py,
security.py, models.py). Copiada para eliminar dependência de runtime do projeto externo.

Regras:
  - NÃO importa PySide6, app, UI, workers Qt, service, storage/SQLite nem agent WSS.
  - `client.py` importa `httpx` (dependência opcional de runtime; o provider faz import
    preguiçoso para que testes mockados não exijam httpx).
  - Imports internos ajustados para relativos.
"""

__all__ = ["client", "endpoints", "tls", "errors", "security", "models"]
