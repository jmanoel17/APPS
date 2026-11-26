"""Testes do contexto de selecao/requisicao (respostas assincronas)."""
from dpn.ui.context import SelectionContext


def test_token_invalid_after_unit_change():
    ctx = SelectionContext()
    ctx.select(1, ("agent", "a1"))
    t = ctx.token()
    assert ctx.valid(t)
    ctx.select(2, ("agent", "a2"))          # A -> B
    assert not ctx.valid(t)                 # resposta de A descartada


def test_same_unit_transport_change_invalidates():
    ctx = SelectionContext()
    ctx.select(1, ("agent", "a1"))
    t = ctx.token()
    ctx.select(1, ("direct", None))         # mesma unidade, transporte mudou
    assert not ctx.valid(t)


def test_heartbeat_same_context_keeps_valid():
    ctx = SelectionContext()
    ctx.select(1, ("agent", "a1"))
    t = ctx.token()
    ctx.select(1, ("agent", "a1"))          # refresh do mesmo cadastro (heartbeat)
    assert ctx.valid(t)                     # resposta valida NAO e descartada


def test_a_b_a_first_response_does_not_override_latest():
    ctx = SelectionContext()
    ctx.select(1, ("agent", "a1"))
    first = ctx.token()                     # consulta de A
    ctx.select(2, ("agent", "a2"))          # -> B
    ctx.select(1, ("agent", "a1"))          # -> A de novo
    latest = ctx.token()
    assert not ctx.valid(first)             # resposta antiga de A nao substitui a nova
    assert ctx.valid(latest)
