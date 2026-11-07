"""Read-only display rules for manual SSH recovery.

ICMP reachability is deliberately not an SSH authentication test. Only
positive evidence of failed key authentication should surface the Repair SSH
action. This helper does not attempt SSH, mutate inventory, or expose secrets.
"""

from __future__ import annotations


_KEY_FAILURES = (
    'permission denied (publickey',
    'too many authentication failures',
    'no supported authentication methods',
    'no authentication methods available',
    'unable to authenticate',
    'ssh authentication failed',
    'authentication failed',
    'no more authentication methods available',
)


def is_ssh_key_failure(message: object) -> bool:
    """Conservatively distinguish authentication errors from ICMP/network errors."""
    value = str(message or '').lower()
    return any(part in value for part in _KEY_FAILURES)


def needs_ssh_repair(host: dict, activities: list[dict]) -> bool:
    """Use latest relevant SSH evidence, never ICMP or a stale failure.

    A successful scan, SSH test, or SSH-repair activity after a failure clears
    the action. Older test activities are matched by their fixed host label;
    future records also carry the host ID. If no evidence exists, hide it and
    let an administrator use Test SSH to establish a diagnosis.
    """
    if not host.get('verified') or host.get('ssh_user') != 'opm':
        return False
    evidence: list[tuple[float, bool]] = []
    scan = host.get('scan') or {}
    if scan.get('state') in ('failed', 'completed'):
        when = float(scan.get('finished') or scan.get('started') or 0)
        if scan.get('state') == 'completed':
            evidence.append((when, False))
        elif is_ssh_key_failure(scan.get('error')):
            evidence.append((when, True))

    for activity in activities or []:
        kind = activity.get('kind')
        if kind not in ('ssh_test', 'ssh_repair'):
            continue
        result = activity.get('result') or {}
        if not isinstance(result, dict):
            result = {}
        target = result.get('host_id')
        if target:
            if str(target) != str(host.get('id')):
                continue
        elif kind == 'ssh_test' and activity.get('label') != 'Testing SSH: ' + str(host.get('name', '')):
            continue
        elif kind == 'ssh_repair':
            # Historical repair activities without a host_id cannot be safely
            # associated with an inventory record by their display text.
            continue
        when = float(activity.get('updated') or activity.get('created') or 0)
        if activity.get('state') == 'completed' or (
                kind == 'ssh_repair' and activity.get('state') == 'partial'
                and result.get('ssh_recovered') is True):
            # Repair may be partial only because sudo needs attention, while
            # the managed SSH key itself has already been restored.
            evidence.append((when, False))
        elif activity.get('state') == 'failed' and is_ssh_key_failure(activity.get('detail')):
            evidence.append((when, True))
    return max(evidence, default=(0, False), key=lambda pair: pair[0])[1]
