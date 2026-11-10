"""Run the fault rig.

    python -m evals.never_stuck.run --seed N [--explain]           one seed; --explain prints every step
    python -m evals.never_stuck.run --seeds 1-10000 [--shard i/n] [--json] [-j N] [--allow-known]
    python -m evals.never_stuck.run --scenarios [--json]           the 52 scenarios, the bugs and the extra checks

A sweep prints one line per failed seed (its seed, step and what broke, and the known bug it matches) and a summary:
how many seeds passed, every bot behaviour, person action and fault the runs used, and the most bot runs any task used
without a change. It exits 1 when any seed failed, known bug or not; with `--allow-known` only a failure that matches no
known bug (sim.KNOWN_BUGS) fails it, so a release check can run while those bugs are open and still stop on a new one.
`--shard i/n` runs every n-th seed of the range, starting at the i-th, for a CI matrix."""

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from typing import Any


def one(seed: int, explain: bool = False) -> dict[str, Any]:
    from . import sim

    started = time.perf_counter()
    try:
        report = sim.run_seed(seed, explain=explain)
        report["ok"] = True
    except sim.RigFailure as failure:
        report = {
            "seed": seed,
            "ok": False,
            "step": failure.step,
            "what": failure.what,
            "bug": failure.bug,
            "last_steps": failure.lines,
        }
    report["seconds"] = round(time.perf_counter() - started, 2)
    return report


def seeds_of(text: str, shard: str | None) -> list[int]:
    first, _, last = text.partition("-")
    seeds = list(range(int(first), int(last or first) + 1))
    if shard:
        index, _, count = shard.partition("/")
        i, n = int(index), int(count)
        if not 1 <= i <= n:
            raise SystemExit("--shard is i/n, 1 <= i <= n")
        seeds = seeds[i - 1 :: n]
    return seeds


def sweep(seeds: list[int], jobs: int) -> dict[str, Any]:
    started = time.perf_counter()
    if jobs > 1:
        with ProcessPoolExecutor(jobs) as pool:
            reports = list(pool.map(one, seeds, chunksize=8))
    else:
        reports = [one(seed) for seed in seeds]
    used: dict[str, Counter[str]] = {
        "behaviours": Counter(),
        "faults": Counter(),
        "people": Counter(),
        "ended": Counter(),
    }
    for report in reports:
        for key, counts in used.items():
            counts.update(report.get(key) or {})
    failed = [r for r in reports if not r["ok"]]
    return {
        "seeds": len(seeds),
        "passed": len(reports) - len(failed),
        "failed": [{k: r[k] for k in ("seed", "step", "what", "bug")} for r in failed],
        "by_bug": dict(Counter(str(r["bug"] or "new") for r in failed)),
        "most_unchanged_runs": max((r.get("most_unchanged_runs", 0) for r in reports), default=0),
        "runs": sum(r.get("runs", 0) for r in reports),
        "seconds": round(time.perf_counter() - started, 1),
        **{key: dict(sorted(counts.items())) for key, counts in used.items()},
    }


def scenarios() -> dict[str, Any]:
    from . import scenarios as SC

    out: dict[str, Any] = {"scenarios": [], "bugs": [], "checks": []}
    groups = [
        ("scenarios", {str(n): c for n, c in sorted(SC.SCENARIOS.items())}),
        ("bugs", SC.BUGS),
        ("checks", SC.CHECKS),
    ]
    for group, cases in groups:
        for key, case in cases.items():
            started = time.perf_counter()
            try:
                SC.run(case)
                result, why = "pass", ""
            except SC.ScenarioFailed as failure:
                result, why = "fail", str(failure)
            expected = "fail" if (group == "bugs" or (key.isdigit() and int(key) in SC.FAILING)) else "pass"
            out[group].append(
                {
                    "case": key,
                    "name": case.name,
                    "result": result,
                    "expected": expected,
                    "bug": SC.FAILING.get(int(key)) if key.isdigit() else (key if group == "bugs" else None),
                    "why": why,
                    "seconds": round(time.perf_counter() - started, 2),
                }
            )
    out["later"] = [{"case": n, "phase": p, "why": w} for n, (p, w) in sorted(SC.LATER.items())]
    out["as_expected"] = all(c["result"] == c["expected"] for g in ("scenarios", "bugs", "checks") for c in out[g])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", type=int, help="run one seed")
    parser.add_argument("--explain", action="store_true", help="with --seed: print every step")
    parser.add_argument("--seeds", help="a range of seeds, a-b")
    parser.add_argument("--shard", help="i/n: every n-th seed of the range, from the i-th")
    parser.add_argument("-j", "--jobs", type=int, default=1, help="processes (default 1)")
    parser.add_argument("--scenarios", action="store_true", help="run the scenarios, the bugs and the extra checks")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--allow-known", action="store_true", help="with --seeds: fail only on a new failure")
    args = parser.parse_args()
    if args.seed is not None:
        report = one(args.seed, args.explain)
        if args.json:
            print(json.dumps(report, indent=2))
        elif report["ok"]:
            print(f"seed {args.seed} passed in {report['seconds']} s: {report['steps']} steps, {report['runs']} runs")
        else:
            bug = f" [known bug: {report['bug']}]" if report["bug"] else ""
            if not args.explain:
                print("\n".join(report["last_steps"]))
            print(f"seed {args.seed} failed at step {report['step']}: {report['what']}{bug}")
            print(f"replay: python -m evals.never_stuck.run --seed {args.seed} --explain")
        return 0 if report["ok"] else 1
    if args.scenarios:
        out = scenarios()
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            for group in ("scenarios", "bugs", "checks"):
                for case in out[group]:
                    mark = "ok " if case["result"] == case["expected"] else "!! "
                    bug = f" [{case['bug']}]" if case["bug"] else ""
                    why = f": {case['why']}" if case["why"] else ""
                    print(f"{mark}{group[:-1]} {case['case']}: {case['name']} -> {case['result']}{bug}{why}"[:400])
            for case in out["later"]:
                print(f"   later {case['case']}: phase {case['phase']}: {case['why']}")
        return 0 if out["as_expected"] else 1
    if not args.seeds:
        parser.error("give --seed, --seeds or --scenarios")
    out = sweep(seeds_of(args.seeds, args.shard), max(1, args.jobs))
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        for failure in out["failed"]:
            bug = f" [known bug: {failure['bug']}]" if failure["bug"] else " [NEW]"
            print(f"seed {failure['seed']} step {failure['step']}: {failure['what']}{bug}"[:600])
        print(
            f"{out['passed']} of {out['seeds']} seeds passed in {out['seconds']} s; failures by bug: {out['by_bug']}; "
            f"most bot runs without a change: {out['most_unchanged_runs']}"
        )
        for key in ("behaviours", "people", "faults", "ended"):
            print(f"{key}: {out[key]}")
    new = [f for f in out["failed"] if not f["bug"]]
    return 0 if not (new if args.allow_known else out["failed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
