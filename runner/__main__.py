"""Register and operate a local machine: python -m runner --help."""

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from clients.tico import Client
from . import importers, mail_key, profiles
from .service import Runner


def profile_command(parser, args, config):
    """Subscription profiles are local to this machine; the server is never told a credential."""
    if args.action == "add":
        try:
            entry = profiles.create(args.config.parent / "profiles", args.name, args.share_operator)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        config.setdefault("profiles", {})[args.name] = entry
        config.setdefault("default_profile", args.name)   # the first profile added is the default
        args.config.write_text(json.dumps(config, indent=2))
        print(json.dumps({"profile": args.name, **entry,
                          "default_profile": config["default_profile"]}, indent=2))
        return
    if args.action == "list":
        print(json.dumps({"default_profile": config.get("default_profile", ""),
                          "profiles": config.get("profiles", {}),
                          "bot_profiles": config.get("bot_profiles", {})}, indent=2))
        return
    entry = (config.get("profiles") or {}).get(args.name)     # login and assign name one
    if not entry:
        parser.error("No such profile on this registration; add it with `profile add`")
    if args.action == "assign":
        config.setdefault("bot_profiles", {})[args.bot] = args.name
        args.config.write_text(json.dumps(config, indent=2))
        print(f"{args.bot} runs on subscription profile {args.name}")
        return
    profile = profiles.Profile(args.name, entry["dir"], entry.get("share_operator"))
    if profile.share_operator:
        parser.error(f"Profile {args.name} shares the operator's own logins; sign in normally")
    if args.runtime == "gemini":
        print(f"Gemini CLI authenticates with an API key, not an interactive login: put "
              f"GEMINI_API_KEY in {Path(config['projects_dir']) / 'secrets'}/_shared.env or "
              f"<bot>.env. This profile's Gemini sessions live in {profile.home('gemini')}.")
        return
    command = profiles.LOGIN_COMMAND[args.runtime]
    if not shutil.which(command[0]):
        parser.error(f"{command[0]} is not on PATH")
    print(f"Signing in to {args.runtime} for profile {args.name} in {profile.home(args.runtime)}")
    # Interactive on purpose: the CLI owns the browser flow and prints its own instructions.
    raise SystemExit(subprocess.run(command, env=profile.environment(args.runtime)).returncode)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/tico/runner.json")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("enroll")
    p.add_argument("--url", required=True)
    p.add_argument("--code-file", type=Path, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--projects", type=Path, required=True)
    p.add_argument("--environment", default="", help="Environment id this registration belongs to")
    p = sub.add_parser("profile", help="Subscription profiles: one provider login per directory")
    actions = p.add_subparsers(dest="action", required=True)
    a = actions.add_parser("add")
    a.add_argument("name")
    a.add_argument("--share-operator", action="store_true", help="Use the operator's own logins")
    actions.add_parser("list")
    a = actions.add_parser("login")
    a.add_argument("name")
    a.add_argument("runtime", choices=profiles.RUNTIMES)
    a = actions.add_parser("assign")
    a.add_argument("bot")
    a.add_argument("name")
    sub.add_parser("status")
    sub.add_parser("doctor", help="Check assigned repositories and executables without running bots")
    p = sub.add_parser("adopt")
    p.add_argument("--team")
    p.add_argument("--bot", action="append")
    sub.add_parser("run")
    sub.add_parser("connectors", help="Publish bounded app connector snapshots from this machine")
    sub.add_parser("connectors-doctor", help="Check local app connector prerequisites without provider calls")
    p = sub.add_parser("close-calls", help="Sync Close call and Notetaker transcripts from this machine")
    p.add_argument("--backfill-days", type=int, metavar="DAYS",
                   help="Run one bounded historical transcript sync, then exit (1–365 days)")
    sub.add_parser("close-calls-doctor", help="Check the local Close transcript credential without provider calls")
    p = sub.add_parser("importers", help="Run the meeting importers (Zoom, Google Meet, Granola) assigned to this machine")
    p.add_argument("--backfill-days", type=int, metavar="DAYS",
                   help="Run one bounded historical sync, then exit (1–365 days)")
    p.add_argument("--only", choices=sorted(importers.REGISTRY), help="Run just this importer")
    sub.add_parser("importers-doctor", help="Check which importer credentials are on this machine without provider calls")
    args = parser.parse_args(argv)
    for name in ("close-calls", "importers"):
        if args.command == name and args.backfill_days is not None and not 1 <= args.backfill_days <= 365:
            parser.error("--backfill-days must be between 1 and 365")
    if args.command == "enroll":
        if args.config.exists():
            parser.error("This machine is already configured; use a distinct --config to enroll another runner")
        code = json.loads(args.code_file.read_text())["code"]
        result = Client(args.url, "").post("runners/enroll", {"code": code, "label": args.label, "platform": sys.platform})
        config = {**result, "url": args.url, "projects_dir": str(args.projects.resolve()), "capacity": 4}
        if args.environment:
            config["environment"] = args.environment
        args.config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(args.config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(config, stream, indent=2)
        mail_key.carry_protected(config, args.config)
        print(json.dumps({"runner_id": result["runner_id"], "config": str(args.config), "operator": result["operator"]}))
        return
    config = json.loads(args.config.read_text())
    if args.command == "profile":
        return profile_command(parser, args, config)
    config.setdefault("state_dir", str(args.config.parent / ("state-" + config["runner_id"])))
    mail_key.carry_protected(config, args.config)
    client = Client(config["url"], config["token"])
    if args.command == "status":
        print(json.dumps({"runner_id": config["runner_id"], "assignments": client.get("runners/assignments"),
                          "eligible": client.get("runners/eligible")}, indent=2))
        return
    if args.command == "connectors-doctor":
        from .connectors import ConnectorPublisher
        publisher = ConnectorPublisher(config)
        publisher.ready()
        print(json.dumps({"local_prerequisites": "present", "calendar_script": str(publisher.script),
                          "mail_script": str(publisher.script),
                          "mail_credential": ("present, not remotely verified"
                                              if publisher.mail_secret() else "missing"),
                          "note": "No provider call was made. Run the connector worker for an authenticated refresh."}))
        return
    if args.command == "importers-doctor":
        from .importers.service import doctor
        print(json.dumps({"credentials_directory": str(Path(config["projects_dir"]) / "secrets"),
                          "importers": doctor(config),
                          "note": "No provider call was made and no credential was printed."}, indent=2))
        return
    if args.command == "close-calls-doctor":
        from .close_calls import CloseCallImporter
        importer = CloseCallImporter(config)
        importer.ready()
        print(json.dumps({"local_prerequisites": "present", "credential_file": str(importer.secret_path),
                          "close_credential": "present, not remotely verified",
                          "note": "No Close call was made, and the key was not printed. Run the "
                                  "close-calls worker for an authenticated pull."}))
        return
    if args.command == "doctor":
        assignments = client.get("runners/assignments")
        eligible = client.get("runners/eligible")
        # Preflight doesn't need an outbox, threads, or a running service. It does read this
        # installation's names and its onboarding record, so it gets the same client.
        service = Runner.__new__(Runner)
        service.config = config
        service.client = client
        from . import harness_tools
        service.tools = harness_tools.Harnesses(harness_tools.tools_dir(config, args.config),
                                                args.config.parent / "harnesses-doctor.json")
        service.tools.expose_path()
        service.tools.want(service.enabled_providers(), {e["config"].get("runtime") for e in assignments})
        from .container_probe import ContainerProbe
        service.container_probe = ContainerProbe(background=False)     # one run: wait for the result
        candidates = service.readiness_candidates(assignments, eligible)
        runtimes = service.runtime_report(candidates)
        rows = service.preflight(candidates, runtimes)
        readiness = service.readiness(candidates, rows, runtimes)
        from .repositories import Repositories
        repos = Repositories(config["projects_dir"], args.config.parent / ("state-" + config["runner_id"]) / "repositories.json", client)
        print(json.dumps({"runner_id": config["runner_id"], "checks": rows,
                          "readiness": readiness, "repositories": repos.inspect(),
                          "credentials_directory": str(Path(config["projects_dir"]) / "secrets"),
                          "note": "Checks repositories, runtime installation, and supported non-model sign-in status. No model calls were made."}, indent=2))
        repos.close()
        if any(not row["ready"] for row in rows):
            raise SystemExit(1)
        return
    if args.command == "adopt":
        if not args.team and not args.bot:
            parser.error("Choose --team or --bot so the assignment scope is explicit")
        eligible = client.get("runners/eligible")
        selected = [r for r in eligible if (args.team and r["team"] == args.team) or r["bot"] in (args.bot or [])]
        if not selected:
            parser.error("No eligible bots match that scope")
        for row in selected:
            if row["runner_id"] == config["runner_id"]:
                continue
            client.post("runners/adopt", {"bot": row["bot"], "expected_generation": row["generation"]})
            print("Assigned " + row["bot"] + " to this machine")
        return
    state_dir = args.config.parent / ("state-" + config["runner_id"])
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_name = {"connectors": "connectors.lock",
                 "close-calls": "close-calls.lock", "importers": "importers.lock"}.get(args.command, "runner.lock")
    with (state_dir / lock_name).open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("A runner process already holds this registration")
        if args.command == "connectors":
            from .connectors import ConnectorPublisher
            service = ConnectorPublisher(config)
        elif args.command == "close-calls":
            from .close_calls import CloseCallImporter
            service = CloseCallImporter(config, state_dir, backfill_days=args.backfill_days)
        elif args.command == "importers":
            from .importers.service import ImporterService
            service = ImporterService(config, state_dir, backfill_days=args.backfill_days, only=args.only)
        else:
            service = Runner(config, state_dir, config_path=args.config.resolve())
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: service.stop.set())
        if args.command in ("close-calls", "importers") and args.backfill_days is not None:
            service.tick()
            return
        # No launchd here: the container asks the runner to supervise the side jobs.
        side = None
        if args.command == "run" and os.environ.get("TICO_SIDE_JOBS") == "1":
            from .sidejobs import SideJobs
            side = SideJobs(config, args.config.resolve())
            side.start()
        try:
            service.run()
        finally:
            if side:
                side.stop()


if __name__ == "__main__":
    main()
