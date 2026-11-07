"""Presentation-only removal of network addresses from Viewer responses.

The controller keeps the real inventory for operations. This redacts the
serialized content handed to restricted browser sessions; it never changes
stored host addresses or scan/audit evidence for administrators.
"""
from __future__ import annotations

import ipaddress
import re

_IPV4 = re.compile(r'(?<![A-Za-z0-9_])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![A-Za-z0-9_])')
_IPV6 = re.compile(r'(?<![A-Za-z0-9_])\[[0-9a-fA-F:.%]+\]|(?<![A-Za-z0-9_])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.%]+')


def redact_ips(text: str) -> str:
    """Replace valid IP literals in text, preserving normal timestamps."""
    def hide(match):
        literal = match.group(0)
        try:
            ipaddress.ip_address(literal.strip('[]').split('%', 1)[0])
        except ValueError:
            return literal
        return '[address hidden]'
    return _IPV4.sub(hide, _IPV6.sub(hide, text))


def redact_for_viewer(data):
    """Redact nested API data before it reaches HTML or JSON serializers."""
    if isinstance(data, dict):
        result = {k: redact_for_viewer(v) for k,v in data.items()}
        if 'address' in result:
            result['address'] = 'Address restricted'
        return result
    if isinstance(data, list):
        return [redact_for_viewer(v) for v in data]
    if isinstance(data, tuple):
        return tuple(redact_for_viewer(v) for v in data)
    if isinstance(data, str):
        return redact_ips(data)
    return data
