"""Authenticated, server-rendered OPM screens.

All remote operations go through the controller's Unix socket. The web layer
never issues SSH or Ansible commands, and never writes the controller's
inventory tables directly. Every mutation requires POST, CSRF validation,
and an authorized Operator or Administrator. A successful HTTP reply is not proof that a
remote patch has completed; the job state is queried separately.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import uuid
from datetime import timedelta
from zoneinfo import available_timezones

from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, password_validation
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.cache import never_cache

from opm.controller import rpc
from opm.validation import sanitize, validate_host
from .models import AuditEvent, LoginFailure
from .timeprefs import (get_configured_timezone, update_configured_timezone,
                        get_validation_preferences, update_validation_preferences)


class HostForm(forms.Form):
    name = forms.CharField(max_length=64, label='Server name')
    address = forms.CharField(max_length=253, label='IP address or hostname')
    ssh_user = forms.CharField(max_length=32, initial='opm', label='Managed SSH user')
    ssh_port = forms.IntegerField(min_value=1, max_value=65535, initial=22, label='SSH port')
    group_name = forms.CharField(max_length=64, initial='Linux VMs', label='Group')
    key_path = forms.CharField(max_length=512, required=False, label='SSH private key path (optional)')
    sudo_mode = forms.ChoiceField(choices=[('password', 'Prompt for sudo password per run'),
                                                ('passwordless', 'Already has passwordless sudo'),
                                                ('read_only', 'Read-only host'), ('root', 'Direct root account')],
                                  initial='password', label='Sudo policy')
    protected = forms.BooleanField(required=False, label='Protected server, extra patch approval')


class BootstrapForm(forms.Form):
    admin_user = forms.CharField(max_length=32, label='Temporary administrator SSH username')
    login_password = forms.CharField(widget=forms.PasswordInput(render_value=False),
                                     label='Temporary administrator SSH password')
    admin_sudo_password = forms.CharField(widget=forms.PasswordInput(render_value=False), required=False,
                                          label='Administrator sudo password (if different)')
    opm_password = forms.CharField(widget=forms.PasswordInput(render_value=False),
                                   min_length=12, label='New remote OPM sudo password')
    confirm = forms.BooleanField(label='I authorize creating the dedicated OPM account')


class EnrollmentForm(forms.Form):
    """Full three-step onboarding, submitted once after identity approval.

    Passwords only exist in the browser's form and this final POST request.
    The form must not be stored in Django sessions or server-side caches.
    """
    name = forms.CharField(max_length=64, label='Server name')
    address = forms.CharField(max_length=253, label='IP address or hostname')
    ssh_port = forms.IntegerField(min_value=1, max_value=65535, initial=22)
    group_name = forms.CharField(max_length=64, initial='Linux VMs')
    protected = forms.BooleanField(required=False)
    admin_user = forms.CharField(max_length=32, label='Temporary administrator SSH username')
    login_password = forms.CharField(widget=forms.PasswordInput(render_value=False),
                                     label='Temporary administrator SSH password')
    admin_sudo_password = forms.CharField(required=False,
        widget=forms.PasswordInput(render_value=False),
        label='Administrator sudo password (leave blank if same as SSH password)')
    account_mode = forms.ChoiceField(choices=[('create', 'Create new account'),
        ('reuse', 'Reuse existing account'), ('replace', 'Delete and recreate account')],
        widget=forms.HiddenInput)
    replace_ack = forms.BooleanField(required=False)
    opm_password = forms.CharField(min_length=1, max_length=1024,
        widget=forms.PasswordInput(render_value=False), label='Remote OPM sudo password')
    opm_password_confirm = forms.CharField(required=False, min_length=1, max_length=1024,
        widget=forms.PasswordInput(render_value=False), label='Confirm new remote OPM sudo password')
    fingerprint = forms.CharField(max_length=120)
    trust_ack = forms.BooleanField(label='I approve trusting this displayed first-use SSH host key')

    def clean(self):
        data = super().clean()
        mode = data.get('account_mode')
        password = data.get('opm_password', '')
        if mode in ('create', 'replace'):
            if len(password) < 12:
                self.add_error('opm_password', 'New account passwords must be at least 12 characters')
            if password != data.get('opm_password_confirm'):
                self.add_error('opm_password_confirm', 'Passwords do not match')
        if mode == 'replace' and not data.get('replace_ack'):
            self.add_error('replace_ack', 'You must explicitly approve deleting the old opm account')
        # Reuse the controller's strict address, username and port validation.
        if all(data.get(field) for field in ('name', 'address', 'ssh_port')):
            try:
                validate_host({'name':data['name'], 'address':data['address'],
                    'ssh_port':data['ssh_port'], 'ssh_user':'opm'})
            except ValueError as exc:
                self.add_error('address', str(exc))
        return data


# Keep the existing Django User flags so upgrades require no schema migration.
# All access checks read these persisted flags on each request.
ACCOUNT_ROLES = (
    ('administrator', 'Administrator (manage OPM and servers)'),
    ('operator', 'Operator (scan, dry run and patch)'),
    ('viewer', 'Viewer (read-only access)'),
)


def _role_for(user):
    if user.is_superuser:
        return 'administrator'
    return 'operator' if user.is_staff else 'viewer'


def _apply_role(user, role):
    if role not in dict(ACCOUNT_ROLES):
        raise ValueError('Unknown account role')
    user.is_superuser = role == 'administrator'
    user.is_staff = role in ('administrator', 'operator')


class AccountForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput(render_value=False), min_length=12)
    role = forms.ChoiceField(choices=ACCOUNT_ROLES, initial='operator', label='Role')

    def clean_username(self):
        value = self.cleaned_data['username']
        if get_user_model().objects.filter(username__iexact=value).exists():
            raise forms.ValidationError('Username is already registered')
        return value

    def clean_password(self):
        pwd = self.cleaned_data['password']
        try:
            password_validation.validate_password(pwd,
                user=get_user_model()(username=self.data.get('username', '')))
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)
        return pwd


class AccountEditForm(forms.Form):
    """Administrators can update roles and optionally reset another password.

    Password values are never prefilled or stored in logs, audit events or
    sessions. Empty password fields leave the current password unchanged.
    """
    role = forms.ChoiceField(choices=ACCOUNT_ROLES, label='Role')
    new_password = forms.CharField(required=False, min_length=12,
        widget=forms.PasswordInput(render_value=False), label='New password (optional)')
    confirm_password = forms.CharField(required=False,
        widget=forms.PasswordInput(render_value=False), label='Confirm new password')

    def __init__(self, *args, account=None, **kwargs):
        self.account = account
        if account is not None and (not args or args[0] is None) and 'initial' not in kwargs:
            kwargs['initial'] = {'role': _role_for(account)}
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        pwd = data.get('new_password', '')
        confirm = data.get('confirm_password', '')
        if pwd != confirm:
            self.add_error('confirm_password', 'Passwords do not match')
        if pwd and self.account is not None:
            try:
                password_validation.validate_password(pwd, user=self.account)
            except ValidationError as exc:
                self.add_error('new_password', exc)
        return data


class WebAccessForm(forms.Form):
    mode = forms.ChoiceField(choices=[
        ('lan', 'LAN / VPN HTTP'),
        ('proxy', 'Internal HTTPS reverse proxy'),
        ('localhost', 'Localhost only'),
    ], label='Access mode')
    hostname = forms.CharField(max_length=253, required=False,
        label='LAN IPv4 address or HTTPS hostname')
    proxy_ip = forms.CharField(max_length=64, required=False,
        label='Reverse proxy source IP')
    confirm_change = forms.BooleanField(required=True,
        label='I understand that changing access mode may disconnect this session')

    def clean(self):
        data = super().clean()
        mode = data.get('mode')
        if mode in ('lan', 'proxy') and not data.get('hostname'):
            self.add_error('hostname', 'Enter the LXC private IP or internal HTTPS hostname')
        if mode == 'proxy' and not data.get('proxy_ip'):
            self.add_error('proxy_ip', 'Enter your reverse proxy source IP')
        if mode == 'lan' and data.get('hostname'):
            try:
                addr = ipaddress.ip_address(data['hostname'])
                if addr.version != 4 or not addr.is_private or addr.is_loopback or addr.is_link_local:
                    raise ValueError('Invalid LAN IPv4 address')
            except ValueError:
                self.add_error('hostname', 'Enter the private IPv4 address of the OPM LXC')
        return data


class TimezoneForm(forms.Form):
    timezone_name = forms.ChoiceField(label='Timezone')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [('America/Toronto', 'America/Toronto')]
        choices.extend((tz, tz) for tz in sorted(available_timezones()) if tz != 'America/Toronto')
        self.fields['timezone_name'].choices = choices


def _call(method: str, **params):
    """One controller RPC per request, isolated from the Django WSGI thread."""
    return asyncio.run(rpc(method, params))


def _actor_action(request, action: str, subject: str = '') -> None:
    """Record operator intent without serializing submitted form secrets."""
    AuditEvent.objects.create(actor=request.user, action=action, subject=str(subject)[:128])


def _operator(request):
    if not request.user.is_staff or not request.user.is_active:
        raise PermissionDenied('An operator account is required')


def _admin(request):
    """Server inventory and SSH setup are administrator operations only."""
    if not request.user.is_superuser or not request.user.is_active:
        raise PermissionDenied('Administrator access required')


def _safe_op(request, method: str, return_to: str, subject: str = '', **params):
    """Share remote error presentation, but never report an operation as
    complete if its underlying controller action raised an exception.
    """
    _operator(request)
    try:
        result = _call(method, **params)
        _actor_action(request, method, subject)
        messages.success(request, f'{method.replace(".", " ").capitalize()} accepted')
        return result
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        messages.error(request, sanitize(str(exc), 300))
        return None


def _credential_scope(request) -> str:
    """Derive a private controller authorization scope for this login session.

    SSH/sudo credentials NEVER enter Django's session or its SQLite-backed
    session table. The controller only receives a digest of this high-entropy
    browser session ID and Django user ID, and retains the password in RAM.
    Signing out or using another browser automatically changes the scope.
    """
    if not request.session.session_key:
        request.session.save()
    token = f"{request.user.pk}:{request.session.session_key}"
    return hashlib.sha256(token.encode()).hexdigest()


def _identity_hash(kind, value):
    return hashlib.sha256((settings.SECRET_KEY + ':' + kind + ':' + value).encode()).hexdigest()


@require_http_methods(['GET', 'POST'])
def login_view(request):
    """Persistent login throttling for private installations.

    The source IP is REMOTE_ADDR, not attacker-controlled X-Forwarded-For.
    Both username-based and source-based counters are enforced. The login
    page never reveals whether a user exists.
    """
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        username = str(request.POST.get('username', ''))[:150]
        password = str(request.POST.get('password', ''))
        ip = str(request.META.get('REMOTE_ADDR', ''))[:120]
        since = timezone.now() - timedelta(minutes=15)
        user_hash = _identity_hash('username', username.casefold())
        ip_hash = _identity_hash('source', ip)
        too_many = (LoginFailure.objects.filter(identifier_hash=user_hash, created__gte=since).count() >= 5
                    or LoginFailure.objects.filter(identifier_hash=ip_hash, created__gte=since).count() >= 20)
        user = None if too_many else authenticate(request, username=username, password=password)
        if user is not None and user.is_active:
            login(request, user)
            return redirect('dashboard')
        # Store no submitted password or raw identifier. Cleanup also limits
        # table growth on systems that are online for months or years.
        with transaction.atomic():
            LoginFailure.objects.bulk_create([
                LoginFailure(identifier_hash=user_hash), LoginFailure(identifier_hash=ip_hash),
            ])
            LoginFailure.objects.filter(created__lt=timezone.now()-timedelta(days=1)).delete()
        messages.error(request, 'Invalid credentials or temporarily locked out. Try again later.')
    return render(request, 'web/login.html')


@login_required
@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')


def _state():
    hosts = _call('hosts.list')
    scans = _call('scans.list')
    latest = {s['host_id']: s for s in scans}
    for host in hosts:
        s = latest.get(host['id'])
        host['scan'] = s
        host['os'] = (s or {}).get('result', {}).get('os', {})
        host['system'] = (s or {}).get('result', {}).get('system', {})
        host['engines'] = (s or {}).get('result', {}).get('engines', {})
        host['updates'] = len(host['os'].get('packages', []))
    return hosts


@login_required
def dashboard(request):
    try:
        hosts = _state()
        jobs = _call('jobs.list')
        activities = _call('activities.list')
        controller = _call('ping')
    except (ValueError, OSError, RuntimeError) as exc:
        hosts, jobs, activities, controller = [], [], [], {'status': 'offline'}
        messages.error(request, 'Controller unavailable: ' + sanitize(str(exc), 240))
    successful = sum(bool(h['scan'] and h['scan']['state'] == 'completed') for h in hosts)
    failed = sum(bool(h['scan'] and h['scan']['state'] == 'failed') for h in hosts)
    active = sum(a['state'] in ('queued', 'running') for a in activities)
    return render(request, 'web/dashboard.html', {'page': 'dashboard', 'hosts': hosts,
        # The dashboard only polls lightweight reachability; do not reload
        # the entire page every eight seconds during a background scan.
        'refresh': False,
        'jobs': jobs[:5], 'activities': activities[:6], 'controller': controller, 'host_count': len(hosts),
        'scan_ok': successful, 'scan_failed': failed, 'updates': sum(h['updates'] for h in hosts),
        'active_jobs': active})


@login_required
@require_GET
@never_cache
def controller_health(request):
    """Authenticated, read-only status of the LOCAL controller Unix socket."""
    from .context import controller_reachable
    ready = controller_reachable()
    return JsonResponse({'status': 'ready' if ready else 'offline'},
                        status=200 if ready else 503)


@login_required
@require_GET
@never_cache
def dashboard_connectivity(request):
    """Poll ICMP reachability only while a dashboard browser tab is open.

    The controller caches probes across tabs and users. We do not refresh
    APT metadata, inspect Docker/Podman or change any scan records. No
    SSH credentials are needed for this read-only ICMP check.
    """
    try:
        results = _call('hosts.reachability')
        return JsonResponse({'hosts': results, 'interval_seconds': 120})
    except (ValueError, OSError, RuntimeError) as exc:
        return JsonResponse({'error': 'Connectivity check unavailable',
                             'detail': sanitize(str(exc), 160)}, status=503)


@login_required
def servers(request):
    try:
        hosts = _state()
    except (ValueError, OSError) as exc:
        hosts = []
        messages.error(request, sanitize(str(exc), 260))
    return render(request, 'web/servers.html', {'page': 'servers', 'hosts': hosts})


@login_required
@require_http_methods(['GET', 'POST'])
def server_add(request):
    """One integrated setup wizard, no incomplete inventory record at step 1."""
    return server_enroll(request)


@login_required
def server_enroll(request, host_id=None):
    _admin(request)
    initial = {}
    if host_id is not None:
        try:
            host = next(h for h in _call('hosts.list') if h['id'] == host_id)
        except StopIteration:
            raise Http404('Unknown server')
        if host['verified']:
            messages.info(request, 'This server is already configured')
            return redirect('server_detail', host_id=host_id)
        initial = {k: host[k] for k in ('name', 'address', 'ssh_port', 'group_name', 'protected')}
    form = EnrollmentForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        values = form.cleaned_data
        try:
            result = _call('hosts.enroll.start', existing_id=host_id or '',
                host={k: values[k] for k in ('name','address','ssh_port','group_name','protected')},
                admin_user=values['admin_user'], login_password=values['login_password'],
                admin_sudo_password=values['admin_sudo_password'],
                opm_password=values['opm_password'], fingerprint=values['fingerprint'],
                account_mode=values['account_mode'], replace_ack=values['replace_ack'])
            _actor_action(request, 'hosts.enroll.start', str(result['activity_id']))
            messages.info(request, 'Server setup started. You can navigate while it runs.')
            return redirect('activity_detail', activity_id=result['activity_id'])
        except (ValueError, OSError, RuntimeError) as exc:
            # Never echo passwords in messages. Sensitive POST fields are not
            # persisted even when a remote provisioning operation fails.
            form.add_error(None, sanitize(str(exc), 260))
    return render(request, 'web/enroll.html', {'page':'servers', 'form':form,
         'resume':host_id is not None, 'existing_id':host_id or ''})


@login_required
@require_POST
def enrollment_account_probe(request):
    """Read-only SSH account discovery, after explicit fingerprint approval.

    Administrator credentials are passed to controller RAM only for the probe.
    They are not retained in SQLite, the Django session, or activity logs.
    """
    _admin(request)
    if request.POST.get('trust_ack') != 'yes':
        return JsonResponse({'error': 'Approve the SSH host fingerprint first'}, status=400)
    try:
        result = _call('hosts.account_probe', address=request.POST.get('address', ''),
            ssh_port=request.POST.get('ssh_port', '22'),
            admin_user=request.POST.get('admin_user', ''),
            login_password=request.POST.get('login_password', ''),
            fingerprint=request.POST.get('fingerprint', ''))
        return JsonResponse({'exists': result['exists']})
    except (ValueError, OSError, RuntimeError) as exc:
        return JsonResponse({'error': sanitize(str(exc), 180)}, status=400)


@login_required
@require_POST
def enrollment_fingerprint(request):
    """Return only a candidate fingerprint, not a trusted connection.

    The endpoint is authenticated, CSRF protected, and uses the controller's
    allowlisted discovery RPC. No submitted SSH credentials are used here.
    """
    _admin(request)
    try:
        keys = _call('hosts.probe_candidate', address=request.POST.get('address',''),
                     ssh_port=request.POST.get('ssh_port','22'))
        ed25519 = [key for key in keys if key['type'] == 'ssh-ed25519']
        if not ed25519:
            raise ValueError('This server did not offer an ED25519 host key. Check its SSH configuration.')
        return JsonResponse({'fingerprint': ed25519[0]['fingerprint'],
                             'key_type': 'ED25519'})
    except (ValueError, OSError, RuntimeError) as exc:
        return JsonResponse({'error': sanitize(str(exc), 240)}, status=400)


@login_required
@require_http_methods(['GET', 'POST'])
def server_edit(request, host_id):
    """Edit stored connection settings without touching the remote server.

    The controller revokes SSH verification when any connection identity
    changes. Running jobs also lock host modification centrally.
    """
    _admin(request)
    try:
        existing = next(h for h in _call('hosts.list') if h['id'] == host_id)
    except StopIteration:
        raise Http404('Unknown server')
    form = HostForm(request.POST or None, initial=existing)
    if request.method == 'POST' and form.is_valid():
        try:
            _call('hosts.update', id=host_id, data=form.cleaned_data)
            _actor_action(request, 'hosts.update', host_id)
            messages.success(request, 'Server profile updated')
            return redirect('server_detail', host_id=host_id)
        except (ValueError, OSError) as exc:
            form.add_error(None, sanitize(str(exc), 400))
    return render(request, 'web/host_form.html', {'page':'servers', 'form':form, 'heading':'Edit server'})


@login_required
def server_detail(request, host_id):
    try:
        host = next(h for h in _state() if h['id'] == host_id)
    except StopIteration:
        raise Http404('Unknown server')
    except (ValueError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 260))
        return redirect('servers')
    return render(request, 'web/host_detail.html', {'page':'servers', 'host':host})


@login_required
@require_POST
def host_action(request, host_id, action):
    _operator(request)
    if action != 'scan':
        _admin(request)
    allowed = {'scan': 'scans.start', 'probe': 'hosts.probe', 'test': 'hosts.test.start',
               'remove': 'hosts.remove', 'ensure_key': 'keys.ensure'}
    if action not in allowed:
        raise Http404()
    if action == 'test':
        # The button appears only for verified hosts, but the backend must
        # enforce that prerequisite too. A forged POST cannot skip setup.
        try:
            verified = next((h.get('verified') for h in _call('hosts.list')
                             if h['id'] == host_id), False)
        except (ValueError, OSError, RuntimeError) as exc:
            messages.error(request, sanitize(str(exc), 240))
            return redirect('server_detail', host_id=host_id)
        if not verified:
            messages.error(request, 'Finish server setup before testing the managed SSH account')
            return redirect('server_detail', host_id=host_id)
    if action == 'scan':
        # Legacy callers should still enter the authenticated scan workflow.
        # Never attempt a background scan without the expected sudo prompt.
        return redirect('/patch/scan/authorization/?host_id=' + host_id)
    if action == 'remove' and request.POST.get('confirm') != 'REMOVE':
        messages.error(request, 'Type REMOVE to confirm inventory removal')
        return redirect('server_detail', host_id=host_id)
    try:
        params = {} if action == 'ensure_key' else {'id': host_id}
        result = _call(allowed[action], **params)
        if action == 'probe':
            request.session['ssh_probe_' + host_id] = result
            messages.info(request, 'Verify the presented fingerprint independently before approving it.')
        else:
            _actor_action(request, allowed[action], host_id)
            messages.info(request, 'SSH test started. Track it under Activity.' if action == 'test'
                          else 'Operation accepted')
        if action == 'remove':
            return redirect('servers')
    except (ValueError, OSError, RuntimeError) as exc:
        messages.error(request, sanitize(str(exc), 300))
    return redirect('server_detail', host_id=host_id)


@login_required
@require_POST
def host_trust(request, host_id):
    _admin(request)
    fingerprint = str(request.POST.get('fingerprint', ''))
    if request.POST.get('verified_out_of_band') != 'yes' or not fingerprint.startswith('SHA256:'):
        messages.error(request, 'Verify the fingerprint on the actual server and explicitly acknowledge it.')
    else:
        _safe_op(request, 'hosts.trust', 'server_detail', host_id, id=host_id, fingerprint=fingerprint)
    return redirect('server_detail', host_id=host_id)


@login_required
@require_POST
def host_bootstrap(request, host_id):
    _admin(request)
    form = BootstrapForm(request.POST)
    if form.is_valid():
        # One-time administrator and new OPM passwords live only in this request
        # and controller memory. They are never put in a session or audit event.
        params = {k: v for k, v in form.cleaned_data.items() if k != 'confirm'}
        result = _safe_op(request, 'hosts.bootstrap', 'server_detail', host_id, id=host_id, **params)
        if result and not result.get('verified'):
            messages.warning(request, result.get('warning', 'Provisioned but not verified'))
    else:
        messages.error(request, 'Check the onboarding form and the explicit authorization box.')
    return redirect('server_detail', host_id=host_id)


@login_required
def patch_manager(request):
    try:
        hosts = _state()
    except (ValueError, OSError) as exc:
        hosts = []
        messages.error(request, sanitize(str(exc), 260))
    total_updates = sum(h['updates'] for h in hosts)
    # Keep eligibility visible without allowing JavaScript to override the
    # server-side Store plan validation. Dry runs may target zero-update hosts,
    # but never unverified or read-only hosts.
    for h in hosts:
        scan = h.get('scan') or {}
        os_result = h.get('os') or {}
        common = (bool(h.get('verified')) and scan.get('state') == 'completed'
                  and os_result.get('family') in ('debian', 'ubuntu')
                  and os_result.get('metadata') in ('fresh', 'cached')
                  and os_result.get('package_query_ok', True) is not False
                  and h.get('sudo_mode') != 'read_only')
        h['dry_run_eligible'] = common
        h['live_eligible'] = common and h['updates'] > 0
    detected_engines = sum(bool(e.get('available')) for h in hosts for e in h['engines'].values())
    return render(request, 'web/patch.html', {'page':'patch', 'hosts':hosts,
        'validation_preferences': get_validation_preferences(),
        'refresh': any(h['scan'] and h['scan']['state']=='running' for h in hosts),
        'total_updates':total_updates, 'detected_engines':detected_engines})


def _scan_targets(host_id: str = '') -> list[dict]:
    """Select eligible hosts using the controller as the inventory authority."""
    hosts = [h for h in _call('hosts.list') if h['verified']]
    if host_id:
        hosts = [h for h in hosts if h['id'] == host_id]
        if not hosts:
            raise Http404('Server is unavailable or has not completed setup')
    return hosts


@login_required
def scan_authorization(request):
    """Prompt in the browser before any scan that needs sudo.

    No password is persisted, and a GET never launches a scan. This keeps
    operator intent explicit even for a one-host scan from the server page.
    """
    _operator(request)
    host_id = str(request.GET.get('host_id', ''))
    try:
        hosts = _scan_targets(host_id)
    except (ValueError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 260))
        return redirect('patch_manager')
    password_hosts = [h for h in hosts if h.get('sudo_mode') == 'password']
    try:
        cached = _call('credentials.status', credential_scope=_credential_scope(request),
                       host_ids=[h['id'] for h in password_hosts])
    except (OSError, RuntimeError, ValueError):
        cached = {}
    for host in password_hosts:
        host['sudo_cached_seconds'] = cached.get(host['id'], 0)
    return render(request, 'web/scan_auth.html', {
        'page': 'patch', 'hosts': hosts, 'host_id': host_id,
        'password_hosts': password_hosts,
    })


@login_required
@require_POST
def scan_all(request):
    """Submit transient scan credentials to the local controller only.

    A single common password can be used for several hosts. Per-host fields
    override it, and neither value is copied into sessions, audit rows, or
    inventory. The controller validates the exact authorized host IDs too.
    """
    _operator(request)
    host_id = str(request.POST.get('host_id', ''))
    try:
        hosts = _scan_targets(host_id)
        shared = str(request.POST.get('shared_sudo_password', ''))
        passwords = {}
        for host in hosts:
            if host.get('sudo_mode') == 'password':
                password = str(request.POST.get('sudo_' + host['id'], '')) or shared
                if password:
                    if len(password) > 2048 or any(ch in password for ch in '\r\n\x00'):
                        raise ValueError('Invalid sudo credential')
                    passwords[host['id']] = password
        result = _call('scans.start', **({'id': host_id} if host_id else {}),
                       sudo_passwords=passwords, credential_scope=_credential_scope(request))
        _actor_action(request, 'scans.start', host_id or 'all')
        messages.info(request, f"Scan started for {result['scheduled']} host(s). View progress in Activity.")
        if host_id:
            return redirect('server_detail', host_id=host_id)
        return redirect('dashboard' if request.POST.get('return_to') == '/' else 'patch_manager')
    except (ValueError, OSError, RuntimeError) as exc:
        messages.error(request, sanitize(str(exc), 300))
        return redirect('scan_authorization' if not host_id else
                        '/patch/scan/authorization/?host_id=' + host_id)


@login_required
@require_POST
def create_plan(request):
    _operator(request)
    ids = list(dict.fromkeys(request.POST.getlist('host_ids')))
    if len(ids) > 100:
        messages.error(request, 'Too many hosts selected')
        return redirect('patch_manager')
    try:
        mode = str(request.POST.get('execution_mode', 'apply'))
        if mode not in ('apply', 'dry_run'):
            raise ValueError('Invalid execution mode')
        prefs = get_validation_preferences()
        plan = _call('plans.create', host_ids=ids, mode=mode,
                     refresh_metadata=(mode == 'dry_run' and
                                       request.POST.get('refresh_metadata') == 'yes'),
                     verbose_dry_run=(mode == 'dry_run' and prefs['verbose_dry_run']))
        _actor_action(request, 'plans.create', plan['id'])
        return redirect('plan_review', plan_id=plan['id'])
    except (ValueError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 400))
        return redirect('patch_manager')


@login_required
def plan_review(request, plan_id):
    _operator(request)
    try:
        plan = _call('plans.get', id=plan_id)
    except (ValueError, OSError):
        raise Http404('Plan unavailable')
    targets = plan['snapshot']['targets']
    password_ids = [t['host_id'] for t in targets if t['sudo_mode'] == 'password']
    try:
        cached = _call('credentials.status', credential_scope=_credential_scope(request),
                       host_ids=password_ids)
    except (ValueError, OSError, RuntimeError):
        cached = {}
    # Expose only remaining seconds in the template, never secrets.
    for target in targets:
        target['sudo_cached_seconds'] = cached.get(target['host_id'], 0)
    return render(request, 'web/review.html', {'page':'patch','plan':plan,
        'targets':targets, 'idempotency':uuid.uuid4().hex,
        'any_protected':any(t['protected'] for t in targets),
        'execution_mode': plan['snapshot'].get('mode', 'apply')})


@login_required
@require_POST
def plan_execute(request, plan_id):
    _operator(request)
    # The confirmed POST is the approval. There is no redundant typed phrase.
    # Do not report success until both approval and detached worker submission
    # return successfully. Never copy password fields to the audit table.
    try:
        plan = _call('plans.get', id=plan_id)
        passwords = {}
        for target in plan['snapshot']['targets']:
            if target['sudo_mode'] == 'password':
                password = request.POST.get('sudo_' + target['host_id'], '')
                if password:
                    passwords[target['host_id']] = password
        # The authenticated, CSRF-protected action button is the confirmation.
        # Protected hosts require their own explicitly labeled submit action.
        _call('plans.approve', id=plan_id, protected_ack=request.POST.get('protected_ack')=='yes')
        _actor_action(request, 'plans.approve', plan_id)
        job = _call('jobs.start', plan_id=plan_id, idempotency=str(request.POST.get('idempotency', '')),
                    sudo_passwords=passwords, credential_scope=_credential_scope(request))
        _actor_action(request, 'jobs.start', job['id'])
        messages.success(request, 'Patch job submitted; monitor its actual state below.')
        return redirect('job_detail', job_id=job['id'])
    except (ValueError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 320))
        return redirect('plan_review', plan_id=plan_id)


@login_required
@require_GET
@never_cache
def activity_feed(request):
    """Non-blocking browser polling for current and recently finished work.

    The controller exposes only curated status values and counters. Web
    sessions, CSRF values, SSH credentials and raw task output never enter
    this endpoint. The browser can navigate freely while this is polled.
    """
    try:
        items = _call('activities.list')
        return JsonResponse({'activities': items}, json_dumps_params={'ensure_ascii': True})
    except (ValueError, OSError, RuntimeError) as exc:
        return JsonResponse({'error': 'Activity status temporarily unavailable',
                             'detail': sanitize(str(exc), 140)}, status=503)


@login_required
@require_GET
def activity_detail(request, activity_id):
    """The stable redirect after scheduling enrollment or a scan.

    Reaching this page is not evidence of successful provisioning. The page
    fetches the actual final state from the persistent controller record.
    """
    try:
        item = _call('activities.get', id=activity_id)
    except (ValueError, OSError, RuntimeError):
        raise Http404('Activity not found')
    return render(request, 'web/activity_detail.html', {'page':'history', 'item': item})


@login_required
def history(request):
    try:
        jobs = _call('jobs.list')
    except (ValueError, OSError) as exc:
        jobs = []
        messages.error(request, sanitize(str(exc), 260))
    return render(request, 'web/history.html', {'page':'history','jobs':jobs})


@login_required
@require_POST
def clear_history(request):
    """Administrator-only deletion of completed patch history.

    Web users cannot call arbitrary controller methods. The controller
    independently refuses deletion during active jobs and keeps inventory,
    scan results, login accounts and the Django audit trail intact.
    """
    if not request.user.is_superuser:
        raise PermissionDenied('Only administrators can clear patch history')
    if request.POST.get('confirm_clear') != 'yes':
        messages.error(request, 'Confirm deletion of completed patch history')
        return redirect('history')
    try:
        result = _call('jobs.clear')
        _actor_action(request, 'history.clear', f"{result['deleted']} jobs")
        if result.get('artifact_cleanup_errors'):
            messages.warning(request, 'History cleared, but some old artifact files could not be removed')
        else:
            messages.success(request, f"Removed {result['deleted']} completed patch job records")
    except (ValueError, RuntimeError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 300))
    return redirect('history')


@login_required
@require_POST
def dismiss_alerts(request):
    """Acknowledge old failures without modifying or deleting their evidence."""
    if not request.user.is_superuser:
        raise PermissionDenied('Only administrators can dismiss failure alerts')
    try:
        result = _call('alerts.acknowledge')
        _actor_action(request, 'alerts.acknowledge', 'historical failures')
        count = result['job_alerts'] + result['activity_alerts']
        messages.success(request, f'Acknowledged {count} historical failure alert(s)')
    except (ValueError, RuntimeError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 300))
    return redirect('history')


@login_required
def job_detail(request, job_id):
    try:
        job = _call('jobs.get', id=job_id)
    except (ValueError, OSError):
        raise Http404('Job unavailable')
    try:
        hostnames = {h['id']: h['name'] for h in _call('hosts.list')}
    except (ValueError, OSError):
        hostnames = {}
    for target in job.get('targets', []):
        target['name'] = hostnames.get(target['host_id'], target['host_id'][:8])
    # Every failed task row can reveal the full redacted message plus the
    # preceding task/verbose events. Retain the original chronological order
    # and keep the selection bounded so expanding an error stays lightweight.
    events = job.get('events') or []
    for index, event in enumerate(events):
        if event.get('level') == 'error':
            event['context_events'] = events[max(0, index - 6):index]
    return render(request, 'web/job_detail.html', {'page':'history','job':job,
        'refresh':job['state'] in ('queued','running')})


@login_required
@require_POST
def cancel_job(request, job_id):
    _operator(request)
    if request.POST.get('confirm') != 'CANCEL':
        messages.error(request, 'Type CANCEL to request cancellation')
    else:
        _safe_op(request, 'jobs.cancel', 'job_detail', job_id, id=job_id)
    return redirect('job_detail', job_id=job_id)


@login_required
@require_http_methods(['GET', 'POST'])
def settings_view(request):
    accounts = []
    form = AccountForm()
    timezone_form = TimezoneForm(initial={'timezone_name': get_configured_timezone()})
    web_access_form = WebAccessForm(initial={
        'mode': getattr(settings, 'OPM_WEB_ACCESS_MODE', 'localhost'),
        'hostname': settings.ALLOWED_HOSTS[0] if settings.ALLOWED_HOSTS else '',
        'proxy_ip': getattr(settings, 'OPM_WEB_TRUSTED_PROXY_IPS', ''),
    })
    if request.method == 'POST':
        if request.POST.get('action') == 'update_timezone':
            if not request.user.is_superuser:
                raise PermissionDenied('Only the administrator can change timezone settings')
            timezone_form = TimezoneForm(request.POST)
            if timezone_form.is_valid():
                tz_name = timezone_form.cleaned_data['timezone_name']
                try:
                    update_configured_timezone(tz_name)
                    _actor_action(request, 'settings.timezone', tz_name)
                    messages.success(request, f'Timezone updated to {tz_name}')
                    return redirect('settings')
                except ValueError as exc:
                    timezone_form.add_error('timezone_name', sanitize(str(exc), 120))
        elif request.POST.get('action') == 'update_web_access':
            if not request.user.is_superuser:
                raise PermissionDenied('Only the administrator can change web access')
            web_access_form = WebAccessForm(request.POST)
            if web_access_form.is_valid():
                params = {k: web_access_form.cleaned_data[k]
                          for k in ('mode', 'hostname', 'proxy_ip')}
                try:
                    result = _call('web.access.configure', **params)
                    _actor_action(request, 'settings.web_access', params['mode'])
                    messages.info(request, 'Web access change scheduled. Your browser may disconnect. '
                        'Open the configured URL after the service restarts.')
                    return redirect('activity_detail', activity_id=result['activity_id'])
                except (ValueError, OSError, RuntimeError) as exc:
                    messages.error(request, sanitize(str(exc), 220))
        elif request.POST.get('action') == 'update_validation':
            if not request.user.is_superuser:
                raise PermissionDenied('Only the administrator can change validation preferences')
            update_validation_preferences(
                default_dry_run=request.POST.get('default_dry_run') == 'yes',
                verbose_dry_run=request.POST.get('verbose_dry_run') == 'yes',
            )
            _actor_action(request, 'settings.validation', 'dry-run defaults')
            messages.success(request, 'Testing and validation preferences saved')
            return redirect('settings')
        elif request.POST.get('action') == 'create_account':
            if not request.user.is_superuser:
                raise PermissionDenied('Only the administrator can add accounts')
            form = AccountForm(request.POST)
            if form.is_valid():
                User = get_user_model()
                with transaction.atomic():
                    role = form.cleaned_data['role']
                    user = User.objects.create_user(username=form.cleaned_data['username'],
                        password=form.cleaned_data['password'],
                        is_staff=role != 'viewer', is_superuser=role == 'administrator')
                    _actor_action(request, 'accounts.create', f'{user.username} ({role})')
                messages.success(request, 'Account created')
                return redirect('settings')
        else:
            return HttpResponseBadRequest('Unknown settings action')
    if request.user.is_superuser:
        accounts = get_user_model().objects.order_by('username')
    return render(request, 'web/settings.html', {
        'page':'settings', 'accounts':accounts, 'form':form,
        'timezone_form': timezone_form, 'current_timezone': get_configured_timezone(),
        'validation_preferences': get_validation_preferences(),
        'web_access_mode': getattr(settings, 'OPM_WEB_ACCESS_MODE', 'legacy'),
        'web_access_hostname': (settings.ALLOWED_HOSTS[0] if settings.ALLOWED_HOSTS else 'Not configured'),
        'web_access_proxy': getattr(settings, 'OPM_WEB_TRUSTED_PROXY_IPS', ''),
        'web_access_form': web_access_form,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def edit_account(request, user_id):
    """Admin-only role and password editing with last-admin protection.

    A role change is effective on the target account's next request. Changing
    a password invalidates existing sessions via Django's session auth hash.
    """
    _admin(request)
    User = get_user_model()
    account = User.objects.filter(pk=user_id).first()
    if account is None:
        raise Http404('Unknown account')
    if account.pk == request.user.pk:
        # Own-password changes require the current password in the existing
        # Change My Password workflow; prevent accidental self-demotion.
        messages.info(request, 'Use Change my password for your own account. Your administrator role cannot be changed here.')
        return redirect('settings')
    form = AccountEditForm(request.POST or None, account=account)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            account = User.objects.select_for_update().get(pk=user_id)
            old_role = _role_for(account)
            new_role = form.cleaned_data['role']
            new_password = form.cleaned_data.get('new_password', '')
            if old_role == 'administrator' and new_role != 'administrator' and account.is_active:
                if User.objects.filter(is_active=True, is_superuser=True).count() <= 1:
                    raise PermissionDenied('Cannot demote the last active administrator')
            modified = []
            if old_role != new_role:
                _apply_role(account, new_role)
                modified.extend(['is_staff', 'is_superuser'])
                _actor_action(request, 'accounts.role.change',
                              f'{account.username}: {old_role} -> {new_role}')
            if new_password:
                account.set_password(new_password)
                modified.append('password')
                _actor_action(request, 'accounts.password.reset', account.username)
            if modified:
                account.save(update_fields=modified)
        if modified:
            messages.success(request, f'Account {account.username} updated. Any password reset takes effect immediately.')
        else:
            messages.info(request, 'No account changes were needed')
        return redirect('settings')
    return render(request, 'web/account_edit.html', {'page': 'settings',
        'account': account, 'form': form, 'role_name': _role_for(account)})


@login_required
@require_POST
def disable_account(request, user_id):
    """Allow administrators to revoke another account without deleting audits."""
    if not request.user.is_superuser:
        raise PermissionDenied('Administrator access required')
    if request.POST.get('confirm') != 'DISABLE':
        messages.error(request, 'Type DISABLE to confirm account revocation')
        return redirect('settings')
    User = get_user_model()
    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=user_id).first()
        if user is None or user.pk == request.user.pk:
            raise PermissionDenied('You cannot deactivate your own account')
        if user.is_superuser and User.objects.filter(is_superuser=True, is_active=True).count() <= 1:
            raise PermissionDenied('The last active administrator cannot be deactivated')
        user.is_active = False
        user.save(update_fields=['is_active'])
        _actor_action(request, 'accounts.disable', user.username)
    messages.success(request, 'Account disabled')
    return redirect('settings')
