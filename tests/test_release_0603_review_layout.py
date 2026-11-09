"""v0.6.3: all review-panel controls share one horizontal inset."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / 'src/opm/web/static/web/styles.css').read_text()
REVIEW = (ROOT / 'src/opm/web/templates/web/review.html').read_text()


def test_review_card_outer_padding_does_not_double_inset_inner_elements():
    assert '.patch-review-panel {' in CSS
    assert '--review-inset: clamp(18px, 2vw, 28px);' in CSS
    assert 'padding: 0;' in CSS.split('/* v0.6.3:')[1].split('.patch-review-panel {')[1].split('}', 1)[0]
    assert '.patch-review-panel > .surface-heading,' in CSS
    assert '.patch-review-panel > .review-host {' in CSS
    assert 'padding-left: var(--review-inset);' in CSS
    assert 'padding-right: var(--review-inset);' in CSS
    assert 'margin: 20px var(--review-inset);' in CSS
    assert 'padding: 20px var(--review-inset) 24px;' in CSS


def test_nested_protected_warning_and_password_are_not_narrower():
    assert '.patch-review-panel > form.form-stack > .warning-banner {' in CSS
    assert 'margin: 20px 0 6px;' in CSS
    assert '.patch-review-panel > form.form-stack > input[type=password] {' in CSS
    assert 'width: 100%;' in CSS
    assert '{% if any_protected %}<div class="warning-banner">' in REVIEW
    assert 'type="password"' in REVIEW


def test_mobile_alignment_and_unchanged_approval_controls():
    assert '.patch-review-panel { --review-inset: 16px; }' in CSS
    assert 'form.form-stack > .form-actions { flex-wrap: wrap; }' in CSS
    assert 'name="protected_ack" value="yes"' in REVIEW
    assert 'sudo_cached_seconds' in REVIEW
    assert 'data-opm-loading="Submitting approved patch job"' in REVIEW
