"""Each check's bar from real outcomes: python -m evals.pipeline_judge.rebuild {fetch,ask,bars} --labels CSV --cache DIR.

The labelled list is every Jev verdict on a page over a window (one CSV row each: verdict_id, judged_at, check, file_id,
page_version, verdict, p, next, next_by, task_id) and what happened next: moved on unchanged, a new version, sent back
or closed. It holds the team's own work, so it and the cache stay outside the repository; only the result (counts per
check and bar, no ids and no text) is committed, in results/, beside pipeline_judges.BARS.

  fetch  reads each page version (`tico file read`) and its task (`tico task show`) into the cache.
  ask    asks Jev each check's parts (pipeline_judges.CHECKS) about each page, the way the server asks them
         (page_verdicts.job_of, pipeline_judges._state and _questions), through `tico decision ask`: one call a page,
         about 830 calls and well under $1. Answers are kept in the cache, so a rerun asks only what it has not.
  bars   reads each check's verdicts at every bar in GRID and picks the lowest bar that keeps met-then-sent-back no
         higher than today's and unsure under UNSURE_UNDER (`choose`), and writes the result (--out).

`--context CSV` (verdict_id, context_json) gives what each verdict was judged against (the PRs' heads then, the release):
without it a page is judged against its task alone, and a part that needs the head or the release cannot be told.

Sent back is a person (or the stage's bot) saying no: a new version a bot wrote before anyone answered is a rewrite,
not a send-back. This makes real model calls, so it never runs in CI; its pure parts are tested in
backend/tests/test_pipeline_judge_eval.py."""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend import page_verdicts as V
from backend import pipeline_judges as P
from backend import pipeline_settings as S

CHECK_OF = {"Spec": "spec", "Review": "review", "QA": "qa", "Checked in production": "validate"}
NAME_OF = {"spec": "Spec page", "review": "Review page", "qa": "QA page", "validate": "Checked in production"}
RUBRIC_OF = {"spec": S.SPEC_RUBRIC, "review": S.REVIEW_RUBRIC, "qa": S.QA_RUBRIC, "validate": S.VALIDATE_RUBRIC}
# The bars tried: a part is yes at met_at or more. Below 0.5 a yes would be likelier no than yes, so the grid starts there.
GRID = tuple(round(0.5 + 0.05 * i, 2) for i in range(10))  # 0.5 .. 0.95
UNSURE_UNDER = 0.10
LABEL = "pipeline-requirement@eval-rebuild"
KEY_VERSION = 2  # bumped when what Jev is shown changes, so earlier answers are asked again
WORKERS = 4


def outcome(row):
    """What happened after the verdict: sent_back (someone said no), rewritten (a new version, no one asked), closed or
    moved_on."""
    return {"sent back": "sent_back", "new version": "rewritten", "closed": "closed"}.get(row["next"], "moved_on")


def verdict_at(check, parts, met_at, not_met_at=P.NOT_MET_AT):
    """The verdict the server gives these part probabilities at this bar (pipeline_judges._part_lines): not met when any
    part is no, unsure when any part is neither or missing, else met. No parts (the call failed) is unsure."""
    if not parts:
        return "unsure"
    said = [
        P._band(parts[part.key], (met_at, not_met_at)) if part.key in parts else "unsure" for part in P.CHECKS[check]
    ]
    return "not_met" if "no" in said else "unsure" if "unsure" in said else "met"


def measure(rows):
    """{n, met, unsure, not_met, unsure_rate, met_then_sent_back, mtsb_rate, out_of_reach} for rows of {verdict,
    outcome, reach}: a met page sent back counts unless `reach` is False (no bar could have held it), which is
    out_of_reach."""
    n = len(rows)
    met = [r for r in rows if r["verdict"] == "met"]
    back = [r for r in met if r["outcome"] == "sent_back"]
    beyond = sum(r.get("reach") is False for r in back)
    unsure = sum(r["verdict"] == "unsure" for r in rows)
    return {
        "n": n,
        "met": len(met),
        "unsure": unsure,
        "not_met": sum(r["verdict"] == "not_met" for r in rows),
        "unsure_rate": round(unsure / n, 3) if n else 0.0,
        "met_then_sent_back": len(back) - beyond,
        "mtsb_rate": round((len(back) - beyond) / len(met), 3) if met else 0.0,
        "out_of_reach": beyond,
    }


def choose(check, rows, grid=GRID):
    """Each bar's measure for one check's rows ({parts, outcome, today}: today is the verdict Jev gave then), today's,
    and the bar: the lowest in `grid` whose met-then-sent-back rate is no higher than today's and whose unsure rate is
    under UNSURE_UNDER, or None when no bar does both.

    A send-back counts against a bar only when some bar could have held it (not met or unsure there) with unsure under
    UNSURE_UNDER; one no such bar holds is out of reach, counted apart, today's included, and named in `out_of_reach`
    with its weakest part. A person who sends back the work itself, every part plainly there, is not Jev's to catch."""
    verdicts = {met_at: [verdict_at(check, r["parts"], met_at) for r in rows] for met_at in grid}
    unsure_ok = [met_at for met_at in grid if rows and verdicts[met_at].count("unsure") / len(rows) < UNSURE_UNDER]
    reach = [
        r["outcome"] != "sent_back" or any(verdicts[met_at][i] != "met" for met_at in unsure_ok)
        for i, r in enumerate(rows)
    ]
    today = measure(
        [
            {"verdict": r["today"].replace(" ", "_"), "outcome": r["outcome"], "reach": ok}
            for r, ok in zip(rows, reach, strict=True)
        ]
    )
    table = {
        met_at: measure(
            [
                {"verdict": v, "outcome": r["outcome"], "reach": ok}
                for r, v, ok in zip(rows, verdicts[met_at], reach, strict=True)
            ]
        )
        for met_at in grid
    }
    bar = next(
        (
            met_at
            for met_at in sorted(grid)
            if table[met_at]["mtsb_rate"] <= today["mtsb_rate"] and table[met_at]["unsure_rate"] < UNSURE_UNDER
        ),
        None,
    )
    beyond = []
    for r, ok in zip(rows, reach, strict=True):
        if not ok:
            weakest = min(r["parts"], key=r["parts"].get) if r["parts"] else None
            beyond.append({"weakest": weakest, "p": r["parts"].get(weakest), "today": r["today"]})
    return {
        "today": today,
        "by_bar": table,
        "bar": None if bar is None else (bar, P.NOT_MET_AT),
        "out_of_reach": beyond,
    }


# ----------------------------------------------------------------------------- the model and data side (not in CI)


def _tico(*args):
    done = subprocess.run(["tico", *args], capture_output=True, text=True, timeout=120, check=False)
    if done.returncode:
        raise RuntimeError(f"tico {args[0]} {args[1]}: {done.stderr.strip()[:200]}")
    return json.loads(done.stdout)


def load_labels(path):
    return list(csv.DictReader(Path(path).open()))


def load_context(path):
    if not path:
        return {}
    return {r["verdict_id"]: json.loads(r["context_json"] or "{}") for r in csv.DictReader(Path(path).open())}


def fetch(rows, cache):
    (cache / "pages").mkdir(parents=True, exist_ok=True)
    (cache / "tasks").mkdir(parents=True, exist_ok=True)
    missing = []
    for row in rows:
        page = cache / "pages" / f"{row['file_id']}@{row['page_version']}.json"
        task = cache / "tasks" / f"{row['task_id']}.json"
        try:
            if not page.exists():
                page.write_text(json.dumps(_tico("file", "read", row["file_id"], "--version", row["page_version"])))
            if not task.exists():
                task.write_text(json.dumps(_tico("task", "show", "--json", row["task_id"])["task"]))
        except (RuntimeError, ValueError) as exc:
            missing.append(f"{row['verdict_id']}: {exc}")
    return missing


def state_of(row, cache, context):
    """The requirement and state the server builds for this page (page_verdicts.job_of, pipeline_judges._state)."""
    check = CHECK_OF[row["check"]]
    page = json.loads((cache / "pages" / f"{row['file_id']}@{row['page_version']}.json").read_text())
    task = json.loads((cache / "tasks" / f"{row['task_id']}.json").read_text())
    # `tico file read` gives the version's stored text: a page is HTML (its styles alone can pass TEXT_CHARS), so it
    # goes through the server's loader (text_of_bytes, the words of the page) as page_verdicts._job reads it.
    raw = page.get("text") or ""
    html = raw.lstrip()[:15].lower().startswith(("<!doctype", "<html"))
    data = raw.encode()
    text, cut = P.text_of_bytes("text/html" if html else "text/markdown", data, len(data))
    prs = [
        link for link in task.get("links") or [] if link.get("kind") == "pr" and link.get("repo") and link.get("number")
    ]
    judged = {"rubric": RUBRIC_OF[check], "context": context or {"task": row["task_id"]}}
    digest = hashlib.sha256((page.get("text") or "").encode()).hexdigest()
    requirement, attachment = V.job_of(
        {"id": row.get("requirement_id") or "r", "name": NAME_OF[check]},
        {
            "title": task.get("title") or "",
            "body": task.get("body") or "",
            "version": task.get("version") or 0,
            "pipeline": {"goal": ""},
            "pipeline_id": task.get("pipeline_id"),
        },
        {"file_id": row["file_id"], "version": int(row["page_version"]), "digest": digest},
        text,
        cut,
        judged,
        # The task's PR, as the server passes it when the task links exactly one.
        {"repo": prs[0]["repo"], "number": prs[0]["number"], "detail": None} if len(prs) == 1 else None,
    )
    return check, P._state(requirement, attachment)


def _key(row, context):
    return hashlib.sha256(
        json.dumps([KEY_VERSION, row["verdict_id"], context or {}], sort_keys=True).encode()
    ).hexdigest()[:16]


def readable(rows, cache):
    """The rows whose page version is in the cache: a page deleted since it was judged cannot be asked again, and is
    left out of every count (`bars` reports how many) rather than scored as unsure."""
    return [r for r in rows if (cache / "pages" / f"{r['file_id']}@{r['page_version']}.json").exists()]


def ask(rows, cache, contexts):
    rows = readable(rows, cache)
    answers_dir = cache / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)

    def one(row):
        context = contexts.get(row["verdict_id"])
        out = answers_dir / f"{_key(row, context)}.json"
        if out.exists():
            return None
        check, state = state_of(row, cache, context)
        with tempfile.TemporaryDirectory() as tmp:
            q, s = Path(tmp) / "q.json", Path(tmp) / "s.json"
            q.write_text(json.dumps(P._questions(check)))
            s.write_text(json.dumps(state))
            for _ in range(2):
                try:
                    said = _tico(
                        "decision", "ask", "--questions-file", str(q), "--state-file", str(s), "--label", LABEL
                    )
                    break
                except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                    said = {"error": str(exc)[:200]}
        parts = {
            qid.removeprefix("part_"): P._probability((a or {}).get("noul"))
            for qid, a in (said.get("answers") or {}).items()
        }
        parts = {k: v for k, v in parts.items() if v is not None}
        cost = ((said.get("usage") or {}).get("cost_usd")) or 0.0
        out.write_text(
            json.dumps({"verdict_id": row["verdict_id"], "parts": parts, "cost_usd": cost, "error": said.get("error")})
        )
        return said.get("error")

    with ThreadPoolExecutor(WORKERS) as pool:
        return [e for e in pool.map(one, rows) if e]


def bars(rows, cache, contexts):
    by_check, cost, unanswered = {}, 0.0, 0
    kept = readable(rows, cache)
    for row in kept:
        out = cache / "answers" / f"{_key(row, contexts.get(row['verdict_id']))}.json"
        said = json.loads(out.read_text()) if out.exists() else {"parts": {}}
        cost += said.get("cost_usd") or 0.0
        unanswered += not said["parts"]
        by_check.setdefault(CHECK_OF[row["check"]], []).append(
            {"parts": said["parts"], "outcome": outcome(row), "today": row["verdict"]}
        )
    result = {check: choose(check, rows_) for check, rows_ in by_check.items()}
    return {
        "verdicts": len(rows),
        "unreadable": len(rows) - len(kept),
        "unanswered": unanswered,
        "with_context": sum(1 for r in rows if contexts.get(r["verdict_id"])),
        "cost_usd": round(cost, 4),
        "grid": list(GRID),
        "not_met_at": P.NOT_MET_AT,
        "unsure_under": UNSURE_UNDER,
        "checks": {
            check: {**r, "by_bar": {f"{k:.2f}": v for k, v in r["by_bar"].items()}}
            for check, r in sorted(result.items())
        },
    }


def table(result):
    """The result as markdown: per check, today and each bar, the chosen one marked."""
    lines = []
    for check, r in result["checks"].items():
        chosen = r["bar"][0] if r["bar"] else None
        lines += [
            f"### {NAME_OF[check]}: bar {'none meets both' if chosen is None else f'{chosen:.2f}'}",
            "",
            "| bar | verdicts | met | unsure | not met | unsure % | met then sent back | sent back, out of reach |",
            "|---|---|---|---|---|---|---|---|",
        ]
        t = r["today"]
        lines.append(
            f"| today | {t['n']} | {t['met']} | {t['unsure']} | {t['not_met']} | {t['unsure_rate']:.0%} | "
            f"{t['met_then_sent_back']} ({t['mtsb_rate']:.1%}) | {t['out_of_reach']} |"
        )
        for bar, m in r["by_bar"].items():
            mark = " **chosen**" if chosen is not None and f"{chosen:.2f}" == bar else ""
            lines.append(
                f"| {bar}{mark} | {m['n']} | {m['met']} | {m['unsure']} | {m['not_met']} | {m['unsure_rate']:.0%} | "
                f"{m['met_then_sent_back']} ({m['mtsb_rate']:.1%}) | {m['out_of_reach']} |"
            )
        for page in r.get("out_of_reach") or []:
            lines.append(
                f"- Out of reach: a page Jev called {page['today']}, weakest part {page['weakest']} at {page['p']}"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("fetch", "ask", "bars"))
    parser.add_argument("--labels", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--context")
    parser.add_argument("--only", help="checks to take, comma-separated: spec,review,qa,validate")
    parser.add_argument("--out", help="bars: write the result here as .json and .md")
    args = parser.parse_args(argv)
    rows = load_labels(args.labels)
    if args.only:
        rows = [r for r in rows if CHECK_OF[r["check"]] in args.only.split(",")]
    cache, contexts = Path(args.cache), load_context(args.context)
    if args.step == "fetch":
        problems = fetch(rows, cache)
    elif args.step == "ask":
        problems = ask(rows, cache, contexts)
    else:
        result = bars(rows, cache, contexts)
        if args.out:
            Path(args.out).with_suffix(".json").write_text(json.dumps(result, indent=1) + "\n")
            Path(args.out).with_suffix(".md").write_text(table(result) + "\n")
        print(table(result))
        problems = []
    for line in problems:
        print(line, file=sys.stderr)
    print(f"{args.step}: {len(rows)} rows, {len(problems)} problems", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
