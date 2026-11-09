"""v0.6.4: account status, checkbox-only deletion, history attribution, form width."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'
VIEWS = (WEB / 'views.py').read_text()
URLS = (WEB / 'urls.py').read_text()
EDIT = (WEB / 'templates/web/account_edit.html').read_text()
SETTINGS = (WEB / 'templates/web/settings.html').read_text()
CSS = (WEB / 'static/web/styles.css').read_text()


def _view_body(name):
    tree = ast.parse(VIEWS)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(VIEWS, fn)


def test_enabled_and_disabled_accounts_have_correct_actions():
    assert "name='account_status'" in URLS
    assert "name='delete_account'" in URLS
    assert '{% if account.is_active %}' in EDIT
    assert 'value="disable"' in EDIT and 'value="enable"' in EDIT
    assert 'Disable account' in EDIT and 'Enable account' in EDIT
    assert 'Delete account permanently' in EDIT
    assert "{% url 'edit_account' a.pk %}" in SETTINGS
    assert 'data-account-disable-toggle' not in SETTINGS
    assert 'User accounts are stored alongside OPM data in SQLite.' not in SETTINGS


def test_account_deletion_requires_checkbox_not_typed_confirmation():
    assert 'type="checkbox" name="confirm_delete" value="yes" required' in EDIT
    assert 'name="confirm_username"' not in EDIT
    assert 'Type ' not in EDIT
    block = _view_body('delete_account')
    assert "request.POST.get('confirm_delete') != 'yes'" in block
    assert 'confirm_username' not in block
    assert '@login_required' in VIEWS and '@require_POST' in VIEWS
    assert '_admin(request)' in block
    assert 'user.pk == request.user.pk' in block
    assert "User.objects.filter(is_superuser=True, is_active=True).count() <= 1" in block
    assert 'user.delete()' in block


def test_disabling_requires_checkbox_last_admin_protection_and_enable_exists():
    block = _view_body('account_status')
    assert "request.POST.get('confirm_disable') != 'yes'" in block
    assert "action not in ('enable', 'disable')" in block
    assert 'user.pk == request.user.pk' in block
    assert 'Cannot disable the last active administrator' in block
    assert "user.save(update_fields=['is_active'])" in block


def test_deletion_preserves_history_actor_names():
    models = (WEB / 'models.py').read_text()
    migration = (WEB / 'migrations/0002_audit_actor_name.py').read_text()
    assert 'actor_name = models.CharField' in models
    assert 'backfill_actor_names' in migration
    block = _view_body('delete_account')
    assert 'AuditEvent.objects.filter(actor=user).update(actor_name=old_name)' in block
    assert "_actor_action(request, 'accounts.delete', old_name)" in block


def test_shared_full_width_forms_and_nested_disclosure():
    assert '.surface.form-panel > form.form-stack' in CSS
    assert '.surface.account-edit-surface > form.form-stack' in CSS
    assert '.form-stack > .full-width-disclosure' in CSS
    assert 'width: 100%;' in CSS.split('.form-stack > .full-width-disclosure {')[1].split('}', 1)[0]
    for name in ('review_refresh.html', 'scan_auth.html'):
        markup = (WEB / 'templates/web' / name).read_text()
        assert 'class="disclosure full-width-disclosure"' in markup


def test_no_typed_confirmation_fields_remain_in_web_operations():
    host = (WEB / 'templates/web/host_detail.html').read_text()
    job = (WEB / 'templates/web/job_detail.html').read_text()
    for markup in (EDIT, host, job):
        assert 'Type REMOVE' not in markup and 'Type CANCEL' not in markup
    assert 'type="checkbox" name="confirm_remove" value="yes" required' in host
    assert 'name="confirm_cancel" type="checkbox" value="yes"' in job
    assert "request.POST.get('confirm_remove') != 'yes'" in VIEWS
    assert "request.POST.get('confirm_cancel') != 'yes'" in VIEWS
