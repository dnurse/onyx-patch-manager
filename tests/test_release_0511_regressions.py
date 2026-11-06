from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_welcome_uses_existing_install_state_not_env_only():
    install = (ROOT / "OPMInstall.sh").read_text()
    assert 'HAS_EXISTING_INSTALL=0' in install
    assert 'if [[ -f "$ENV_FILE" && -d "$APP_DIR" ]]; then' in install
    assert 'if [[ "$OPM_UI_ACTIVE" == 1 && "$HAS_EXISTING_INSTALL" == 0 ]]; then' in install


def test_maintenance_exposes_complete_uninstall_and_removes_local_account():
    maintenance = (ROOT / "OPMMaintenance.sh").read_text()
    assert '--uninstall --purge-data' in maintenance
    assert 'Complete uninstall (remove database, keys, history, and local opm account)' in maintenance
    assert 'userdel -r "$APP_USER"' in maintenance


def test_sidebar_identity_panel_and_patch_bulk_select_alignment_rules_present():
    styles = (ROOT / "src/opm/web/static/web/styles.css").read_text()
    assert '.sidebar-bottom form {' in styles
    assert 'justify-content:center;' in styles
    assert '.user-detail {' in styles
    assert 'text-align:center' in styles
    assert '.select-all-line {' in styles
    assert 'padding-left: 18px;' in styles
