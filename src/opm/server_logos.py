"""OPM server branding: registry-independent Docker/Podman icons, cached locally.

Only a Docker Hub exact-repository logo or a known public icon collection may
provide artwork. Image references from either engine are data, never URLs or
commands. The controller does not fetch from arbitrary registries or LAN hosts.
Missing artwork falls back to Linux distro art and is explained in Edit Server.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .config import Config

# Each choice has a stable hash, not an arbitrary URL or filesystem path.
_REPOSITORY = re.compile(r'[a-z0-9_][a-z0-9_.-]{0,126}/[a-z0-9_][a-z0-9_.-]{0,126}\Z')
_PART = re.compile(r'[a-z0-9_][a-z0-9_.-]{0,126}\Z')
_HOSTNAME = re.compile(r'[a-z0-9][a-z0-9.-]{0,250}(?::[0-9]{1,5})?\Z')
_ICON_SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,79}\Z')
_NON_APP_NAMES = {'app', 'server', 'web', 'webapp', 'backend', 'frontend',
                  'worker', 'base', 'latest', 'main', 'docker', 'container', 'image'}
_SUPPORTED_DISTROS = {'debian', 'ubuntu', 'fedora', 'centos', 'opensuse',
                      'suse', 'redhat', 'raspberry-pi', 'linux'}
_MAX_BODY = 512 * 1024
_CACHE_AGE = 30 * 86400
_NEGATIVE_AGE = 86400
# v0.6.8 and early v0.6.9 cached misses before generic icon resolution.
_CACHE_FORMAT = 'v3'

# Known public icon collection, fixed host and path; no user-supplied URLs.
# A matching icon is a public application mark, not a claim of Docker Hub
# endorsement. Some containers don't have a published application icon.
_ICON_URLS = (
    'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/{slug}.png',
    'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/png/{slug}.png',
)



class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def docker_hub_repository(image: str) -> str | None:
    """Return *only* a Docker Hub repository. Ignore other registries.

    Strip the tag/digest, never interpret image names as URLs. Both
    docker.io/example/service and unqualified example/service are supported.
    """
    if not isinstance(image, str) or not image or len(image) > 300:
        return None
    image = image.strip().lower()
    if '@' in image:
        image = image.split('@', 1)[0]
    image = image.rsplit(':', 1)[0] if ':' in image.rsplit('/', 1)[-1] else image
    parts = image.split('/')
    if parts[0] in ('docker.io', 'index.docker.io', 'registry-1.docker.io'):
        parts = parts[1:]
    elif '.' in parts[0] or ':' in parts[0] or parts[0] == 'localhost':
        return None
    if len(parts) == 1:
        parts.insert(0, 'library')
    if len(parts) != 2:
        return None
    value = '/'.join(parts)
    return value if _REPOSITORY.fullmatch(value) else None


def container_repository(image: str) -> str | None:
    """Normalize public/private Docker image references without fetching them.

    Includes GHCR, lscr.io and self-hosted registries as *logo choices*.
    The image's registry hostname is never contacted for icon resolution.
    """
    if not isinstance(image, str) or not image or len(image) > 350:
        return None
    name = image.strip().lower().split('@', 1)[0]
    if ':' in name.rsplit('/', 1)[-1]:
        name = name.rsplit(':', 1)[0]
    parts = name.split('/')
    if not parts or any(not part for part in parts):
        return None
    first = parts[0]
    is_registry = '.' in first or ':' in first or first == 'localhost'
    if not is_registry:
        # Preserve the existing Docker Hub choice IDs for upgraded servers.
        return docker_hub_repository(image)
    if first in ('docker.io', 'index.docker.io', 'registry-1.docker.io'):
        return docker_hub_repository(image)
    if (not _HOSTNAME.fullmatch(first) or len(parts) < 2 or len(parts) > 6
            or any(part in ('.','..') or not _PART.fullmatch(part) for part in parts[1:])):
        return None
    return '/'.join(parts)


def _application_icon_slugs(repo: str) -> tuple[str, ...]:
    """Derive conservative app identity candidates for *any* image registry.

    Only app-like image basenames are tried. A requested slug is considered
    real only if the fixed icon catalogue actually serves a valid PNG.
    Never guess from the namespace/owner avatar or use generic 'server' icons.
    """
    if container_repository(repo) != repo:
        return ()
    name = repo.rsplit('/', 1)[-1]
    slug = re.sub(r'[^a-z0-9]+', '-', name).strip('-')
    candidates = [slug]
    for suffix in ('-server', '-container', '-docker', '-image', '-app', '-ce'):
        if slug.endswith(suffix):
            candidates.append(slug[:-len(suffix)])
    return tuple(dict.fromkeys(c for c in candidates
                   if _ICON_SLUG.fullmatch(c) and c not in _NON_APP_NAMES))

def distro_icon(os_data: dict | None) -> str:
    data = os_data if isinstance(os_data, dict) else {}
    family = str(data.get('family') or '').lower()
    name = str(data.get('name') or '').lower()
    if family in ('rhel', 'redhat', 'red hat') or 'red hat' in name:
        return 'redhat'
    if family in ('raspbian',) or 'raspberry pi' in name:
        return 'raspberry-pi'
    if family in ('opensuse-leap', 'opensuse-tumbleweed') or 'opensuse' in name:
        return 'opensuse'
    if family in ('sles', 'suse'):
        return 'suse'
    if family in _SUPPORTED_DISTROS:
        return family
    if 'ubuntu' in name:
        return 'ubuntu'
    if 'debian' in name:
        return 'debian'
    if 'fedora' in name:
        return 'fedora'
    if 'centos' in name:
        return 'centos'
    return 'linux'


def choices_for_host(host: dict) -> list[dict]:
    """Return stable choices from Docker and Podman scan data.

    Docker application artwork has priority in automatic mode, followed by
    Podman and finally the distro. A shared image has a single logo choice,
    preserving v0.6.9 choice IDs across upgrades and engine changes. This is
    branding only: Podman workloads remain excluded from Compose updates.
    """
    engines = host.get('engines') or {}
    found: dict[str, set[str]] = {}
    if isinstance(engines, dict):
        for engine in ('docker', 'podman'):
            inventory = engines.get(engine) or {}
            discovered = inventory.get('containers') if isinstance(inventory, dict) else []
            if not isinstance(discovered, list):
                continue
            for container in discovered[:500]:
                if not isinstance(container, dict) or container.get('managed_by'):
                    continue  # Ignore vendor-managed infrastructure images.
                repo = container_repository(container.get('image', ''))
                if repo:
                    found.setdefault(repo, set()).add(engine)
    # Keep insertion order by engine (Docker before Podman), not by registry.
    docker_repos = sorted(repo for repo, sources in found.items() if 'docker' in sources)
    podman_repos = sorted(repo for repo, sources in found.items() if 'podman' in sources
                          and 'docker' not in sources)
    options = [{'value': 'auto', 'label': 'Automatic (container logo first, then Linux distro)',
                'repo': '', 'kind': 'auto'}]
    for repo in docker_repos + podman_repos:
        is_hub = bool(_REPOSITORY.fullmatch(repo))
        key = ('hub:' if is_hub else 'image:') + hashlib.sha256(repo.encode()).hexdigest()[:24]
        source = 'Docker Hub' if is_hub else 'Container registry'
        engines_text = ' + '.join(e.title() for e in ('docker', 'podman') if e in found[repo])
        options.append({'value': key, 'label': repo + ' (' + engines_text + ' · ' + source + ')',
                        'repo': repo, 'kind': 'container'})
    os_data = host.get('os') or {}
    family = distro_icon(os_data)
    options.append({'value': 'distro', 'label': family.replace('-', ' ').title() + ' (Linux distribution)',
                    'repo': '', 'kind': 'distro'})
    return options


def selected_repositories(host: dict, requested: str | None = None) -> list[str]:
    """Resolve a stored/manual choice against current scan evidence."""
    options = choices_for_host(host)
    choice = str(requested if requested is not None else host.get('logo_choice') or 'auto')
    if choice == 'distro':
        return []
    matches = [opt for opt in options if opt['value'] == choice and opt['kind'] == 'container']
    if matches:
        return [matches[0]['repo']]
    if choice != 'auto':
        return []  # stale preference or forged choice: safe distro fallback
    return [opt['repo'] for opt in options if opt['kind'] == 'container'][:8]


def _valid_image_host(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        host = parsed.hostname or ''
        return host in {'hub.docker.com', 'd1q6f0aelx0por.cloudfront.net'} or (
            host == 'docker.com' or host.endswith('.docker.com') or
            host.endswith('.dockerusercontent.com') or host.endswith('.dockerstatic.com'))
    except ValueError:
        return False


def _get(url: str, *, accept: str, limit: int, timeout: float) -> bytes:
    # Redirects are deliberately rejected. A response body can never cause us
    # to fetch from a different (possibly internal/private) network location.
    opener = build_opener(_NoRedirect)
    request = Request(url, headers={'Accept': accept, 'User-Agent': 'OPM-LogoCache/0.6.12'})
    with opener.open(request, timeout=timeout) as result:
        if result.status != 200:
            raise ValueError('Unexpected Docker Hub response')
        body = result.read(limit + 1)
    if len(body) > limit:
        raise ValueError('Artwork exceeds size limit')
    return body


def _image_format(raw: bytes) -> str | None:
    """Serve only small raster image types; never SVG, HTML or active content."""
    if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) >= 24:
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9'):
        return 'image/jpeg'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _lookup_hub_logo(repo: str) -> tuple[bytes, str] | None:
    """Best-effort public Docker Hub product artwork; exact slug required.

    The newer repository-information API does not guarantee an application
    logo field. Docker Hub product search exposes optional `logo_url` artwork.
    Absence, ambiguity, private images or untrusted image hosts => no logo.
    """
    if not _REPOSITORY.fullmatch(repo):
        return None
    query = urlencode({'q': repo, 'type': 'image', 'page_size': 25})
    endpoint = 'https://hub.docker.com/api/content/v1/products/search?' + query
    response = json.loads(_get(endpoint, accept='application/json', limit=160_000, timeout=2.5))
    items = response.get('summaries') or response.get('results') or []
    if not isinstance(items, list):
        return None
    for item in items[:25]:
        if not isinstance(item, dict):
            continue
        slug = str(item.get('slug') or '').lower().strip('/')
        # For official-library entries Docker Hub may report just 'nginx'.
        if slug != repo and not (repo.startswith('library/') and slug == repo[8:]):
            continue
        logo = item.get('logo_url') or {}
        url = (logo.get('large') or logo.get('small')) if isinstance(logo, dict) else logo
        if not isinstance(url, str) or not _valid_image_host(url):
            return None
        image = _get(url, accept='image/png,image/jpeg,image/webp', limit=_MAX_BODY, timeout=2.5)
        content_type = _image_format(image)
        if content_type:
            return image, content_type
        return None
    return None


def _lookup_curated_logo(repo: str) -> tuple[bytes, str] | None:
    """Resolve app art for any image registry using derived public icon names.

    Static trusted CDNs, bounded responses, no redirects, no SVG, no arbitrary
    icon URL or registry-derived host. Absence is normal and not an error.
    """
    for slug in _application_icon_slugs(repo):
        for template in _ICON_URLS:
            url = template.format(slug=slug)
            try:
                raw = _get(url, accept='image/png', limit=_MAX_BODY, timeout=2.5)
                if _image_format(raw) == 'image/png':
                    return raw, 'image/png'
            except (OSError, ValueError, TimeoutError):
                continue
    return None


def cached_logo(repo: str, cache_root: Path | None = None) -> tuple[bytes, str] | None:
    """Serve cached application artwork with negative caching and distro fallback."""
    if container_repository(repo) != repo:
        return None
    root = Path(cache_root) if cache_root is not None else Config.load().base / 'logo_cache'
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
    except OSError:
        return None
    key = hashlib.sha256((_CACHE_FORMAT + ':' + repo).encode()).hexdigest()
    path = root / (key + '.img')
    negative = root / (key + '.miss')
    try:
        if path.is_file() and time.time() - path.stat().st_mtime < _CACHE_AGE:
            content = path.read_bytes()
            kind = _image_format(content) if len(content) <= _MAX_BODY else None
            if kind:
                return content, kind
        if negative.is_file() and time.time() - negative.stat().st_mtime < _NEGATIVE_AGE:
            return None
    except OSError:
        return None
    try:
        result = _lookup_hub_logo(repo) if _REPOSITORY.fullmatch(repo) else None
    except (OSError, ValueError, KeyError, TypeError, TimeoutError, json.JSONDecodeError):
        result = None
    if result is None:
        result = _lookup_curated_logo(repo)
    try:
        if result:
            payload, kind = result
            temp = root / (key + '.' + str(os.getpid()) + '.tmp')
            # A temp file + atomic replace avoids partially served logos.
            with temp.open('wb') as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(payload)
            temp.replace(path)
            negative.unlink(missing_ok=True)
            return payload, kind
        negative.touch(mode=0o600)
    except OSError:
        pass
    return None
