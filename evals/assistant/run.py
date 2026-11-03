#!/usr/bin/env python3
"""Score the Assistant template against scripted chats from one person, with a real model. On demand only: never in CI.

    python3 evals/assistant/run.py --repo <tico checkout> [--model claude-haiku-5-5] [--judge claude-opus-5-5]
        [--only queue,approvals] [--case id,id] [--samples 2] [-j 6] [--tag base-haiku]

The template (templates/catalog/assistant) and the hub parser (clients/hubcli.py) come from --repo, so the same cases
score main against a branch. Each case is one message from the owner, Dana, as the runner sends an Assistant DM turn
(runner-rs/src/prompt.rs: "You are privately assisting ...", the personal-chat lines, then "Current message from ...").
Each sample runs in a fresh sandbox copy of the template with placeholders filled, on a fake `hub` on PATH. The fake hub
validates argv with the repo's real parser (a command that parser rejects counts as a failure), logs every call and
answers from the case's canned outputs. `--on-behalf-of` is ignored when canned outputs are matched, so a canned
answer does not depend on whether the template cites the turn itself.

Checks, per sample:
  must:      regexes; each must match some logged hub call (or the reply, written "reply:<regex>")
  must_not:  regexes; none may match a logged hub call (or the reply)
  invalid:   any hub call the real parser rejects fails the sample
  reply:     a rubric; Opus judges the final reply, the hub log and the canned record against it

Results: out/<tag>/<case>.s<n>.json per sample, and out/<tag>/summary.md with pass rate per case.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import statistics
import subprocess
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TEMPLATE = "assistant"
OWNER = "human:dana"
FILL = {"{{app_name}}": "Tico", "{{assistant_name}}": "Tico Assistant", "{{company_name}}": "Acme",
        "{{mailbox}}": "ops@acme.example"}

FAKE_HUB = r'''#!/usr/bin/env python3
import json, os, re, sys, io, contextlib
sys.path.insert(0, os.environ["EVAL_REPO"] + "/clients")
argv = sys.argv[1:]
line = " ".join(argv)
canon = re.sub(r" --on-behalf-of \S+", "", line)
words = canon.split()
log = open(os.environ["EVAL_LOG"], "a")
err, out = io.StringIO(), io.StringIO()
code = 0
try:
    import hubcli
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        hubcli.parser().parse_args(argv)
except SystemExit as e:
    code = e.code or 0
    if code == 0:  # --help
        log.write(json.dumps({"argv": line, "help": True}) + "\n")
        print(out.getvalue())
        sys.exit(0)
except Exception:  # parser import trouble: do not block the run
    pass
if code:
    log.write(json.dumps({"argv": line, "invalid": err.getvalue().strip()[-300:]}) + "\n")
    sys.stderr.write(err.getvalue())
    sys.exit(2)
files = {}
for i, a in enumerate(argv[:-1]):
    if a.endswith("-file") or a == "--file":
        try:
            files[argv[i + 1]] = open(argv[i + 1]).read()[:1500]
        except Exception:
            pass

# State: what the writes in this run changed, so later reads show it (the real hub does the same).
state_path = os.environ["EVAL_STATE"]
try:
    state = json.load(open(state_path))
except Exception:
    state = {}
state.setdefault("tasks", {})        # task id -> status
state.setdefault("automations", {})  # automation id -> enabled
WRITE_VERBS = {"update", "delete", "archive", "restore", "label", "withdraw", "answer", "approve", "decline", "done",
               "close", "cancel", "set", "remove", "add", "bulk"}


def flag_values(name):
    return [words[i + 1] for i, w in enumerate(words[:-1]) if w == name]


def write_answer(mutate):
    """The answer the real hub gives a write: the changed object. Records the change in the state when `mutate`."""
    noun, verb = words[0], (words[1] if len(words) > 1 else "")
    tid = words[2] if len(words) > 2 and not words[2].startswith("-") else None
    if noun == "task" and verb == "close" and tid:
        if mutate:
            state["tasks"][tid] = "closed"
        return {"ok": True, "task": {"id": tid, "status": "closed"}}
    if noun == "task" and verb == "update" and tid:
        status = (flag_values("--status") or [None])[-1]
        if mutate and status:
            state["tasks"][tid] = status
        return {"ok": True, "task": {"id": tid, **({"status": status} if status else {})}}
    if noun == "task" and verb == "bulk" and len(words) > 2:
        action = words[2]
        ids = flag_values("--where-id")
        dry = "--dry-run" in words
        n = len(ids) if ids else 3
        new_status = "closed" if action == "close" else (words[3] if action == "status" and len(words) > 3 else None)
        if mutate and not dry and ids and new_status:
            for i in ids:
                state["tasks"][i] = new_status
        key = "closed" if action == "close" else "changed"
        return {"ok": True, "dry_run": dry, "matched": n, key: 0 if dry else n, "ids": ids}
    if noun == "automation" and verb == "update" and tid:
        enabled = False if "--disable" in words else True if "--enable" in words else None
        if mutate and enabled is not None:
            state["automations"][tid] = enabled
        return {"ok": True, "automation": {"id": tid, **({"enabled": enabled} if enabled is not None else {})}}
    if noun == "approval" and verb == "decline" and tid:
        return {"ok": True, "approval": {"id": tid, "status": "declined"}}
    if noun == "rules" and verb == "add":
        return {"ok": True, "rule": {"id": "rule_1", "status": "active"}}
    return None


def apply_state(obj, table, field, drop=None):
    """Set `field` on each dict in obj whose id is in table; with `drop`, leave out the list items whose table value is drop."""
    if isinstance(obj, list):
        kept = []
        for item in obj:
            if isinstance(item, dict) and item.get("id") in table and drop is not None and table[item["id"]] == drop:
                continue
            kept.append(apply_state(item, table, field, drop))
        return kept
    if isinstance(obj, dict):
        obj = {k: apply_state(v, table, field, drop) for k, v in obj.items()}
        if obj.get("id") in table:
            obj[field] = table[obj["id"]]
    return obj


canned = json.load(open(os.environ["EVAL_CANNED"]))
row = next((r for r in canned if re.search(r["match"], canon)), None)
# A canned row that fails (exit non-zero) changes nothing in the state.
resp = write_answer(mutate=not (row and int(row.get("exit", 0))))
log.write(json.dumps({"argv": line, "matched": row["match"] if row else None, "files": files}) + "\n")
if row:
    out = row.get("out", "")
    if words[:2] == ["task", "list"] and not isinstance(out, str):
        keep = "--all" in words or "closed" in flag_values("--where-status") or "closed" in flag_values("--status")
        out = apply_state(out, state["tasks"], "status", None if keep else "closed")
    if words[:2] == ["automation", "show"] and not isinstance(out, str):
        out = apply_state(out, state["automations"], "enabled")
    print(out if isinstance(out, str) else json.dumps(out, indent=1))
elif resp is not None:
    print(json.dumps(resp))
else:
    verb = words[1] if len(words) > 1 else ""
    if verb in ("list", "search", "recent-runs", "runs", "held"):
        print(json.dumps({"items": [], "next_offset": None}))
    elif verb in ("create", "comment", "ask", "send"):
        print(json.dumps({"ok": True, "id": "t_" + str(abs(hash(canon)) % 100000)}))
    else:
        reply = {"ok": True}
        if verb in WRITE_VERBS and len(words) > 2 and not words[2].startswith("-"):
            reply["id"] = words[2]
        print(json.dumps(reply))
json.dump(state, open(state_path, "w"))
sys.exit(int(row.get("exit", 0)) if row else 0)
'''


def opening(background=False):
    """The standing part of an Assistant turn: the runner's opening lines, then the owner's personal-chat block, or for a
    background run (a routine the Assistant starts itself) the block that says it acts as the Assistant, not the person."""
    person = [
        f"You are privately assisting {OWNER}. ",
        "This provider session and its conversation history belong only to that person. Never copy personal preferences, "
        "messages, or attachments into shared repository files.",
    ] if not background else [
        f"This is a scheduled run for {OWNER}'s queue, not a chat. It acts as the Assistant, not as the person: a write uses "
        "only the Assistant's own rights, and anything that needs the person's rights goes on a Confirm card "
        "(`hub assistant propose`).",
    ]
    return "\n".join([
        "You are the Assistant, the AI teammate at Acme that works in Tico, the company's operating system: its tasks, "
        "conversations, approvals and schedules all live there. Your repository is your durable workspace.",
        "Read AGENT.md and state.md, then carry out the requested work in this turn.",
        "Do not end the turn with only a plan or progress update. Continue until the work is complete or genuinely "
        "blocked; if blocked, report the concrete blocker and what you verified.",
        "Use the hub CLI for all tasks, messages, approvals and status. It calls the shared cloud API.",
        "Your final answer is saved in this conversation. Do not duplicate it with hub message send unless necessary.",
        *person,
        "For live questions about running, queued, blocked, waiting, unhealthy, or approval work, run `hub health check`. "
        "Its response is already filtered to the signed-in person you represent.",
        "You may explain or propose an action, but only the human-facing Hub can record an approval decision.",
        "For strategy advice or goal changes (goal and KPI colours and reasons, the vision) ask the Strategy Bot: "
        "`hub task create --owner goal-manager --request-id turn`.",
        "For research, Market and Docs setup or library work ask the Librarian: "
        "`hub task create --owner librarian --request-id turn`.",
        "To create, change, debug, test, deploy or maintain a bot ask BotOps: `hub task create --owner botops "
        "--request-id turn`, so BotOps acts with the person's own rights. Keep this turn responsive: confirm the handoff, "
        "then report the helper's result when the task returns.",
    ])


def turn_text(case):
    """The whole prompt for one turn: the standing part, the earlier words of this chat, then the owner's message. A
    background run gets its routine's standing text instead of a person's message, as the runner sends a scheduled routine."""
    if case.get("background"):
        return "\n\n".join([opening(background=True),
                             f"Scheduled routine for {OWNER}: the assigned task's body is the routine's standing text, not a "
                             "person's message. Do what it says for this occurrence.",
                             case["prompt"].strip()])
    parts = [opening()]
    history = case.get("history") or []
    if history:
        parts.append("Earlier in this chat:\n" + "\n".join(f"{h['from']}: {h['body']}" for h in history))
    parts.append(f"Current message from {OWNER}:\n{case['prompt'].strip()}")
    return "\n\n".join(parts)


def sandbox(repo, root):
    """A copy of the template with its placeholders filled. The card and the reports stay out, as the runner leaves them."""
    src = Path(repo) / "templates/catalog" / TEMPLATE
    shutil.copytree(src, root, ignore=shutil.ignore_patterns("card.yaml", "software", "reports"))
    for f in root.rglob("*"):
        if f.is_file() and f.suffix in (".md", ".yaml", ".yml", ".json"):
            t = f.read_text()
            for k, v in FILL.items():
                t = t.replace(k, v)
            f.write_text(t)


def run_sample(case, sample, args, outdir):
    template = Path(args.repo) / "templates/catalog" / TEMPLATE
    bot = yaml.safe_load((template / "bot.yaml").read_text()) or {}
    effort = case.get("effort") or args.effort or str(bot.get("reasoning_effort") or "high")
    tag = f"{case['id']}.s{sample}"
    work = outdir / "work" / tag
    if work.exists():
        shutil.rmtree(work)
    sandbox(args.repo, work / "repo")
    g = ["git", "-C", str(work / "repo")]
    subprocess.run(g + ["init", "-q"], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["-c", "user.name=Bot", "-c", "user.email=bot@example.invalid", "commit", "-qm", "seed"], check=True)
    bindir = work / "bin"
    bindir.mkdir(parents=True)
    (bindir / "hub").write_text(FAKE_HUB)
    (bindir / "hub").chmod(0o755)
    (work / "canned.json").write_text(json.dumps(case.get("hub") or []))
    logf = work / "hub.log"
    logf.write_text("")
    (work / "hubdir/scripts").mkdir(parents=True)
    mail = work / "hubdir/scripts/mail.sh"
    mail.write_text("#!/bin/sh\necho '[]'\n")
    mail.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "EVAL_REPO": args.repo, "EVAL_LOG": str(logf),
           "EVAL_CANNED": str(work / "canned.json"), "EVAL_STATE": str(work / "state.json"), "HUB_DIR": str(work / "hubdir"), "TICO_BOT": "coo"}
    cmd = ["claude", "-p", turn_text(case), "--model", args.model, "--effort", effort, "--setting-sources", "project",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence",
           "--permission-mode", "bypassPermissions", "--output-format", "json", "--max-turns", str(args.max_turns)]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=work / "repo", env=env, capture_output=True, text=True, timeout=args.timeout, check=False)
        data = json.loads(p.stdout or "{}")
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:  # timeout, no claude binary, or bad json
        data = {"result": f"(harness error: {e})", "is_error": True}
    reply = str(data.get("result") or "")
    calls = [json.loads(l) for l in logf.read_text().splitlines() if l.strip()]
    # A `--help` lookup is not a call that changes anything: it is left out of the checks and the judge's list.
    lines = [c["argv"] for c in calls if not c.get("help")]
    attached = {k: v for c in calls for k, v in (c.get("files") or {}).items()}
    fails = []
    for rx in case.get("must") or []:
        if rx.startswith("reply:"):
            if not re.search(rx[6:], reply, re.I | re.S):
                fails.append(f"reply missing /{rx[6:]}/")
        elif not any(re.search(rx, l) for l in lines):
            fails.append(f"no call /{rx}/")
    for rx in case.get("must_not") or []:
        if rx.startswith("reply:"):
            if re.search(rx[6:], reply, re.I | re.S):
                fails.append(f"reply has /{rx[6:]}/")
        else:
            hit = [l for l in lines if re.search(rx, l)]
            if hit:
                fails.append(f"forbidden call {hit[0][:120]}")
    bad = [c for c in calls if c.get("invalid")]
    for c in bad:
        fails.append(f"invalid command: hub {c['argv'][:100]}")
    verdict = judge(case, reply, lines, attached, args)
    if not verdict.get("pass"):
        fails.append(f"judge: {verdict.get('why', '')[:300]}")
    res = {"id": case["id"], "sample": sample, "model": args.model, "effort": effort, "pass": not fails, "fails": fails,
           "score": verdict.get("score"), "why": verdict.get("why", ""), "reply": reply, "calls": lines,
           "invalid": len(bad), "turns": data.get("num_turns"), "cost_usd": data.get("total_cost_usd"),
           "secs": round(time.time() - t0)}
    (outdir / f"{tag}.json").write_text(json.dumps(res, indent=1))
    return res


def judge(case, reply, lines, attached, args):
    if not case.get("reply"):
        return {"pass": True, "score": 5, "why": "no rubric"}
    canned = json.dumps([{"matches": h["match"], "returns": h.get("out", "")} for h in case.get("hub") or []],
                        indent=1)[:4000]
    prompt = f"""You grade one turn of an AI assistant that works for one person in a company work app (Tico). Be strict but
fair: judge only against the rubric, not style. The assistant's hub CLI calls are listed; a call ending in an error was
rejected. The CANNED RECORD is what the hub returned for the calls the grader scripted; numbers in the reply must match it.

THE PERSON'S MESSAGE:
{case['prompt'].strip()}

RUBRIC (what a good turn does):
{case['reply'].strip()}

HUB CALLS THE ASSISTANT MADE (in order):
{chr(10).join('- hub ' + l for l in lines) or '(none)'}

FILES PASSED TO HUB CALLS (truncated):
{chr(10).join(f'--- {k}{chr(10)}{v}' for k, v in attached.items()) or '(none)'}

CANNED RECORD (scripted hub answers, truncated):
{canned}

THE ASSISTANT'S FINAL REPLY:
{reply.strip() or '(empty)'}

Answer with only a JSON object: {{"pass": true|false, "score": 1-5, "why": "<one or two sentences: the main miss, or why it passes>"}}"""
    cmd = ["claude", "-p", prompt, "--model", args.judge, "--effort", "medium", "--setting-sources", "project",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence",
           "--output-format", "json", "--max-turns", "1", "--disallowedTools",
           "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task"]
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=300, check=False)
        text = json.loads(p.stdout)["result"]
        return json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError, AttributeError) as e:
        return {"pass": False, "score": 0, "why": f"judge error: {e}"}


def load_cases(only="", case_ids=""):
    """Every case file under cases/. `only` names files (queue, approvals, jobs) or case id prefixes; `case_ids` exact ids."""
    cases = []
    for f in sorted((HERE / "cases").glob("*.yaml")):
        for c in yaml.safe_load(f.read_text()) or []:
            c.setdefault("group", f.stem)
            cases.append(c)
    if only:
        keep = set(only.split(","))
        cases = [c for c in cases if c["group"] in keep or any(c["id"].startswith(k) for k in keep)]
    if case_ids:
        keep = set(case_ids.split(","))
        cases = [c for c in cases if c["id"] in keep]
    return cases


def summarise(outdir, tag, model, judge, samples):
    rows = [json.loads(f.read_text()) for f in sorted(outdir.glob("*.s*.json"))]
    by_case = {}
    for r in rows:
        by_case.setdefault(r["id"], []).append(r)
    passed = sum(r["pass"] for r in rows)
    md = [f"# {tag}: {passed}/{len(rows)} sample runs pass ({passed / max(len(rows), 1):.0%}), model {model}, "
          f"judge {judge}", "",
          f"{len(by_case)} cases x up to {samples} samples. Score is the judge's 1-5 mean.", "",
          "| case | pass | score | turns | $ | first failure |", "|---|---|---|---|---|---|"]
    for cid in sorted(by_case):
        rs = by_case[cid]
        ok = sum(r["pass"] for r in rs)
        score = statistics.mean(r["score"] or 0 for r in rs)
        turns = statistics.mean(r["turns"] or 0 for r in rs)
        cost = sum(r["cost_usd"] or 0 for r in rs)
        first = next((r["fails"][0] for r in rs if r["fails"]), "")
        md.append(f"| {cid} | {ok}/{len(rs)} | {score:.1f} | {turns:.0f} | {cost:.3f} | "
                  f"{first[:160].replace('|', '/')} |")
    (outdir / "summary.md").write_text("\n".join(md) + "\n")
    return md[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="a Tico checkout: its template and hub parser are used")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--judge", default="claude-opus-5-5")
    ap.add_argument("--effort", default="", help="override the template's reasoning effort")
    ap.add_argument("--only", default="", help="comma list of case files (queue, approvals, jobs) or id prefixes")
    ap.add_argument("--case", default="", help="comma list of exact case ids")
    ap.add_argument("--samples", type=int, default=1, help="runs per case")
    ap.add_argument("--tag", default="run")
    ap.add_argument("-j", type=int, default=6)
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    args.repo = str(Path(args.repo).resolve())
    if not (Path(args.repo) / "templates/catalog" / TEMPLATE / "AGENT.md").exists():
        raise SystemExit(f"no {TEMPLATE} template under {args.repo}")
    cases = load_cases(args.only, args.case)
    if not cases:
        raise SystemExit("no cases match")
    outdir = HERE / "out" / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.s*.json"):
        old.unlink()
    jobs = [(c, s) for c in cases for s in range(1, args.samples + 1)]
    with cf.ThreadPoolExecutor(args.j) as ex:
        futures = {ex.submit(run_sample, c, s, args, outdir): (c["id"], s) for c, s in jobs}
        for fut in cf.as_completed(futures):
            cid, s = futures[fut]
            r = fut.result()
            print(("PASS " if r["pass"] else "FAIL ") + f"{cid} s{s}" +
                  ("" if r["pass"] else "  " + " | ".join(r["fails"])[:400]), flush=True)
    print(summarise(outdir, args.tag, args.model, args.judge, args.samples), flush=True)


if __name__ == "__main__":
    main()
