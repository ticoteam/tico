"""Rule check eval: python -m evals.rule_check.run [--live] [--levels low,medium,high] [--only <case id>].

cases.json holds 10 internal rules for the fictional Acme team and 32 hand-labelled bot outputs: short breaks, paraphrases
and code words, near misses (the topic without the forbidden thing, a refusal, the rule quoted), a break at the end of a
long update, and outputs to several recipients. Each output is judged against all 10 rules at once, with the state and
questions `backend/org_rule_check.py` builds (questions/rule-check.json, one noul per rule, `broken` threshold).

With no flags nothing is sent: the cases are checked and each state is validated against the question set. Exit 1 on a
problem.

--live sends every case once per level and prints, per level, the false-block rate (a rule flagged that the label says
holds) and the miss rate (a labelled break not flagged), over (output, rule) pairs and over outputs, then each mistake.
Where the answers come from, first match wins:

    TICO_GATEWAY_KEY (+ TICO_GATEWAY_URL)  the Gateway at each level, as a dev install's server asks it
    TYPESAFE_API_KEY                       Jev directly; Low only
    HUB_API_URL + HUB_TOKEN                a dev install's POST /api/v2/decisions, at the install's own Decisions level

Without any of them nothing is sent and the exit code is 2. No customer data belongs in cases.json."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from clients import judge as J

HERE = Path(__file__).parent
CASES = HERE / "cases.json"
KINDS = ("short", "break", "paraphrase", "near_miss", "end_of_long", "multi")
NO_ENGINE = ("No decision model: set TICO_GATEWAY_KEY (and TICO_GATEWAY_URL for a dev Gateway), TYPESAFE_API_KEY, or "
             "HUB_API_URL and HUB_TOKEN of a dev install. Nothing was sent. Then run: python -m evals.rule_check.run --live")


def load(path=CASES):
    return json.loads(Path(path).read_text())


def problems(data):
    """Why the cases cannot be used, as a list (empty when they can)."""
    rules = [r.get("id") for r in data.get("rules", [])]
    out = [] if len(set(rules)) == len(rules) and rules else ["rule ids missing or repeated"]
    seen = set()
    for case in data.get("cases", []):
        who = case.get("id", "?")
        missing = [k for k in ("id", "kind", "operation", "from", "to", "output", "broken") if k not in case]
        if missing:
            out.append(f"{who}: missing {', '.join(missing)}")
            continue
        if who in seen:
            out.append(f"{who}: id used twice")
        seen.add(who)
        if case["kind"] not in KINDS:
            out.append(f"{who}: kind {case['kind']!r}")
        if set(case["broken"]) - set(rules):
            out.append(f"{who}: unknown rule in broken")
        if not case["to"] or not str(case["output"]).strip():
            out.append(f"{who}: no recipient or no output")
    return out


def request(data, case):
    """(state, questions, rule ids by question id) exactly as org_rule_check.preflight builds them."""
    from backend import org_rule_check as K
    qset = J.load_set(K.QUESTION_SET)
    rules = [{"id": r["id"], "version": 1, "text": r["text"]} for r in data["rules"]]
    questions = K._questions(qset, rules)
    state = {"rules": [{"id": f"r{n}", "text": r["text"]} for n, r in enumerate(rules, 1)],
             "operation": K.OPERATIONS.get(case["operation"], case["operation"]), "output": case["output"],
             "from": case["from"], "to": case["to"], "context": case.get("context") or {}}
    return state, questions, {f"r{n}": r["id"] for n, r in enumerate(rules, 1)}, qset


def engines(levels, env=None):
    """[(level label, engine)] from the environment, or [] with nothing to ask."""
    env = os.environ if env is None else env
    key = (env.get("TICO_GATEWAY_KEY") or "").strip()
    if key:
        from backend.config import _gateway_url
        url = _gateway_url()
        return [(level, J.gateway(key, url, model=J.decide_model(level), timeout=J.decide_timeout(level)))
                for level in levels]
    typesafe = (env.get(J.KEY_ENV) or "").strip()
    if typesafe:
        return [("low", J.direct(typesafe))]
    if env.get("HUB_API_URL") and env.get("HUB_TOKEN"):
        from clients.tico import Client
        return [("install", J.through_hub(Client(env["HUB_API_URL"], env["HUB_TOKEN"], timeout=J.HUB_WAIT)))]
    return []


def score(data, engine, cases):
    """Every case through `engine`: the counts and the mistakes."""
    pairs = {"fp": 0, "neg": 0, "fn": 0, "pos": 0}
    outputs = {"blocked_clean": 0, "clean": 0, "missed": 0, "breaking": 0}
    mistakes, failed, cost = [], [], 0.0
    for case in cases:
        state, questions, ids, qset = request(data, case)
        try:
            result = engine(state, questions, qset["label"])
            answers = J.check_answers(result.get("answers"), questions)
        except J.JudgeError as exc:
            failed.append(f"{case['id']}: {exc.code}")
            continue
        cost += float((result.get("usage") or {}).get("cost_usd") or 0)
        cut = float(qset["thresholds"]["broken"])
        flagged = {ids[q] for q in questions if J.noul(answers, q) >= cut}
        labelled = set(case["broken"])
        fp, fn = flagged - labelled, labelled - flagged
        pairs["fp"] += len(fp)
        pairs["neg"] += len(ids) - len(labelled)
        pairs["fn"] += len(fn)
        pairs["pos"] += len(labelled)
        if labelled:
            outputs["breaking"] += 1
            outputs["missed"] += bool(fn)
        else:
            outputs["clean"] += 1
            outputs["blocked_clean"] += bool(fp)
        for rule in sorted(fp):
            mistakes.append(f"false block {case['id']} ({case['kind']}): {rule} at {J.noul(answers, _qid(ids, rule)):.2f}")
        for rule in sorted(fn):
            mistakes.append(f"miss {case['id']} ({case['kind']}): {rule} at {J.noul(answers, _qid(ids, rule)):.2f}")
    return pairs, outputs, mistakes, failed, cost


def _qid(ids, rule):
    return next(q for q, r in ids.items() if r == rule)


def _rate(num, den):
    return f"{num}/{den} = {100 * num / den:.1f}%" if den else "n/a"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="send each case to the decision model and score it")
    parser.add_argument("--levels", default=",".join(J.DECIDE_LEVELS), help="comma-separated, for the Gateway")
    parser.add_argument("--only", metavar="CASE_ID", help="just this case")
    args = parser.parse_args(argv)
    data = load()
    found = problems(data)
    cases = [c for c in data["cases"] if not args.only or c["id"] == args.only]
    for case in cases if not found else []:
        state, questions, _, qset = request(data, case)
        try:
            J.validate(state, questions, qset["label"])
        except J.JudgeError as exc:
            found.append(f"{case['id']}: {exc.detail}")
    if found or not cases:
        print("\n".join(found or [f"no case named {args.only}"]), file=sys.stderr)
        return 1
    if not args.live:
        print(f"{len(cases)} cases x {len(data['rules'])} rules: ok. Score them with --live.")
        return 0
    levels = [x.strip() for x in args.levels.split(",") if x.strip()]
    bad = [x for x in levels if x not in J.DECIDE_LEVELS]
    if bad:
        parser.error("unknown level: " + ", ".join(bad))
    chosen = engines(levels)
    if not chosen:
        print(NO_ENGINE, file=sys.stderr)
        return 2
    for level, engine in chosen:
        started = time.perf_counter()
        pairs, outputs, mistakes, failed, cost = score(data, engine, cases)
        print(f"\n== {level}: {len(cases)} outputs x {len(data['rules'])} rules, {time.perf_counter() - started:.0f}s"
              + (f", ${cost:.4f}" if cost else ""))
        print(f"   false block, pairs:   {_rate(pairs['fp'], pairs['neg'])}")
        print(f"   miss, pairs:          {_rate(pairs['fn'], pairs['pos'])}")
        print(f"   clean outputs held:   {_rate(outputs['blocked_clean'], outputs['clean'])}")
        print(f"   breaking outputs missed (any rule): {_rate(outputs['missed'], outputs['breaking'])}")
        for line in mistakes + [f"no answer {x}" for x in failed]:
            print("   " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
