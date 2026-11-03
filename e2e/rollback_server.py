"""One side of the rollback check (e2e/tests/test_rollback.py): the server of another git ref, run as its own process.

    populate DIR --out PATH   boots that server on a new database in DIR, with the checkout's own test fixture (its seeded
                              company and identities), runs the scenario and the direct rows, takes a backup-API copy of
                              the database (DIR/backup.sqlite: the pre-upgrade backup) and writes the ids and the answers.
    serve DB --ids PATH --out PATH
                              boots that server on a copy of the backup (a restore) and writes the same answers.

The process runs with the checkout as its working directory, so `backend` is the checkout's; this tree is on PYTHONPATH,
so `e2e` is this tree's. Both steps block outside network the way every e2e test does (backend/tests/live.py).
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _Patch:
    """The one method block_outside_network uses of pytest's monkeypatch fixture."""

    def setattr(self, target, name, value):
        setattr(target, name, value)


def block_outside_network(reason):
    spec = importlib.util.spec_from_file_location("e2e_live", ROOT / "backend" / "tests" / "live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.block_outside_network(_Patch(), reason)


def populate(workdir, out):
    from backend.tests.test_api import api   # the checkout's fixture: its seeding, bots and people
    from e2e import renames, scenario
    from e2e.normalize import normalize
    from e2e.server import serve, settings_for, snapshot

    workdir = Path(workdir)
    (workdir / "fixture").mkdir(parents=True, exist_ok=True)
    fixture = api.__wrapped__(workdir / "fixture", None)
    client = next(fixture)
    try:
        ids = scenario.populate(client)
        source = client.app.state.store.settings.db_path
        scenario.populate_direct(source, columns=renames.old_columns(ROOT / "scripts" / "cleanup" / "renames.yaml"))
        raw = workdir / "raw" / "hub.sqlite"
        snapshot(source, raw)
        names = {k: v for k, v in ids.items() if not k.startswith("_")}
        # The fixture seeds rows after its own boot: one boot of this server settles them before the answers are read.
        with serve(settings_for(workdir / "settle", raw)) as settled:
            answers = {n: normalize(e, names) for n, e in scenario.capture(settled, ids).items()}
        snapshot(raw, workdir / "backup.sqlite")
    finally:
        next(fixture, None)
    Path(out).write_text(json.dumps({"ids": ids, "answers": answers}, sort_keys=True))


def serve_copy(db, ids_path, out):
    from e2e import scenario
    from e2e.normalize import normalize
    from e2e.server import serve, settings_for

    ids = json.loads(Path(ids_path).read_text())["ids"]
    names = {k: v for k, v in ids.items() if not k.startswith("_")}
    db = Path(db)
    with serve(settings_for(db.parent / "served", db)) as client:
        if client.get("/healthz").status_code != 200:
            raise SystemExit("healthz did not answer 200")
        answers = {n: normalize(e, names) for n, e in scenario.capture(client, ids).items()}
    Path(out).write_text(json.dumps({"answers": answers}, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m e2e.rollback_server", description=__doc__.split("\n")[0])
    steps = parser.add_subparsers(dest="step", required=True)
    first = steps.add_parser("populate")
    first.add_argument("workdir")
    first.add_argument("--out", required=True)
    second = steps.add_parser("serve")
    second.add_argument("db")
    second.add_argument("--ids", required=True)
    second.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    block_outside_network("the rollback check has no network")
    if args.step == "populate":
        populate(args.workdir, args.out)
    else:
        serve_copy(args.db, args.ids, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
