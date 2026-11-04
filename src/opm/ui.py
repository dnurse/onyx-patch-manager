"""Mouse-first Textual user interface for the local Onyx Patch Manager.

Every button is a local controller request. No OS update or remote command
runs directly in the TUI process. Dynamic rows retain keyboard equivalents,
and the terminal's own mouse support handles clicks and wheel scrolling.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Input, Label, Select, Static

from .controller import rpc


CSS = """
/* Onyx palette: muted charcoal, blue slate, teal emphasis and amber warnings.
   Textual renders in terminal cells, so round borders are the closest honest
   approximation to the original graphical mockup's rounded card corners. */
Screen { background: #101820; color: #E7F1F4; }
#topbar { height: 3; background: #14232C; padding: 0 2; align-vertical: middle; }
#brand { width: 1fr; color: #F0F8FA; text-style: bold; }
#top-status { width: auto; color: #84DBB7; text-align: right; }
#shell { height: 1fr; background: #101820; }
#sidebar { width: 26; min-width: 26; background: #162630; padding: 2 1; }
#sidebar-title { color: #7ED7B6; text-style: bold; margin: 0 1 2 1; }
#sidebar-caption { color: #8FAAB8; margin: 1 1; }
#sidebar Button { width: 100%; height: 3; margin: 0 0 1 0; background: #162630;
    color: #C2D2DA; border: none; text-align: left; content-align: left middle; padding: 0 2; }
#sidebar Button:hover { background: #25423F; color: #E7FFF6; }
#sidebar Button.active { background: #28564C; color: #D1FFEA;
    text-style: bold; border-left: heavy #83E5BF; }
#sidebar #sidebar-health { color: #85D9B9; margin: 2 1 0 1; }
#main { height: 1fr; width: 1fr; padding: 2 3 3 3; scrollbar-color: #3C6A69; }
.page-header { height: auto; margin-bottom: 2; }
.page-title { color: #F3F8FA; text-style: bold; text-style: bold; margin-bottom: 1; }
.page-subtitle { color: #A5BAC5; margin-bottom: 1; }
.section-title { color: #E4F1F5; text-style: bold; margin: 1 0 1 0; }
.caption { color: #9EB4C0; margin-bottom: 1; }
.muted { color: #8CA3B1; }
.good { color: #8ADDB8; }
.held { color: #F3C48A; }
.failed { color: #EBA6A5; }
.info { color: #9BCBE1; }
.note { color: #B4CAD5; }
.stat-row { height: 7; margin: 0 0 2 0; }
.stat-card { width: 1fr; height: 7; background: #1C303C; border: round #35525A;
    padding: 0 2; margin-right: 1; }
.stat-card .stat-number { color: #EAF6F9; text-style: bold; margin-top: 0; }
.stat-card .stat-label { color: #9AB4C0; }
.stat-card .stat-extra { color: #7FD5B4; }
.panel { background: #1A2C37; border: round #354E57; padding: 1 2; margin-bottom: 2; height: auto; }
.panel-heading { color: #EDF6F9; text-style: bold; margin-bottom: 1; }
.status-row { height: auto; margin-bottom: 1; align-vertical: middle; }
.status-row .status-name { width: 2fr; color: #E9F1F5; }
.status-row .status-secondary { width: 2fr; color: #9DB4C2; }
.status-row .status-badge { width: 1fr; text-align: right; }
.host-row { background: #21343F; border: round #36515A; padding: 1 2;
    height: auto; margin-bottom: 1; }
.host-row Button { background: transparent; border: none; min-width: 18; padding: 0 1; }
.host-row Button:hover { background: #2C534E; }
.host-row.selected { background: #24443F; border: round #4D9180; }
.host-row .host-main { width: 2fr; }
.host-row .host-middle { width: 2fr; color: #B9D1DB; }
.host-row .host-right { width: 1fr; text-align: right; }
.detail-card { background: #1A2D37; border: round #3F6167; padding: 1 2; margin-top: 1; margin-bottom: 2; }
.detail-card .section-title { color: #87DEBC; }
.action-row { height: auto; margin: 1 0 2 0; align-vertical: middle; }
.action-row Button { margin-right: 1; min-width: 12; }
Button { background: #273B47; border: round #415865; color: #DCEAF0; min-width: 11; }
Button:hover { background: #335663; color: #F4FCFF; }
Button.-primary { background: #286653; border: round #4A8B78; color: #E4FFF5; }
Button.-primary:hover { background: #328468; }
Button:focus { border: round #98E2C7; }
Input { margin-bottom: 1; border: round #4B6570; background: #11222C; }
Input:focus { border: round #73D7B4; }
Select { margin-bottom: 1; }
Checkbox { margin-bottom: 1; }
.form-card { background: #1B303C; border: round #3D5963; padding: 2 3; margin-bottom: 2; }
.warning-card { background: #302A27; border: round #7E644A; padding: 1 2; margin-bottom: 2; }
.empty-card { background: #1B2E39; border: round #35505B; padding: 2 3; margin: 2 0; }
.split-row { height: auto; }
.split-main { width: 2fr; margin-right: 2; }
.split-side { width: 1fr; }
.divider { color: #3D5661; margin: 1 0; }
Footer { background: #14232C; color: #9CB5C2; }
FooterKey { background: #14232C; color: #ADC5CE; }
FooterKey:hover { background: #28534A; }
/* Narrow terminals retain clickable navigation but give more room to content. */
.compact #sidebar { width: 17; min-width: 17; padding: 1 0; }
.compact #sidebar Button { padding: 0 1; }
.compact #main { padding: 1 1 2 1; }
.compact .split-side { display: none; }
"""


def line(value: str, classes: str = "") -> Static:
    """Render server-supplied strings literally rather than as Rich markup."""
    return Static(str(value), classes=classes, markup=False)


def panel(*children, classes: str = "") -> Vertical:
    """Group related data in a rounded terminal card."""
    return Vertical(*children, classes=("panel " + classes).strip())


def buttons(*pairs: tuple[str, str]) -> Horizontal:
    """Create a compact, consistently spaced row of clickable actions."""
    primary_actions = {"scan-all", "server-new", "server-save", "bootstrap-confirm",
                       "trust-accept", "plan-create", "plan-start"}
    return Horizontal(*(Button(label, id=identifier,
                              variant="primary" if identifier in primary_actions else "default")
                        for label, identifier in pairs), classes="action-row")


def page_header(title: str, subtitle: str) -> Vertical:
    """Use the same heading hierarchy on every page."""
    return Vertical(line(title, "page-title"), line(subtitle, "page-subtitle"), classes="page-header")


def stat_card(number: str, label: str, detail: str = "") -> Vertical:
    """One data-driven overview tile, with no invented placeholder metrics."""
    return Vertical(line(number, "stat-number"), line(label, "stat-label"),
                    line(detail, "stat-extra"), classes="stat-card")


def scan_age(scan: dict | None) -> str:
    """Make cached status visibly different from real-time availability."""
    if not scan or not scan.get("finished"):
        return "Never scanned"
    seconds = max(0, time.time() - scan["finished"])
    if seconds < 60:
        return "Checked just now"
    if seconds < 3600:
        return f"Checked {int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"Checked {int(seconds / 3600)}h ago"
    return f"Checked {int(seconds / 86400)}d ago"


def health_label(host: dict, scan: dict | None) -> tuple[str, str]:
    """Present last OBSERVED reachability, not unverified live presence.

    A successfully saved SSH fingerprint is not proof that the server remains
    online. The scan result always carries its age in the neighboring column.
    """
    if not host.get("verified"):
        return "SSH setup", "held"
    if not scan:
        return "Not checked", "muted"
    if scan["state"] == "running":
        return "Scanning", "info"
    if scan["state"] == "failed":
        return "Scan failed", "failed"
    if scan["state"] == "completed":
        return ("Recently online", "good") if scan.get("finished") and time.time() - scan["finished"] < 900 else ("Last seen", "info")
    return "Unknown", "muted"


class OPMApp(App):
    """Primary operator client, keeping selection state local to this session."""

    TITLE = "Onyx Patch Manager"
    SUB_TITLE = "Private controller, agentless patching"
    CSS = CSS
    BINDINGS = [
        ("ctrl+1", "navigate('dashboard')", "Dashboard"),
        ("ctrl+2", "navigate('servers')", "Servers"),
        ("ctrl+3", "navigate('patch')", "Patch Manager"),
        ("ctrl+4", "navigate('history')", "History"),
        ("ctrl+5", "navigate('settings')", "Settings"),
        ("ctrl+r", "refresh", "Refresh"),
        ("ctrl+q", "quit", "Quit TUI"),
    ]

    def __init__(self):
        super().__init__()
        self.page = "dashboard"
        self.hosts: list[dict] = []
        self.scans: list[dict] = []
        self.jobs: list[dict] = []
        self.selected: set[str] = set()
        self.editing_id: str | None = None
        self.trust_id: str | None = None
        self.bootstrap_id: str | None = None
        self.key_options: list[dict] = []
        self.plan: dict | None = None
        self.open_job: str | None = None
        self.confirm_remove_id: str | None = None
        self.confirm_cancel_id: str | None = None
        self.status_message = ""
        self.focused_host_id: str | None = None
        self.expanded_patch_hosts: set[str] = set()
        self.patch_filter = "all"

    def compose(self) -> ComposeResult:
        # A persistent side rail matches the approved dashboard design, while
        # leaving all controls accessible using a terminal mouse or keyboard.
        with Horizontal(id="topbar"):
            yield Static("ONYX   /   PATCH MANAGER", id="brand", markup=False)
            yield Static("PRIVATE  /  SSH", id="top-status", markup=False)
        with Horizontal(id="shell"):
            with Vertical(id="sidebar"):
                yield Static("OPM  /  MANAGE", id="sidebar-title", markup=False)
                for label, identifier in (("Dashboard", "dashboard"), ("Servers", "servers"),
                                          ("Patch Manager", "patch"), ("History", "history"),
                                          ("Settings", "settings")):
                    yield Button(label, id=f"nav-{identifier}")
                yield Static("CONTROLLER", id="sidebar-caption", markup=False)
                yield Static("Checking connection", id="sidebar-health", markup=False)
            yield VerticalScroll(id="main")
        yield Footer()

    async def on_mount(self) -> None:
        await self.reload()

    def on_resize(self, event) -> None:
        """A narrow terminal uses a smaller rail instead of hiding actions."""
        self.set_class(event.size.width < 105, "compact")

    async def action_navigate(self, page: str) -> None:
        self.page = page
        await self.render_page()

    async def action_refresh(self) -> None:
        await self.reload()

    async def reload(self) -> None:
        """Recover gracefully if the controller is stopped or unavailable."""
        try:
            self.hosts = await rpc("hosts.list")
            self.scans = await rpc("scans.list")
            self.jobs = await rpc("jobs.list")
            self.status_message = "Controller connected"
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            self.status_message = f"Controller unavailable: {exc}"
        await self.render_page()

    def scan_for(self, host_id: str) -> dict | None:
        return next((s for s in self.scans if s["host_id"] == host_id), None)

    async def render_page(self) -> None:
        """Build mouse-operable controls for the selected section and state."""
        main = self.query_one("#main", VerticalScroll)
        await main.remove_children()
        # Style the active page instead of using oversized primary buttons.
        for button in self.query("#sidebar Button"):
            button.set_class(button.id == f"nav-{self.page}", "active")
        self.query_one("#sidebar-health", Static).update(self.status_message)
        if self.page == "dashboard":
            elements = self.dashboard()
        elif self.page == "servers":
            elements = self.server_form() if self.editing_id is not None else self.servers_page()
        elif self.page == "trust":
            elements = self.trust_page()
        elif self.page == "bootstrap":
            elements = self.bootstrap_page()
        elif self.page == "patch":
            elements = self.review_page() if self.plan else self.patch_page()
        elif self.page == "history":
            elements = self.history_page()
        elif self.page == "settings":
            elements = self.settings_page()
        else:
            elements = [line("Unknown page")]
        await main.mount(*elements)

    def dashboard(self) -> list:
        """Show actual cached findings with a dated reachability indicator.

        In particular, do not turn SSH key enrollment into an 'Online' badge:
        only a successful recent scan establishes observed connectivity.
        """
        completed = [scan for scan in self.scans if scan["state"] == "completed"]
        updates = sum(len(scan["result"].get("os", {}).get("packages", []))
                      for scan in completed)
        failures = sum(scan["state"] == "failed" for scan in self.scans)
        active_jobs = sum(job["state"] in ("queued", "running") for job in self.jobs)
        recently_online = sum(scan.get("finished") and time.time() - scan["finished"] < 900
                              for scan in completed)
        container_total = sum(
            len(info.get("containers", []))
            for scan in completed
            for info in scan["result"].get("engines", {}).values()
            if info.get("available"))
        output = [
            page_header("Dashboard", "System overview  /  Latest recorded observations"),
            Horizontal(
                stat_card(str(len(self.hosts)), "Managed servers", "Inventory"),
                stat_card(str(recently_online), "Recently online", "Seen within 15 min"),
                stat_card(str(updates), "OS packages", "From completed scans"),
                stat_card(str(container_total), "Containers", "Docker and Podman"),
                stat_card(str(failures + active_jobs), "Needs attention", f"{failures} scan errors  /  {active_jobs} active jobs"),
                classes="stat-row"),
            buttons(("Scan All", "scan-all"), ("Refresh", "refresh"),
                    ("Open Patch Manager", "nav-patch")),
            line("Server overview", "section-title"),
        ]
        if not self.hosts:
            output.append(Vertical(
                line("No servers registered", "panel-heading"),
                line("Open Servers to add a host, verify its SSH identity and begin scanning.", "caption"),
                buttons(("Add First Server", "server-new")),
                classes="empty-card"))
        else:
            output.append(Horizontal(line("HOST", "muted status-name"),
                                     line("LAST CHECK", "muted status-secondary"),
                                     line("STATUS", "muted status-badge"),
                                     classes="status-row"))
            for host in self.hosts:
                scan = self.scan_for(host["id"])
                state, state_class = health_label(host, scan)
                package_count = len((scan or {}).get("result", {}).get("os", {}).get("packages", []))
                summary = f"{scan_age(scan)}  /  {package_count} packages" if scan and scan["state"] == "completed" else scan_age(scan)
                output.append(Horizontal(
                    Button(host["name"], id=f"host-focus-{host['id']}"),
                    line(summary, "status-secondary"),
                    line(state, "status-badge " + state_class),
                    classes="host-row"))
        if self.jobs:
            recent = []
            for job in self.jobs[:4]:
                tone = "failed" if job["state"] in ("failed", "interrupted") else "good" if job["state"] == "completed" else "info"
                recent.append(Horizontal(
                    line(time.strftime("%b %d %H:%M", time.localtime(job["created"])), "status-secondary"),
                    line(job["state"].replace("_", " "), "status-badge " + tone),
                    classes="status-row"))
            output.extend([line("Recent patch activity", "section-title"), panel(*recent)])
        output.append(line("Status reflects the most recent completed scan. Use Scan All to refresh it.", "caption"))
        return output

    def servers_page(self) -> list:
        """List hosts compactly and show actions for only the selected server."""
        output = [page_header("Servers", "Manage inventory and SSH access without editing YAML"),
                  buttons(("Add Server", "server-new"), ("Refresh", "refresh"))]
        if not self.hosts:
            output.append(Vertical(line("Your inventory is empty", "panel-heading"),
                                   line("Add a Debian/Ubuntu server to start SSH onboarding.", "caption"),
                                   classes="empty-card"))
            return output
        if not any(host["id"] == self.focused_host_id for host in self.hosts):
            self.focused_host_id = self.hosts[0]["id"]
        output.append(line("Managed hosts", "section-title"))
        for host in self.hosts:
            scan = self.scan_for(host["id"])
            state, tone = health_label(host, scan)
            output.append(Horizontal(
                Button(host["name"], id=f"host-focus-{host['id']}"),
                line(f"{host['address']}  /  {host['ssh_user']}  /  {host['group_name']}", "host-middle"),
                line(state, "host-right " + tone),
                classes="host-row" + (" selected" if host["id"] == self.focused_host_id else "")))
        host = next(h for h in self.hosts if h["id"] == self.focused_host_id)
        scan = self.scan_for(host["id"])
        result = (scan or {}).get("result", {})
        stats = result.get("system", {})
        details = [
            line(host["name"] + "  /  Host details", "section-title"),
            line(f"{host['address']}:{host['ssh_port']}  /  {host['ssh_user']}  /  "
                 f"{'SSH key verified' if host['verified'] else 'SSH identity not verified'}", "note"),
            line(f"Sudo: {host.get('sudo_mode', 'passwordless')}  /  "
                 f"{'Protected' if host['protected'] else 'Standard patch policy'}", "caption"),
            line(f"Latest observation: {scan_age(scan)}", "caption"),
        ]
        if stats:
            details.extend([
                line("System snapshot", "section-title"),
                line(f"CPU: {stats.get('cpu_count', 'Unknown')} logical cores   "
                     f"Load (1m): {stats.get('load_1m', 'Unknown')}   "
                     f"Uptime: {stats.get('uptime', 'Unknown')}", "note"),
                line(f"Memory: {stats.get('memory_percent', 'Unknown')}% used   "
                     f"Root disk: {stats.get('disk_percent', 'Unknown')}% used", "note"),
            ])
        if scan and scan["state"] == "completed":
            osdata = result.get("os", {})
            details.append(line(f"{osdata.get('name', 'Unknown operating system')}  /  "
                                f"{len(osdata.get('packages', []))} pending OS packages", "note"))
            for engine in ("docker", "podman"):
                info = result.get("engines", {}).get(engine, {})
                if info.get("available"):
                    details.append(line(f"{engine.title()}: {len(info.get('containers', []))} containers accessible", "note"))
        details.append(buttons(("Edit", f"server-edit-{host['id']}"),
                               ("SSH Key", f"server-probe-{host['id']}"),
                               ("Setup OPM Account", f"server-bootstrap-{host['id']}"),
                               ("Test SSH", f"server-test-{host['id']}"),
                               ("Remove", f"server-remove-{host['id']}")))
        output.append(Vertical(*details, classes="detail-card"))
        if self.confirm_remove_id:
            target = next((h for h in self.hosts if h["id"] == self.confirm_remove_id), None)
            if target:
                output.append(Vertical(
                    line(f"Remove {target['name']} from inventory? The remote machine will not be modified.", "held"),
                    buttons(("Confirm Remove", "server-remove-confirm"),
                            ("Keep Server", "server-remove-cancel")), classes="warning-card"))
        return output

    def server_form(self) -> list:
        host = next((h for h in self.hosts if h["id"] == self.editing_id), None)
        return [
            page_header("Edit Server" if host else "Add Server", "SSH connection and patch policy"),
            line("Save this server, approve its SSH fingerprint, then use Setup OPM Account."
                 " An existing SSH account can also be used instead.", "caption"),
            Label("Friendly name"), Input(value=host["name"] if host else "", id="field-name", placeholder="frigate-vm"),
            Label("IP address or DNS name"), Input(value=host["address"] if host else "", id="field-address", placeholder="192.168.1.20"),
            Label("Managed SSH user"), Input(value=host["ssh_user"] if host else "opm", id="field-user", placeholder="opm"),
            Label("SSH port"), Input(value=str(host["ssh_port"]) if host else "22", id="field-port"),
            Label("Absolute path to SSH private key (optional; SSH agent can be used)"),
            Input(value=host["key_path"] if host else "", id="field-key", placeholder="~/.ssh/id_ed25519"),
            Label("Sudo authentication"), Select([
                ("Ask for password at patch approval", "password"),
                ("Already configured passwordless sudo", "passwordless"),
                ("SSH as root (no sudo)", "root"),
                ("Read-only, never patch", "read_only")],
                value=host.get("sudo_mode", "password") if host else "password",
                allow_blank=False, id="field-sudo"),
            Label("Server group"), Input(value=host["group_name"] if host else "Linux VMs", id="field-group"),
            Checkbox("Protected server: require separate patch approval", value=bool(host and host["protected"]), id="field-protected"),
            buttons(("Save Server", "server-save"), ("Cancel", "server-cancel")),
            line("Security: private keys and SSH passwords are never copied into the inventory database.", "caption"),
        ]

    def trust_page(self) -> list:
        host = next((h for h in self.hosts if h["id"] == self.trust_id), None)
        result = [page_header("Verify SSH Host Identity", "Independent fingerprint approval required"),
                  line(f"Host: {host['name'] if host else 'Unknown'}"),
                  line("SSH key fingerprints from the network are not proof of identity. Compare with the server console or another trusted source.", "held")]
        if self.key_options:
            items = [(f"{k['type']}  {k['fingerprint']}", k["fingerprint"]) for k in self.key_options]
            result.extend([Label("SSH public-key fingerprint"),
                           Select(items, value=items[0][1], id="fingerprint-selection", allow_blank=False),
                           Checkbox("I independently verified this fingerprint", id="fingerprint-approved"),
                           buttons(("Trust Verified Key", "trust-accept"), ("Cancel", "trust-cancel"))])
        else:
            result.append(buttons(("Probe Again", "trust-probe-again"), ("Back", "trust-cancel")))
        return result

    def bootstrap_page(self) -> list:
        """One-time administrator authorization for a dedicated remote user.

        Password widgets mask their contents. All values are sent directly to
        the local controller on explicit approval and are not retained in the
        inventory, TUI preferences or background service settings.
        """
        host = next((h for h in self.hosts if h["id"] == self.bootstrap_id), None)
        if not host:
            return [line("No host selected"), buttons(("Back", "bootstrap-cancel"))]
        return [
            page_header("Setup OPM Account", "One-time administrator authorization"),
            line(f"Target: {host['name']} ({host['address']}:{host['ssh_port']})"),
            line("FIRST: Trust the host SSH fingerprint using SSH Key on the Servers page."
                 " Administrator authentication is used for this setup only.", "held"),
            Label("Existing administrative SSH username"),
            Input(id="bootstrap-user", placeholder="Existing admin user or root"),
            Label("Existing administrator SSH login password"),
            Input(id="bootstrap-login-pass", password=True),
            Label("Existing administrator sudo password (blank for root or passwordless sudo)"),
            Input(id="bootstrap-admin-sudo", password=True),
            Label("Choose NEW dedicated opm sudo password (minimum 12 characters)"),
            Input(id="bootstrap-opm-pass", password=True),
            Label("Confirm new opm sudo password"),
            Input(id="bootstrap-opm-confirm", password=True),
            Checkbox("I approve creation of user opm, installation of its public SSH key, "
                     "and addition to the sudo group with password required",
                     id="bootstrap-approve"),
            buttons(("Create and Verify OPM Account", "bootstrap-confirm"),
                    ("Cancel", "bootstrap-cancel")),
            line("This never replaces your current account, resets existing opm users, "
                 "enables NOPASSWD sudo or disables SSH password login.", "caption"),
        ]

    def patch_page(self) -> list:
        """One combined, filterable workspace for OS and container observations.

        The UI intentionally does not expose a Docker/Podman upgrade button:
        Compose deployment execution is not implemented in this release.
        """
        completed = [s for s in self.scans if s["state"] == "completed"]
        packages_total = sum(len(s["result"].get("os", {}).get("packages", [])) for s in completed)
        container_total = sum(len(engine.get("containers", []))
                              for s in completed for engine in s["result"].get("engines", {}).values()
                              if engine.get("available"))
        changed = sum(1 for s in completed
                      for e in s["result"].get("engines", {}).values()
                      for c in e.get("containers", []) if c.get("update_status") == "changed")
        output = [page_header("Patch Manager", "Scan  /  Select  /  Review  /  Execute"),
                  Horizontal(stat_card(str(packages_total), "OS package updates", "APT"),
                             stat_card(str(changed), "Image changes", "Registry comparison"),
                             stat_card(str(container_total), "Containers found", "Docker and Podman"),
                             classes="stat-row"),
                  buttons(("Scan All", "scan-all"), ("Refresh Results", "refresh")),
                  buttons(("All", "filter-all"), ("OS Updates", "filter-os"),
                          ("Containers", "filter-containers")),
                  line("Latest scan results", "section-title")]
        if not self.hosts:
            output.append(Vertical(line("No hosts to scan", "panel-heading"),
                                   line("Register a server in the Servers section first.", "caption"),
                                   classes="empty-card"))
        for host in self.hosts:
            scan = self.scan_for(host["id"])
            result = scan["result"] if scan and scan["state"] == "completed" else {}
            osdata = result.get("os", {})
            packages = osdata.get("packages", [])
            engines = result.get("engines", {})
            engine_containers = [(engine, c) for engine, info in engines.items()
                                 if info.get("available") for c in info.get("containers", [])]
            eligible = bool(host["verified"] and packages and
                            osdata.get("family") in ("debian", "ubuntu") and
                            osdata.get("metadata") in ("fresh", "cached") and
                            osdata.get("package_query_ok", True) and
                            host.get("sudo_mode") != "read_only" and scan and
                            time.time() - scan["finished"] <= 3600)
            state, tone = health_label(host, scan)
            display = [Horizontal(Button(host["name"], id=f"patch-expand-{host['id']}"),
                                  line(f"{osdata.get('name', 'Not scanned')}  /  {scan_age(scan)}", "host-middle"),
                                  line(state, "host-right " + tone), classes="host-row")]
            if scan and scan["state"] == "failed":
                display.append(line("Scan error: " + scan["error"], "failed"))
            if host["id"] in self.expanded_patch_hosts:
                if self.patch_filter in ("all", "os"):
                    osrows = [line("Operating system", "section-title"),
                              line(f"{len(packages)} package candidates  /  "
                                   f"{'Protected host' if host['protected'] else 'Standard policy'}", "caption")]
                    if osdata.get("apt_error"):
                        osrows.append(line("APT metadata: " + osdata["apt_error"], "held"))
                    if osdata.get("metadata") == "cached":
                        osrows.append(line("Cached indexes. Exact changes are revalidated before patching.", "held"))
                    if packages:
                        osrows.append(Checkbox("Select OS updates for this host", value=host["id"] in self.selected,
                                               disabled=not eligible, id=f"choose-{host['id']}"))
                        osrows.extend(line(f"{pkg['name']}: {pkg['current']} -> {pkg['candidate']}", "note")
                                      for pkg in packages[:20])
                        if len(packages) > 20:
                            osrows.append(line(f"{len(packages) - 20} more packages not shown", "muted"))
                    else:
                        osrows.append(line("No pending OS updates or no completed scan.", "muted"))
                    display.append(Vertical(*osrows, classes="detail-card"))
                if self.patch_filter in ("all", "containers"):
                    container_rows = [line("Containers", "section-title"),
                                      line("Discovery only. No container recreation in v0.3.0.", "held")]
                    if engine_containers:
                        for engine, c in engine_containers:
                            container_rows.append(line(f"{engine.title()}  /  {c['name']}  /  "
                                                       f"{c['state']}  /  {c['health']}", "note"))
                            container_rows.append(line(f"Image: {c['image']}  /  {c['update_status']} ", "caption"))
                    else:
                        container_rows.append(line("No accessible Docker or Podman containers detected.", "muted"))
                    for engine in ("docker", "podman"):
                        info = engines.get(engine)
                        if info and not info.get("available"):
                            container_rows.append(line(f"{engine.title()}: {info.get('error', 'Unavailable')[:120]}", "muted"))
                    display.append(Vertical(*container_rows, classes="detail-card"))
            output.extend(display)
        output.extend([buttons(("Review Selected OS Updates", "plan-create")),
                       line(f"Selected hosts: {len(self.selected)}  /  Reboot: manual  /  Container updates: review only", "caption")])
        return output

    def review_page(self) -> list:
        plan = self.plan or {}
        targets = plan.get("snapshot", {}).get("targets", [])
        output = [line("REVIEW APPROVED OS PATCH TARGETS", "section-title"),
                  line("Ansible APT safe upgrade. No automatic reboot. Package scripts can still restart services.", "held"),
                  line(f"Plan expires in approximately {max(0,int((plan.get('expires', 0) - time.time())/60))} minutes."),
                  *[panel(line(f"{t['name']}: {len(t['packages'])} package candidates"),
                          line("Protected host" if t["protected"] else "Standard host", "caption")) for t in targets],
                  Checkbox("Acknowledge additional risk for protected hosts", id="ack-protected"),
                  line("SUDO AUTHORIZATION (not saved)", "section-title"),
                  line("Enter a common password once if the selected hosts share it. "
                       "For differing passwords, fill in individual overrides.", "caption"),
                  Label("Common sudo password for selected password-protected hosts (optional)"),
                  Input(id="sudo-common", password=True),
                  *[panel(Label(f"{target['name']} sudo password override (optional)"),
                          Input(id=f"sudo-{target['host_id']}", password=True))
                    for target in targets if target.get("sudo_mode") == "password"],
                  Label("Type PATCH to authorize the approved plan"),
                  Input(id="confirmation", placeholder="PATCH"),
                  buttons(("Approve and Start", "plan-start"), ("Back to Selection", "plan-back")),
                  line("Preflight will recheck SSH, sudo, exact package candidates and APT simulation before any modification.", "caption")]
        return output

    def history_page(self) -> list:
        if self.open_job:
            output = [line("JOB DETAILS", "section-title"), buttons(("Back to History", "job-back"), ("Refresh", "refresh"))]
            return output + [line("Loading selected job...")]
        output = [page_header("History", "Patch execution records and diagnostic logs"), buttons(("Refresh", "refresh"))]
        for job in self.jobs:
            output.append(panel(line(f"Job {job['id']}  |  {job['state']}  |  "
                                     f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(job['created']))}"),
                                buttons(("View Status and Logs", f"job-open-{job['id']}"))))
        if not self.jobs:
            output.append(line("No patch jobs have been submitted."))
        return output

    def settings_page(self) -> list:
        return [page_header("Settings", "Controller configuration and feature availability"),
                panel(line("Version: 0.3.0"),
                      line("Controller access: private Unix socket", "caption"),
                      line("OS updates: approved, sequential, no automatic reboot", "caption"),
                      line("Docker/Podman: current SSH user context, review-only scanning", "caption"),
                      line("SSH: strict host-key verification and key/agent authentication", "caption")),
                line("V2: optional LAN/VPN-only web interface. Not enabled in V1.", "caption"),
                line("Controller and application self-updates are not automatic.", "caption")]

    async def show_job(self, job_id: str) -> None:
        """Display durable job state with actionable error details and no fake progress.

        Actual target states come from the controller's job record. A failed
        task remains visible alongside its original error and later rechecks.
        This screen never automatically retries a modifying operation.
        """
        try:
            job = await rpc("jobs.get", {"id": job_id})
        except (OSError, ValueError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.open_job = job_id
        self.page = "history"
        await self.render_page()
        main = self.query_one("#main", VerticalScroll)
        await main.remove_children()
        target_states = [item["state"] for item in job["targets"]]
        completed = sum(value == "completed" for value in target_states)
        active = sum(value in ("running", "queued") for value in target_states)
        failed = sum(value in ("failed", "interrupted", "requires_attention") for value in target_states)
        rows = [
            page_header("Execution Monitor", f"Job {job_id}  /  {job['state'].replace('_', ' ')}"),
            Horizontal(stat_card(str(completed), "Completed", "Verified task result"),
                       stat_card(str(active), "Active or queued", "Worker state"),
                       stat_card(str(failed), "Attention needed", "Failed or interrupted"),
                       classes="stat-row"),
            buttons(("Back to History", "job-back"), ("Refresh Status", f"job-refresh-{job_id}")),
        ]
        if job["state"] in ("queued", "running"):
            if self.confirm_cancel_id == job_id:
                rows.append(Vertical(
                    line("Stop queued work? An active APT transaction is not forcibly terminated.", "held"),
                    buttons(("Confirm Cancellation", f"job-cancel-confirm-{job_id}"),
                            ("Keep Running", f"job-cancel-back-{job_id}")),
                    classes="warning-card"))
            else:
                rows.append(buttons(("Request Safe Cancellation", f"job-cancel-{job_id}")))
        if job["state"] in ("failed", "interrupted", "partially_completed"):
            rows.append(buttons(("Recheck Failed Hosts", f"job-recheck-{job_id}")))
        rows.append(line("Host execution", "section-title"))
        for target in job["targets"]:
            host = next((item for item in self.hosts if item["id"] == target["host_id"]), None)
            name = host["name"] if host else target["host_id"]
            state = target["state"].replace("_", " ")
            tone = "failed" if target["state"] in ("failed", "interrupted", "requires_attention") else (
                "good" if target["state"] == "completed" else "info")
            pieces = [Horizontal(line(name, "status-name"), line(state, "status-badge " + tone),
                                 classes="status-row")]
            if target["error"]:
                pieces.append(line(target["error"], "failed"))
            pieces.append(line("Reboot required" if target["reboot_required"] else
                               "No reboot flag reported", "caption"))
            rows.append(panel(*pieces))
        rows.append(line("Task events", "section-title"))
        events = []
        for event in job["events"]:
            message = (f"{time.strftime('%H:%M:%S', time.localtime(event['timestamp']))}  "
                       f"[{event['level']}]  {event['task']}: {event['message']}")
            events.append(line(message, "failed" if event["level"] == "error" else "caption"))
        rows.append(panel(*events) if events else panel(line("No task events recorded yet.", "muted")))
        if job["state"] in ("failed", "interrupted", "partially_completed"):
            rows.append(Vertical(
                line("Recovery", "section-title"),
                line("Recheck the affected host before approving a new plan. "
                     "Investigate partially completed APT transactions manually.", "held"),
                classes="warning-card"))
        await main.mount(*rows)

    async def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        widget_id = event.checkbox.id or ""
        if widget_id.startswith("choose-"):
            host_id = widget_id[7:]
            if event.value:
                self.selected.add(host_id)
            else:
                self.selected.discard(host_id)

    def input_value(self, identifier: str) -> str:
        return self.query_one(f"#{identifier}", Input).value.strip()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route click and keyboard activation through the same controller API."""
        action = event.button.id or ""
        try:
            if action.startswith("host-focus-"):
                # Clicking any server selects its detailed, actionable profile.
                self.focused_host_id = action[len("host-focus-"):]
                self.page = "servers"
                self.editing_id = None
                await self.render_page()
            elif action.startswith("patch-expand-"):
                host_id = action[len("patch-expand-"):]
                if host_id in self.expanded_patch_hosts:
                    self.expanded_patch_hosts.remove(host_id)
                else:
                    self.expanded_patch_hosts.add(host_id)
                await self.render_page()
            elif action.startswith("filter-"):
                self.patch_filter = action[len("filter-"):]
                await self.render_page()
            elif action.startswith("nav-"):
                self.page = action[4:]
                self.plan = None
                self.editing_id = None
                self.open_job = None
                await self.render_page()
            elif action == "refresh":
                if self.open_job:
                    await self.show_job(self.open_job)
                else:
                    await self.reload()
            elif action == "server-new":
                self.page = "servers"
                self.editing_id = "new"
                await self.render_page()
            elif action.startswith("server-edit-"):
                self.editing_id = action[12:]
                await self.render_page()
            elif action == "server-cancel":
                self.editing_id = None
                await self.render_page()
            elif action == "server-save":
                data = {"name": self.input_value("field-name"), "address": self.input_value("field-address"),
                        "ssh_user": self.input_value("field-user"), "ssh_port": self.input_value("field-port"),
                        "key_path": self.input_value("field-key"), "group_name": self.input_value("field-group"),
                        "sudo_mode": str(self.query_one("#field-sudo", Select).value),
                        "protected": self.query_one("#field-protected", Checkbox).value}
                if self.editing_id == "new":
                    result = await rpc("hosts.add", data)
                    self.notify(f"Added {result['name']} as unverified. Next approve its SSH host key.")
                else:
                    result = await rpc("hosts.update", {"id": self.editing_id, "data": data})
                    self.notify(f"Updated {result['name']}")
                self.editing_id = None
                await self.reload()
            elif action.startswith("server-remove-") and action != "server-remove-confirm" and action != "server-remove-cancel":
                self.confirm_remove_id = action[len("server-remove-"):]
                await self.render_page()
            elif action == "server-remove-cancel":
                self.confirm_remove_id = None
                await self.render_page()
            elif action == "server-remove-confirm":
                await rpc("hosts.remove", {"id": self.confirm_remove_id})
                self.confirm_remove_id = None
                await self.reload()
            elif action.startswith("server-bootstrap-"):
                self.bootstrap_id = action[len("server-bootstrap-"):]
                self.page = "bootstrap"
                await self.render_page()
            elif action == "bootstrap-cancel":
                self.bootstrap_id = None
                self.page = "servers"
                await self.render_page()
            elif action == "bootstrap-confirm":
                if not self.query_one("#bootstrap-approve", Checkbox).value:
                    raise ValueError("Explicit account provisioning approval is required")
                opm_password = self.query_one("#bootstrap-opm-pass", Input).value
                if opm_password != self.query_one("#bootstrap-opm-confirm", Input).value:
                    raise ValueError("New OPM account passwords do not match")
                if len(opm_password) < 12:
                    raise ValueError("OPM account password must be at least 12 characters")
                # Send sensitive inputs only in this explicit submission. The
                # controller will not store these passwords or log the RPC.
                result = await rpc("hosts.bootstrap", {
                    "id": self.bootstrap_id,
                    "admin_user": self.input_value("bootstrap-user"),
                    "login_password": self.query_one("#bootstrap-login-pass", Input).value,
                    "admin_sudo_password": self.query_one("#bootstrap-admin-sudo", Input).value,
                    "opm_password": opm_password,
                })
                # Destroy password fields immediately after a response; any
                # further display is rebuilt without the sensitive values.
                for field in ("bootstrap-login-pass", "bootstrap-admin-sudo",
                              "bootstrap-opm-pass", "bootstrap-opm-confirm"):
                    self.query_one(f"#{field}", Input).value = ""
                self.bootstrap_id = None
                self.page = "servers"
                self.notify("OPM user created and verified" if result["verified"]
                            else result.get("warning", "Account created; connection needs testing"))
                await self.reload()
            elif action.startswith("server-probe-"):
                self.trust_id = action[len("server-probe-"):]
                self.key_options = await rpc("hosts.probe", {"id": self.trust_id})
                self.page = "trust"
                await self.render_page()
            elif action == "trust-probe-again":
                self.key_options = await rpc("hosts.probe", {"id": self.trust_id})
                await self.render_page()
            elif action == "trust-cancel":
                self.page = "servers"
                await self.render_page()
            elif action == "trust-accept":
                if not self.query_one("#fingerprint-approved", Checkbox).value:
                    raise ValueError("Verify the fingerprint using a trusted channel before accepting it")
                fingerprint = self.query_one("#fingerprint-selection", Select).value
                await rpc("hosts.trust", {"id": self.trust_id, "fingerprint": fingerprint})
                self.notify("SSH identity trusted. Use Setup OPM Account, or Test SSH if the account already exists.")
                self.page = "servers"
                await self.render_page()
            elif action.startswith("server-test-"):
                result = await rpc("hosts.test", {"id": action[len("server-test-"):]})
                self.notify(f"SSH connected. Sudo: {result['sudo']}")
                await self.reload()
            elif action == "scan-all":
                result = await rpc("scans.start")
                self.notify(f"Scanning {result['scheduled']} host(s). Refresh to view results.")
            elif action == "plan-create":
                self.plan = await rpc("plans.create", {"host_ids": sorted(self.selected)})
                await self.render_page()
            elif action == "plan-back":
                self.plan = None
                await self.render_page()
            elif action == "plan-start":
                if not self.plan:
                    raise ValueError("No reviewed plan")
                ack = self.query_one("#ack-protected", Checkbox).value
                typed = self.input_value("confirmation")
                await rpc("plans.approve", {"id": self.plan["id"], "confirmation": typed,
                                            "protected_ack": ack})
                common_password = self.query_one("#sudo-common", Input).value
                passwords = {}
                for target in self.plan["snapshot"]["targets"]:
                    if target.get("sudo_mode") == "password":
                        override = self.query_one(f"#sudo-{target['host_id']}", Input).value
                        password = override or common_password
                        if not password:
                            raise ValueError(f"Enter the sudo password for {target['name']}")
                        passwords[target["host_id"]] = password
                job = await rpc("jobs.start", {"plan_id": self.plan["id"],
                                "sudo_passwords": passwords,
                                "idempotency": uuid.uuid4().hex})
                self.query_one("#sudo-common", Input).value = ""
                for host_id in passwords:
                    self.query_one(f"#sudo-{host_id}", Input).value = ""
                self.plan = None
                self.selected.clear()
                self.notify("Approved job submitted to systemd worker")
                await self.show_job(job["id"])
            elif action.startswith("job-open-"):
                await self.show_job(action[len("job-open-"):])
            elif action.startswith("job-refresh-"):
                await self.show_job(action[len("job-refresh-"):])
            elif action == "job-back":
                self.open_job = None
                await self.reload()
            elif action.startswith("job-recheck-"):
                job_id = action[len("job-recheck-"):]
                job = await rpc("jobs.get", {"id": job_id})
                hosts = [t["host_id"] for t in job["targets"] if t["state"] in
                         ("failed", "interrupted", "requires_attention")]
                for host_id in hosts:
                    await rpc("scans.start", {"id": host_id})
                self.notify(f"Read-only recheck requested for {len(hosts)} host(s). Review the results before any new patch plan.")
                await self.show_job(job_id)
            elif action.startswith("job-cancel-confirm-"):
                job_id = action[len("job-cancel-confirm-"):]
                await rpc("jobs.cancel", {"id": job_id})
                self.confirm_cancel_id = None
                self.notify("Cancellation requested. Active package tasks will not be terminated")
                await self.show_job(job_id)
            elif action.startswith("job-cancel-back-"):
                self.confirm_cancel_id = None
                await self.show_job(action[len("job-cancel-back-"):])
            elif action.startswith("job-cancel-"):
                job_id = action[len("job-cancel-"):]
                self.confirm_cancel_id = job_id
                await self.show_job(job_id)
        except (ValueError, OSError, asyncio.TimeoutError) as exc:
            self.notify(str(exc), severity="error", timeout=8)
