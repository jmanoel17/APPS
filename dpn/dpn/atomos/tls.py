"""Construcao de contexto TLS por unidade.

Tratamento correto de certificado autoassinado: a excecao e SEMPRE por unidade
e fica visivel na interface. Nenhuma desativacao global de verificacao.
"""
from __future__ import annotations

import socket
import ssl

from dpn.models import TLSMode


def build_ssl_context(tls_mode: TLSMode, ca_bundle_path: str | None = None) -> ssl.SSLContext:
    """Retorna um SSLContext conforme o modo de validacao da unidade."""
    if tls_mode is TLSMode.INSECURE:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    if tls_mode is TLSMode.CA_BUNDLE:
        if not ca_bundle_path:
            raise ValueError("tls_mode=CA_BUNDLE requer ca_bundle_path")
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cafile=ca_bundle_path)
        return ctx

    # TLSMode.VERIFY
    return ssl.create_default_context()


def verify_arg_for(tls_mode: TLSMode, ca_bundle_path: str | None = None):
    """Valor do parametro `verify` do httpx para o modo escolhido."""
    if tls_mode is TLSMode.INSECURE:
        return False
    if tls_mode is TLSMode.CA_BUNDLE:
        return build_ssl_context(tls_mode, ca_bundle_path)
    return True


def fetch_server_cert(host: str, port: int = 443, timeout: float = 5.0) -> str | None:
    """Obtem o certificado PEM do servidor SEM validar (para o usuario fixar).

    Retorna o PEM do certificado do host, ou None se falhar. Util para gerar o
    arquivo de CA da unidade (tratar o certificado proprio de forma correta).
    """
    try:
        ctx = ssl._create_unverified_context()  # somente para ler o cert apresentado
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
                if der:
                    return ssl.DER_cert_to_PEM_cert(der)
    except Exception:
        return None
    return None
