"""The company's Google Workspace key, where bots cannot read it (Linux Docker runner).

The service-account key acts as any mailbox in the company. Every bot on a computer runs as the
same user, so a key in workspace/secrets is one prompt injection away from all of them. When the
two-user layout is on (runner_support/isolation.py) the supervisor keeps the key in its own state directory
(owned by ticorun, 0600, closed to the bot user), the connectors job reads it there, and an inbox
bot's turn asks the supervisor, over the credential socket, for a short-lived token for one mailbox
(runner_support/credential_socket.py). Without the layout (a Mac, or Docker started the old way) the key
stays where it was and bots can read it: `exposed` says so, and Settings > Health shows a warning.
"""

import json
import os
import stat
import tempfile
from pathlib import Path

from .outage import log

FILE = "google-sa.json"
GRANTS = "google-oauth.json"  # people's own connected Google accounts (connectors/mail/oauth.py)
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
        if (
            previous.get("environment")
            and config.get("environment")
            and previous["environment"] != config["environment"]
        ):
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
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != uid
                or info.st_dev != parent.st_dev
                or info.st_mode & 0o066
            ):
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
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != uid
                or info.st_dev != parent.st_dev
                or info.st_mode & 0o077
            ):
                return False
            descriptor, temporary = tempfile.mkstemp(prefix="." + name + "-", dir=new)
            with os.fdopen(descriptor, "wb") as output:
                output.write(handle.read())
                os.fchmod(output.fileno(), stat.S_IMODE(info.st_mode))
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, new / name)  # publish a complete file without replacing an existing key
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
            log(
                f"Tico runner: could not carry the protected {name} to this registration ({type(exc).__name__}); "
                "check free space and permissions on the runner volume; the old key is unchanged"
            )
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
