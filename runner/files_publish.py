"""Publish a bot's reports and artifacts as Tico files after a completed turn (docs/files.md).

The runner looks at the folders the bot writes deliverables to (`reports/` and `artifacts/` in its
checkout, or `files: {publish: [...]}` in bot.yaml, or the older employee.yaml), and uploads each new or changed file. What
it sends is decided here, on the bot's own computer: allowed types, a size cap, no credential-like
names, no symbolic links, nothing outside the checkout (clients/bot_files.py).

An upload is a row in a small outbox in the runner's own state database, keyed by the bot, the
path and the content digest. The same key is the request's idempotency key, so a retry after a
restart, or after a reply that never arrived, lands once. A file that could not be uploaded is
reported as "not synced" and never shown with a link that opens nothing.
"""

import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from clients import bot_files as BF
from clients.manifest import manifest_path
from clients.tico import APIError, Client
from .outage import describe, log

MAX_PER_TURN = 40
MAX_DEPTH = 6
DRAIN_BATCH = 20
GIVE_UP_AFTER = 20                     # retryable failures in a row before the file is marked not synced
UPLOAD_TIMEOUT = 120
_drain_lock = threading.Lock()
_refusals_logged = set()               # (bot, path, reason): a refused file is logged once, not every turn

TABLES = """
CREATE TABLE IF NOT EXISTS file_outbox(
  key TEXT PRIMARY KEY, bot TEXT NOT NULL, attempt_id TEXT NOT NULL, root TEXT NOT NULL, rel TEXT NOT NULL,
  digest TEXT NOT NULL, commit_sha TEXT NOT NULL DEFAULT '', phase TEXT NOT NULL DEFAULT 'pending',
  tries INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS file_published(
  bot TEXT NOT NULL, rel TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(bot, rel));
"""


def folders_for(config, root):
    """The folders to publish from: the bot's `files.publish`, else reports/ and artifacts/."""
    for source in (config or {}, _employee_yaml(root)):
        value = (source.get("files") or {}) if isinstance(source, dict) else {}
        if isinstance(value, dict) and isinstance(value.get("publish"), list):
            return [str(f).strip().strip("/") for f in value["publish"] if str(f).strip().strip("/")]
    return [f.strip("/") for f in BF.DEFAULT_FOLDERS]


def _employee_yaml(root):
    try:
        import yaml
        return yaml.safe_load(manifest_path(root).read_text()) or {}
    except Exception:
        return {}


def candidates(root, folders):
    """([(relative path, refused-reason or "")], newest first) under the publish folders."""
    root = Path(root).resolve()
    found = []
    for folder in folders:
        base = root / folder
        if base.is_symlink() or not base.is_dir() or root not in base.resolve().parents:
            continue
        stack = [(base, 0)]
        while stack:
            directory, depth = stack.pop()
            try:
                entries = sorted(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith(".") and entry.suffix.lower() not in BF.TYPES:
                    continue
                rel = entry.relative_to(root).as_posix()
                if entry.is_symlink():
                    found.append((rel, "is a symbolic link"))
                elif entry.is_dir():
                    if depth < MAX_DEPTH:
                        stack.append((entry, depth + 1))
                else:
                    found.append((rel, ""))
    return found[:MAX_PER_TURN * 5]


def prepare(state, bot, attempt_id, root, config, pushed=False, skip=()):
    """Queue what changed since the last publish; return (queued, refused) counts. `skip` names files that hold a
    granted secret (runner/redact.py): they are never queued."""
    queued = refused = 0
    with state.connect() as c:
        c.executescript(TABLES)
    for rel, why in candidates(root, folders_for(config, root)):
        try:
            if rel in skip:
                raise BF.Refused("holds a secret")
            if why:
                raise BF.Refused(why)
            path, rel = BF.local_file(root, rel)
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
        except (BF.Refused, OSError) as exc:
            refused += 1
            if (bot, rel, str(exc)) not in _refusals_logged:
                if len(_refusals_logged) > 10000:
                    _refusals_logged.clear()
                _refusals_logged.add((bot, rel, str(exc)))
                log(f"Tico runner: {bot}: not publishing {rel}: {exc}")
            continue
        key = f"file:{bot}:{BF.sha256(rel.encode())[:16]}:{digest[:32]}"
        commit = BF.pushed_commit(root, rel) if pushed else ""
        with state.connect() as c:
            same = c.execute("SELECT 1 FROM file_published WHERE bot=? AND rel=? AND digest=?", (bot, rel, digest)).fetchone()
            if same or c.execute("SELECT 1 FROM file_outbox WHERE key=?", (key,)).fetchone():
                continue
            if queued >= MAX_PER_TURN:
                break
            c.execute("INSERT INTO file_outbox(key,bot,attempt_id,root,rel,digest,commit_sha,created) VALUES(?,?,?,?,?,?,?,?)",
                      (key, bot, attempt_id, str(Path(root).resolve()), rel, digest, commit, time.time()))
            queued += 1
    return queued, refused


def pending_under(state, root):
    """Whether a file under `root` still waits to be uploaded: its folder must stay until it has gone."""
    with state.connect() as c:
        c.executescript(TABLES)
        return c.execute("SELECT 1 FROM file_outbox WHERE phase='pending' AND root=? LIMIT 1",
                         (str(Path(root).resolve()),)).fetchone() is not None


def _query(row, extra=None):
    return urlencode({"bot": row["bot"], "attempt": row["attempt_id"], "path": row["rel"],
                      "name": Path(row["rel"]).name, **({"commit": row["commit_sha"]} if row["commit_sha"] else {}),
                      **(extra or {})})


def drain(runner, limit=DRAIN_BATCH):
    """Upload what is queued. Safe to call any time and from any thread: a retry is the same request."""
    if not _drain_lock.acquire(blocking=False):
        return 0
    try:
        with runner.state.connect() as c:
            c.executescript(TABLES)
            rows = [dict(r) for r in c.execute("SELECT * FROM file_outbox WHERE phase='pending' ORDER BY created LIMIT ?", (limit,))]
        if not rows:
            return 0
        # Big files need longer than the runner's quick calls allow.
        client = Client(runner.client.url, runner.client.token, timeout=UPLOAD_TIMEOUT, retries=1)
        sent = 0
        for row in rows:
            sent += upload(runner, client, row)
        return sent
    finally:
        _drain_lock.release()


def upload(runner, client, row):
    state, bot = runner.state, row["bot"]

    def mark(phase, error="", tries=None):
        with state.connect() as c:
            c.execute("UPDATE file_outbox SET phase=?,error=?,tries=coalesce(?,tries) WHERE key=?", (phase, error, tries, row["key"]))
            if phase == "done":
                c.execute("INSERT OR REPLACE INTO file_published VALUES(?,?,?)", (bot, row["rel"], row["digest"]))

    try:
        path, rel = BF.local_file(row["root"], row["rel"])
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != row["digest"]:
            mark("stale", "changed on disk since it was queued; the next turn queues the new content")
            return 0
    except (BF.Refused, OSError) as exc:
        mark("failed", str(exc))
        return 0
    try:
        with path.open("rb") as source:
            client.request("POST", "/api/v2/files/uploads?" + _query(row), raw=source, key=row["key"])
        mark("done")
        return 1
    except APIError as exc:
        tries = row["tries"] + 1
        if exc.retryable and tries < GIVE_UP_AFTER:
            mark("pending", describe(exc), tries)
            return 0
        log(f"Tico runner: {bot}: could not publish {row['rel']} ({describe(exc)}); it shows as not synced")
        mark("failed", describe(exc), tries)
        report_not_synced(client, row)
        return 0


def report_not_synced(client, row):
    """Tell the hub this file is not synced, so its page never shows a link that opens nothing."""
    try:
        client.request("POST", "/api/v2/files/uploads?" + _query(row, {"sync": "failed"}), raw=b"",
                       key=row["key"] + ":not-synced")
    except APIError:
        pass


def after_turn(runner, attempt, root, pushed=False, skip=()):
    """The one call the runner makes when a turn has completed. It never raises: a report that
    cannot be published must not turn a finished turn into a failure."""
    try:
        bot = attempt["bot"]
        queued, refused = prepare(runner.state, bot, attempt["id"], root, attempt.get("config") or {}, pushed, skip)
        if queued:
            log(f"Tico runner: {bot}: publishing {queued} file{'s' if queued != 1 else ''}")
        drain(runner)
    except Exception as exc:
        log(f"Tico runner: publishing files failed ({type(exc).__name__}: {exc}); will retry")
