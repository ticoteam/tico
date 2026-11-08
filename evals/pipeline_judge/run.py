"""Pipeline judge matrix: python -m evals.pipeline_judge.run --models haiku-5.5,jev,sonnet [--dry-run] [--out DIR].

cases.json holds 34 labeled cases for the requirement judge (backend/pipeline_judges.py): a requirement, the attachment as
text (what a caption or OCR and the metadata say), the work's current version, and the right answer: met, not_met or
unsure. 17 are Dev (tests pass, screenshots match spec, review done) and 17 Marketing (lead has company and contact, campaign
brief approved, landing page live). Traps: a missing file, an older work_version, the wrong screen, a lead with no email, a
draft, a placeholder page, a self-review. No customer data belongs in cases.json.

Each case is one call per model. The model answers one yes/no question with a probability; the verdict is met at 0.8 or more,
not_met at 0.2 or less, and unsure between (the engine's rule: unsure passes the call up to a person and never passes the
work). A call that raises is retried once after RETRY_WAIT_S seconds; a call that fails twice is an error and fails the bar. Per
model the report gives accuracy, false "met" count (a met verdict where the label says not_met or unsure: it must be 0 to
pass), the unsure rate, the retries, cost, p50 and p95 latency; it is written as JSON and a markdown table into --out
(evals/pipeline_judge/results by default).

This makes real model calls, so it is NOT a pytest and never runs in CI. --dry-run sends nothing: it checks the cases and runs
two fake models through the same scoring (an oracle that must pass, and a sloppy one that must fail the bar).
The bar (approval criterion 3): accuracy at least 90%, no false met, an unsure rate at most the labeled unsure share plus 10
points, p95 under 30 seconds, no failed calls, and a nonempty case set. Exit 0 after a run, 1 when the cases are unusable,
2 when any requested model has no key: that run is incomplete, so nothing is sent (adapter.py says which)."""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from clients import judge as J

from . import adapter

HERE = Path(__file__).parent
CASES = HERE / "cases.json"
DOMAINS = ("dev", "marketing")
LABELS = ("met", "not_met", "unsure")
MET_AT, NOT_MET_AT = 0.8, 0.2
ACCURACY_BAR = 0.9
UNSURE_SLACK = 0.10
P95_BAR_MS = 30_000
RETRY_WAIT_S = 5  # one retry of a call that raised, after this wait
NO_ENGINE = (
    "No model has a key. See evals/pipeline_judge/adapter.py. Nothing was sent. Try --dry-run to check the harness."
)
INCOMPLETE = "INCOMPLETE RUN: no key for {names}. Nothing was sent. An approval run needs every requested model."


def load(path=CASES):
    return json.loads(Path(path).read_text())["cases"]


def problems(cases):
    """Why the cases cannot be used, as a list (empty when they can)."""
    out, seen = [], set()
    for case in cases:
        who = case.get("id", "?")
        missing = [k for k in ("id", "domain", "requirement", "work", "attachment", "expected") if k not in case]
        if missing:
            out.append(f"{who}: missing {', '.join(missing)}")
            continue
        if who in seen:
            out.append(f"{who}: id used twice")
        seen.add(who)
        if case["domain"] not in DOMAINS:
            out.append(f"{who}: domain {case['domain']!r}")
        if case["expected"] not in LABELS:
            out.append(f"{who}: expected {case['expected']!r}")
        if not (case["requirement"].get("name") and case["requirement"].get("rubric")):
            out.append(f"{who}: requirement needs a name and a rubric")
        if case["attachment"] is None and case["expected"] != "not_met":
            out.append(f"{who}: no attachment can only be not_met")
        try:
            J.validate(adapter.state_for(case), adapter.QUESTIONS, f"{adapter.QUESTION_SET}@eval")
        except J.JudgeError as exc:
            out.append(f"{who}: {exc.code}")
    for domain in DOMAINS:
        if sum(1 for c in cases if c.get("domain") == domain) < 10:
            out.append(f"{domain}: fewer than 10 cases")
    return out


def verdict(p):
    return "met" if p >= MET_AT else "not_met" if p <= NOT_MET_AT else "unsure"


def percentile(values, q):
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def score(name, judge, cases, pause=time.sleep):
    """Every case through `judge(case) -> (p, cost_usd, ms)`: one model's row and its mistakes. A call that raises is tried
    once more after RETRY_WAIT_S (`pause` is the wait, injectable); a second failure is an error."""
    rows, mistakes, errors = [], [], []
    for case in cases:
        retries = 0
        try:
            p, cost, ms = judge(case)
        except J.JudgeError:
            retries = 1
            pause(RETRY_WAIT_S)
            try:
                p, cost, ms = judge(case)
            except J.JudgeError as exc:
                errors.append(f"{case['id']}: {exc.code}")
                p, cost, ms = 0.5, 0.0, 0  # a failed call is unsure: it goes to a person and never passes the work
        got = verdict(p)
        rows.append(
            {
                "id": case["id"],
                "domain": case["domain"],
                "expected": case["expected"],
                "got": got,
                "p": p,
                "cost": cost,
                "ms": ms,
                "retries": retries,
                "trap": case.get("trap"),
            }
        )
        if got != case["expected"]:
            mistakes.append(f"{case['id']}: expected {case['expected']}, got {got} (p={p:.2f})")
    n = len(rows)
    false_met = [r["id"] for r in rows if r["got"] == "met" and r["expected"] != "met"]
    by_domain = {
        d: round(
            sum(r["got"] == r["expected"] for r in rows if r["domain"] == d)
            / max(1, sum(r["domain"] == d for r in rows)),
            3,
        )
        for d in DOMAINS
    }
    times = [r["ms"] for r in rows if r["ms"]]
    labeled_unsure = sum(c["expected"] == "unsure" for c in cases) / max(1, n)
    unsure_rate = round(sum(r["got"] == "unsure" for r in rows) / max(1, n), 3)
    accuracy = round(sum(r["got"] == r["expected"] for r in rows) / max(1, n), 3)
    p95 = percentile(times, 0.95)
    failed_bar = []
    if not n:
        failed_bar.append("no cases")
    if accuracy < ACCURACY_BAR:
        failed_bar.append(f"accuracy {accuracy:.0%} is under {ACCURACY_BAR:.0%}")
    if false_met:
        failed_bar.append(f"{len(false_met)} false met")
    if unsure_rate > labeled_unsure + UNSURE_SLACK:
        failed_bar.append(f"unsure {unsure_rate:.0%} is over the labeled {labeled_unsure:.0%} plus 10 points")
    if p95 >= P95_BAR_MS:
        failed_bar.append(f"p95 {p95} ms is 30 s or more")
    if errors:
        failed_bar.append(f"{len(errors)} failed calls")
    return {
        "model": name,
        "cases": n,
        "accuracy": accuracy,
        "accuracy_by_domain": by_domain,
        "false_met": len(false_met),
        "false_met_cases": false_met,
        "missed_met": sum(r["got"] != "met" and r["expected"] == "met" for r in rows),
        "unsure_rate": unsure_rate,
        "retries": sum(r["retries"] for r in rows),
        "errors": errors,
        "cost_usd": round(sum(r["cost"] for r in rows), 5),
        "p50_ms": round(statistics.median(times)) if times else 0,
        "p95_ms": p95,
        "bar_failures": failed_bar,
        "passes_bar": not failed_bar,
        "mistakes": mistakes,
        "rows": rows,
    }


def table(results):
    head = (
        "| Model | Accuracy | Dev | Marketing | False met | Unsure | Retries | Cost | p50 | p95 | Bar |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    return head + "".join(
        f"| {r['model']} | {r['accuracy']:.0%} | {r['accuracy_by_domain']['dev']:.0%} | {r['accuracy_by_domain']['marketing']:.0%} "
        f"| {r['false_met']} | {r['unsure_rate']:.0%} | {r['retries']} | ${r['cost_usd']:.3f} | {r['p50_ms']} ms | {r['p95_ms']} ms "
        f"| {'pass' if r['passes_bar'] else 'FAIL'} |\n"
        for r in results
    )


def fake_judges(cases):
    """Two fake models for --dry-run, keyed by case id: an oracle (always right) and a sloppy one that says met to anything
    with an attachment, which the bar must catch."""
    label = {c["id"]: c for c in cases}

    def oracle(case):
        return {"met": 0.95, "not_met": 0.05, "unsure": 0.5}[case["expected"]], 0.0, 5

    def sloppy(case):
        return (0.9 if label[case["id"]]["attachment"] else 0.05), 0.0, 5

    return {"fake-oracle": oracle, "fake-sloppy": sloppy}


def write(results, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps({"results": results}, indent=1))
    (out / f"{stamp}.md").write_text(table(results))
    return out / f"{stamp}.md"


def main(argv=None, env=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--models", default="haiku-5.5,jev,sonnet")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", help="one case id")
    parser.add_argument("--out", default=str(HERE / "results"))
    args = parser.parse_args(argv)
    cases = load()
    found = problems(cases)
    if found:
        print("\n".join(found), file=sys.stderr)
        return 1
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
    if args.dry_run:
        judges = fake_judges(cases)
    else:
        judges, skipped = {}, []
        for name in [m.strip() for m in args.models.split(",") if m.strip()]:
            engine = adapter.build_engine(name, env)
            if engine is None:
                skipped.append(name)
                continue
            judges[name] = lambda case, engine=engine, name=name: adapter.judge_once(engine, case, name)
        if skipped:
            print(INCOMPLETE.format(names=", ".join(skipped)), file=sys.stderr)
            return 2
        if not judges:
            print(NO_ENGINE, file=sys.stderr)
            return 2
    results = [score(name, judge, cases) for name, judge in judges.items()]
    print(table(results))
    for r in results:
        for line in r["mistakes"]:
            print(f"{r['model']}: {line}")
    if not args.dry_run:
        print("written:", write(results, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
