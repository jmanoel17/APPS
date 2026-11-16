"""Excecoes do cliente AtomOS."""
from __future__ import annotations


class AtomOSError(Exception):
    """Erro base do cliente AtomOS."""


class ConnectionError(AtomOSError):
    """Falha de rede/timeout/TLS ao falar com a unidade."""


class AuthError(AtomOSError):
    """Credenciais invalidas ou sessao expirada."""


class EndpointUnconfirmedError(AtomOSError):
    """Operacao ainda nao confirmada por captura real — nao sera executada.

    Levantada sempre que a operacao tentar usar um endpoint cujo caminho/payload
    nao foi confirmado em codigo nem em captura fornecida (ver endpoint registry).
    """


class UnexpectedResponseError(AtomOSError):
    """A unidade respondeu com algo fora do esperado (status/payload)."""


class UnitMismatchError(AtomOSError):
    """Uma acao tentou atingir uma unidade diferente da registrada."""


class IdentityMismatchError(AtomOSError):
    """O ID fisico observado difere do esperado no cadastro."""


class UncertainError(AtomOSError):
    """A operacao pode ter sido aplicada, mas nao houve confirmacao.

    NUNCA reenviar automaticamente operacoes mutaveis com este estado.
    """
