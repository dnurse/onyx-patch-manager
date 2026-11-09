"""Read-only SSH discovery and explicit SSH host-key enrollment.

Remote inspection is a fixed Python program passed over SSH stdin. No client
host field, image name or address is interpolated into a remote shell script.
Unknown registry status must never be presented as 'up to date'.
"""

from __future__ import annotations

import json
import os
import socket
import time
import subprocess
import tempfile
from pathlib import Path

from .config import Config
from .validation import sanitize

# This script runs with the registered SSH identity on the remote Linux host.
# It intentionally makes no changes to package state or running containers.
# Discovery includes the SSH user's context and, when expressly authorized,
# rootful and dynamically discovered rootless Podman account contexts.
REMOTE_INSPECT = r'''
import json
import os
import socket
import time
import subprocess
import shutil
import sys
import pwd


def run(args, timeout=22, cwd=None):
    # A privileged SSH discovery script starts in the SSH user's home (often
    # /home/opm, mode 0700). Rootless Podman owners cannot traverse that path.
    # Set an explicit, publicly traversable cwd before switching user IDs.
    try:
        p = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, check=False,
                           cwd=cwd)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)


def operating_system():
    values = {}
    try:
        with open('/etc/os-release', encoding='utf-8') as source:
            for raw in source:
                if '=' in raw and not raw.startswith('#'):
                    key, value = raw.rstrip().split('=', 1)
                    values[key] = value.strip('"')
    except OSError:
        pass
    family = values.get('ID', '').lower()
    result = {'family': family, 'name': values.get('PRETTY_NAME', family),
              'packages': [], 'apt_error': '', 'metadata': 'unsupported', 'package_query_ok': False, 'reboot_required':
              os.path.exists('/var/run/reboot-required')}
    if family in ('debian', 'ubuntu'):
        # Scans may refresh APT metadata if noninteractive sudo is configured.
        # This writes only package indexes, not installed package files. The
        # result is explicitly marked cached if refresh cannot be completed.
        if '--read-only' in sys.argv or '--os-only' in sys.argv:
            # A worker that has just refreshed package metadata separately
            # must not initiate a second unauthenticated sudo operation.
            result['metadata'] = 'cached'
        else:
            refresh_rc, _, refresh_error = run(
                ['sudo', '-n', 'apt-get', 'update', '-o', 'APT::Update::Error-Mode=any'], 75)
            result['metadata'] = 'fresh' if refresh_rc == 0 else 'cached'
            if refresh_rc:
                result['apt_error'] = 'Package indexes were not refreshed: ' + refresh_error.strip()[:300]
        rc, output, error = run(['apt', 'list', '--upgradable'], 30)
        if rc:
            result['apt_error'] = (error or output).strip()[:1000]
        else:
            result['package_query_ok'] = True
            for line in output.splitlines():
                if '/' not in line or '[upgradable from:' not in line:
                    continue
                try:
                    package, remainder = line.split('/', 1)
                    candidate = remainder.split(' ', 2)[1]
                    current = line.split('[upgradable from:', 1)[1].split(']', 1)[0].strip()
                    result['packages'].append({'name': package, 'current': current,
                                               'candidate': candidate})
                except (IndexError, ValueError):
                    continue
    return result


def image_status(engine, image_id, image_ref, prefix=None):
    # Docker and Podman image IDs are digests of the image configuration.
    # Compare against the selected platform's *remote config digest*, rather
    # than a multi-platform index digest (which may change for other CPUs).
    # Skopeo only fetches registry metadata, never full image layers.
    if not image_ref or not image_id or not shutil.which('skopeo'):
        return 'unknown', 'Skopeo or local image metadata unavailable'
    if '@sha256:' in image_ref:
        return 'pinned', 'Image reference is digest-pinned'
    rc, raw, error = run((prefix or []) + [engine, 'image', 'inspect', image_id], 14)
    if rc:
        return 'unknown', 'Could not inspect local image architecture'
    try:
        local = json.loads(raw)[0]
        platform = (str(local.get('Os') or '').lower(),
                    str(local.get('Architecture') or '').lower())
        variant = str(local.get('Variant') or '').lower()
        if not all(platform):
            return 'unknown', 'Image platform was not available'
        rc, raw, error = run(['skopeo', 'inspect', '--raw',
                              'docker://' + image_ref], 16)
        if rc:
            return 'unknown', (error or 'Registry metadata unavailable').strip()[:180]
        manifest = json.loads(raw)
        if 'manifests' in manifest:
            matching = []
            for item in manifest.get('manifests', []):
                host = item.get('platform') or {}
                if ((str(host.get('os') or '').lower(),
                     str(host.get('architecture') or '').lower()) == platform
                        and (not variant or str(host.get('variant') or '').lower() == variant)):
                    matching.append(item['digest'])
            if len(matching) != 1:
                return 'unknown', 'No unique matching platform manifest'
            # Request the selected architecture by immutable child digest so
            # an index change affecting another platform is not a false hit.
            repository = image_ref.split('@', 1)[0]
            if '/' in repository and ':' in repository.rsplit('/', 1)[-1]:
                repository = repository.rsplit(':', 1)[0]
            elif '/' not in repository and ':' in repository:
                repository = repository.rsplit(':', 1)[0]
            rc, raw, error = run(['skopeo', 'inspect', '--raw',
                                  'docker://' + repository + '@' + matching[0]], 16)
            if rc:
                return 'unknown', 'Selected platform manifest could not be retrieved'
            manifest = json.loads(raw)
        config_digest = (manifest.get('config') or {}).get('digest')
        if not config_digest or not str(config_digest).startswith('sha256:'):
            return 'unknown', 'No comparable image config digest'
        local_digest = str(image_id).lower()
        if not local_digest.startswith('sha256:'):
            local_digest = 'sha256:' + local_digest
        return ('current', 'Image config digest matches registry' ) if local_digest == config_digest.lower() else (
                'changed', 'New image content exists at the configured reference')
    except (ValueError, KeyError, IndexError, TypeError):
        return 'unknown', 'Unrecognized image manifest metadata'


def containers(engine, prefix=None, scope='OPM SSH user',
               query_timeout=22, inspect_timeout=35, registry_checks=True,
               working_directory=None):
    """Inspect only the requested Podman/Docker user namespace.

    ``prefix`` is a fixed argv list assembled from trusted system account
    metadata. Never execute an arbitrary service command or accept a username
    from a browser form. The returned workload data is informational only.
    """
    if not shutil.which(engine):
        return {'available': False, 'installed': False, 'error': '',
                'containers': [], 'scope': scope}
    args = prefix or []
    def run_in_scope(argv, timeout):
        if working_directory is None:
            return run(argv, timeout)
        return run(argv, timeout, cwd=working_directory)
    rc, output, error = run_in_scope(args + [engine, 'ps', '-aq'], query_timeout)
    if rc:
        return {'available': False, 'installed': True, 'error': (error or output).strip()[:800],
                'containers': [], 'scope': scope}
    identifiers = [v.strip() for v in output.splitlines() if v.strip()]
    results = []
    if identifiers:
        rc, raw, error = run_in_scope(args + [engine, 'inspect', *identifiers], inspect_timeout)
        if rc:
            return {'available': True, 'installed': True, 'error': (error or raw).strip()[:800],
                    'containers': [], 'scope': scope}
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError('Invalid inspect response')
        except ValueError:
            return {'available': True, 'installed': True, 'error': 'Could not parse container inspect JSON',
                    'containers': [], 'scope': scope}
        for item in parsed:
            config = item.get('Config') or {}
            labels = config.get('Labels') or {}
            state = item.get('State') or {}
            health = (state.get('Health') or {}).get('Status')
            image_ref = config.get('Image') or item.get('ImageName') or ''
            # Docker Compose evidence must be based on Docker's own labels;
            # Podman Compose and standalone containers are outside update scope.
            docker_compose = (engine == 'docker'
                              and bool(labels.get('com.docker.compose.project'))
                              and bool(labels.get('com.docker.compose.service')))
            project = labels.get('com.docker.compose.project') or labels.get('io.podman.compose.project')
            service = labels.get('com.docker.compose.service') or labels.get('io.podman.compose.service')
            # Record PRESENCE only. Absolute paths may expose deployment details,
            # and must be independently revalidated before any future update.
            config_files_present = bool(labels.get('com.docker.compose.project.config_files'))
            working_dir_present = bool(labels.get('com.docker.compose.project.working_dir'))
            oneoff = str(labels.get('com.docker.compose.oneoff') or '').lower() == 'true'
            # Identify the *workload*, not the Linux account. An account can
            # own both vendor-managed and user-managed containers.
            image_names = (str(image_ref).lower(),
                           str(item.get('ImageName') or '').lower())
            vendor_managed = any(name.startswith((
                'docker.io/library/uosserver:', 'docker.io/library/uosserver@',
                'localhost/uosserver:', 'uosserver:')) for name in image_names)
            if vendor_managed:
                status, reason = 'vendor-managed', 'UniFi OS Server controls updates'
            elif not registry_checks:
                status, reason = 'unknown', 'Cross-user registry comparison deferred (read-only discovery)'
            else:
                status, reason = image_status(engine, str(item.get('Image') or ''), image_ref, args)
            results.append({'name': str(item.get('Name') or '').lstrip('/'),
                            'id': str(item.get('Id') or '')[:12],
                            'image': image_ref, 'image_id': str(item.get('Image') or ''),
                            'state': str(state.get('Status') or 'unknown'),
                            'health': health or 'not configured',
                            'project': project, 'service': service,
                            'compose_origin': ('docker-compose' if docker_compose else
                                               'other-compose' if project and service else ''),
                            'compose_has_config_files': config_files_present if docker_compose else False,
                            'compose_has_working_dir': working_dir_present if docker_compose else False,
                            'compose_oneoff': oneoff if docker_compose else False,
                            'deployment': ('vendor-managed (UniFi OS Server)' if vendor_managed
                                           else 'compose candidate' if project and service else 'standalone / unknown'),
                            'update_status': status, 'update_reason': reason,
                            'scope': scope, 'managed_by': 'unifi-os-server' if vendor_managed else ''})
    return {'available': True, 'installed': True, 'error': '', 'containers': results,
            'scope': scope}


def _podman_subid_users():
    """Find account mappings, including service users with UIDs below 1000.

    Subordinate ID mappings are *hints*, not proof that Podman is installed
    for that account. They help find nonstandard storage configurations.
    """
    owners = set()
    for path in ('/etc/subuid', '/etc/subgid'):
        try:
            with open(path, encoding='utf-8', errors='replace') as source:
                for line in source:
                    account, separator, _ = line.partition(':')
                    if separator and account:
                        owners.add(account)
        except OSError:
            pass
    return owners


def _podman_process_owners():
    """Observe running Podman helpers; do not start user sessions/processes."""
    owners = set()
    try:
        entries = os.scandir('/proc')
    except OSError:
        return owners
    with entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                with open(entry.path + '/comm', encoding='utf-8') as f:
                    name = f.read(80).strip()
                if name in ('conmon', 'podman', 'rootlessport', 'slirp4netns', 'pasta'):
                    owners.add(os.stat(entry.path).st_uid)
            except (OSError, ValueError):
                continue
    return owners


def discover_podman_accounts():
    """Enumerate likely rootless Podman owners, without hardcoded usernames.

    Signals: user storage, per-user Podman config, running Podman helpers,
    active runtime stores, and subordinate UID/GID mappings. NSS accounts
    (including service accounts) are eligible regardless of UID or shell.
    No account is modified and no lingering/login session is enabled.
    """
    subids = _podman_subid_users()
    processes = _podman_process_owners()
    candidates = []
    for account in pwd.getpwall():
        uid = account.pw_uid
        username = account.pw_name
        home = account.pw_dir
        if uid == 0 or not username or username.startswith('-') or not home.startswith('/'):
            continue
        runtime_dir = '/run/user/' + str(uid)
        persistent = os.path.exists(os.path.join(home, '.local/share/containers/storage'))
        configured = any(os.path.exists(os.path.join(home, name)) for name in (
            '.config/containers/storage.conf', '.config/containers/containers.conf',
            '.config/containers/systemd'))
        active = os.path.exists(os.path.join(runtime_dir, 'containers'))
        running = uid in processes
        subid = username in subids or str(uid) in subids
        if not any((persistent, configured, active, running, subid)):
            continue
        # Strong evidence first; accounts with only subuid entries are a
        # lower-priority fallback and are still queried when time permits.
        score = (8 if running else 0) + (4 if persistent else 0) + (4 if active else 0) + (2 if configured else 0)
        candidates.append((score, username, account))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [entry[2] for entry in candidates]


def all_rootless_podman():
    """Read each discovered Podman store as its owner using fixed argv.

    A bounded total time budget prevents an unreachable runtime or private
    registry from stalling the complete SSH scan. Exhaustion is explicit.
    """
    if not shutil.which('podman'):
        return [], ''
    accounts = discover_podman_accounts()
    if not accounts:
        return [], ''
    if not shutil.which('runuser'):
        return [], 'Cannot inspect rootless Podman accounts: runuser unavailable'
    if os.geteuid() != 0:
        return [], 'Cannot inspect other Podman users without authorized root access'
    results = []
    deadline = time.monotonic() + 115
    skipped = 0
    # Cached CLI metadata is read only, and registry requests are deliberately
    # omitted for cross-user contexts. The administrator can see every owner.
    for account in accounts:
        remaining = deadline - time.monotonic()
        if remaining < 9:
            skipped += 1
            continue
        username, uid, home = account.pw_name, account.pw_uid, account.pw_dir
        runtime_dir = '/run/user/' + str(uid)
        environment = ['PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=' + home,
                       'USER=' + username, 'LOGNAME=' + username]
        if os.path.isdir(runtime_dir):
            try:
                if os.stat(runtime_dir).st_uid == uid:
                    environment.extend(('XDG_RUNTIME_DIR=' + runtime_dir,
                                        'DBUS_SESSION_BUS_ADDRESS=unix:path=' + runtime_dir + '/bus'))
            except OSError:
                pass
        prefix = ['runuser', '-u', username, '--', 'env', '-i', *environment]
        scope = 'Podman user ' + username + ' (UID ' + str(uid) + ')'
        results.append(containers('podman', prefix, scope,
                                  query_timeout=min(9, max(2, int(remaining - 2))),
                                  inspect_timeout=min(18, max(2, int(remaining - 2))),
                                  registry_checks=False,
                                  working_directory='/'))
    warning = ('Rootless Podman scan time budget reached; ' + str(skipped) +
               ' context(s) were not inspected') if skipped else ''
    return results, warning


def system_snapshot():
    """Read a small, point-in-time status summary without extra privileges.

    No CPU percentage is invented from a single sample. The one-minute load
    average, RAM used and root filesystem use are available from the kernel
    and do not require an agent or a continuously running monitor.
    """
    result = {'cpu_count': os.cpu_count() or 0,
              'load_1m': 'unknown', 'memory_percent': 'unknown',
              'disk_percent': 'unknown', 'uptime': 'unknown'}
    try:
        result['load_1m'] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    try:
        values = {}
        with open('/proc/meminfo', encoding='utf-8') as source:
            for raw in source:
                label, _, amount = raw.partition(':')
                if label in ('MemTotal', 'MemAvailable'):
                    values[label] = int(amount.split()[0])
        if values.get('MemTotal', 0) > 0 and 'MemAvailable' in values:
            result['memory_percent'] = round(
                100 * (values['MemTotal'] - values['MemAvailable']) / values['MemTotal'], 1)
    except (OSError, ValueError, IndexError):
        pass
    try:
        usage = shutil.disk_usage('/')
        if usage.total:
            result['disk_percent'] = round(100 * usage.used / usage.total, 1)
    except OSError:
        pass
    try:
        with open('/proc/uptime', encoding='utf-8') as source:
            elapsed = int(float(source.read().split()[0]))
        days, remainder = divmod(elapsed, 86400)
        hours = remainder // 3600
        result['uptime'] = f'{days}d {hours}h' if days else f'{hours}h'
    except (OSError, ValueError, IndexError):
        pass
    return result


# A controller-approved, authenticated scan may run only this fixed
# container-discovery branch as root. It never changes running containers.
# The ordinary branch retains visibility into rootless Podman for the SSH user.
if '--os-only' in sys.argv:
    # The patch preflight must emit exactly one JSON document and avoid
    # registry calls. Never splice Python source based on text indentation.
    print(json.dumps({'os': operating_system()}))
elif '--privileged-engines' in sys.argv:
    engines = {
        'docker': containers('docker', scope='root'),
        'podman': containers('podman', scope='root')}
    engines['podman_users'], engines['podman_discovery_warning'] = all_rootless_podman()
    print(json.dumps({'engines': engines}))
else:
    print(json.dumps({'os': operating_system(), 'system': system_snapshot(), 'engines': {
        'docker': containers('docker'), 'podman': containers('podman')}}))
'''

# The patch executor needs only OS facts. Keep Docker/Podman discovery and
# registry calls out of the modifying-job preflight, both for speed and to
# prevent an unrelated registry outage from blocking an approved APT update.
# Keep the verified remote script as one source of truth. The OS-only
# command-line flag selects a dedicated branch, so Python emits a single
# parseable JSON object even when other discovery branches are refactored.
REMOTE_OS_INSPECT = REMOTE_INSPECT


# This fixed program is sent over existing key-authenticated SSH only after
# an administrator explicitly requests Compose verification. The only dynamic
# source value is a JSON-encoded, strictly validated Compose project name.
# It never pulls images, fetches registry metadata, starts/stops workloads,
# or prints Docker inspect JSON, Compose contents, environment or file paths.
REMOTE_COMPOSE_VERIFY = r'''
import json
import os
import re
import subprocess
import sys

EXPECTED_PROJECT = ''


def run(argv, cwd=None, timeout=25):
    try:
        completed = subprocess.run(argv, cwd=cwd, capture_output=True,
                                   text=True, timeout=timeout, check=False)
        return completed.returncode, completed.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ''


def fail(reason):
    return {'status': 'unverified', 'detail': reason, 'service_count': 0,
            'container_count': 0}


def verify():
    project = EXPECTED_PROJECT
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,119}', project):
        return fail('Invalid Compose project name')
    rc, ids = run(['docker', 'ps', '-aq', '--filter',
                   'label=com.docker.compose.project=' + project])
    if rc:
        return fail('Docker engine is unavailable to the verification account')
    identifiers = [x.strip() for x in ids.splitlines() if x.strip()]
    if not identifiers or len(identifiers) > 128:
        return fail('No unique running Compose deployment found')
    rc, raw = run(['docker', 'inspect', *identifiers], timeout=40)
    if rc:
        return fail('Could not inspect project containers')
    try:
        containers = json.loads(raw)
    except (ValueError, TypeError):
        return fail('Docker inspect response was invalid')
    if not isinstance(containers, list) or len(containers) != len(identifiers):
        return fail('Container inventory changed during verification')
    identities = set()
    hashes = {}
    services = set()
    for item in containers:
        labels = (item.get('Config') or {}).get('Labels') or {}
        if labels.get('com.docker.compose.project') != project:
            return fail('Compose project identity changed')
        service = str(labels.get('com.docker.compose.service') or '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', service):
            return fail('Unrecognized or missing Compose service label')
        if str(labels.get('com.docker.compose.oneoff') or '').lower() == 'true':
            return fail('One-off Compose containers cannot be verified for updates')
        image = str((item.get('Config') or {}).get('Image') or '').lower()
        if (image.startswith(('uosserver:', 'localhost/uosserver:',
                             'docker.io/library/uosserver:'))):
            return fail('Vendor-controlled workload is excluded')
        working = str(labels.get('com.docker.compose.project.working_dir') or '')
        config_string = str(labels.get('com.docker.compose.project.config_files') or '')
        paths = tuple(config_string.split(','))
        if not os.path.isabs(working) or not os.path.isdir(working):
            return fail('Original Compose project directory is unavailable')
        if (not paths or len(paths) > 12 or
            any(not os.path.isabs(f) or not os.path.isfile(f) for f in paths)):
            return fail('Original Compose file is missing or inaccessible')
        if any(len(f) > 2048 or any(c in f for c in '\r\n\x00') for f in paths):
            return fail('Invalid Compose file metadata')
        identities.add((working, paths))
        h = str(labels.get('com.docker.compose.config-hash') or '')
        if not h or not re.fullmatch(r'[a-fA-F0-9]{64}', h):
            return fail('Deployed container has no comparable Compose configuration hash')
        hashes.setdefault(service, set()).add(h.lower())
        services.add(service)
    if len(identities) != 1:
        return fail('Inconsistent Compose provenance across project containers')
    working, files = next(iter(identities))
    base = ['docker', 'compose', '--project-directory', working,
            '-p', project]
    for file in files:
        base.extend(('-f', file))
    rc, _ = run(base + ['config', '--quiet'], cwd=working, timeout=50)
    if rc:
        return fail('Compose config validation failed (check env files and interpolation)')
    rc, listing = run(base + ['config', '--services'], cwd=working, timeout=40)
    if rc:
        return fail('Cannot enumerate services from the original Compose files')
    defined = set(listing.splitlines())
    if not services.issubset(defined):
        return fail('Running services do not match the Compose source')
    # Compose V2 declares --hash as a STRING option (service name or '*').
    # A bare --hash is invalid and previously made every verification fail.
    # Hash only the services whose deployed labels we are comparing.
    rc, output = run(base + ['config', '--hash=' + ','.join(sorted(services))],
                     cwd=working, timeout=40)
    if rc:
        return fail('Compose service hashes are not available')
    config_hashes = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and re.fullmatch('[a-fA-F0-9]{64}', fields[1]):
            config_hashes[fields[0]] = fields[1].lower()
    if not all(s in config_hashes and hashes[s] == {config_hashes[s]}
               for s in services):
        return fail('Running container configuration differs from Compose source')
    return {'status': 'verified', 'detail': 'Compose source matches deployed services',
            'service_count': len(services), 'container_count': len(identifiers)}

try:
    result = verify()
except Exception:
    # Never include arbitrary exceptions: they can contain private paths,
    # Compose interpolation values, registry credentials, or env content.
    result = fail('Compose verification could not be completed')
print(json.dumps(result, separators=(',', ':')))
'''


def verify_compose_source(host: dict, config: Config, project: str,
                          sudo_password: str | None = None) -> dict:
    """Verify Compose provenance without updating, pulling, or disclosing files.

    The source paths originate only from fresh Docker inspect labels fetched on
    the managed host. A web request never supplies a filesystem path or command.
    """
    import re
    if not isinstance(project, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,119}', project):
        raise RemoteError('Invalid Docker Compose project')
    script = REMOTE_COMPOSE_VERIFY.replace("EXPECTED_PROJECT = ''",
                                          'EXPECTED_PROJECT = ' + json.dumps(project))
    cmd = _sudo_args(host, ['python3', '-'], sudo_password)
    raw = ssh_command(host, config, cmd,
        ((sudo_password + '\n') if sudo_password else '') + script, timeout=190)
    try:
        result = json.loads(raw)
        if (not isinstance(result, dict) or result.get('status') not in
                ('verified', 'unverified') or
                not isinstance(result.get('service_count'), int) or
                not isinstance(result.get('container_count'), int)):
            raise ValueError('Invalid verification response')
        # The remote code uses only fixed diagnostic strings. Additional keys
        # are deliberately discarded rather than written into History.
        allowed = {
            'Compose source matches deployed services',
            'Invalid Compose project name',
            'Docker engine is unavailable to the verification account',
            'No unique running Compose deployment found',
            'Could not inspect project containers',
            'Docker inspect response was invalid',
            'Container inventory changed during verification',
            'Compose project identity changed',
            'Unrecognized or missing Compose service label',
            'One-off Compose containers cannot be verified for updates',
            'Vendor-controlled workload is excluded',
            'Original Compose project directory is unavailable',
            'Original Compose file is missing or inaccessible',
            'Invalid Compose file metadata',
            'Deployed container has no comparable Compose configuration hash',
            'Inconsistent Compose provenance across project containers',
            'Compose config validation failed (check env files and interpolation)',
            'Cannot enumerate services from the original Compose files',
            'Running services do not match the Compose source',
            'Compose service hashes are not available',
            'Running container configuration differs from Compose source',
            'Compose verification could not be completed',
        }
        detail = str(result.get('detail') or '')
        if detail not in allowed:
            raise ValueError('Unrecognized verification diagnostic')
        return {'status': result['status'], 'detail': detail,
                'service_count': min(max(0, result['service_count']), 128),
                'container_count': min(max(0, result['container_count']), 128)}
    except (ValueError, TypeError, KeyError) as exc:
        raise RemoteError('Docker Compose verification produced an invalid result') from exc


class RemoteError(RuntimeError):
    """Display-safe remote execution failure."""


def _ssh_arguments(host: dict, config: Config) -> list[str]:
    """Assemble SSH arguments without shell evaluation or interactive prompts."""
    args = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={config.known_hosts}",
            "-o", "ConnectTimeout=8", "-o", "NumberOfPasswordPrompts=0",
            "-p", str(host["ssh_port"])]
    if host.get("key_path"):
        args += ["-i", host["key_path"], "-o", "IdentitiesOnly=yes"]
    # '--' is not universally recognized by all SSH clients. Strict validation
    # ensures that the username/address cannot start an SSH option instead.
    args.append(f"{host['ssh_user']}@{host['address']}")
    return args


def ssh_command(host: dict, config: Config, remote_args: list[str],
                input_text: str | None = None, timeout: int = 45) -> str:
    """Run a fixed remote command with verified host-key checking enabled."""
    cmd = _ssh_arguments(host, config) + remote_args
    try:
        p = subprocess.run(cmd, input=input_text, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteError(sanitize(str(exc))) from exc
    if p.returncode:
        raise RemoteError(sanitize(p.stderr.strip() or p.stdout.strip() or
                                   f"SSH returned exit code {p.returncode}"))
    return p.stdout


def test_connection(host: dict, config: Config) -> dict:
    """Confirm key-based SSH works and determine passwordless sudo readiness."""
    # No changes are made by either command. Background jobs cannot safely
    # prompt for a sudo password after the operator disconnects.
    output = ssh_command(host, config, ["true"], timeout=18)
    del output
    try:
        ssh_command(host, config, ["sudo", "-n", "true"], timeout=18)
        sudo = "passwordless"
    except RemoteError:
        sudo = "password required or unavailable"
    return {"connected": True, "sudo": sudo}


def verify_sudo_password(host: dict, config: Config, password: str) -> bool:
    """Validate a password-required sudo policy without writing credentials."""
    if not password or "\n" in password or "\r" in password:
        return False
    try:
        # The empty prompt must be quoted for OpenSSH's remote shell.
        # -k invalidates cached sudo state, ensuring password verification.
        ssh_command(host, config, ["sudo", "-k", "-S", "-p", "''", "true"],
                    password + "\n", timeout=20)
        return True
    except RemoteError:
        return False


def _decode_inspect(raw: str, required: tuple[str, ...]) -> dict:
    """Reject incomplete remote JSON instead of presenting unknown as healthy."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not all(name in data for name in required):
            raise ValueError("missing discovery sections")
        return data
    except ValueError as exc:
        raise RemoteError(f"Remote discovery returned invalid JSON: {exc}") from exc


def _sudo_args(host: dict, command: list[str], password: str | None) -> list[str]:
    """Prepare a fixed privileged command without shell or password interpolation.

    sudo reads a provided password from stdin. SSH itself remains key-based,
    with strict host-key verification. Never place the password in argv.
    """
    if host.get('sudo_mode') == 'root':
        return command
    if password:
        # -k avoids accidentally relying on a cached sudo timestamp.
        # The quoted empty prompt is required by OpenSSH remote-shell parsing.
        return ['sudo', '-k', '-S', '-p', "''", *command]
    return ['sudo', '-n', *command]


def _merge_container_scopes(engines: dict, engine: str, discovered: list[dict]) -> None:
    """Accumulate separate Podman user stores without changing existing IDs.

    Never turn an inaccessible service-user scope into a false all-clear.
    Keep the per-context error alongside any healthy contexts so OPM can
    explain why a vendor workload is still absent after an authorized scan.
    """
    current = engines.setdefault(engine, {'available': False, 'installed': False,
                                          'containers': [], 'scope': 'OPM SSH user'})
    scopes = [current.get('scope', 'OPM SSH user')]
    warnings = []
    # Container IDs are globally generated identifiers. A separate rootless
    # scan of the SSH user's account must not show the same container twice.
    def identity(container, scope):
        return ('container', container['id']) if container.get('id') else (
            scope, container.get('name'))
    seen = {identity(c, c.get('scope', scopes[0])) for c in current.get('containers', [])}
    for item in discovered:
        if not item:
            continue
        scope = item.get('scope', 'root')
        scopes.append(scope)
        # A context may successfully list containers but fail to inspect
        # their details; this is a partial scan, not a clean result.
        if item.get('error'):
            warnings.append(scope + ': ' + str(item['error'])[:150])
        if item.get('available'):
            current['available'] = True
            current['installed'] = True
            for container in item.get('containers', []):
                container = dict(container)
                container['scope'] = scope
                identifier = identity(container, scope)
                if identifier not in seen:
                    current.setdefault('containers', []).append(container)
                    seen.add(identifier)
    current['inspected_scopes'] = list(dict.fromkeys(scopes))
    current['scope'] = ', '.join(current['inspected_scopes'])
    if warnings:
        current['scope_warning'] = '; '.join(warnings)[:500]


def inspect_host(host: dict, config: Config, sudo_password: str | None = None) -> dict:
    """Read OS and container state using only explicitly approved privileges.

    A scan with a password first refreshes APT through sudo, then performs
    unprivileged discovery to preserve the SSH user's rootless Podman view.
    If Docker is inaccessible to that user, OPM performs a second read-only
    engine inspection using the same one-time sudo authorization. Neither
    operation installs packages, pulls images, or restarts containers.
    """
    if sudo_password is not None and (not sudo_password or any(
            char in sudo_password for char in '\r\n\x00')):
        raise RemoteError('Invalid sudo password for authenticated scan')
    mode = host.get('sudo_mode', 'passwordless')
    authenticated = mode in ('root', 'passwordless') or bool(sudo_password)
    apt_error = ''
    if authenticated:
        try:
            # Only apt-get update is permitted in scanning. No upgrade runs.
            command = _sudo_args(host, [
                'apt-get', 'update', '-o', 'APT::Update::Error-Mode=any'], sudo_password)
            ssh_command(host, config, command,
                        sudo_password + '\n' if sudo_password else None, timeout=130)
        except RemoteError as exc:
            apt_error = 'Package indexes were not refreshed: ' + sanitize(str(exc), 300)
    # The read-only script avoids a duplicate `sudo -n` attempt after the
    # explicitly authorized refresh, even when that refresh failed.
    raw = ssh_command(host, config, ['python3', '-', '--read-only'], REMOTE_INSPECT, timeout=240)
    data = _decode_inspect(raw, ('os', 'engines'))
    if data['os'].get('family') in ('debian', 'ubuntu'):
        data['os']['metadata'] = 'fresh' if authenticated and not apt_error else 'cached'
        if apt_error:
            data['os']['apt_error'] = apt_error
        elif not authenticated:
            data['os']['apt_error'] = 'Package indexes not refreshed: sudo authorization was not provided.'
    engines = data['engines']
    # Elevated checks are deliberate and only occur after a sudo-authorized
    # scan. Podman rootless stores are isolated per Linux account: scanning
    # the SSH account and root alone cannot establish a complete inventory.
    if authenticated:
        try:
            command = _sudo_args(host, ['python3', '-', '--privileged-engines'], sudo_password)
            output = ssh_command(host, config, command,
                                 (sudo_password + '\n' if sudo_password else '') + REMOTE_INSPECT,
                                 timeout=240)
            elevated = _decode_inspect(output, ('engines',))['engines']
            for engine in ('docker', 'podman'):
                sources = [elevated.get(engine) or {}]
                if engine == 'podman':
                    sources.extend(elevated.get('podman_users') or [])
                _merge_container_scopes(engines, engine, sources)
                if engine == 'podman' and elevated.get('podman_discovery_warning'):
                    previous = engines[engine].get('scope_warning', '')
                    warning = str(elevated['podman_discovery_warning'])[:240]
                    engines[engine]['scope_warning'] = (previous + '; ' + warning if previous else warning)[:500]
        except RemoteError as exc:
            # Keep an accurate, context-specific warning rather than claim
            # that all Docker/Podman contexts have zero containers.
            for engine in ('docker', 'podman'):
                current = engines.setdefault(engine, {})
                current['scope_warning'] = ('Privileged context discovery failed: ' +
                                            sanitize(str(exc), 260))
    else:
        for engine in ('docker', 'podman'):
            if engines.get(engine, {}).get('installed'):
                engines[engine]['scope_warning'] = (
                    'Other Linux users and root contexts were not scanned. '
                    'An authorized sudo scan is required.')
    return data


def inspect_os(host: dict, config: Config) -> dict:
    """Refresh and inspect APT metadata only, with no container queries."""
    raw = ssh_command(host, config, ['python3', '-', '--os-only'], REMOTE_OS_INSPECT, timeout=115)
    try:
        return json.loads(raw)['os']
    except (ValueError, KeyError) as exc:
        raise RemoteError('OS verification returned invalid JSON. Patch stopped before installing packages; scan and retry after the remote Python output is corrected') from exc


def probe_host_key(host: dict) -> list[dict]:
    """Discover public host keys for out-of-band fingerprint confirmation.

    ssh-keyscan does not authenticate the remote host. The displayed
    fingerprints must be verified using a separate trusted channel before the
    operator explicitly accepts them.
    """
    cmd = ["ssh-keyscan", "-T", "6", "-p", str(host["ssh_port"]), host["address"]]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteError(str(exc)) from exc
    keys = []
    for line in p.stdout.splitlines():
        if not line or line.startswith("#") or len(line.split()) < 3:
            continue
        # ssh-keygen can fingerprint stdin, but a private temporary file also
        # works across OpenSSH versions and is deleted after inspection.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as tmp:
            tmp.write(line + "\n")
            filename = tmp.name
        try:
            result = subprocess.run(["ssh-keygen", "-lf", filename, "-E", "sha256"],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) > 1:
                    keys.append({"fingerprint": parts[1], "type": line.split()[1], "key": line})
        finally:
            Path(filename).unlink(missing_ok=True)
    if not keys:
        raise RemoteError(sanitize(p.stderr.strip() or "No SSH host keys discovered"))
    return keys


def accept_host_key(host: dict, config: Config, selected_fingerprint: str) -> dict:
    """Trust only the key currently presented with the exact approved hash.

    A changed known key is rejected rather than silently replaced. The
    operator should investigate potential host replacement or interception.
    """
    key = next((k for k in probe_host_key(host)
                if k["fingerprint"] == selected_fingerprint), None)
    if not key:
        raise RemoteError("SSH host key changed during confirmation. Trust was not saved")
    target = key["key"].split()[0]
    existing = [line for line in config.known_hosts.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#") and line.split()[0] == target]
    if existing and key["key"] not in existing:
        raise RemoteError("A different key is already trusted for this server. Manual investigation required")
    if not existing:
        with config.known_hosts.open("a", encoding="utf-8") as fh:
            fh.write(key["key"] + "\n")
        config.known_hosts.chmod(0o600)
    return {"fingerprint": key["fingerprint"], "type": key["type"]}


def probe_icmp_reachability(host: dict, timeout: float = 2.0) -> dict:
    """Issue one ICMP echo, preserving errors for permission troubleshooting.

    OPM runs as the unprivileged `opm` Unix account even when an administrator
    opens an LXC shell as root. `ping` may succeed in that root shell while
    failing here if the container's ping group range excludes the OPM group.
    We never substitute an SSH/TCP connection and call it an ICMP result.
    """
    observed_at = int(time.time())
    args = ["ping", "-n", "-c", "1", "-W", str(max(1, int(timeout))),
            str(host["address"])]
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                timeout=timeout + 2, check=False)
    except FileNotFoundError:
        return {"state": "unknown", "checked_at": observed_at,
                "detail": "The ping executable is missing on the controller"}
    except PermissionError:
        return {"state": "unknown", "checked_at": observed_at,
                "detail": "Controller's opm account is not permitted to send ICMP ping"}
    except subprocess.TimeoutExpired:
        return {"state": "unknown", "checked_at": observed_at,
                "detail": "ICMP command timed out on the controller"}
    except OSError as exc:
        return {"state": "unknown", "checked_at": observed_at,
                "detail": "ICMP command could not start: " + sanitize(str(exc), 130)}
    if result.returncode == 0:
        return {"state": "up", "checked_at": observed_at,
                "detail": "ICMP echo reply received"}
    error = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
    # Some ping variants use exit status 1 for permissions as well as for
    # missing replies. Inspect stderr first so a blocked ICMP socket cannot
    # be mislabeled as an offline server.
    if any(message in error.lower() for message in (
            "operation not permitted", "permission denied", "socket: permission")):
        detail = "ICMP not permitted for the controller's opm account: " + error
        return {"state": "unknown", "checked_at": observed_at,
                "detail": sanitize(detail, 240)}
    if result.returncode == 1:
        return {"state": "down", "checked_at": observed_at,
                "detail": "No ICMP echo reply (host may block ping)"}
    # On Debian's iputils, exit 2 can mean raw/ping socket permission errors,
    # bad routing, or name-resolution failure, and is NOT a negative echo.
    detail = "ICMP command failed: " + (error or f"exit code {result.returncode}")
    return {"state": "unknown", "checked_at": observed_at,
            "detail": sanitize(detail, 240)}
