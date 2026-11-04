"""Run the redesign scenarios: python -m evals.redesign.run [names or numbers] [--explain], or matrix [--seed N],
or the ten fake-day workflows with --workflows (TESTING L2c; not in the default run).

Each check prints PASS, or FAIL with the PLAN §6 wave it waits on ("not built yet: wave 2 · budgets"). A wave listed in
harness.BUILT prints the real reason instead. A check the CONTRACT cannot back prints as untestable. Exit 1 on any FAIL."""
import argparse
import sys
import time

from .cases import SCENARIOS
from .harness import BUILT, GAP, run_scenario

MATRIX = "matrix"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="scenario names or numbers (default: all), or 'matrix'")
    parser.add_argument("--explain", action="store_true", help="also print each API call a scenario made")
    parser.add_argument("--seed", type=int, default=1, help="seed for the matrix's all-pairs plan (default 1)")
    parser.add_argument("--workflows", action="store_true", help="run workflows 1-10 (a fake day each) instead")
    args = parser.parse_args(argv)
    if args.workflows:
        from .workflows import WORKFLOWS
        known = {w.name for w in WORKFLOWS} | {str(w.num) for w in WORKFLOWS}
        unknown = [n for n in args.names if n not in known]
        if unknown:
            parser.error("unknown workflow: " + ", ".join(unknown))
        chosen = [w for w in WORKFLOWS if not args.names or w.name in args.names or str(w.num) in args.names]
        return _run(chosen, args)
    known = {s.name for s in SCENARIOS} | {str(s.num) for s in SCENARIOS} | {MATRIX}
    unknown = [n for n in args.names if n not in known]
    if unknown:
        parser.error("unknown scenario: " + ", ".join(unknown))
    if MATRIX in args.names:
        from .cases.matrix import matrix_scenario
        from .matrix import plan
        print(f"matrix seed {args.seed}: {len(plan(args.seed))} combinations")
        chosen = [matrix_scenario(args.seed)]
    else:
        chosen = [s for s in SCENARIOS if not args.names or s.name in args.names or str(s.num) in args.names]
    return _run(chosen, args)


def _run(chosen, args):
    passed = failed = 0
    started = time.perf_counter()
    for scenario in chosen:
        results, log, seconds = run_scenario(scenario)
        print(f"{scenario.num:>2} {scenario.name} ({seconds:.1f}s)")
        if args.explain:
            print("\n".join("     " + line for line in log))
        for result in results:
            if result.ok:
                passed += 1
                print(f"   PASS {result.label}")
                continue
            failed += 1
            if result.wave == GAP:
                why = f"untestable as written: {result.text}"
            elif result.wave in BUILT:
                why = result.text
            else:
                why = f"not built yet: {result.wave} ({result.text})"
            print(f"   FAIL {result.label}: {why}")
    print(f"\n{passed} passed, {failed} failed, {time.perf_counter() - started:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
