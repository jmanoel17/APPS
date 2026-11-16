#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sharding.py — SLN V2 (FASE 10)

Sharding ESTÁTICO de devices entre hosts. Não faz descoberta global obrigatória nem coordenação
distribuída. Cada host lê sua lista (`shards/<HOST_ID>.txt`) ou recebe um subconjunto.

- `SLN_HOST_ID` identifica o host (registrado na telemetria).
- `SLN_DEVICE_GROUP`/`SLN_SHARD_DIR` opcionais.
- `assign()` faz um split determinístico (fallback) se não houver arquivo de shard.

Testável offline.
"""
import os

DEFAULT_SHARD_DIR = "shards"


def host_id(default=""):
    return os.environ.get("SLN_HOST_ID", default) or default


def shard_dir(base_dir, d=None):
    d = d or os.environ.get("SLN_SHARD_DIR") or DEFAULT_SHARD_DIR
    return d if os.path.isabs(d) else os.path.join(base_dir, d)


def shard_file(base_dir, hid, d=None):
    return os.path.join(shard_dir(base_dir, d), "%s.txt" % hid)


def load_shard(base_dir, hid, d=None):
    """Lê `shards/<hid>.txt` (1 device por linha; ignora vazias e comentários '#'). [] se não existir."""
    p = shard_file(base_dir, hid, d)
    devs = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    devs.append(ln)
    except Exception:
        return []
    return devs


def write_shard(base_dir, hid, devices, d=None):
    p = shard_file(base_dir, hid, d)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("# SLN V2 shard %s\n" % hid)
        for dev in devices:
            f.write(dev + "\n")
    return p


def assign(devices, host_index, host_count):
    """Split determinístico round-robin: devices[i] -> host i % host_count. Puro/testável."""
    if host_count <= 0:
        return list(devices)
    return [d for i, d in enumerate(devices) if (i % host_count) == host_index]


def resolve_devices(base_dir, hid=None, discovered=None, host_index=0, host_count=1, d=None):
    """Decide a lista de devices do host: arquivo de shard > assign(discovered). Puro o suficiente."""
    hid = hid or host_id()
    if hid:
        sh = load_shard(base_dir, hid, d)
        if sh:
            return sh, "shard_file"
    if discovered:
        return assign(discovered, host_index, host_count), "assign"
    return [], "none"
