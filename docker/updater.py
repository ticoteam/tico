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
import signal
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
POLL_SECONDS = float(os.environ.get("TICO_UPDATER_POLL", "1"))   # how often a health or switch check is repeated
PULL = os.environ.get("TICO_UPDATER_PULL", "always")   # "never" only in docker/smoke.sh, whose tags exist only locally
RELEASES = os.environ.get("TICO_RELEASES_URL", "https://github.com/ticoteam/tico/releases")
LATEST_API = os.environ.get("TICO_LATEST_URL", "https://api.github.com/repos/ticoteam/tico/releases/latest")
BUNDLE = os.environ.get("TICO_UPDATER_BUNDLE", "always")   # "never" only in docker/smoke.sh, whose releases exist only as local tags
SUPERVISOR_UID = SUPERVISOR_GID = 10002   # ticorun, the runner supervisor (docker/runner-entrypoint.sh)
SNAPSHOTS = "/data/snapshots"   # inside the server container: the data volume
KEEP_SNAPSHOTS = int(os.environ.get("TICO_UPDATER_KEEP_SNAPSHOTS", "3"))
SNAPSHOT_SECONDS = int(os.environ.get("TICO_UPDATER_SNAPSHOT_SECONDS", "900"))
SELF_UPDATE = os.environ.get("TICO_UPDATER_SELF", "always")   # "never" leaves this updater on its image
# The runner's default memory cap is the installer's rule (docker/runner-memory.sh, copied into this image). Inside a
# container /proc/meminfo is the host's total unless LXCFS is mounted, so the default is the host's, as the installer's is.
MEMINFO = os.environ.get("TICO_UPDATER_MEMINFO", "/proc/meminfo")
RUNNER_MEMORY_RULE = os.environ.get("TICO_RUNNER_MEMORY_RULE", "/usr/local/bin/tico-runner-memory")
SWAP_HELPER = "tico-updater-swap"
STATUS_FILE = ".updater-status.json"   # the outcome survives the updater being replaced
PREVIOUS = ".bundle-previous"
NEVER_TOUCH = {".env"}
MAX_BUNDLE = 20 * 1024 * 1024
# A docker inspect, ps or tag answers in seconds; one a stalled daemon holds must not keep an update running.
INSPECT_SECONDS = 60
# compose verbs that change containers: one that does not report back may still be carried out by Docker (Settle).
MUTATING = {"up", "stop", "run", "rm", "down", "restart", "kill", "start", "create"}
SETTLE_RETRY_SECONDS = 60
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


def docker_run(argv, timeout, env=None, check=False, capture_output=True, text=True):
    """subprocess.run for a docker command, in its own process group. `docker compose` runs as a plugin child of the
    docker CLI, so killing only the CLI on a timeout would leave compose switching or rolling back later: the whole
    group is killed and reaped before TimeoutExpired is raised."""
    with subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          start_new_session=True) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise
    result = subprocess.CompletedProcess(argv, proc.returncode, out, err)
    if check:
        result.check_returncode()
    return result


class Unsettled(RuntimeError):
    """A command that changes containers did not report back. Killing the client does not cancel what the daemon
    already accepted (an accepted stop runs to its end), so what that command does to the containers is unknown."""


def runner_memory_default():
    """The default TICO_RUNNER_MEMORY for this host ("27g"), or "" when the host gets no cap."""
    try:
        result = subprocess.run(["sh", RUNNER_MEMORY_RULE, MEMINFO], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def env_file_sets(name):
    """Whether the install's .env sets the key in any form compose reads (`NAME=v`, `NAME = v`, `NAME: v`, `export NAME=v`):
    its value wins over the default, and an empty value counts. A commented line does not."""
    pattern = re.compile(r"[ \t]*(?:export[ \t]+)?%s[ \t]*[=:]" % re.escape(name))
    try:
        with open(os.path.join(PROJECT, ".env")) as f:
            return any(pattern.match(line) for line in f)
    except OSError:
        return False


def cap_running_runner():
    """A runner recreated by an older updater, which knows nothing of the default, has no memory limit. Give it the default
    live: `docker update` sets the limit of the running container, and the container keeps it on its own restarts. Only a
    runner with no limit is changed, so a limit someone set stays. Failures are logged and ignored."""
    if MODE != "runner" or env_file_sets("TICO_RUNNER_MEMORY"):
        return
    cap = runner_memory_default()
    if not cap:
        return
    try:
        ids = compose("ps", "-q", SERVICE, timeout=INSPECT_SECONDS).split()
        if not ids:
            return
        limit = docker_run(["docker", "inspect", "--format", "{{.HostConfig.Memory}}", ids[0]], capture_output=True,
                           text=True, check=True, timeout=INSPECT_SECONDS).stdout.strip()
        if limit != "0":
            return
        docker_run(["docker", "update", "--memory", cap, "--memory-swap", cap, ids[0]], capture_output=True, text=True,
                   check=True, timeout=INSPECT_SECONDS)
        print("tico-updater: capped the runner's memory at %s, the default for this computer" % cap, flush=True)
    except Exception as exc:
        print("tico-updater: could not cap the runner's memory (%s); leaving it as it is" % exc, flush=True)


def compose(*args, tag=None, timeout=600, extra_env=None):
    env = {**os.environ, **({"TICO_TAG": tag} if tag else {}), **(extra_env or {})}
    # The environment of compose outranks .env, so the default is set only when .env leaves the cap unset.
    if MODE == "runner" and "TICO_RUNNER_MEMORY" not in env and not env_file_sets("TICO_RUNNER_MEMORY"):
        cap = runner_memory_default()
        if cap:
            env["TICO_RUNNER_MEMORY"] = cap
    files = ["-f", os.path.join(PROJECT, COMPOSE_FILE)] if COMPOSE_FILE else []
    override = os.path.join(PROJECT, OVERRIDE_FILE)
    if COMPOSE_FILE and os.path.exists(override):   # the installer's own additions (--server-network); an update keeps them
        files += ["-f", override]
    try:
        result = docker_run(["docker", "compose", *files, "--project-directory", PROJECT, *args], env=env,
                                capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:   # docker_run killed and reaped its whole group; the daemon may not have stopped
        message = "docker compose %s did not finish within %d seconds" % (args[0], timeout)
        raise Unsettled(message) if args[0] in MUTATING else RuntimeError(message)
    if result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError((lines[-1] if lines else "docker compose failed") + overlap_hint(result.stderr or result.stdout))
    return result.stdout


EDGE_HINT = (" The front doors' edge network (172.30.254.0/28 unless .env moves it) clashes with a network on this "
             "computer: set TICO_EDGE_SUBNET, TICO_EDGE_DYNAMIC_RANGE, TICO_CADDY_ADDRESS and TICO_CLOUDFLARED_ADDRESS "
             "in .env to a free range, then update again. To move an edge network that already exists, run docker compose "
             "down, then docker compose up -d, then update again. See docs/install.md.")


def overlap_hint(output):
    """Docker's own words for a subnet clash name no setting; say which ones move it."""
    return EDGE_HINT if "Pool overlaps" in (output or "") else ""


def running_image():
    """(image id, tag) of the running service, so a failed update can put exactly that back."""
    container = compose("ps", "-q", SERVICE, timeout=INSPECT_SECONDS).split()[0]
    out = docker_run(["docker", "inspect", "--format", "{{.Image}} {{.Config.Image}}", container],
                         capture_output=True, text=True, check=True, timeout=INSPECT_SECONDS).stdout.split()
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
        time.sleep(POLL_SECONDS)


def container_healthy(seconds):
    """The runner image has a HEALTHCHECK; the container must report `healthy`, not merely be running."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            container = compose("ps", "-q", SERVICE, timeout=INSPECT_SECONDS).split()
            state = docker_run(["docker", "inspect", "--format", "{{.State.Health.Status}}", container[0]],
                                   capture_output=True, text=True, timeout=INSPECT_SECONDS).stdout.strip() if container else ""
            if state == "healthy":
                return True
            if state == "unhealthy":   # past its start period and failing: waiting out the window changes nothing
                return False
        except (RuntimeError, OSError, subprocess.SubprocessError):
            pass
        time.sleep(POLL_SECONDS)
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
        time.sleep(POLL_SECONDS)
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


def selected_caddy():
    """Only reconcile an existing install's front door; naming a profiled service in `up` would enable it."""
    if MODE != "server" or "caddy" not in compose("config", "--services").split():
        return False
    return any(not oneoff for oneoff, _ in containers_of_service("caddy").values())


def reconcile_caddy(tag):
    """The healthy server has prepared its config. Replace the front door from this exact bundle, even when only
    volume contents changed. A restored pre-feature bundle runs its old direct proxy, not the new supervisor or
    the stale asset config an old server cannot clear. New supervisors must report their applied generation healthy."""
    before = set(containers_of_service("caddy"))
    set_status(front_door="restarting")
    compose("up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "--wait",
            "--wait-timeout", str(HEALTH_SECONDS), "caddy", tag=tag, timeout=HEALTH_SECONDS + INSPECT_SECONDS)
    now = {cid: state for cid, (oneoff, state) in containers_of_service("caddy").items() if not oneoff}
    if len(now) != 1 or next(iter(now)) in before or next(iter(now.values())) != "running":
        raise Unsettled("Caddy recreate did not confirm one fresh running container")
    state = inspect(next(iter(now)))
    if not state.get("State", {}).get("Running") or state["State"].get("Restarting"):
        raise RuntimeError("Caddy stopped after its recreate")
    health = state.get("State", {}).get("Health", {}).get("Status")
    if health == "healthy":
        set_status(front_door="healthy")
    elif health is None and (state.get("Config", {}).get("Cmd") or [])[:2] == ["caddy", "reverse-proxy"]:
        # Old direct proxies have no healthcheck or local asset listener. Running is not asset readiness.
        set_status(front_door="legacy_running")
    else:
        raise RuntimeError("Caddy did not report its applied configuration healthy")


def tunnel_services():
    """The tunnel front door's services in the current compose files: cloudflared, and the hold it routes through when
    that compose file has one. Empty without the cloudflared profile, and on a runner."""
    if MODE != "server":
        return []
    names = compose("config", "--services").split()
    return [n for n in ("hold", "cloudflared") if n in names] if "cloudflared" in names else []


def selected_tunnel():
    """Like selected_caddy: only an install that already runs cloudflared has its tunnel reconciled."""
    return any(not oneoff for oneoff, _ in containers_of_service("cloudflared").values())


def hold_ready(seconds):
    """One running hold that answers: a request to its own port reaches the healthy server's /healthz through it."""
    deadline = time.time() + seconds
    while True:
        try:
            ids = [cid for cid, (oneoff, _) in containers_of_service("hold").items() if not oneoff]
            state = inspect(ids[0]).get("State", {}) if len(ids) == 1 else {}
            if state.get("Running") and not state.get("Restarting"):
                compose("exec", "-T", "hold", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz",
                        timeout=INSPECT_SECONDS)
                return True
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
            pass
        if time.time() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def reconcile_tunnel(tag, settling=False):
    """cloudflared reads its route only when it starts, and the route a compose file with a hold names (ingress.yml)
    sends every request through the hold. So the hold is started and must answer before cloudflared is recreated; if it
    does not, cloudflared is left as it is (on an install from before the hold, its direct route to the server) and the
    update fails, so the rollback puts the old bundle back. `up --no-deps` skips compose.yaml's depends_on, and a
    cloudflared started onto a missing hold would leave the install unreachable from outside. Settling replaces each
    container: a cut recreate may have left a stop pending against one that `up` would otherwise keep."""
    services = tunnel_services()
    if "hold" in services:
        start_tunnel_service("hold", tag, settling)
        if not hold_ready(HEALTH_SECONDS):
            raise RuntimeError("the tunnel's hold did not come up within %d seconds, so cloudflared was left on its "
                               "old route" % HEALTH_SECONDS)
    if "cloudflared" in services:
        start_tunnel_service("cloudflared", tag, settling)


def start_tunnel_service(name, tag, settling):
    if not settling:
        compose("up", "-d", "--no-deps", name, tag=tag)
        return
    before = set(containers_of_service(name))
    compose("up", "-d", "--no-deps", "--force-recreate", "--pull", "never", name, tag=tag)
    now = {cid: state for cid, (oneoff, state) in containers_of_service(name).items() if not oneoff}
    if len(now) != 1 or next(iter(now)) in before or next(iter(now.values())) != "running":
        raise Unsettled("%s recreate did not confirm one fresh running container" % name)


SNAPSHOT_SCRIPT = """
import glob, os, sqlite3, sys, threading, time
folder, name, keep, seconds = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
deadline = time.monotonic() + seconds   # the whole attempt, copy and check, ends inside the updater's own timeout
os.makedirs(folder, exist_ok=True)
def remove_all(pattern):
    # SQLite deletes the copy's -journal at the end of each write transaction, so a file listed here can be gone by the
    # time it is removed while a worker is still copying.
    for path in glob.glob(pattern):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
remove_all(os.path.join(folder, "pre-update-*.sqlite.part*"))   # an earlier attempt that ran out of time; each is as large as the database
part, final = os.path.join(folder, name + ".part"), os.path.join(folder, name)
def remove_part():
    remove_all(part + "*")
result = []
def copy_and_check():
    try:
        source, target = sqlite3.connect("/data/hub.sqlite", timeout=30), sqlite3.connect(part)
        source.backup(target)     # a consistent copy while the server keeps writing
        # quick_check reads every page and b-tree but skips integrity_check's index-against-table cross-check, which on
        # a multi-gigabyte database is disk-bound for longer than an update can wait.
        result.append(target.execute("PRAGMA quick_check").fetchone()[0])
        target.close(); source.close()
    except sqlite3.Error as exc:
        result.append("the snapshot copy failed: %s" % exc)
# Neither backup() nor a check that has not started yet can be interrupted, so the copy and check run on a worker
# and this thread keeps the one deadline: past it, the process ends, worker included, inside the updater's own
# timeout. The worker may still create the partial copy after it is removed here; the next attempt sweeps it above.
worker = threading.Thread(target=copy_and_check, daemon=True)
worker.start()
worker.join(max(0.0, deadline - time.monotonic()))
if worker.is_alive():
    remove_part()
    print("the snapshot did not finish within %d seconds" % seconds, file=sys.stderr, flush=True)
    os._exit(1)
if result != ["ok"]:
    remove_part()
    sys.exit(result[0] if result and result[0].startswith("the snapshot") else "the snapshot fails its integrity check")
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
    compose("exec", "-T", SERVICE, "python", "-c", SNAPSHOT_SCRIPT, SNAPSHOTS, name, str(KEEP_SNAPSHOTS),
            str(max(SNAPSHOT_SECONDS - 60, SNAPSHOT_SECONDS * 4 // 5)), timeout=SNAPSHOT_SECONDS)
    return name


def restore_snapshot(name, previous):
    """Server stopped (and Litestream with it, which runs inside the server container), the old image's own container
    puts the snapshot back in place of the migrated database and clears Litestream's tracking directory."""
    compose("stop", SERVICE)
    try:
        compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "python", SERVICE, "-c", RESTORE_SCRIPT,
                SNAPSHOTS + "/" + name, tag=previous)
    except Unsettled as exc:   # the one-off may still be putting the snapshot back
        exc.restoring = True
        raise


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
    if isinstance(saved, dict) and saved.get("state") in ("healthy", "rolled_back", "failed", "uncertain", "restarting"):
        with lock:
            status.update({k: v for k, v in saved.items() if isinstance(v, (str, bool, int))})
        if saved.get("state") in ("uncertain", "restarting") and saved.get("settle_tag"):   # settles what the last left
            threading.Thread(target=settle, args=(saved["settle_tag"], str(saved.get("settle_outcome") or "")),
                             daemon=True).start()


def containers_of_service(service=SERVICE):
    """{id: (one-off, state)} for every container of the service. `compose ps -a` lists `compose run` one-offs too
    (the snapshot restore is one); their oneoff label tells them apart."""
    ids = compose("ps", "-a", "-q", service, timeout=INSPECT_SECONDS).split()
    if not ids:
        return {}
    result = docker_run(["docker", "inspect", "--format",
                         '{{.Id}} {{.State.Status}} {{index .Config.Labels "com.docker.compose.oneoff"}}', *ids],
                        capture_output=True, text=True, timeout=INSPECT_SECONDS)
    if result.returncode:   # one went away between ps and inspect (a --rm one-off ending): ask again later
        raise RuntimeError("could not read the %s containers" % service)
    found = {}
    for line in result.stdout.splitlines():
        cid, state, oneoff = (line.split(" ", 2) + [""])[:3]
        found[cid] = (oneoff.strip() == "True", state)
    return found


def one_offs_done(outcome):
    """True when no one-off of the service can still act. A killed `compose run` (the snapshot restore) leaves its
    container to the daemon: running, it may still replace the database, so the server must not be started under it.
    One that was created but never started is removed without force, which Docker refuses if it has started since.
    An exited one can do nothing more and is left alone."""
    waiting = []
    for cid, (oneoff, state) in containers_of_service().items():
        if not oneoff or state in ("exited", "dead"):
            continue
        if state == "created" and not docker_run(["docker", "rm", cid], capture_output=True, text=True,
                                                 timeout=INSPECT_SECONDS).returncode:
            continue
        waiting.append(state)
    if waiting:
        set_status(message="Not settled: %s. Holding new work until the one-off %s container (%s) ends: it may "
                           "still be restoring the database snapshot." % (
                               outcome, SERVICE, ", ".join(waiting)))
    return not waiting


def settle(tag, outcome):
    """After a command that changes containers did not report back, the outcome is unknown, and the runner keeps
    holding work while this says `uncertain`. Settled means: no one-off of the service still running (a killed
    snapshot restore), then a recreate of the service that did report back, and exactly one container for it, made
    by that recreate. An earlier request can act only on the containers it was given, and that recreate removed
    them. Until then this keeps trying, saying why."""
    caddy = status.get("settle_caddy") if MODE == "server" else False
    tunnel = status.get("settle_tunnel") if MODE == "server" else False
    set_status(state="uncertain", settle_tag=tag, settle_outcome=outcome,
               message="Not settled: %s. Holding new work until Docker confirms the %s on %s." % (outcome, SERVICE, tag))
    save_status()
    while True:
        try:
            if caddy is None:   # an older updater's saved outcome has no front-door selection
                caddy = selected_caddy()
                set_status(settle_caddy=caddy)
            if tunnel is None:
                tunnel = bool(tunnel_services()) and selected_tunnel()
                set_status(settle_tunnel=tunnel)
            if one_offs_done(outcome):
                before = {cid for cid, (oneoff, _) in containers_of_service().items() if not oneoff}
                compose("up", "-d", "--no-deps", "--force-recreate", "--pull", "never", SERVICE, tag=tag)
                now = [cid for cid, (oneoff, _) in containers_of_service().items() if not oneoff]
                if len(now) == 1 and now[0] not in before:
                    back = healthy(HEALTH_SECONDS)
                    if caddy:
                        if not back:
                            raise RuntimeError("the restored server is not healthy; Caddy settlement remains pending")
                        reconcile_caddy(tag)
                    if tunnel:
                        if not back:
                            raise RuntimeError("the restored server is not healthy; tunnel settlement remains pending")
                        reconcile_tunnel(tag, settling=True)   # a cut cloudflared recreate may have left it stopped or stopping
                    set_status(state="rolled_back" if back else "failed", settle_tag="", settle_outcome="", settle_caddy=False, settle_tunnel=False,
                               message="%s. Docker then confirmed the %s on %s%s." % (
                                   outcome, SERVICE, tag, "" if back else ", but it did not come up healthy"))
                    save_status()
                    return
                set_status(message="Not settled: %s. Holding new work: %d %s containers after the recreate." % (
                    outcome, len(now), SERVICE))
        except Exception as exc:   # anything: only a confirmed recreate may end the hold
            set_status(message="Not settled: %s. Holding new work: %s." % (outcome, exc))
        save_status()
        time.sleep(SETTLE_RETRY_SECONDS)


def inspect(ref):
    result = docker_run(["docker", "inspect", ref], capture_output=True, text=True, timeout=60)
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
        pulled = docker_run(["docker", "pull", new_ref], capture_output=True, text=True, timeout=600)
        if pulled.returncode:
            return "The updater itself stayed on %s: could not pull %s." % (image_tag(ref), new_ref)
        # A second runner on the same host has its own updater; its helper must not remove this one's.
        project = (me["Config"].get("Labels") or {}).get("com.docker.compose.project", "")
        helper = SWAP_HELPER if project in ("", "tico", "tico-runner") else "%s-%s" % (SWAP_HELPER, project)
        docker_run(["docker", "rm", "-f", helper], capture_output=True, timeout=60)
        env = {"TICO_UPDATER_MODE": MODE, "TICO_PROJECT_DIR": host_dir, "TICO_COMPOSE_FILE": COMPOSE_FILE,
               "TICO_SWAP_TAG": tag, "TICO_SWAP_OLD_ID": me["Image"], "TICO_SWAP_OLD_REF": ref,
               "TICO_SWAP_NAME": me["Name"].lstrip("/"), "DOCKER_CONFIG": "/tmp/.docker",
               "TICO_IMAGE": IMAGE, "TICO_UPDATER_PULL": "never", "TICO_UPDATER_BUNDLE": "never"}
        argv = ["docker", "run", "-d", "--name", helper, "--network", "none", "--read-only", "--tmpfs", "/tmp",
                "--security-opt", "no-new-privileges:true", "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE",
                "-v", "/var/run/docker.sock:/var/run/docker.sock", "-v", "%s:%s" % (host_dir, host_dir)]
        for key, value in env.items():
            argv += ["-e", "%s=%s" % (key, value)]
        docker_run([*argv, new_ref, "python", "/usr/local/bin/tico-updater", "replace-self"],
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
    try:
        docker_run(["docker", "tag", old_id, old_ref], check=False, timeout=INSPECT_SECONDS)
    except subprocess.SubprocessError as exc:
        print("tico-updater: could not tag the old updater back: %s" % exc, flush=True)
    try:
        compose(*up, extra_env={"TICO_UPDATER_TAG": image_tag(old_ref)})
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print("tico-updater: could not restart the old updater: %s" % exc, flush=True)
        return 2
    print("tico-updater: went back to %s" % old_ref, flush=True)
    return 1


def prune_images(keep, keep_ids=()):
    """After a healthy update, drop this service's and the updater's older release images (the new one and the one to
    roll back to stay). A newer release may be staged for another install on this Docker host, so it stays too.
    Without pruning every old release stays on disk until the host fills and an update fails. An image a container
    still uses is refused by Docker and kept."""
    def core(tag):
        found = re.fullmatch(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)", tag)
        return tuple(int(part) for part in found.groups()) if found else None

    keep = {str(k) for k in keep if k} | {"latest"}
    versions = {version for tag in keep if (version := core(tag)) is not None}
    newest = max(versions, default=None)
    keep_ids = {str(i) for i in keep_ids if i}
    for repo in (IMAGE, IMAGE.replace("tico-runner", "tico-updater") if MODE == "runner" else IMAGE + "-updater"):
        try:
            listed = docker_run(["docker", "image", "ls", repo, "--format", "{{.Tag}}"], capture_output=True, text=True,
                                    timeout=60).stdout.split()
        except (OSError, subprocess.SubprocessError):
            continue
        for tag in listed:
            version = core(tag)
            if version is None or newest is None or version in versions or version > newest:
                continue
            docker_run(["docker", "rmi", repo + ":" + tag], capture_output=True, check=False, timeout=120)
        # A release left untagged when `latest` (or a re-pulled tag) moved on: nothing would ever remove it.
        try:
            dangling = docker_run(["docker", "image", "ls", repo, "--filter", "dangling=true", "--no-trunc",
                                       "--format", "{{.ID}}"], capture_output=True, text=True, timeout=60).stdout.split()
        except (OSError, subprocess.SubprocessError):
            continue
        for image in dangling:
            if image not in keep_ids:   # the image just replaced stays, untagged or not
                docker_run(["docker", "rmi", image], capture_output=True, check=False, timeout=120)


def update(version, running=""):
    """`running` is the release the server reported it was running when the update was asked for: what "from" says.
    The image tag only says what to put back if this fails, and can be `latest`.

    A runner holds new work while this says pulling or restarting, so it must always end, and say how only once it
    can change nothing more: every docker call has a time limit (docker_run() kills and reaps one that runs over), an
    overrun goes through the same rollback as a failure, and the outcome is set after the last call. Killing the
    client does not undo what Docker accepted, so after a container-changing command overran this says uncertain
    (still holding) until settle() has Docker confirm the old version."""
    staging = None
    bundled = False
    snapshot, switched = "", False
    caddy = False
    tunnel = []   # the tunnel front door's services in the new bundle, reconciled once the server is healthy
    unsettled = False   # a command that changes containers did not report back: settle() before any outcome
    try:
        try:
            image_id, previous = running_image()
        except (RuntimeError, OSError, IndexError, subprocess.SubprocessError) as exc:
            set_status(state="failed", to=version, message="Not updated: could not read the running %s (%s)." % (SERVICE, exc))
            return
        if not release_name(previous) and release_name(running):
            previous = release_name(running)   # an untagged or `latest` image: the server knows which release it is
        set_status(state="pulling", **{"from": release_name(running) or previous, "to": version}, message="", snapshot="", restored=False,
                   front_door="", settle_caddy=False, settle_tunnel=False)
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
        caddy = selected_caddy()
        set_status(settle_caddy=caddy)
        if BUNDLE != "never":
            apply_bundle(files, release)
            bundled = True
        try:
            if bundled:   # listed first: a runner, or a server without the profile, has no cloudflared to ask about
                tunnel = tunnel_services()
                tunnel = tunnel if tunnel and selected_tunnel() else []
            set_status(settle_tunnel=bool(tunnel))
            if PULL != "never":
                compose("pull", SERVICE, tag=version)
                if caddy:
                    # The verified target bundle may pin a new front-door image.
                    # Stage it before switching; rollback/settle use local images.
                    compose("pull", "caddy", tag=version)
                if tunnel:   # likewise the hold and cloudflared: one that cannot be pulled refuses the update here
                    compose("pull", *tunnel, tag=version)
            if MODE == "server":
                # Last thing before the switch, so little is lost if it is undone: a new version may migrate the
                # schema and then fail its health check, and the old image cannot read a newer schema.
                try:
                    snapshot = take_snapshot(previous)
                except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    raise RuntimeError("could not snapshot the database first (%s)" % exc)
                set_status(snapshot=snapshot)
            if MODE == "runner":
                # Saved, so an updater stopped during the switch settles it when it starts again (load_status); the
                # server keeps its old way, since settling there would bring back the old image on a migrated database.
                set_status(state="restarting", settle_tag=previous,
                           settle_outcome="the updater stopped while it was switching to " + version)
                save_status()
            else:
                set_status(state="restarting")
            switched = True
            compose("up", "-d", "--no-deps", "--pull", "never", SERVICE, tag=version)
            if not healthy(HEALTH_SECONDS):
                raise RuntimeError(("the runner" if MODE == "runner" else "the server") + " did not come up healthy within %d seconds" % HEALTH_SECONDS)
            check_switched(version, target)
            if caddy:
                reconcile_caddy(version)
            if tunnel:
                reconcile_tunnel(version)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            unsettled = isinstance(exc, Unsettled)
            restoring = False
            # The old image is still on disk; point its tag back at it and start it again, with the old bundle.
            restored = ""
            try:
                docker_run(["docker", "tag", image_id, IMAGE + ":" + previous], check=False, timeout=INSPECT_SECONDS)
                if bundled:
                    restore_bundle()
                if switched and snapshot:
                    # The new version may have migrated the database; put back the copy taken just before.
                    try:
                        restore_snapshot(snapshot, previous)
                        restored = " Restored the database from the snapshot taken before the update (%s); changes made since then are not in it." % snapshot
                        set_status(restored=True)
                    except (RuntimeError, OSError, subprocess.SubprocessError) as snap_exc:
                        restoring = isinstance(snap_exc, Unsettled)   # a killed stop or restore may still act
                        unsettled = unsettled or restoring
                        restored = (" The database snapshot restore did not report back (%s), so it may or may not have put %s back; it is in %s."
                                    if getattr(snap_exc, "restoring", False)
                                    else " Could not restore the database snapshot %s (%s); it is in %s.") % (
                            (snap_exc, snapshot, SNAPSHOTS) if getattr(snap_exc, "restoring", False) else (snapshot, snap_exc, SNAPSHOTS))
                    print("tico-updater: rollback:" + restored, flush=True)
                back = False
                if not restoring:   # else settle() starts the old server once no restore can still act
                    compose("up", "-d", "--no-deps", "--pull", "never", SERVICE, tag=previous)
                    back = healthy(HEALTH_SECONDS)
                    if back and caddy and not unsettled:
                        reconcile_caddy(previous)
                    if back and tunnel and not unsettled:
                        reconcile_tunnel(previous)   # the restored bundle's route: unchanged unless it had moved
            except (RuntimeError, OSError, subprocess.SubprocessError) as rollback_exc:
                unsettled = unsettled or isinstance(rollback_exc, Unsettled)
                back = False
                restored += " Could not confirm rollback (%s)." % rollback_exc
            if unsettled:
                settle(previous, str(exc) + restored)
                return
            set_status(state="rolled_back" if back else "failed", settle_tag="", settle_outcome="", settle_caddy=False, settle_tunnel=False,
                       message=str(exc) + (". Went back to " + previous + "." if back else ". The old version did not start either.") + restored)
            return
        remember(version)
        prune_images(keep={version, previous, release_name(version), release_name(previous)}, keep_ids={image_id})
        message = ""
        if bundled and MODE == "server":
            # Slack, the front door and anything new follow the new compose file; the updater itself stays put.
            for name in other_services():
                if name == "caddy" or name in tunnel:   # front doors were reconciled above; do not enable another profile
                    continue
                try:
                    compose("up", "-d", "--no-deps", name, tag=version)
                except RuntimeError as exc:
                    message = "Updated, but %s did not restart: %s" % (name, exc)
        set_status(state="healthy", message=message, settle_tag="", settle_outcome="", settle_caddy=False, settle_tunnel=False)
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
            if status["state"] in ("pulling", "restarting", "uncertain"):
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
    threading.Thread(target=cap_running_runner, daemon=True).start()   # not in front of the server: a slow docker waits here
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
