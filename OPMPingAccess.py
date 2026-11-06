#!/usr/bin/env python3
"""Grant the OPM service GID ICMP echo socket access on Debian 13.

Only changes ping_group_range when the service GID is excluded. It does not
set CAP_NET_RAW, change container privileges, or modify any remote host.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import sys
import tempfile

PING_SYSCTL = Path('/proc/sys/net/ipv4/ping_group_range')
PING_CONF = Path('/etc/sysctl.d/90-opm-ping.conf')


def required_range(current: str, gid: int) -> tuple[int, int] | None:
    """Return minimal enclosing interval, or None when GID is authorized."""
    lo_s, hi_s = current.split()
    low, high = int(lo_s), int(hi_s)
    if not 0 <= gid <= 4294967294 or not 0 <= low <= 4294967294 or not 0 <= high <= 4294967294:
        raise ValueError('Invalid ping group range or service GID')
    if low <= gid <= high:
        return None
    if low > high:  # 1 0 is the kernel's deny-all sentinel.
        return gid, gid
    return min(low, gid), max(high, gid)


def configure(gid: int, sysctl_path: Path = PING_SYSCTL,
              conf_path: Path = PING_CONF) -> str:
    current = sysctl_path.read_text().strip()
    wanted = required_range(current, gid)
    if wanted is None:
        return 'Service GID already allowed by net.ipv4.ping_group_range'
    low, high = wanted
    value = f'{low} {high}\n'
    settings = ('# Onyx Patch Manager: allow the opm service GID ICMP echo sockets.\n'
                '# A Linux ping_group_range is an interval, not a per-user ACL.\n'
                f'net.ipv4.ping_group_range = {low} {high}\n')
    # Apply to the running namespace first. In some unprivileged LXCs this is
    # read-only; do not leave behind an ineffective persistent configuration.
    try:
        sysctl_path.write_text(value)
    except OSError as exc:
        raise RuntimeError('Kernel/LXC denied ping socket configuration: ' + str(exc)) from exc
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix='.opm-ping.', dir=conf_path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(settings)
        os.chmod(path, 0o644)
        os.replace(path, conf_path)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    return f'Enabled ICMP echo sockets for service GID {gid}; saved {conf_path}'


def main() -> int:
    parser = argparse.ArgumentParser(description='Configure OPM service-account ping access')
    parser.add_argument('--user', default='opm')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('root privileges required')
    try:
        gid = pwd.getpwnam(args.user).pw_gid
        print(configure(gid))
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        print('OPM ping setup: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
