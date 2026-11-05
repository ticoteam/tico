"""Moves a checkout to the release its server runs (docs/updates.md).

The Rust runner decides when (runner-rs/src/follow.rs) and starts this as a separate process
(`python -m runner_support.release_update`). It has to outlive the runner it restarts, and it is the one thing
that can put the old code back when the new code does not come up. Nothing here discards local work:
a checkout with changes is left alone and the reason goes to Health.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .regular_file import SMALL, read as read_regular, lock as open_regular_lock

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
STATUS_FILE = "update-status.json"
HEALTH_S = 180
LOCK_WAIT_S = 10 * 60        # for another environment's update of the same checkout; under follow.rs STALE_UPDATING_S
LOCK_FILE = "tico-update.lock"
PROCESS_LOCK = "update-process.lock"  # follow.rs probes this before starting an updater or declaring one stale
GIT_BUSY = "repository copy needs attention: Git work is busy or its lock cannot be checked"
MAX_DEFERRALS = 3           # repeats of the same non-transient cause before Health asks for help
DEFERRAL_DELAYS_S = (120, 300, 900, 1800, 3600)
DEFERRAL_LIMIT_S = 2 * 3600
DEFERRAL_FIELDS = ("deferrals", "deferred_since", "deferral_cause", "cause_count")
KINDS = ("mac", "linux", "docker")
HELPERS = ("connectors", "close-calls", "importers")   # scripts/tico installs these as launchd jobs (systemd user units on Linux) beside the bot job


def key(value):
    """Sort key of a semantic version, or None. (backend/releases.py parse; the runner does not import the backend.)"""
    match = SEMVER.match(str(value or "").strip().lstrip("v"))
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    order = (1,) if not pre else (0, *((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")))
    return int(major), int(minor), int(patch), order


def kind(env=os.environ, exists=os.path.exists):
    if env.get("TICO_RUNNER_KIND") in KINDS:
        return env["TICO_RUNNER_KIND"]
    if exists("/.dockerenv"):
        return "docker"
    return "mac" if sys.platform == "darwin" else "linux"


def git(root, *args, run=subprocess.run, timeout=60):
    from . import git_locks
    command = ["git", "-C", str(root), *args]
    kwargs = dict(capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                  env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    try:
        with git_locks.command_lock(root, wait=min(timeout, 10)) as common:
            done = run(command, **kwargs)
            if done.returncode:
                note = git_locks.report(root, done.stderr or "", common)
                if note:
                    done.stderr = note
            return done
    except BlockingIOError:
        return subprocess.CompletedProcess(command, 1, "", GIT_BUSY)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def repair_instruction_links(root, g):
    """Repair dereferenced instruction links only when they contain exactly the repository's own bytes.

    A filename is not proof of runner ownership: staged changes, edits to the target, and different contents stay.
    Some copy/archive tools flatten the tracked AGENTS.md/CLAUDE.md links in an installed checkout.
    """
    root = Path(root).resolve()
    metadata = g("rev-parse", "--absolute-git-dir")
    git_dir = Path(metadata.stdout.rstrip("\n"))
    if metadata.returncode or not git_dir.is_absolute() or not git_dir.is_dir() or git_dir.is_symlink():
        return
    listing = g("ls-files", "--stage", "-z", "--", "AGENTS.md", "CLAUDE.md",
                ":(glob)**/AGENTS.md", ":(glob)**/CLAUDE.md")
    if listing.returncode:
        return
    for entry in listing.stdout.split("\0"):
        info, sep, name = entry.partition("\t")
        if not sep or not info.startswith("120000 ") or not info.endswith(" 0"):
            continue
        path = root / name
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or path.parent.resolve() != path.parent:
                continue
            if g("diff", "--cached", "--quiet", "HEAD", "--", name).returncode:
                continue
            link = g("cat-file", "blob", info.split()[1])
            if link.returncode or not link.stdout or "\n" in link.stdout:
                continue
            target = (path.parent / link.stdout).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                continue
            relative = target.relative_to(root).as_posix()
            content = read_regular(path)
            accepted = read_regular(target)
            if content != link.stdout.encode():
                original = g("show", "HEAD:" + relative)
                if original.returncode or g("diff", "--quiet", "HEAD", "--", relative).returncode:
                    continue
                if content != original.stdout.encode() or content != accepted:
                    continue
            # Keep open writers attached to a named inode in metadata, even after the link is restored.
            digest = hashlib.sha256(accepted).hexdigest()
            backup = git_dir / "tico-instruction-backups" / Path(name).parent / (Path(name).name + f".tico-backup-{int(time.time())}-{uuid.uuid4().hex}-sha256-{digest}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.parent.resolve() != backup.parent:
                continue
            after = path.lstat()
            if (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns) or read_regular(path) != content:
                continue
            path.rename(backup)
            saved = backup.lstat()
            if (saved.st_dev, saved.st_ino) != (before.st_dev, before.st_ino):
                # The replacement and the raced file both stay available for review.
                with contextlib.suppress(OSError):
                    os.link(backup, path, follow_symlinks=False)
                continue
            try:
                path.symlink_to(link.stdout)
            except OSError:
                # Never replace another writer's new path; the backup still holds the original.
                with contextlib.suppress(OSError):
                    os.link(backup, path, follow_symlinks=False)
        except (OSError, ValueError):
            continue
    return instruction_backup_note(root, git_dir)


def instruction_backup_note(root, git_dir):
    """Recheck retained backups on every update so a late write is named in the next Health report."""
    saved = git_dir / "tico-instruction-backups"
    names = []
    if saved.is_symlink():
        note = "repository copy needs attention: cannot inspect instruction backups: " + os.path.relpath(saved, root)
        print(note, file=sys.stderr)
        return note
    try:
        for backup in saved.rglob("*.tico-backup-*"):
            relative = backup.relative_to(saved)
            name = relative.with_name(relative.name.rsplit(".tico-backup-", 1)[0])
            try:
                before = backup.lstat()
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or backup.parent.resolve() != backup.parent:
                    names.append(os.path.relpath(backup, root))
                    continue
                _, marked, digest = backup.name.rpartition("-sha256-")
                if marked and not re.fullmatch(r"[0-9a-f]{64}", digest):
                    names.append(os.path.relpath(backup, root))
                    continue
                if not marked:
                    target = (root / name).resolve(strict=True)
                    if not target.is_relative_to(root):
                        raise OSError("instruction target is outside checkout")
                    digest = hashlib.sha256(read_regular(target)).hexdigest()
                if hashlib.sha256(read_regular(backup)).hexdigest() != digest:
                    names.append(os.path.relpath(backup, root))
                    continue
                # Keep the inode: a writer can still hold it after the link is restored.
                after = backup.lstat()
                if (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns) or hashlib.sha256(read_regular(backup)).hexdigest() != digest:
                    names.append(os.path.relpath(backup, root))
                elif not marked:
                    # Accept a legacy backup only while it still equals the checkout; preserve its inode.
                    baseline = backup.with_name(backup.name + "-sha256-" + digest)
                    if baseline.exists() or baseline.is_symlink():
                        names.append(os.path.relpath(backup, root))
                    else:
                        backup.rename(baseline)
            except OSError:
                names.append(os.path.relpath(backup, root))
    except OSError:
        names.append(os.path.relpath(saved, root))
    if names:
        note = "repository copy needs attention: bot instruction edit to review: " + ", ".join(names[:3])
        print(note, file=sys.stderr)
        return note
    return ""


def checkout_release(root=ROOT, run=subprocess.run):
    """The release tag HEAD is exactly on ("0.3.0"), or "" for anything else (main, a branch)."""
    try:
        done = git(root, "describe", "--tags", "--exact-match", "HEAD", run=run, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    tag = done.stdout.strip().lstrip("v")
    return tag if not done.returncode and key(tag) else ""


# -- the outcome of the last checkout update, for the next heartbeat ------------------------------

def disk_space_error(error):
    return any(word in str(error).lower() for word in ("no space left", "not enough disk space", "disk full", "enospc"))


def disk_error_message(error):
    return ("Not enough disk space to update. Free space on this computer (Docker: `docker image prune -a`); "
            "the update retries when space frees.") if disk_space_error(error) else error


def read_status(directory, *, strict=False):
    try:
        data = json.loads(read_regular(Path(directory) / STATUS_FILE, SMALL))
        if strict and not isinstance(data, dict):
            raise ValueError("update status must be an object")
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        if strict:
            raise
        return {}


def write_status(directory, **fields):
    if fields.get("state") == "updating":
        # Waiting on a shared lock must not erase the deferral history before this attempt can defer again.
        before = read_status(directory)
        if before.get("target") == fields.get("target"):
            fields.update({name: before[name] for name in DEFERRAL_FIELDS if name in before})
    path = Path(directory) / STATUS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(fields))
    os.replace(tmp, path)


def defer_update(directory, version, reason, *, clock=time.time, transient=False):
    """Release claims until the next try; old followers need failed because they ignore not_before."""
    target = version.lstrip("v")
    reason = " ".join(str(reason).split())[:250]
    try:
        before = read_status(directory)
    except Exception:
        # A failed status read must not prevent replacing it with a retry time.
        before = {}
    before = before if before.get("target") == target else {}
    now = clock()
    count = before.get("deferrals", 0)
    count = count if type(count) is int and count >= 0 else 0
    count = min(count + 1, len(DEFERRAL_DELAYS_S))
    repeats = before.get("cause_count", 0)
    repeats = repeats if type(repeats) is int and repeats >= 0 else 0
    since = before.get("deferred_since", now) if repeats else now
    since = since if type(since) in (int, float) and 0 <= since <= now else now
    if transient:
        repeats = 0
    elif before.get("deferral_cause") == reason:
        repeats = min(repeats + 1, MAX_DEFERRALS)
    else:
        repeats = 1
    blocked = not transient and (repeats == MAX_DEFERRALS or now - since >= DEFERRAL_LIMIT_S)
    short = os.environ.get("TICO_UPDATE_RETRY") == "short"
    delay = DEFERRAL_DELAYS_S[count - 1] if short else 6 * 3600
    state = "blocked" if blocked else "idle" if short else "failed"
    prefix = "update paused: "
    if blocked:
        prefix = f"update blocked after {repeats} tries: " if repeats == MAX_DEFERRALS else "update blocked after two hours: "
    error = prefix + reason
    error = error[:250] + (f"; retrying in {delay // 60} minutes" if short else "; retrying in six hours")
    timer = {"not_before": now + delay} if short else {}
    write_status(directory, state=state, target=target, error=error, at=now, deferrals=count,
                 deferred_since=now if transient else since, deferral_cause="" if transient else reason,
                 cause_count=repeats, **timer)
    return {"state": state, "target": target, "error": error}


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


def prepare_binary(root, run=subprocess.run):
    """The checkout's Rust runner, ready before the restart, so a release with none fails here and goes back. A new
    process: this one still has the old code in memory. Returns an error or "". A release from before
    runner_support/ (going back to one) keeps its engine in runner/; a tree with neither is left to get its own."""
    module = next((name for name in ("runner_support", "runner") if (Path(root) / name / "engine.py").is_file()), None)
    if not module:
        return ""
    try:
        done = run([sys.executable, "-m", f"{module}.engine", "prepare"], cwd=str(root), capture_output=True, text=True,
                   timeout=2400, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not get the Rust runner ready ({type(exc).__name__})"
    if not done.returncode:
        return ""
    if disk_space_error(done.stderr):
        return disk_error_message(done.stderr)
    return (done.stderr.strip().splitlines() or ["could not get the Rust runner ready"])[-1]


# What the runner binary is built from: runner-rs/build.rs's source fingerprint (runner_support/engine.py source_id).
# runner/harnesses is where releases from before runner_support/ keep the manifests, for a move across that change.
RUNNER_SOURCES = ("runner-rs/build.rs", "runner-rs/Cargo.toml", "runner-rs/Cargo.lock", "runner-rs/src", "runner-rs/harnesses",
                  "runner/harnesses")


def binary_pending(root, old, target, version, run=subprocess.run, listed=None, which=shutil.which):
    """Why this checkout should wait before moving to `target` (release `version`), or "". A release whose runner
    changed is published before its Mac binaries (release.yml macos-assets adds them minutes later). Moving now would
    fail to get a binary, go back, and not try again for hours, so the update waits while the release's checksum
    file does not list this computer's binary yet. Same runner sources: the binary it has fits. With cargo it builds one."""
    if git(root, "diff", "--quiet", old, target, "--", *RUNNER_SOURCES, run=run).returncode == 0:
        return ""
    if which("cargo"):
        return ""
    if listed is None:
        from .engine import published as listed
    if listed(version) is False:
        return f"release v{version} does not have this computer's runner binary yet; the update waits for it"
    return ""


def in_flight(directory):
    import sqlite3
    try:
        with contextlib.closing(sqlite3.connect("file:" + str(Path(directory) / "runner.sqlite") + "?mode=ro", uri=True)) as c:
            return c.execute("SELECT count(*) FROM attempts WHERE phase IN ('claimed','running')").fetchone()[0]
    except sqlite3.Error as exc:
        raise RuntimeError(f"cannot check running work: {exc}") from exc


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
    """The checkout's shared update lock; None for a busy Git gate, an error for other lookup failures."""
    done = git(root, "rev-parse", "--git-common-dir", run=run, timeout=10)
    if done.returncode and done.stderr == GIT_BUSY:
        return None
    common = done.stdout.strip()
    if done.returncode or not common:
        raise OSError(done.stderr.strip() or "the checkout's update lock cannot be found")
    return Path(root) / common / LOCK_FILE


def apply(root, version, directory, *, run=subprocess.run, lock_wait=LOCK_WAIT_S, clock=time.time, **kw):
    """`update` (below) under the checkout's update lock. Runners of several company environments may share one checkout,
    and their updates must not interleave: one's rollback would move the checkout under another that already restarted.
    The second waits, saying "updating" so its runner does not start another, then sees where the first left the checkout.
    If the lock cannot be found, or after `lock_wait` seconds, it defers and releases claims until the next retry."""
    path = lock_path(root, run)
    target = version.lstrip("v")
    if path is None:
        return defer_update(directory, target, "the checkout is busy or its update lock cannot be checked", clock=clock, transient=True)
    with open_regular_lock(path) as handle:
        deadline, said = time.time() + lock_wait, False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    return defer_update(directory, target, "another update still holds the checkout lock", clock=clock, transient=True)
                if not said:
                    write_status(directory, state="updating", target=target, error="", at=clock())
                    said = True
                time.sleep(1)
        try:
            result = update(root, version, directory, run=run, clock=clock, **kw)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return result


def update(root, version, directory, *, run=subprocess.run, install=pip_install, prepare=prepare_binary,
           restart=None, healthy=None, busy=None, clock=time.time, helpers=None, started="", listed=None,
           which=shutil.which):
    """Move the checkout to release `version`, get its Rust runner ready (`prepare`), and bring the runner back on
    it; undo that if either fails or the runner does not come back. Once it is healthy, `helpers` restarts the helper jobs so they run the new code too (a rollback
    needs nothing: a helper that saw the new revision exits again when the old one returns).
    `started` is the commit the runner process started on. A checkout that is already on the release while the
    runner still runs other code (another runner sharing the checkout moved it, or a person did) only needs
    the restart.
    Returns the status written: healthy, blocked, failed or rolled_back."""
    tag = "v" + version.lstrip("v")
    version = tag[1:]

    def say(state, error="", **extra):
        # A blocked checkout's message holds file names, which may look like a disk error (test_enospc.py).
        error = error if state == "blocked" else disk_error_message(error)
        disk = {}
        if state != "blocked" and disk_space_error(error):
            try:
                disk["disk_free"] = shutil.disk_usage(directory).free
            except OSError:
                pass
        write_status(directory, state=state, target=version, error=error[:300], at=clock(), **disk, **extra)
        return {"state": state, "target": version, "error": error}

    def g(*args, timeout=60):
        return git(root, *args, run=run, timeout=timeout)

    if not key(version):
        return say("failed", f"{version!r} is not a release version")
    old = g("rev-parse", "HEAD").stdout.strip()
    branch = g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch not in ("main", "HEAD"):        # HEAD: detached, which is where a release update leaves it
        return say("blocked", f"the checkout is on branch {branch}; switch it to main to follow releases")
    instruction_note = repair_instruction_links(root, g)
    dirty = [line for line in g("status", "--porcelain", "--untracked-files=no").stdout.splitlines() if line]
    if instruction_note:
        names = ", ".join(line[3:] for line in dirty[:3])
        return say("blocked", instruction_note + (f"; changed files: {names}" if names else ""))
    if dirty:
        # "XY path" lines: the status letters and the space come off; a rename keeps its "old -> new" as it is.
        named = ", ".join(line[3:] for line in dirty[:3])
        more = f", and {len(dirty) - 3} more" if len(dirty) > 3 else ""
        # The names go last: say() cuts the error at 300 characters, and the instruction must survive that.
        return say("blocked", f"the checkout has {len(dirty)} changed file{'s' if len(dirty) != 1 else ''}; "
                              f"commit or stash them and the runner updates itself: {named}{more}")
    fetched = g("fetch", "--quiet", "origin", f"refs/tags/{tag}:refs/tags/{tag}", timeout=120)
    if fetched.returncode:
        return say("failed", fetched.stderr if fetched.stderr.startswith("repository copy needs attention:") or disk_space_error(fetched.stderr) else
                   f"release {tag} was not found on the public repository, or a local tag of that name differs")
    target = g("rev-parse", "--verify", "--quiet", tag + "^{commit}").stdout.strip()
    if not target:
        return say("failed", f"release {tag} does not name a commit")
    if busy and busy():
        return defer_update(directory, version, "this computer still has work running", clock=clock, transient=True)
    if target == old and started in ("", target):
        return say("healthy")
    if target != old:
        pending = binary_pending(root, old, target, version, run=run, listed=listed, which=which)
        if pending:
            return defer_update(directory, version, pending, clock=clock)
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
        error = install(root, started, target) or prepare(root)
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
        return say("failed", switched.stderr if switched.stderr.startswith("repository copy needs attention:") or disk_space_error(switched.stderr) else f"could not switch to {tag}: local files are in the way")
    error = install(root, old, target) or prepare(root)
    if error:
        return back_out(error)
    since = clock()
    restart and restart()
    if healthy and not healthy(target, since):
        return back_out(f"the runner did not report in within {HEALTH_S} seconds on {tag}")
    result = say("healthy")
    restarted_helpers()
    return result


@contextlib.contextmanager
def updater_lock(directory):
    """One updater per runner, including its drain and checkout-lock waits; a duplicate leaves its status alone."""
    with open_regular_lock(Path(directory) / PROCESS_LOCK) as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m runner_support.release_update")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--version", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--started", default="")     # the commit the runner started on; "" from an older runner
    args = argparse.Namespace()
    try:
        args = parser.parse_args(argv, namespace=args)
        with updater_lock(args.state_dir) as acquired:
            if not acquired:
                print("Tico update: an updater is already running for this computer", flush=True)
                return 0
            read_status(args.state_dir, strict=True)
            from . import git_locks
            # Older updaters ignore this environment setting when a shared checkout moves back.
            roots = json.loads(os.environ.get("TICO_GIT_OWNED_ROOTS", "[]"))
            if not isinstance(roots, list) or any(not isinstance(root, str) for root in roots):
                raise ValueError("TICO_GIT_OWNED_ROOTS must be a list of paths")
            git_locks.OWNED_ROOTS = (Path(args.root), Path(args.state_dir), *map(Path, roots))
            git_locks.register_checkout(args.root)
            return run_update(args)
    except BaseException as exc:
        # Every stopped attempt needs an outcome, including exits before apply can write one.
        if not getattr(args, "version", None) or not getattr(args, "state_dir", None):
            raise  # There is no runner status to write for help or an incomplete command.
        if isinstance(exc, SystemExit) and (exc.code is None or isinstance(exc.code, int)):
            cause = f"the update stopped before it finished (exit {exc.code or 0})"
        elif isinstance(exc, KeyboardInterrupt):
            cause = str(exc) or "the update was interrupted"
        else:
            cause = str(exc) or type(exc).__name__
        result = defer_update(args.state_dir, args.version, cause)
        print(json.dumps(result))
        return 1


def run_update(args):
    deadline = time.time() + 600
    while in_flight(args.state_dir) and time.time() < deadline:   # the runner waits for quiet first; this is the second look
        time.sleep(10)
    if in_flight(args.state_dir):
        result = defer_update(args.state_dir, args.version, "this computer still has work running", transient=True)
        print(json.dumps(result))
        return 1
    helpers = restart_helpers if kind() == "mac" else restart_helpers_systemd if systemd_unit() else None
    result = apply(args.root, args.version, args.state_dir, busy=lambda: in_flight(args.state_dir),
                   restart=lambda: restart_runner(args.pid),
                   healthy=lambda commit, since: wait_healthy(args.state_dir, commit or "", since),
                   helpers=helpers, started=args.started)
    print(json.dumps(result))
    return 0 if result["state"] == "healthy" else 1


if __name__ == "__main__":
    sys.exit(main())
