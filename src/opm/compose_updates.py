"""Explicit, fail-closed Docker Compose image refresh operations.

Only rootful Docker Compose workloads that match a verified source may be
changed. All command arguments originate from Docker's verified deployment
labels and validated project/service identifiers, not user filesystem paths.
No Compose data, registry credentials or command output leaves the host.
"""
from __future__ import annotations

import json
import re

from .remote import (REMOTE_COMPOSE_VERIFY, RemoteError, _sudo_args,
                     ssh_command)

_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z')
_PROJECT = re.compile(r'[a-z0-9][a-z0-9_-]{0,119}\Z')

# Reuse *exactly* the established read-only identity/hash verification routine
# at the start of the update operation, on the managed host. The v0.6.6
# verifier's last block prints JSON; we instead use its verified functions.
_VERIFY_END = '\ntry:\n    result = verify()\n'
assert REMOTE_COMPOSE_VERIFY.count(_VERIFY_END) == 1
_VERIFY_SOURCE = REMOTE_COMPOSE_VERIFY.split(_VERIFY_END, 1)[0]

REMOTE_COMPOSE_UPDATE = _VERIFY_SOURCE + r'''

EXPECTED_SERVICE = ''


def reply(status, detail):
    return {'status': status, 'detail': detail}


def write(argv, *, cwd, timeout):
    # Never collect/return stdout, stderr, Compose interpolation or registry
    # auth messages from a privileged operation into controller logs.
    try:
        completed = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=timeout, check=False)
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def deployed():
    rc, raw = run(['docker', 'ps', '-aq', '--filter',
                   'label=com.docker.compose.project=' + EXPECTED_PROJECT,
                   '--filter', 'label=com.docker.compose.service=' + EXPECTED_SERVICE])
    if rc or not raw.strip():
        return None
    ids = [s.strip() for s in raw.splitlines() if s.strip()]
    if len(ids) > 128:
        return None
    rc, data = run(['docker', 'inspect', *ids], timeout=40)
    if rc:
        return None
    try:
        values = json.loads(data)
        if not isinstance(values, list) or len(values) != len(ids):
            return None
        for item in values:
            labels = (item.get('Config') or {}).get('Labels') or {}
            if (labels.get('com.docker.compose.project') != EXPECTED_PROJECT or
                labels.get('com.docker.compose.service') != EXPECTED_SERVICE or
                str(labels.get('com.docker.compose.oneoff') or '').lower() == 'true'):
                return None
        return values
    except (ValueError, TypeError, AttributeError):
        return None


def perform():
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', EXPECTED_SERVICE):
        return reply('blocked', 'Invalid service selection')
    verified = verify()
    if verified['status'] != 'verified':
        return reply('blocked', 'Compose source no longer matches the deployment; verify again')
    items = deployed()
    if not items or any((item.get('State') or {}).get('Status') != 'running' for item in items):
        return reply('blocked', 'Selected Compose service is missing or not running')
    if any(str((item.get('Config') or {}).get('Image') or '').lower().startswith((
            'uosserver:', 'localhost/uosserver:', 'docker.io/library/uosserver:'))
           for item in items):
        return reply('blocked', 'Vendor-managed service is excluded')
    if len({(item.get('Config') or {}).get('Image') for item in items}) != 1:
        return reply('blocked', 'Service replicas reference inconsistent images')
    labels = (items[0].get('Config') or {}).get('Labels') or {}
    working = labels['com.docker.compose.project.working_dir']
    files = tuple(labels['com.docker.compose.project.config_files'].split(','))
    base = ['docker', 'compose', '--project-directory', working,
            '-p', EXPECTED_PROJECT]
    for path in files:
        base.extend(('-f', path))
    rc, source = run(base + ['config', '--format', 'json'], cwd=working, timeout=50)
    if rc:
        return reply('blocked', 'Cannot validate the selected Compose image definition')
    try:
        definition = json.loads(source)['services'][EXPECTED_SERVICE]
        image_ref = definition['image']
        if (not isinstance(image_ref, str) or not image_ref or len(image_ref) > 400 or
            'build' in definition or '@sha256:' in image_ref or
            any(c in image_ref for c in '\r\n\x00')):
            return reply('blocked', 'Service uses a build or pinned image; automatic refresh excluded')
    except (ValueError, TypeError, KeyError):
        return reply('blocked', 'Selected service has no safe image reference')
    # Ensure the image in the live service actually belongs to this config,
    # allowing Docker's standard unqualified name normalization only.
    live_ref = str((items[0].get('Config') or {}).get('Image') or '')
    def normalized(value):
        for host in ('docker.io/', 'index.docker.io/', 'registry-1.docker.io/'):
            if value.startswith(host):
                value = value[len(host):]
                break
        value = value.removeprefix('library/')
        # Docker expands untagged references to :latest in Config.Image.
        if '@' not in value and ':' not in value.rsplit('/', 1)[-1]:
            value += ':latest'
        return value
    if normalized(live_ref) != normalized(image_ref):
        return reply('blocked', 'Running image reference differs from Compose source')
    previous_ids = {str(item.get('Image') or '') for item in items}
    if len(previous_ids) != 1 or not next(iter(previous_ids)).startswith('sha256:'):
        return reply('blocked', 'Running image identity is unavailable')
    # A pull only changes the local image store. No stop/recreate until pull
    # succeeds and the source hashes still match the deployed configuration.
    if not write(base + ['pull', '--policy', 'always', '--quiet', EXPECTED_SERVICE],
                 cwd=working, timeout=480):
        return reply('failed', 'Image pull failed; running containers were not recreated')
    rc, image_json = run(['docker', 'image', 'inspect', image_ref], timeout=25)
    if rc:
        return reply('failed', 'Pulled image could not be inspected; no recreation attempted')
    try:
        image_items = json.loads(image_json)
        new_id = str(image_items[0]['Id'])
        if not new_id.startswith('sha256:'):
            raise ValueError('Invalid image identifier')
    except (ValueError, TypeError, IndexError, KeyError):
        return reply('failed', 'Pulled image metadata is invalid; no recreation attempted')
    if new_id in previous_ids:
        return reply('unchanged', 'Registry image matches the running service; nothing restarted')
    if verify()['status'] != 'verified':
        return reply('blocked', 'Compose configuration changed during image pull; no recreation attempted')
    if not write(base + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never',
                         EXPECTED_SERVICE], cwd=working, timeout=240):
        return reply('failed', 'Compose recreate failed; inspect the service before retrying')
    import time
    # Existing service replicas must all be running and healthy (where Docker
    # HEALTHCHECK exists). A status check is never treated as application-level
    # or database migration validation.
    for attempt in range(50):
        current = deployed()
        if current and len(current) == len(items):
            statuses = [(item.get('State') or {}) for item in current]
            if all(s.get('Status') == 'running' and
                   (s.get('Health') is None or s['Health'].get('Status') == 'healthy')
                   for s in statuses):
                if all(str(item.get('Image') or '') == new_id for item in current):
                    return reply('updated', 'Service image refreshed; running/health checks passed')
            if any(s.get('Status') in ('exited', 'dead') or
                   (s.get('Health') or {}).get('Status') == 'unhealthy' for s in statuses):
                return reply('failed', 'Recreated service failed runtime/health checks; manual recovery may be required')
        if attempt != 49:
            time.sleep(3)
    return reply('failed', 'Recreated service was not confirmed healthy; inspect manually')


try:
    outcome = perform()
except Exception:
    # Never leak private YAML, env values, absolute paths or registry output.
    outcome = reply('failed', 'Compose image update encountered an error; inspect the managed server')
print(json.dumps(outcome, separators=(',', ':')))
'''


def update_compose_service(host: dict, config, project: str, service: str,
                           sudo_password: str | None = None) -> dict:
    """Perform one explicitly approved service update; returns curated status.

    Cannot run arbitrary remote commands or accept a filesystem path.
    """
    if not isinstance(project, str) or not _PROJECT.fullmatch(project):
        raise RemoteError('Invalid Docker Compose project')
    if not isinstance(service, str) or not _NAME.fullmatch(service):
        raise RemoteError('Invalid Docker Compose service')
    script = (REMOTE_COMPOSE_UPDATE
              .replace("EXPECTED_PROJECT = ''", 'EXPECTED_PROJECT = ' + json.dumps(project))
              .replace("EXPECTED_SERVICE = ''", 'EXPECTED_SERVICE = ' + json.dumps(service)))
    cmd = _sudo_args(host, ['python3', '-'], sudo_password)
    try:
        response = ssh_command(host, config, cmd,
            ((sudo_password + '\n') if sudo_password else '') + script, timeout=1200)
        result = json.loads(response)
    except (ValueError, TypeError, OSError, RemoteError) as exc:
        raise RemoteError('Compose update connection interrupted; inspect the service before retrying') from exc
    details = {
        'blocked': {'Invalid service selection',
                    'Compose source no longer matches the deployment; verify again',
                    'Selected Compose service is missing or not running',
                    'Vendor-managed service is excluded',
                    'Service replicas reference inconsistent images',
                    'Cannot validate the selected Compose image definition',
                    'Service uses a build or pinned image; automatic refresh excluded',
                    'Selected service has no safe image reference',
                    'Running image reference differs from Compose source',
                    'Running image identity is unavailable',
                    'Compose configuration changed during image pull; no recreation attempted'},
        'failed': {'Image pull failed; running containers were not recreated',
                   'Pulled image could not be inspected; no recreation attempted',
                   'Pulled image metadata is invalid; no recreation attempted',
                   'Compose recreate failed; inspect the service before retrying',
                   'Recreated service failed runtime/health checks; manual recovery may be required',
                   'Recreated service was not confirmed healthy; inspect manually',
                   'Compose image update encountered an error; inspect the managed server'},
        'unchanged': {'Registry image matches the running service; nothing restarted'},
        'updated': {'Service image refreshed; running/health checks passed'},
    }
    if (not isinstance(result, dict) or not isinstance(result.get('status'), str)
            or result.get('detail') not in details.get(result['status'], set())):
        raise RemoteError('Compose update returned an invalid sanitized result')
    return {'status': result['status'], 'detail': result['detail']}
