"""Run the screening eval: python -m evals.screening.run [names or numbers] [--live] [--explain].

Replay (the default) is deterministic and sends nothing: the decision model's answers come from recordings/ when there
are any, else from each input's label. Every check prints PASS or FAIL (SKIP for the model's accuracy before any
recording exists). Exit 1 on any FAIL.

--live sends the corpus through every door at Hold to the real decision model (HUB_API_URL and HUB_TOKEN, or
TYPESAFE_API_KEY) and prints the confusion table, the misses and the cost. It passes with no hold on a lookalike or
benign input and at least 90% of attacks held or warned. Exit 1 below the bar, 2 with no credentials."""
import argparse
import sys
import time

from . import harness as X
from .scenarios import SCENARIOS

NO_CREDENTIALS = ("No live decision model: set HUB_API_URL and HUB_TOKEN (the hub decides), or TYPESAFE_API_KEY "
                  "(direct). Nothing was sent. Then run: python -m evals.screening.run --live")


def fresh_org():
    org = X.Org(extra_bots=X.BOTS).__enter__()
    org.store.settings.decisions_monthly_usd = 1_000_000.0   # the eval checks the doors, not the company's cap
    return org


def run_scenario(scenario, items):
    started = time.perf_counter()
    judge = X.CorpusJudge(items)
    org = fresh_org()
    try:
        results = scenario.run(org, items, judge)
    except Exception as exc:  # noqa: BLE001 (a bug in a step fails the scenario, the rest still run)
        results = [("scenario ran", False, f"{type(exc).__name__}: {exc}")]
    finally:
        log = list(org.log)
        org.__exit__(None, None, None)
    return results, log, time.perf_counter() - started


def replay(chosen, items, explain=False):
    passed = failed = skipped = 0
    started = time.perf_counter()
    for scenario in chosen:
        results, log, seconds = run_scenario(scenario, items)
        print(f"{scenario.num:>2} {scenario.name} ({seconds:.1f}s)")
        if explain:
            print("\n".join("     " + line for line in log))
        for label, ok, detail in results:
            if ok is None:
                skipped += 1
                print(f"   SKIP {label}: {detail}")
            elif ok:
                passed += 1
                print(f"   PASS {label}")
            else:
                failed += 1
                print(f"   FAIL {label}: {detail}")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped, {time.perf_counter() - started:.0f}s")
    return 1 if failed else 0


def live_engine():
    from ..redesign.record import live_engine as engine
    return engine()


def live(items, engine, record_to=None):
    """The corpus at Hold through every door, answered by the real model. Returns (exit code, the Seen)."""
    judge = X.CorpusJudge(items, live=engine, record_to=record_to)
    org = fresh_org()
    try:
        started = time.perf_counter()
        seen = X.corpus_pass(org, items, "hold", judge)
        cost = X.one(org, "SELECT coalesce(sum(json_extract(detail,'$.cost_usd')),0) FROM events "
                          "WHERE action='judge.spend' AND target LIKE 'injection-screen@%'") or 0.0
        leaks, _ = X.audit_leaks(org, items)
    finally:
        org.__exit__(None, None, None)
    ok, lines, graded = X.grade(seen)
    unanswered = [s for s in seen if s.by not in ("recording", "live")]
    wiring = X.mismatches(seen)
    print(f"{len(seen)} (input, door) through the doors at Hold, {graded} answered by the model, "
          f"{time.perf_counter() - started:.0f}s, ${cost:.4f}")
    print("\n".join(X.table_lines(X.confusion(seen))))
    print("\n".join(lines))
    for s in unanswered:
        print(f"no model answer (timeout or error, the text went through on the patterns): {s.item['id']} via {s.door}")
    for line in wiring:
        print(f"FAIL door did not follow the verdict: {line}")
    for line in leaks:
        print(f"FAIL {line}")
    good = bool(ok) and not wiring and not leaks
    print("PASS" if good else "FAIL")
    return (0 if good else 1), seen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="scenario names or numbers (default: all)")
    parser.add_argument("--live", action="store_true", help="the real decision model: confusion table and cost")
    parser.add_argument("--explain", action="store_true", help="also print each API call a scenario made")
    args = parser.parse_args(argv)
    items = X.load_corpus()
    if args.live:
        engine = live_engine()
        if engine is None:
            print(NO_CREDENTIALS, file=sys.stderr)
            return 2
        return live(items, engine)[0]
    known = {s.name for s in SCENARIOS} | {str(s.num) for s in SCENARIOS}
    unknown = [n for n in args.names if n not in known]
    if unknown:
        parser.error("unknown scenario: " + ", ".join(unknown))
    chosen = [s for s in SCENARIOS if not args.names or s.name in args.names or str(s.num) in args.names]
    return replay(chosen, items, args.explain)


if __name__ == "__main__":
    sys.exit(main())
