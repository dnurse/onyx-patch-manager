"""Shared template metadata and lightweight local-controller health."""
import asyncio

from opm import __version__
from opm.controller import rpc


def controller_reachable() -> bool:
    """Real controller Unix-socket ping, not host ICMP or a CSS guess.

    Bound the request: if the controller is hung, page rendering should not
    block for the RPC client's much longer general-operation timeout.
    """
    try:
        result = asyncio.run(asyncio.wait_for(rpc('ping'), timeout=2.5))
        return isinstance(result, dict) and result.get('status') == 'ready'
    except (OSError, ValueError, RuntimeError, KeyError, asyncio.TimeoutError):
        return False


def app_version(request):
    data = {'opm_version': __version__}
    # No controller status disclosure or needless RPC on anonymous login pages.
    if getattr(request.user, 'is_authenticated', False):
        data['controller'] = {'status': 'ready' if controller_reachable() else 'offline'}
    return data
