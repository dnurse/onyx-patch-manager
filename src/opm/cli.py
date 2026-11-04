"""Command-line entry point for operator UI and background controller.

The regular operator needs only `opm` to open the Textual dashboard after
installing and starting the user-level systemd controller service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from .config import Config


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentless Onyx Patch Manager")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("tui", help="Launch mouse-enabled terminal interface")
    sub.add_parser("controller", help="Run local Unix-socket controller")
    web = sub.add_parser("web", help="Django administration (migrate, createsuperuser, check)")
    web.add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("ping", help="Check controller availability")
    inv = sub.add_parser("inventory", help="Print generated JSON Ansible inventory")
    inv.add_argument("--list", action="store_true", help="Ansible dynamic inventory interface")
    inv.add_argument("--host", default=None, help="Ansible dynamic inventory host lookup")
    args = parser.parse_args()
    config = Config.load()
    if args.command == "controller":
        from .controller import Controller
        try:
            asyncio.run(Controller(config).start())
        except KeyboardInterrupt:
            pass
    elif args.command == "web":
        # Forward arguments unchanged to Django. Operators use this for
        # database migration and account creation, never a public signup URL.
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "opm.web.settings")
        from django.core.management import execute_from_command_line
        execute_from_command_line(["opm web", *args.args])
    elif args.command == "ping":
        from .controller import rpc
        print(json.dumps(asyncio.run(rpc("ping")), indent=2))
    elif args.command == "inventory":
        from .inventory import ansible_inventory
        from .store import Store
        hosts = Store(config.database).hosts()
        data = ansible_inventory(hosts, str(config.known_hosts))
        variables = data.get("all", {}).get("hosts", {})
        if args.host is not None:
            output = variables.get(args.host, {})
        elif args.list:
            # A dynamic inventory script's --list response intentionally
            # differs from a static YAML/JSON inventory FILE. Preserve the
            # CLI contract while the worker uses the native file structure.
            output = {"all": {"hosts": list(variables)},
                      "_meta": {"hostvars": variables}}
        else:
            output = data
        print(json.dumps(output, indent=2))
    else:
        from .ui import OPMApp
        OPMApp().run()


if __name__ == "__main__":
    main()
