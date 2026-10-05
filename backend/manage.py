"""Explicit offline administration and migration. These commands do not run bots."""

import argparse
import json
import os
from pathlib import Path
import sqlite3

import yaml

from clients.manifest import manifest_path, repo_dir

from .auth import Auth
from .archive import import_text_archives
from .backup import inspect, snapshot, upload_bundle
from .blobs import Blobs
from .config import ROOT, Settings
from .execution import Execution
from .store import H, P, Store, encode, repo_url


CORE_HISTORY = ("conversations", "messages", "tasks", "task_events", "turns", "meetings",
                "meeting_versions", "meeting_deliveries")


def registry_dir():
    """Offline commands seed and read the environment's own registry, not the checkout's."""
    return Path(os.environ.get("TICO_REGISTRY_DIR") or ROOT / "registry")


def bots_by_operator(c):
    """Who hosts what after an import, so an operator can check the split at a glance."""
    result = {}
    for bot, operator in c.execute("SELECT bot,operator FROM bot_config ORDER BY operator,bot"):
        result.setdefault(operator, []).append(bot)
    return result


def resolved_registry(projects, registry_dir):
    source = yaml.safe_load((registry_dir / "employees.yaml").read_text())
    entries = {}
    fields = {"name", "display_name", "description", "repo", "reports_to", "team", "runtime", "model",
              "reasoning_effort", "effort", "status", "max_run_minutes", "schedules", "access", "routines", "tools",
              "reads", "slack_channel", "role", "title", "order"}
    for entry in source["employees"]:
        slug = entry["name"]
        path = manifest_path(repo_dir(projects, slug))
        manifest = yaml.safe_load(path.read_text()) if path.exists() else {}
        merged = {**source.get("defaults", {}), **entry, **(manifest or {})}
        entries[slug] = {k: v for k, v in merged.items() if k in fields}
        entries[slug].update({"name": slug, "host": "keeper", "tasks": "hub"})
    return entries


def migrate_legacy(source, destination, runtime, projects, *, queue_backlog=False):
    """Create a new cloud database from a read-only legacy source snapshot."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    runtime, projects = Path(runtime).resolve(), Path(projects).resolve()
    if not source.is_file() or source == destination:
        raise ValueError("Migrate from an existing legacy database into a distinct new file")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as c:
        tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"bots", "humans", "conversations", "messages", "tasks", "turns", "meetings"}
    if not required.issubset(tables) or tables & {"cloud_migrations", "bot_config", "runners"}:
        raise ValueError("Source must be the legacy Hub database, not an initialized cloud database")
    source_report = snapshot(source, destination)
    store = Store(Settings(db_path=destination, registry_dir=registry_dir()))
    store.initialize(adopt_legacy=True)
    entries = resolved_registry(projects, store.settings.registry_dir)
    with store.read() as c:
        seeded = bool(c.execute("SELECT 1 FROM bot_config LIMIT 1").fetchone())
    if not seeded:
        store.seed(entries=entries)
    with store.transaction() as c:
        # Legacy bot tokens, machine paths, runtime hosts and provider sessions
        # are local capabilities/history, never cloud execution credentials.
        c.execute("UPDATE bots SET token_hash=NULL,thread_id=NULL,cwd='',host='remote'")
        c.execute("DELETE FROM rate_limits")
        H.event(c, H.KEEPER, "legacy.execution_fenced", "database",
                {"source_sha256": source_report["sha256"]})
    with store.read() as c:
        pending_messages = c.execute(
            "SELECT count(*) FROM messages WHERE to_actor LIKE 'bot:%' AND delivered_at IS NULL"
        ).fetchone()[0]
    if queue_backlog:
        store.enqueue_existing()
    archives = import_text_archives(store, runtime)
    with store.transaction() as c:
        counts = {table: c.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]
                  for table in CORE_HISTORY}
        expected = {table: source_report["tables"].get(table, 0) for table in CORE_HISTORY}
        if counts != expected:
            raise RuntimeError("Core history counts changed during migration")
        state = {"pending_messages": pending_messages,
                 "backlog_activated": bool(queue_backlog),
                 "queued_jobs": c.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0],
                 "assignments": c.execute("SELECT count(*) FROM assignments").fetchone()[0],
                 "runners": c.execute("SELECT count(*) FROM runners").fetchone()[0],
                 "legacy_tokens": c.execute("SELECT count(*) FROM bots WHERE token_hash IS NOT NULL").fetchone()[0],
                 "legacy_paths": c.execute("SELECT count(*) FROM bots WHERE cwd<>'' OR host<>'remote'").fetchone()[0],
                 "bots_by_operator": bots_by_operator(c)}
        if state["assignments"] or state["runners"] or state["legacy_tokens"] or state["legacy_paths"]:
            raise RuntimeError("Legacy execution authority was not fully fenced")
        manifest = {"format": "tico-legacy-import-v1", "source": str(source),
                    "source_sha256": source_report["sha256"], "history": counts,
                    "archives": archives, "execution": state}
        c.execute("INSERT INTO registry_metadata VALUES('legacy-import',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                  (encode(manifest),))
    # Produce one portable file for transfer. The cloud server will return it to WAL mode on
    # first startup, but no accepted migration write may remain only in a local sidecar.
    with sqlite3.connect(destination) as c:
        checkpoint = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and (checkpoint[0] or checkpoint[1] != checkpoint[2]):
            raise RuntimeError("Could not checkpoint the migrated database for transfer")
        if c.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
            raise RuntimeError("Could not make the migrated database a portable single file")
    final = inspect(destination)
    if final["integrity"] != ["ok"] or final["foreign_key_violations"]:
        raise RuntimeError("Migrated database failed final integrity verification")
    return {"source": source_report, "destination": final, "history": counts,
            "archives": archives, "execution": state}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("database", type=Path)
    p = sub.add_parser("snapshot")
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    p = sub.add_parser("checkpoint", help="Exact deployment rollback copy without the full integrity scan")
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    p = sub.add_parser("compact", help="Drop expired idempotency rows and VACUUM a fenced database when worthwhile")
    p.add_argument("database", type=Path)
    p = sub.add_parser("restore")
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    p = sub.add_parser("initialize")
    p.add_argument("database", type=Path)
    p.add_argument("--projects", type=Path, default=ROOT.parent)
    p.add_argument("--registry", type=Path, default=registry_dir(),
                   help="This environment's registry directory (default: $TICO_REGISTRY_DIR)")
    p = sub.add_parser("set-repo", help="Point a bot at its Git repository: a name, owner/name, or an https URL")
    p.add_argument("database", type=Path)
    p.add_argument("bot")
    p.add_argument("repo")
    p = sub.add_parser("usage-count", help="The anonymous usage count (PRIVACY.md): show it, turn it on or off, or make a new install ID")
    p.add_argument("database", type=Path)
    p.add_argument("action", choices=("show", "on", "off", "reset-id", "payload"))
    p = sub.add_parser("import-history")
    p.add_argument("database", type=Path)
    p.add_argument("--runtime", type=Path, required=True)
    p = sub.add_parser("migrate-legacy")
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    p.add_argument("--runtime", type=Path, required=True)
    p.add_argument("--projects", type=Path, default=ROOT.parent)
    p.add_argument("--queue-legacy-backlog", action="store_true",
                   help="Explicitly make old undelivered bot messages claimable after import")
    p = sub.add_parser("enrollment")
    p.add_argument("database", type=Path)
    owner = p.add_mutually_exclusive_group(required=True)
    owner.add_argument("--owner", dest="operator", help="the human who owns the computer")
    owner.add_argument("--operator", dest="operator", help=argparse.SUPPRESS)      # the old spelling, hidden for one release
    p.add_argument("--out", type=Path, required=True, help="New private file for the enrollment code")
    p = sub.add_parser("delete-tasks", help="Move tasks made by mistake, with their conversations, to the "
                       "trash; lists what would go unless --apply")
    p.add_argument("database", type=Path)
    p.add_argument("--ids-file", type=Path, required=True, help="One task id per line")
    p.add_argument("--apply", action="store_true", help="Delete; without it nothing changes")
    p = sub.add_parser("restore-tasks", help="Put deleted tasks back from the trash")
    p.add_argument("database", type=Path)
    p.add_argument("--ids-file", type=Path, required=True, help="One task id per line")
    p = sub.add_parser("purge-deleted-tasks", help="Remove for good what has been in the trash longer than "
                       "--older-than-days; lists it unless --apply")
    p.add_argument("database", type=Path)
    p.add_argument("--older-than-days", type=int, required=True)
    p.add_argument("--apply", action="store_true", help="Purge; without it nothing changes")
    p = sub.add_parser("backup")
    p.add_argument("database", type=Path)
    p.add_argument("--bucket", required=True)
    p.add_argument("--key", required=True)
    storage = p.add_mutually_exclusive_group(required=True)
    storage.add_argument("--blob-dir", type=Path)
    storage.add_argument("--blob-bucket")
    args = parser.parse_args(argv)
    if args.command == "inspect":
        report = inspect(args.database)
    elif args.command == "snapshot":
        report = snapshot(args.source, args.destination)
    elif args.command == "checkpoint":
        from .backup import checkpoint
        report = checkpoint(args.source, args.destination)
    elif args.command == "compact":
        from .backup import compact
        report = compact(args.database)
    elif args.command == "restore":
        from .backup import restore_snapshot
        report = restore_snapshot(args.source, args.destination)
    elif args.command == "initialize":
        store = Store(Settings(db_path=args.database, registry_dir=args.registry))
        store.initialize()
        entries = resolved_registry(args.projects, store.settings.registry_dir)
        store.seed(entries=entries)
        store.enqueue_existing()
        report = {"database": str(args.database), "bots": len(entries)}
        with store.read() as c:
            report["bots_by_operator"] = bots_by_operator(c)
    elif args.command == "enrollment":
        from .models import EnrollmentRequest
        store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
        auth = Auth(store)
        execution = Execution(store, auth)
        with store.transaction() as c:
            auth.sync_access(c)   # the owner is stored in the database; an offline command has no request to load it
            # The offline command acts as the environment owner, never as an ambient superuser.
            code = execution.issue_enrollment(c, auth.owner_identity(c), EnrollmentRequest(operator=args.operator))
            # Never print a credential into the operator's shared logs.
            fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(code, stream)
        report = {"written": str(args.out), "expires": code["expires"]}
    elif args.command == "set-repo":
        from .models import repo_reference
        store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
        repo = repo_reference(args.repo)
        with store.transaction() as c:
            row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (args.bot,)).fetchone()
            if not row:
                raise SystemExit("Unknown bot: " + args.bot)
            config = {**json.loads(row["config_json"] or "{}"), "repo": repo}
            c.execute("UPDATE bot_config SET repo=?,config_json=?,revision=revision+1 WHERE bot=?",
                      (repo, encode(config), args.bot))
            H.event(c, H.KEEPER, "bot.repo_set", args.bot, {"repo": repo})
        report = {"bot": args.bot, "repo": repo,
                  "repo_url": repo_url(repo, store.settings.github_owner)}
    elif args.command == "delete-tasks":
        from .task_delete import delete_tasks
        store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
        with store.transaction() as c:
            report = delete_tasks(c, args.ids_file.read_text().split(), apply=args.apply)
    elif args.command == "restore-tasks":
        from .task_delete import restore_tasks
        store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
        with store.transaction() as c:
            report = restore_tasks(c, args.ids_file.read_text().split())
    elif args.command == "purge-deleted-tasks":
        from .task_delete import purge_trash
        store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
        with store.transaction() as c:
            report = purge_trash(c, args.older_than_days, apply=args.apply)
    elif args.command == "usage-count":
        from .census import Census
        from .releases import version
        settings = Settings(db_path=args.database, registry_dir=registry_dir())
        store, census = Store(settings), Census(Store(settings), settings)
        if args.action == "on" or args.action == "off":
            report = census.set_enabled(args.action == "on", H.KEEPER)
        elif args.action == "reset-id":
            report = census.reset_id(H.KEEPER)
        elif args.action == "payload":
            # What would be sent right now; nothing is sent by this command.
            with store.read() as c:
                report = census.payload(c, version()) or {"sent": False, "reason": census.off_reason(c) or "notice not yet shown"}
        else:
            with store.read() as c:
                report = census.view(c)
    elif args.command == "import-history":
        store = Store(Settings(db_path=args.database))
        store.initialize()
        report = import_text_archives(store, args.runtime)
    elif args.command == "migrate-legacy":
        report = migrate_legacy(args.source, args.destination, args.runtime, args.projects,
                                queue_backlog=args.queue_legacy_backlog)
    else:
        import boto3
        s3 = boto3.client("s3")
        settings = Settings(db_path=args.database, blob_dir=args.blob_dir, blob_bucket=args.blob_bucket or "")
        report = upload_bundle(args.database, args.bucket, args.key, s3, Blobs(settings, s3))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
