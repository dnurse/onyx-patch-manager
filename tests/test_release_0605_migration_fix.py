"""Regression tests for the v0.6.4 upgrade failure with the Django app label."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/opm/web"


def test_audit_migration_uses_registered_app_label_for_dependency_and_historical_model():
    apps = ast.parse((WEB / "apps.py").read_text())
    config = next(n for n in apps.body if isinstance(n, ast.ClassDef) and n.name == "OPMWebConfig")
    label = next(n.value.value for n in config.body if isinstance(n, ast.Assign)
                 for target in n.targets if isinstance(target, ast.Name) and target.id == "label")
    assert label == "opm_web"
    migration = ast.parse((WEB / "migrations/0002_audit_actor_name.py").read_text())
    dependency = next(n.value for n in migration.body if isinstance(n, ast.ClassDef) and n.name == "Migration"
                      for n in n.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "dependencies" for t in n.targets))
    assert ast.literal_eval(dependency) == [(label, "0001_initial")]
    data = (WEB / "migrations/0002_audit_actor_name.py").read_text()
    assert "apps.get_model('opm_web', 'AuditEvent')" in data
    assert "apps.get_model('web', 'AuditEvent')" not in data


def test_first_migration_and_model_remain_intact_for_existing_installations():
    migration = (WEB / "migrations/0001_initial.py").read_text()
    assert "name='AuditEvent'" in migration
    assert "name='LoginFailure'" in migration
    assert "actor_name = models.CharField" in (WEB / "models.py").read_text()
    assert "RunPython(backfill_actor_names" in (WEB / "migrations/0002_audit_actor_name.py").read_text()
