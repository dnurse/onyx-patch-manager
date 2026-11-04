"""Source-level contract tests for the redesigned Textual UI.

These tests run even when Textual is not installed in a build environment.
They are not a substitute for a real visual and mouse interaction test inside
Debian 13, where OPM's runtime dependencies are available.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

from opm.remote import REMOTE_INSPECT

ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = (ROOT / "src" / "opm" / "ui.py").read_text()


def _pure_ui_helpers() -> dict:
    """Compile only independent helpers, avoiding a runtime Textual import."""
    parsed = ast.parse(UI_SOURCE)
    helpers = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
               and node.name in {"scan_age", "health_label"}]
    module = ast.Module(body=helpers, type_ignores=[])
    compiled = compile(ast.fix_missing_locations(module), "ui.py", "exec")
    namespace = {"time": time}
    exec(compiled, namespace)
    return namespace


def test_modern_layout_has_expected_widgets_and_mouse_targets():
    """Ensure selected server actions remain reachable after the redesign."""
    assert 'id="sidebar"' in UI_SOURCE
    assert 'classes="stat-row"' in UI_SOURCE
    assert 'id=f"host-focus-{host[\'id\']}"' in UI_SOURCE
    assert 'id=f"patch-expand-{host[\'id\']}"' in UI_SOURCE
    assert '"filter-containers"' in UI_SOURCE
    assert '"server-bootstrap-"' in UI_SOURCE
    assert '"plan-start"' in UI_SOURCE


def test_last_seen_is_not_presented_as_live_online():
    """Old SSH verification alone must not render an Online status."""
    helpers = _pure_ui_helpers()
    assert helpers["health_label"]({"verified": 1}, None)[0] == "Not checked"
    assert helpers["health_label"]({"verified": 0}, None)[0] == "SSH setup"
    old_scan = {"state": "completed", "finished": time.time() - 3600}
    assert helpers["health_label"]({"verified": 1}, old_scan)[0] == "Last seen"
    recent_scan = {"state": "completed", "finished": time.time() - 30}
    assert helpers["health_label"]({"verified": 1}, recent_scan)[0] == "Recently online"
    assert helpers["scan_age"](old_scan).startswith("Checked ")


def test_remote_snapshot_requires_no_agent_or_privileges():
    """Read-only status is computed in the existing SSH inspection script."""
    compile(REMOTE_INSPECT, "remote-inspect.py", "exec")
    for label in ("'cpu_count'", "'load_1m'", "'memory_percent'",
                  "'disk_percent'", "'uptime'"):
        assert label in REMOTE_INSPECT
    assert 'system_snapshot()' in REMOTE_INSPECT
