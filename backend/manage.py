"""Explicit offline administration and migration. These commands do not run bots."""

import argparse
import json
import os
from pathlib import Path

import yaml

from clients.manifest import manifest_path, repo_dir

from .auth import Auth
from .archive import import_text_archives
from .backup import inspect, snapshot, upload_bundle
from .blobs import Blobs
from .config import ROOT, Settings
from .store import H, Problem, Store, repo_url
from . import bot_settings as BS


def registry_dir():
    """Offline commands seed and read the environment's own registry, not the checkout's."""
    return Path(os.environ.get("TICO_REGISTRY_DIR") or ROOT / "registry")


def bots_by_operator(c):
    """Who hosts what after an import, so an operator can check the split at a glance."""
    result = {}
    for bot, operator in c.execute("SELECT bot,owner_id FROM bot_config ORDER BY owner_id,bot"):
        result.setdefault(operator, []).append(bot)
    return result


def resolved_registry(projects, registry_dir):
    source = yaml.safe_load((registry_dir / "employees.yaml").read_text())
    entries = {}
    fields = {
        "name",
        "display_name",
        "description",
        "repo",
        "reports_to",
        "team",
        "runtime",
        "model",
        "reasoning_effort",
        "effort",
        "compact_at",
        "status",
        "max_run_minutes",
        "schedules",
        "access",
        "routines",
        "tools",
        "reads",
        "slack_channel",
        "role",
        "title",
        "order",
    }
    for entry in source["employees"]:
        slug = entry["name"]
        path = manifest_path(repo_dir(projects, slug))
        manifest = yaml.safe_load(path.read_text()) if path.exists() else {}
        merged = {**source.get("defaults", {}), **entry, **(manifest or {})}
        entries[slug] = {k: v for k, v in merged.items() if k in fields}
        entries[slug].update({"name": slug, "host": "keeper", "tasks": "hub"})
    return entries


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
    p.add_argument(
        "--registry",
        type=Path,
        default=registry_dir(),
        help="This environment's registry directory (default: $TICO_REGISTRY_DIR)",
    )
    p = sub.add_parser("set-repo", help="Point a bot at its Git repository: a name, owner/name, or an https URL")
    p.add_argument("database", type=Path)
    p.add_argument("bot")
    p.add_argument("repo")
    p = sub.add_parser(
        "usage-count",
        help="The anonymous usage count (PRIVACY.md): show it, turn it on or off, or make a new install ID",
    )
    p.add_argument("database", type=Path)
    p.add_argument("action", choices=("show", "on", "off", "reset-id", "payload"))
    p = sub.add_parser("import-history")
    p.add_argument("database", type=Path)
    p.add_argument("--runtime", type=Path, required=True)
    p = sub.add_parser("hosting-enrollment")
    p.add_argument("database", type=Path)
    p.add_argument("--owner", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--code-hash", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--status", action="store_true")
    p = sub.add_parser("enrollment")
    p.add_argument("database", type=Path)
    owner = p.add_mutually_exclusive_group(required=True)
    owner.add_argument("--owner", dest="operator", help="the roster ID or email of the human who owns the computer")
    owner.add_argument(
        "--operator", dest="operator", help=argparse.SUPPRESS
    )  # the old spelling, hidden for one release
    p.add_argument("--out", type=Path, required=True, help="New private file for the enrollment code")
    p = sub.add_parser(
        "delete-tasks",
        help="Move tasks made by mistake, with their conversations, to the trash; lists what would go unless --apply",
    )
    p.add_argument("database", type=Path)
    p.add_argument("--ids-file", type=Path, required=True, help="One task id per line")
    p.add_argument("--apply", action="store_true", help="Delete; without it nothing changes")
    p = sub.add_parser("restore-tasks", help="Put deleted tasks back from the trash")
    p.add_argument("database", type=Path)
    p.add_argument("--ids-file", type=Path, required=True, help="One task id per line")
    p = sub.add_parser(
        "purge-deleted-tasks",
        help="Remove for good what has been in the trash longer than --older-than-days; lists it unless --apply",
    )
    p.add_argument("database", type=Path)
    p.add_argument("--older-than-days", type=int, required=True)
    p.add_argument("--apply", action="store_true", help="Purge; without it nothing changes")
    p = sub.add_parser(
        "owner-email",
        help="Change the owner's sign-in email in place (the same person), e.g. to get back in "
        "after a lockout; shows what would change unless --apply",
    )
    p.add_argument("database", type=Path)
    p.add_argument("--to", required=True, help="The owner's new sign-in address")
    p.add_argument("--apply", action="store_true", help="Change it; without it nothing changes")
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
    elif args.command == "hosting-enrollment":
        from .hosting_enrollment import enrollment

        store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
        report = enrollment(store, Auth(store), args.request_id, args.code_hash, args.label, args.owner, args.status)
    elif args.command == "enrollment":
        from .models import EnrollmentRequest
        from .execution import Execution  # local: keeps execution out of the selector's fan-in

        store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
        auth = Auth(store)
        execution = Execution(store, auth)
        with store.transaction() as c:
            auth.sync_access(c)  # the owner is stored in the database; an offline command has no request to load it
            operator = args.operator
            if "@" in operator:
                row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (operator.strip().lower(),)).fetchone()
                if row:
                    operator = row["id"]
            # The offline command acts as the environment owner, never as an ambient superuser.
            code = execution.issue_enrollment(c, auth.owner_identity(c), EnrollmentRequest(operator=operator))
            # Never print a credential into the operator's shared logs.
            fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(code, stream)
        report = {"written": str(args.out), "expires": code["expires_at"]}
    elif args.command == "set-repo":
        from .models import repo_reference

        store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
        repo = repo_reference(args.repo)
        with store.transaction() as c:
            bot = (H.resolve_bot(c, args.bot) or {}).get("id", args.bot)
            row = c.execute(BS.ONE_SQL, (bot,)).fetchone()
            if not row:
                raise SystemExit("Unknown bot: " + args.bot)
            config = {**json.loads(row["config"] or "{}"), "repo": repo}
            BS.write(c, bot, config)
            c.execute("UPDATE bot_config SET repo=?,revision=revision+1 WHERE bot=?", (repo, bot))
            H.event(c, H.KEEPER, "bot.repo_set", bot, {"repo": repo})
        report = {"bot": bot, "repo": repo, "repo_url": repo_url(repo, store.settings.github_owner)}
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
    elif args.command == "owner-email":
        from . import access
        from .views import roster

        store = Store(Settings(db_path=args.database, registry_dir=registry_dir()))
        try:
            with store.transaction() as c:
                owner = access.load_owner(c, store.settings)
                # A dry run makes the same change with every check, then rolls it back.
                c.execute("SAVEPOINT owner_email")
                after = access.change_owner_email(
                    c,
                    "host",
                    roster(c),
                    owner,
                    args.to,
                    expected_revision=owner["revision"],
                    now=H.now(),
                    settings=store.settings,
                    via="host",
                )
                lists = access.load_access(c, store.settings)
                if not args.apply:
                    c.execute("ROLLBACK TO owner_email")
                c.execute("RELEASE owner_email")
        except Problem as exc:
            raise SystemExit(exc.detail) from None
        # The server reads the owner record on its next request: no restart.
        report = {
            "owner": owner["email"],
            "to": after["email"],
            "revision": after["revision"],
            "applied": args.apply,
            "allowed": lists["allowed"],
            "admins": lists["admins"],
        }
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
                report = census.payload(c, version()) or {
                    "sent": False,
                    "reason": census.off_reason(c) or "notice not yet shown",
                }
        else:
            with store.read() as c:
                report = census.view(c)
    elif args.command == "import-history":
        store = Store(Settings(db_path=args.database))
        store.initialize()
        report = import_text_archives(store, args.runtime)
    else:
        import boto3

        s3 = boto3.client("s3")
        settings = Settings(db_path=args.database, blob_dir=args.blob_dir, blob_bucket=args.blob_bucket or "")
        # A hand-run bundle carries the key files beside the database (credential.key, github-app.key, slack.key):
        # restore_bundle (python -m backend.backup) puts them back beside the restored database.
        from .company_storage import for_backup

        report = upload_bundle(
            args.database,
            args.bucket,
            args.key,
            s3,
            for_backup(Store(settings), Blobs(settings, s3)),
            key_dir=Path(args.database).resolve().parent,
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
