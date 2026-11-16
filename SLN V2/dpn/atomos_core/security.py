"""Helpers de seguranca: sanitizacao de segredos e criptografia da senha.

A senha do login AtomOS NAO e enviada em claro: o frontend do proprio painel
criptografa a senha com RSA-OAEP (SHA-1) usando uma chave publica embutida no
bundle JS, e envia o resultado em base64. Confirmado inspecionando o JS servido
pela unidade (index.*.js) e testado ao vivo contra 34.34.34.34.
"""
from __future__ import annotations

import base64
import re
from typing import Callable

# Campos/palavras que indicam segredo e devem ser mascarados em logs/historico.
_SECRET_KEYS = (
    "password", "pass", "pwd", "token", "auth", "cookie", "session",
    "authorization", "secret", "key", "api_key",
)


def redact(text: str) -> str:
    """Mascara valores potencialmente sensiveis em um texto ja serializado."""
    if not text:
        return text
    # mascara "chave": "valor" / chave=valor
    text = re.sub(
        r'("?(?:%s)"?\s*[:=]\s*")([^"]{1,})(")' % "|".join(_SECRET_KEYS),
        r"\1***\3",
        text,
        flags=re.IGNORECASE,
    )
    return text


def sanitize_error(exc: BaseException) -> str:
    """Mensagem de erro segura para gravar em historico (sem URL com credenciais)."""
    msg = str(exc)
    return redact(msg)


# Gancho de derivacao de senha (a confirmar por captura).
PasswordTransform = Callable[[str], str]


# Chave publica RSA extraida do bundle JS servido pela unidade (34.34.34.34).
# Usada para criptografar a senha do login (OAEP/SHA-1). Pode variar por firmware;
# o cliente tenta buscar a chave atual da propria unidade e usa esta como fallback.
ATOMOS_RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAxZ9YwQ1CVM4zNHVNPxoD
Sz6uFGEcyHUOFkoA2hnijjJccCNRGnYlQmnCmaKPtCPiJ26ibXcL9BpfputJpE7Q
cJcJx8CN0Pr/MceYQraFS3UG+zNdI6tGzLDGrBoB+5WFSbK6aOdHFJfcoBfdULHb
g2eGp2IJwSPal3lFNwE/oTL3K1z7EiwbDq0LrY7FcwMGmG3EFaGtMxRy/cq3r0xR
M1V7WIu1I6gw463luLs6NFCdrY/fiXoSrXRf6sOTZClXeRhKjA6c0wLIxizgw6ll
4EeffYVBQSKlEjJJR2y7cxxbp1XkC19evxe0DYbnsemogDcSkmDCj75hsgwuzoTM
FwIDAQAB
-----END PUBLIC KEY-----"""


def rsa_encrypt_password(password: str, public_key_pem: str | None = None) -> str:
    """Criptografa a senha com RSA-OAEP(SHA-1) — espelho do publicEncrypt do JS.

    Confirmado empiricamente contra a unidade (34.34.34.34): PKCS#1 v1.5
    resultou em "decryptionError", enquanto OAEP/SHA-1 foi aceito e retornou
    "wrongPassword" para uma senha de teste errada.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = public_key_pem or ATOMOS_RSA_PUBLIC_KEY
    key = serialization.load_pem_public_key(pem.encode("utf-8"))
    oaep = padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA1()),
        algorithm=hashes.SHA1(),
        label=None,
    )
    ct = key.encrypt(password.encode("utf-8"), oaep)
    return base64.b64encode(ct).decode("ascii")


def identity_password(password: str) -> str:
    """NAO use para login AtomOS (senha e RSA). Mantido para referencia/clareza."""
    return password
