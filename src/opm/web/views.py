"""Authenticated, server-rendered OPM screens.

All remote operations go through the controller's Unix socket. The web layer
never issues SSH or Ansible commands, and never writes the controller's
inventory tables directly. Every mutation requires POST, CSRF validation,
and an authorized Operator or Administrator. A successful HTTP reply is not proof that a
remote patch has completed; the job state is queried separately.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import ipaddress
import time
import uuid
from datetime import timedelta
from zoneinfo import available_timezones

from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, password_validation
from django.contrib.auth.views import PasswordChangeView
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest, JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.templatetags.static import static
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.cache import never_cache

from opm.controller import rpc
from opm.compose_inventory import docker_compose_projects
from opm.host_csv import CSV_FIELDS, MAX_CSV_BYTES, csv_cell, parse_hosts_csv
from opm.server_logos import choices_for_host, selected_repositories, cached_logo, distro_icon
from opm.validation import sanitize, validate_host
from .redaction import redact_for_viewer
from .ssh_status import needs_ssh_repair
from .models import AuditEvent, LoginFailure
from .timeprefs import (get_configured_timezone, update_configured_timezone,
                        get_validation_preferences, update_validation_preferences,
                        get_server_display_preferences, update_server_display_preferences)


class HostForm(forms.Form):
    name = forms.CharField(max_length=64, label='Server name',
                           help_text='Display label; spaces are allowed. SSH hostname is set separately.')
    address = forms.CharField(max_length=253, label='IP address or hostname')
    ssh_user = forms.CharField(max_length=32, initial='opm', label='Managed SSH user')
    ssh_port = forms.IntegerField(min_value=1, max_value=65535, initial=22, label='SSH port')
    group_name = forms.CharField(max_length=64, initial='Linux VMs', label='Group')
    key_path = forms.CharField(max_length=512, required=False, label='SSH private key path (optional)')
    sudo_mode = forms.ChoiceField(choices=[('password', 'Prompt for sudo password per run'),
                                                ('passwordless', 'Already has passwordless sudo'),
                                                ('read_only', 'Read-only host'), ('root', 'Direct root account')],
                                  initial='password', label='Sudo policy')
    protected = forms.BooleanField(required=False, initial=False, label='Protected server, extra patch approval')
    logo_choice = forms.ChoiceField(required=False, label='Server logo',
                                    choices=[('auto', 'Automatic (container first, distro fallback)')])

    def __init__(self, *args, logo_options=None, **kwargs):
        super().__init__(*args, **kwargs)
        if logo_options is not None:
            self.fields['logo_choice'].choices = [(opt['value'], opt['label']) for opt in logo_options]
        self.fields['logo_choice'].help_text = (
            'Uses Docker and Podman application artwork where available. '
            'If no matching icon is published, the Linux distribution logo is shown. '
            'Check the live preview before saving.')
        self.fields['logo_choice'].widget.attrs['data-opm-logo-select'] = '1'


class SSHRepairForm(forms.Form):
    """Explicit manual recovery of the managed opm SSH key on a verified host.

    SSH host identity is reviewed before administrator credentials are sent.
    Secrets are used for one authenticated controller request and never stored.
    """
    admin_user = forms.CharField(max_length=32, label='Existing administrator SSH username')
    login_password = forms.CharField(max_length=1024,
        widget=forms.PasswordInput(render_value=False), label='Administrator SSH password')
    admin_sudo_password = forms.CharField(max_length=1024, required=False,
        widget=forms.PasswordInput(render_value=False),
        label='Administrator sudo password (if different)')
    opm_password = forms.CharField(required=False, max_length=1024,
        widget=forms.PasswordInput(render_value=False),
        label='Current opm sudo password (optional verification)')
    create_if_missing = forms.BooleanField(required=False, initial=False,
        label='Create a new opm account if it no longer exists')
    new_opm_password = forms.CharField(required=False, max_length=1024,
        widget=forms.PasswordInput(render_value=False), label='New opm sudo password')
    new_opm_password_confirm = forms.CharField(required=False, max_length=1024,
        widget=forms.PasswordInput(render_value=False), label='Confirm new opm password')

    def clean(self):
        data = super().clean()
        if data.get('create_if_missing'):
            password = data.get('new_opm_password') or ''
            if len(password) < 12:
                self.add_error('new_opm_password', 'Use at least 12 characters for a new account')
            if password != data.get('new_opm_password_confirm'):
                self.add_error('new_opm_password_confirm', 'Passwords must match')
        return data
    fingerprint = forms.CharField(max_length=120, widget=forms.HiddenInput)
    trust_ack = forms.BooleanField(label='I verified and approve this SSH host fingerprint')

    def clean_fingerprint(self):
        value = self.cleaned_data['fingerprint']
        if not value.startswith('SHA256:'):
            raise forms.ValidationError('Check and approve the displayed fingerprint before continuing')
        return value


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
    name = forms.CharField(max_length=64, label='Server name',
                           help_text='Display label; spaces are allowed. SSH hostname is set separately.')
    address = forms.CharField(max_length=253, label='IP address or hostname')
    ssh_port = forms.IntegerField(min_value=1, max_value=65535, initial=22)
    group_name = forms.CharField(max_length=64, initial='Linux VMs')
    protected = forms.BooleanField(required=False, initial=False)
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
                self.add_error('name' if str(exc).startswith('Server name') else 'address', str(exc))
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
    AuditEvent.objects.create(actor=request.user, actor_name=request.user.username,
                              action=action, subject=str(subject)[:128])


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


class OPMPasswordChangeView(PasswordChangeView):
    """Self-service password changes remain available to Viewers.

    Viewers cannot open Settings, so a successful change or cancellation
    returns them to Dashboard instead of a newly forbidden Settings page.
    """
    def get_success_url(self):
        return reverse('settings' if self.request.user.is_staff else 'dashboard')


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
            _actor_action(request, 'sessions.login')
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
    _actor_action(request, 'sessions.logout')
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


def _dashboard_ip_order(host: dict):
    """Sort IPv4/IPv6 numerically; place DNS names after IP literals.

    This runs on the controller dataset *before* Viewer redaction. Client
    sorting uses a non-secret ordinal rather than embedding raw IP addresses
    into page attributes visible to Viewers.
    """
    address = str(host.get('address') or '')
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return (2, address.casefold(), str(host.get('name') or '').casefold())
    return (0 if parsed.version == 4 else 1, int(parsed), str(host.get('name') or '').casefold())


def _public_for(request, data):
    """Restrict literal network addresses from read-only Viewer responses.

    Keep the full controller dataset for authenticated Operators/Admins; do
    not expose IPs in HTML, background activity JSON, or diagnostic logs.
    """
    if not request.user.is_staff:
        return redact_for_viewer(data)
    return data


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
    hosts = sorted(hosts, key=_dashboard_ip_order)
    hosts = _public_for(request, hosts)
    activities = _public_for(request, activities)
    jobs = _public_for(request, jobs)
    successful = sum(bool(h['scan'] and h['scan']['state'] == 'completed') for h in hosts)
    failed = sum(bool(h['scan'] and h['scan']['state'] == 'failed') for h in hosts)
    active = sum(a['state'] in ('queued', 'running') for a in activities)
    return render(request, 'web/dashboard.html', {'page': 'dashboard', 'hosts': hosts,
        # The dashboard only polls lightweight reachability; do not reload
        # the entire page every eight seconds during a background scan.
        'refresh': False,
        'jobs': jobs[:5], 'activities': activities[:20], 'controller': controller, 'host_count': len(hosts),
        'scan_ok': successful, 'scan_failed': failed, 'updates': sum(h['updates'] for h in hosts),
        'active_jobs': active, 'server_display': get_server_display_preferences()})


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
        results = _public_for(request, _call('hosts.reachability'))
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
    hosts = _public_for(request, hosts)
    return render(request, 'web/servers.html', {'page': 'servers', 'hosts': hosts,
        'server_display': get_server_display_preferences()})


@login_required
@require_http_methods(['GET', 'POST'])
@never_cache
def server_csv_import(request):
    _admin(request)
    context = {'page': 'servers'}
    if request.method == 'GET':
        return render(request, 'web/server_csv_import.html', context)

    salt = 'opm.server-csv-import'
    try:
        if 'confirm' in request.POST:
            try:
                batch = signing.loads(request.POST['confirm'], salt=salt, max_age=900)
            except signing.BadSignature:
                raise ValueError('This preview expired or is invalid. Preview the CSV again.') from None
            if batch['actor'] != request.user.pk:
                raise PermissionDenied
            results = _call('hosts.csv_import', profiles=batch['profiles'])
            added = results.count('')
            skipped = batch['skipped'] + len(results) - added
            _actor_action(request, 'hosts.csv_import', f'{added} added, {skipped} skipped')
            messages.success(request, f'Added {added} server(s). Skipped {skipped} duplicate(s).')
            return redirect('servers')

        uploaded = request.FILES.get('csv_file')
        text = request.POST.get('csv_text', '')
        if uploaded is not None and text.strip():
            raise ValueError('Choose a CSV file or paste CSV, then preview.')
        if uploaded is not None:
            if uploaded.size > MAX_CSV_BYTES:
                raise ValueError('CSV must be 64 KiB or smaller.')
            try:
                text = uploaded.read().decode('utf-8-sig')
            except UnicodeDecodeError:
                raise ValueError('Save the CSV as UTF-8 and try again.') from None
        rows, errors = parse_hosts_csv(text)
        profiles = [profile for _, profile in rows]
        results = _call('hosts.csv_preview', profiles=profiles) if profiles else []
        context['rows'] = [
            dict(profile, line=line, duplicate=duplicate)
            for (line, profile), duplicate in zip(rows, results)
        ]
        context['errors'] = errors
        ready = [profile for profile, duplicate in zip(profiles, results) if not duplicate]
        context['ready_count'] = len(ready)
        context['skipped_count'] = len(profiles) - len(ready)
        if ready and not errors:
            context['confirm'] = signing.dumps(
                {'actor': request.user.pk, 'profiles': ready, 'skipped': context['skipped_count']},
                salt=salt, compress=True,
            )
    except (ValueError, OSError, RuntimeError) as error:
        context['error'] = sanitize(str(error), 300)
    return render(request, 'web/server_csv_import.html', context)


@login_required
@require_GET
@never_cache
def server_csv_export(request):
    _admin(request)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="opm-servers.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(CSV_FIELDS)
    cursor = ''
    try:
        while page := _call('hosts.csv_export', after=cursor):
            for host in page:
                writer.writerow(csv_cell(host[field]) for field in CSV_FIELDS)
            cursor = page[-1]['id']
    except (ValueError, OSError, RuntimeError):
        return HttpResponse('Server inventory export is temporarily unavailable.', status=503)
    return response


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
def server_repair_ssh(request, host_id):
    """Explicit recovery only, never automatic password fallback during scans."""
    _admin(request)
    try:
        host = next(h for h in _call('hosts.list') if h['id'] == host_id)
    except StopIteration:
        raise Http404('Unknown server')
    if not host['verified'] or host['ssh_user'] != 'opm':
        raise PermissionDenied('Only verified OPM-managed SSH accounts support key recovery')
    form = SSHRepairForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        values = form.cleaned_data
        try:
            result = _call('hosts.ssh_repair.start', id=host_id,
                admin_user=values['admin_user'], login_password=values['login_password'],
                admin_sudo_password=values['admin_sudo_password'],
                opm_password=values['opm_password'], fingerprint=values['fingerprint'],
                trust_ack=values['trust_ack'],
                create_if_missing=values['create_if_missing'],
                new_opm_password=values['new_opm_password'])
            _actor_action(request, 'hosts.ssh_repair.start', host_id)
            messages.info(request, 'SSH recovery started. Review the result in History.')
            return redirect('activity_detail', activity_id=result['activity_id'])
        except (ValueError, RuntimeError, OSError) as exc:
            error = str(exc)
            for secret in (values['login_password'], values['admin_sudo_password'],
                           values['opm_password'], values['new_opm_password']):
                if secret:
                    error = error.replace(secret, '[REDACTED]')
            form.add_error(None, sanitize(error, 280))
    return render(request, 'web/repair_ssh.html', {
        'page':'servers', 'form':form, 'host':host})


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
    latest = next((item for item in _state() if item['id'] == host_id), existing)
    options = choices_for_host(latest)
    form = HostForm(request.POST or None, initial=existing, logo_options=options)
    if request.method == 'POST' and form.is_valid():
        try:
            # Save an independently checked visual preference through the
            # controller. No URL or shell string comes from the browser.
            choice = form.cleaned_data.get('logo_choice') or 'auto'
            _call('hosts.logo.set', id=host_id, choice=choice)
            profile = {k: v for k, v in form.cleaned_data.items() if k != 'logo_choice'}
            _call('hosts.update', id=host_id, data=profile)
            _actor_action(request, 'hosts.update', host_id)
            messages.success(request, 'Server profile updated')
            return redirect('server_detail', host_id=host_id)
        except (ValueError, OSError) as exc:
            form.add_error(None, sanitize(str(exc), 400))
    return render(request, 'web/host_form.html', {'page':'servers', 'form':form,
                'heading':'Edit server', 'logo_preview_host':existing})


@login_required
@require_POST
def verify_compose_configuration(request, host_id):
    """Admin-only read-only Docker Compose verification; no arbitrary paths.

    Form credentials are sent only to the memory-resident controller, never
    written into Django model fields, session records or audit details.
    """
    _admin(request)
    project = request.POST.get('project', '')
    password = request.POST.get('sudo_password', '')
    try:
        result = _call('hosts.compose_verify.start', id=host_id,
                       project=project, sudo_password=password,
                       credential_scope=_credential_scope(request))
        _actor_action(request, 'hosts.compose_verify.start', host_id)
        return redirect('activity_detail', activity_id=result['activity_id'])
    except (ValueError, RuntimeError, OSError) as exc:
        error = str(exc).replace(password, '[REDACTED]') if password else str(exc)
        messages.error(request, sanitize(error, 220))
        return redirect('server_detail', host_id=host_id)


@login_required
@require_POST
def update_compose_configuration(request, host_id):
    """Explicitly approved Docker Compose pull/recreate, never automatic."""
    _admin(request)
    project = request.POST.get('project', '')
    service = request.POST.get('service', '')
    password = request.POST.get('sudo_password', '')
    try:
        result = _call('hosts.compose_update.start', id=host_id, project=project,
            service=service, sudo_password=password,
            confirm_update=request.POST.get('confirm_update') == 'yes',
            protected_ack=request.POST.get('protected_ack') == 'yes',
            credential_scope=_credential_scope(request))
        _actor_action(request, 'hosts.compose_update.start', host_id)
        return redirect('activity_detail', activity_id=result['activity_id'])
    except (ValueError, RuntimeError, OSError) as exc:
        error = str(exc).replace(password, '[REDACTED]') if password else str(exc)
        messages.error(request, sanitize(error, 240))
        return redirect('server_detail', host_id=host_id)


@login_required
def server_detail(request, host_id):
    try:
        host = next(h for h in _state() if h['id'] == host_id)
    except StopIteration:
        raise Http404('Unknown server')
    except (ValueError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 260))
        return redirect('servers')
    # Repair SSH is a recovery action, not a permanent navigation shortcut.
    # Do not confuse ICMP failure or an arbitrary sudo error with a broken key.
    # Test SSH remains available to establish a diagnosis when necessary.
    repair_needed = False
    if request.user.is_superuser and host.get('verified') and host.get('ssh_user') == 'opm':
        try:
            recent = _call('activities.list')
        except (ValueError, OSError, RuntimeError):
            recent = []
        repair_needed = needs_ssh_repair(host, recent)
    projects = docker_compose_projects(host.get('engines'))
    if request.user.is_superuser and projects:
        try:
            verified = _call('hosts.compose_verifications', id=host_id)
        except (ValueError, RuntimeError, OSError):
            verified = []
        checks = {(item['project'], item['scope']): item for item in verified}
        for project in projects:
            record = checks.get((project['name'], project['scope']))
            if record:
                project['verification_detail'] = record.get('detail', '')
                project['verification_checked'] = record.get('checked', 0)
                # Timeboxed display is informational. A future update worker
                # must reverify before touching the deployment.
                project['verification_state'] = (
                    record['status'] if (time.time() - record.get('checked', 0)) < 3600
                    else 'expired')
            else:
                project['verification_state'] = 'not_checked'
            eligible = {}
            for service in project['services']:
                if (not service['vendor_managed'] and service['image_status'] != 'pinned'
                        and '@sha256:' not in service['image']):
                    eligible.setdefault(service['name'], service)
            project['update_services'] = list(eligible.values())
            project['can_update'] = (bool(eligible)
                and project['verification_state'] == 'verified'
                and host.get('sudo_mode') != 'read_only' and host.get('verified')
                and not project['vendor_managed'] and not project['oneoff']
                and not project['legacy_evidence'] and project['config_evidence']
                and project['scope'] in ('root', 'OPM SSH user'))
            project['can_verify'] = not (project['vendor_managed'] or project['oneoff']
                 or project['legacy_evidence'] or not project['config_evidence']
                 or project['scope'] not in ('root', 'OPM SSH user'))
    else:
        for project in projects:
            project['can_verify'] = False
            project['can_update'] = False
            project['update_services'] = []
            project['verification_state'] = 'not_checked'
    return render(request, 'web/host_detail.html', {
        'page': 'servers', 'host': _public_for(request, host),
        'ssh_repair_needed': repair_needed, 'compose_project_list': projects,
    })


@login_required
@login_required
@require_GET
@never_cache
def server_logo(request, host_id):
    """Serve local image bytes or a distro fallback, never a third-party URL.

    No browser-side hour-long caching: the chosen image can change when an
    administrator saves the server profile or after a new scan. The controller
    cache still avoids repeatedly downloading actual artwork.
    """
    try:
        host = _call('hosts.logo_info', id=host_id)
    except (OSError, ValueError, RuntimeError):
        raise Http404('Server unavailable')
    choice = None
    if request.user.is_superuser and 'choice' in request.GET:
        requested = str(request.GET.get('choice', ''))
        if requested in {opt['value'] for opt in choices_for_host(host)}:
            choice = requested
    requested_choice = choice if choice is not None else host.get('logo_choice') or 'auto'
    repos = selected_repositories(host, choice)
    for repository in repos:
        artwork = cached_logo(repository)
        if artwork:
            if request.GET.get('status') == '1' and request.user.is_superuser:
                return JsonResponse({'available': True, 'repository': repository,
                    'message': 'Application artwork available for ' + repository + '.'})
            image, kind = artwork
            response = HttpResponse(image, content_type=kind)
            response['X-Content-Type-Options'] = 'nosniff'
            return response
    if request.GET.get('status') == '1' and request.user.is_superuser:
        distro = distro_icon(host.get('os'))
        if requested_choice == 'distro':
            message = 'Linux distribution logo selected (' + distro + ').'
        elif repos:
            message = ('No downloadable application artwork is available for '
                       + ', '.join(repos[:3]) + '. Showing the ' + distro + ' logo instead.')
        else:
            message = 'No eligible container artwork found. Showing the ' + distro + ' logo.'
        return JsonResponse({'available': False, 'repository': '', 'message': message})
    response = redirect(static('web/logos/' + distro_icon(host.get('os')) + '.svg'))
    return response


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
    if action == 'remove' and request.POST.get('confirm_remove') != 'yes':
        messages.error(request, 'Acknowledge inventory removal before continuing')
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
        scan_finished = scan.get('finished') or 0
        needs_refresh = (scan.get('state') != 'completed' or
                         time.time() - scan_finished > 3600)
        can_refresh = bool(h.get('verified')) and h.get('sudo_mode') != 'read_only'
        h['dry_run_eligible'] = common or (needs_refresh and can_refresh)
        h['live_eligible'] = (common and h['updates'] > 0) or (needs_refresh and can_refresh)
    detected_engines = sum(bool(e.get('available')) for h in hosts for e in h['engines'].values())
    hosts = _public_for(request, hosts)
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
        if request.POST.get('action') == 'authorize':
            result = _call('credentials.authorize', sudo_passwords=passwords,
                           credential_scope=_credential_scope(request))
            _actor_action(request, 'credentials.authorize', host_id or 'verified servers')
            messages.success(request, f"Sudo authorized for {result['authorized']} server(s) for 10 minutes. No scan was performed.")
            return redirect('server_detail', host_id=host_id) if host_id else redirect('patch_manager')
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


# An in-progress Patch Manager review contains ONLY non-secret selection data.
# SSH and sudo passwords remain restricted to the controller's RAM and a single
# password-form POST. No password is stored in Django sessions or History.
_REVIEW_SESSION_KEY = 'pending_patch_review'
_REVIEW_CONTEXT_TTL = 7200


def _pending_review(request):
    state = request.session.get(_REVIEW_SESSION_KEY)
    if not isinstance(state, dict) or time.time() - state.get('created', 0) > _REVIEW_CONTEXT_TTL:
        request.session.pop(_REVIEW_SESSION_KEY, None)
        return None
    ids = state.get('host_ids')
    if (not isinstance(ids, list) or not 0 < len(ids) <= 100
            or any(not isinstance(hid, str) or len(hid) > 80 or not hid for hid in ids)
            or state.get('mode') not in ('apply', 'dry_run')):
        request.session.pop(_REVIEW_SESSION_KEY, None)
        return None
    return state


def _refresh_candidates(ids):
    """Return selected stale scans using the same one-hour rule as Store.

    Read-only and independent of displayed host data, so Viewer redaction
    cannot expose IP addresses in a hidden form or HTML attribute.
    """
    inventory = {h['id']: h for h in _call('hosts.list')}
    if any(hid not in inventory for hid in ids):
        raise ValueError('One of the selected servers was removed. Select servers again.')
    if any(not inventory[hid]['verified'] or inventory[hid]['sudo_mode'] == 'read_only' for hid in ids):
        raise ValueError('Selected server requires completed SSH setup and patch-capable sudo access')
    scans = {s['host_id']: s for s in _call('scans.list')}
    timestamp = time.time()
    stale = []
    for hid in ids:
        scan = scans.get(hid)
        if (not scan or scan.get('state') != 'completed'
                or not scan.get('finished') or timestamp - scan['finished'] > 3600):
            stale.append(inventory[hid])
    return stale


def _submit_pending_plan(request, state):
    """Create a fresh immutable plan only after the selected scans are valid."""
    prefs = get_validation_preferences()
    plan = _call('plans.create', host_ids=state['host_ids'], mode=state['mode'],
                 refresh_metadata=bool(state.get('refresh_metadata')),
                 verbose_dry_run=(state['mode'] == 'dry_run' and prefs['verbose_dry_run']))
    _actor_action(request, 'plans.create', plan['id'])
    request.session.pop(_REVIEW_SESSION_KEY, None)
    return redirect('plan_review', plan_id=plan['id'])


@login_required
@require_http_methods(['GET', 'POST'])
def review_refresh(request):
    """Inline authorization step when an OS review requires fresh scans.

    GET is strictly read-only; it cannot initiate SSH, APT, or plan creation.
    Only the operator-approved POST launches a limited read-only scan.
    """
    _operator(request)
    state = _pending_review(request)
    if not state:
        messages.warning(request, 'Your patch selection expired. Select the servers again.')
        return redirect('patch_manager')
    try:
        stale = _refresh_candidates(state['host_ids'])
        if request.method == 'GET':
            password_hosts = [h for h in stale if h['sudo_mode'] == 'password']
            cached = (_call('credentials.status', credential_scope=_credential_scope(request),
                            host_ids=[h['id'] for h in password_hosts])
                      if password_hosts else {})
            for host in password_hosts:
                host['sudo_cached_seconds'] = cached.get(host['id'], 0)
            return render(request, 'web/review_refresh.html', {
                'page': 'patch', 'hosts': stale, 'password_hosts': password_hosts,
                'selected_count': len(state['host_ids']), 'mode': state['mode'],
                'all_fresh': not stale,
            })
        # A cancelled attempt never launches a scan or creates a plan.
        if request.POST.get('action') == 'cancel':
            request.session.pop(_REVIEW_SESSION_KEY, None)
            return redirect('patch_manager')
        if request.POST.get('action') != 'refresh':
            raise ValueError('Unknown refresh action')
        if not stale:
            # No scan is necessary. Keep the normal POST-only review path.
            return _submit_pending_plan(request, state)
        passwords = {}
        shared = str(request.POST.get('shared_sudo_password', ''))
        for host in stale:
            if host['sudo_mode'] == 'password':
                password = str(request.POST.get('sudo_' + host['id'], '')) or shared
                if password:
                    if len(password) > 2048 or any(c in password for c in '\r\n\x00'):
                        raise ValueError('Invalid sudo credential')
                    passwords[host['id']] = password
        scan = _call('scans.start', ids=[h['id'] for h in stale],
                     sudo_passwords=passwords,
                     credential_scope=_credential_scope(request))
        state['activity_id'] = scan['activity_id']
        request.session[_REVIEW_SESSION_KEY] = state
        _actor_action(request, 'scans.start.review', f"{len(stale)} selected server(s)")
        return redirect('review_refresh_progress')
    except (ValueError, RuntimeError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 360))
        return redirect('review_refresh')


@login_required
@require_POST
def review_refresh_finish(request):
    """Final POST-only step after a completed, explicitly authorized scan."""
    _operator(request)
    state = _pending_review(request)
    if not state or not state.get('activity_id'):
        return redirect('patch_manager')
    try:
        activity = _call('activities.get', id=state['activity_id'])
        if activity.get('kind') != 'scan' or activity['state'] not in ('completed', 'partial'):
            raise ValueError('The requested scan has not finished successfully')
        if _refresh_candidates(state['host_ids']):
            raise ValueError('One or more selected scans need attention before patch review')
    except (ValueError, RuntimeError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 360))
        return redirect('review_refresh')
    try:
        return _submit_pending_plan(request, state)
    except (ValueError, RuntimeError, OSError) as exc:
        # A genuinely stale scan at this boundary should offer the refresh
        # again; other invalid plans must not cause an automatic POST loop.
        if 'run a fresh scan (within 1 hour)' in str(exc):
            return redirect('review_refresh')
        request.session.pop(_REVIEW_SESSION_KEY, None)
        messages.error(request, sanitize(str(exc), 360))
        return redirect('patch_manager')


@login_required
@require_GET
def review_refresh_progress(request):
    """Wait for the requested scan and continue directly to the review page.

    Never repeat a failed, partial, or interrupted scan automatically.
    A failed scan leaves the selection available for a deliberate retry.
    """
    _operator(request)
    state = _pending_review(request)
    if not state or not state.get('activity_id'):
        return redirect('patch_manager')
    try:
        activity = _call('activities.get', id=state['activity_id'])
        if activity.get('kind') != 'scan':
            raise ValueError('Unexpected scan activity')
        ready = (activity['state'] in ('completed', 'partial')
                 and not _refresh_candidates(state['host_ids']))
        return render(request, 'web/review_refresh_progress.html', {
            'page': 'patch', 'activity': activity, 'ready': ready,
            'waiting': activity['state'] in ('queued', 'running'),
            'refresh': activity['state'] in ('queued', 'running'),
        })
    except (ValueError, RuntimeError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 360))
        return redirect('review_refresh')


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
        if not ids:
            raise ValueError('Select at least one server')
        # Preserve only the selection/mode, never SSH or sudo credentials.
        request.session[_REVIEW_SESSION_KEY] = {
            'host_ids': ids, 'mode': mode,
            'refresh_metadata': mode == 'dry_run' and request.POST.get('refresh_metadata') == 'yes',
            'created': time.time(),
        }
        if _refresh_candidates(ids):
            return redirect('review_refresh')
        return _submit_pending_plan(request, request.session[_REVIEW_SESSION_KEY])
    except (ValueError, OSError, RuntimeError) as exc:
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
        'any_repository_warnings': plan['snapshot'].get('mode') == 'apply' and any(
            t.get('scan_metadata') == 'cached' for t in targets),
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
        _call('plans.approve', id=plan_id, protected_ack=request.POST.get('protected_ack')=='yes',
              repo_warning_ack=request.POST.get('repo_warning_ack')=='yes')
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
        items = _public_for(request, _call('activities.list'))
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
    return render(request, 'web/activity_detail.html', {'page':'history', 'item': _public_for(request, item)})


@login_required
def history(request):
    """Unified chronological operations and patch history.

    The activity timeline holds deliberately sanitized controller messages.
    Security audit actions are included for administrators only. No supplied
    passwords, remote raw stdout/stderr, or SSH key materials are rendered.
    """
    filtering = 'filter' in request.GET
    show_patches = not filtering or 'patches' in request.GET
    show_activities = not filtering or 'activities' in request.GET
    sort_order = 'oldest' if request.GET.get('sort') == 'oldest' else 'newest'
    items = []
    try:
        if show_patches:
            for job in _call('jobs.list'):
                items.append({**job, 'type':'patch',
                    'label':'OS dry run' if job.get('mode') == 'dry_run' else 'OS patch job',
                    'detail':job.get('error') or '', 'events':[]})
        if show_activities:
            for activity in _call('activities.history'):
                items.append({**activity, 'type':'activity'})
    except (ValueError, OSError, RuntimeError) as exc:
        messages.error(request, 'History unavailable: ' + sanitize(str(exc), 220))
    if show_activities and request.user.is_superuser:
        try:
            for audit in AuditEvent.objects.select_related('actor').order_by('-created')[:250]:
                items.append({'id':str(audit.pk), 'type':'audit', 'kind':'audit',
                    'label':audit.action.replace('.', ' ').replace('_',' ').title(),
                    'state':'recorded', 'created':audit.created.timestamp(),
                    'detail':audit.subject, 'actor':audit.actor.username if audit.actor else (audit.actor_name or 'Deleted account'),
                    'events':[]})
        except Exception:
            messages.warning(request, 'Web audit history could not be loaded')
    items.sort(key=lambda item: (item.get('created') or 0, item['type'], item['id']),
               reverse=sort_order=='newest')
    items = _public_for(request, items)
    return render(request, 'web/history.html', {'page':'history', 'items':items,
        'show_patches':show_patches, 'show_activities':show_activities, 'sort_order':sort_order})


@login_required
@require_POST
def clear_history(request):
    """Administrator-only, acknowledged removal of all operation history.

    Work is rejected while a job/activity is active. Inventory, scan data,
    accounts, and persisted source verification remain untouched. Do not
    re-add an AuditEvent after deletion: the user explicitly cleared audits.
    """
    if not request.user.is_superuser:
        raise PermissionDenied('Only administrators can clear all history')
    if request.POST.get('confirm_clear') != 'yes':
        messages.error(request, 'Acknowledge permanent deletion of all history')
        return redirect('history')
    try:
        result = _call('history.clear')
        summary = (f"Removed {result['jobs']} patch jobs, "
                   f"{result['activities']} activities and {result['audits']} audit records")
        if result.get('artifact_cleanup_errors'):
            messages.warning(request, summary + '; some old job files could not be removed')
        else:
            messages.success(request, summary)
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
        messages.success(request, f'Cleared {count} alert(s); history is preserved')
    except (ValueError, RuntimeError, OSError) as exc:
        messages.error(request, sanitize(str(exc), 300))
    # The header's Clear alerts button should keep operators on their page.
    from django.utils.http import url_has_allowed_host_and_scheme
    next_path = str(request.POST.get('next', ''))
    if next_path and next_path.startswith('/') and not next_path.startswith('//') and \
            url_has_allowed_host_and_scheme(next_path, allowed_hosts={request.get_host()},
                                            require_https=request.is_secure()):
        return redirect(next_path)
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
    return render(request, 'web/job_detail.html', {'page':'history','job':_public_for(request, job),
        'refresh':job['state'] in ('queued','running')})


@login_required
@require_POST
def cancel_job(request, job_id):
    _operator(request)
    if request.POST.get('confirm_cancel') != 'yes':
        messages.error(request, 'Acknowledge cancellation before continuing')
    else:
        _safe_op(request, 'jobs.cancel', 'job_detail', job_id, id=job_id)
    return redirect('job_detail', job_id=job_id)


@login_required
@require_http_methods(['GET', 'POST'])
def settings_view(request):
    _operator(request)  # Viewers cannot access Settings, even by direct URL.
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
        elif request.POST.get('action') == 'update_server_display':
            if not request.user.is_superuser:
                raise PermissionDenied('Only the administrator can change server display settings')
            grouped = request.POST.get('group_servers') == 'yes'
            update_server_display_preferences(grouped)
            _actor_action(request, 'settings.server_display',
                          'group headings enabled' if grouped else 'group headings disabled')
            messages.success(request, 'Server organization preferences saved')
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
        'server_display': get_server_display_preferences(),
        'web_access_mode': getattr(settings, 'OPM_WEB_ACCESS_MODE', 'legacy'),
        'web_access_hostname': (settings.ALLOWED_HOSTS[0] if settings.ALLOWED_HOSTS else 'Not configured'),
        'web_access_proxy': getattr(settings, 'OPM_WEB_TRUSTED_PROXY_IPS', ''),
        'web_access_form': web_access_form,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def create_account(request):
    """Administrator-only account management page, separate from Settings."""
    _admin(request)
    form = AccountForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        User = get_user_model()
        with transaction.atomic():
            role = form.cleaned_data['role']
            user = User.objects.create_user(username=form.cleaned_data['username'],
                password=form.cleaned_data['password'],
                is_staff=role != 'viewer', is_superuser=role == 'administrator')
            _actor_action(request, 'accounts.create', f'{user.username} ({role})')
        messages.success(request, 'Account created')
        return redirect('settings')
    return render(request, 'web/account_create.html', {'page':'settings', 'form':form})


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
def account_status(request, user_id):
    """Explicit, CSRF-protected enable/disable with last-admin protection."""
    _admin(request)
    action = request.POST.get('action')
    if action not in ('enable', 'disable'):
        return HttpResponseBadRequest('Unknown account status action')
    if action == 'disable' and request.POST.get('confirm_disable') != 'yes':
        return HttpResponseBadRequest('Confirm account disable')
    User = get_user_model()
    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=user_id).first()
        if user is None:
            raise Http404('Unknown account')
        if user.pk == request.user.pk:
            raise PermissionDenied('You cannot change your own account status')
        if action == 'disable' and user.is_superuser and user.is_active:
            if User.objects.filter(is_superuser=True, is_active=True).count() <= 1:
                raise PermissionDenied('Cannot disable the last active administrator')
        desired = action == 'enable'
        if user.is_active != desired:
            user.is_active = desired
            user.save(update_fields=['is_active'])
            _actor_action(request, 'accounts.enable' if desired else 'accounts.disable', user.username)
    messages.success(request, f'Account {user.username} {"enabled" if desired else "disabled"}')
    return redirect('edit_account', user_id=user_id)


@login_required
@require_POST
def delete_account(request, user_id):
    """Remove another login while preserving existing audit records and identity."""
    _admin(request)
    User = get_user_model()
    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=user_id).first()
        if user is None:
            raise Http404('Unknown account')
        if user.pk == request.user.pk:
            raise PermissionDenied('You cannot delete your own account')
        if request.POST.get('confirm_delete') != 'yes':
            return HttpResponseBadRequest('Confirm permanent account deletion')
        if user.is_superuser and user.is_active:
            if User.objects.filter(is_superuser=True, is_active=True).count() <= 1:
                raise PermissionDenied('Cannot delete the last active administrator')
        old_name = user.username
        # Older AuditEvent rows may predate the actor-name snapshot field.
        # Preserve attribution before Django nulls their actor FK on deletion.
        AuditEvent.objects.filter(actor=user).update(actor_name=old_name)
        user.delete()
        _actor_action(request, 'accounts.delete', old_name)
    messages.success(request, f'Account {old_name} deleted')
    return redirect('settings')
