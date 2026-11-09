"""Read-only classification of Docker Compose projects in OPM scan snapshots.

This is an inventory *candidate* view, not authority to execute Docker commands.
Labels can be forged or stale. Never consider a project update-ready until its
Compose source and service/image definitions have been independently verified
at execution time under the enrolled remote account.
"""
from __future__ import annotations


def docker_compose_projects(engines: dict | None) -> list[dict]:
    """Group Docker-only Compose workloads without revealing label paths.

    Legacy scans containing project/service labels remain visible, but their
    missing provenance flags are treated as unverified, never as permission to
    mutate a workload. Podman, standalone and vendor-managed contexts are not
    eligible for updates.
    """
    if not isinstance(engines, dict):
        return []
    docker = engines.get('docker') or {}
    if not isinstance(docker, dict):
        return []
    projects: dict[tuple[str, str], dict] = {}
    for container in docker.get('containers') or []:
        if not isinstance(container, dict):
            continue
        name = str(container.get('project') or '').strip()
        service = str(container.get('service') or '').strip()
        if not name or not service:
            continue  # standalone containers are never update candidates
        # The discovery script explicitly distinguishes Docker Compose labels
        # from Podman labels. Older scans did not; represent as legacy evidence.
        origin = container.get('compose_origin')
        if origin not in ('docker-compose', None, ''):
            continue
        scope = str(container.get('scope') or 'Docker')
        identity = (scope, name)
        item = projects.setdefault(identity, {
            'name': name[:120], 'scope': scope[:120], 'services': [],
            'vendor_managed': False, 'config_evidence': True,
            'legacy_evidence': False, 'oneoff': False,
            'update_enabled': False,  # deliberately not an executable plan
        })
        vendor = bool(container.get('managed_by')) or 'vendor-managed' in str(container.get('deployment') or '')
        item['vendor_managed'] |= vendor
        item['legacy_evidence'] |= origin is None
        item['oneoff'] |= bool(container.get('compose_oneoff'))
        item['config_evidence'] &= (container.get('compose_has_config_files') is True
                                    and container.get('compose_has_working_dir') is True)
        item['services'].append({
            'name': service[:120],
            'container': str(container.get('name') or '')[:120],
            'image': str(container.get('image') or '')[:220],
            'state': str(container.get('state') or 'unknown')[:50],
            'image_status': str(container.get('update_status') or 'unknown')[:45],
            'vendor_managed': vendor,
        })
    result = []
    for item in projects.values():
        item['services'].sort(key=lambda s: (s['name'].casefold(), s['container'].casefold()))
        if item['vendor_managed']:
            item['status'] = 'Vendor-controlled — excluded'
        elif item['oneoff']:
            item['status'] = 'One-off Compose container — excluded'
        elif item['legacy_evidence'] or not item['config_evidence']:
            item['status'] = 'Compose candidate — source configuration not verified'
        else:
            item['status'] = 'Compose detected — source verification required'
        result.append(item)
    return sorted(result, key=lambda p: (p['name'].casefold(), p['scope'].casefold()))
