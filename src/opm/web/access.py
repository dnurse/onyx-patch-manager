"""Application-level protection for OPM's internal HTTPS proxy mode.

The listener must still be restricted by an upstream firewall to the private
reverse-proxy address. This middleware is defense in depth: direct requests
from other addresses are denied even if port 8765 is reachable, and a client
cannot spoof X-Forwarded-Proto to impersonate an HTTPS proxy.
"""
from __future__ import annotations

import ipaddress
from django.conf import settings
from django.http import HttpResponseForbidden


def trusted_proxy_request(remote_addr: str, known_proxy_ips: str) -> bool:
    """Compare only the socket peer, never X-Forwarded-For supplied by clients."""
    try:
        actual = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    for entry in known_proxy_ips.split(','):
        try:
            expected = ipaddress.ip_address(entry.strip())
        except ValueError:
            continue
        if actual == expected:
            return True
    return False


class InternalWebAccessMiddleware:
    """Require configured HTTPS proxy source in reverse-proxy access mode."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, 'OPM_WEB_ACCESS_MODE', 'legacy') == 'proxy':
            remote = request.META.get('REMOTE_ADDR', '')
            # Installer and local health diagnostics may use loopback; the
            # browser cannot authenticate over HTTP with Secure cookies.
            local = remote in ('127.0.0.1', '::1')
            proxied = trusted_proxy_request(
                remote, getattr(settings, 'OPM_WEB_TRUSTED_PROXY_IPS', ''))
            if not (local or proxied):
                return HttpResponseForbidden('OPM is available only via its internal HTTPS reverse proxy.')
            # A genuine proxy must assert that the client reached it over TLS.
            # Django then enforces secure CSRF origins and Secure cookies.
            if proxied and request.META.get('HTTP_X_FORWARDED_PROTO', '').lower() != 'https':
                return HttpResponseForbidden('HTTPS is required for OPM reverse-proxy access.')
            if local:
                request.META.pop('HTTP_X_FORWARDED_PROTO', None)
        return self.get_response(request)
