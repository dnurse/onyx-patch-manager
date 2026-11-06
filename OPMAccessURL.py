#!/usr/bin/env python3
"""Compute the browser URL to show after OPM installation.

A listener on 0.0.0.0 accepts connections but that address is not a URL
clients should enter. Prefer the configured interface IPv4 or an actual
private address assigned to the local host; never invent an address.
"""
from __future__ import annotations

import ipaddress
import re
import subprocess
import sys


def interface_addresses(output: str) -> list[str]:
    addresses: list[str] = []
    for line in output.splitlines():
        match = re.search(r'\binet (\d{1,3}(?:\.\d{1,3}){3})/\d+', line)
        if match:
            address = match.group(1)
            try:
                ip = ipaddress.IPv4Address(address)
            except ipaddress.AddressValueError:
                continue
            if ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
                addresses.append(address)
    return addresses


def access_url(mode: str, bind: str, allowed_hosts: str, ips: list[str]) -> str:
    host, _, port = bind.rpartition(':')
    if not port.isdecimal() or not 0 < int(port) < 65536:
        port = '8765'
    if mode == 'proxy':
        domain = allowed_hosts.split(',')[0].strip()
        if domain and domain not in ('*', '0.0.0.0'):
            return 'https://' + domain
        return 'Proxy hostname not configured; check Web Access settings'
    if mode == 'localhost':
        return f'http://127.0.0.1:{port} (SSH tunnel required)'
    if mode == 'lan':
        allow = [item.strip() for item in allowed_hosts.split(',')]
        selected = next((x for x in allow if x in ips), None)
        if selected is None:
            selected = ips[0] if ips else None
        if selected:
            return f'http://{selected}:{port}'
        return 'LAN IPv4 address unavailable; check: ip -4 addr show'
    return 'Review OPM Web Access settings before connecting'


def main() -> None:
    _, mode, bind, hosts = sys.argv
    try:
        result = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'scope', 'global'],
                                capture_output=True, text=True, timeout=3, check=False)
        ips = interface_addresses(result.stdout if result.returncode == 0 else '')
    except (OSError, subprocess.TimeoutExpired):
        ips = []
    print(access_url(mode, bind, hosts, ips))


if __name__ == '__main__':
    main()
