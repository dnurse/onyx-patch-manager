# OPM v0.5.3: Internal Web UI Security

OPM is a high-privilege internal maintenance tool. It is designed to operate within a trusted homelab network or VPN and **must not be exposed on a public website**.

## Secure deployment option: private HTTPS reverse proxy

OPM can now listen on `0.0.0.0:8765` to accept requests from another LAN host running Nginx Proxy Manager. Choose **Internal HTTPS reverse proxy** in the first-install wizard or run `sudo opm-installer --web-access` on an existing installation.

This mode sets:

```text
OPM_WEB_ACCESS_MODE=proxy
OPM_WEB_BIND=0.0.0.0:8765
OPM_WEB_ALLOWED_HOSTS=<internal HTTPS hostname>,localhost,127.0.0.1
OPM_WEB_CSRF_TRUSTED_ORIGINS=https://<internal HTTPS hostname>
OPM_WEB_SECURE_COOKIES=1
OPM_WEB_TRUST_PROXY=1
OPM_WEB_TRUSTED_PROXY_IPS=<exact private NPM source IP>
OPM_WEB_ENABLE_HSTS=0
```

The listener is not directly encrypted; TLS is terminated by NPM. Keep the traffic between NPM and the LXC on a trusted private network, preferably with a firewall rule allowing port **8765** only from NPM. Django's middleware additionally rejects other source IPs when in proxy mode. It checks the socket peer IP (`REMOTE_ADDR`), not user-controlled forwarding headers, and accepts `X-Forwarded-Proto: https` only from the configured proxy source. NPM **must** strip and replace any client-provided `X-Forwarded-Proto` header and preserve the intended `Host` header.

Set an NPM Access List to allow LAN and VPN client ranges only. If your internet-facing pfSense forwards 80/443 to NPM, an HTTPS certificate and an internal-looking domain **do not** prevent external access. Test from cellular with the VPN disabled. Verify the actual LAN IP seen by the OPM LXC, especially if NPM runs inside Docker and NAT changes the source IP.

Never configure a wildcard public origin or `ALLOWED_HOSTS=*` to solve a bad-host error. Enter the exact hostname through the maintenance utility. Browser cookies use Secure and HttpOnly in private HTTPS mode. HSTS is deliberately disabled by default; enable it only after confirming HTTPS and trusted certificate behavior for all clients.

## Alternative modes

- **Localhost only:** binds to `127.0.0.1:8765`, appropriate for SSH tunnels or an on-host reverse proxy.
- **LAN/VPN direct HTTP:** binds to `0.0.0.0:8765` with an explicit allowed LAN hostname/IP, but traffic and login credentials are **not TLS-encrypted**. Do not use this over untrusted networks or expose TCP 8765 to the public internet. It is the default for simple private LAN/VPN installations.

## Web account roles (v0.5.5)

- **Administrator:** Can view and change global settings, manage accounts, enroll/edit/trust/bootstrap/test/remove registered servers, and scan/patch.
- **Operator:** Can view servers and history, scan managed hosts, perform Dry Runs and execute approved patch jobs. Cannot add/edit/remove servers, trust host keys, provision SSH accounts, or trigger managed SSH tests. Backend guards reject forged requests with HTTP 403.
- **Viewer:** May view dashboard, server details, patch plans and history without initiating scans or patches. Cannot change settings or manage inventory.

Accounts continue to use Django's `is_superuser`/`is_staff` flags for role enforcement; no separate database role schema or public registration is introduced. Administrator password resets are validated and recorded only as audit actions (never as plaintext secrets), and changes to a user's password invalidate their existing sessions under Django's session authentication hash checks. The currently logged-in administrator changes their own password through the current-password-protected page; direct self-demotion is blocked. The final active administrator is protected against demotion or deactivation.

## Application boundaries

- Django provides authentication, Argon2id hashing, CSRF protections, secure session cookies and clickjacking protections.
- There is no public self-registration. Administrative features require a superuser.
- SSH onboarding keys, host fingerprints and remote sudo credentials remain managed by the controller. Passwords are not saved to the browser or SQLite. Cached sudo credentials live in controller RAM for at most ten minutes per login session.
- The Ansible controller is still local-only via a private Unix domain socket. No public controller API is added by this change.
- The web and controller services share the unprivileged `opm` Linux account. A web compromise could therefore impact managed SSH credentials. This is one reason to enforce both NPM LAN/VPN ACLs and a firewall rule for the upstream listener.
- Backups contain sensitive inventory and hashed authentication data. Keep their file permissions restrictive.

## Updates and safeguards

- The installed root-owned web configurator is stored at `/usr/local/share/opm-installer/OPMWebAccess.py`; the maintenance utility never executes the copy in the service-owned application tree as root.
- Web configuration changes preserve the Django signing secret, accounts, managed keys and patch history. Previous web config is copied into a root-only backup under `/var/log/opm-installer/`.
- The web service restarts during network reconfiguration, but the controller and systemd patch jobs continue independently.
- Live reverse-proxy deployment requires validation. Local automated regression tests do not prove the NPM ACL or pfSense rules are correct.

## Web settings from browser

Only authenticated administrators can request a change from Settings. The controller starts a dedicated background operation, and the trusted helper is executed as the unprivileged OPM service account. CSRF protection is required. Switching access modes can disconnect the browser. A local health check cannot guarantee DNS, proxy ACL and certificate validity; verify external connectivity separately.
