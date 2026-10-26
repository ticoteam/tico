"""Run the factory simulation: `python -m evals.factory.run [scenario ...] [--seeds 1-3] [--local [-j N]]`.

Each scenario drives the real server with simulated bots on a fake clock, then prints what happened and which of
its checks passed. A failed check that names a Dev ticket is a known gap that ticket fixes. A report (NOTE) is
for a person to read and never fails the run.

By default the run goes to RunsOn (scripts/remote.py), never this computer: the (scenario, seed) pairs are packed by
their last timings (timings.json) into groups of about TARGET_SECONDS on a 4-vCPU Spot VM each, all at once. A
short run is unlikely to be reclaimed, and a group whose VM was is run once more. `--local` runs here (and on the VM
itself), one process per scenario: each has its own server, database and clock. `--shard i/n` runs one of n groups
of the same packing, for a CI matrix."""
import argparse
import json
import os
import sys
import time
import math
import shlex
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

TIMINGS = Path(__file__).with_name("timings.json")
TARGET_SECONDS = 180    # a VM's whole share; Spot VMs are reclaimed with two minutes' notice, rarely in a short run
REMOTE_CPUS = 4
DEFAULT_SECONDS = 60    # a scenario with no timing yet


def one(name, seed, explain):
    """Run one scenario in this process: (name, seconds, summary, check results, explain lines, trace)."""
    from .scenarios import REPORT, SCENARIOS
    from .sim import Run
    scenario = next(s for s in SCENARIOS if s.name == name)
    started = time.perf_counter()
    run = Run(scenario, seed).go()
    results = []
    for check, ticket, fn in scenario.checks:
        ok, detail = fn(run)
        report = ticket == REPORT
        results.append({"check": check, "ok": ok or report, "ticket": None if report else ticket, "detail": detail,
                        "report": report, "found": not ok})
    return {"scenario": name, "seed": seed, "about": scenario.about, "seconds": round(time.perf_counter() - started, 1),
            "summary": run.summary(), "checks": results, "explain": run.explain() if explain else []}


def render(r):
    lines = [f"\n== {r['scenario']} seed {r['seed']} ({r['seconds']:.0f}s): {r['about']}",
             "   " + ", ".join(f"{k} {v}" for k, v in r["summary"].items())]
    for c in r["checks"]:
        gap = f" [{c['ticket']}]" if c["ticket"] and not c["ok"] else ""
        if c.get("report"):
            if c["found"]:
                lines.append(f"   NOTE {c['check']}: {c['detail']}")
            continue
        lines.append(f"   {'PASS' if c['ok'] else 'FAIL'} {c['check']}{gap}" + (f": {c['detail']}" if c["detail"] else ""))
    lines += ["     " + line for line in r["explain"]]
    return "\n".join(lines)


def seeds_of(text):
    """`3`, `1-3` or `1,4,7` as a list of seeds."""
    out = []
    for part in str(text).split(","):
        low, _, high = part.partition("-")
        out += range(int(low), int(high or low) + 1)
    return out


def timings():
    try:
        return json.loads(TIMINGS.read_text())
    except (OSError, ValueError):
        return {}


def pack(pairs, groups, seconds):
    """`pairs` in `groups` groups of about equal time, longest first onto the lightest group: the same packing for
    the same pairs and timings, so a CI matrix's shards cover every pair once."""
    bins = [[0.0, i, []] for i in range(groups)]
    for pair in sorted(pairs, key=lambda p: (-seconds.get(p[0], DEFAULT_SECONDS), p)):
        light = min(bins)
        light[0] += seconds.get(pair[0], DEFAULT_SECONDS)
        light[2].append(pair)
    return [b[2] for b in sorted(bins, key=lambda b: b[1]) if b[2]]


def groups_for(pairs, seconds, cpus=REMOTE_CPUS):
    total = sum(seconds.get(name, DEFAULT_SECONDS) for name, _ in pairs)
    return max(1, min(len(pairs), math.ceil(total / (TARGET_SECONDS * cpus))))


def run_here(pairs, jobs, explain, as_json):
    results = []
    if jobs <= 1 or len(pairs) == 1:
        for name, seed in pairs:
            results.append(one(name, seed, explain))
            print(json.dumps(results[-1], default=str) if as_json else render(results[-1]), flush=True)
        return results
    # A fresh interpreter per scenario: the fake clock and the server's module state are per process.
    with ProcessPoolExecutor(min(jobs, len(pairs)), mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(one, name, seed, explain) for name, seed in pairs]
        for future in as_completed(futures):
            results.append(future.result())
            print(json.dumps(results[-1], default=str) if as_json else render(results[-1]), flush=True)
    return results


def run_remote(pairs, explain, minutes):
    """Each group on its own VM, all at once; the results as the VMs print them (one JSON line per scenario)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts import remote
    groups = pack(pairs, groups_for(pairs, timings()), timings())
    print(f"remote: {len(pairs)} scenario runs on {len(groups)} VMs of {REMOTE_CPUS} vCPUs", file=sys.stderr, flush=True)

    def group(chosen):
        command = ["python", "-W", "ignore", "-m", "evals.factory.run", "--local", "--json", "-j", str(REMOTE_CPUS),
                   "--pairs", ",".join(f"{name}:{seed}" for name, seed in chosen)] + (["--explain"] if explain else [])
        for _ in range(2):       # once more when the VM never finished (reclaimed, setup failed)
            code, logs = remote.run(command, setup="python", cpus=str(REMOTE_CPUS), minutes=minutes, echo=False)
            if code is not None:
                break
        found = []
        log = logs / "output.log"
        for line in (log.read_text(errors="replace").splitlines() if log.is_file() else []):
            if line.startswith("{"):
                try:
                    found.append(json.loads(line))
                except ValueError:
                    pass
        missing = set(chosen) - {(r["scenario"], r["seed"]) for r in found}
        return found, missing, shlex.join(command), logs

    results, missing = [], []
    with ThreadPoolExecutor(len(groups)) as pool:
        for found, lost, command, logs in pool.map(group, groups):
            results += found
            if lost:
                missing.append(f"{len(lost)} of the runs in {logs}: {command}")
    for r in results:
        print(render(r), flush=True)
    return results, missing


def main(argv=None):
    from .scenarios import SCENARIOS
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="scenarios to run (default: all)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--seeds", help="several seeds: 1-3 or 1,4,7 (default: --seed)")
    parser.add_argument("--local", action="store_true", help="run on this computer instead of RunsOn")
    parser.add_argument("--shard", help="i/n: run group i of n of the packing (for a CI matrix; implies --local)")
    parser.add_argument("--pairs", help=argparse.SUPPRESS)       # name:seed,... chosen by a remote run
    parser.add_argument("--minutes", type=int, default=15, help="time limit for each remote VM")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4, help="scenarios at once (1: in order)")
    parser.add_argument("--json", action="store_true", help="print one JSON object per scenario")
    parser.add_argument("--explain", action="store_true", help="say where each unfinished ticket stands")
    parser.add_argument("--save-timings", action="store_true", help="write this run's times to timings.json")
    args = parser.parse_args(argv)
    known = {s.name for s in SCENARIOS}
    unknown = set(args.names) - known
    if unknown:
        parser.error("unknown scenario: " + ", ".join(sorted(unknown)))
    names = [s.name for s in SCENARIOS if not args.names or s.name in args.names]
    if args.pairs:
        pairs = [(name, int(seed)) for name, seed in (p.rsplit(":", 1) for p in args.pairs.split(","))]
        if {name for name, _ in pairs} - known:
            parser.error("unknown scenario in --pairs")
    else:
        pairs = [(name, seed) for seed in (seeds_of(args.seeds) if args.seeds else [args.seed]) for name in names]
    if args.shard:
        index, _, count = args.shard.partition("/")
        if not (index.isdigit() and count.isdigit() and 1 <= int(index) <= int(count)):
            parser.error("--shard is i/n, 1 <= i <= n")
        packed = pack(pairs, int(count), timings())
        pairs = packed[int(index) - 1] if int(index) <= len(packed) else []
    started = time.perf_counter()
    missing = []
    if args.local or args.shard or args.pairs:
        results = run_here(pairs, args.jobs, args.explain, args.json)
    else:
        results, missing = run_remote(pairs, args.explain, args.minutes)
    if args.save_timings and results:
        times = timings()
        for name in {r["scenario"] for r in results}:
            times[name] = max(r["seconds"] for r in results if r["scenario"] == name)
        TIMINGS.write_text(json.dumps(dict(sorted(times.items())), indent=1) + "\n")
    failed = sum(not c["ok"] for r in results for c in r["checks"])
    if not args.json:
        print(f"\n{len(results)} scenario runs in {time.perf_counter() - started:.0f}s; "
              f"{sum(all(c['ok'] for c in r['checks']) for r in results)} passed, {failed} failed checks")
        for r in sorted(results, key=lambda r: (r["seed"], names.index(r["scenario"]) if r["scenario"] in names else 0)):
            bad = [c for c in r["checks"] if not c["ok"]]
            if bad:
                print(f"   FAIL {r['scenario']:24} seed {r['seed']}: "
                      + "; ".join(f"{c['check']} [{c['ticket'] or '-'}]" for c in bad))
        for line in missing:
            print(f"   NO RESULT {line}")
    return 1 if failed or missing else 0


if __name__ == "__main__":
    sys.exit(main())
