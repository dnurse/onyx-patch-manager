"""Dependency-free source checks; real Django integration needs Django installed.

These tests are intentionally separate from live security and UI tests, which
must be run on the target Debian LXC because Django is not available here.
"""
from pathlib import Path
import ast
import subprocess

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'src/opm/web'


def test_python_source_syntax():
    for path in WEB.rglob('*.py'):
        ast.parse(path.read_text())


def test_security_boundaries_in_source():
    views=(WEB/'views.py').read_text()
    settings=(WEB/'settings.py').read_text()
    assert '@login_required' in views
    assert '@require_POST' in views
    assert "_call('plans.approve'" in views
    assert "_call('jobs.start'" in views
    assert 'request.user.is_staff' in views
    assert 'request.user.is_superuser' in views
    assert 'Argon2PasswordHasher' in settings
    assert 'CsrfViewMiddleware' in settings
    assert 'SECRET_KEY = os.environ.get' in settings
    assert 'DEBUG = False' in settings


def test_installer_and_private_service_defaults():
    installer=ROOT/'OPMInstall.sh'
    assert subprocess.run(['bash','-n',str(installer)],capture_output=True).returncode == 0
    script=installer.read_text()
    assert 'loginctl enable-linger' in script
    assert 'state-$TIMESTAMP.db' in script
    assert 'WEB_BIND="127.0.0.1:8765"' in script
    assert 'createsuperuser' in script
    assert 'Active or queued patch jobs' in script
    assert 'opm-web.service' in script


def test_no_javascript_frontend_framework():
    # Only small, locally hosted scripts are used: onboarding step handling
    # and dashboard-only SSH reachability. No frontend framework is present.
    assert sorted(p.name for p in WEB.rglob('*.js')) == ['actions.js', 'activity.js', 'dashboard.js', 'enroll.js', 'patch_manager.js']
    assert not list(WEB.rglob('package.json'))
    assert (WEB/'static/web/styles.css').exists()
    for name in ('login','dashboard','servers','host_detail','patch','review','history','settings'):
        assert (WEB/f'templates/web/{name}.html').exists()
