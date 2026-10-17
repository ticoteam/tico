"""A dev server's database: a pruned copy of a real install that cannot act on the world.

    python -m backend.devcopy --from <database | s3://bucket/backups/<name>.sqlite> --to <path> [--days 30]

The source is only read. Every table is classified: EMPTY (it queues work, schedules it, lets a machine or a person
in, or reaches an outside service: emptied), HISTORY (cut to the last `--days`) or KEEP. A table in none of them, one
added after this list, is emptied and named in the output: a new table is safe until someone decides about it.

Runs stay for realism: finished attempts in the window keep their events, conversations and inputs (inputs are
message links, no text, and decide who may see a run). Unfinished jobs and attempts go. The tokens on kept rows are
replaced by values no token hashes to: bots, external agents, runners (also revoked) and attempts (lease ended).
The key files that sit beside a database (backup.KEY_FILES) are never copied, so a credential row missed here still
opens nothing. Beside the copy go a `server.env` with TICO_REHEARSAL=1 (nothing runs on a timer, nothing leaves the
server, and runners are refused), a fresh local sign-in token, and empty registry and attachment folders.
"""
import argparse
import json
import os
import secrets
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from . import backup
from .hubdb import now, shift

# Emptied, never pruned: one surviving row is one thing the copy could do.
EMPTY = (
    # queued work
    "job_recovery", "service_jobs", "doc_source_jobs", "update_queue", "task_reminders", "calendar_actions",
    "meeting_deliveries", "worktree_waits", "idempotency", "blob_media_retries", "github_product_repo_operations",
    # routines and watchers
    "schedules", "schedule_config", "schedule_occurrences", "triggers", "trigger_deliveries", "watcher_runs", "watcher_items", "watcher_refs",
    # runner and bot sessions
    "enrollments", "assignments", "agent_pairings", "bot_sessions", "session_epochs", "model_logins", "model_login_cleared",
    "computer_profiles", "subscription_assignments", "runner_harness_actions", "runner_harness_choices",
    "runner_versions",
    # human sign-in and service keys
    "human_tokens", "oidc_sessions", "oidc_codes", "oauth_grants", "oauth_clients", "service_keys",
    "service_key_tasks",
    # credentials
    "credentials", "credential_keys", "credential_grants", "credential_requests", "credential_imports",
    "directory_credentials", "company_storage",
    # mail, Slack, GitHub, Granola, meeting bots, support
    "mail_mailboxes", "bot_mail_settings", "google_connects", "google_mail_holders", "slack_credentials", "slack_posts", "slack_events", "slack_threads",
    "github_app", "github_app_states", "granola_connections", "meeting_importers", "live_meeting_bots",
    "support_tickets", "support_access", "support_access_log",
    # update feeds: a walk of an outside feed, and what it delivers to bots
    "feed_updates", "feed_sources", "feed_walks", "feed_cursors", "feed_deliveries",
    "usage_alerts", "task_requesters", "roadmap_actions",
    # raw provider streams: customer text, and whatever the secret scrub missed
    "attempt_captures", "attempt_capture_drops",
)
# Cut to `--days`, in this order: {table: its time column}. A row a kept row still points at stays.
HISTORY = {"attempts": "created", "jobs": "created", "messages": "created", "events": "ts", "deltas": "ts",
           "bot_status_history": "since"}
# Kept whole, except rows whose parent went (a pruned attempt's events). Tokens on runners are replaced.
KEEP = frozenset((
    "agents", "approvals", "archives", "assistant_actions", "attempt_conversations", "attempt_events",
    "attempt_inputs", "backup_verified_blobs", "batches", "blob_locations", "blob_media", "blobs",
    "bot_agent_instructions", "bot_config", "bot_control", "bot_file_activity", "bot_file_versions", "bot_files",
    "bot_memory_documents", "bot_memory_seen", "bot_memory_updates", "bot_repo_access", "bot_status",
    "bot_tool_requests", "bot_transition_checkpoints", "bot_transitions", "bots", "changes", "chat_goals",
    "cloud_migrations", "connector_snapshots", "conversations", "doc_sources", "doc_versions", "docs",
    "document_versions", "documents", "external_documents", "external_sections", "external_snapshot_docs",
    "external_snapshots", "external_versions", "flight_db", "flight_events", "flight_process", "flight_requests",
    "flight_slow", "flight_sql", "goal_asks", "goal_checkins", "goal_events", "goal_kpis", "goal_proposals", "goals",
    "health_findings", "health_reports", "humans", "import_refs", "intake_items", "kpi_definitions", "kpi_readings",
    "kpis", "learning_cursors", "learning_deliveries", "learning_items", "learning_runs", "learnings", "linked_docs",
    "listen_items", "listen_judgments", "listen_runs", "live_meeting_chat", "live_meeting_chat_routes",
    "live_meeting_chunk_versions", "live_meeting_chunks", "live_meeting_events", "live_meeting_humans",
    "live_meeting_turns", "live_meeting_windows", "live_meetings", "mail_agent_instructions", "mail_messages",
    "main_pushes", "market_citations", "market_edges", "market_entities", "market_entity_keys", "market_events",
    "market_evidence", "market_insights", "media_assets", "media_control", "meeting_brain", "meeting_comments",
    "meeting_items", "meeting_versions", "meetings", "message_assets", "message_links", "message_links_dirty",
    "message_links_seen", "notes", "perf_overloads", "perf_points", "perf_usage", "preferences", "rate_limits",
    "recording_source_refs", "recording_transcripts", "refusals", "registry_metadata", "repositories", "runners",
    "service_health", "settings_changes", "slack_channels", "slack_digests", "slack_reads", "tags", "task_assets",
    "task_check_models", "task_delegations", "task_events", "task_file_reviews", "task_links", "task_relations",
    "task_roles", "task_steps", "task_tags", "task_trash", "task_trash_assets", "task_types", "tasks",
    "turn_usage_segments", "turns", "update_archive_overrides", "update_reads", "update_settings", "updates",
    "usage_limits",
))
# Work still to do: a job or attempt in any other state is finished and kept as history.
# Rows that belong to their attempt: they go with it and never hold an old attempt back.
OWNED = frozenset(("attempt_events", "attempt_conversations", "attempt_inputs"))
OPEN_JOBS = ("queued", "leased", "running", "input", "uncertain")
OPEN_ATTEMPTS = ("queued", "leased", "running", "input")


def tables(c):
    return {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def data_tables(c):
    """Every ordinary table: not SQLite's own, not a full-text index or its shadow tables (they follow their source)."""
    virtual = [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                           "AND sql LIKE 'CREATE VIRTUAL TABLE%'")]
    return {t for t in tables(c) if not t.startswith("sqlite_")
            and not any(t == v or t.startswith(v + "_") for v in virtual)}


def unclassified(c):
    return sorted(t for t in data_tables(c) if t not in KEEP and t not in HISTORY and t not in EMPTY)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def emptied(c):
    """The tables this module empties that exist in `c`: EMPTY and every unclassified one (Litestream's included)."""
    present = tables(c)
    return [t for t in EMPTY if t in present] + unclassified(c)


def children(c, parent):
    """(child table, its column, the parent's column) for every foreign key that points at `parent`."""
    found = []
    for table in sorted(tables(c)):
        for fk in c.execute(f"PRAGMA foreign_key_list({quote(table)})"):
            if fk[2] == parent:
                found.append((table, fk[3], fk[4] or "rowid"))
    return found


def in_use(path):
    """Whether another connection holds `path` open: leaving WAL needs every other connection gone."""
    try:
        with closing(sqlite3.connect(path, timeout=0)) as c:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
            if mode == "wal":
                return c.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete"
            c.execute("BEGIN EXCLUSIVE")
            c.rollback()
            return False
    except sqlite3.Error:
        return True


def ignored_by_git(directory):
    """False only when `directory` is inside a git checkout that would track it: copies never become fixtures."""
    probe = directory
    while not probe.exists():
        probe = probe.parent
    try:
        top = subprocess.run(["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    if top.returncode != 0:
        return True                     # not in a checkout
    check = subprocess.run(["git", "-C", top.stdout.strip(), "check-ignore", "-q", str(directory / "devcopy-probe")],
                           capture_output=True, timeout=10)
    return check.returncode == 0


def check_destination(source, dest, force):
    """Refuse a destination that is the source, a database in use, an existing file without --force, a folder
    holding key files, or a folder git would track."""
    if dest == source or (dest.exists() and os.path.samefile(dest, source)):
        raise SystemExit("devcopy: the destination is the source database")
    for name in backup.KEY_FILES:
        if (dest.parent / name).exists():
            raise SystemExit(f"devcopy: {dest.parent} holds {name}; put the copy in a folder of its own")
    if not ignored_by_git(dest.parent):
        raise SystemExit(f"devcopy: {dest.parent} is inside a git checkout and not ignored; use a gitignored "
                         "folder (runtime/ in this repository) or one outside any checkout")
    leftovers = [Path(str(dest) + suffix) for suffix in ("", "-wal", "-shm", "-journal")]
    if any(p.exists() for p in leftovers) or (dest.parent / "server.env").exists():
        if not force:
            raise SystemExit(f"devcopy: {dest} or its server.env already exists; pass --force to replace them")
        if dest.exists() and in_use(dest):
            raise SystemExit(f"devcopy: {dest} is in use (a server has it open); stop it first")
        for p in leftovers:
            p.unlink(missing_ok=True)


def fetch_bundle(url, directory, profile=None, region=None):
    """The database of a backup bundle (backup.upload_bundle), downloaded and checked against its manifest. Only the
    snapshot is read: the bundle's key files and attachments are left in the bucket."""
    import json
    import boto3
    bucket, _, key = url.removeprefix("s3://").partition("/")
    if not key.startswith("backups/") or not key.endswith(".sqlite"):
        raise SystemExit("devcopy: name the exact s3://<bucket>/backups/<name>.sqlite recovery point")
    s3 = boto3.Session(profile_name=profile, region_name=region).client("s3")
    with s3.get_object(Bucket=bucket, Key=key + ".manifest.json")["Body"] as stream:
        manifest = json.loads(stream.read(64 * 1024 * 1024))
    if manifest.get("format") != "tico-backup-v1" or manifest.get("database", {}).get("key") != key:
        raise SystemExit("devcopy: not a complete Tico backup manifest for that key")
    local = Path(directory) / "bundle.sqlite"
    s3.download_file(bucket, key, str(local))
    local.chmod(0o600)
    if {"key": key, **backup.inspect(local)} != manifest["database"]:
        raise SystemExit("devcopy: the downloaded database does not match its manifest")
    return local


def prune(c, days):
    """Empty what can act, revoke every token, cut history to `days`. Returns {table: rows removed}."""
    removed = {}
    present = tables(c)

    def delete(table, where="", args=()):
        count = c.execute(f"DELETE FROM {quote(table)}" + (" WHERE " + where if where else ""), args).rowcount
        removed[table] = removed.get(table, 0) + count

    gone = emptied(c)
    for table in gone:
        delete(table)
    # A deleted task's snapshot holds copies of its own rows; an emptied table's copies (requesters' contacts and
    # token hashes, delivery bookkeeping) go too, so neither a read of the trash nor a restore brings them back.
    if "task_trash" in present:
        for task_id, raw in c.execute("SELECT task_id,rows_json FROM task_trash").fetchall():
            snapshot = json.loads(raw or "{}")
            rows = snapshot.get("rows") if isinstance(snapshot, dict) else None
            if isinstance(rows, dict) and set(rows) & set(gone):
                snapshot["rows"] = {t: r for t, r in rows.items() if t not in gone}
                c.execute("UPDATE task_trash SET rows_json=? WHERE task_id=?", (json.dumps(snapshot), task_id))
    # The secret that keys visitors' hashed addresses is the live install's; the copy gets its own.
    if "registry_metadata" in present:
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='roadmap_ip_secret'",
                  (json.dumps(secrets.token_hex(32)),))
    if "jobs" in present:
        delete("jobs", "state IN (%s)" % ",".join("?" * len(OPEN_JOBS)), OPEN_JOBS)
    if "attempts" in present:
        delete("attempts", "finished IS NULL OR state IN (%s)" % ",".join("?" * len(OPEN_ATTEMPTS)), OPEN_ATTEMPTS)
    stamp = now()
    if days:
        cutoff = shift(stamp, days=-days)
        cleared = set(emptied(c)) | OWNED
        for table, column in HISTORY.items():
            if table not in present:
                continue
            refs = [(child, col, key) for child, col, key in children(c, table) if child not in cleared]
            where = f"{quote(column)} < ?" + "".join(
                f" AND {quote(key)} NOT IN (SELECT {quote(col)} FROM {quote(child)} WHERE {quote(col)} IS NOT NULL)"
                for child, col, key in refs)
            delete(table, where, (cutoff,))
    # Kept rows keep no way in: a value no token hashes to (the columns are NOT NULL UNIQUE where not nullable).
    if "bots" in present:
        c.execute("UPDATE bots SET token_hash=NULL")
    if "agents" in present:
        c.execute("UPDATE agents SET token_hash='devcopy-revoked:'||bot, revoked_at=coalesce(revoked_at,?), "
                  "revoked_by=coalesce(revoked_by,'devcopy')", (stamp,))
    if "runners" in present:
        c.execute("UPDATE runners SET token_hash='devcopy-revoked:'||id, revoked_at=coalesce(revoked_at,?)", (stamp,))
    if "attempts" in present:
        c.execute("UPDATE attempts SET token_hash='devcopy-revoked:'||id, lease_until=coalesce(finished,created)")
    # A row that points at one just removed goes too (attempt_events of an attempt, oauth_grants of a token):
    # repeated until no foreign key is broken by what this run removed.
    touched, frontier = set(removed), set(removed)
    while frontier:
        suspects = {child for parent in frontier for child, _, _ in children(c, parent)}
        frontier = set()
        for child in sorted(suspects):
            broken = [rowid for _, rowid, parent, _ in c.execute(f"PRAGMA foreign_key_check({quote(child)})")
                      if parent in touched and rowid is not None]
            if broken:
                c.executemany(f"DELETE FROM {quote(child)} WHERE rowid=?", [(r,) for r in broken])
                removed[child] = removed.get(child, 0) + len(broken)
                touched.add(child)
                frontier.add(child)
    if "messages" in present:
        for table in ("message_links", "message_links_dirty", "message_links_seen"):
            if table in present:
                delete(table, "message_id NOT IN (SELECT id FROM messages)")
    return {t: n for t, n in removed.items() if n}


def build(source, dest, days=30, force=False):
    """Copy `source` (read-only) to `dest`, pruned. Returns ({table: rows removed}, [unclassified tables emptied])."""
    source, dest = Path(source).resolve(), Path(dest).resolve()
    if not source.is_file():
        raise SystemExit(f"devcopy: {source} is not a database file")
    check_destination(source, dest, force)
    dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    work = dest.parent / f".{dest.name}.devcopy-{secrets.token_hex(4)}"
    try:
        backup.copy_pages(source, work)          # opens the source ?mode=ro; the work file is created 0600
        with closing(sqlite3.connect(work, isolation_level=None)) as c:
            c.execute("PRAGMA journal_mode=DELETE")
            c.execute("PRAGMA foreign_keys=OFF")
            # Triggers would queue jobs and write change rows for every deleted row: off while pruning, then back.
            triggers = c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
            c.execute("BEGIN IMMEDIATE")
            for name, _ in triggers:
                c.execute(f"DROP TRIGGER {quote(name)}")
            loose = unclassified(c)
            removed = prune(c, days)
            for _, sql in triggers:
                c.execute(sql)
            c.execute("COMMIT")
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            os.environ.setdefault("SQLITE_TMPDIR", str(dest.parent))
            c.execute("VACUUM INTO ?", (str(dest),))
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(str(work) + suffix).unlink(missing_ok=True)
    dest.chmod(0o600)
    with closing(sqlite3.connect(dest.as_uri() + "?mode=ro", uri=True)) as c:
        if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SystemExit(f"devcopy: {dest} failed its integrity check")
        left = [t for t in emptied(c) if c.execute(f"SELECT 1 FROM {quote(t)} LIMIT 1").fetchone()]
        if left:
            raise SystemExit("devcopy: tables not empty after pruning: " + ", ".join(left))
        live = c.execute("SELECT count(*) FROM attempts WHERE token_hash NOT LIKE 'devcopy-revoked:%' "
                         "OR finished IS NULL").fetchone()[0] if "attempts" in tables(c) else 0
        if live:
            raise SystemExit("devcopy: an unfinished or unrevoked attempt survived pruning")
    return removed, loose


def owner_email(path):
    import json
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as c:
        row = c.execute("SELECT value_json FROM registry_metadata WHERE key='owner'").fetchone() \
            if "registry_metadata" in tables(c) else None
        if row:
            email = str((json.loads(row[0]) or {}).get("email") or "")
            if email:
                return email
        row = c.execute("SELECT email FROM humans WHERE email LIKE '%@%' ORDER BY id LIMIT 1").fetchone() \
            if "humans" in tables(c) else None
        return row[0] if row else ""


def write_environment(dest, port, email):
    """server.env, the loopback sign-in token and the empty folders the dev server runs with, all owner-only."""
    folder = Path(dest).parent
    token = folder / "local-owner.token"
    token.unlink(missing_ok=True)
    fd = os.open(token, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(secrets.token_urlsafe(32) + "\n")
    for name in ("registry", "blobs"):
        (folder / name).mkdir(mode=0o700, exist_ok=True)
    url = f"http://127.0.0.1:{port}"
    lines = {"TICO_REHEARSAL": "1", "TICO_SCHEDULER": "0", "TICO_DB": str(dest),
             "TICO_BLOB_DIR": str(folder / "blobs"), "TICO_REGISTRY_DIR": str(folder / "registry"),
             "TICO_PUBLIC_URL": url, "TICO_RUNNER_URL": url, "TICO_PORT": str(port),
             "TICO_LOCAL_OWNER_TOKEN_FILE": str(token), "TICO_OWNER_EMAIL": email}
    env = folder / "server.env"
    env.unlink(missing_ok=True)
    fd = os.open(env, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("# A dev copy (python -m backend.devcopy): rehearsal mode, loopback only. No key files.\n")
        # Quoted only where needed, so `. server.env` keeps a path with spaces whole (dotenv reads the same quoting).
        handle.writelines(f"{key}={shlex.quote(value)}\n" for key, value in lines.items())
    return env, token, url


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m backend.devcopy", description=__doc__.split("\n\n")[0])
    parser.add_argument("--from", dest="source", required=True,
                        help="a database file (a live one is read, never written) or s3://<bucket>/backups/<name>.sqlite")
    parser.add_argument("--to", dest="dest", required=True, type=Path, help="the copy's database file (a new one)")
    parser.add_argument("--days", type=int, default=30, help="history kept, in days (0 keeps all of it)")
    parser.add_argument("--port", type=int, default=8879, help="the loopback port written to server.env")
    parser.add_argument("--owner-email", default="", help="who signs in as owner (default: the copy's own owner)")
    parser.add_argument("--force", action="store_true", help="replace an existing copy that is not in use")
    parser.add_argument("--profile", help="AWS profile for an s3:// bundle")
    parser.add_argument("--region", default="us-west-2", help="AWS region for an s3:// bundle")
    args = parser.parse_args(argv)
    dest = args.dest.expanduser().resolve()
    if args.days < 0:
        parser.error("--days must be 0 or more")
    dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tico-devcopy-", dir=dest.parent) as scratch:
        os.chmod(scratch, 0o700)
        source = (fetch_bundle(args.source, scratch, args.profile, args.region)
                  if args.source.startswith("s3://") else Path(args.source).expanduser())
        removed, loose = build(source, dest, days=args.days, force=args.force)
    email = args.owner_email or owner_email(dest)
    env, token, url = write_environment(dest, args.port, email)
    if not email:
        print(f"No owner found in the copy: set TICO_OWNER_EMAIL in {env} to someone on its roster.")
    for table, count in sorted(removed.items()):
        print(f"  {table}: {count} removed")
    for table in loose:
        print(f"Emptied {table}: not classified in backend/devcopy.py (KEEP, HISTORY or EMPTY); decide and add it.")
    print(f"Copy: {dest} ({dest.stat().st_size // 1024} KB, rehearsal mode, no key files)")
    print(f"Start it from this checkout:\n"
          f"  set -a; . {shlex.quote(str(env))}; set +a; python -m backend.server --host 127.0.0.1 "
          f"--port {args.port}\n"
          f"Sign in: {url}/api/v2/local-signin?token=$(cat {shlex.quote(str(token))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
