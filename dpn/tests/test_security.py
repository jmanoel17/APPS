"""Testes da criptografia da senha (RSA-OAEP/SHA-1) e sanitizacao."""
import base64

from dpn.security import redact, rsa_encrypt_password


def test_rsa_block_size():
    """Cifra RSA-2048 => bloco de 256 bytes."""
    out = rsa_encrypt_password("minha-senha")
    assert out != "minha-senha"
    raw = base64.b64decode(out)
    assert len(raw) == 256


def test_rsa_oaep_sha1_roundtrip():
    """Valida OAEP/SHA-1 com chave gerada localmente (par publico/privado)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    ct = base64.b64decode(rsa_encrypt_password("segredo", pem))
    oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(),
                        label=None)
    assert key.decrypt(ct, oaep) == b"segredo"


def test_rsa_is_nondeterministic():
    """OAEP tem padding aleatorio: duas execucoes diferem."""
    a = rsa_encrypt_password("x")
    b = rsa_encrypt_password("x")
    assert a != b


def test_redact_masks_secrets():
    s = '{"username":"admin","password":"segredo123","token":"abc"}'
    out = redact(s)
    assert "segredo123" not in out
    assert "abc" not in out
