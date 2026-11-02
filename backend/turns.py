"""What a bot's turn did, for the chat under its reply (#524).

A reply carries `refs.turn_id`, its attempt. `annotate` adds two things a person reading the room
may see: the titles of the tasks a message refers to, and `run` on a reply: the hub work the turn
did while it ran (tasks handed out, links filed, questions and messages sent, refusals) and the
size of its work (steps, tool calls, time). `GET /api/v2/turns/{id}/steps` is that work line by
line, read when someone opens it. Everything is read from what the hub already records; a row
the reader could not open on its own (a task or a conversation outside their reach) is left out.
"""

import json
import re

from fastapi import Request

from .store import H, Problem
from . import task_privacy as privacy

SAID_CHARS, THINKING_CHARS, STEP_LIMIT = 280, 2000, 300
# A tool event that opens a call; the next event for the same call closes it.
TOOL_START = {"started", "start", "running", "pending", "in_progress", "tool_execution_start"}
TOOL_QUIET = {"", "completed", "done", "success", "succeeded", "tool_execution_end"}
# Text deltas are the reply being typed and tokens/status are bookkeeping: none is a step.
STEP_EVENTS = ("(kind IN ('tool','message','error') OR "
               "(kind='delta' AND json_extract(payload,'$.delta_kind')='thought'))")


# A tool event that reports a call's end (or its failure) is the same call the start already listed.
TOOL_ENDED = TOOL_QUIET | {"failed", "error", "cancelled", "canceled"}
TOOL_LABEL_CHARS = 60
# A call that moved to the background while it ran: still the same open call, so not a step of its own.
TOOL_BACKGROUNDED = "backgrounded"


def clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def subagent_kind(p):
    """A subagent's type ("general-purpose") from a tool event that starts or ends one, else None. Only the type
    is read: its description and prompt are the call's arguments."""
    agent = p.get("subagent")
    if not isinstance(agent, dict):
        return None
    words = " ".join(re.sub(r"[^A-Za-z0-9_-]+", " ", str(agent.get("type") or "")).split())
    return words[:TOOL_LABEL_CHARS] or "agent"


def build_steps(rows):
    """One line per thing the turn did, from its attempt events: (kind, payload, created). A subagent is a
    `subagent` step; a call a subagent made is `nested`."""
    steps, calls, thinking = [], {}, None
    final = next((str(p.get("text") or "") for kind, p, _ in reversed(rows) if kind == "message" and p.get("final")), "")
    for kind, p, at in rows:
        if kind == "delta":       # the model's thinking arrives in pieces; one run of them is one step
            if not thinking:
                thinking = {"kind": "thinking", "tool": "", "text": "", "at": at}
                steps.append(thinking)
            thinking["text"] += str(p.get("text") or "")
            continue
        thinking = None
        if kind == "tool":
            name = str(p.get("tool") or p.get("name") or "tool")
            status = str(p.get("status") or p.get("state") or p.get("phase") or "").lower()
            key = p.get("item_id") or name
            agent = subagent_kind(p)
            if status == TOOL_BACKGROUNDED:
                if key in calls:
                    calls[key]["background"] = True
                continue
            if key in calls and status not in TOOL_START:
                step = calls.pop(key)
                outcome = "" if status in TOOL_QUIET else status
                step["text"] = f"{agent} · {outcome}" if agent and outcome else agent or outcome
                continue
            text = "" if status in TOOL_QUIET | TOOL_START else status
            if agent:
                step = {"kind": "subagent", "tool": "subagent", "text": f"{agent} · {text}" if text else agent, "at": at}
            else:
                step = {"kind": "tool", "tool": name, "text": text, "at": at}
            if p.get("parent_id"):
                step["nested"] = True
            if p.get("background"):
                step["background"] = True
            steps.append(step)
            if status in TOOL_START:
                calls[key] = step
        elif kind == "message" and not p.get("final"):
            text = str(p.get("text") or "")
            if text.strip() and text.strip() != final.strip():
                steps.append({"kind": "said", "tool": "", "text": clip(text, SAID_CHARS), "at": at})
        elif kind == "error":
            steps.append({"kind": "error", "tool": "", "text": clip(p.get("error") or p.get("text"), SAID_CHARS), "at": at})
    for step in steps:
        if step["kind"] == "thinking":
            step["text"] = clip(step["text"], THINKING_CHARS)
    return steps[:STEP_LIMIT]


def tool_label(payload):
    """"Ran hub task create" from a tool event: the tool's own name and nothing else. An event can
    carry a call's arguments or output; neither is read, so neither can reach a frontend."""
    words = " ".join(re.sub(r"[^A-Za-z0-9]+", " ", str(payload.get("tool") or payload.get("name") or "")).split())
    if words.lower().startswith("mcp "):
        words = words[4:]
    return "Ran " + (words[:TOOL_LABEL_CHARS].strip() or "a tool")


def reply_parts(rows):
    """The pieces of a run's live reply, in order: [{kind, text, at}], from its attempt events
    (kind, payload, created).

    A message is one piece however it arrived. Its deltas type into a piece as they come, so they
    join with nothing between them; the message that ends it (which holds its whole text) replaces
    what they typed; and the next delta or message starts a new piece. A closing message that only
    repeats the piece before it adds nothing. A tool call is a short piece of its own (its label,
    never its arguments or output) and ends the piece being typed. A text piece the bot wrote just
    before a tool call is `progress`; the others are `reply`. A model's thinking is not the reply.
    """
    parts, typing, called = [], None, set()
    for kind, payload, at in rows:
        if kind == "delta":
            if payload.get("delta_kind") == "thought":
                continue
            if typing is None:
                typing = {"kind": "reply", "text": "", "at": at}
                parts.append(typing)
            typing["text"] += str(payload.get("text", ""))
        elif kind == "message":
            text = str(payload.get("text", ""))
            if typing is not None:
                if text.strip():
                    typing["text"] = text
                typing = None
            elif text.strip():
                last = next((p for p in reversed(parts) if p["kind"] != "tool"), None)
                if not last or last["text"].strip() != text.strip():
                    parts.append({"kind": "reply", "text": text, "at": at})
        elif kind == "tool":
            status = str(payload.get("status") or payload.get("state") or payload.get("phase") or "").lower()
            key = payload.get("item_id")
            if status in TOOL_ENDED or (key and key in called) or payload.get("parent_id"):
                continue  # a subagent's own calls are its work, not the reply's
            called.add(key)
            typing = None
            parts.append({"kind": "tool", "text": tool_label(payload), "at": at})
    parts = [{**p, "text": p["text"].strip()} for p in parts]
    parts = [p for p in parts if p["text"]]
    for here, after in zip(parts, parts[1:]):
        if here["kind"] == "reply" and after["kind"] == "tool":
            here["kind"] = "progress"
    return parts


def seconds(start, end):
    a, b = H.parse_ts(start), H.parse_ts(end)
    return int((b - a).total_seconds()) if a and b else None


def readable(fn):
    try:
        fn()
        return True
    except Problem:
        return False


def task_refs(message):
    for key, values in (message.get("refs") or {}).items():
        if "task" in key.lower():
            yield from (v for v in (values if isinstance(values, list) else [values]) if isinstance(v, str) and v)


def mark_runs(c, messages):
    """Say which run handled each message, on the message itself.

    A message a bot's run was started for is `run.state` "started_run"; one that was folded into a
    run already working is "added_to_run", and `run` names that run's job and attempt either way.
    A bot's reply carries `run` (the run that wrote it) and `answers`, every message that run
    handled, both saved with the reply when the run completed (`refs.run`, `refs.answers`).
    Who was folded in is `attempt_inputs`, which the hub keeps, so it needs nothing new stored;
    a message from before this, or one no run has taken yet, simply has no `run`.
    """
    ids = [m["id"] for m in messages]
    taken = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for row in c.execute(
                "SELECT j.message_id,j.id AS job_id,j.attempt_id,a.job_id AS run_job,"
                " EXISTS(SELECT 1 FROM attempt_inputs i WHERE i.message_id=j.message_id AND i.attempt_id=j.attempt_id) AS folded"
                " FROM jobs j JOIN attempts a ON a.id=j.attempt_id WHERE j.message_id IN (%s)" % ",".join("?" * len(chunk)),
                tuple(chunk)):
            if row["folded"] or row["run_job"] == row["job_id"]:      # not a notice another run merely coalesced
                taken[row["message_id"]] = {"job_id": row["run_job"], "attempt_id": row["attempt_id"],
                                            "state": "added_to_run" if row["folded"] else "started_run"}
    for m in messages:
        refs = m.get("refs") or {}
        if isinstance(refs.get("run"), dict) and isinstance(refs.get("answers"), list):
            m["run"], m["answers"] = dict(refs["run"]), list(refs["answers"])
        elif m["id"] in taken:
            m["run"] = taken[m["id"]]
    return messages


def mark_delivery(c, messages):
    """Say what happens to a message still queued while its bot answers in the same conversation.

    `delivery.mode` is "steer" when the bot's harness writes it into the running turn (`steer`), "next_turn" when
    it waits for that turn to end (its harness cannot steer, or it was sent with queue: backend/delivery.py), and
    "redirect" once its sender chose Stop and redirect. `delivery.attempt_id`
    is the running turn. A message a run took already has `run` instead (mark_runs)."""
    pending = {m["id"]: m for m in messages if "run" not in m and str(m.get("recipient_id", "")).startswith("bot:")}
    if not pending:
        return
    from .harnesses import steers
    ids, configs = list(pending), {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for row in c.execute(
                "SELECT j.message_id,j.bot_id,j.redirect,json_extract(m.refs,'$.delivery') AS way,(SELECT a.id FROM attempts a JOIN jobs aj ON aj.id=a.job_id"
                " JOIN messages am ON am.id=aj.message_id WHERE a.bot_id=j.bot_id AND a.state IN ('leased','running')"
                " AND (am.conversation_id=m.conversation_id OR EXISTS(SELECT 1 FROM attempt_conversations ac"
                " WHERE ac.attempt_id=a.id AND ac.conversation_id=m.conversation_id)) ORDER BY a.created_at LIMIT 1) AS live"
                " FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued'"
                " AND j.message_id IN (%s)" % ",".join("?" * len(chunk)), tuple(chunk)):
            if not row["live"]:
                continue
            if row["bot_id"] not in configs:
                config = c.execute("SELECT config FROM bot_config WHERE bot=?", (row["bot_id"],)).fetchone()
                configs[row["bot_id"]] = json.loads(config[0]) if config else {}
            config = configs[row["bot_id"]]
            mode = ("redirect" if row["redirect"] else "steer" if row["way"] != "queue"
                    and steers(config, config.get("runtime")) else "next_turn")
            pending[row["message_id"]]["delivery"] = {"attempt_id": row["live"], "mode": mode}


def mark_prompts(c, messages):
    """Say who started the run that sent a bot's loose message to a person, on the message itself.

    A reply carries `in_reply_to` and `refs.turn_id`; a message a bot sends with a tool while it works
    carries neither. When that run was started by another bot's message ("From Dots: ..."), the chat
    folds it with the other side traffic instead of showing it as the bot talking to you. `prompted_by`
    is only the kind of sender ("bot", "keeper" or "person"), never who or what they wrote, and a run
    that took in any person's message counts as the person's. A bot's runs overlap only across task
    lanes (one_active_attempt_per_lane), so the run is the latest one whose time span holds the message.
    """
    loose = [m for m in messages if str(m.get("sender_id", "")).startswith("bot:")
             and str(m.get("recipient_id", "")).startswith("human:") and m.get("kind") in ("say", "notice")
             and not m.get("in_reply_to") and not (m.get("refs") or {}).get("turn_id")]
    if not loose:
        return
    bots = sorted({m["sender_id"][4:] for m in loose})
    lo, hi = min(m["created_at"] for m in loose), max(m["created_at"] for m in loose)
    runs = c.execute(
        f"SELECT a.bot_id,a.state,coalesce(a.started_at,a.created_at) AS began,a.finished_at,t.sender_id,"
        " EXISTS(SELECT 1 FROM attempt_inputs i JOIN messages im ON im.id=i.message_id"
        "  WHERE i.attempt_id=a.id AND im.sender_id LIKE 'human:%') AS person"
        " FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages t ON t.id=j.message_id"
        f" WHERE a.bot_id IN ({','.join('?' * len(bots))}) AND coalesce(a.started_at,a.created_at)<=?"
        " AND ((a.finished_at IS NULL AND a.state IN ('leased','running')) OR a.finished_at>=?)",
        (*bots, hi, lo)).fetchall()
    # A run that failed before it started has no `finished_at`; only a live one is open-ended.
    runs = sorted(runs, key=lambda r: r["began"], reverse=True)
    for m in loose:
        run = next((r for r in runs if "bot:" + r["bot_id"] == m["sender_id"] and r["began"] <= m["created_at"]
                    and (r["finished_at"] is None and r["state"] in ("leased", "running")
                         or r["finished_at"] is not None and m["created_at"] <= r["finished_at"])), None)
        if not run:
            continue
        sender = str(run["sender_id"] or "")
        m["prompted_by"] = ("person" if run["person"] or sender.startswith("human:")
                            else "bot" if sender.startswith("bot:") else "keeper")


def annotate(c, auth, who, messages):
    """Add `run` (which run handled it), `ref_tasks`, `prompted_by` on a bot's loose message to a person,
    and, on a bot's reply, what the run did to messages this reader may already see."""
    messages[:] = privacy.readable_messages(c, privacy.actor(who), messages)
    mark_runs(c, messages)
    mark_delivery(c, messages)
    mark_prompts(c, messages)
    titles = {}
    refs = {v for m in messages for v in task_refs(m)}
    H.prime_tasks(c, refs)
    for tid in refs:
        task = H.task(c, tid)
        if task and readable(lambda: auth.task_row(c, who, task)):
            titles[tid] = {"title": task["title"], "owner_id": task["owner_id"], "status": task["status"]}
    for m in messages:
        known = {v: titles[v] for v in task_refs(m) if v in titles}
        if known:
            m["ref_tasks"] = known
    replies = {m["refs"]["turn_id"]: m for m in messages
               if str(m.get("sender_id", "")).startswith("bot:") and (m.get("refs") or {}).get("turn_id")}
    if not replies:
        return messages
    marks = ",".join("?" * len(replies))
    turns = {r["id"]: dict(r) for r in c.execute(
        f"SELECT id,bot_id,created_at,started_at,finished_at FROM attempts WHERE id IN ({marks})", tuple(replies))}
    # What a bot did to answer is its run log: Read. Someone who may only write to it gets the answer.
    access = auth.bot_accesses(c, who, sorted({t["bot_id"] for t in turns.values()}))
    turns = {aid: t for aid, t in turns.items() if access.get(t["bot_id"], auth.FULL)["read"]
             and privacy.attempt_readable(c, privacy.actor(who), aid)}
    replies = {aid: m for aid, m in replies.items() if aid in turns}
    if not turns:
        return messages
    for t in turns.values():
        t["from"], t["to"] = t["started_at"] or t["created_at"], t["finished_at"] or H.now()
    actors = sorted({"bot:" + t["bot_id"] for t in turns.values()})
    lo, hi = min(t["from"] for t in turns.values()), max(t["to"] for t in turns.values())
    who_marks = ",".join("?" * len(actors))

    def owner_of(actor, at):
        return next((aid for aid, t in turns.items() if "bot:" + t["bot_id"] == actor and t["from"] <= at <= t["to"]), None)

    did = {aid: [] for aid in turns}
    for row in c.execute(f"SELECT * FROM tasks WHERE requester_id IN ({who_marks}) AND created_at BETWEEN ? AND ?",
                         (*actors, lo, hi)):
        aid = owner_of(row["requester_id"], row["created_at"])
        if aid and readable(lambda: auth.task_row(c, who, dict(row))):
            did[aid].append({"kind": "task", "task_id": row["id"], "title": row["title"], "owner_id": row["owner_id"],
                             "at": row["created_at"]})
    for row in c.execute(f"SELECT l.*,t.owner_id,t.requester_id,t.id AS tid FROM task_links l JOIN tasks t ON t.id=l.task_id "
                         f"WHERE l.added_by IN ({who_marks}) AND l.created_at BETWEEN ? AND ?", (*actors, lo, hi)):
        aid = owner_of(row["added_by"], row["created_at"])
        if aid and readable(lambda: auth.task(c, who, row["tid"])):
            did[aid].append({"kind": "link", "url": row["url"], "title": row["title"] or "", "at": row["created_at"]})
    rooms_of = {aid: replies[aid]["conversation_id"] for aid in turns}
    for row in c.execute(f"SELECT id,conversation_id,sender_id,recipient_id,kind,body,created_at FROM messages "
                         f"WHERE sender_id IN ({who_marks}) AND kind IN ('ask','say') AND created_at BETWEEN ? AND ? "
                         "AND deleted_at IS NULL", (*actors, lo, hi)):
        aid = owner_of(row["sender_id"], row["created_at"])
        if not aid or row["conversation_id"] == rooms_of[aid] or row["recipient_id"] == row["sender_id"]:
            continue
        if readable(lambda: auth.conversation(c, who, row["conversation_id"])) and privacy.message_readable(c, privacy.actor(who), row):
            did[aid].append({"kind": row["kind"], "to": row["recipient_id"], "text": clip(row["body"], 160), "at": row["created_at"]})
    for row in c.execute(f"SELECT created_at,actor,rule,detail FROM refusals WHERE actor IN ({who_marks}) AND created_at BETWEEN ? AND ?",
                         (*actors, lo, hi)):
        aid = owner_of(row["actor"], row["created_at"])
        if aid:
            detail = (json.loads(row["detail"] or "{}") or {}).get("detail") or ""
            did[aid].append({"kind": "refused", "rule": row["rule"], "text": clip(detail, 200), "at": row["created_at"]})
    events = {aid: [] for aid in turns}
    for row in c.execute(f"SELECT attempt_id,kind,payload,created_at FROM attempt_events WHERE attempt_id IN "
                         f"({','.join('?' * len(turns))}) AND {STEP_EVENTS} ORDER BY attempt_id,seq", tuple(turns)):
        if not privacy.content_readable(c, privacy.actor(who), dict(row)):
            continue
        events[row["attempt_id"]].append((row["kind"], json.loads(row["payload"]), row["created_at"]))
    for aid, t in turns.items():
        steps = build_steps(events[aid])
        replies[aid].setdefault("run", {}).update(
            did=sorted(did[aid], key=lambda d: d["at"]), steps=len(steps),
            tool_calls=sum(s["kind"] == "tool" for s in steps), took_s=seconds(t["started_at"], t["finished_at"]))
    return messages


def install(app, store, auth):
    @app.get("/api/v2/turns/{aid}/steps")
    def turn_steps(request: Request, aid: str):
        """A turn's work, one line per step. Readable by whoever can read the room it answered, and by the bots
        above its bot on the org chart (`Auth.run_conversation`)."""
        who = request.state.identity
        with store.read() as c:
            row = c.execute("SELECT a.*,j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id "
                            "JOIN messages m ON m.id=j.message_id WHERE a.id=?", (aid,)).fetchone()
            message = row and H.message(c, row["message_id"])
            if not message:
                raise Problem("not_found", "Turn not found", 404)
            auth.run_conversation(c, who, row["bot_id"], message)
            privacy.require_attempt(c, who, aid)
            auth.require_read(c, who, row["bot_id"])
            events = [(r["kind"], json.loads(r["payload"]), r["created_at"]) for r in c.execute(
                f"SELECT kind,payload,created_at FROM attempt_events WHERE attempt_id=? AND {STEP_EVENTS} ORDER BY seq",
                (aid,)) if privacy.content_readable(c, privacy.actor(who), dict(r))]
            # A live run of a bot org rules apply to keeps what it says out until its reply is checked and delivered
            # (backend/org_rule_check.py): its tool steps only, as the room's live view shows it.
            from .org_rule_check import hides_run
            if hides_run(c, row):
                events = [e for e in events if e[0] == "tool"]
            steps = build_steps(events)
            return {"turn_id": aid, "started_at": row["started_at"], "finished_at": row["finished_at"],
                    "took_s": seconds(row["started_at"], row["finished_at"]),
                    "tool_calls": sum(s["kind"] == "tool" for s in steps), "steps": steps}
