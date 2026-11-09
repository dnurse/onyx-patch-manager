# Docker Compose rollout status

**v0.6.7 internal:** Optional, explicitly approved single-service image pulls and
Compose recreations are enabled only after source verification, with an
administrator checkbox and protected-host approval. Docker's running/health
state is checked afterwards. No automatic updates, rollback, builds, or Podman
updates. The user must evaluate application/database backup and rollback needs.

# Docker Compose Update Scope — OPM Road to GM

The sole container-update target is **Docker Compose V2** (`docker compose`).
Other container technologies remain inventory-only:

- Standalone Docker containers
- Podman, Podman Compose, Quadlet, rootless Podman
- Vendor-managed containers, such as UniFi OS Server
- One-off Compose containers or workloads with uncertain provenance

## v0.6.1 internal candidate — inspection only

- Group containers by Docker Compose project and service labels.
- Record **boolean presence** of working-directory and config-files labels, not path contents or environment variables.
- Display candidate projects and service/image status in Server detail and Patch Manager.
- Show vendor-controlled, one-off, and legacy snapshots as excluded or unverified.
- **Update operations are available in v0.6.7 only after manual approval, not automatically.**

## Next development stage — verified immutable plan

- On an explicitly authorized refresh, verify the remote host's actual Docker Compose V2 plugin, trusted Docker context, all Compose source files, working directory, and required environment inputs.
- Verify that project/service labels, effective Compose config, deployed container identity, image reference and digest agree. Files may be replaced or edited after the scan; reject any mismatch at execution time.
- Do not record `docker compose config` output, environment interpolation results, registry tokens, Docker credentials, or Compose secrets in the OPM database or Activity History.
- Classify `build:` services, `latest` or mutable references, private registry access, digests pinned by the operator, dependent services, volumes, and current health status. Do not assume a tag change means a safe application upgrade.
- Present an immutable operator review for the exact host/project/service/images, with protected-host acknowledgement. No automatic/bulk updates on first release.
- Never take a project over merely because a container has Compose labels.

## Execution stage — after live acceptance of planning

- Take an immutable approved plan and enforce fresh SSH/sudo, source-file, project and digest preflight immediately before pulling.
- Execute narrowly scoped Docker Compose operations for the approved service only; no `down`, `--remove-orphans`, automatic full-project recreate, or arbitrary shell commands.
- Pull the selected new image and recreate using the *original verified Compose deployment*. Do not rebuild images or renew anonymous volumes implicitly.
- Verify container replacement, expected image digest, `running` state and Docker HEALTHCHECK when configured. Distinguish missing health checks from healthy applications.
- Persist per-step actions and sanitized errors in History; handle partial success without blindly repeating a patch. Preserve the prior image reference to support manual/controlled rollback when feasible.
- Warn that image rollback alone does not roll back volume/database migrations. Never promise guaranteed recovery without an application data backup.

## Live acceptance cases

1. Two-service Compose project, update one service without recreating the other.
2. No-update image tag and digest-pinned image handled without changes.
3. Vendor-managed container sharing a Docker host remains excluded.
4. Missing Compose files, changed project labels, changed configuration, changed SSH identity => fail closed.
5. Root-only Docker access, missing Compose plugin, registry outage, and invalid credentials produce clear diagnostics.
6. An image pull that fails must not stop the existing container.
7. A recreated container that fails to become healthy is recorded as failed, with old image reference available for review.
8. Project with bind mounts, named volumes, anonymous volumes, profiles and `depends_on` is reviewed accurately.
9. Protected-host approval, cross-user session separation and operator/viewer permissions are enforced server-side.
10. No container operations on Podman, standalone Docker, or vendor-managed workloads.

Internal patch builds use the `0.6.x` version line; publish the next validated GitHub milestone as `v0.7.0` per `VERSIONING.md`.

## v0.6.6 — Read-only source verification (implemented)

Under each server's **Docker Compose projects** card, an administrator can select **Verify Configuration**. OPM uses the current Docker inventory only to identify a candidate; it independently reads the running deployment labels from the target and validates the original local Compose configuration and service hashes before recording a short-lived verification status. Only Docker Compose projects in the rootful Docker context are eligible. A failure remains unverified and provides a sanitized History diagnostic.

Source verification itself remains read-only. Separately, a manual update request can pull an image and recreate one explicitly approved service, after source re-verification. Image status discovery is independent and may be unknown if Skopeo is unavailable.
