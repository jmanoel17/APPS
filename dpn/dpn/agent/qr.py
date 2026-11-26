"""QR de configuracao do agente (alternativa ao ADB).

O QR contem apenas: endereco do controlador, fingerprint e credencial TEMPORARIA
de pareamento. Nunca senha AtomOS nem token permanente.
"""
from __future__ import annotations

import json

import qrcode


def qr_payload(controller_url: str, fingerprint: str, pairing_code: str) -> str:
    return json.dumps({"v": 1, "url": controller_url, "fp": fingerprint,
                       "code": pairing_code}, separators=(",", ":"))


def qr_matrix(payload: str) -> list[list[bool]]:
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.get_matrix()
