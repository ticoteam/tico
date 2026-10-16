#!/usr/bin/env python3
"""Replay signed cases on a candidate build or prompt, and report wins and regressions. Release preparation only.

    python3 evals/replay/run.py --url https://scratch.example --token <owner API token> --cases <private folder> \\
        [--only a,b] [--report replay.md] [--json replay.json] [--candidate "v0.3.52-rc1, AGENT.md 3f2a…"]

The server is a scratch team on the candidate build. Each case's bot must exist there (same slug), on a computer with
a real model, running the prompt you want to compare: the candidate prompt, or the original one to test a build. The
report records each bot's instructions hash so you can see which prompt actually ran.

For each case: a task is made owned by you with the case's title and body, its files attached and the people's opening
messages posted as comments; it is then handed to the bot and run. A simulated person answers each question (on the task, or a
card filed for them) with the case's `person` answers in order. The run ends when the task settles (waiting, review, ready, done, closed or
declined) and the bot is quiet, or at --timeout. It is scored with the same code as the original outcome.

Cases that are not signed by a person (`reviewed_by`) or still hold a secret-looking value are refused, and so is a
server that holds the cases' original tasks (the live team). A case whose bot never runs is reported as could not run.
Exit code 0 only when every case ran and none regressed.
"""
import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import replay  # noqa: E402

# The BotOps runner's HTTP client; loaded by path because that file is also named run.py.
_spec = importlib.util.spec_from_file_location("botops_run", HERE.parent / "botops" / "run.py")
botops_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(botops_run)
Live = botops_run.Live


def ok(server, method, path, body=None, token=None):
    code, answer = server.request(method, path, body, token)
    if code != 200:
        raise RuntimeError(f"{method} {path}: {code} {answer.get('detail') or answer.get('title') or answer}")
    return answer


def prompt_of(server, token, bot):
    snapshot = ok(server, "GET", f"bots/{bot}/instructions", token=token)
    if not snapshot.get("published"):
        raise RuntimeError(f"{bot} has no published instructions on this server yet")
    return replay.sha(snapshot["content"])


def prepare(server, token, me, case):
    """The case's opening state, owned by the caller so nothing runs until it is handed over."""
    task = case["task"]
    made = ok(server, "POST", "tasks", {"title": task["title"], "body": task.get("body") or task["title"], "owner": me,
                                        "acceptance_criteria": task.get("acceptance_criteria") or []}, token)
    tid = made["task"]["id"]
    for f in case.get("files") or []:
        ok(server, "POST", f"tasks/{tid}/files", {"name": f["name"], "text": f.get("text") or "(empty)"}, token)
    for m in case.get("conversation") or []:
        ok(server, "POST", f"tasks/{tid}/comments", {"text": m["text"]}, token)
    return tid


def hand_over(server, token, tid, bot):
    current = ok(server, "GET", f"tasks/{tid}", token=token)["task"]
    # The new owner is woken by the reassignment itself; a run-now on top could queue a second run.
    ok(server, "POST", f"tasks/{tid}", {"version": current["version"], "owner": "bot:" + bot}, token)


def outcome(server, token, tid, bot):
    """The replay as it stands, read the same way capture read the original (replay.side)."""
    detail = ok(server, "GET", f"tasks/{tid}", token=token)
    children = [ok(server, "GET", f"tasks/{c['id']}", token=token) for c in detail.get("children") or []]
    now = replay.side(detail, ok(server, "GET", f"tasks/{tid}/files", token=token), bot, children)
    return now | {"waiting_on": detail["task"].get("waiting_on")}


def choose(ask, text):
    """A structured question answered with the case's words: the option whose label they match, else as Other."""
    answers = {q["id"]: [o["label"] for o in q.get("options") or [] if o["label"].casefold() == text.strip().casefold()][:1]
               for q in ask.get("questions") or []}
    return {"answers": answers, **({"other": text} if not all(answers.values()) else {})}


def answer(server, token, me, tid, now, text):
    """The simulated person answers the bot's oldest open question to them the way a person does in the app: a
    question on the task or on a card through its answer, a card they own by finishing it, a task waiting on them
    by a comment. False when nothing waits on them."""
    mine = [q for q in now["open_questions"] if q["to"] == me]
    if mine:
        q = mine[0]
        if q["ask"]:
            ok(server, "POST", f"tasks/{q['task']}/answers", {"target": {"comment": q["id"]}, **choose(q["ask"], text)}, token)
        else:
            ok(server, "POST", f"messages/{q['id']}/answer", {"text": text}, token)
        return True
    if now["open_cards"]:
        card = ok(server, "GET", f"tasks/{now['open_cards'][0]}", token=token)["task"]
        ok(server, "POST", f"tasks/{card['id']}", {"version": card["version"], "status": "done", "note": text}, token)
        return True
    if now["status"] == "waiting" and now["waiting_on"] == me:
        ok(server, "POST", f"tasks/{tid}/comments", {"text": text}, token)
        return True
    return False


def run_one(server, token, me, case, quiet=60, timeout=1800, poll=5):
    """Replay one case and return its outcome as `measure` reads it."""
    tid = prepare(server, token, me, case)
    hand_over(server, token, tid, case["bot"])
    answers, settled_at = list(case.get("person") or []), None
    deadline = time.monotonic() + timeout
    now = outcome(server, token, tid, case["bot"])
    while time.monotonic() < deadline:
        time.sleep(poll)
        now = outcome(server, token, tid, case["bot"])
        if answers and not now["running"] and answer(server, token, me, tid, now, answers[0]["text"]):
            answers.pop(0)
            settled_at = None
            continue
        if now["status"] in replay.SETTLED and not now["running"]:
            settled_at = settled_at or time.monotonic()
            if time.monotonic() - settled_at >= quiet:
                break
        else:
            settled_at = None
    if not now["runs"]:
        raise RuntimeError(f"{case['bot']} never ran on the replayed task (is it on a computer with a model?)")
    return tid, now


def inside_repo(path):
    root = Path(__file__).resolve().parents[2]
    return (root / ".git").exists() and Path(path).resolve().is_relative_to(root)


def source_team(server, token, cases):
    """True when the server holds a case's original task: that is the live team the cases came from, not a scratch
    team, and replaying there would hand real tasks to its live bots."""
    return any(server.request("GET", f"tasks/{(c.get('source') or {}).get('task')}", token=token)[0] == 200
               for c in cases if (c.get("source") or {}).get("task"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True, help="an owner's API token on the scratch team")
    parser.add_argument("--cases", required=True, help="the folder of signed cases (kept outside this repository)")
    parser.add_argument("--only", help="comma list of case ids")
    parser.add_argument("--candidate", default="", help="what is being compared, for the report's heading")
    parser.add_argument("--report", help="write the markdown report here")
    parser.add_argument("--json", help="write every result, with transcripts, here")
    parser.add_argument("--quiet", type=int, default=60, help="seconds a settled task must stay quiet")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds before a case is scored as it stands")
    parser.add_argument("--check", action="store_true", help="only check the cases can run; replay nothing")
    args = parser.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    cases = [c for c in replay.load(args.cases) if not only or c.get("id") in only]
    refused = {c.get("id") or c["_path"]: replay.problems(c) for c in cases if replay.problems(c)}
    for name, why in refused.items():
        print(f"{name}: refused: {'; '.join(why)}")
    if args.check or not cases:
        return 1 if refused or not cases else 0
    if any(p and inside_repo(p) for p in (args.report, args.json)):
        print("write the report and results outside this repository: they hold the cases' text")
        return 1
    server = Live(args.url)
    if source_team(server, args.token, cases):
        print("this server holds the cases' original tasks: replay on a scratch team, never the live one")
        return 1
    me = ok(server, "GET", "me", token=args.token)["actor"]
    rows, prompts = [], {}
    for case in cases:
        if case.get("id") in refused or case["_path"] in refused:
            continue
        before = replay.measure(case, case["original"])
        try:
            prompts.setdefault(case["bot"], prompt_of(server, args.token, case["bot"]))
            tid, now = run_one(server, args.token, me, case, quiet=args.quiet, timeout=args.timeout)
        except (RuntimeError, OSError) as exc:     # OSError: a dropped connection or a read timeout
            rows.append({"id": case["id"], "verdict": "error", "why": [str(exc)], "before": before})
            print(f"{case['id']:30} ERROR {exc}")
            continue
        after = replay.measure(case, now)
        verdict, why = replay.compare(before, after)
        same_prompt = prompts[case["bot"]] == (case.get("prompt") or {}).get("sha256")
        rows.append({"id": case["id"], "task": tid, "verdict": verdict, "why": why, "before": before, "after": after,
                     "prompt": prompts[case["bot"]], "same_prompt": same_prompt, "transcript": now["transcript"]})
        print(f"{case['id']:30} {verdict.upper():10} {'; '.join(why)}")
    note = "Prompts: " + ", ".join(f"{bot} {digest[:12]}" for bot, digest in sorted(prompts.items()))
    text = replay.report(rows, "Behaviour replay" + (f": {args.candidate}" if args.candidate else ""), note)
    if args.report:
        Path(args.report).write_text(text)
    else:
        print("\n" + text)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, default=str))
    return 0 if rows and not any(r["verdict"] in ("regression", "error") for r in rows) and not refused else 1


if __name__ == "__main__":
    sys.exit(main())
