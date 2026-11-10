"""Behaviour replay: re-run a finished task on a candidate build or prompt and score it against what happened.

A case is one real task: its opening state (title, body, the people's messages and the files it had before the bot's
first run, and the bot's prompt version) and its original outcome (final status, what the bot said, the files it
attached, runs and minutes). capture.py writes a draft case from a finished task; a person reads it, cuts anything
private and signs it with `reviewed_by`. run.py replays signed cases on a scratch team and writes the report.

Both sides are scored by the same code: the BotOps scorers (evals/botops/score.py) for jargon, duplicates and settings
detours, and the case's `done` checks. So an edit the reviewing person makes to the original transcript applies to the
baseline too. Pure and stdlib-only apart from yaml, so the scripted test (backend/tests/test_replay_evals.py) and the
live runner score the same way.

Real cases hold a customer's words and live outside this public repository (`--cases`); `cases/` here holds only a
made-up example.
"""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "botops"))
import score  # (the BotOps scorers)

# A task is finished in one of these. A replayed task has also settled when its turn is a person's (the bot asked
# someone and stopped), once its bot is not running.
SETTLED = ("done", "closed")
# What a person answers on a task: a question the bot asked them, or an OK (an ask, or the stage's own).
ASKS = ("answer", "ok")
# Lower is better for each; minutes are reported but too noisy to decide a verdict.
NUMBERS = ("person_steps", "jargon", "duplicates", "sent_elsewhere", "runs")
# Common shapes of what a case may never hold once signed: provider keys, bearer tokens, a password, a password in a
# URL, an email address, a phone number. A token with no telling shape (Tico's own are random text) is not caught:
# the person's signature is the real gate.
SECRET = re.compile(
    r"(?<![\w-])(sk-[A-Za-z0-9_-]{16,}|[sr]k_live_[A-Za-z0-9]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|A[KS]IA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.|[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}|"
    r"(?i:(?:password|passwd|secret|api[_-]?key|aws_secret_access_key)\s*[:=]\s*\S{6,})|"
    r"https?://[^/\s:@]+:[^/\s@]+@|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\+\d[\d ().-]{8,}\d)"
)


def sha(text):
    return hashlib.sha256(str(text or "").encode()).hexdigest()


def when(stamp):
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def load(folder):
    """Every case in `folder`, sorted by file name."""
    return [yaml.safe_load(p.read_text()) | {"_path": str(p)} for p in sorted(Path(folder).glob("*.yaml"))]


def secrets_in(value):
    """Each secret-looking string anywhere in a case (keys, tokens, emails), for capture and the run's refusal."""
    if isinstance(value, dict):
        return [hit for v in value.values() for hit in secrets_in(v)]
    if isinstance(value, (list, tuple)):
        return [hit for v in value for hit in secrets_in(v)]
    return [m.group(0) for m in SECRET.finditer(value)] if isinstance(value, str) else []


def problems(case):
    """Why a case may not run: unsigned, or still holding a secret, or missing what a replay needs."""
    found = []
    if not str(case.get("reviewed_by") or "").strip():
        found.append("no reviewed_by: a person has not read and redacted it")
    for key in ("id", "bot", "task", "original"):
        if not case.get(key):
            found.append(f"no {key}")
    if not (case.get("task") or {}).get("title"):
        found.append("no task title")
    hits = secrets_in({k: v for k, v in case.items() if k != "_path"})
    if hits:
        found.append(f"{len(hits)} secret-looking value(s) left, e.g. {hits[0][:12]}…")
    return found


# ------------------------------------------------------------------ capture: a finished task -> a draft case
def _person(actor):
    return str(actor or "").startswith("human:")


def questions(detail, bot):
    """What waits on a person on one task: each question or OK of its stage that a person answers (the bot's asks
    and the stage's own OKs), with whether it is still open, in the task's order. Read from the task's `to_leave`
    (docs/pipelines-v3.md: requirements and asks)."""
    task = detail.get("task") or {}
    # An answered ask names who answered it; an open one, who it waits on.
    who = lambda r: r.get("who") or (r.get("answer") or {}).get("by")  # noqa: E731
    return [
        {
            "id": r["requirement_id"],
            "task": task.get("id"),
            "to": who(r),
            "kind": r["kind"],
            "choices": r.get("choices") or [],
            "by_bot": r.get("asked_by") == "bot:" + bot,
            "open": not r.get("met"),
        }
        for r in task.get("to_leave") or []
        if r.get("kind") in ASKS and _person(who(r))
    ]


def filed(child_details, bot):
    """The tasks filed under this one that put work to a person: owned by a person, or holding an ask of the bot's to
    one. Each is one step for the person."""
    return [
        d for d in child_details if _person(d["task"].get("owner_id")) or any(q["by_bot"] for q in questions(d, bot))
    ]


def turn_of(detail):
    """Whose turn the task is (docs/pipelines-v3.md "turn"): the person it waits on, or None."""
    turn = (detail.get("task") or {}).get("turn") or {}
    return turn.get("who") if turn.get("state") == "person" else None


def settled(outcome):
    """A replay can stop here once its bot is quiet: the task is finished, or it is a person's turn."""
    return outcome["status"] in SETTLED or bool(outcome.get("turn"))


def bot_runs(detail, bot):
    """The case's bot's runs on the task, oldest first: another bot's turns on it are not this bot's work."""
    return sorted(
        (r for r in detail.get("runs") or [] if r.get("started_at") and r.get("bot") in (bot, "bot:" + bot)),
        key=lambda r: r["started_at"],
    )


def attached(files, bot):
    """Names of the files the bot attached or changed: any version by the bot. The same rule on both sides."""
    return [
        f["name"] for f in files.get("files") or [] if any(v.get("by") == "bot:" + bot for v in f.get("versions") or [])
    ]


def side(detail, files, bot, child_details=()):
    """One side's outcome, cut at the bot's first run: what the bot and people said after it, the files the bot
    attached, its runs and minutes, and the person's steps (their messages after the cut, each ask of the bot's to a
    person on the task, and each task filed for a person under it). `child_details` are the filed tasks' details.
    Capture and the replay both read a task through this, so both are counted alike."""
    runs = bot_runs(detail, bot)
    start = when(runs[0]["started_at"]) if runs else None
    seen, transcript = set(), []
    for m in sorted((detail.get("messages") or []) + (detail.get("comments") or []), key=lambda m: m["created_at"]):
        if (
            m["id"] in seen
            or m.get("kind") == "notice"
            or m.get("deleted_at")
            or not start
            or when(m["created_at"]) < start
        ):
            continue
        seen.add(m["id"])
        if m["sender_id"] == "bot:" + bot:
            transcript.append(["bot", m["body"]])
        elif _person(m["sender_id"]):
            transcript.append(["person", m["body"]])
    minutes = (
        sum((when(r["finished_at"]) - when(r["started_at"])).total_seconds() for r in runs if r.get("finished_at")) / 60
    )
    under = filed(child_details, bot)
    open_questions = [q for d in (detail, *under) for q in questions(d, bot) if q["open"]]
    asks = [q for q in questions(detail, bot) if q["by_bot"]]
    return {
        "status": detail["task"]["status"],
        "turn": turn_of(detail),
        "transcript": transcript,
        "files": attached(files, bot),
        "runs": len(runs),
        "minutes": round(minutes, 1),
        "person_steps": sum(1 for who, _ in transcript if who == "person") + len(asks) + len(under),
        "open_questions": open_questions,
        "open_filed": [
            d["task"]["id"] for d in under if _person(d["task"].get("owner_id")) and d["task"].get("status") == "open"
        ],
        "running": not runs or any(not r.get("finished_at") for r in runs),
    }


def as_started(detail, start):
    """The task's title, body and acceptance criteria as they were at `start`: an edit made after it is outcome."""
    task, value = detail["task"], {}
    for e in sorted(detail.get("events") or [], key=lambda e: e["created_at"]):
        if e.get("field") in ("title", "body", "acceptance_criteria", "acceptance") and when(e["created_at"]) >= start:
            value.setdefault("acceptance_criteria" if e["field"].startswith("acceptance") else e["field"], e.get("old"))
    criteria = value.get("acceptance_criteria", task.get("acceptance_criteria") or [])
    if isinstance(criteria, str):
        try:
            criteria = json.loads(criteria)
        except ValueError:
            criteria = [criteria]
    return {
        "title": value.get("title") or task["title"],
        "body": value.get("body", task.get("body")) or "",
        "acceptance_criteria": criteria or [],
    }


def split(detail, files, instructions, bot=None, child_details=()):
    """A draft case from a task's detail (GET tasks/{id}), its files (GET tasks/{id}/files) and its bot's
    instructions snapshot (GET bots/{bot}/instructions). The opening state is the task as it was and everything a
    person wrote or attached before the bot's first run; the original outcome is what followed."""
    task = detail["task"]
    bot = bot or str(task["owner_id"]).removeprefix("bot:")
    runs = bot_runs(detail, bot)
    if not runs:
        raise ValueError(f"{bot} never ran on the task")
    if detail.get("has_more"):
        raise ValueError("the task has more messages than one read returns, so its opening may be missing")
    start = when(runs[0]["started_at"])
    opening = [
        {"from": "person", "text": m["body"]}
        for m in sorted(
            {m["id"]: m for m in (detail.get("messages") or []) + (detail.get("comments") or [])}.values(),
            key=lambda m: m["created_at"],
        )
        if _person(m["sender_id"])
        and m.get("kind") != "notice"
        and not m.get("deleted_at")
        and when(m["created_at"]) < start
    ]
    mine = set(attached(files, bot))
    opened = []
    for f in files.get("files") or []:
        first = min(f.get("versions") or [{}], key=lambda v: v.get("n", 0))
        if f["name"] not in mine and first.get("created_at") and when(first["created_at"]) < start:
            opened.append({"name": f["name"], "text": first.get("text", "")})
    snapshot = instructions.get("content") or ""
    exact = bool(instructions.get("updated_at")) and when(instructions["updated_at"]) <= start
    status = task["status"]
    original = side(detail, files, bot, child_details)
    return {
        "id": re.sub(r"[^a-z0-9]+", "-", task["title"].lower()).strip("-")[:50] + "-" + task["id"][:8],
        "title": task["title"],
        "source": {"task": task["id"], "captured": datetime.now().astimezone().isoformat(timespec="seconds")},
        "reviewed_by": "",
        "bot": bot,
        # The prompt the original ran on. `exact: false`: the snapshot is newer than the task's first run.
        "prompt": {"sha256": sha(snapshot), "updated": instructions.get("updated_at"), "exact": exact},
        "task": as_started(detail, start),
        "conversation": opening,
        "files": opened,
        "person": [],
        "done": [{"status": [status]}],
        "limits": {},
        "original": {k: original[k] for k in ("status", "transcript", "files", "runs", "minutes", "person_steps")},
    }


# ------------------------------------------------------------------ scoring, the same for both sides
def check(predicate, outcome, facts):
    ((name, args),) = ({predicate: {}} if isinstance(predicate, str) else predicate).items()
    if name == "status":
        wanted = [args] if isinstance(args, str) else list(args)
        return name, outcome["status"] in wanted, f"ended {outcome['status']}"
    if name == "file_attached":
        hit = any(re.search(str(args), n, re.I) for n in outcome.get("files") or [])
        return name, hit, "no attached file matches"
    if name in ("reply_matches", "reply_not_matches"):
        return score.check(predicate, facts)
    raise ValueError("Unknown check: " + name)


def measure(case, outcome):
    """One side's result. `outcome`: status, transcript [(from, text)], files, runs, minutes, person_steps."""
    transcript = [tuple(t) for t in outcome.get("transcript") or []]
    said = [t for who, t in transcript if who == "bot"]
    facts = score.Facts(None, None, said)
    checks = [check(p, outcome, facts) for p in case.get("done") or []]
    numbers = {
        "person_steps": int(outcome.get("person_steps") or 0),
        "jargon": sum(len(score.jargon_words(t)) for t in said),
        "duplicates": score.duplicates(transcript),
        "sent_elsewhere": score.sent_elsewhere(said),
        "runs": int(outcome.get("runs") or 0),
    }
    limits = case.get("limits") or {}
    over = {k: v for k, v in numbers.items() if v > limits.get(k, 10**6)}
    done = all(held for _, held, _ in checks)
    return {
        "done": done,
        "passed": done and not over,
        "over_limit": over,
        "status": outcome.get("status"),
        "minutes": outcome.get("minutes"),
        "checks": [{"check": n, "held": h, **({} if h else {"why": w})} for n, h, w in checks],
        **numbers,
    }


def compare(before, after):
    """win, regression, mixed or same, and why: done first, then within limits, then each number (lower is better)."""
    for key, label in (("done", "done"), ("passed", "within limits")):
        if before[key] != after[key]:
            return ("win" if after[key] else "regression"), [f"{'now' if after[key] else 'no longer'} {label}"]
    better = [f"{k} {before[k]}→{after[k]}" for k in NUMBERS if after[k] < before[k]]
    worse = [f"{k} {before[k]}→{after[k]}" for k in NUMBERS if after[k] > before[k]]
    if better and worse:
        return "mixed", better + worse
    return ("win", better) if better else ("regression", worse) if worse else ("same", [])


def report(rows, title, prompt_note=""):
    """The release-preparation report: counts, then regressions, then wins, then the full table."""
    count = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("win", "regression", "mixed", "same", "error")}
    lines = [
        f"# {title}",
        "",
        prompt_note,
        "" if prompt_note else None,
        f"{len(rows)} case{'' if len(rows) == 1 else 's'}: {count['win']} wins, {count['regression']} regressions, {count['mixed']} mixed, "
        f"{count['same']} same" + (f", {count['error']} could not run" if count["error"] else "") + ".",
        "",
    ]
    for verdict, heading in (
        ("regression", "Regressions"),
        ("error", "Could not run"),
        ("win", "Wins"),
        ("mixed", "Mixed"),
    ):
        picked = [r for r in rows if r["verdict"] == verdict]
        if picked:
            lines += [f"## {heading}", ""] + [f"- **{r['id']}**: {'; '.join(r['why']) or '—'}" for r in picked] + [""]
    lines += [
        "## Every case",
        "",
        "| Case | Verdict | Status (was → now) | Done | Steps | Jargon | Dupes | Settings | Runs | Minutes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        b, a = r.get("before") or {}, r.get("after") or {}
        pair = lambda k: f"{b.get(k, '—')} → {a.get(k, '—')}"
        lines.append(
            f"| {r['id']} | {r['verdict']} | {pair('status')} | {pair('done')} | {pair('person_steps')} | "
            f"{pair('jargon')} | {pair('duplicates')} | {pair('sent_elsewhere')} | {pair('runs')} | {pair('minutes')} |"
        )
    return "\n".join(line for line in lines if line is not None) + "\n"
