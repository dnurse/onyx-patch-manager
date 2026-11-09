"""v0.6.11: footer branding stays consistent; GM readiness is explicit."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_sidebar_branding_matches_user_visible_status_and_tooltips():
    page = (ROOT/'src/opm/web/templates/web/base.html').read_text()
    script = (ROOT/'src/opm/web/static/web/controller_health.js').read_text()
    assert '<span>OPM Controller</span>' in page
    assert 'aria-label="OPM Controller ' in page
    assert 'title="OPM Controller ' in page
    assert '`OPM Controller ${ready' in script
    assert '<span>LOCAL CONTROLLER</span>' not in page
    assert 'Local controller ${ready' not in script
    assert "data-health-url=" in page and "controller_health" in page
    assert '20000' in script


def test_gm_checklist_has_working_status_and_explicit_podman_deferral():
    checklist = (ROOT/'GM_READINESS_CHECKLIST.md').read_text()
    for phrase in ('- [x] Fresh', '- [x] **Multi-host Linux APT patch execution**',
                   '- [ ] **In-place upgrade:**', '- [ ] **SSH Repair:**',
                   '- [ ] **Actual changed-image update:**',
                   '- [ ] **Controller/web restart:**',
                   'Podman application logos are NOT working', 'Gold Master',
                   'v1.0.0', 'Test evidence log'):
        assert phrase in checklist
    assert 'GM_READINESS_CHECKLIST.md' in (ROOT/'README.md').read_text()


def test_no_unintentional_containers_or_db_workflow_change():
    readme = (ROOT/'README.md').read_text()
    assert 'Podman logos remain unverified' in readme
    assert 'Podman and standalone Docker remain review-only' in readme
