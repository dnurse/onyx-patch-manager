#!/usr/bin/python3 -I
"""Verify a release signed for tester, then deploy it while OPM is idle."""

import base64
import fcntl
import os
from pathlib import Path, PurePosixPath
import pwd
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile


STATE = Path("/var/lib/opm-deploy")
SIGNERS = Path("/etc/opm-deploy/allowed-signers")
OPM_HOME = Path("/home/opm")
APP = OPM_HOME / "onyx_patch_manager"
DATABASE = OPM_HOME / ".local/share/opm/state.db"
INSTALLER = "/usr/local/sbin/opm-installer"
UNITS = ("opm-web.service", "opm-controller.service")
MAX_ZIP = 128 * 1024 * 1024
MAX_EXTRACTED = 512 * 1024 * 1024
NAMESPACE = "opm-release-tester"


def command(*args, **kwargs):
    return subprocess.run(args, check=True, stdin=subprocess.DEVNULL, **kwargs)


def read_release(header, directory):
    signature = directory / "release.sig"
    signature.write_bytes(base64.b64decode(header.strip(), validate=True))
    archive = directory / "release.zip"
    with archive.open("wb") as output:
        remaining = MAX_ZIP
        while block := sys.stdin.buffer.read(65536):
            remaining -= len(block)
            if remaining < 0:
                raise ValueError("Release ZIP exceeds the upload limit")
            output.write(block)
    with archive.open("rb") as payload:
        subprocess.run(
            ("/usr/bin/ssh-keygen", "-Y", "verify", "-f", str(SIGNERS),
             "-I", "opm-release", "-n", NAMESPACE, "-s", str(signature)),
            check=True, stdin=payload,
        )
    return archive


def extract(archive, destination):
    with zipfile.ZipFile(archive) as release:
        entries = release.infolist()
        if len(entries) > 10000 or sum(entry.file_size for entry in entries) > MAX_EXTRACTED:
            raise ValueError("Release exceeds the extraction limits")
        seen = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or not path.parts or path in seen
                    or kind not in (0, stat.S_IFDIR, stat.S_IFREG)):
                raise ValueError(f"Unsafe ZIP entry: {entry.filename!r}")
            seen.add(path)
            target = destination.joinpath(*path.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with release.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    installers = list(destination.rglob("OPMInstall.sh"))
    if len(installers) != 1:
        raise ValueError("Expected exactly one OPMInstall.sh")
    return installers[0].parent


def version(directory):
    with (directory / "pyproject.toml").open("rb") as source:
        value = tomllib.load(source)["project"]["version"]
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isascii() or not part.isdecimal() for part in parts):
        raise ValueError(f"Expected a three-part OPM release version, got {value!r}")
    return tuple(map(int, parts))


def release_build(directory):
    release_version = version(directory)
    try:
        fd = os.open(directory / "OPM_BUILD_REVISION", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return release_version, 0
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("OPM build revision must be a regular file")
        revision = os.read(fd, 11)
    finally:
        os.close(fd)
    revision = revision.removesuffix(b"\n")
    if not revision.isdigit() or len(revision) > 9:
        raise ValueError("Expected a numeric OPM build revision")
    return release_version, int(revision)


def ensure_idle(connection):
    for table in ("jobs", "activities"):
        entry = connection.execute(
            "SELECT type FROM sqlite_schema WHERE name = ?", (table,)
        ).fetchone()
        if table == "activities" and entry is None:
            continue
        if entry != ("table",):
            raise RuntimeError(f"Cannot verify job state: invalid {table} table")
        active = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE lower(state) IN ('queued', 'running')"
        ).fetchone()[0]
        if active:
            raise RuntimeError(f"Refusing deployment: {active} active entries in {table}")


def systemctl(*args):
    runtime = f"/run/user/{pwd.getpwnam('opm').pw_uid}"
    command(
        "/usr/sbin/runuser", "-u", "opm", "--", "/usr/bin/env",
        f"XDG_RUNTIME_DIR={runtime}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus",
        "/usr/bin/systemctl", "--user", *args,
    )


def check_services():
    for unit in UNITS:
        systemctl("is-active", unit)


def make_backup():
    directory = Path(tempfile.mkdtemp(prefix="backup-", dir=STATE / "backups"))
    try:
        command(
            "/usr/bin/tar", "--acls", "--xattrs", "--numeric-owner",
            "-C", "/", "-czf", str(directory / "opm.tar.gz"),
            "home/opm", "usr/local/sbin/opm-installer", "usr/local/share/opm-installer",
        )
        command("/usr/bin/tar", "-tzf", str(directory / "opm.tar.gz"), stdout=subprocess.DEVNULL)
    except BaseException:
        shutil.rmtree(directory)
        raise
    print(f"Verified backup: {directory / 'opm.tar.gz'}", flush=True)


def deploy(source):
    installed = release_build(APP)
    selected = release_build(source)
    if selected == installed:
        print("The selected OPM build is already installed.")
        check_services()
        return
    if selected < installed:
        raise RuntimeError("Refusing to downgrade the OPM version or build revision")
    check_services()
    with sqlite3.connect(DATABASE.as_uri() + "?mode=rw", uri=True, timeout=5) as connection:
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("BEGIN IMMEDIATE")
        ensure_idle(connection)
        try:
            systemctl("stop", *UNITS)
        except BaseException:
            connection.rollback()
            systemctl("start", *reversed(UNITS))
            raise
    try:
        make_backup()
    except BaseException:
        systemctl("start", *reversed(UNITS))
        raise
    command(INSTALLER, "--install", "--yes", "--source", str(source), cwd=source)
    if release_build(APP) != selected:
        raise RuntimeError("Installed OPM build did not match the release")
    check_services()
    print(f"Verified installed OPM build revision: {selected[1]}")


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise RuntimeError("Run as root with no command-line arguments")
    os.umask(0o077)
    os.chdir("/")
    os.environ.clear()
    os.environ.update(PATH="/usr/sbin:/usr/bin:/sbin:/bin", HOME="/root", LANG="C.UTF-8")
    with (STATE / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGALRM, upload_timeout)
        signal.alarm(120)
        header = sys.stdin.buffer.readline(8193)
        if not header:
            command(INSTALLER, "--status")
            return
        if len(header) > 8192 or not header.endswith(b"\n"):
            raise ValueError("Invalid release signature header")
        with tempfile.TemporaryDirectory(prefix="release-", dir=STATE) as staging:
            directory = Path(staging)
            archive = read_release(header, directory)
            signal.alarm(0)
            deploy(extract(archive, directory / "source"))


def upload_timeout(_signum, _frame):
    raise TimeoutError("Release upload timed out")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError,
            sqlite3.Error, zipfile.BadZipFile) as error:
        print(f"OPM deployment stopped: {error}", file=sys.stderr)
        sys.exit(1)
