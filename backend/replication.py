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
and /data/slack.key (the Slack tokens; backend/slack_app.py). They are copied whenever they exist, KMS or not: either may
still seal secrets saved before KMS was set, which open with it until their first read re-seals them with the vault's key.
/data/tico-session-secret (backend/oidc.py) is not copied: it signs only the few minutes of a sign-in round trip (the
sessions themselves are in the database), so a new one costs nobody their session and one less secret leaves the server.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
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
DB_LEVEL_NUMBERS = (0, 1, 2, 3, 9)


def db_levels(generation=""):
    """The folders that hold the database's Litestream levels: the root's, or those of a backup generation."""
    base = generation + "/" if generation else ""
    return (tuple(base + str(n).rjust(4, "0") for n in DB_LEVEL_NUMBERS)
            + tuple(base + "ltx/" + str(n) for n in DB_LEVEL_NUMBERS))


DB_LEVELS = db_levels()
# Lag is about transaction IDs, never clocks: the backup location is behind when the local level-0 files hold a TXID
# it lacks. A warning waits this long (or ten sync intervals, when longer) and a bad one waits an hour; a quiet
# database has no new TXIDs, so it is never behind however old its last copy is.
BEHIND_WARN_MIN_S = 600
BEHIND_BAD_MIN_S = 3600
NO_RECENT_COPY = "Litestream has not sent any copy of the database to the backup location."
SYNC_INTERVAL_DEFAULT = 60
# The replica fields write_status keeps when it has no mirror to read, and drops when the generation changes.
REPLICA_FIELDS = ("last_replicated_at", "reason", "replica_txid", "behind_since", "pending_txid")
_LTX_NAME = re.compile(r"([0-9a-f]{16})-([0-9a-f]{16})\.ltx")
_SYNC_UNITS = {"s": 1, "m": 60, "h": 3600}
FILES_PREFIX = "files"
# An unreachable bucket makes Litestream retry for minutes; a listing that long is treated as unreadable.
LISTING_TIMEOUT = 60
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
# Backup generations. The replica may keep the database's Litestream levels in a folder named for when a new generation was
# started, gen-<UTC yyyymmddThhmmssZ>/, and environment.json at the root names it. No field there means the root, as every
# replica had before. files/ and credential-key/ stay at the root for every generation.
GENERATION_FIELD = "generation"
GENERATION_PATTERN = re.compile(r"gen-\d{8}T\d{6}Z")
GENERATION_PROBE = ".tico-generation"
# The generation the data is in, in the data volume beside the database: "" for the root, else the folder's name. It is what
# a start reads (no network), kept equal to environment.json by the reset job, which runs before Litestream does.
GENERATION_CACHE = ".tico-backup-generation"
URL_LINE = re.compile(r"^([ \t]+)url: .*$", re.M)
GENERATION_SUFFIX = re.compile("/" + GENERATION_PATTERN.pattern + "$")
# The heal. A copy is stuck when its local state holds more L0 copies than a healthy install keeps (about 200) and the newest
# of them is more than heal_lag_seconds() ahead of the replica. The replication loop asks for a heal once the replica has not
# moved for heal_after_seconds() while the database kept changing, and stops Litestream so the container restarts. The limits
# are these minimums, raised with the sync interval: an install that uploads once an hour is not stuck for being quiet between
# uploads. At most one heal per HEAL_INTERVAL. HEAL_FLAG, in the data volume, holds the request and when the last heal began.
LOCAL_STUCK_FILES = 5000
HEAL_LAG = 30 * 60
HEAL_AFTER = 20 * 60
HEAL_INTERVAL = 6 * 3600
HEAL_FLAG = ".tico-backup-heal"


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


def sync_interval_seconds(env=None):
    """TICO_BACKUP_SYNC_INTERVAL in seconds, read the way docker/entrypoint.sh reads it: a number and s, m or h, from 1s
    to 1h, and 1m when unset. The entrypoint refuses any other value, so the fallback only matters outside a container."""
    env = os.environ if env is None else env
    found = re.fullmatch(r"([0-9]{1,4})([smh])", env.get("TICO_BACKUP_SYNC_INTERVAL") or "1m")
    seconds = int(found.group(1)) * _SYNC_UNITS[found.group(2)] if found else 0
    return seconds if 1 <= seconds <= 3600 else SYNC_INTERVAL_DEFAULT


def behind_warn_seconds(env=None):
    """How long the backup location may stay behind before it is a warning: ten sync intervals, at least ten minutes."""
    return max(BEHIND_WARN_MIN_S, 10 * sync_interval_seconds(env))


def stuck_file_limit(env=None):
    """More local L0 copies than this is a stuck copy. Litestream writes at most about one L0 file a second, so three sync
    intervals of writes is what a healthy install can hold between uploads: at least LOCAL_STUCK_FILES."""
    return max(LOCAL_STUCK_FILES, 3 * sync_interval_seconds(env))


def heal_after_seconds(env=None):
    """How long the replica may stay still while the database changes before it is healed: two sync intervals, and at
    least HEAL_AFTER."""
    return max(HEAL_AFTER, 2 * sync_interval_seconds(env))


def heal_lag_seconds(env=None):
    """How far the newest local copy may run ahead of the replica before it counts as stuck: two sync intervals, and at
    least HEAL_LAG."""
    return max(HEAL_LAG, 2 * sync_interval_seconds(env))


def _stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now():
    return _stamp(time.time())


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
    """{mode, last_replicated_at, reason, behind_since, checked_at, unsent_files, target_kind, warning, credential_key,
    key_files} for the config payload. `reason` says why there is no copy at all; `behind_since` is when the backup location
    fell behind the database (null when caught up); `checked_at` is when the loop last read the backup location, and
    `unsent_files` is the local level-0 pile-up (local_level_zero())."""
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
    return {"mode": mode, "last_replicated_at": saved.get("last_replicated_at"), "reason": saved.get("reason"),
            "behind_since": saved.get("behind_since"), "checked_at": saved.get("checked_at"),
            "unsent_files": saved.get("unsent_files"), "target_kind": kind,
            "warning": warning,
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

    def _files(self, prefix):
        """(file name, modified time) for every file under `prefix`."""
        base = self.root / prefix
        return [(p.name, p.stat().st_mtime) for p in base.rglob("*") if p.is_file()] if base.is_dir() else []

    def latest(self, prefixes, replica=""):
        """(newest copy time, highest end TXID) of the first of `prefixes` (Litestream levels, tried in order) that has a
        file; `replica` is the folder of the replica under the backup location, "" the root. See _first_level."""
        return _first_level(self._files(_level_path(replica, prefix)) for prefix in prefixes)

    def newest(self, prefixes, replica=""):
        return self.latest(prefixes, replica)[0]


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

    def _files(self, prefix):
        return [(key.rsplit("/", 1)[-1], modified.timestamp()) for key, modified in self._objects(prefix)]

    def latest(self, prefixes, replica=""):
        """As LocalMirror.latest, from the listing: names and dates only, nothing downloaded."""
        return _first_level(self._files(_level_path(replica, prefix)) for prefix in prefixes)

    def newest(self, prefixes, replica=""):
        return self.latest(prefixes, replica)[0]


def _level_path(replica, level):
    """`level` inside the replica folder `replica` ("" is the backup location's root)."""
    return "/".join(part for part in (replica.strip("/"), level) if part)


def ltx_range(name):
    """(lowest, highest) TXID of a Litestream LTX file, from its name `<min>-<max>.ltx` (16 hex digits each); None for
    any other name."""
    found = _LTX_NAME.fullmatch(name)
    return (int(found.group(1), 16), int(found.group(2), 16)) if found else None


def max_txid(names):
    """The highest end TXID among the LTX file names given; None when none of them is one."""
    return max((found[1] for found in map(ltx_range, names) if found), default=None)


def _first_level(levels):
    """(newest time, highest end TXID) of the first level that has any file, the levels given in the order to try them.
    Litestream writes level 0 first and compacts upward, and compaction keeps the highest TXID, so the first non-empty
    level is where the replica is. The levels after it are not listed: the higher ones keep the 30-day history (tens of
    thousands of files) that a status check run every 30 seconds must not list each time."""
    for files in levels:
        if files:
            return max(modified for _, modified in files), max_txid(name for name, _ in files)
    return None, None


def mirror_from_env(env=None):
    env = os.environ if env is None else env
    url = env.get("TICO_BACKUP_URL", "")
    if not url:
        return LocalMirror(env.get("TICO_BACKUP_DIR", "/backups"))
    parsed = urlparse(url)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError("TICO_BACKUP_URL must be s3://bucket/prefix")
    import boto3
    from botocore.config import Config
    # The starts and seeds wait on this client, so an unreachable bucket fails in seconds, not the SDK's default minutes.
    timeouts = Config(connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 2})
    client = boto3.client("s3", endpoint_url=env.get("TICO_BACKUP_ENDPOINT") or None, config=timeouts,
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
    """True when nothing of an install is in the data volume. The generation cache alone does not make it an install."""
    root = Path(data_dir)
    return not root.is_dir() or not any(p.name != GENERATION_CACHE for p in root.iterdir())


def _marker_text(environment_id):
    return json.dumps({"environment_id": environment_id, "written_at": _now()}) + "\n"


def read_marker(mirror):
    """environment.json at the backup location as a dict: {} when there is none. Raises when it cannot be read or is not an
    object, so nothing is written over a marker that was not read."""
    if not mirror.exists(MARKER):
        return {}
    fd, temporary = tempfile.mkstemp(prefix=".marker-read-")
    os.close(fd)
    try:
        mirror.get(MARKER, temporary)
        data = json.loads(Path(temporary).read_text())
    finally:
        os.unlink(temporary)
    if not isinstance(data, dict):
        raise ValueError("environment.json is not an object")
    return data


def replica_generation(mirror):
    """The backup generation the database's data is in: "" for the root layout (no field, or no environment.json). Raises
    when environment.json cannot be read or names a generation this version does not know, so no caller guesses a layout."""
    value = str(read_marker(mirror).get(GENERATION_FIELD) or "")
    if value and not GENERATION_PATTERN.fullmatch(value):
        raise UnsupportedGeneration("environment.json names a backup generation this version does not know")
    return value


def cache_path(data_dir):
    return Path(data_dir) / GENERATION_CACHE


def read_generation_cache(data_dir):
    """The generation the data volume's cache names: None when there is no cache, "" for the root layout, else the folder's
    name. Raises when the file cannot be read or does not name a generation."""
    try:
        text = cache_path(data_dir).read_text().strip()
    except FileNotFoundError:
        return None
    if text and not GENERATION_PATTERN.fullmatch(text):
        raise ValueError("the file does not name a backup generation")
    return text


def write_generation_cache(data_dir, generation):
    _write_file(cache_path(data_dir), generation + "\n")


def _has_database(db):
    try:
        return os.path.getsize(db) > 0
    except OSError:
        return False


def sync_generation(config, db, env=None, strict=False, write_cache=True):
    """Makes the Litestream config and the data volume's cache name the backup generation that environment.json names. This
    is the start's one read of environment.json: backup_configuration reads only the cache, so a slow bucket cannot hold up
    the start, and the reset job (run before Litestream starts) corrects the config here.

    Returns what it went on: "none" (no backup location); "read" (environment.json says which generation); "kept" (it lacks
    the generation while the cache names one, or it could not be read and the cache names one: reading a marker never moves
    a copy off its generation); or "root" (it could not be read, there is no cache, and the database exists). The root case
    is for a database from before the cache: the cache and the database share the data volume, so a database with no cache
    has no generation to lose.

    strict is for a restore and a seed, which must not restore from a guess: environment.json has to be readable, and an
    unreadable one raises GenerationUnknown with no use of the cache or the root. A generation this version does not know
    raises in both modes. Raises GenerationUnknown when nothing says which generation the data is in, or when the config
    names another location than the backup settings do: the config is then left as it is. A rehearsal reads environment.json
    the same way: nothing is written to the backup location, and the config and cache are local. write_cache=False leaves the
    cache to the caller, which writes it once the local state is dealt with (reset_stale_litestream)."""
    env = os.environ if env is None else env
    if mode_from_env(env) == "off" or env.get("TICO_BACKUP_MODE") == "off":
        return "none"    # no backup location, so backup_configuration wrote no config to read
    data_dir = Path(db).parent
    try:
        cached = read_generation_cache(data_dir)
    except (OSError, ValueError) as exc:
        raise GenerationUnknown("the backup generation file cannot be used (" + str(exc)[:160] + ")") from None
    root = replica_root_url(env)
    try:
        named = config_root(config)
    except (OSError, ValueError) as exc:
        raise GenerationUnknown("the Litestream config could not be read (" + str(exc)[:160] + ")") from None
    if named != root:    # one location for both: a reset never re-targets the replica to the wrong folder
        raise GenerationUnknown("the Litestream config names " + named + " but the backup settings name " + root
                                + "; the config is left as it is")
    try:
        generation, outcome = replica_generation(mirror_from_env(env)), "read"
    except UnsupportedGeneration as exc:
        raise GenerationUnknown(str(exc)) from None
    except Exception as exc:
        reason = type(exc).__name__ + ": " + str(exc)[:160]
        if strict:
            raise GenerationUnknown("environment.json could not be read (" + reason + "), so the backup generation the data is "
                                    "in is not known") from None
        if cached is not None:
            generation, outcome = cached, "kept"
            what = "keeping the backup generation the data volume names (" + (generation or "the root") + ")"
        elif _has_database(db):
            generation, outcome = "", "root"
            what = "this database has no backup generation file, so the root of the backup location is used"
        else:
            raise GenerationUnknown("environment.json could not be read (" + reason + ") and nothing on this volume says which "
                                    "backup generation the data is in") from None
        print("tico: warning: could not read environment.json (" + reason + "); " + what, file=sys.stderr, flush=True)
    if outcome == "read" and not generation and cached:
        if strict:
            raise GenerationUnknown("environment.json names the root of the backup location, but the data volume's cache "
                                    "names " + cached + ", so the backup generation the data is in is not known")
        generation, outcome = cached, "kept"
    try:
        point_config_at(config, root + ("/" + generation if generation else ""))
    except Exception as exc:
        raise GenerationUnknown("the Litestream config could not name the backup generation (" + type(exc).__name__ + ")") from None
    if write_cache and outcome == "read" and cached != generation:
        try:
            write_generation_cache(data_dir, generation)
        except OSError as exc:
            raise GenerationUnknown("the backup generation file could not be written (" + str(exc)[:160] + ")") from None
    return outcome


def _put_text(mirror, key, text):
    fd, temporary = tempfile.mkstemp(prefix=".marker-")
    try:
        with os.fdopen(fd, "w") as out:
            out.write(text)
        mirror.put(key, temporary)
    finally:
        os.unlink(temporary)


def write_markers(data_dir, environment_id, mirror=None):
    """Records that this install is an existing company. The volume marker is required; the backup one is best effort
    (a warning, because a bucket that is down must not stop a running company from starting). The backup's marker keeps
    its other fields, the generation it names among them, and is not written at all when it cannot be read."""
    path = Path(data_dir) / LOCAL_MARKER
    path.write_text(_marker_text(environment_id))
    if mirror is not None:
        try:
            marker = {**read_marker(mirror), "environment_id": environment_id, "written_at": _now()}
            _put_text(mirror, MARKER, json.dumps(marker) + "\n")
        except Exception as exc:
            print("tico: warning: could not write the environment marker to the backup location: "
                  + type(exc).__name__ + ": " + str(exc)[:200], file=sys.stderr, flush=True)


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
            cached = read_generation_cache(data_dir) or ""    # the generation a start already synced, if it is not the root
            found = mirror.exists(MARKER) or any(mirror.newest(db_levels(g)) is not None
                                                 for g in {replica_generation(mirror), cached})
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


def _txid(text):
    """The TXID a status file holds in hex; None when there is none or it cannot be read."""
    try:
        return int(text, 16) if text else None
    except (TypeError, ValueError):
        return None


def _next_lag(saved, local_txid, replica_txid, now):
    """(behind_since, pending_txid) after this round. Behind means the local level-0 files hold a TXID the replica lacks.
    An interval starts when the backup location falls behind and is timed against `pending_txid`, the local TXID the
    replica must reach. A busy install is behind at nearly every round (Litestream writes local TXIDs every second and
    uploads once per sync interval), so once the replica reaches the pending TXID that interval is retired and a new one
    starts if it is still behind. Real lag then reads as at most a sync interval plus a round, while a replica that stops
    advancing keeps its first time."""
    if local_txid is None or (replica_txid is not None and local_txid <= replica_txid):
        return None, None
    since, pending = saved.get("behind_since"), _txid(saved.get("pending_txid"))
    if since and pending is not None and not (replica_txid is not None and replica_txid >= pending):
        return since, pending
    return _stamp(now), local_txid


def write_status(mirror, path, credential_key=None, key_files=None, unsent=None, local_txid=None,
                 warn_after=BEHIND_WARN_MIN_S, levels=DB_LEVELS, generation="", now=None):
    """The newest database copy, whether the backup location is behind the database, when the loop last checked it, and
    when the key files were last copied. They are kept apart: a round where one fails must not wipe what the others last
    did. `levels` are the folders of the current backup generation (`generation`, "" for the root).

    Lag is judged by TXIDs (_next_lag). `last_replicated_at` is the newest copy of the first replica level that has one,
    so an idle replica still shows a time; it is null, with `reason`, only when the replica holds no database file at all.
    When the listing fails, the replica fields are kept, the error type is recorded as `listing_error`, the local pile-up is
    still written, and `checked_at` is left where it was, so health sees the status age. Without a mirror (backups off) the
    replica fields are carried over, not read. `warn_after` is the warning time, for the cell stats to judge by."""
    now = time.time() if now is None else now
    saved = _status_file({}, path)
    if (saved.get(GENERATION_FIELD) or "") != generation:
        # Written for another backup generation: a reset may have moved the data to a new folder, so none of its lag or
        # copy times apply here and they are dropped before anything is compared.
        saved = {key: value for key, value in saved.items() if key not in REPLICA_FIELDS}
    data = {"unsent_files": unsent, "warn_after_s": warn_after, GENERATION_FIELD: generation,
            "local_txid": _hex_txid(local_txid), "checked_at": _stamp(now)}
    if mirror is None:
        data.update({key: saved[key] for key in REPLICA_FIELDS if key in saved})
    else:
        try:
            newest, replica_txid = mirror.latest(levels)
        except Exception as exc:    # unreachable or unreadable: the type only, nothing a message might quote
            data.update({key: saved[key] for key in REPLICA_FIELDS if key in saved})
            data["listing_error"] = type(exc).__name__
            if saved.get("checked_at"):
                data["checked_at"] = saved["checked_at"]
            else:
                del data["checked_at"]
        else:
            since, pending = _next_lag(saved, local_txid, replica_txid, now)
            data.update({"last_replicated_at": _stamp(newest) if newest is not None else None,
                         "replica_txid": _hex_txid(replica_txid), "behind_since": since,
                         "pending_txid": _hex_txid(pending)})
            if newest is None:
                data["reason"] = NO_RECENT_COPY
    if credential_key is not None or saved.get("credential_key"):
        data["credential_key"] = credential_key if credential_key is not None else saved["credential_key"]
    kept = {**(saved.get("key_files") if isinstance(saved.get("key_files"), dict) else {}), **(key_files or {})}
    if kept:
        data["key_files"] = kept
    fd, temporary = tempfile.mkstemp(dir=Path(path).parent)
    with os.fdopen(fd, "w") as out:
        json.dump(data, out)
    os.replace(temporary, path)


def litestream_state_dir(db):
    """Where Litestream 0.5 keeps its local copy of what it has shipped: `.<name>-litestream` beside the database."""
    db = Path(db)
    return db.parent / ("." + db.name + "-litestream")


def _hex_txid(txid):
    return None if txid is None else "%016x" % txid


def local_level_zero(db):
    """What Litestream keeps in its local level-0 folder beside the database, from the file names only and in one
    listing: (how many LTX files, the highest end TXID among them). The count is None when the folder cannot be read,
    and the TXID is None when there is no such file. Uploads that stop getting through let the files pile up."""
    try:
        with os.scandir(litestream_state_dir(db) / "ltx" / "0") as entries:
            names = [entry.name for entry in entries if entry.name.endswith(".ltx")]
    except FileNotFoundError:
        return 0, None
    except OSError:
        return None, None
    return len(names), max_txid(names)


def replica_listing(config, db, binary="litestream", timeout=LISTING_TIMEOUT):
    """The LTX files the replica holds at every level (snapshots included), as `litestream ltx -json` lists them: dicts with
    level, min_txid, max_txid, size and timestamp. None when it cannot be read: a failed or timed-out listing, or output that
    is not a list. An empty replica lists as []."""
    try:
        result = subprocess.run([binary, "ltx", "-level", "all", "-json", "-config", config, db],
                                capture_output=True, text=True, timeout=timeout, check=False)
        listed = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return listed if isinstance(listed, list) else None


def _utc_text(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def replica_newest_copy(listing):
    """When the newest L0 or L1 file in a listing was written, or None when there is none. Raises ValueError when such a file
    has no readable time, so the caller can say the time is unknown rather than guess it."""
    try:
        times = [_parse_utc(item["timestamp"]) for item in listing if isinstance(item, dict) and item.get("level") in (0, 1)]
    except (KeyError, TypeError) as exc:
        raise ValueError("a level 0 or 1 file has no timestamp") from exc
    return max(times, default=None)


def local_copies(state):
    """How many L0 copies Litestream keeps locally, and when the newest was written (None when there are none)."""
    level = Path(state) / "ltx" / "0"
    files = [p for p in level.glob("*.ltx") if p.is_file()] if level.is_dir() else []
    return len(files), max((p.stat().st_mtime for p in files), default=None)


def stuck_by_local_state(state, listing, env=None):
    """True when the local state is provably stuck: more local L0 copies than stuck_file_limit allows, and the newest of them
    is more than heal_lag_seconds ahead of the replica's newest L0 or L1 file (or the replica has none at all). Both limits
    grow with the sync interval, since an install that uploads once an hour holds an hour's copies. A time that cannot be
    read proves nothing."""
    count, local = local_copies(state)
    if count <= stuck_file_limit(env) or local is None:
        return False
    try:
        remote = replica_newest_copy(listing)
    except ValueError:
        return False
    return remote is None or local - remote > heal_lag_seconds(env)


def heal_flag_path(db):
    return Path(db).parent / HEAL_FLAG


def read_heal_flag(path):
    """The flag file as a dict. Its presence asks for a heal (the runtime check creates it, and a person may too); a heal
    settles it to {"requested": false, "last_heal_at": ...}, which is what keeps the rate limit's time. Anything that is not
    that object, such as an empty file, asks."""
    try:
        text = Path(path).read_text()
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {"requested": True}
    if not isinstance(data, dict):
        return {"requested": True}
    return {"requested": True, **data}


def heal_allowed(flag, now):
    """At most one heal per HEAL_INTERVAL, counted from when the last one began (the flag's last_heal_at)."""
    try:
        last = _parse_utc(flag["last_heal_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return now - last >= HEAL_INTERVAL


def write_heal_flag(path, requested, last_heal_at):
    _write_file(path, json.dumps({"requested": bool(requested), "last_heal_at": last_heal_at}) + "\n")


def _settle_heal_flag(path, last_heal_at):
    """Clears a heal request and keeps when the last heal began. A write that fails is said, not raised."""
    try:
        write_heal_flag(path, False, last_heal_at)
    except OSError as exc:
        print("tico: warning: could not update " + HEAL_FLAG + ": " + str(exc)[:200], file=sys.stderr, flush=True)


def _write_file(path, text):
    """Replaces a small file in one step: the text goes to a temporary beside it, is flushed to disk, and is renamed over the
    file. The file keeps its mode."""
    path = Path(path)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class GenerationRefused(Exception):
    """A new backup generation was not started, and nothing changed: Litestream keeps the copy it has."""


class GenerationSwitchFailed(Exception):
    """A new backup generation broke off after environment.json may have named it. Litestream must not run until a start has
    read environment.json again and pointed its config at the same place."""


class UnsupportedGeneration(ValueError):
    """environment.json names a backup generation this version does not know. Nothing falls back on it, in any mode."""


class GenerationUnknown(Exception):
    """Nothing says which backup generation the data is in, so Litestream and a restore may not start on a guess."""


def new_generation_name(epoch):
    return "gen-" + datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def replica_root_url(env):
    """The backup location as backup_configuration (docker/entrypoint.sh) names it, before any generation is added."""
    return (env.get("TICO_BACKUP_URL") or "file://" + (env.get("TICO_BACKUP_DIR") or "/backups")).rstrip("/")


def config_url(config):
    """The one replica url in the Litestream config, as written. Raises when the config does not hold exactly one."""
    urls = re.findall(r"^[ \t]+url: (.*)$", Path(config).read_text(), re.M)
    if len(urls) != 1:
        raise ValueError("the Litestream config does not hold exactly one replica url")
    return urls[0].strip()


def config_root(config):
    """The backup location the Litestream config's replica url names, without a generation folder."""
    return GENERATION_SUFFIX.sub("", config_url(config))


def config_generation(config):
    """The generation folder the Litestream config's replica url names: "" for the root layout."""
    found = GENERATION_SUFFIX.search(config_url(config))
    return found.group(0)[1:] if found else ""


def point_config_at(config, url):
    """Changes the one replica url in the Litestream config. The file is replaced whole or not at all."""
    path = Path(config)
    text = path.read_text()
    if len(URL_LINE.findall(text)) != 1:
        raise ValueError("the Litestream config does not hold exactly one replica url")
    _write_file(path, URL_LINE.sub(lambda m: m.group(1) + "url: " + url, text))


def start_generation(config, db, env, now, mirror=None):
    """Starts a new backup generation for the database, which is the heal for a stuck copy. In order, with what a failure
    leaves behind:
      1. a probe file is written to the new folder, which must be empty (a failure changes nothing);
      2. the Litestream config names the new folder (a failure leaves the old config in place);
      3. environment.json names the new folder. This is the commit. If the write failed and did not land, the old config is
         put back (GenerationRefused); if it may have landed, the start must read it again (GenerationSwitchFailed);
      4. the local state is removed (a failure is GenerationSwitchFailed, and the next start finishes the job).
    The data volume's cache is not written here: reset_stale_litestream writes it last, once the local state is gone, so the
    status loop never pairs the new folder with the old folder's local copies. Litestream reads its config when it starts, so
    it always runs against the folder environment.json names. The old folder is kept, and nothing is written to it again.
    Returns the line to print."""
    mirror = mirror_from_env(env) if mirror is None else mirror
    root, new = replica_root_url(env), new_generation_name(now)
    try:
        marker = read_marker(mirror)
        if mirror.newest(db_levels(new)) is not None:
            raise GenerationRefused(new + " already holds files")
        _put_text(mirror, new + "/" + GENERATION_PROBE, _utc_text(now) + "\n")
        config_text = Path(config).read_text()
    except GenerationRefused:
        raise
    except Exception as exc:
        raise GenerationRefused("the backup location could not be used (" + type(exc).__name__ + ": "
                                + str(exc)[:160] + ")") from None
    try:
        point_config_at(config, root + "/" + new)
    except Exception as exc:
        raise GenerationRefused("the Litestream config could not be changed (" + type(exc).__name__ + ")") from None
    old_generation = str(marker.get(GENERATION_FIELD) or "")
    try:
        _put_text(mirror, MARKER, json.dumps({**marker, GENERATION_FIELD: new}) + "\n")
    except Exception as exc:
        try:
            landed = replica_generation(mirror) == new
        except Exception:
            landed = None
        if landed is not False:
            raise GenerationSwitchFailed("environment.json could not be written or read back (" + type(exc).__name__
                                         + "); the next start reads it again") from None
        try:
            _write_file(config, config_text)
        except OSError:
            raise GenerationSwitchFailed("environment.json was not written, and the Litestream config could not be put "
                                         "back") from None
        raise GenerationRefused("environment.json could not be written (" + type(exc).__name__ + ")") from None
    try:
        shutil.rmtree(litestream_state_dir(db))
    except OSError as exc:
        raise GenerationSwitchFailed("the local Litestream state could not be removed (" + str(exc)[:160] + ")") from None
    previous = root + "/" + old_generation if old_generation else root
    return ("tico: the local Litestream state was stuck; started backup generation " + new + " at " + root + "/" + new
            + " (the copy at " + previous + " is kept)")


def _follow_config_with_cache(config, db):
    """Writes the data volume's cache to the generation the Litestream config names. The reset job does this last, after the
    local state is dealt with: the status loop reads the cache every round and must not pair a new generation with the local
    copies of the old one. A failure is GenerationSwitchFailed; the next start reads environment.json again."""
    data_dir = Path(db).parent
    generation = config_generation(config)
    try:
        if read_generation_cache(data_dir) != generation:
            write_generation_cache(data_dir, generation)
    except (OSError, ValueError) as exc:
        raise GenerationSwitchFailed("the backup generation file could not be written (" + str(exc)[:160] + "); the next "
                                     "start reads environment.json again") from None


def reset_stale_litestream(config, db, binary="litestream", env=None, now=None):
    """Removes Litestream's local state for `db` when the replica holds no files at all (a new or moved backup location
    while the state is from an earlier run): Litestream then starts the replica from the database itself, not from a
    local copy the replica no longer matches. Nothing else is touched. Litestream's auto-recover is not used: after it,
    local TXIDs restart below a populated replica's position and new writes are never uploaded.

    A populated replica whose local state is stuck (stuck_by_local_state), or for which the replication loop asked for a
    heal, gets a new backup generation instead (start_generation). The copy it had stays where it is, and there is at most
    one such heal per HEAL_INTERVAL. Returns the line to print, or None when there is nothing to say. Raises
    GenerationSwitchFailed when a generation switch broke off after it began. First, the config is made to name the generation
    environment.json names (sync_generation): GenerationUnknown propagates when nothing says which generation that is."""
    env = os.environ if env is None else env
    outcome = sync_generation(config, db, env, write_cache=False)
    message = _reset_local_state(config, db, binary, env, now)
    if outcome == "read":
        _follow_config_with_cache(config, db)    # last, once the local state is dealt with
    return message


def _reset_local_state(config, db, binary, env, now):
    """reset_stale_litestream's work, once the config names the generation environment.json names."""
    state = litestream_state_dir(db)
    flag_path = heal_flag_path(db)
    flag = read_heal_flag(flag_path)
    asked = bool(flag.get("requested"))
    if not state.is_dir() or state.is_symlink():
        if asked:    # with no local state there is nothing to heal, so the request is dropped rather than kept for later
            _settle_heal_flag(flag_path, flag.get("last_heal_at"))
        return None
    listing = replica_listing(config, db, binary)
    if listing is None:
        return "tico: could not read the backup location; skipped the check for stale Litestream state"
    if not listing:
        if asked:
            _settle_heal_flag(flag_path, flag.get("last_heal_at"))
        try:
            shutil.rmtree(state)
        except OSError as exc:
            return "tico: the backup location is empty but Litestream's stale local state could not be removed: " + str(exc)[:200]
        return "tico: the backup location is empty; removed Litestream's stale local state (" + state.name + ")"
    if no_mirror(env) or not (asked or stuck_by_local_state(state, listing, env)):
        return None
    now = time.time() if now is None else now
    if not heal_allowed(flag, now):
        if asked:
            _settle_heal_flag(flag_path, flag.get("last_heal_at"))
        return ("tico: the local Litestream state looks stuck, but a new backup generation was started less than "
                + str(HEAL_INTERVAL // 3600) + " hours ago; not starting another")
    try:
        message = start_generation(config, db, env, now)
    except GenerationRefused as exc:
        message = "tico: could not start a new backup generation (" + str(exc) + "); Litestream keeps the copy it has"
    except GenerationSwitchFailed:
        _settle_heal_flag(flag_path, _utc_text(now))
        raise
    _settle_heal_flag(flag_path, _utc_text(now))    # an attempt counts toward the limit, whatever it did
    return message


def replica_newest(mirror, generation=""):
    """When the replica's newest L0 or L1 file was written, by the mirror's own times; None when it has none."""
    base = generation + "/" if generation else ""
    newest = [mirror.newest([base + "0000", base + "ltx/0"]), mirror.newest([base + "0001", base + "ltx/1"])]
    return max((t for t in newest if t is not None), default=None)


def database_mtime(db):
    """When the database or its WAL last changed. Litestream's own checkpoints count: a copy that is working keeps moving."""
    times = []
    for path in (str(db), str(db) + "-wal"):
        try:
            times.append(os.stat(path).st_mtime)
        except OSError:
            pass
    return max(times, default=None)


class StallWatch:
    """Notices a replica that has stopped moving while the database kept changing. Feed it once per round the replica's newest
    copy time and the database's change time; observe() is true once that has gone on for `after` seconds."""
    def __init__(self):
        self.replica, self.since = None, None

    def reset(self):
        self.replica, self.since = None, None

    def observe(self, replica, changed, now, after):
        if replica != self.replica:
            self.replica, self.since = replica, None    # the replica moved, so what was pending has been copied
        if changed is not None and (replica is None or changed > replica):
            self.since = now if self.since is None else self.since
        else:
            self.since = None
        return self.since is not None and now - self.since >= after


def stop_litestream(env):
    """Stops the Litestream process the entrypoint started (its pid, TICO_LITESTREAM_PID). supervise then stops the server, and
    the container ends, for its restart policy to start it again."""
    try:
        pid = int(env.get("TICO_LITESTREAM_PID") or 0)
    except ValueError:
        pid = 0
    if pid <= 0:
        print("tico: warning: Litestream's process is not known here; restart the server to start a new backup generation",
              flush=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass    # it has already ended, and so will the container


def stuck_replica_check(env, mirror, watch, now):
    """One round of the runtime check. It asks for a heal when the replica has not moved for heal_after_seconds while the
    database kept changing and the local state holds more copies than stuck_file_limit allows, and at most once per
    HEAL_INTERVAL. The heal itself is reset_stale_litestream's, run by the container's next start. Returns True when it
    asked."""
    db = env.get("TICO_DB") or "/data/hub.sqlite"
    after = heal_after_seconds(env)
    if local_copies(litestream_state_dir(db))[0] <= stuck_file_limit(env):
        watch.reset()    # a healthy install: no listing of the replica, and no stall to time
        return False
    generation = replica_generation(mirror)
    if not watch.observe(replica_newest(mirror, generation), database_mtime(db), now, after):
        return False
    flag_path = heal_flag_path(db)
    flag = read_heal_flag(flag_path)
    if not heal_allowed(flag, now):
        return False
    write_heal_flag(flag_path, True, flag.get("last_heal_at"))
    print("tico: the backup copy is stuck: the replica has not moved for " + str(after // 60) + " minutes while the "
          "database changed. Stopping Litestream, so the container restarts and starts a new backup generation.", flush=True)
    stop_litestream(env)
    return True


def loop(env=None, interval=30, warn_every=3600, sleep=time.sleep, rounds=None, clock=time.time):
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
    db = env.get("TICO_DB") or "/data/hub.sqlite"
    watch = StallWatch()
    # The reset job can move the data to another backup generation while this loop runs, so the volume's cache (a local
    # file, no network) is read every round. No cache means the root layout, as at a start. An unreadable cache is not a
    # switch: the generation read last time stays.
    generation, levels = "", DB_LEVELS
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
            generation = read_generation_cache(Path(db).parent) or ""
            levels = db_levels(generation)
        except (OSError, ValueError):
            pass
        try:
            unsent, local_txid = local_level_zero(db)
            write_status(mirror, path, copied, files_copied, unsent, local_txid, behind_warn_seconds(env), levels,
                         generation, clock())
        except Exception as exc:
            print("tico: warning: backup status failed: " + type(exc).__name__ + ": " + str(exc)[:200], flush=True)
        try:
            if stuck_replica_check(env, mirror, watch, clock()):
                return    # Litestream has been stopped; the container restarts and starts a new backup generation
        except Exception as exc:
            print("tico: warning: backup stall check failed: " + type(exc).__name__ + ": " + str(exc)[:200], flush=True)
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
    elif command == "cached-generation":    # DIR: the generation the data volume's cache names; empty for the root (no network)
        try:
            print(read_generation_cache(argv[1]) or "")
        except (OSError, ValueError) as exc:
            print("tico: error: the backup generation file cannot be used (" + str(exc)[:160] + ")", file=sys.stderr)
            return 2
    elif command == "sync-generation":    # [--strict] CONFIG DB: 0 when the generation is known, 5 for the root by fallback
                                          # (not with --strict), 4 when unknown
        paths = [arg for arg in argv[1:] if arg != "--strict"]
        try:
            outcome = sync_generation(paths[0], paths[1], env, strict="--strict" in argv)
        except GenerationUnknown as exc:
            print("tico: error: " + str(exc), file=sys.stderr, flush=True)
            return 4
        if outcome == "root":
            print("tico: error: the backup location cannot be read and this database has no backup generation file, so the "
                  "generation to restore is not known", file=sys.stderr, flush=True)
            return 5
    elif command == "reset-stale-litestream":    # CONFIG DB: run before `litestream replicate`; 3 and 4 = do not start Litestream
        try:
            message = reset_stale_litestream(argv[1], argv[2], env=env)
        except GenerationSwitchFailed as exc:
            print("tico: error: a new backup generation did not start cleanly: " + str(exc), file=sys.stderr, flush=True)
            return 3
        except GenerationUnknown as exc:
            print("tico: error: " + str(exc), file=sys.stderr, flush=True)
            return 4
        if message:
            print(message, flush=True)
    else:
        print("usage: python -m backend.replication loop|restore-blobs|restore-credential-key|is-empty DIR|check-new-company DIR|mark-environment DIR ID|cached-generation DIR|sync-generation [--strict] CONFIG DB|reset-stale-litestream CONFIG DB", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
