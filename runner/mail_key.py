"""The company's Google Workspace key, where bots cannot read it (Linux Docker runner).

The service-account key acts as any mailbox in the company. Every bot on a computer runs as the
same user, so a key in workspace/secrets is one prompt injection away from all of them. When the
two-user layout is on (runner/isolation.py) the supervisor keeps the key in its own state directory
(owned by ticorun, 0600, closed to the bot user), the connectors job reads it there, and an inbox
bot's turn asks the supervisor, over the credential socket, for a short-lived token for one mailbox
(runner/credential_socket.py). Without the layout (a Mac, or Docker started the old way) the key
stays where it was and bots can read it: `exposed` says so, and Settings > Health shows a warning.
"""
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

from . import isolation
from .outage import log

FILE = "google-sa.json"
GRANTS = "google-oauth.json"        # people's own connected Google accounts (connectors/mail/oauth.py)
ROOT = Path(__file__).resolve().parents[1]


CARRY_MARKERS = {FILE: ".mail-key-carried", GRANTS: ".google-oauth-carried"}


def carry_protected(config, config_path):
    """Carry the key and the connected-account grants to a new registration (_carry_one, once each)."""
    carried = [_carry_one(config, config_path, name, marker) for name, marker in CARRY_MARKERS.items()]
    return any(carried)


def _carry_one(config, config_path, name, marker_name):
    """Recover the mail key after re-enrollment, only from this operator's previous registration.

    The container retains runner.json.stale when replacing a rejected registration. Never scan
    unrelated state directories: their ownership alone does not identify the Team operator.
    Leave the old key intact for rollback; never replace a key already in the new registration.
    Record a copy, an existing key or a confirmed absent source once per new registration, so
    deleting the key later is respected on restart. Failed reads or copies remain retryable.
    """
    config_path = Path(config_path)
    stale = config_path.with_name(config_path.name + ".stale")
    temporary = None
    try:
        uid = os.getuid()
        parent = config_path.parent.stat()
        info = stale.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_dev != parent.st_dev or info.st_mode & 0o077:
            return False
        previous = json.loads(stale.read_text())
        if not isinstance(previous, dict):
            return False
        if not config.get("operator") or any(previous.get(k) != config.get(k) for k in ("operator", "url")):
            return False
        if previous.get("environment") and config.get("environment") and previous["environment"] != config["environment"]:
            return False
        old_id = str(previous.get("runner_id") or "")
        new_id = str(config.get("runner_id") or "")
        if not old_id or not new_id or any("/" in ident or ident in (".", "..") for ident in (old_id, new_id)):
            return False
        old = config_path.parent / ("state-" + old_id)
        new = config_path.parent / ("state-" + new_id)
        new.mkdir(mode=0o700, exist_ok=True)

        def safe_directory(directory):
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_dev != parent.st_dev or info.st_mode & 0o066:
                return False
            return True

        if not safe_directory(new):
            return False
        marker = new / marker_name
        if marker.exists() or marker.is_symlink():
            return False

        def carried():
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)

        if (new / name).exists() or (new / name).is_symlink():
            carried()
            return False
        try:
            if not safe_directory(old):
                return False
            source = old / name
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            carried()
            return False
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_dev != parent.st_dev or info.st_mode & 0o077:
                return False
            descriptor, temporary = tempfile.mkstemp(prefix="." + name + "-", dir=new)
            with os.fdopen(descriptor, "wb") as output:
                output.write(handle.read())
                os.fchmod(output.fileno(), stat.S_IMODE(info.st_mode))
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, new / name)     # publish a complete file without replacing an existing key
            except FileExistsError:
                carried()
                return False
        carried()
        return True
    except FileExistsError:
        return False
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError) as exc:
        if stale.exists():
            log(f"Tico runner: could not carry the protected {name} to this registration ({type(exc).__name__}); "
                "check free space and permissions on the runner volume; the old key is unchanged")
        return False
    finally:
        if temporary is not None:
            os.unlink(temporary)


def protected_path(config):
    """Where the key lives when isolation is on: the supervisor's state directory."""
    home = Path(os.environ.get("HOME") or "/home/runner")
    return Path(config.get("state_dir") or home / ("state-" + str(config.get("runner_id") or ""))) / FILE


def workspace_path(config):
    return Path(config.get("projects_dir") or "") / "secrets" / FILE


def protect(config):
    """Move a key found in the workspace (put there by an older install, or by an operator following
    an older doc) to the protected place. One time in practice; cheap, so it runs on every check.
    Returns True when it moved one. A no-op wherever isolation is off."""
    if not isolation.enabled() or os.environ.get("GOOGLE_SA_KEY"):
        return False
    moved = False
    for name in (FILE, GRANTS):
        old, new = workspace_path(config).with_name(name), protected_path(config).with_name(name)
        try:
            if old.is_symlink() or not old.is_file():
                continue
            data = old.read_bytes()
            if name == GRANTS and new.is_file():
                data = _merged_grants(new, data)
            new.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = new.with_name(new.name + ".new")
            temporary.unlink(missing_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            os.replace(temporary, new)
            old.unlink()
        except (OSError, ValueError) as exc:     # ValueError: a workspace grants file that is not JSON; both stay
            log(f"Tico runner: could not move {name} out of the bots' reach ({type(exc).__name__})")
            continue
        log(f"Tico runner: moved {name} out of workspace/secrets to {new}; bots ask the runner for mail access")
        moved = True
    return moved


def _merged_grants(protected, incoming):
    """The protected grants with the workspace's merged in, so a move never drops a grant already protected."""
    from connectors.mail.oauth import merge_into
    try:
        kept = json.loads(protected.read_text())
    except ValueError:
        kept = {}
    found = json.loads(incoming)
    if not isinstance(found, dict):
        raise ValueError("the workspace grants file is not a JSON object")
    return json.dumps(merge_into(kept if isinstance(kept, dict) else {}, found), indent=2, sort_keys=True).encode()


def status(config):
    """`protected`, `exposed` (a key bots can read) or None (no key on this computer)."""
    if isolation.enabled():
        if any(workspace_path(config).with_name(n).is_file() for n in (FILE, GRANTS)):
            return "exposed"
        return "protected" if any(protected_path(config).with_name(n).is_file() for n in (FILE, GRANTS)) else None
    from .connectors import mail_credential_present
    return "exposed" if mail_credential_present(config) else None


def held_by_computer(config):
    """True when this computer holds the Google key or a connected account's grant for its bots: the two-user layout is on, the key is in
    the supervisor's state directory and the connectors job runs here (the runner supervises it when
    TICO_SIDE_JOBS is 1, the container default). A bot's turn never has GOOGLE_SA_KEY in its environment
    then; it asks for a short-lived token over the credential socket, so the key is there, not missing."""
    if not isolation.enabled() or os.environ.get("TICO_SIDE_JOBS") != "1":
        return False
    from .connectors import mail_credential_present
    return mail_credential_present(config)


def minter(config, script=None):
    """(service, mailbox) -> {"token", "expiry"}: the mail CLI, run here with the key, prints one token."""
    from .connectors import mail_cli_env, mail_umask
    script = script or ROOT / "scripts" / "mail.sh"
    key = protected_path(config)

    def mint(service, mailbox):
        env = mail_cli_env(config, key)
        env["GOOGLE_SA_KEY"] = str(key)
        result = subprocess.run([str(script), "mint-token", "--mailbox", mailbox, "--service", service],
                                cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=60, env=env, **mail_umask())
        if result.returncode:
            raise RuntimeError("mint-token failed")
        return json.loads(result.stdout)
    return mint
