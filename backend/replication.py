"""Off-volume copies of what Litestream does not carry: the attachments in /data/blobs and the local key files.

It also reports how the install is backed up. The container entrypoint decides the mode and exports it;
a loop next to the server keeps the blobs mirrored and writes the time the copies last moved to a status
file the server reads. Blobs are content-addressed and immutable, so a sync only ever adds files.

The credential key (/data/credential.key, backend/credentials.py) is what decrypts the stored credentials when no AWS KMS
key is set, and a restored database without it cannot. It is copied to the same place as everything else, and only there:
never logged, never in the status file, never in a message. The status keeps when it was copied and the file's size and
modification time, not the key or anything derived from it.

Two more key files beside it seal secrets the vault does not hold, and get the same handling: /data/github-app.key (the
GitHub App's private key, client secret and webhook secret, and the directory sync credentials; backend/github_app.py)
and /data/slack.key (the Slack tokens; backend/slack_app.py). They are copied whenever they exist, KMS or not: the Slack
gateway always opens the tokens with slack.key, and github-app.key may still seal an app saved before KMS was set.
/data/tico-session-secret (backend/oidc.py) is not copied: it signs only the few minutes of a sign-in round trip (the
sessions themselves are in the database), so a new one costs nobody their session and one less secret leaves the server.
"""

import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

STATUS_FILE = "/tmp/tico-backup-status.json"
LOCAL_WARNING = ("Backups are only on this server's disk (the tico-backups volume). Losing the server loses them "
                 "too. Set TICO_BACKUP_URL to an S3 or R2 bucket in .env.")
OFF_WARNING = "Backups are off (TICO_BACKUP=off). Losing the data volume loses everything."
# Litestream 0.5 keeps one directory per compaction level at the top of a replica, under ltx/ on a file
# replica and not on S3; ours sits beside them. Listing the levels rather than everything keeps the check
# cheap however many attachments there are.
DB_LEVELS = tuple(prefix + str(level).rjust(width, "0") for prefix, width in (("", 4), ("ltx/", 1)) for level in (0, 1, 2, 3, 9))
FILES_PREFIX = "files"
# The environment marker: a small file OUTSIDE the database that says "this is an existing company, with this id".
# One in the data volume, one beside the replica. A start with an empty volume reads them before it may begin a
# new company, so an unreachable backup can never turn into a blank company replicating over the real one.
MARKER = "environment.json"
LOCAL_MARKER = ".tico-environment"
# The local credential key and where its copy goes in the backup location (beside files/, not inside a replica level).
CREDENTIAL_KEY = "credential.key"
CREDENTIAL_KEY_COPY = "credential-key/" + CREDENTIAL_KEY
CREDENTIAL_KEY_BYTES = 32
# The other key files, copied to the same place: credential-key/<name>.
KEY_FILES = ("github-app.key", "slack.key")


def key_copy(name):
    return "credential-key/" + name


def rehearsal_on(env=None):
    """TICO_REHEARSAL=1: a server on a copy of real data that must write nothing back to it and send nothing out
    (docs/install.md, "Rehearse a migration"). One reading of the switch for the entrypoint, the server and this module."""
    env = os.environ if env is None else env
    return (env.get("TICO_REHEARSAL") or "").strip().lower() in ("1", "true", "yes", "on")


def no_mirror(env):
    """Whether nothing may be written to the off-server copy: backups are off, or this is a rehearsal."""
    return env.get("TICO_BACKUP_MODE") in ("off", "rehearsal") or rehearsal_on(env)


def target_kind(url="", endpoint=""):
    if not url:
        return "local"
    if "r2.cloudflarestorage.com" in endpoint:
        return "r2"
    return "s3-compatible" if endpoint else "s3"


def mode_from_env(env):
    """remote | local-only | off, from the same variables the entrypoint reads."""
    if (env.get("TICO_BACKUP") or "").lower() == "off":
        return "off"
    return "remote" if env.get("TICO_BACKUP_URL") else "local-only"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def credential_key_path(env):
    return Path(env.get("TICO_CREDENTIAL_KEY_FILE") or Path(env.get("TICO_DB") or "/data/hub.sqlite").parent / CREDENTIAL_KEY)


def credential_key_stamp(path):
    """Size and modification time of the key file: enough to know it changed, and nothing of the key itself."""
    try:
        info = Path(path).stat()
    except OSError:
        return ""
    return "%d:%d" % (info.st_size, info.st_mtime_ns)


def local_key_in_use(env, path=None):
    """A local credential key exists and nothing else (AWS KMS) holds the credentials' data key."""
    return not (env.get("TICO_CREDENTIAL_KMS_KEY") or "").strip() and bool(credential_key_stamp(path or credential_key_path(env)))


def key_file_path(env, name):
    return Path(env.get("TICO_DB") or "/data/hub.sqlite").parent / name


def key_files_state(env, recorded):
    """{name: {present, copied_at, current}} for the key files that exist, like credential_key_state."""
    recorded = recorded if isinstance(recorded, dict) else {}
    found = {}
    for name in KEY_FILES:
        stamp = credential_key_stamp(key_file_path(env, name))
        if not stamp:
            continue
        saved = recorded.get(name) if isinstance(recorded.get(name), dict) else {}
        copied = str(saved.get("copied_at") or "") or None
        found[name] = {"present": True, "copied_at": copied, "current": bool(copied and saved.get("stamp") == stamp)}
    return found


def credential_key_state(env, recorded):
    """{present, copied_at, current}: whether a local key exists, when it was last copied, and whether that copy is of
    the file as it is now. `recorded` is what the loop wrote to the status file."""
    present = local_key_in_use(env)
    recorded = recorded if isinstance(recorded, dict) else {}
    copied = str(recorded.get("copied_at") or "") or None
    return {"present": present, "copied_at": copied if present else None,
            "current": bool(present and copied and recorded.get("stamp") == credential_key_stamp(credential_key_path(env)))}


def _status_file(env, path):
    try:
        return json.loads(Path(path or env.get("TICO_BACKUP_STATUS_FILE") or STATUS_FILE).read_text())
    except (OSError, ValueError):
        return {}


def status(env=None, path=None):
    """{mode, last_replicated_at, target_kind, warning, credential_key} for the config payload."""
    env = os.environ if env is None else env
    if rehearsal_on(env) or env.get("TICO_BACKUP_MODE") == "rehearsal":
        return {"mode": "rehearsal", "last_replicated_at": None, "target_kind": "none", "warning": ""}
    if not env.get("TICO_BACKUP_MODE"):
        # Not started by the container entrypoint (a VM install, tests). VM stacks run Litestream from
        # their own service config, so absence of TICO_BACKUP_URL says nothing: report unknown, not off.
        if not env.get("TICO_BACKUP_URL"):
            return {"mode": None, "last_replicated_at": None, "warning": "", "target_kind": "none",
                    "credential_key": credential_key_state(env, None), "key_files": key_files_state(env, None)}
        return {"mode": "remote", "last_replicated_at": None, "warning": "",
                "target_kind": target_kind(env.get("TICO_BACKUP_URL", ""), env.get("TICO_BACKUP_ENDPOINT", "")),
                "credential_key": credential_key_state(env, None), "key_files": key_files_state(env, None)}
    mode = env["TICO_BACKUP_MODE"]
    kind = "none" if mode == "off" else target_kind(env.get("TICO_BACKUP_URL", ""), env.get("TICO_BACKUP_ENDPOINT", ""))
    saved = _status_file(env, path)
    warning = {"local-only": LOCAL_WARNING, "off": OFF_WARNING}.get(mode, "")
    return {"mode": mode, "last_replicated_at": saved.get("last_replicated_at"), "target_kind": kind, "warning": warning,
            "credential_key": credential_key_state(env, None if mode == "off" else saved.get("credential_key")),
            "key_files": key_files_state(env, None if mode == "off" else saved.get("key_files"))}


class LocalMirror:
    def __init__(self, root):
        self.root = Path(root)

    def keys(self, prefix):
        base = self.root / prefix
        return {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()} if base.is_dir() else set()

    def put(self, key, source):
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".part-", dir=target.parent)
        with os.fdopen(fd, "wb") as out, open(source, "rb") as src:
            while chunk := src.read(1 << 20):
                out.write(chunk)
        os.replace(temporary, target)

    def get(self, key, destination):
        with open(self.root / key, "rb") as src, open(destination, "wb") as out:
            while chunk := src.read(1 << 20):
                out.write(chunk)

    def exists(self, key):
        return (self.root / key).is_file()

    def newest(self, prefixes):
        times = [p.stat().st_mtime for prefix in prefixes if (self.root / prefix).is_dir()
                 for p in (self.root / prefix).rglob("*") if p.is_file()]
        return max(times) if times else None


class S3Mirror:
    def __init__(self, client, bucket, prefix):
        self.s3, self.bucket, self.prefix = client, bucket, prefix.strip("/")

    def _key(self, key):
        return (self.prefix + "/" if self.prefix else "") + key

    def _objects(self, prefix):
        base = self._key(prefix) + "/"
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=base):
            for item in page.get("Contents", []):
                yield item["Key"][len(base):], item["LastModified"]

    def keys(self, prefix):
        return {key for key, _ in self._objects(prefix)}

    def put(self, key, source):
        self.s3.upload_file(str(source), self.bucket, self._key(key))

    def get(self, key, destination):
        self.s3.download_file(self.bucket, self._key(key), str(destination))

    def exists(self, key):
        from botocore.exceptions import ClientError
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def newest(self, prefixes):
        times = [modified.timestamp() for prefix in prefixes for _, modified in self._objects(prefix)]
        return max(times) if times else None


def mirror_from_env(env=None):
    env = os.environ if env is None else env
    url = env.get("TICO_BACKUP_URL", "")
    if not url:
        return LocalMirror(env.get("TICO_BACKUP_DIR", "/backups"))
    parsed = urlparse(url)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError("TICO_BACKUP_URL must be s3://bucket/prefix")
    import boto3
    client = boto3.client("s3", endpoint_url=env.get("TICO_BACKUP_ENDPOINT") or None,
                          region_name=env.get("TICO_BACKUP_REGION") or ("us-east-1" if env.get("TICO_BACKUP_ENDPOINT") else None),
                          aws_access_key_id=env.get("LITESTREAM_ACCESS_KEY_ID") or None,
                          aws_secret_access_key=env.get("LITESTREAM_SECRET_ACCESS_KEY") or None)
    return S3Mirror(client, parsed.netloc, parsed.path)


def _blob_files(blob_dir):
    base = Path(blob_dir) / "blobs"
    if not base.is_dir():
        return {}
    return {p.relative_to(base).as_posix(): p for p in base.rglob("*")
            if p.is_file() and not p.name.startswith(".upload-")}


def sync_blobs(blob_dir, mirror):
    """Uploads the blobs the mirror lacks; returns how many."""
    have = mirror.keys(FILES_PREFIX + "/blobs")
    sent = 0
    for key, path in sorted(_blob_files(blob_dir).items()):
        if key not in have:
            mirror.put(FILES_PREFIX + "/blobs/" + key, path)
            sent += 1
    return sent


def restore_blobs(mirror, blob_dir):
    """Downloads every mirrored blob, checking each against the sha256 in its name."""
    count = 0
    for key in sorted(mirror.keys(FILES_PREFIX + "/blobs")):
        target = Path(blob_dir) / "blobs" / key
        if target.exists():
            continue
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".restore-", dir=target.parent)
        os.close(fd)
        mirror.get(FILES_PREFIX + "/blobs/" + key, temporary)
        digest = hashlib.sha256(Path(temporary).read_bytes()).hexdigest()
        if digest != key.rsplit("/", 1)[-1]:
            os.unlink(temporary)
            raise SystemExit("tico: error: backup blob " + key + " fails its checksum")
        os.replace(temporary, target)
        count += 1
    return count


def sync_credential_key(path, mirror, state, copy=CREDENTIAL_KEY_COPY):
    """Copies the local credential key to the backup location when it is new or changed; returns what the status file
    keeps ({copied_at, stamp}), or None when there is no key to copy. `state` holds the last digest copied, in memory
    only, so an unchanged key is not sent again. Nothing here prints or returns the key."""
    path = Path(path)
    stamp = credential_key_stamp(path)
    if not stamp:
        return None
    data = path.read_bytes()
    if len(data) != CREDENTIAL_KEY_BYTES:
        return None    # not a key (yet); a half-written file is never the one copy
    digest = hashlib.sha256(data).digest()
    if state.get("digest") != digest:
        fd, temporary = tempfile.mkstemp(prefix=".key-")    # mode 0600
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            _keep_replaced_copy(mirror, copy, digest)
            mirror.put(copy, temporary)
        finally:
            os.unlink(temporary)
        state["digest"] = digest
        state["copied_at"] = _now()
    # The stamp read before the file: if it changed while it was read, it no longer matches and is copied again.
    return {"copied_at": state["copied_at"], "stamp": stamp}


def _keep_replaced_copy(mirror, copy, digest):
    """A different key already in the backup may be the only one that opens what the database holds (a key file that did
    not come back with a restore, and a new one made since). It is kept as <copy>.replaced-<UTC time>, never overwritten.
    A key is never rotated in place, so this happens only after such a loss."""
    if not mirror.exists(copy):
        return
    fd, previous = tempfile.mkstemp(prefix=".key-")    # mode 0600
    os.close(fd)
    try:
        mirror.get(copy, previous)
        if hashlib.sha256(Path(previous).read_bytes()).digest() != digest:
            mirror.put(copy + ".replaced-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"), previous)
    finally:
        os.unlink(previous)


def restore_credential_key(mirror, path, replace=False, copy=CREDENTIAL_KEY_COPY):
    """Puts the backed-up credential key back at `path`. Returns "restored", "kept" (a key is already there and
    `replace` is not set, or it is the same key) or "missing" (the backup holds none). A different key already in
    place is moved aside, never overwritten. The copy must be 32 bytes, and is written with mode 0600."""
    path = Path(path)
    if not mirror.exists(copy):
        return "missing"
    if path.exists() and not replace:
        return "kept"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".restore-key-", dir=path.parent)
    os.close(fd)
    try:
        mirror.get(copy, temporary)
        data = Path(temporary).read_bytes()
        if len(data) != CREDENTIAL_KEY_BYTES:
            raise SystemExit("tico: error: " + copy + " in the backup is not a valid key")
        if path.exists():
            if path.read_bytes() == data:
                return "kept"
            path.rename(path.with_name(path.name + ".before-restore." + str(int(time.time()))))
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return "restored"


def restore_key_files(env, replace=False, mirror=None):
    """credential.key, github-app.key and slack.key back beside the restored database. A backup made before the last two
    were copied has neither: that is said, not an error (Health then names what cannot be opened). Each file is tried
    even when another fails; the exit status says whether any did."""
    mirror = mirror or mirror_from_env(env)
    failed = 0
    said = {CREDENTIAL_KEY: ("the credential key", "none was ever made, or AWS KMS holds the data key")}
    for name, path, copy in [(CREDENTIAL_KEY, credential_key_path(env), CREDENTIAL_KEY_COPY)] + \
            [(name, key_file_path(env, name), key_copy(name)) for name in KEY_FILES]:
        what, why = said.get(name, (name, "nothing used it, or the backup predates copying it"))
        try:
            result = restore_credential_key(mirror, path, replace, copy)
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            failed += 1
            continue
        print({"restored": "tico: restored " + what,
               "kept": "tico: " + what + " is already on the data volume; left as it is",
               "missing": "tico: the backup holds no " + what + " (" + why + ")"}[result])
    return 1 if failed else 0


def is_empty(data_dir):
    """True when nothing of an install is in the data volume."""
    root = Path(data_dir)
    return not root.is_dir() or not any(root.iterdir())


def _marker_text(environment_id):
    return json.dumps({"environment_id": environment_id, "written_at": _now()}) + "\n"


def write_markers(data_dir, environment_id, mirror=None):
    """Records that this install is an existing company. The volume marker is required; the backup one is best effort
    (a warning, because a bucket that is down must not stop a running company from starting)."""
    path = Path(data_dir) / LOCAL_MARKER
    path.write_text(_marker_text(environment_id))
    if mirror is not None:
        fd, temporary = tempfile.mkstemp(prefix=".marker-")
        try:
            with os.fdopen(fd, "w") as out:
                out.write(_marker_text(environment_id))
            mirror.put(MARKER, temporary)
        except Exception as exc:
            print("tico: warning: could not write the environment marker to the backup location: "
                  + type(exc).__name__ + ": " + str(exc)[:200], file=sys.stderr, flush=True)
        finally:
            os.unlink(temporary)


def check_new_company(data_dir, mirror, restore_failed=False, initialize_empty=False):
    """Run when the database is missing after the restore attempt. Returns None when starting a new company is
    safe, else the message to refuse with. Safe means: nothing anywhere says a company already exists, and the
    backup location could be read to find that out."""
    have_local = (Path(data_dir) / LOCAL_MARKER).exists()
    problem = None
    where = "the backup location"
    if mirror is None:
        found = False
    else:
        try:
            found = mirror.exists(MARKER) or mirror.newest(DB_LEVELS) is not None
        except Exception as exc:
            found, problem = False, "could not read " + where + " (" + type(exc).__name__ + ": " + str(exc)[:160] + ")"
    if initialize_empty:
        if found or have_local:
            print("tico: WARNING: TICO_INITIALIZE_EMPTY is set: starting a NEW company although an existing one was "
                  "found. Its backup will be overwritten.", file=sys.stderr, flush=True)
        return None
    if found or have_local:
        return ("an existing company was found in " + ("the backup location" if found else "this data volume's marker")
                + " but the database could not be restored" + (" (the restore command failed)" if restore_failed else "")
                + ". Starting would create a blank company and replicate it over the real backup. Fix the backup "
                "settings (TICO_BACKUP_URL, keys, network) and start again, or run `docker compose run --rm server "
                "restore`. To knowingly start a new company anyway, set TICO_INITIALIZE_EMPTY=1.")
    if problem or restore_failed:
        return ((problem or "the restore command failed") + ", so it is unknown whether a company already lives there. "
                "Refusing to start a blank one over it. Fix the backup settings and start again; for a genuinely new "
                "install with an unreachable bucket, set TICO_INITIALIZE_EMPTY=1.")
    return None


def write_status(mirror, path, credential_key=None, key_files=None):
    """The time the database copy last moved, and when the key files were last copied. They are kept apart: a
    round where one fails must not wipe what the others last did."""
    saved = _status_file({}, path)
    newest = mirror.newest(DB_LEVELS) if mirror is not None else None
    if newest is None and credential_key is None and not key_files:
        return
    text = datetime.fromtimestamp(newest, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if newest is not None \
        else saved.get("last_replicated_at")
    data = {"last_replicated_at": text, "checked_at": _now()}
    if credential_key is not None or saved.get("credential_key"):
        data["credential_key"] = credential_key if credential_key is not None else saved["credential_key"]
    kept = {**(saved.get("key_files") if isinstance(saved.get("key_files"), dict) else {}), **(key_files or {})}
    if kept:
        data["key_files"] = kept
    fd, temporary = tempfile.mkstemp(dir=Path(path).parent)
    with os.fdopen(fd, "w") as out:
        json.dump(data, out)
    os.replace(temporary, path)


def loop(env=None, interval=30, warn_every=3600, sleep=time.sleep, rounds=None):
    env = os.environ if env is None else env
    if no_mirror(env):
        print("tico: rehearsal: nothing is copied to the backup location", flush=True)
        return
    mirror = mirror_from_env(env)
    blob_dir = env.get("TICO_BLOB_DIR", "/data/blobs")
    path = env.get("TICO_BACKUP_STATUS_FILE") or STATUS_FILE
    warning = status(env, path)["warning"]
    last_warned, done = 0.0, 0
    key_path, key_state, copied = credential_key_path(env), {}, None
    file_states, files_copied = {name: {} for name in KEY_FILES}, {}
    while rounds is None or done < rounds:
        if warning and time.monotonic() - last_warned >= warn_every:
            print("tico: WARNING: " + warning, flush=True)
            last_warned = time.monotonic()
        try:
            if not env.get("TICO_BLOB_BUCKET"):
                sync_blobs(blob_dir, mirror)
        except Exception as exc:  # keep trying: the next round may find the bucket back
            print("tico: warning: backup sync failed: " + type(exc).__name__ + ": " + str(exc)[:200], flush=True)
        try:
            if local_key_in_use(env, key_path):
                copied = sync_credential_key(key_path, mirror, key_state)
        except Exception as exc:
            # The type only: nothing about the key, or what an error might quote of it, goes in a log.
            print("tico: warning: could not copy the credential key to the backup location: " + type(exc).__name__, flush=True)
        for name in KEY_FILES:
            try:
                done_copy = sync_credential_key(key_file_path(env, name), mirror, file_states[name], key_copy(name))
                if done_copy is not None:
                    files_copied[name] = done_copy
            except Exception as exc:
                print("tico: warning: could not copy " + name + " to the backup location: " + type(exc).__name__, flush=True)
        try:
            write_status(mirror, path, copied, files_copied)
        except Exception as exc:
            print("tico: warning: backup status failed: " + type(exc).__name__ + ": " + str(exc)[:200], flush=True)
        done += 1
        sleep(interval)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    env = os.environ
    if command == "loop":
        loop(env)
    elif command == "restore-blobs":
        n = restore_blobs(mirror_from_env(env), env.get("TICO_BLOB_DIR", "/data/blobs"))
        print("tico: restored %d attachment file(s)" % n)
    elif command == "restore-credential-key":   # [--replace]: every key file is put back beside the restored database
        return restore_key_files(env, "--replace" in argv)
    elif command == "is-empty":
        return 0 if is_empty(argv[1]) else 1
    elif command == "check-new-company":   # DIR [--restore-failed] [--initialize-empty]; backups off: no mirror
        mirror = None if env.get("TICO_BACKUP_MODE") == "off" else mirror_from_env(env)   # a rehearsal still reads it
        message = check_new_company(argv[1], mirror, "--restore-failed" in argv, "--initialize-empty" in argv)
        if message:
            print("tico: error: " + message, file=sys.stderr)
            return 1
    elif command == "mark-environment":    # DIR ID
        write_markers(argv[1], argv[2], None if no_mirror(env) else mirror_from_env(env))
    else:
        print("usage: python -m backend.replication loop|restore-blobs|restore-credential-key|is-empty DIR|check-new-company DIR|mark-environment DIR ID", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
