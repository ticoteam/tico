"""Flow's live evals: python -m evals.flow.run ramble|judge [--dry-run] [-j 1|2] [--only IDS] [--out FILE].

ramble sends each case in ramble_cases.json to a real writer exactly as POST /api/v2/flow/ramble asks it (the
server's SYSTEM and prompt, one call, no retry, flow_questions.DEADLINE) and runs the answer through the server's own
filter (flow_questions.read), then scores it (evals/flow/ramble.py): `hard` must hold on every case, and a case passes
`soft` when it fills at least 3 in 4 of the sections it should, keeps every phrase it should and says none it should
not. Bar: no hard failure, no failed call, and at least RAMBLE_BAR of the cases passed. The writer comes from the
environment: FLOW_EVAL_PROVIDER (anthropic, openai, google, xai or openrouter) with that provider's key
(ANTHROPIC_API_KEY, ...) and FLOW_EVAL_MODEL; else TICO_GATEWAY_KEY (TICO_GATEWAY_URL, else Tico HQ's) on the Tico AI
Gateway, FLOW_EVAL_MODEL or the Gateway's Medium (the model the server's writer uses, updates.writer_for).

judge asks the judge model (Jev, Decide Low: TICO_GATEWAY_KEY, else clients.judge.from_env(): HUB_API_URL and
HUB_TOKEN, or TYPESAFE_API_KEY) the rubric's judgement checks (rubric.json) about each question in a JSON file
(--file, default question_examples.json: {"questions": [{question, choices, context, ...}]} or a bare list), one call
per question, and prints each verdict beside plain code's (evals/flow/quality.py). --flow judges Flow's own Strategy
questions instead. Where a question carries `expect`, the judged verdicts are scored against it. Bar: at least
JUDGE_BAR agreement, every question expected to fail one check caught, and no failed call.

These make real model calls: never in CI, on demand only, at most two at once (-j 2). --dry-run sends nothing: ramble
scores each case's careful split, and judge answers from each question's `expect` (an oracle that must pass) and as a
judge that says yes to everything (which must fail). Exit 0 at the bar, 1 below it or on unusable cases, 2 when no
model is configured (nothing was sent)."""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend import flow_questions as FQ
from backend import strategy as S
from clients import judge as J

from . import quality as Q
from . import ramble as R

HERE = Path(__file__).parent
EXAMPLES = HERE / "question_examples.json"
RAMBLE_BAR = 0.8
JUDGE_BAR = 0.85
JOBS_MAX = 2
LABEL = "flow-eval"
NO_WRITER = (
    "No writer: set FLOW_EVAL_PROVIDER, its key (ANTHROPIC_API_KEY, ...) and FLOW_EVAL_MODEL, or TICO_GATEWAY_KEY. "
    "Nothing was sent. Try --dry-run to check the harness."
)
NO_JUDGE = (
    "No judge: set TICO_GATEWAY_KEY (Jev on the Tico AI Gateway), or HUB_API_URL and HUB_TOKEN, or TYPESAFE_API_KEY. "
    "Nothing was sent. Try --dry-run to check the harness."
)


# ---- models -----------------------------------------------------------------------------------------------------
def writer(env=None):
    """write(system, user) -> the model's JSON object, as the server's writer is called; None with no configuration."""
    env = os.environ if env is None else env
    provider, model = (env.get("FLOW_EVAL_PROVIDER") or "").strip(), (env.get("FLOW_EVAL_MODEL") or "").strip()
    if provider:
        key = (env.get(J.LLM_KEYS.get(provider, "")) or "").strip() if provider in J.LLM_KEYS else ""
        return J.llm_writer(provider, key, model) if key and model else None
    key = (env.get("TICO_GATEWAY_KEY") or "").strip()
    if not key:
        return None
    from backend import models_preset
    from clients import gateway as G

    return J.gateway_writer(lambda: key, G.chat_base(env.get("TICO_GATEWAY_URL", "")), model or models_preset.pick())


def judge_engine(env=None):
    """engine(state, questions, label=) on the judge model; None with no configuration."""
    env = os.environ if env is None else env
    key = (env.get("TICO_GATEWAY_KEY") or "").strip()
    if key:
        from backend.config import _gateway_url

        return J.gateway(key, env.get("TICO_GATEWAY_URL") or _gateway_url(), model=J.GATEWAY_MODEL)
    return J.from_env(env)


def _pool(jobs, work, items):
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(work, items))


# ---- ramble -----------------------------------------------------------------------------------------------------
def ramble_one(write, case):
    """One case through `write` as the server calls it: (result, the model's raw answer or None, error or None, ms)."""
    started = time.monotonic()
    try:
        with J.within(FQ.DEADLINE):
            raw = write(FQ.SYSTEM, FQ.prompt(case["text"], case["asked"], S.DEFAULT_GUIDE))
    except Exception as exc:  # noqa: BLE001 - a failed call is a row, the other cases still run
        result = FQ.fallback(case["text"], case["asked"], "failed")
        return result, None, J.error_code(exc), round((time.monotonic() - started) * 1000)
    return R.split(raw, case), raw, None, round((time.monotonic() - started) * 1000)


def run_ramble(cases, write, jobs=1, out=None):
    rows = []
    for case, (result, raw, error, ms) in zip(cases, _pool(jobs, lambda c: ramble_one(write, c), cases), strict=True):
        hard, soft = R.hard(case, result), R.soft(case, result, raw)
        rows.append({"id": case["id"], "hard": hard, "soft": soft, "error": error, "ms": ms, "lines": result["lines"]})
        mark = "PASS" if not hard and not error and soft["passed"] else "FAIL"
        detail = f"sections {soft['sections']:.2f}"
        detail += f", missed {soft['missed']}" if soft["missed"] else ""
        detail += f", said {soft['said']}" if soft["said"] else ""
        detail += "" if soft["usable"] else ", usable wrong"
        detail += "" if soft["bets_ok"] else ", too many bets"
        detail += f", HARD {hard}" if hard else ""
        detail += f", call failed ({error})" if error else ""
        print(f"{mark} {case['id']} ({ms} ms): {detail}")
    passed = sum(not r["hard"] and not r["error"] and r["soft"]["passed"] for r in rows)
    hard = sum(bool(r["hard"]) for r in rows)
    errors = sum(bool(r["error"]) for r in rows)
    good = bool(rows) and not hard and not errors and passed >= RAMBLE_BAR * len(rows)
    print(
        f"\n{passed}/{len(rows)} cases passed, {hard} hard failures, {errors} failed calls: {'PASS' if good else 'FAIL'}"
    )
    if out:
        Path(out).write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    return 0 if good else 1


def careful_writer(cases):
    """The dry run's model: each case's careful split, found by its text."""
    by_text = {c["text"]: R.careful(c) for c in cases}

    def write(system, user):
        return by_text[json.loads(user)["answer"]]

    return write


# ---- judge ------------------------------------------------------------------------------------------------------
def load_questions(path=EXAMPLES):
    data = json.loads(Path(path).read_text())
    items = data["questions"] if isinstance(data, dict) else data
    return [{"id": str(item.get("id") or i + 1), **item} for i, item in enumerate(items)]


def flow_questions():
    """Flow's own Strategy questions as rubric items: each part's question, as an empty draft gets it."""
    names = FQ.titles(S.DEFAULT_GUIDE)
    out = []
    for key, ask in FQ.ASKS.items():
        context = f"The owner's strategy draft has nothing in {names[key]} yet; the answer is saved there, as a draft."
        out.append({"id": f"strategy/{key}", **Q.of_flow({"key": key, "title": names[key], "question": ask}, context)})
    return out


def problems(items):
    out, seen = [], set()
    for item in items:
        if not str(item.get("question") or "").strip():
            out.append(f"{item['id']}: no question")
        if item["id"] in seen:
            out.append(f"{item['id']}: id used twice")
        seen.add(item["id"])
        unknown = set(item.get("expect") or {}) - set(Q.CHECKS)
        if unknown:
            out.append(f"{item['id']}: expect names {sorted(unknown)}")
        try:
            J.validate(Q.judge_state(item), Q.judge_questions(), LABEL)
        except J.JudgeError as exc:
            out.append(f"{item['id']}: {exc.code}")
    return out


def judge_one(engine, item):
    """(verdicts, error or None, ms): one call with every judged check."""
    started = time.monotonic()
    try:
        answer = engine(Q.judge_state(item), Q.judge_questions(), label=LABEL)
        answers = J.check_answers((answer or {}).get("answers"), Q.judge_questions())
    except Exception as exc:  # noqa: BLE001 - a failed call is a row, the other questions still run
        return Q.verdicts(item, {}), J.error_code(exc), round((time.monotonic() - started) * 1000)
    return Q.verdicts(item, answers), None, round((time.monotonic() - started) * 1000)


def run_judge(items, engine, jobs=1, out=None):
    rows, agree, judged, missed = [], 0, 0, []
    for item, (verdict, error, ms) in zip(items, _pool(jobs, lambda i: judge_one(engine, i), items), strict=True):
        code, expect = Q.mechanical(item), item.get("expect") or {}
        fails = [k for k, v in verdict.items() if v is False]
        for check in Q.CHECKS:
            if code[check] is None and check in expect and verdict[check] is not None:
                judged += 1
                agree += verdict[check] == expect[check]
        if expect and not all(expect.values()) and not fails:
            missed.append(item["id"])
        rows.append({"id": item["id"], "verdicts": verdict, "mechanical": code, "error": error, "ms": ms})
        shown = ", ".join(f"{k} {'by code' if code[k] is not None else 'judged'}" for k in fails) or "passes"
        print(f"{'ERROR' if error else 'FAIL ' if fails else 'OK   '} {item['id']} ({ms} ms): {shown}")
    errors = sum(bool(r["error"]) for r in rows)
    rate = agree / judged if judged else None
    if rate is not None:
        print(f"\nagreement with expect on judged checks: {agree}/{judged} ({rate:.0%})")
    if missed:
        print(f"bad questions passed: {', '.join(missed)}")
    good = bool(rows) and not errors and not missed and (rate is None or rate >= JUDGE_BAR)
    print(f"{len(rows)} questions, {errors} failed calls: {'PASS' if good else 'FAIL'}")
    if out:
        Path(out).write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    return 0 if good else 1


def oracle(items):
    """The dry run's right judge: each question's `expect`, as nouls."""
    by_question = {i["question"]: i.get("expect") or {} for i in items}

    def engine(state, questions, label=None):
        expect = by_question[state["question"]]
        return {"answers": {q: {"noul": 1.0 if expect.get(q, True) else 0.0} for q in questions}}

    return engine


def yes_man(state, questions, label=None):
    """The dry run's wrong judge: yes to every check."""
    return {"answers": {q: {"noul": 1.0} for q in questions}}


# ---- main -------------------------------------------------------------------------------------------------------
def _only(items, only):
    names = [n.strip() for n in (only or "").split(",") if n.strip()]
    return [i for i in items if not names or i["id"] in names]


def main(argv=None, env=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=("ramble", "judge"))
    parser.add_argument("--dry-run", action="store_true", help="send nothing: check the harness with fake models")
    parser.add_argument("-j", "--jobs", type=int, default=1, help=f"calls at once, at most {JOBS_MAX}")
    parser.add_argument("--only", help="comma-separated case or question ids")
    parser.add_argument("--file", help="judge: a JSON file of questions (default question_examples.json)")
    parser.add_argument("--flow", action="store_true", help="judge: Flow's own Strategy questions")
    parser.add_argument("--out", help="write each row as JSON here")
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= JOBS_MAX:
        parser.error(f"-j is 1 to {JOBS_MAX}")
    if args.what == "ramble":
        cases = _only(R.load(), args.only)
        if not cases:
            print("no such case", file=sys.stderr)
            return 1
        write = careful_writer(cases) if args.dry_run else writer(env)
        if write is None:
            print(NO_WRITER, file=sys.stderr)
            return 2
        return run_ramble(cases, write, args.jobs, args.out)
    items = _only(flow_questions() if args.flow else load_questions(args.file or EXAMPLES), args.only)
    wrong = problems(items) or ([] if items else ["no questions"])
    if wrong:
        print("\n".join(wrong), file=sys.stderr)
        return 1
    if args.dry_run:
        right = run_judge(items, oracle(items), args.jobs)
        if not any(not all((i.get("expect") or {}).values()) for i in items):
            # Nothing here is expected to fail (Flow's own questions carry no `expect`), so a yes-man cannot be told apart.
            print(
                "\ndry run: " + ("PASS" if right == 0 else "FAIL") + " (no bad question here to catch a yes-man with)"
            )
            return right
        print("\n-- a judge that says yes to everything must fail:")
        sloppy = run_judge(items, yes_man, args.jobs)
        print("\ndry run: " + ("PASS" if right == 0 and sloppy == 1 else "FAIL"))
        return 0 if right == 0 and sloppy == 1 else 1
    engine = judge_engine(env)
    if engine is None:
        print(NO_JUDGE, file=sys.stderr)
        return 2
    return run_judge(items, engine, args.jobs, args.out)


if __name__ == "__main__":
    sys.exit(main())
