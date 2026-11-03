"""Register and operate a local machine: python -m runner --help."""

import argparse
import fcntl
import json
import signal
import sys
from pathlib import Path

from clients.tico import Client
from . import importers, mail_key


# The Rust runner's own commands: `python -m runner <one>` execs it with the same arguments (runner/engine.py), so
# launchd plists, systemd units and older docs that say `python -m runner run|doctor|...` keep working.
BINARY = ("enroll", "profile", "status", "doctor", "adopt", "run")


def binary_command(argv):
    """(command, --config path) when argv names one of the Rust runner's commands, else ("", None)."""
    config, i = Path.home() / ".config/tico/runner.json", 0
    while i < len(argv):
        if argv[i] == "--config" and i + 1 < len(argv):
            config, i = Path(argv[i + 1]), i + 2
        elif argv[i].startswith("--config="):
            config, i = Path(argv[i].split("=", 1)[1]), i + 1
        else:
            return (argv[i], config) if argv[i] in BINARY else ("", None)
    return "", None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command, config_path = binary_command(argv)
    if command:
        from . import engine
        engine.exec_binary(argv, config_path=config_path if command == "run" else None)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/tico/runner.json")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in BINARY:
        sub.add_parser(name, help="The Rust runner's command (tico-runner --help)")
    sub.add_parser("catalog", help="Set up or refresh built-in bots from the catalog (JSON on stdin; for the Rust runner)")
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
    config = json.loads(args.config.read_text())
    config.setdefault("state_dir", str(args.config.parent / ("state-" + config["runner_id"])))
    mail_key.carry_protected(config, args.config)
    client = Client(config["url"], config["token"])
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
    if args.command == "catalog":
        from . import catalog_job
        return catalog_job.main(config, client)
    state_dir = args.config.parent / ("state-" + config["runner_id"])
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state_dir / f"{args.command}.lock").open("w") as lock:
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
        else:
            from .importers.service import ImporterService
            service = ImporterService(config, state_dir, backfill_days=args.backfill_days, only=args.only)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: service.stop.set())
        if args.command in ("close-calls", "importers") and args.backfill_days is not None:
            service.tick()
            return
        service.run()


if __name__ == "__main__":
    main()
