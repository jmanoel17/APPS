"""Certificado TLS autoassinado do controlador para o canal WSS.

O agente faz *pinning* da impressao digital deste certificado apos a conferencia
inicial (nao um trust-all). Gerado uma vez e reutilizado.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _fingerprint(cert_der: bytes) -> str:
    return hashlib.sha256(cert_der).hexdigest()


def ensure_server_cert(data_dir: str) -> tuple[str, str, str]:
    """Garante cert/chave autoassinados. Retorna (cert_pem, key_pem, fingerprint)."""
    os.makedirs(data_dir, exist_ok=True)
    cert_path = os.path.join(data_dir, "controller-cert.pem")
    key_path = os.path.join(data_dir, "controller-key.pem")

    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        with open(cert_path, "rb") as f:
            der = x509.load_pem_x509_certificate(f.read()).public_bytes(
                serialization.Encoding.DER)
        return cert_path, key_path, _fingerprint(der)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "dpn-control"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DPN Control"),
    ])
    san = [x509.DNSName("localhost"),
           x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=1))
        .not_valid_after(now + _dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    der = cert.public_bytes(serialization.Encoding.DER)
    return cert_path, key_path, _fingerprint(der)
