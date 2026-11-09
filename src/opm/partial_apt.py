"""Conservative APT partial-repository upgrade support (Debian/Ubuntu).

Only used after a *normal* signed index refresh fails. A private, initially
empty list directory prevents stale entries from a failed vendor repository
from participating in package selection. Neither sources nor global APT
configuration are changed. No weaker signature policy is permitted.

The remote helper is a fixed Python program delivered over SSH stdin, never
assembled from a hostname, package name or untrusted repository output.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .remote import RemoteError, _sudo_args, ssh_command
from .validation import sanitize

APT_DIR_RE = re.compile(r'/var/tmp/opm-apt-[A-Za-z0-9_]{6,28}\Z')
PACKAGE_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9+._:-]{0,179}\Z')
VERSION_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9+.:~\-]{0,179}\Z')

# The allowed per-command overrides MUST also be present on the modifying
# command. Disabling signature verification or accepting unauthenticated
# repositories is never an available option.
APT_SECURITY = (
    '-o', 'Acquire::AllowInsecureRepositories=false',
    '-o', 'Acquire::AllowWeakRepositories=false',
    '-o', 'Acquire::AllowDowngradeToInsecureRepositories=false',
    '-o', 'APT::Get::AllowUnauthenticated=false',
    '-o', 'Dir::Cache::pkgcache=', '-o', 'Dir::Cache::srcpkgcache=',
)

REMOTE_APT_HELPER = r'''
import json, os, re, shutil, subprocess, sys, tempfile, pwd
from urllib.parse import urlsplit

PREFIX = '/var/tmp/opm-apt-'
IDENTITY = re.compile(r'/var/tmp/opm-apt-[A-Za-z0-9_]{6,28}\Z')
OPTIONS = ['-o', 'Acquire::AllowInsecureRepositories=false',
           '-o', 'Acquire::AllowWeakRepositories=false',
           '-o', 'Acquire::AllowDowngradeToInsecureRepositories=false',
           '-o', 'APT::Get::AllowUnauthenticated=false',
           '-o', 'Dir::Cache::pkgcache=', '-o', 'Dir::Cache::srcpkgcache=']

def run(argv, timeout=180):
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, errors='replace', timeout=timeout,
                                check=False, env={**os.environ, 'LC_ALL': 'C',
                                                  'DEBIAN_FRONTEND': 'noninteractive'})
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, '', str(error)

def result(data):
    print(json.dumps(data, separators=(',', ':')))

def reject_untrusted_sources():
    # Explicitly trusted/insecure sources can bypass ordinary Release signing.
    # Refuse partial mode instead of treating their indexes as authenticated.
    names = ['/etc/apt/sources.list']
    directory = '/etc/apt/sources.list.d'
    if os.path.isdir(directory):
        names.extend(os.path.join(directory, name) for name in os.listdir(directory)
                     if name.endswith(('.list', '.sources')))
    for name in names:
        if not os.path.isfile(name):
            continue
        try:
            with open(name, encoding='utf-8', errors='replace') as handle:
                data = '\n'.join(line for line in handle if not line.lstrip().startswith('#'))
        except OSError:
            return False
        if re.search(r'(?i)\b(?:trusted|allow-insecure|allow-weak|allow-downgrade-to-insecure)\s*=\s*(?:yes|true|1)\b', data):
            return False
        if re.search(r'(?im)^\s*(?:Trusted|Allow-Insecure|Allow-Weak|Allow-Downgrade-To-Insecure):\s*(?:yes|true|1)\s*$', data):
            return False
    code, config, error = run(['apt-config', 'dump'], 15)
    if code:
        return False
    for flag in ('Acquire::AllowInsecureRepositories', 'Acquire::AllowWeakRepositories',
                 'Acquire::AllowDowngradeToInsecureRepositories',
                 'APT::Get::AllowUnauthenticated'):
        if re.search(r'(?im)^' + re.escape(flag) + r'\s+"?(?:true|yes|1)"?\s*;', config):
            return False
    return True

def verified_lists(directory):
    # The directory started empty. APT writes Packages indexes here only after
    # validating Release metadata. Check for a matching signed Release for
    # every Packages file (never trust a stray unsigned index).
    names = os.listdir(directory)
    signed = set()
    for name in names:
        if name.endswith('_InRelease'):
            signed.add(name[:-len('_InRelease')])
        if name.endswith('_Release.gpg') and name[:-len('.gpg')] in names:
            signed.add(name[:-len('_Release.gpg')])
    package_names = [name for name in names if '_Packages' in name and
                     not name.endswith('.FAILED') and os.path.isfile(os.path.join(directory, name))]
    return bool(signed and package_names and all(
        any(name.startswith(repo + '_') for repo in signed) for name in package_names
    ))

def prepare():
    if not reject_untrusted_sources():
        result({'ok': False, 'error': 'APT sources permit insecure or explicitly trusted repositories; partial patching blocked'})
        return
    directory = tempfile.mkdtemp(prefix='opm-apt-', dir='/var/tmp')
    try:
        # APT's unprivileged _apt downloader can traverse via its group;
        # other local users cannot browse the private index directory.
        apt_user = pwd.getpwnam('_apt')
        os.chown(directory, -1, apt_user.pw_gid)
        os.chmod(directory, 0o750)
        lists = os.path.join(directory, 'lists')
        os.mkdir(lists, 0o750)
        os.chown(lists, -1, apt_user.pw_gid)
        partial_dir = os.path.join(lists, 'partial')
        os.mkdir(partial_dir, 0o700)
        # Only _apt may write temporary fetched content; root controls lists.
        os.chown(partial_dir, apt_user.pw_uid, apt_user.pw_gid)
        opts = ['-o', 'Dir::State::lists=' + lists, *OPTIONS]
        code, stdout, stderr = run(['apt-get', *opts, 'update',
                                    '-o', 'APT::Update::Error-Mode=any'], 240)
        if code == 0:
            # The earlier normal refresh failed transiently; all sources are
            # now healthy. Caller can still validate exact approved candidates.
            warning = ''
        elif not any(marker in (stdout + '\n' + stderr).lower() for marker in
                     ('failed to fetch', 'signature verification', 'temporary failure resolving',
                      'could not resolve', 'connection failed', 'connection timed out',
                      'certificate verification failed', 'repository is not signed')):
            result({'ok': False, 'error': 'APT refresh failed for an unclassified reason; partial patching blocked'})
            return
        else:
            failed_hosts = []
            # Only public URL hostnames from failed-source diagnostic lines.
            # Do not store APT stderr, credentials or repository paths.
            for line in (stdout + '\n' + stderr).splitlines():
                if not re.search(r'(?i)(?:Err:|Failed to fetch|signature verification failed)', line):
                    continue
                for raw_url in re.findall(r'https?://[^\s\"\']+', line):
                    try:
                        hostname = urlsplit(raw_url).hostname or ''
                    except ValueError:
                        continue
                    if (re.fullmatch(r'[A-Za-z0-9.-]{1,253}', hostname) and
                            hostname not in failed_hosts):
                        failed_hosts.append(hostname)
            warning = ('Repository verification failed' +
                       (' for ' + ', '.join(failed_hosts[:4]) if failed_hosts else '') +
                       '; older indexes are excluded')
        if not verified_lists(lists):
            result({'ok': False, 'error': 'No independently refreshed signed package indexes available'})
            return
        rc, available, err = run(['apt', *opts, 'list', '--upgradable'], 45)
        if rc:
            result({'ok': False, 'error': 'Could not enumerate candidates from verified temporary indexes'})
            return
        candidates = []
        for line in available.splitlines():
            if '/' not in line or '[upgradable from:' not in line:
                continue
            try:
                package, rest = line.split('/', 1)
                version = rest.split(' ', 2)[1]
                current = line.split('[upgradable from:', 1)[1].split(']', 1)[0].strip()
                if (re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+._:-]{0,179}', package) and
                    re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+.:~\-]{0,179}', version)):
                    candidates.append({'name': package, 'current': current, 'candidate': version})
            except (IndexError, ValueError):
                continue
        rc, simulated, errors = run(['apt-get', *opts, '-s', '--no-remove', '--no-install-recommends',
                                         '-o', 'Dpkg::Options::=--force-confdef',
                                         '-o', 'Dpkg::Options::=--force-confold',
                                         'upgrade'], 65)
        if rc or any(line.startswith(('Remv ', 'Purg ')) for line in simulated.splitlines()):
            result({'ok': False, 'error': 'APT simulation failed or would remove packages; partial patching blocked'})
            return
        upgrades = []
        for line in simulated.splitlines():
            if not line.startswith('Inst '):
                continue
            match = re.match(r'^Inst\s+(\S+)\s+\[([^\]]+)\]\s+\((\S+)', line)
            if not match:
                result({'ok': False, 'error': 'Unexpected APT simulation transaction format; partial patching blocked'})
                return
            upgrades.append({'name': match.group(1), 'current': match.group(2),
                             'candidate': match.group(3)})
        result({'ok': True, 'directory': directory, 'candidates': candidates,
                'upgrades': upgrades, 'warning': warning, 'partial': bool(code)})
        directory = None
    finally:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)

def cleanup():
    if len(sys.argv) != 3 or not IDENTITY.fullmatch(sys.argv[2]):
        result({'ok': False})
        return
    directory = sys.argv[2]
    if os.path.islink(directory):
        result({'ok': False})
        return
    shutil.rmtree(directory, ignore_errors=True)
    result({'ok': not os.path.exists(directory)})

if os.geteuid() != 0:
    result({'ok': False, 'error': 'Privileged APT verification was not authorized'})
elif len(sys.argv) >= 2 and sys.argv[1] == 'prepare':
    prepare()
elif len(sys.argv) >= 2 and sys.argv[1] == 'cleanup':
    cleanup()
else:
    result({'ok': False, 'error': 'Unknown APT verification operation'})
'''


@dataclass(frozen=True)
class PartialAptPlan:
    directory: str
    to_upgrade: tuple[dict, ...]
    excluded: tuple[dict, ...]
    held: tuple[dict, ...]
    warning: str


def _invoke(host, config, password, action: str, directory: str = '') -> dict:
    if action not in ('prepare', 'cleanup'):
        raise ValueError('Invalid APT action')
    if action == 'cleanup' and not APT_DIR_RE.fullmatch(directory):
        raise ValueError('Invalid APT sandbox')
    args = ['python3', '-', action] + ([directory] if action == 'cleanup' else [])
    command = _sudo_args(host, args, password)
    prefix = (password + '\n') if password and host.get('sudo_mode') != 'root' else ''
    raw = ssh_command(host, config, command, prefix + REMOTE_APT_HELPER, timeout=390)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('Invalid response')
    except (TypeError, ValueError) as exc:
        raise RemoteError('Remote APT verification returned invalid status') from exc
    return payload


def plan_verified_subset(host, config, password, approved: list[dict]) -> PartialAptPlan:
    """Fail closed unless isolated signed metadata proves an exact approved subset.

    The selected transaction is never allowed to include a new package or
    a changed version that wasn't explicitly approved in the immutable plan.
    """
    payload = _invoke(host, config, password, 'prepare')
    directory = payload.get('directory', '')
    if not payload.get('ok'):
        raise RemoteError(sanitize(str(payload.get('error', 'Verified repository refresh unavailable')), 250))
    try:
        if not isinstance(directory, str) or not APT_DIR_RE.fullmatch(directory):
            raise RemoteError('APT repository verification returned an invalid sandbox')
        original = {p['name']: p for p in approved}
        if len(original) != len(approved) or any(
            not PACKAGE_RE.fullmatch(name) or not VERSION_RE.fullmatch(p.get('candidate', ''))
            for name, p in original.items()
        ):
            raise RemoteError('Approved package snapshot contains invalid candidates')
        candidates = payload.get('candidates')
        upgrade = payload.get('upgrades')
        if not isinstance(candidates, list) or not isinstance(upgrade, list):
            raise RemoteError('Verified APT candidate inventory is incomplete')
        available = {}
        for entry in candidates:
            name, version = entry['name'], entry['candidate']
            if name in available or not PACKAGE_RE.fullmatch(name) or not VERSION_RE.fullmatch(version):
                raise RemoteError('Unrecognized verified APT candidate')
            available[name] = entry
            if (name not in original or original[name]['candidate'] != version or
                original[name]['current'] != entry.get('current')):
                raise RemoteError('Verified package candidates changed since approval. Scan and review again')
        selected = {}
        for entry in upgrade:
            name, version = entry['name'], entry['candidate']
            if (name in selected or name not in available or
                available[name]['candidate'] != version or
                available[name]['current'] != entry.get('current')):
                raise RemoteError('APT simulation does not match the verified package inventory')
            selected[name] = entry
        if not selected:
            raise RemoteError('No approved package can be upgraded from freshly verified repositories')
        excluded = tuple(p for p in approved if p['name'] not in available)
        held = tuple(p for p in approved if p['name'] in available and p['name'] not in selected)
        return PartialAptPlan(directory, tuple(selected.values()), excluded, held,
                              sanitize(str(payload.get('warning') or 'Repository refresh was incomplete'), 220))
    except BaseException:
        cleanup_verified_subset(host, config, password, directory)
        raise


def cleanup_verified_subset(host, config, password, directory: str) -> None:
    if APT_DIR_RE.fullmatch(directory or ''):
        try:
            _invoke(host, config, password, 'cleanup', directory)
        except (RemoteError, OSError):
            # A failed cleanup must not turn a completed package operation
            # into a second transaction. APT only leaves metadata, not secrets.
            pass


def partial_upgrade_argv(plan: PartialAptPlan) -> list[str]:
    if not APT_DIR_RE.fullmatch(plan.directory):
        raise ValueError('Invalid isolated APT index location')
    return ['apt-get', '-o', 'Dir::State::lists=' + plan.directory + '/lists',
            *APT_SECURITY, '--no-remove', '--no-install-recommends',
            '-o', 'Dpkg::Options::=--force-confdef',
            '-o', 'Dpkg::Options::=--force-confold',
            '-y', 'upgrade']


def installed_versions(host, config, approved: list[dict]) -> dict[str, str]:
    """Query approved packages only; don't persist an unrestricted dpkg dump."""
    names = [p['name'] for p in approved]
    if not names or len(names) > 1000 or any(not PACKAGE_RE.fullmatch(n) for n in names):
        raise RemoteError('Invalid approved package verification list')
    # DPKG formatting is a constant literal; names are previously validated.
    output = ssh_command(host, config, ['dpkg-query', '-W',
        "-f='${binary:Package}\\t${Version}\\n'", *names], timeout=50)
    result = {}
    for line in output.splitlines():
        if '\t' not in line:
            continue
        name, version = line.split('\t', 1)
        result[name] = version.strip()
    return result
