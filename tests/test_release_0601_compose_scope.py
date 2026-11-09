"""Docker Compose-only scope; no container mutation in internal build v0.6.1."""
from pathlib import Path
import json
import subprocess
import sys

import pytest

from opm.compose_inventory import docker_compose_projects
from opm.remote import REMOTE_INSPECT

ROOT = Path(__file__).resolve().parents[1]


def c(project='photoprism', service='app', **extra):
    return {
        'name': service + '-1', 'project': project, 'service': service,
        'scope': 'root', 'state': 'running', 'update_status': 'unknown',
        'compose_origin': 'docker-compose',
        'compose_has_config_files': True, 'compose_has_working_dir': True,
        **extra,
    }


def test_groups_multiple_compose_services_without_enabling_mutation():
    projects=docker_compose_projects({'docker':{'containers':[c(service='web'), c(service='db')]}})
    assert len(projects)==1
    p=projects[0]
    assert p['name']=='photoprism' and [s['name'] for s in p['services']]==['db','web']
    assert p['update_enabled'] is False
    assert 'verification required' in p['status']


def test_podman_and_standalone_docker_never_become_compose_candidates():
    engines={'docker':{'containers':[{'name':'standalone','image':'nginx:latest'},
                                      c(compose_origin='other-compose', project='fake', service='podman')]},
             'podman':{'containers':[c(project='podman-only',service='podman')]}}
    assert docker_compose_projects(engines)==[]


def test_vendor_control_excludes_whole_project_not_just_one_service():
    projects=docker_compose_projects({'docker':{'containers':[c(service='a'),c(service='b',managed_by='unifi-os-server')]}})
    assert len(projects)==1
    assert projects[0]['vendor_managed'] and projects[0]['status']=='Vendor-controlled — excluded'


def test_compose_oneoff_is_excluded():
    projects=docker_compose_projects({'docker':{'containers':[c(compose_oneoff=True)]}})
    assert projects[0]['status']=='One-off Compose container — excluded'


def test_missing_or_old_config_evidence_is_not_trusted():
    bad=docker_compose_projects({'docker':{'containers':[c(compose_has_config_files=False)]}})
    assert 'not verified' in bad[0]['status']
    old=c(); old.pop('compose_origin'); old.pop('compose_has_config_files'); old.pop('compose_has_working_dir')
    result=docker_compose_projects({'docker':{'containers':[old]}})
    assert result[0]['legacy_evidence'] and not result[0]['config_evidence']
    assert result[0]['update_enabled'] is False


def test_all_scopes_remain_view_only_even_if_image_changed():
    projects=docker_compose_projects({'docker':{'containers':[c(update_status='changed')]}})
    assert projects[0]['services'][0]['image_status']=='changed'
    assert projects[0]['update_enabled'] is False


def test_untrusted_inputs_and_no_engines_do_not_crash():
    for invalid in (None, [], {}, {'docker':{}}, {'podman':{'containers':[c()]}},
                    {'docker':{'containers':['bad', None, 3]}}):
        assert docker_compose_projects(invalid)==[]


def test_remote_discovery_records_boolean_only_provenance():
    # No configuration paths or env values are persisted by the new fields.
    assert "'compose_has_config_files': config_files_present if docker_compose else False" in REMOTE_INSPECT
    assert "'compose_has_working_dir': working_dir_present if docker_compose else False" in REMOTE_INSPECT
    assert "'compose_origin': ('docker-compose' if docker_compose else" in REMOTE_INSPECT
    compile(REMOTE_INSPECT, '<remote-inspection>', 'exec')


def test_server_and_patch_views_are_review_only():
    server=(ROOT/'src/opm/web/templates/web/host_detail.html').read_text()
    patch=(ROOT/'src/opm/web/templates/web/patch.html').read_text()
    assert 'compose_project_list' in server
    assert '|compose_projects' in patch
    assert 'read-only' in server.lower() and 'review only' in patch.lower()
    assert 'docker compose up -d' not in server.lower()+patch.lower()
    assert 'compose/verify/' in (ROOT/'src/opm/web/urls.py').read_text()


def test_internal_release_version_consistency():
    import tomllib
    project=tomllib.loads((ROOT/'pyproject.toml').read_text())
    version=project['project']['version']
    assert version=='0.7.0'
    assert f'__version__ = "{version}"' in (ROOT/'src/opm/__init__.py').read_text()
    assert f'OPM_UI_VERSION="{version}"' in (ROOT/'OPMTUI.sh').read_text()
