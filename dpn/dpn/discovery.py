"""Descoberta assistida no Windows.

- `discover()`: hosts HTTPS cujo certificado sugira uma DPN (uso legado).
- `parse_arp_table()` / `find_ip_for_mac()`: correlaciona MAC->IP pela tabela de
  vizinhos/ARP. MAC e apenas referencia: pode nao atravessar roteadores e o
  Android pode randomizar o MAC. Nunca inventamos IP a partir do MAC.
- `list_adb_devices()`: `adb devices -l` (somente leitura, diagnostico).
"""
from __future__ import annotations

import re
import socket
import ssl
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from dpn.atomos.tls import fetch_server_cert


def normalize_mac(mac: str) -> str:
    """Normaliza MAC para 'aa:bb:cc:dd:ee:ff' (minusculo)."""
    hexs = re.sub(r"[^0-9a-fA-F]", "", mac or "").lower()
    if len(hexs) != 12:
        return ""
    return ":".join(hexs[i:i + 2] for i in range(0, 12, 2))


def parse_arp_table() -> list[dict]:
    """Le a tabela ARP do Windows: [{ip, mac}] normalizados."""
    out: list[dict] = []
    try:
        raw = subprocess.check_output(["arp", "-a"], text=True, timeout=10)
    except Exception:
        return out
    for line in raw.splitlines():
        m = re.search(
            r"(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})",
            line,
        )
        if m:
            out.append({"ip": m.group(1), "mac": normalize_mac(m.group(2))})
    return out


def find_ip_for_mac(mac: str, table: list[dict] | None = None) -> str | None:
    target = normalize_mac(mac)
    if not target:
        return None
    for row in (table if table is not None else parse_arp_table()):
        if row["mac"] == target:
            return row["ip"]
    return None


def list_adb_devices() -> list[dict]:
    """`adb devices -l` (somente leitura). Retorna serial/modelo/estado."""
    out: list[dict] = []
    try:
        raw = subprocess.check_output(["adb", "devices", "-l"], text=True, timeout=10)
    except Exception:
        return out
    for line in raw.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial = parts[0]
        state = parts[1] if len(parts) > 1 else ""
        model = ""
        for p in parts[2:]:
            if p.startswith("model:"):
                model = p.split(":", 1)[1]
        out.append({"serial": serial, "state": state, "model": model})
    return out


def adb_device_info(serial: str) -> dict:
    """Consulta somente leitura de propriedades uteis (sem alterar o aparelho)."""
    info: dict = {}
    for prop, key in (("ro.product.model", "model"),
                      ("ro.serialno", "serial"),
                      ("dhcp.wlan0.ipaddress", "wlan_ip")):
        try:
            val = subprocess.check_output(
                ["adb", "-s", serial, "shell", "getprop", prop],
                text=True, timeout=8,
            ).strip()
            info[key] = val
        except Exception:
            continue
    return info


def get_local_ips() -> list[str]:
    """IPs IPv4 locais (best-effort, sem dependencia externa)."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr and not addr.startswith("127."):
                ips.add(addr)
    except Exception:
        pass
    try:
        for name, _alias, addrs in socket.gethostbyname_ex(socket.gethostname()):
            for a in addrs:
                if a and not a.startswith("127."):
                    ips.add(a)
    except Exception:
        pass
    return sorted(ips)


def subnets_from_ips(ips: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for ip in ips:
        parts = ip.split(".")
        if len(parts) != 4:
            continue
        subnet = ".".join(parts[:3]) + ".0"
        if subnet not in seen:
            seen.add(subnet)
            out.append(subnet)
    return out


def _probe_host(ip: str, port: int, timeout: float) -> tuple[str, str | None] | None:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            pass
    except OSError:
        return None
    cert = fetch_server_cert(ip, port, timeout=timeout)
    cn = None
    if cert:
        try:
            obj = ssl._ssl._test_decode_cert(cert)  # noqa
            for rdns in obj.get("subject", ()):
                for k, v in rdns:
                    if k == "commonName":
                        cn = v
        except Exception:
            cn = None
    return ip, cn


def discover(
    ports: tuple[int, ...] = (443,),
    timeout: float = 1.2,
    max_hosts: int = 254,
    max_workers: int = 64,
    filter_deeper: bool = True,
) -> list[dict]:
    """Retorna [{ip, cn, port}] de hosts com HTTPS cujo CN sugira ser uma DPN."""
    ips = get_local_ips()
    subnets = subnets_from_ips(ips)
    results: list[dict] = []

    def _scan(ip: str) -> list[dict]:
        found = []
        for port in ports:
            r = _probe_host(ip, port, timeout)
            if r is None:
                continue
            host_ip, cn = r
            if filter_deeper and not (cn and "deeper" in cn.lower()):
                continue
            found.append({"ip": host_ip, "cn": cn, "port": port})
        return found

    targets: list[str] = []
    for subnet in subnets:
        base = subnet.rsplit(".", 1)[0] + "."
        targets.extend(f"{base}{i}" for i in range(1, 255))

    if not targets:
        return results
    if max_hosts and len(targets) > max_hosts:
        targets = targets[:max_hosts]

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_scan, t): t for t in targets}
        for fut in as_completed(futs):
            try:
                results.extend(fut.result())
            except Exception:
                continue
    return results


def localize_agents(mac_refs: list[str]) -> dict:
    """Correlaciona MACs de referencia com IP atual (ARP) e lista devices ADB.

    Retorna {'macs': {mac: ip|None}, 'arp': [...], 'adb': [...]}.
    Nao confirma conexao: apenas aponta candidatos para o usuario revisar.
    """
    arp = parse_arp_table()
    macs = {normalize_mac(m): find_ip_for_mac(m, arp) for m in mac_refs}
    return {"macs": macs, "arp": arp, "adb": list_adb_devices()}

