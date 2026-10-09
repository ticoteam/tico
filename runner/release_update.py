"""Keeps a runner on the release its server runs (docs/updates.md).

The server names its release (`GET runners/desired`); GitHub is never asked. A runner on a git
checkout (Mac, Linux) moves to that release's tag when no turn is running, and a Docker runner asks its
updater sidecar (docker/updater.py in runner mode) for the matching image. Nothing here discards local
work: a checkout with changes is left alone and the reason goes to Health.

A checkout update runs as a separate process (`python -m runner.release_update`). It has to outlive
the runner it restarts, and it is the one thing that can put the old code back when the new code
does not come up.
"""
import argparse
import fcntl
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
STATUS_FILE = "update-status.json"
RETRY_AFTER_S = 6 * 3600     # after a failed or refused update, until the server's release changes
HEALTH_S = 180
DRAIN_S = 20 * 60            # a long turn may not hold the update back for ever: after this, stop claiming
STALE_UPDATING_S = 15 * 60   # an update that has said "updating" this long without an outcome died
LOCK_WAIT_S = 10 * 60        # for another environment's update of the same checkout; under STALE_UPDATING_S
LOCK_FILE = "tico-update.lock"
KINDS = ("mac", "linux", "docker")
HELPERS = ("connectors", "close-calls", "importers")   # scripts/tico installs these as launchd jobs (systemd user units on Linux) beside the bot job
NO_SUPERVISOR = ("no supervisor would start this runner again after an update: run `scripts/tico install` "
                 "once on this computer and it updates itself")


def key(value):
    """Sort key of a semantic version, or None. (backend/releases.py parse; the runner does not import the backend.)"""
    match = SEMVER.match(str(value or "").strip().lstrip("v"))
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    order = (1,) if not pre else (0, *((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")))
    return int(major), int(minor), int(patch), order


def behind(have, want):
    """Whether `have` should move to `want`: an untagged or unknown release always should."""
    wanted = key(want)
    return bool(wanted and (key(have) is None or key(have) < wanted))


def kind(env=os.environ, exists=os.path.exists):
    if env.get("TICO_RUNNER_KIND") in KINDS:
        return env["TICO_RUNNER_KIND"]
    if exists("/.dockerenv"):
        return "docker"
    return "mac" if sys.platform == "darwin" else "linux"


def git(root, *args, run=subprocess.run, timeout=60):
    return run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout,
               stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def checkout_release(root=ROOT, run=subprocess.run):
    """The release tag HEAD is exactly on ("0.3.0"), or "" for anything else (main, a branch)."""
    try:
        done = git(root, "describe", "--tags", "--exact-match", "HEAD", run=run, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    tag = done.stdout.strip().lstrip("v")
    return tag if not done.returncode and key(tag) else ""


def checkout_commit(root=ROOT, run=subprocess.run):
    """The commit HEAD names, or "" when git cannot say."""
    try:
        done = git(root, "rev-parse", "HEAD", run=run, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    commit = done.stdout.strip()
    return commit if not done.returncode and re.fullmatch(r"[0-9a-f]{40,64}", commit) else ""


def current_release(env=os.environ, root=ROOT, run=subprocess.run):
    if kind(env) == "docker":
        value = env.get("TICO_VERSION", "").strip().lstrip("v")   # the image sets it; `latest` and `dev` are no release
        return value if key(value) else ""
    return checkout_release(root, run)


# -- the outcome of the last checkout update, for the next heartbeat ------------------------------

def disk_space_error(error):
    return any(word in str(error).lower() for word in ("no space left", "not enough disk space", "disk full", "enospc"))


def disk_error_message(error):
    return ("Not enough disk space to update. Free space on this computer (Docker: `docker image prune -a`); "
            "the update retries when space frees.") if disk_space_error(error) else error


def read_status(directory):
    try:
        data = json.loads((Path(directory) / STATUS_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(directory, **fields):
    path = Path(directory) / STATUS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(fields))
    os.replace(tmp, path)


# -- the update itself (a checkout) ---------------------------------------------------------------

def changed_files(root, old, new, run=subprocess.run):
    done = git(root, "diff", "--name-only", old, new, run=run)
    return done.stdout.split() if not done.returncode else ["backend/requirements.txt"]


def pip_install(root, old, new, run=subprocess.run):
    """The install step: dependencies, when the release changed them. Returns an error or "". `sys.executable` is the
    runner's own interpreter: spawn starts the update process with it (TICO_RUNNER_PYTHON when that is set)."""
    if "backend/requirements.txt" not in changed_files(root, old, new, run):
        return ""
    done = run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                str(Path(root) / "backend" / "requirements.txt")], capture_output=True, text=True, timeout=900)
    if not done.returncode:
        return ""
    if disk_space_error(done.stderr):
        return disk_error_message(done.stderr)
    return "pip install failed: " + (done.stderr.strip().splitlines() or ["no output"])[-1]


def in_flight(directory):
    import sqlite3
    try:
        c = sqlite3.connect("file:" + str(Path(directory) / "runner.sqlite") + "?mode=ro", uri=True)
        return c.execute("SELECT count(*) FROM attempts WHERE phase IN ('claimed','running')").fetchone()[0]
    except sqlite3.Error:
        return 0


def systemd_unit(env=os.environ):
    """The systemd user unit that runs this runner (`scripts/tico install` on Linux sets TICO_SYSTEMD_UNIT), or ""."""
    unit = str(env.get("TICO_SYSTEMD_UNIT") or "").strip()
    return unit if unit.endswith(".service") and "/" not in unit else ""


def restart_via_systemd(unit, run=subprocess.run, say=None):
    """`systemctl --user restart <unit>`: systemd stops the runner and starts it on whatever is checked out. Returns
    whether it did; a caller that gets False falls back to stopping the process, which `Restart=always` starts again."""
    say = say or (lambda line: print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + line, flush=True))
    try:
        done = run(["systemctl", "--user", "restart", unit], capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"Tico update: systemctl could not restart {unit}: {type(exc).__name__}")
        return False
    if done.returncode:
        say(f"Tico update: systemctl could not restart {unit}: exited {done.returncode}")
        return False
    say(f"Tico update: restarted {unit} on the new release")
    return True


def restart_runner(pid, wait=60, env=os.environ, run=subprocess.run):
    """Restart the runner through its supervisor: `systemctl --user restart` for a systemd unit, otherwise stop it and let
    the supervisor (launchd KeepAlive, the Docker restart policy, systemd's Restart=always) start it again on whatever is
    checked out."""
    unit = systemd_unit(env)
    if unit and restart_via_systemd(unit, run):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def helper_labels(env=os.environ):
    """The launchd label of each helper job. scripts/tico names them from the bot job's label (`team.tico.tico-bot`,
    or `team.tico.tico.<env>.bot` for a company environment), and launchd gives this process that label."""
    service = env.get("XPC_SERVICE_NAME", "")
    prefix = service[:-3] if service.startswith("team.tico") and service.endswith("bot") else "team.tico.tico-"
    return {helper: prefix + helper for helper in HELPERS}


def restart_helpers(env=os.environ, run=subprocess.run, agents=None, uid=None, say=None):
    """Restart the helper jobs that are installed on this Mac (scripts/tico restart semantics: `launchctl kickstart -k`,
    or a bootstrap when the job is installed but not loaded), so none keeps the old release in memory. A job with no
    plist is not installed and is left alone. Returns the restarted job names."""
    say = say or (lambda line: print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + line, flush=True))
    agents = Path(agents) if agents else Path.home() / "Library" / "LaunchAgents"
    domain = f"gui/{os.getuid() if uid is None else uid}"
    restarted = []
    for helper, label in helper_labels(env).items():
        plist = agents / f"{label}.plist"
        if not plist.is_file():
            continue
        try:
            done = run(["launchctl", "kickstart", "-k", f"{domain}/{label}"], capture_output=True, text=True, timeout=60)
            if done.returncode:
                done = run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            say(f"Tico update: could not restart {label}: {type(exc).__name__}")
            continue
        if done.returncode:
            say(f"Tico update: could not restart {label}: launchctl exited {done.returncode}")
            continue
        say(f"Tico update: restarted {label} on the new release")
        restarted.append(helper)
    return restarted


def restart_helpers_systemd(env=os.environ, run=subprocess.run, unit_dir=None, say=None):
    """The systemd counterpart of restart_helpers: restart the helper units installed beside this runner's unit (their
    files are in the user unit directory), so none keeps the old release in memory. Returns the restarted job names."""
    from .systemd_units import helper_units, is_installed
    say = say or (lambda line: print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + line, flush=True))
    restarted = []
    for helper, unit in helper_units(systemd_unit(env)).items():
        if not is_installed(unit, unit_dir):
            continue
        try:
            done = run(["systemctl", "--user", "restart", unit], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            say(f"Tico update: could not restart {unit}: {type(exc).__name__}")
            continue
        if done.returncode:
            say(f"Tico update: could not restart {unit}: systemctl exited {done.returncode}")
            continue
        say(f"Tico update: restarted {unit} on the new release")
        restarted.append(helper)
    return restarted


def wait_healthy(directory, commit, since, seconds=HEALTH_S, sleep=time.sleep):
    """True once a runner started on `commit` has heartbeated to the server after `since`."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        started = read_json(Path(directory) / "runner-revision")
        beat = Path(directory) / "heartbeat"
        if started.get("revision") == commit and beat.exists() and beat.stat().st_mtime >= since:
            return True
        sleep(3)
    return False


def read_json(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def lock_path(root, run=subprocess.run):
    """The update lock of a checkout, in git's common directory so every worktree of it shares one; None if git cannot say."""
    try:
        done = git(root, "rev-parse", "--git-common-dir", run=run, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    common = done.stdout.strip()
    return Path(root) / common / LOCK_FILE if not done.returncode and common else None


def apply(root, version, directory, *, run=subprocess.run, lock_wait=LOCK_WAIT_S, clock=time.time, **kw):
    """`update` (below) under the checkout's update lock. Runners of several company environments may share one checkout,
    and their updates must not interleave: one's rollback would move the checkout under another that already restarted.
    The second waits, saying "updating" so its runner does not start another, then sees where the first left the checkout.
    After `lock_wait` seconds it gives up with "waiting" and its runner tries again later."""
    path = lock_path(root, run)
    if path is None:
        return update(root, version, directory, run=run, clock=clock, **kw)
    target = version.lstrip("v")
    with open(path, "a") as handle:
        deadline, said = time.time() + lock_wait, False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    write_status(directory, state="waiting", target=target, error="", at=clock())
                    return {"state": "waiting", "target": target, "error": ""}
                if not said:
                    write_status(directory, state="updating", target=target, error="", at=clock())
                    said = True
                time.sleep(1)
        try:
            result = update(root, version, directory, run=run, clock=clock, **kw)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    if said and result["state"] == "waiting":     # not left saying "updating" while nothing is under way
        write_status(directory, state="waiting", target=target, error="", at=clock())
    return result


def update(root, version, directory, *, run=subprocess.run, install=pip_install, restart=None, healthy=None,
           busy=None, clock=time.time, helpers=None, started=""):
    """Move the checkout to release `version` and bring the runner back on it; undo that if it does not
    come back. Once it is healthy, `helpers` restarts the helper jobs so they run the new code too (a rollback
    needs nothing: a helper that saw the new revision exits again when the old one returns).
    `started` is the commit the runner process started on. A checkout that is already on the release while the
    runner still runs other code (another runner sharing the checkout moved it, or a person did) only needs
    the restart.
    Returns the status written: healthy, waiting, blocked, failed or rolled_back."""
    tag = "v" + version.lstrip("v")
    version = tag[1:]

    def say(state, error=""):
        error = disk_error_message(error)
        disk = {}
        if disk_space_error(error):
            try:
                disk["disk_free"] = shutil.disk_usage(directory).free
            except OSError:
                pass
        write_status(directory, state=state, target=version, error=error[:300], at=clock(), **disk)
        return {"state": state, "target": version, "error": error}

    def g(*args, timeout=60):
        return git(root, *args, run=run, timeout=timeout)

    if not key(version):
        return say("failed", f"{version!r} is not a release version")
    old = g("rev-parse", "HEAD").stdout.strip()
    branch = g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch not in ("main", "HEAD"):        # HEAD: detached, which is where a release update leaves it
        return say("blocked", f"the checkout is on branch {branch}; switch it to main to follow releases")
    dirty = [line for line in g("status", "--porcelain", "--untracked-files=no").stdout.splitlines() if line]
    if dirty:
        return say("blocked", f"the checkout has {len(dirty)} changed file{'s' if len(dirty) != 1 else ''}; "
                              "commit or stash them and the runner updates itself")
    fetched = g("fetch", "--quiet", "origin", f"refs/tags/{tag}:refs/tags/{tag}", timeout=120)
    if fetched.returncode:
        return say("failed", fetched.stderr if disk_space_error(fetched.stderr) else
                   f"release {tag} was not found on the public repository, or a local tag of that name differs")
    target = g("rev-parse", "--verify", "--quiet", tag + "^{commit}").stdout.strip()
    if not target:
        return say("failed", f"release {tag} does not name a commit")
    if busy and busy():
        return {"state": "waiting", "target": version, "error": ""}
    if target == old and started in ("", target):
        return say("healthy")
    say("updating")

    def restarted_helpers():
        if helpers:
            try:
                helpers()
            except Exception as exc:     # the runner is on the new release; a helper that is missed follows on its own
                print(f"Tico update: could not restart the helper jobs: {type(exc).__name__}", flush=True)

    if target == old:
        # Nothing to switch. The dependencies may still differ from the ones this runner started with (a runner with
        # its own interpreter); going back would only restart onto this same code, so a failure is reported as it is.
        error = install(root, started, target)
        if error:
            return say("failed", error)
        since = clock()
        restart and restart()
        if healthy and not healthy(target, since):
            return say("failed", f"the runner did not report in within {HEALTH_S} seconds on {tag}")
        result = say("healthy")
        restarted_helpers()
        return result
    back = branch if branch != "HEAD" else old

    def back_out(reason):
        """Old code, old dependencies, old runner: the reverse of what was done, then the reason."""
        g("checkout", "--quiet", back)
        note = install(root, target, old) if changed_files(root, old, target, run) else ""
        since = clock()
        restart and restart()
        ok = healthy(old, since) if healthy else True
        return say("rolled_back" if ok else "failed",
                   reason + (". Went back to the previous release." if ok else ". The previous release did not start either.")
                   + (" " + note if note else ""))

    switched = g("checkout", "--quiet", "--detach", target)
    if switched.returncode:
        return say("failed", switched.stderr if disk_space_error(switched.stderr) else f"could not switch to {tag}: local files are in the way")
    error = install(root, old, target)
    if error:
        return back_out(error)
    since = clock()
    restart and restart()
    if healthy and not healthy(target, since):
        return back_out(f"the runner did not report in within {HEALTH_S} seconds on {tag}")
    result = say("healthy")
    restarted_helpers()
    return result


def spawn(root, version, directory, pid, started="", env=os.environ, run=subprocess.run):
    """Start the update as its own process, so it survives the runner it restarts. Under systemd the runner's unit
    kills every process in its cgroup when it restarts, so the update is started as a transient unit of its own."""
    unit = systemd_unit(env)
    if unit:
        log = str(Path(directory) / "update.log")
        keep = {name: env[name] for name in ("PATH", "HOME", "TICO_SUPERVISED", "TICO_RUNNER_KIND") if env.get(name)}
        keep["TICO_SYSTEMD_UNIT"] = unit
        command = ["systemd-run", "--user", "--quiet", "--collect", f"--unit=tico-update-{pid}", f"--working-directory={root}",
                   *(f"--setenv={name}={value}" for name, value in keep.items()),
                   "-p", f"StandardOutput=append:{log}", "-p", f"StandardError=append:{log}",
                   sys.executable, "-m", "runner.release_update", "--root", str(root), "--version", version,
                   "--state-dir", str(directory), "--pid", str(pid), "--started", started]
        done = run(command, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        if done.returncode:
            raise OSError("systemd-run exited " + str(done.returncode))
        return done
    log = open(Path(directory) / "update.log", "ab")
    return subprocess.Popen([sys.executable, "-m", "runner.release_update", "--root", str(root), "--version", version,
                             "--state-dir", str(directory), "--pid", str(pid), "--started", started], cwd=str(root), stdin=subprocess.DEVNULL,
                            stdout=log, stderr=log, start_new_session=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m runner.release_update")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--version", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--started", default="")     # the commit the runner started on; "" from an older runner
    args = parser.parse_args(argv)
    deadline = time.time() + 600
    while in_flight(args.state_dir) and time.time() < deadline:   # the runner waits for quiet first; this is the second look
        time.sleep(10)
    helpers = restart_helpers if kind() == "mac" else restart_helpers_systemd if systemd_unit() else None
    result = apply(args.root, args.version, args.state_dir, busy=lambda: in_flight(args.state_dir),
                   restart=lambda: restart_runner(args.pid),
                   healthy=lambda commit, since: wait_healthy(args.state_dir, commit or "", since),
                   helpers=helpers, started=args.started)
    print(json.dumps(result))
    return 0 if result["state"] == "healthy" else 1


# -- the runner's side: decide, hold off, report ---------------------------------------------------

class Follower:
    """Decides whether this runner should update, and what to tell the server. One per runner process."""

    def __init__(self, config, directory, client, *, root=ROOT, env=os.environ, supervised=lambda: True,
                 run=subprocess.run, launch=spawn, sidecar=None, clock=time.monotonic, wall=time.time):
        self.config, self.directory, self.client, self.root = config or {}, Path(directory), client, root
        self.env, self.supervised, self.run, self._launch = env, supervised, run, launch
        self.sidecar = sidecar or Sidecar(env)
        self.clock, self.wall = clock, wall
        self.kind = kind(env)
        self.desired = None          # None: the server does not know this (an older server); "" a build with no release
        # What this process runs is what it started on, not what the checkout names now: runners of several
        # company environments may share one checkout, and the first to update moves it under the others.
        self.release = current_release(env, root, run)
        self.started = "" if self.kind == "docker" else checkout_commit(root, run)
        self.state, self.error, self.target = "idle", "", ""
        self.pending = None          # a release to move to as soon as nothing runs
        self.pending_since = None
        self.running = False         # an update is under way; nothing may be claimed

    @property
    def following(self):
        """True when the server names a release: this runner follows it, and not main."""
        return bool(self.desired)

    def pinned(self):
        if self.config.get("pinned") or self.env.get("TICO_RUNNER_PINNED") == "1":
            return True
        # A checkout with `self_update: false` was set up to stay where it is. A container always has that
        # flag (it cannot pull); for it, the updater sidecar is the way.
        return self.kind != "docker" and (self.config.get("self_update") is False
                                          or self.env.get("TICO_RUNNER_SELF_UPDATE") == "0")

    def poll(self, desired=None):
        """`desired`: the release the server's event stream named, so it need not be asked."""
        from clients.tico import APIError
        try:
            self.desired = str(desired if desired is not None else
                               (self.client.get("runners/desired") or {}).get("version") or "")
        except APIError as exc:
            if exc.status not in (404, 405):
                return                   # a blip: keep what we knew
            self.desired = None          # a server from before this existed: nothing is sent it, main is followed
        except Exception:
            return
        self.decide()

    def space_freed(self, status, error):
        if not disk_space_error(error):
            return False
        try:
            free = shutil.disk_usage("/" if self.kind == "docker" else self.directory).free
        except OSError:
            return False
        marker = (status.get("to") or status.get("target"), error, status.get("at"))
        previous = getattr(self, "_disk_failure", None)
        baseline = status.get("disk_free")
        if not isinstance(baseline, int) or baseline < 0:
            baseline = previous[1] if previous and previous[0] == marker else free
        self._disk_failure = (marker, baseline)
        return free > baseline

    def outcome(self, want):
        """(state, error) of the last update to `want` when it should not be tried again yet."""
        if self.kind == "docker":
            status = self.sidecar.status() or {}
            state = {"rolled_back": "rolled_back", "failed": "failed"}.get(status.get("state"))
            if state and str(status.get("to") or "").lstrip("v") == want:
                error = str(status.get("message") or "")
                return None if self.space_freed(status, error) else (state, disk_error_message(error))
            if status.get("state") in ("pulling", "restarting") and str(status.get("to") or "").lstrip("v") == want:
                return "updating", ""
            if status.get("state") == "healthy" and str(status.get("to") or "").lstrip("v") == want:
                # The image came up but does not say it is `want`: never loop on pulling it again.
                return "failed", f"updated to {want}, but this runner still reports {self.release or 'no release'}"
            return None
        status = read_status(self.directory)
        if status.get("target") != want:
            return None
        age = self.wall() - float(status.get("at") or 0)
        if status.get("state") == "updating":
            return ("updating", "") if age < STALE_UPDATING_S else ("failed", "the update stopped without an outcome")
        if status.get("state") in ("failed", "rolled_back", "blocked") and age < RETRY_AFTER_S:
            error = str(status.get("error") or "")
            return None if self.space_freed(status, error) else (status["state"], disk_error_message(error))
        return None

    def no_updater_hint(self, want):
        # A compose runner only lacks the updater when it was taken out; a bare `docker run` has none
        # and can only get one by being moved onto the compose file, which the installer does.
        if self.env.get("TICO_RUNNER_COMPOSE") == "1":
            return "no updater is set up; run docker compose pull && docker compose up -d on this box"
        return ("no updater is set up; move this runner onto the compose file that has one: on this box run "
                f"curl -fsSL https://github.com/ticoteam/tico/releases/download/v{want}/install.sh | sh -s -- --runner "
                "--url <your Tico address> --code <new code from Settings > Computers > Add computer> --label <name>")

    def decide(self):
        want = self.desired or ""
        self.target, self.pending, self.error = want, None, ""
        self.running = False
        if not want or not behind(self.release, want):
            self.state = "idle"
            self.pending_since = None
            return
        if self.pinned():
            self.state, self.error = "pinned", f"pinned to this release; the server runs {want}"
        elif self.kind != "docker" and not self.supervised():
            self.state, self.error = "blocked", NO_SUPERVISOR
        elif self.kind == "docker" and not self.sidecar.configured():
            self.state, self.error = "blocked", self.no_updater_hint(want)
        elif (last := self.outcome(want)):
            self.state, self.error = last
            self.running = self.state == "updating"
        else:
            self.pending, self.state = want, "waiting"
            if self.pending_since is None:
                self.pending_since = self.clock()
            return
        self.pending_since = None

    def blocks_claims(self, active):
        """True while an update is due or under way. Starts it once nothing is running."""
        if self.running:
            return True
        if not self.pending:
            return False
        if not active:
            self.launch()
            return True
        return self.clock() - self.pending_since >= DRAIN_S

    def launch(self):
        want = self.pending
        try:
            if self.kind == "docker":
                self.sidecar.start(want)
            else:
                self._launch(self.root, want, self.directory, os.getpid(), self.started)
        except urllib.error.HTTPError as exc:
            if exc.code != 409:      # 409: the updater is already on it
                return self.launch_failed(want, f"the updater answered {exc.code}")
        except Exception as exc:     # the runner keeps working; Health says why it did not update
            return self.launch_failed(want, type(exc).__name__)
        self.state, self.pending, self.running = "updating", None, True

    def launch_failed(self, want, why):
        self.state, self.error, self.pending = "failed", f"could not start the update: {why}", None
        write_status(self.directory, state="failed", target=want, error=self.error, at=self.wall())

    def fields(self):
        """What a heartbeat adds. Nothing for a server that would refuse unknown fields."""
        if self.desired is None:
            return {}
        return {"release": self.release or "dev", "kind": self.kind,
                "update": {"state": self.state, "target": self.target, "error": self.error[:300]}}


class Sidecar:
    """The updater sidecar next to a Docker runner: the only container that holds the Docker socket."""

    def __init__(self, env=os.environ):
        self.url = env.get("TICO_UPDATER_URL", "").rstrip("/")
        self.token_file = env.get("TICO_UPDATER_TOKEN_FILE", "/control/updater-token")

    def configured(self):
        return bool(self.url)

    def call(self, method, path, body=None):
        try:
            token = Path(self.token_file).read_text().strip()
        except OSError:
            token = ""
        request = urllib.request.Request(self.url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as reply:
            return json.loads(reply.read() or b"{}")

    def status(self):
        try:
            return self.call("GET", "/status") if self.url else None
        except (OSError, ValueError):
            return None

    def start(self, version):
        return self.call("POST", "/update", {"version": version})


if __name__ == "__main__":
    sys.exit(main())
