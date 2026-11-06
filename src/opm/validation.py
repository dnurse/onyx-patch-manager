"""Input validation and defensive output redaction.

These routines protect command argument boundaries and stored diagnostics.
Redaction is only a last layer of defense. Remote commands must not print
credentials in the first place, and no system can promise perfect redaction.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path


HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
SENSITIVE_RE = re.compile(
    r"(?i)((?:password|token|api[_-]?key|authorization|secret)\s*[:=]\s*)([^\s,;]+)"
)


def host_address(value: str) -> str:
    """Accept an IPv4/IPv6 literal or conservative DNS name, without options."""
    value = value.strip()
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        if not HOSTNAME_RE.fullmatch(value) or ".." in value:
            raise ValueError("Enter a valid IP address or DNS name") from None
        return value


def validate_host(payload: dict) -> dict:
    """Normalize public host fields and reject unsafe SSH argument injection."""
    name = str(payload.get("name", "")).strip()
    user = str(payload.get("ssh_user", "")).strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("Server name must begin with a letter and use letters, digits, dot, dash or underscore")
    if not USER_RE.fullmatch(user):
        raise ValueError("Invalid SSH username")
    try:
        port = int(payload.get("ssh_port", 22))
    except (ValueError, TypeError):
        raise ValueError("SSH port must be a number") from None
    if port < 1 or port > 65535:
        raise ValueError("SSH port must be between 1 and 65535")
    identity = str(payload.get("key_path", "")).strip()
    if identity:
        path = Path(identity).expanduser()
        if not path.is_absolute():
            raise ValueError("SSH identity path must be absolute")
        if "\n" in identity or "\r" in identity:
            raise ValueError("Invalid SSH identity path")
        identity = str(path)
    sudo_mode = str(payload.get("sudo_mode", "password"))
    if sudo_mode not in ("password", "passwordless", "root", "read_only"):
        raise ValueError("Invalid sudo policy")
    if sudo_mode == "root" and user != "root":
        raise ValueError("Root mode requires the root SSH account")
    return {
        "sudo_mode": sudo_mode,
        "name": name,
        "address": host_address(str(payload.get("address", ""))),
        "ssh_user": user,
        "ssh_port": port,
        "key_path": identity,
        "group_name": str(payload.get("group_name", "Linux VMs")).strip()[:64] or "Linux VMs",
        "protected": bool(payload.get("protected", False)),
    }


def sanitize(value: str, limit: int = 6000) -> str:
    """Bound log size and mask obvious key/value secrets before persistence."""
    return SENSITIVE_RE.sub(r"\1[REDACTED]", str(value))[:limit]
