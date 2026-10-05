"""Updates this Tico install to another image version, and undoes it when the server does not come back.

POST /update {"version": "1.2.3" | "v1.2.3" | "latest", "from": "1.2.2"}   start an update (409 while one runs);
                                                 "from" is the release the server runs, which status reports as "from"
GET  /status                                     {"state", "from", "to", "message"}
GET  /diagnostics                                versions, each container's state and restart count, and the last
                                                 failures: what a support bundle a person chooses to send holds
All need `Authorization: Bearer <token>`, the token the server wrote to /control/updater-token.

It talks to Docker through the mounted socket, which is root on the host: it listens on the compose
network only, and runs nothing but `docker compose` for the one service it manages.

Runner mode (TICO_UPDATER_MODE=runner) is the same updater beside a Docker runner (docker/runner.compose.yaml).
It manages the `runner` service and the tico-runner image, answers "healthy" from the container's own
health check, and writes its own token to /control (the runner reads it there, read-only) because no server
does. The runner asks for an update only when no turn is running, and only for the release its server names.

The compose bundle moves with the image. Before touching any container the updater downloads the target release's
tico-bundle-vX.Y.Z.tar.gz and SHA256SUMS (the URLs scripts/install.sh uses), checks the checksum, and replaces the
bundle's files in the install directory (compose.yaml, .env.example, docker/runner.compose.yaml, ...; a runner box
only runner.compose.yaml; never .env) one atomic rename at a time, keeping the old copies in .bundle-previous/.
A bad checksum or download refuses the update and changes nothing. If the new version does not turn healthy, the
image and the bundle are both put back. 
The server's database is snapshotted first (a consistent SQLite copy in /data/snapshots, the last few kept): a new
version may migrate the schema before it fails its health check, and an old image cannot read a newer schema. When
the update is rolled back the snapshot is restored too, and /status says so (`snapshot`, `restored`).

The updater then replaces itself: an updater that never moves would keep its own bugs on every install. After a
successful update it pulls its new image and starts a short-lived helper container from that image, which recreates
the `updater` service and checks that the new one stays up; if it does not, the helper puts the old updater back. An
install whose updater is pinned to a local-only tag (TICO_UPDATER_PULL=never, docker/smoke.sh) is left alone.
"""

import collections
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT = os.environ.get("TICO_PROJECT_DIR", "/project")
TOKEN_FILE = os.environ.get("TICO_UPDATER_TOKEN_FILE", "/control/updater-token")
MODE = "runner" if os.environ.get("TICO_UPDATER_MODE") == "runner" else "server"
SERVICE = "runner" if MODE == "runner" else "server"
IMAGE = os.environ.get("TICO_IMAGE", "ghcr.io/ticoteam/tico-runner" if MODE == "runner" else "ghcr.io/ticoteam/tico")
COMPOSE_FILE = os.environ.get("TICO_COMPOSE_FILE", "")   # relative to PROJECT; the runner box uses runner.compose.yaml
OVERRIDE_FILE = "runner.override.yaml"   # never part of the bundle, so an update never replaces it
HEALTH_URL = os.environ.get("TICO_HEALTH_URL", "http://server:8765/healthz")
HEALTH_SECONDS = int(os.environ.get("TICO_HEALTH_SECONDS", "180"))
PULL = os.environ.get("TICO_UPDATER_PULL", "always")   # "never" only in docker/smoke.sh, whose tags exist only locally
RELEASES = os.environ.get("TICO_RELEASES_URL", "https://github.com/ticoteam/tico/releases")
LATEST_API = os.environ.get("TICO_LATEST_URL", "https://api.github.com/repos/ticoteam/tico/releases/latest")
BUNDLE = os.environ.get("TICO_UPDATER_BUNDLE", "always")   # "never" only in docker/smoke.sh, whose releases exist only as local tags
SUPERVISOR_UID = SUPERVISOR_GID = 10002   # ticorun, the runner supervisor (docker/runner-entrypoint.sh)
SNAPSHOTS = "/data/snapshots"   # inside the server container: the data volume
KEEP_SNAPSHOTS = int(os.environ.get("TICO_UPDATER_KEEP_SNAPSHOTS", "3"))
SELF_UPDATE = os.environ.get("TICO_UPDATER_SELF", "always")   # "never" leaves this updater on its image
SWAP_HELPER = "tico-updater-swap"
STATUS_FILE = ".updater-status.json"   # the outcome survives the updater being replaced
PREVIOUS = ".bundle-previous"
NEVER_TOUCH = {".env"}
MAX_BUNDLE = 20 * 1024 * 1024
VERSION = re.compile(r"latest|v?[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?")

lock = threading.Lock()
status = {"state": "idle", "from": "", "to": "", "message": ""}
errors = collections.deque(maxlen=50)     # the last failures, in memory, for GET /diagnostics


def set_status(**fields):
    message = str(fields.get("message") or "")
    if any(word in message.lower() for word in ("no space left", "not enough space", "disk full", "enospc")):
        fields["message"] = "Not enough disk space to update. Free space on this computer (Docker: `docker image prune -a`); the update retries when space frees."
        try:
            fields["disk_free"] = shutil.disk_usage("/").free
        except OSError:
            pass
    elif fields.get("state") == "pulling":
        fields["disk_free"] = -1
    with lock:
        status.update(fields)
        if fields.get("state") in ("failed", "rolled_back") or (status["state"] == "failed" and fields.get("message")):
            errors.append("%s %s: %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), status["state"],
                                         str(status.get("message") or "")[:250]))


def diagnostics():
    """Facts about this install's containers and the tools that run them; nothing from inside a container."""
    def run(*args):
        try:
            done = subprocess.run(list(args), capture_output=True, text=True, timeout=15)
            return done.stdout.strip() if done.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    containers = []
    try:
        ids = compose("ps", "-a", "-q", timeout=30).split()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        ids = []
    for ident in ids[:50]:
        row = run("docker", "inspect", "--format",
                  '{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Status}}|'
                  '{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}', ident).split("|")
        if len(row) == 4:
            containers.append({"name": row[0], "state": row[1], "health": row[2],
                               "restarts": int(row[3]) if row[3].isdigit() else 0})
    own = run("docker", "inspect", "--format", "{{.Config.Image}}", os.environ.get("HOSTNAME", ""))
    with lock:
        recent = list(errors)
    return {"mode": MODE, "version": image_tag(own) if own else "",
            "docker": run("docker", "version", "--format", "{{.Server.Version}}"),
            "compose": run("docker", "compose", "version", "--short"), "containers": containers, "errors": recent}


def compose(*args, tag=None, timeout=600, extra_env=None):
    env = {**os.environ, **({"TICO_TAG": tag} if tag else {}), **(extra_env or {})}
    files = ["-f", os.path.join(PROJECT, COMPOSE_FILE)] if COMPOSE_FILE else []
    override = os.path.join(PROJECT, OVERRIDE_FILE)
    if COMPOSE_FILE and os.path.exists(override):   # the installer's own additions (--server-network); an update keeps them
        files += ["-f", override]
    result = subprocess.run(["docker", "compose", *files, "--project-directory", PROJECT, *args], env=env,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "docker compose failed")
    return result.stdout


def running_image():
    """(image id, tag) of the running service, so a failed update can put exactly that back."""
    container = compose("ps", "-q", SERVICE).split()[0]
    out = subprocess.run(["docker", "inspect", "--format", "{{.Image}} {{.Config.Image}}", container],
                         capture_output=True, text=True, check=True).stdout.split()
    return out[0], image_tag(out[1])


def release_name(value):
    """A release as the image tag spells it (v1.2.3), or "" when `value` is not a version. The server reports the
    release it is running; a `latest` or other tag says nothing about which release that is."""
    value = value.strip() if isinstance(value, str) else ""
    if not value or not VERSION.fullmatch(value) or value == "latest":
        return ""
    return "v" + value if value[0].isdigit() else value


def older_than_running(version, running=""):
    """True when `version` is a release older than the one now running (the server's own report, else the image tag).
    `latest` and anything unparsable pass."""
    def core(tag):
        found = re.match(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)", tag)
        return tuple(int(part) for part in found.groups()) if found else None
    try:
        running = core(running or running_image()[1])
    except (RuntimeError, OSError, IndexError, subprocess.SubprocessError):
        return False
    wanted = core(version)
    return bool(running and wanted and wanted < running)


def pinned_services(version):
    """Services that would not run the target image: an override that sets `image:` keeps them where they are.
    Renders the compose files with the target tag, the way the switch will."""
    rendered = json.loads(compose("config", "--format", "json", tag=version, timeout=60)).get("services", {})
    names = [SERVICE] + (["slack"] if MODE == "server" and "slack" in rendered else [])
    return [n for n in names if (rendered.get(n) or {}).get("image") != IMAGE + ":" + version]


class Mismatch(Exception):
    """The switch ran the wrong image or release: roll back at once."""


def check_switched(version, release, seconds=None):
    """After the switch: the service runs the image just pulled, and the server reports the release asked for.
    A build that reports no release (a local or edge image) is not held to one. A check that cannot be made
    (a slow first request, a quick restart) is tried again until the health window ends; only a real
    mismatch, or no answer in the whole window, rolls back."""
    deadline = time.time() + (HEALTH_SECONDS if seconds is None else seconds)
    while True:
        try:
            if running_image()[0] != inspect(IMAGE + ":" + version)["Id"]:
                raise Mismatch("the %s container is not running the %s image" % (SERVICE, version))
            if MODE == "server" and release:
                with urllib.request.urlopen(HEALTH_URL, timeout=5) as reply:
                    reported = release_name(json.loads(reply.read()).get("release", ""))
                if reported and reported != release:
                    raise Mismatch("the server reports %s, not %s" % (reported, release))
            return
        except Mismatch as exc:
            raise RuntimeError(str(exc))
        except (OSError, ValueError, KeyError, IndexError, AttributeError, subprocess.SubprocessError) as exc:
            if time.time() >= deadline:
                raise RuntimeError("could not check the new version (%s)" % exc)
        time.sleep(3)


def container_healthy(seconds):
    """The runner image has a HEALTHCHECK; the container must report `healthy`, not merely be running."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            container = compose("ps", "-q", SERVICE).split()
            state = subprocess.run(["docker", "inspect", "--format", "{{.State.Health.Status}}", container[0]],
                                   capture_output=True, text=True).stdout.strip() if container else ""
            if state == "healthy":
                return True
        except (RuntimeError, OSError, subprocess.SubprocessError):
            pass
        time.sleep(3)
    return False


def healthy(seconds):
    if MODE == "runner":
        return container_healthy(seconds)
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as reply:
                if reply.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(3)
    return False


def remember(tag):
    """Keep `docker compose up` from the host on the version this install now runs."""
    path = os.path.join(PROJECT, ".env")
    try:
        lines = [line for line in open(path).read().splitlines() if not line.startswith("TICO_TAG=")]
        with open(path, "w") as stream:  # in place: the file may be bind-mounted by inode
            stream.write("\n".join(lines + ["TICO_TAG=" + tag]) + "\n")
    except OSError:
        pass


class BundleError(RuntimeError):
    pass


def http_get(url, limit=MAX_BUNDLE):
    with urllib.request.urlopen(url, timeout=30) as reply:
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise BundleError("%s is larger than %d bytes" % (url, limit))
    return data


def release_of(version):
    """The release tag whose bundle goes with an image tag; `latest` is looked up on GitHub."""
    if version != "latest":
        return version
    try:
        tag = json.loads(http_get(LATEST_API, 1 << 20)).get("tag_name", "")
    except (OSError, ValueError) as exc:
        raise BundleError("could not look up the latest release: %s" % exc)
    if not isinstance(tag, str) or tag == "latest" or not VERSION.fullmatch(tag):
        raise BundleError("the latest release has no usable tag")
    return tag


def bundle_targets(names):
    """{path inside the bundle: path in the install directory}. A runner box wants runner.compose.yaml, nothing else."""
    if MODE == "runner":
        return {"docker/runner.compose.yaml": "runner.compose.yaml"} if "docker/runner.compose.yaml" in names else {}
    return {name: name for name in names}


def fetch_bundle(release, into):
    """Download and verify the release's bundle, unpack it under `into`, and return {bundle path: unpacked file}."""
    name = "tico-bundle-%s.tar.gz" % release
    base = "%s/download/%s" % (RELEASES.rstrip("/"), release)
    try:
        sums = http_get(base + "/SHA256SUMS", 1 << 20).decode("utf-8", "replace")
        blob = http_get(base + "/" + name)
    except OSError as exc:
        raise BundleError("could not download the %s bundle: %s" % (release, exc))
    want = ""
    for line in sums.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip("*") == name:
            want = fields[0].lower()
            break
    if not want:
        raise BundleError("SHA256SUMS does not list %s; refusing to use it" % name)
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise BundleError("checksum mismatch for %s (expected %s, got %s); nothing was changed" % (name, want, got))
    archive = os.path.join(into, "bundle.tar.gz")
    with open(archive, "wb") as stream:
        stream.write(blob)
    files = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            path = os.path.normpath(member.name)
            if path.startswith("/") or path == ".." or path.startswith("../") or not (member.isfile() or member.isdir()):
                raise BundleError("the bundle has unsafe paths; refusing to unpack it")
            if member.isdir():
                continue
            out = os.path.join(into, "files", path)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with tar.extractfile(member) as source, open(out, "wb") as target:
                shutil.copyfileobj(source, target)
            os.chmod(out, 0o755 if member.mode & 0o111 else 0o644)
            files[path] = out
    return files


def install_file(source, dest):
    """One atomic rename: a reader sees the old file or the new one, never half of it."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    temp = dest + ".tico-new"
    shutil.copyfile(source, temp)
    os.chmod(temp, os.stat(source).st_mode & 0o777)
    try:
        info = os.stat(dest)
        os.chown(temp, info.st_uid, info.st_gid)   # keep the installer's ownership when we are allowed to
    except OSError:
        pass
    os.replace(temp, dest)


def apply_bundle(files, release):
    """Replace the install directory's bundle files, keeping the old ones in .bundle-previous/ for rollback."""
    targets = {rel: dest for rel, dest in bundle_targets(files).items() if dest not in NEVER_TOUCH}
    keep = os.path.join(PROJECT, PREVIOUS)
    shutil.rmtree(keep, ignore_errors=True)
    os.makedirs(keep)
    manifest = {"release": release, "existed": [], "added": []}
    try:
        manifest["version_file"] = open(os.path.join(PROJECT, ".bundle-version")).read()
    except OSError:
        manifest["version_file"] = None
    for dest in targets.values():
        current = os.path.join(PROJECT, dest)
        if os.path.isfile(current):
            saved = os.path.join(keep, "files", dest)
            os.makedirs(os.path.dirname(saved), exist_ok=True)
            shutil.copy2(current, saved)
            manifest["existed"].append(dest)
        else:
            manifest["added"].append(dest)
    with open(os.path.join(keep, "manifest.json"), "w") as stream:
        json.dump(manifest, stream)
    try:
        for rel, dest in targets.items():
            install_file(files[rel], os.path.join(PROJECT, dest))
        marker = os.path.join(PROJECT, ".bundle-version")
        with open(marker + ".tico-new", "w") as stream:
            stream.write(release + "\n")
        os.replace(marker + ".tico-new", marker)
    except OSError:
        restore_bundle()
        raise


def restore_bundle():
    """Put the previous bundle back: the files it replaced, and gone again the ones it added. Never touches .env."""
    keep = os.path.join(PROJECT, PREVIOUS)
    try:
        manifest = json.load(open(os.path.join(keep, "manifest.json")))
    except (OSError, ValueError):
        return False
    for dest in manifest.get("existed", []):
        if dest not in NEVER_TOUCH:
            install_file(os.path.join(keep, "files", dest), os.path.join(PROJECT, dest))
    for dest in manifest.get("added", []):
        try:
            os.remove(os.path.join(PROJECT, dest))
        except OSError:
            pass
    version_path = os.path.join(PROJECT, ".bundle-version")
    try:
        if manifest.get("version_file") is None:
            os.remove(version_path)
        else:
            with open(version_path, "w") as stream:
                stream.write(manifest["version_file"])
    except OSError:
        pass
    return True


def other_services():
    """Services of the (new) compose file that follow the release, except the one being moved and this updater."""
    try:
        names = compose("config", "--services").split()
    except RuntimeError:
        return []
    return [n for n in names if n not in (SERVICE, "updater")]


SNAPSHOT_SCRIPT = """
import glob, os, sqlite3, sys
folder, name, keep = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.makedirs(folder, exist_ok=True)
part, final = os.path.join(folder, name + ".part"), os.path.join(folder, name)
source, target = sqlite3.connect("/data/hub.sqlite", timeout=30), sqlite3.connect(part)
source.backup(target)     # a consistent copy while the server keeps writing
ok = target.execute("PRAGMA integrity_check").fetchone()[0]
target.close(); source.close()
if ok != "ok":
    os.remove(part); sys.exit("the snapshot fails its integrity check")
os.replace(part, final)
for old in sorted(glob.glob(os.path.join(folder, "pre-update-*.sqlite")))[:-keep]:
    os.remove(old)
"""

RESTORE_SCRIPT = """
import os, shutil, sqlite3, sys, tempfile
from contextlib import closing
from pathlib import Path
snapshot, db = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "/data/hub.sqlite"
def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
def copy(source, target):
    with open(source, "rb") as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush(); os.fsync(dst.fileno())
with closing(readonly(snapshot)) as source:
    if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit("the snapshot fails its integrity check")
# The copy is finished on disk before the database is touched, so a crash leaves the migrated one, never none.
copy(snapshot, db + ".restoring")
folder = os.path.dirname(os.path.abspath(db))
# Writers (including Litestream) are stopped by restore_snapshot. Read WAL commits into a
# separate backup before deleting anything; never overwrite evidence from an earlier rollback.
if any(os.path.exists(db + suffix) for suffix in ("", "-wal", "-shm")):
    failed = tempfile.mkdtemp(prefix=os.path.basename(db) + ".failed-update-", dir=folder)
    recovered = os.path.join(failed, "recovery.sqlite")
    try:
        with closing(readonly(db)) as source, closing(sqlite3.connect(recovered)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("failed database is not readable")
        with open(recovered, "rb") as saved:
            os.fsync(saved.fileno())
    except sqlite3.Error:
        # An unreadable database still has forensic value. Keep its main file and sidecars
        # together under their original names; this is not a validated single-file backup.
        raw = os.path.join(failed, "raw")
        os.mkdir(raw)
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db + suffix):
                copy(db + suffix, os.path.join(raw, os.path.basename(db) + suffix))
        sync_dir(raw)
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(recovered + suffix):
                os.remove(recovered + suffix)
    sync_dir(failed)
    sync_dir(folder)
# Any preservation failure exits before sidecar removal, tracking removal or database replacement.
# Litestream's record of the file it followed is the migrated database's; left in place it would treat the
# restored file as a break and could upload it as the newest copy. Removed, it starts from what is on disk.
shutil.rmtree(os.path.join(os.path.dirname(db), "." + os.path.basename(db) + "-litestream"), ignore_errors=True)
for extra in ("-wal", "-shm"):
    if os.path.exists(db + extra):
        os.remove(db + extra)
os.replace(db + ".restoring", db)
sync_dir(folder)
"""


def take_snapshot(previous):
    """A consistent copy of the database, made by the running server's own container. Returns its file name."""
    name = "pre-update-%s-%s.sqlite" % (re.sub(r"[^0-9A-Za-z.-]", "", previous) or "unknown", time.strftime("%Y%m%d%H%M%S"))
    compose("exec", "-T", SERVICE, "python", "-c", SNAPSHOT_SCRIPT, SNAPSHOTS, name, str(KEEP_SNAPSHOTS))
    return name


def restore_snapshot(name, previous):
    """Server stopped (and Litestream with it, which runs inside the server container), the old image's own container
    puts the snapshot back in place of the migrated database and clears Litestream's tracking directory."""
    compose("stop", SERVICE)
    compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "python", SERVICE, "-c", RESTORE_SCRIPT,
            SNAPSHOTS + "/" + name, tag=previous)


def save_status():
    try:
        with lock:
            data = json.dumps(status)
        temp = os.path.join(PROJECT, STATUS_FILE + ".tmp")
        with open(temp, "w") as stream:
            stream.write(data)
        os.replace(temp, os.path.join(PROJECT, STATUS_FILE))
    except OSError:
        pass


def load_status():
    """After this updater replaced its predecessor, keep answering with the last update's outcome."""
    try:
        saved = json.load(open(os.path.join(PROJECT, STATUS_FILE)))
    except (OSError, ValueError):
        return
    if isinstance(saved, dict) and saved.get("state") in ("healthy", "rolled_back", "failed"):
        with lock:
            status.update({k: v for k, v in saved.items() if isinstance(v, (str, bool, int))})


def inspect(ref):
    result = subprocess.run(["docker", "inspect", ref], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("docker inspect %s failed" % ref)
    return json.loads(result.stdout)[0]


def image_tag(ref):
    return ref.rsplit(":", 1)[1] if ":" in ref.rsplit("/", 1)[-1] else "latest"


def replace_updater(tag):
    """After a good update: start a helper from the new updater image to recreate this container. Returns a note
    for the status line ("" when there is nothing to say)."""
    if SELF_UPDATE == "never" or PULL == "never":
        return ""
    try:
        me = inspect(os.environ.get("HOSTNAME", ""))
        ref = me["Config"]["Image"]
        host_dir = next(m["Source"] for m in me["Mounts"] if m["Destination"] == PROJECT)
        if image_tag(ref) == tag:
            return ""
        new_ref = ref.rsplit(":", 1)[0] + ":" + tag if ":" in ref.rsplit("/", 1)[-1] else ref + ":" + tag
        pulled = subprocess.run(["docker", "pull", new_ref], capture_output=True, text=True, timeout=600)
        if pulled.returncode:
            return "The updater itself stayed on %s: could not pull %s." % (image_tag(ref), new_ref)
        # A second runner on the same host has its own updater; its helper must not remove this one's.
        project = (me["Config"].get("Labels") or {}).get("com.docker.compose.project", "")
        helper = SWAP_HELPER if project in ("", "tico", "tico-runner") else "%s-%s" % (SWAP_HELPER, project)
        subprocess.run(["docker", "rm", "-f", helper], capture_output=True, timeout=60)
        env = {"TICO_UPDATER_MODE": MODE, "TICO_PROJECT_DIR": host_dir, "TICO_COMPOSE_FILE": COMPOSE_FILE,
               "TICO_SWAP_TAG": tag, "TICO_SWAP_OLD_ID": me["Image"], "TICO_SWAP_OLD_REF": ref,
               "TICO_SWAP_NAME": me["Name"].lstrip("/"), "DOCKER_CONFIG": "/tmp/.docker",
               "TICO_IMAGE": IMAGE, "TICO_UPDATER_PULL": "never", "TICO_UPDATER_BUNDLE": "never"}
        argv = ["docker", "run", "-d", "--name", helper, "--network", "none", "--read-only", "--tmpfs", "/tmp",
                "--security-opt", "no-new-privileges:true", "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE",
                "-v", "/var/run/docker.sock:/var/run/docker.sock", "-v", "%s:%s" % (host_dir, host_dir)]
        for key, value in env.items():
            argv += ["-e", "%s=%s" % (key, value)]
        subprocess.run([*argv, new_ref, "python", "/usr/local/bin/tico-updater", "replace-self"],
                       capture_output=True, text=True, timeout=120, check=True)
        return "The updater is replacing itself with %s." % tag
    except (StopIteration, KeyError, IndexError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return "The updater itself stayed on its current version (%s)." % (str(exc) or type(exc).__name__)


def updater_stays_up(old_id, seconds=12):
    """The recreated updater is running, on a different image, and still running a few seconds later."""
    container = compose("ps", "-q", "updater").split()
    if not container:
        return False
    deadline = time.time() + seconds
    while True:
        state = inspect(container[0])
        if not state["State"]["Running"] or state["State"].get("Restarting") or state["Image"] == old_id:
            return False
        if time.time() >= deadline:
            return True
        time.sleep(2)


def pin_updater_tag(tag):
    """The compose files read TICO_UPDATER_TAG from .env when the install pinned it; move the pin along."""
    path = os.path.join(PROJECT, ".env")
    try:
        lines = open(path).read().splitlines()
        if not any(line.startswith("TICO_UPDATER_TAG=") for line in lines):
            return
        lines = [("TICO_UPDATER_TAG=" + tag) if line.startswith("TICO_UPDATER_TAG=") else line for line in lines]
        with open(path, "w") as stream:
            stream.write("\n".join(lines) + "\n")
    except OSError:
        pass


def replace_self():
    """Runs in the helper container, from the NEW updater image. The old updater is recreated as this one's twin;
    if the new one does not stay up, the old image goes back. Exit status 0 only when an updater is running."""
    tag, old_id, old_ref = os.environ["TICO_SWAP_TAG"], os.environ["TICO_SWAP_OLD_ID"], os.environ["TICO_SWAP_OLD_REF"]
    # This helper runs with pulls and bundles off; the compose files read both from the environment, so without
    # this the recreated updater would inherit them and never pull an image or refresh the bundle again.
    for name in ("TICO_UPDATER_PULL", "TICO_UPDATER_BUNDLE"):
        os.environ.pop(name, None)
    up = ["up", "-d", "--no-deps", "--pull", "never", "updater"]
    try:
        compose(*up, extra_env={"TICO_UPDATER_TAG": tag})
        if updater_stays_up(old_id):
            pin_updater_tag(tag)
            print("tico-updater: replaced by %s" % tag, flush=True)
            return 0
        print("tico-updater: the new updater did not stay up", flush=True)
    except (RuntimeError, OSError, subprocess.SubprocessError, KeyError, ValueError) as exc:
        print("tico-updater: replacing the updater failed: %s" % exc, flush=True)
    subprocess.run(["docker", "tag", old_id, old_ref], check=False)
    try:
        compose(*up, extra_env={"TICO_UPDATER_TAG": image_tag(old_ref)})
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print("tico-updater: could not restart the old updater: %s" % exc, flush=True)
        return 2
    print("tico-updater: went back to %s" % old_ref, flush=True)
    return 1


def prune_images(keep):
    """After a healthy update, drop this service's and the updater's older release images (the new one and the one to
    roll back to stay). Without this every release stays on disk until the host fills and an update fails. An image a
    container still uses is refused by Docker and kept."""
    keep = {str(k) for k in keep if k} | {"latest"}
    for repo in (IMAGE, IMAGE.replace("tico-runner", "tico-updater") if MODE == "runner" else IMAGE + "-updater"):
        try:
            listed = subprocess.run(["docker", "image", "ls", repo, "--format", "{{.Tag}}"], capture_output=True, text=True,
                                    timeout=60).stdout.split()
        except (OSError, subprocess.SubprocessError):
            continue
        for tag in listed:
            if tag in keep or tag == "<none>" or not re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
                continue
            subprocess.run(["docker", "rmi", repo + ":" + tag], capture_output=True, check=False, timeout=120)


def update(version, running=""):
    """`running` is the release the server reported it was running when the update was asked for: what "from" says.
    The image tag only says what to put back if this fails, and can be `latest`."""
    staging = None
    bundled = False
    snapshot, switched = "", False
    try:
        image_id, previous = running_image()
        if not release_name(previous) and release_name(running):
            previous = release_name(running)   # an untagged or `latest` image: the server knows which release it is
        set_status(state="pulling", **{"from": release_name(running) or previous, "to": version}, message="", snapshot="", restored=False)
        target = release_name(version)
        # Everything that can refuse the update happens here, before any file or container changes.
        try:
            pinned = pinned_services(version)
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            set_status(state="failed", message="Not updated: could not read the compose files (%s)." % exc)
            return
        if pinned:
            set_status(state="failed", message="Not updated: %s pins the image for %s; remove `image:` so updates can change it."
                                               % ("compose.override.yaml" if MODE == "server" else OVERRIDE_FILE, ", ".join(pinned)))
            return
        if BUNDLE != "never":
            try:
                staging = tempfile.mkdtemp(prefix="tico-bundle-")
                release = release_of(version)
                files = fetch_bundle(release, staging)
            except BundleError as exc:
                set_status(state="failed", message="Not updated: %s." % exc)
                return
            target = release_name(release)
        if BUNDLE != "never":
            apply_bundle(files, release)
            bundled = True
        try:
            if PULL != "never":
                compose("pull", SERVICE, tag=version)
            if MODE == "server":
                # Last thing before the switch, so little is lost if it is undone: a new version may migrate the
                # schema and then fail its health check, and the old image cannot read a newer schema.
                try:
                    snapshot = take_snapshot(previous)
                except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    raise RuntimeError("could not snapshot the database first (%s)" % exc)
                set_status(snapshot=snapshot)
            set_status(state="restarting")
            switched = True
            compose("up", "-d", "--no-deps", "--pull", "never", SERVICE, tag=version)
            if not healthy(HEALTH_SECONDS):
                raise RuntimeError(("the runner" if MODE == "runner" else "the server") + " did not come up healthy within %d seconds" % HEALTH_SECONDS)
            check_switched(version, target)
        except RuntimeError as exc:
            # The old image is still on disk; point its tag back at it and start it again, with the old bundle.
            subprocess.run(["docker", "tag", image_id, IMAGE + ":" + previous], check=False)
            restored = ""
            try:
                if bundled:
                    restore_bundle()
                if switched and snapshot:
                    # The new version may have migrated the database; put back the copy taken just before.
                    try:
                        restore_snapshot(snapshot, previous)
                        restored = " Restored the database from the snapshot taken before the update (%s); changes made since then are not in it." % snapshot
                        set_status(restored=True)
                    except (RuntimeError, OSError, subprocess.SubprocessError) as snap_exc:
                        restored = " Could not restore the database snapshot %s (%s); it is in %s." % (snapshot, snap_exc, SNAPSHOTS)
                    print("tico-updater: rollback:" + restored, flush=True)
                compose("up", "-d", "--no-deps", "--pull", "never", SERVICE, tag=previous)
                back = healthy(HEALTH_SECONDS)
            except (RuntimeError, OSError):
                back = False
            set_status(state="rolled_back" if back else "failed",
                       message=str(exc) + (". Went back to " + previous + "." if back else ". The old version did not start either.") + restored)
            return
        remember(version)
        prune_images(keep={version, previous, release_name(version), release_name(previous)})
        message = ""
        if bundled and MODE == "server":
            # Slack, the front door and anything new follow the new compose file; the updater itself stays put.
            for name in other_services():
                try:
                    compose("up", "-d", "--no-deps", name, tag=version)
                except RuntimeError as exc:
                    message = "Updated, but %s did not restart: %s" % (name, exc)
        set_status(state="healthy", message=message)
        save_status()
        note = replace_updater(version)   # last: this container may be gone a moment after it starts the helper
        if note:
            set_status(message=(message + " " + note).strip())
    except Exception as exc:  # anything unexpected must show in /status rather than kill the thread
        set_status(state="failed", message=str(exc))
    finally:
        save_status()
        if staging:
            shutil.rmtree(staging, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "tico-updater"

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        try:
            expected = open(TOKEN_FILE).read().strip()
        except OSError:
            expected = ""
        given = self.headers.get("Authorization", "")
        return bool(expected) and hmac.compare_digest(given, "Bearer " + expected)

    def do_GET(self):
        if not self.authorized():
            return self.reply(401, {"error": "unauthorized"})
        if self.path == "/diagnostics":
            return self.reply(200, diagnostics())
        if self.path != "/status":
            return self.reply(404, {"error": "not found"})
        with lock:
            self.reply(200, dict(status))

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {"error": "unauthorized"})
        if self.path != "/update":
            return self.reply(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            asked = json.loads(self.rfile.read(min(length, 1024)) or b"{}")
            version, running = asked.get("version", ""), release_name(asked.get("from", ""))
        except (ValueError, AttributeError):
            version, running = "", ""
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            return self.reply(422, {"error": "version is latest or X.Y.Z"})
        if version[0].isdigit():
            version = "v" + version  # the server names a release 1.2.3; its image tag is v1.2.3
        if older_than_running(version, running):   # a bot could otherwise move the box to a release that predates the bot user
            return self.reply(409, {"error": "older than the running version"})
        with lock:
            if status["state"] in ("pulling", "restarting"):
                return self.reply(409, {"error": "an update is already running", **status})
            # A whole new status, in one step: a read before the thread gets going must not show the last update's
            # "from", snapshot or outcome next to this one's "to". update() settles "from" against the image.
            status.update(state="pulling", to=version, message="", snapshot="", restored=False)
            status["from"] = running or ""
        threading.Thread(target=update, args=(version,), kwargs={"running": running}, daemon=True).start()
        self.reply(202, dict(status))

    def log_message(self, *args):
        pass


def ensure_token():
    """Runner mode: nothing else writes the token, so the updater does, for the runner supervisor to read.
    Every start makes it the supervisor's alone (ticorun 10002, mode 0600): bots run as another uid and must not
    be able to read it. Needs CAP_CHOWN (docker/runner.compose.yaml)."""
    if MODE != "runner":
        return
    if not os.path.exists(TOKEN_FILE):
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_hex(32) + "\n")
    info = os.lstat(TOKEN_FILE)
    if info.st_uid == SUPERVISOR_UID and info.st_mode & 0o777 == 0o600:
        return   # already locked down; root without CAP_FOWNER could not chmod the supervisor's file anyway
    try:
        os.chmod(TOKEN_FILE, 0o600)   # before the owner changes, so the old 0644 is never left readable
        os.chown(TOKEN_FILE, SUPERVISOR_UID, SUPERVISOR_GID)
    except PermissionError as exc:   # never stop the updater over this: 0600 root still keeps bots out
        print("tico-updater: could not lock the token down (%s); leaving it as it is" % exc, flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["replace-self"]:
        sys.exit(replace_self())
    load_status()
    ensure_token()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
