"""Controller protocol tests validate access to the real SQLite authority."""

import asyncio
import os

from opm.controller import Controller, rpc


def test_dispatch_add_list_without_systemd(repo, host_data):
    store, config = repo
    async def run():
        controller = Controller(config)
        item = await controller.dispatch("hosts.add", host_data)
        records = await controller.dispatch("hosts.list", {})
        assert len(records) == 1 and records[0]["id"] == item["id"]
        ping = await controller.dispatch("ping", {})
        assert ping["status"] == "ready"
    asyncio.run(run())


def test_socket_request_response(repo, host_data):
    """Exercise the same Unix-socket RPC that the Textual UI uses."""
    store, config = repo

    async def run():
        controller = Controller(config)
        server = await asyncio.start_unix_server(controller.handle, path=str(config.socket))
        config.socket.chmod(0o600)
        async with server:
            created = await rpc("hosts.add", host_data, config)
            listed = await rpc("hosts.list", config=config)
            assert listed[0]["id"] == created["id"]
        config.socket.unlink(missing_ok=True)

    asyncio.run(run())
