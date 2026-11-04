#!/usr/bin/env python3
"""Configure the private OPM web listener for localhost, LAN or HTTPS proxy.

Root-owned utility installed independently from the opm-writable application
source. It never executes the EnvironmentFile as shell code, preserves the
existing application secret, and requires an explicit operator selection before
altering access to a running installation.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import re
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from datetime import datetime, timezone

ENV_PATH = Path('/home/opm/.config/opm/web.env')
LOG_DIR = Path('/var/log/opm-installer')
PORT = 8765
CONFIG_KEYS = (
    'OPM_WEB_ACCESS_MODE', 'OPM_WEB_BIND', 'OPM_WEB_ALLOWED_HOSTS',
    'OPM_WEB_CSRF_TRUSTED_ORIGINS', 'OPM_WEB_SECURE_COOKIES',
    'OPM_WEB_TRUST_PROXY', 'OPM_WEB_TRUSTED_PROXY_IPS',
    'OPM_WEB_ENABLE_HSTS',
)


def validated_hostname(value: str) -> str:
    """Accept a DNS hostname or IPv4 LAN address, never a URL/port/path."""
    host = value.strip().lower().rstrip('.')
    if not host or len(host) > 253 or any(c in host for c in '/\\:@\n\r #;'):
        raise ValueError('Enter a DNS hostname without https://, port or path')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                   for label in host.split('.')):
            raise ValueError('Enter a valid DNS hostname or IPv4 LAN address') from None
    else:
        if address.version != 4 or not address.is_private:
            raise ValueError('A direct-access IP must be a private IPv4 address')
    return host


def validated_lan_ip(value: str) -> str:
    """Direct LAN access must use a concrete private, non-loopback IPv4."""
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        raise ValueError('Enter the LXC private IPv4 address, not a domain') from None
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local \
            or ip.is_unspecified or ip.is_multicast:
        raise ValueError('Enter a private non-loopback IPv4 address for LAN access')
    return str(ip)


def validated_proxy_ip(value: str) -> str:
    """Require one concrete non-loopback private IP; never trust a whole subnet."""
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        raise ValueError('Enter the reverse proxy source IP, not a subnet or hostname') from None
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise ValueError('The reverse proxy must have a private non-loopback source IP')
    return str(ip)


def detect_lan_ip() -> str:
    """Best-effort primary private IPv4; never return an untrusted public IP."""
    candidates = []
    try:
        ip = subprocess.run(['ip', '-4', 'route', 'get', '1.1.1.1'],
                            capture_output=True, text=True, timeout=3, check=False)
        match = re.search(r'\bsrc\s+(\d+\.\d+\.\d+\.\d+)', ip.stdout)
        if match:
            candidates.append(match.group(1))
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'scope', 'global'],
                                capture_output=True, text=True, timeout=3, check=False)
        candidates.extend(re.findall(r'\binet\s+(\d+\.\d+\.\d+\.\d+)/', result.stdout))
    except (OSError, subprocess.TimeoutExpired):
        pass
    for candidate in candidates:
        try:
            ip = ipaddress.ip_address(candidate)
            if ip.version == 4 and ip.is_private and not (ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_unspecified):
                return str(ip)
        except ValueError:
            continue
    return ''


def settings_for(mode: str, hostname: str = '', proxy_ip: str = '') -> dict[str, str]:
    """Build an atomic, coherent set of Django and Gunicorn access settings."""
    if mode == 'localhost':
        return {
            'OPM_WEB_ACCESS_MODE': 'localhost', 'OPM_WEB_BIND': f'127.0.0.1:{PORT}',
            'OPM_WEB_ALLOWED_HOSTS': 'localhost,127.0.0.1',
            'OPM_WEB_CSRF_TRUSTED_ORIGINS': '', 'OPM_WEB_SECURE_COOKIES': '0',
            'OPM_WEB_TRUST_PROXY': '0', 'OPM_WEB_TRUSTED_PROXY_IPS': '',
            'OPM_WEB_ENABLE_HSTS': '0',
        }
    if mode == 'proxy':
        name = validated_hostname(hostname)
        proxy = validated_proxy_ip(proxy_ip)
        return {
            'OPM_WEB_ACCESS_MODE': 'proxy', 'OPM_WEB_BIND': f'0.0.0.0:{PORT}',
            'OPM_WEB_ALLOWED_HOSTS': f'{name},localhost,127.0.0.1',
            'OPM_WEB_CSRF_TRUSTED_ORIGINS': f'https://{name}',
            'OPM_WEB_SECURE_COOKIES': '1', 'OPM_WEB_TRUST_PROXY': '1',
            'OPM_WEB_TRUSTED_PROXY_IPS': proxy,
            'OPM_WEB_ENABLE_HSTS': '0',
        }
    if mode == 'lan':
        name = validated_lan_ip(hostname)
        return {
            'OPM_WEB_ACCESS_MODE': 'lan', 'OPM_WEB_BIND': f'0.0.0.0:{PORT}',
            'OPM_WEB_ALLOWED_HOSTS': f'{name},localhost,127.0.0.1',
            'OPM_WEB_CSRF_TRUSTED_ORIGINS': '', 'OPM_WEB_SECURE_COOKIES': '0',
            'OPM_WEB_TRUST_PROXY': '0', 'OPM_WEB_TRUSTED_PROXY_IPS': '',
            'OPM_WEB_ENABLE_HSTS': '0',
        }
    raise ValueError('Choose localhost, lan or proxy')


def read_entries(path: Path) -> dict[str, str]:
    """Parse simple systemd EnvironmentFile records without evaluating code."""
    result = {}
    if path.exists():
        for raw in path.read_text(encoding='utf-8').splitlines():
            if not raw.strip() or raw.lstrip().startswith('#'):
                continue
            if '=' not in raw:
                raise ValueError('Malformed existing web configuration; leaving unchanged')
            key, value = raw.split('=', 1)
            if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
                raise ValueError('Invalid existing environment key; leaving unchanged')
            # The OPM-generated environment values contain no spaces/quoting.
            # Preserve other existing values untouched; never print secrets.
            result[key] = value
    return result


def write_entries(path: Path, updates: dict[str, str], *, back_up: bool = True) -> Path | None:
    """Atomic replacement with 0600 permissions and owner preserved."""
    if path.is_symlink():
        raise ValueError('Refusing a symlinked OPM environment file')
    original = read_entries(path)
    if 'OPM_WEB_SECRET_KEY' not in original or len(original['OPM_WEB_SECRET_KEY']) < 48:
        raise ValueError('The existing web secret is missing; do not overwrite credentials')
    if any(k not in CONFIG_KEYS for k in updates):
        raise ValueError('Unknown web access configuration key')
    if any('\n' in v or '\r' in v or '\x00' in v for v in updates.values()):
        raise ValueError('Invalid environment value')
    values = {**original, **updates}
    owner = path.stat() if path.exists() else path.parent.stat()
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOG_DIR.chmod(0o700)
    backup = None
    if back_up and path.exists():
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = LOG_DIR / f'web-config-{stamp}.env'
        with backup.open('xb') as target, path.open('rb') as source:
            os.fchmod(target.fileno(), 0o600)
            shutil.copyfileobj(source, target)
    fd, tempname = tempfile.mkstemp(prefix='.web.env.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as dest:
            os.fchmod(dest.fileno(), 0o600)
            os.fchown(dest.fileno(), owner.st_uid, owner.st_gid)
            for k, v in values.items():
                dest.write(f'{k}={v}\n')
            dest.flush()
            os.fsync(dest.fileno())
        os.replace(tempname, path)
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)
    return backup


def restore_backup(path: Path, backup: Path) -> None:
    """Restore a failed config change without replacing opm ownership with root."""
    owner = path.stat()
    fd, tempname = tempfile.mkstemp(prefix='.web-rollback.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as dest, backup.open('rb') as source:
            os.fchmod(dest.fileno(), 0o600)
            os.fchown(dest.fileno(), owner.st_uid, owner.st_gid)
            shutil.copyfileobj(source, dest)
            dest.flush()
            os.fsync(dest.fileno())
        os.replace(tempname, path)
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)


def prompt_value(message: str, *, default: str = '') -> str:
    suffix = f' [{default}]' if default else ''
    value = input(f'{message}{suffix}: ').strip()
    return value or default


def interactive_settings() -> dict[str, str] | None:
    """Use LAN HTTP by default, but always show the choice on first install."""
    print('\nOPM Web Access Configuration')
    print('1. LAN / VPN HTTP (default; no hostname, proxy or certificate required)')
    print('2. Internal HTTPS reverse proxy (existing proxy and certificate)')
    print('3. Localhost only (SSH tunnel)')
    print('4. Cancel')
    choice = prompt_value('Select access mode (1-4)', default='1')
    if choice == '4':
        return None
    if choice == '3':
        return settings_for('localhost')
    if choice == '1':
        print('Warning: HTTP is unencrypted. Use only on an isolated LAN or trusted VPN.')
        selected = prompt_value('OPM LXC LAN IPv4 address', default=detect_lan_ip())
        if not selected:
            raise ValueError('Cannot detect a LAN IP; enter the LXC private IPv4 address')
        return settings_for('lan', selected)
    if choice == '2':
        print('Enter the internal HTTPS hostname and your reverse proxy source IP.')
        host = prompt_value('HTTPS hostname (without https://)')
        proxy_ip = prompt_value('Reverse proxy source IP reaching this LXC')
        return settings_for('proxy', hostname=host, proxy_ip=proxy_ip)
    raise ValueError('Choose a valid menu option')


def describe(settings: dict[str, str]) -> None:
    print('Web listener: ' + settings['OPM_WEB_BIND'])
    if settings['OPM_WEB_ACCESS_MODE'] == 'proxy':
        host = settings['OPM_WEB_ALLOWED_HOSTS'].split(',')[0]
        print('Open from mobile/VPN: https://' + host)
        print('Upstream in Nginx Proxy Manager: http://<OPM-LXC-LAN-IP>:' + str(PORT))
        print('Proxy: set Host and X-Forwarded-Proto=https; enable a valid certificate.')
        print('Restrict proxy host to LAN/VPN using an Access List, and restrict TCP 8765 to the proxy IP.')
        print('Verify the hostname is NOT accessible from cellular with VPN disconnected.')
    elif settings['OPM_WEB_ACCESS_MODE'] == 'lan':
        host = settings['OPM_WEB_ALLOWED_HOSTS'].split(',')[0]
        print(f'LAN browser: http://{host}:{PORT} (HTTP only, no TLS)')
    else:
        print(f'Loopback browser or SSH tunnel: http://127.0.0.1:{PORT}')


def restart_web() -> None:
    user = 'opm'
    uid = subprocess.check_output(['id', '-u', user], text=True).strip()
    if os.geteuid() == 0:
        prefix = ['runuser', '-u', user, '--', 'env', f'XDG_RUNTIME_DIR=/run/user/{uid}',
                  f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus',
                  'systemctl', '--user']
    elif os.geteuid() == pwd.getpwnam(user).pw_uid:
        prefix = ['systemctl', '--user']
    else:
        raise PermissionError('Only root or the OPM service account can reconfigure web access')
    subprocess.run(prefix + ['restart', 'opm-web.service'], check=True)
    subprocess.run(prefix + ['is-active', '--quiet', 'opm-web.service'], check=True)
    # A running process is not proof that the HTTP endpoint is serving pages.
    # Check loopback directly; proxy mode specifically allows local diagnostics.
    for attempt in range(8):
        try:
            with urlopen(f'http://127.0.0.1:{PORT}/login/', timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, ValueError):
            pass
        if attempt < 7:
            time.sleep(0.5)
    raise RuntimeError('Web service did not respond successfully on localhost:8765')


def apply_settings(settings: dict[str, str], *, restart: bool) -> None:
    """Never overwrite the signing key, and roll back on failed health checks."""
    global LOG_DIR
    if os.geteuid() != 0:
        LOG_DIR = ENV_PATH.parent / 'backups'
    previous = write_entries(ENV_PATH, settings, back_up=restart)
    if not restart:
        return
    try:
        restart_web()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        if previous is not None:
            restore_backup(ENV_PATH, previous)
            try:
                restart_web()
            except (OSError, RuntimeError, subprocess.CalledProcessError):
                raise RuntimeError('Previous configuration restored but web recovery needs manual review') from exc
        raise RuntimeError('Web configuration failed; previous settings restored') from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='OPM internal web access setup')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--initial', action='store_true', help='First installation; no restart')
    group.add_argument('--configure', action='store_true', help='Existing installation; restart web')
    group.add_argument('--controller-apply', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--mode', choices=('lan', 'proxy', 'localhost'), default='')
    parser.add_argument('--hostname', default='')
    parser.add_argument('--proxy-ip', default='')
    args = parser.parse_args(argv)
    if args.controller_apply:
        if os.geteuid() != pwd.getpwnam('opm').pw_uid:
            print('Controller web changes must run as the OPM service account.', file=sys.stderr)
            return 1
        try:
            settings = settings_for(args.mode, args.hostname, args.proxy_ip)
            apply_settings(settings, restart=True)
            return 0
        except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            print('Web configuration failed: ' + str(exc), file=sys.stderr)
            return 1
    if os.geteuid() != 0:
        print('Run as root.', file=sys.stderr)
        return 1
    try:
        if args.mode and (args.initial or args.configure):
            # Whiptail gathers an explicit selection; keep all validation and
            # atomic rollback in this single trusted backend.
            settings = settings_for(args.mode, args.hostname, args.proxy_ip)
            apply_settings(settings, restart=args.configure)
            describe(settings)
            return 0
        if not sys.stdin.isatty():
            if args.configure:
                print('An interactive terminal is required for reconfiguration.', file=sys.stderr)
                return 2
            address = detect_lan_ip()
            if address:
                settings = settings_for('lan', address)
                apply_settings(settings, restart=False)
                print('Noninteractive first install: LAN/VPN HTTP at http://' + address + ':' + str(PORT))
                return 0
            print('No private LAN address was detected; retaining localhost for safety.')
            return 0
        settings = interactive_settings()
        if settings is None:
            if args.initial:
                print('First-install network selection cancelled. Run setup again to choose a mode.')
                return 2
            print('Configuration unchanged.')
            return 0
        describe(settings)
        answer = prompt_value('Apply these web settings? (Y/n)', default='Y')
        if answer.lower() not in ('y', 'yes'):
            print('Configuration unchanged.')
            return 2 if args.initial else 0
        apply_settings(settings, restart=args.configure)
        print('OPM web access configuration saved.')
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Web access configuration failed: {exc}', file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print('\nConfiguration cancelled.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
