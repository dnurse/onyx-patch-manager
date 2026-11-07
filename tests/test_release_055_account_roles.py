"""v0.5.5 account and authorization source contracts, plus Django test-client smoke.

The smoke test uses a temporary SQLite database. It never touches live OPM
state, remote servers, Linux users or SSH keys. Skip it if Django is absent.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'


def _functions(path):
    module = ast.parse(path.read_text())
    return {node.name: node for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _calls(node, name):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == name for n in ast.walk(node))


def test_all_server_management_endpoints_have_admin_guard():
    functions = _functions(WEB / 'views.py')
    for name in ('server_enroll', 'enrollment_fingerprint', 'server_edit',
                 'host_trust', 'host_bootstrap'):
        assert _calls(functions[name], '_admin'), name
    # host_action is allowed for Operators only when action == 'scan'.
    code = ast.get_source_segment((WEB/'views.py').read_text(), functions['host_action'])
    assert "if action != 'scan':\n        _admin(request)" in code
    assert _calls(functions['scan_all'], '_operator')
    assert _calls(functions['create_plan'], '_operator')


def test_roles_and_account_edit_contract():
    views = (WEB/'views.py').read_text()
    urls = (WEB/'urls.py').read_text()
    settings_html = (WEB/'templates/web/settings.html').read_text()
    editor = (WEB/'templates/web/account_edit.html').read_text()
    assert "('administrator', 'Administrator" in views
    assert "('operator', 'Operator" in views
    assert "('viewer', 'Viewer" in views
    assert 'class AccountEditForm(forms.Form)' in views
    assert "user.is_superuser = role == 'administrator'" in views
    assert "user.is_staff = role in ('administrator', 'operator')" in views
    assert "'accounts.password.reset'" in views
    assert "'accounts.role.change'" in views
    assert "User.objects.filter(is_active=True, is_superuser=True).count() <= 1" in views
    assert "name='edit_account'" in urls
    assert "{% url 'create_account' %}" in settings_html
    assert "name='create_account'" in urls
    assert 'form.username' not in settings_html
    assert (WEB/'templates/web/account_create.html').exists()
    assert "{% url 'edit_account' a.pk %}" in settings_html
    assert 'form.role' in editor and 'form.new_password' in editor
    assert 'form.confirm_password' in editor
    for name in ('servers.html','host_detail.html'):
        text = (WEB/'templates/web'/name).read_text()
        assert 'request.user.is_superuser' in text


def test_source_compiles_without_django_import():
    for path in WEB.rglob('*.py'):
        ast.parse(path.read_text())


@pytest.mark.skipif(importlib.util.find_spec('django') is None,
                    reason='Django not installed in offline build environment')
def test_real_django_role_and_permissions_with_temporary_sqlite(tmp_path):
    """Run Django in a fresh Python process so settings cannot leak to tests."""
    code = '''
import django
from django.core.management import call_command
from django.test import Client
from django.contrib.auth import get_user_model

django.setup()
from opm.web import views
call_command('migrate', verbosity=0, interactive=False)
User = get_user_model()
admin = User.objects.create_superuser('owner', 'owner@example.invalid', 'Long!OwnerPassword2026')
operator = User.objects.create_user('operator', password='Long!OperatorPassword2026', is_staff=True)
viewer = User.objects.create_user('viewer', password='Long!ViewerPassword2026')
client = Client()
client.force_login(admin)
resp = client.get('/settings/')
assert resp.status_code == 200, resp.status_code
assert b'Create another account' in resp.content
assert b'Edit' in resp.content
for name, role, superuser, staff in [('admintwo','administrator',True,True),
                                      ('opnew','operator',False,True),
                                      ('watcher','viewer',False,False)]:
    resp = client.post('/settings/', {'action':'create_account','username':name,
                       'password':'C0mpl3x!Password!2026!','role':role})
    assert resp.status_code == 302, (role,resp.status_code)
    fresh = User.objects.get(username=name)
    assert (fresh.is_superuser,fresh.is_staff) == (superuser,staff)

new_client = Client()
new_client.force_login(operator)
assert new_client.get('/servers/add/').status_code == 403
assert new_client.get('/servers/example/edit/').status_code == 403
assert new_client.post('/servers/enroll/fingerprint/', {}).status_code == 403
for action in ('remove','test','probe','ensure_key'):
    assert new_client.post('/servers/example/action/'+action+'/', {}).status_code == 403
assert new_client.post('/servers/example/trust/', {}).status_code == 403
assert new_client.post('/servers/example/bootstrap/', {}).status_code == 403
assert new_client.get('/settings/users/'+str(viewer.pk)+'/edit/').status_code == 403
assert new_client.post('/settings/', {'action':'create_account','username':'bad',
                           'password':'C0mpl3x!Password!2026!','role':'administrator'}).status_code == 403
assert new_client.get('/servers/').status_code in (200,503)

view_client = Client()
view_client.force_login(viewer)
assert view_client.get('/servers/add/').status_code == 403
assert view_client.post('/patch/scan/', {}).status_code == 403

resp=client.get('/settings/users/'+str(operator.pk)+'/edit/')
assert resp.status_code == 200 and b'operator' in resp.content
assert b'Edit operator' in resp.content
reset='N3w!Password!ForOperator2026'
resp=client.post('/settings/users/'+str(operator.pk)+'/edit/',
     {'role':'viewer','new_password':reset,'confirm_password':reset})
assert resp.status_code == 302
operator.refresh_from_db()
assert not operator.is_staff and not operator.is_superuser
assert operator.check_password(reset)
assert new_client.get('/settings/').status_code == 302  # password reset invalidated old session

resp=client.post('/settings/users/'+str(viewer.pk)+'/edit/',
     {'role':'operator','new_password':'OnePassword2026!','confirm_password':'DIFFERENT2026!'})
assert resp.status_code == 200
viewer.refresh_from_db()
assert not viewer.is_staff
resp=client.post('/settings/users/'+str(viewer.pk)+'/edit/',
     {'role':'operator','new_password':'','confirm_password':''})
assert resp.status_code == 302
viewer.refresh_from_db()
assert viewer.is_staff and not viewer.is_superuser

# Self-editing cannot revoke the only active admin; the existing self-password
# page remains the supported path for the current administrator.
assert client.get('/settings/users/'+str(admin.pk)+'/edit/').status_code == 302
assert User.objects.filter(is_superuser=True,is_active=True).count() >= 1
'''
    env = dict(os.environ, OPM_HOME=str(tmp_path/'opm'),
        OPM_WEB_SECRET_KEY='S'*76,
        OPM_WEB_ACCESS_MODE='lan', OPM_WEB_ALLOWED_HOSTS='testserver,localhost,127.0.0.1',
        DJANGO_SETTINGS_MODULE='opm.web.settings',
        PYTHONPATH=str(ROOT/'src')+os.pathsep+os.environ.get('PYTHONPATH',''))
    result = subprocess.run([sys.executable,'-c',code], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
