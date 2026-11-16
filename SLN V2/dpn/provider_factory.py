#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dpn/provider_factory.py — registro/factory de provedores DPN por controller.type.

controller.type:
  "fake"           -> FakeDPNProvider (offline/testes)
  "deeper_connect" -> DeeperConnectProvider (SKELETON; integração real pendente)

Type desconhecido -> UnknownProviderError (o runner registra ERROR naquela DPN e segue).
"""
from .provider import DPNProvider, FakeDPNProvider
from .provider_deeper import DeeperConnectProvider


class UnknownProviderError(Exception):
    pass


_REGISTRY = {
    "fake": FakeDPNProvider,
    "deeper_connect": DeeperConnectProvider,
}


def register(controller_type, cls):
    _REGISTRY[str(controller_type)] = cls


def available():
    return sorted(_REGISTRY.keys())


def build_provider(controller_type, controller_cfg=None, dpn_id=None):
    cls = _REGISTRY.get(controller_type)
    if cls is None:
        raise UnknownProviderError(
            "controller.type desconhecido: %r (conhecidos=%s)" % (controller_type, available()))
    cfg = controller_cfg
    if dpn_id is not None:
        cfg = dict(controller_cfg or {})
        cfg.setdefault("_dpn_id", dpn_id)
    return cls.from_config(cfg)
