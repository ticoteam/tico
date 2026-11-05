"""The recorder (TESTING L4, PLAN decision 37): sends each saved input once through the decision engine and keeps the
answer as recordings/<key>.json, which ReplayJudge reads back. It needs live credentials, so the checks use a stub.

    python -m evals.redesign.record [--only <input id>] [--force]

The key is a hash of (state, questions, level): a replay of the same call finds its recording. An answer that does not
match its question types is never saved. With no credentials nothing is sent and the exit code is 2."""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from clients import judge
from .fakes import RECORDINGS, recording_key

INPUTS = RECORDINGS / "inputs"
REQUIRED = ("id", "kind", "questions", "level", "state", "note")
# Which question sets an input of each kind may use: a mail is judged by mail-triage, and so on.
KINDS = {"mail": ("mail-triage",), "slack": ("slack-route",), "listening": ("listening-item", "listening-card"),
         "task": ("stale-review",)}
# The engines from judge.from_env() take no level and answer at the default, Low (judge.py documents it as the default
# model). A higher level is refused rather than saved under a label the engine did not answer at, until one takes it.
ENGINE_LEVELS = ("low",)
NO_CREDENTIALS = ("No live judge: set HUB_API_URL and HUB_TOKEN (the hub decides, and its key stays on the server), "
                  "or TYPESAFE_API_KEY (direct to TypeSafe). Nothing was sent.")


def input_problems(data, stem):
    """Why an input cannot be recorded, as a list (empty when it can). The recorder and the scenario checks both use it,
    so they never disagree on what an input must carry."""
    if not isinstance(data, dict):
        return ["not a JSON object"]
    missing = [name for name in REQUIRED if name not in data]
    if missing:
        return ["missing " + ", ".join(missing)]
    problems = []
    if data["id"] != stem:
        problems.append("id is not the file name")
    if not isinstance(data["kind"], str) or data["kind"] not in KINDS:
        problems.append(f"unknown kind {data['kind']!r}")
    elif data["questions"] not in KINDS[data["kind"]]:
        problems.append(f"{data['questions']} is not a {data['kind']} question set")
    if data["level"] not in judge.DECIDE_LEVELS:
        problems.append(f"unknown level {data['level']!r}")
    if not str(data["note"]).strip():
        problems.append("empty note")
    if not isinstance(data["state"], dict):
        return problems + ["state is not a map"]
    try:
        qset = judge.load_set(data["questions"], root=judge.QUESTIONS_DIR)
    except judge.JudgeError as exc:
        return problems + [exc.detail]
    expected, given = set(qset["state"]), set(data["state"])
    if expected - given:
        problems.append(f"state lacks {sorted(expected - given)}")
    if given - expected:
        problems.append(f"state has fields the set does not name: {sorted(given - expected)}")
    return problems


def load_inputs(inputs_dir):
    """(id, data, problems) for each input file, by name. A file that is not JSON is a problem, not a crash."""
    loaded = []
    for path in sorted(Path(inputs_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except ValueError as exc:
            loaded.append((path.stem, None, [f"not JSON: {exc}"]))
            continue
        loaded.append((path.stem, data, input_problems(data, path.stem)))
    return loaded


def _row(stem, status, key="", detail=""):
    return {"id": stem, "status": status, "key": key, "detail": detail}


def _why(exc):
    if isinstance(exc, judge.JudgeError):
        return f"{exc.code}: {exc.detail}"
    return f"{type(exc).__name__}: {exc}"


def save(out_dir, key, meta):
    """One recording as ReplayJudge reads it: named by its key, JSON, with a trailing newline."""
    (Path(out_dir) / f"{key}.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")


def record(engine, inputs_dir, out_dir, force=False, only=None):
    """Answers each input once and saves it as out_dir/<key>.json. An input whose recording is there is skipped unless
    `force`. A failed input saves nothing and the rest still run. Returns one row per input: id, status, key, detail."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for stem, data, problems in load_inputs(inputs_dir):
        if only and stem != only:
            continue
        if problems:
            rows.append(_row(stem, "failed", detail="; ".join(problems)))
            continue
        qset = judge.load_set(data["questions"], root=judge.QUESTIONS_DIR)
        state, questions, level = data["state"], qset["questions"], data["level"]
        key = recording_key(state, questions, level)
        if (out_dir / f"{key}.json").exists() and not force:
            rows.append(_row(stem, "skipped", key, "recording exists"))
            continue
        try:
            judge.validate(state, questions, qset["label"])   # refuse before the engine does
            answer = engine(state, questions, label=qset["label"], level=level)
            if not isinstance(answer, dict):
                raise judge.JudgeError("shape", "the engine returned no object")
            answers = judge.check_answers(answer.get("answers"), questions)
        except Exception as exc:  # any failure is a row, not a crash: the other inputs still run
            rows.append(_row(stem, "failed", key, _why(exc)))
            continue
        save(out_dir, key, {"key": key, "input": stem, "questions_id": data["questions"], "level": level,
                            "model": answer.get("model"), "usage": judge.clean_usage(answer.get("usage")),
                            "ms": answer.get("ms", 0), "answers": answers,
                            "recorded": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
        rows.append(_row(stem, "recorded", key, f"{answer.get('model')}, {answer.get('ms', 0)} ms"))
    return rows


def live_engine(env=None):
    """judge.from_env() shaped for record(): `engine(state, questions, label=, level=)`. None when the environment gives
    no judge, so nothing is sent. A level other than the engines' default is refused before any call."""
    engine = judge.from_env(env)
    if engine is None:
        return None

    def ask(state, questions, label=None, level=None):
        if level not in ENGINE_LEVELS:
            raise judge.JudgeError("unconfigured", f"a {level} decision needs an engine that takes a level, and "
                                   "judge.from_env() does not yet (TESTING gap c)")
        return engine(state, questions, label=label)
    return ask


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", metavar="INPUT_ID", help="record just this input")
    parser.add_argument("--force", action="store_true", help="answer again even where a recording exists")
    args = parser.parse_args(argv)
    engine = live_engine()
    if engine is None:
        print(NO_CREDENTIALS, file=sys.stderr)
        return 2
    rows = record(engine, INPUTS, RECORDINGS, force=args.force, only=args.only)
    if not rows:
        print(f"no input named {args.only}" if args.only else f"no inputs in {INPUTS}", file=sys.stderr)
        return 1
    for row in rows:
        print(f"{row['status']:<8} {row['id']}  {row['detail']}")
    counts = {status: sum(row["status"] == status for row in rows) for status in ("recorded", "skipped", "failed")}
    print(f"\n{counts['recorded']} recorded, {counts['skipped']} skipped, {counts['failed']} failed")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
