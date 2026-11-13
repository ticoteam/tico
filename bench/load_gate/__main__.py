"""The load gate's command line (bench/load_gate/README.md).

python -m bench.load_gate run --seed 7 --out reports/gate       # this machine: server child process, then send
python -m bench.load_gate run --seed 7 --server-ssh gate-server --remote-dir /srv/tico --out reports/gate
python -m bench.load_gate serve --seed 7 --listen 0.0.0.0:8800  # the server host alone
python -m bench.load_gate send --target http://gate-server:8800 --manifest manifest.json --out reports/gate
python -m bench.load_gate compare reports/a/report.json reports/b/report.json
"""

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Fictional only (AC-8): the test registry, no update check or container probe, no cloud credentials. Unlike the test
# suite, SQLite keeps its normal sync: the gate measures the server as it is deployed.
os.environ["TICO_REGISTRY_DIR"] = str(ROOT / "backend/tests/fixtures/registry")
os.environ["TICO_UPDATE_CHECK"] = "off"
os.environ["TICO_RUNNER_CONTAINER_PROBE"] = "off"
for _name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"):
    os.environ.pop(_name, None)
os.environ.update(
    {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_CONFIG_FILE": os.devnull,
        "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "AWS_EC2_METADATA_DISABLED": "true",
    }
)

from bench.load_gate import company, gate, report as R

ADMIN_TOKEN = "ana-test"  # the fixture owner's token: reads the flight recorder


def sizes(name):
    return {"full": company.FULL, "smoke": company.SMOKE}[name]


def cmd_build(args):
    db, manifest = company.cached(args.cache, args.seed, sizes(args.size), gate.commit()[0])
    print(json.dumps({"db": str(db), "rows": manifest["rows"], "build_seconds": manifest["build_seconds"]}))


def cmd_serve(args):
    """Serve until stdin closes (a parent or ssh hung up) or SIGTERM; the first stdout line is the announcement."""
    db, manifest = company.cached(args.cache, args.seed, sizes(args.size), gate.commit()[0])
    folder = Path(args.folder or tempfile.mkdtemp(prefix="load-gate-"))
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))

    def announce(info):
        if args.manifest_out:
            Path(args.manifest_out).write_text(json.dumps(info["manifest"], indent=1))
        print(json.dumps({**info, "manifest": info["manifest"]}), flush=True)

    with gate.serve(folder, db, manifest, args.listen, not args.no_scheduler, announce):
        if args.until_stdin:
            sys.stdin.read()
        else:
            while not stop["now"]:
                time.sleep(0.5)


def levels(text):
    return [int(x) for x in text.split(",") if x]


def send_and_report(args, target, manifest, meta, read):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = gate.run_levels(
        target, manifest, levels(args.levels), args.ramp, args.hold, args.workers, args.seed, out, rest=args.rest
    )
    result = gate.assemble(runs, read, manifest, meta, gate.load_budget(args.budget))
    paths = R.dump(result, out)
    print("\n".join(str(p) for p in paths))
    return result


def meta_for(args, server):
    head, dirty = gate.commit()
    return {
        "commit": head,
        "dirty": dirty,
        "server_commit": server.get("commit"),
        "server_host": server.get("host"),
        "sender_host": gate.host(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "levels": levels(args.levels),
        "ramp_s": args.ramp,
        "hold_s": args.hold,
        "workers": args.workers,
    }


def cmd_send(args):
    manifest = json.loads(Path(args.manifest).read_text())
    server = json.loads(Path(args.server_info).read_text()) if args.server_info else {}
    send_and_report(args, args.target, manifest, meta_for(args, server), gate.http_flight(args.target, ADMIN_TOKEN))


def cmd_run(args):
    """Start the server (here as a child process, or on another host over ssh), send, report, stop the server."""
    if not args.listen:
        # A server on another host must listen where the sender can reach it; keep that port open to the sender only.
        args.listen = "0.0.0.0:8800" if args.server_ssh else "127.0.0.1:0"
    serve = [
        "-m",
        "bench.load_gate",
        "serve",
        "--seed",
        str(args.seed),
        "--size",
        args.size,
        "--cache",
        args.cache,
        "--listen",
        args.listen,
        "--until-stdin",
    ]
    if args.server_ssh:
        head, _ = gate.commit()
        remote = (
            f"cd {shlex.quote(args.remote_dir)} && git fetch -q origin && git checkout -q --detach {head} && "
            f".venv/bin/python {shlex.join(serve)}"
        )
        command = ["ssh", args.server_ssh, remote]
    else:
        command = [sys.executable, *serve]
        if args.server_cpus:
            command = ["taskset", "-c", args.server_cpus, *command]
    child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        line = child.stdout.readline()
        if not line:
            raise SystemExit("the gate's server exited before it announced itself")
        server = json.loads(line)
        target = server["url"]
        if args.server_ssh:
            target = target.replace("127.0.0.1", args.server_ssh.split("@")[-1]).replace(
                "0.0.0.0", args.server_ssh.split("@")[-1]
            )
        if args.sender_cpus:
            os.sched_setaffinity(0, {int(c) for c in _cpus(args.sender_cpus)})
        meta = meta_for(args, server)
        meta["local"] = not args.server_ssh
        if meta["local"]:
            meta["note"] = "local run: server and sender share one machine (pinned CPUs: server %s, sender %s)" % (
                args.server_cpus or "none",
                args.sender_cpus or "none",
            )
        send_and_report(args, target, server["manifest"], meta, gate.http_flight(target, ADMIN_TOKEN))
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=60)
        except subprocess.TimeoutExpired:
            child.kill()


def _cpus(text):
    for part in text.split(","):
        a, _, b = part.partition("-")
        yield from range(int(a), int(b or a) + 1)


def cmd_compare(args):
    a, b = (json.loads(Path(p).read_text()) for p in (args.a, args.b))
    rows = R.compare(a, b, args.share, args.min_requests)
    for row in rows:
        mark = "ok " if row["within"] else "OFF"
        print(
            f"{mark} level {row['level']:>3} {row['side']:<10} {row['route']:<52} {row['a']:>9} {row['b']:>9} {row['delta_ms']:+.1f}"
        )
    off = [r for r in rows if not r["within"]]
    print(f"{len(rows) - len(off)} of {len(rows)} within {args.share:.0%}")
    return 1 if off else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m bench.load_gate", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    def common(q):
        q.add_argument("--seed", type=int, default=7)
        q.add_argument("--size", choices=("full", "smoke"), default="full")
        q.add_argument("--cache", default=str(Path(tempfile.gettempdir()) / "tico-load-gate"))

    def sending(q):
        q.add_argument("--levels", default=",".join(map(str, gate.LEVELS)))
        q.add_argument("--ramp", type=float, default=60, help="seconds each level ramps up")
        q.add_argument("--hold", type=float, default=600, help="seconds each level holds steady (whole minutes)")
        q.add_argument(
            "--rest", type=float, default=30, help="seconds between levels (under a minute: computers stay awake)"
        )
        q.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 1), help="sender processes")
        q.add_argument("--out", default="load-gate-report")
        q.add_argument("--budget", help="budget lines (JSON, see README)")

    q = sub.add_parser("build", help="build the synthetic company's database once per seed and size")
    common(q)
    q.set_defaults(func=cmd_build)

    q = sub.add_parser("serve", help="serve the company on this host")
    common(q)
    q.add_argument("--listen", default="127.0.0.1:8800")
    q.add_argument("--folder")
    q.add_argument("--manifest-out")
    q.add_argument("--no-scheduler", action="store_true", help="routines stay in the data but do not fire")
    q.add_argument("--until-stdin", action="store_true", help=argparse.SUPPRESS)
    q.set_defaults(func=cmd_serve)

    q = sub.add_parser("send", help="send the levels to a server and write the report")
    common(q)
    sending(q)
    q.add_argument("--target", required=True)
    q.add_argument("--manifest", required=True)
    q.add_argument("--server-info", help="the serve command's first line, saved, for its commit and host")
    q.set_defaults(func=cmd_send)

    q = sub.add_parser("run", help="the one command: serve, send, report")
    common(q)
    sending(q)
    q.add_argument("--listen", help="the server's address: 127.0.0.1:0 locally, 0.0.0.0:8800 with --server-ssh")
    q.add_argument("--server-ssh", help="run the server on this host over ssh (its checkout at --remote-dir)")
    q.add_argument("--remote-dir", default="tico")
    q.add_argument("--server-cpus", help="local run: CPUs for the server, e.g. 0-3")
    q.add_argument("--sender-cpus", help="local run: CPUs for the sender, e.g. 4-7")
    q.set_defaults(func=cmd_run)

    q = sub.add_parser("compare", help="Measures 3 and 4: per route p95 of two reports")
    q.add_argument("a")
    q.add_argument("b")
    q.add_argument("--share", type=float, default=0.15)
    q.add_argument("--min-requests", type=int, default=R.MIN_REQUESTS)
    q.set_defaults(func=cmd_compare)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
