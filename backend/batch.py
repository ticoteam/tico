"""A batch: what needs a person, frozen, walked one item at a time, responses collected and
applied together on commit.

The person goes through the list giving a response per item; nothing is applied while they
talk. Commit applies the decisions with the person's own identity, records snoozes, and sends each bot one message in
the person's chat with it: what was decided, and the questions, instructions and rules it must act on. Each bot's
reply, split per item, comes back on those items the next time that bot comes up. Any front end runs the same loop:
the hub tools an outside agent (Grok Bot, Meta Muse) calls, a page.

The list is Needs you (`waiting_on.needs`): every OK and question a bot asked of the person (`ask:<requirement>`) and
every task a bot filed for them (`task:<task>`); a batch holds only what bots are waiting on. A decision is what the
Needs-you buttons do: OK, Send back (with notes), an answer, Done or Close (`_apply_decide`, through the task calls).
An OK that is bound to what it lets go (send, spend, publish: backend/task_asks.py) is never given on a walk: it is
the person's own click on its preview in Tico. The person's Assistant may walk the list for them, as them; it never
OKs. It is the whole list, or one bot's part of it (`scope`). "Who needs me" starts the bot that most needs the
person, and commit says who is next: the bots come to the person one at a time, most important first.
"""

import contextlib
import json
import re

from . import hubdb as H
from . import task_asks
from .store import Problem
from . import task_privacy as privacy

KINDS = ("decide", "needs_info", "instruct", "rule", "skip", "later")
DECISIONS = ("ok", "send_back", "done", "close", "answer")
VERDICTS = ("decide", "skip", "later")
LATER_DAYS = 1
REPORT_DAYS = 14  # how far back a bot's reply to a batch still comes back on its items
MARKER = re.compile(r"^\s*\[item (\d+)\]", re.M)
NEXT = "next"  # the scope that means "whichever bot most needs the person"
ORDER = {"ok": 0, "answer": 1, "task": 2}  # an OK first, then a question, then a task filed for them


def _clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _snoozed(c, actor):
    """Item keys the person said "later" to, still inside their window."""
    now = H.now()
    out = set()
    # The person's own rows (events_task_origin), not every person's in ts order (events_action_ts).
    for row in c.execute(
        "SELECT target, detail FROM events INDEXED BY events_task_origin WHERE actor=? "
        "AND action='batch.later' ORDER BY created_at",
        (actor,),
    ):
        until = (H._json(row["detail"], {}) or {}).get("until") or ""
        if until > now:
            out.add(row["target"])
        else:
            out.discard(row["target"])
    return out


def _row(c, entry):
    """One Needs-you entry as a front end presents it: the whole ask, never a clipped title. None for what no bot is
    waiting on (a person's own to-do, a bot that cannot run)."""
    from . import task_requirements as R

    task = H.task(c, entry["task_id"]) if entry.get("task_id") else None
    if not task:
        return None
    if entry["kind"] == "ask":
        requirement = R.get(c, entry["requirement_id"])
        if not requirement:
            return None
        frm = requirement.get("added_by_id") if requirement.get("visit_id") else task.get("owner_id")
        kind = requirement["kind"]
        row = {
            "key": "ask:" + entry["requirement_id"],
            "kind": kind,
            "title": _clip(entry.get("line") or requirement.get("line"), 160),
            "task": entry["task_id"],
            "task_title": _clip(task.get("title"), 160),
        }
        if requirement.get("choices"):
            row["choices"] = requirement["choices"]
        if requirement.get("shows"):
            row["shows"] = requirement["shows"]
            row["source"] = requirement["shows"]  # its words come from those pages (files.readable_page_refs)
        if kind == "ok" and task_asks.grant(c, entry["requirement_id"]):
            # Bound to what it lets go: OK'd only from its preview in Tico, never on a walk.
            row["preview_only"] = True
    elif entry["kind"] == "task":
        frm = task.get("requester_id")
        row = {"key": "task:" + entry["task_id"], "kind": "task", "title": _clip(task.get("title"), 160)}
        if task.get("body"):
            row["body"] = _clip(task["body"], 800)
        if entry.get("missing"):
            row["missing"] = entry["missing"]
    else:
        return None
    if not H.is_bot(frm):
        return None
    row["from"] = frm
    if entry.get("since"):
        row["created_at"] = entry["since"]
    if task.get("due_at"):
        row["due_at"] = task["due_at"]
    return row


def alerts(snapshot):
    """Bots that cannot do their work, said once at the top of a batch."""
    out = []
    for b in snapshot.get("bots") or []:
        name, queued = b.get("name") or b.get("slug"), b.get("queued") or 0
        tail = f" with {queued} task{'s' if queued != 1 else ''} queued" if queued else ""
        if b.get("state") in ("crashed", "quarantined"):
            out.append(f"{name} is {b['state']}{tail}")
        elif b.get("state") == "limited":
            out.append(f"{name} is rate limited{tail}")
        elif b.get("online") is False and queued:
            out.append(f"{name} is offline{tail}")
    return out


def relevant(c, actor, reader=None):
    """What bots are waiting on the person for, from Needs you: what they deferred dropped, each task the reader may read
    once, an OK above a question above a task, oldest first."""
    from . import waiting_on

    snoozed = _snoozed(c, actor)
    out, seen = [], set()
    for entry in waiting_on.needs(c, actor):
        task = H.task(c, entry["task_id"]) if entry.get("task_id") else None
        if not task or not H.task_private_readable(c, reader or actor, task):
            continue
        row = _row(c, entry)
        if not row or row["key"] in seen or row["key"] in snoozed:
            continue
        seen.add(row["key"])
        out.append(row)
    out.sort(key=_rank)
    return out


def group_of(row, person):
    """The bot an item is waiting with, or None when no bot is: the person's own task, or another
    person's request. Only a bot's items go in a batch."""
    frm = row.get("from") or ""
    return frm if H.is_bot(frm) else None


def _name(c, actor):
    return (H.bot(c, actor) or {}).get("name") or H.actor_id(actor)


def _rank(row):
    """An OK first, then a question, then a task filed for the person; a due date sooner, then the oldest."""
    return (ORDER.get(row["kind"], 3), row.get("due_at") or "~", row.get("created_at") or "")


def lineup(c, person, rows):
    """Who needs the person, most important first: each group ranked by its most pressing item (`_rank`), more items
    breaking a tie.
    `awaiting` counts items the person already answered and whose bot has not replied yet."""
    groups = {}
    for row in rows:
        who = group_of(row, person)
        g = groups.setdefault(who, {"who": who, "items": 0, "awaiting": 0, "best": None, "top": ""})
        g["items"] += 1
        g["awaiting"] += bool(row.get("awaiting"))
        if row.get("awaiting"):
            continue
        rank = _rank(row)
        if g["best"] is None or rank < g["best"]:
            g["best"], g["top"] = rank, row["title"]
    # A group that is only waiting on its bot's reply goes last: there is nothing new to say.
    ordered = sorted(groups.values(), key=lambda g: (g["best"] is None, g["best"] or (), -g["items"]))
    return [
        {"who": g["who"], "name": _name(c, g["who"]), "items": g["items"], "awaiting": g["awaiting"], "top": g["top"]}
        for g in ordered
    ]


def _sent(c, person, since):
    """The batch messages this person sent since `since`, each with the item keys it carried."""
    rows = c.execute(
        "SELECT m.id, m.recipient_id, m.refs, b.id AS batch, b.items, b.report, "
        "b.message_id AS first "
        "FROM message_refs m JOIN batches b ON b.id=json_extract(m.refs,'$.live.batch') "
        "WHERE m.sender_id=? AND m.ref_live_kind='batch' "
        "AND m.created_at>=? ORDER BY m.created_at DESC",
        (person, since),
    ).fetchall()
    return [dict(r) for r in rows]


def _consumed(report_json, message_id, batch_message_id):
    report = H._json(report_json, {}) or {}
    # Before per-bot routing a batch sent one message to the assistant and marked it with `reply`.
    if report.get("reply") and message_id == batch_message_id:
        return True
    return message_id in (report.get("consumed") or {})


def _pending(c, person):
    """Replies to recent batch messages not yet shown, and the items still waiting on a reply.

    Returns `(reports, awaiting)`: each report is `{message, reply, batch, to, answers, loose}`
    with `answers` keyed by item key; `awaiting` maps an item key to the bot it was sent to."""
    reports, awaiting = [], {}
    since = H.shift(H.now(), days=-REPORT_DAYS)
    for sent in _sent(c, person, since):
        if not privacy.message_readable(c, person, sent):
            continue
        if _consumed(sent["report"], sent["id"], sent["first"]):
            continue
        keys = ((H._json(sent["refs"], {}) or {}).get("live") or {}).get("items") or []
        reply = c.execute(
            "SELECT * FROM messages WHERE in_reply_to=? AND sender_id=? ORDER BY created_at DESC LIMIT 1",
            (sent["id"], sent["recipient_id"]),
        ).fetchone()
        if not reply:
            for key in keys:
                awaiting.setdefault(key, sent["recipient_id"])
            continue
        if not privacy.message_readable(c, person, reply):
            continue
        items = json.loads(sent["items"] or "[]")
        parts = MARKER.split(reply["body"] or "")
        answers, loose = {}, parts[0].strip()
        for n, body in zip(parts[1::2], parts[2::2]):
            idx = int(n) - 1
            if 0 <= idx < len(items):
                answers[items[idx]["key"]] = body.strip()
            else:
                loose += ("\n" if loose else "") + body.strip()
        reports.append(
            {
                "message": sent["id"],
                "reply": reply["id"],
                "batch": sent["batch"],
                "to": sent["recipient_id"],
                "answers": answers,
                "loose": loose,
            }
        )
    return reports, awaiting


def _consume(c, report):
    row = c.execute("SELECT report FROM batches WHERE id=?", (report["batch"],)).fetchone()
    data = H._json(row["report"] if row else None, {}) or {}
    data.setdefault("consumed", {})[report["message"]] = report["reply"]
    c.execute("UPDATE batches SET report=? WHERE id=?", (json.dumps(data), report["batch"]))


def _scope(c, person, line, asked):
    """The group a new batch holds: None for the whole list, the top of the line-up for `next`."""
    if not asked:
        return None
    asked = str(asked).strip()
    if asked == NEXT:
        fresh = [g for g in line if g["items"] > g["awaiting"]]
        return (fresh or line or [{"who": None}])[0]["who"]
    who = H.resolve_actor(c, asked)
    if not who or not H.is_bot(who):
        raise Problem("not_found", f"{asked} is not a bot; a batch holds what bots are waiting on", 404)
    return who


def current(c, actor):
    row = c.execute(
        "SELECT * FROM batches WHERE person=? AND state='open' ORDER BY created_at DESC LIMIT 1", (actor,)
    ).fetchone()
    return dict(row) if row else None


def candidates(c, actor, reader=None):
    """What would go in a new batch: Needs you after the relevance check, with each bot's replies
    attached to the items they answer and its loose notes as a report item from that bot. Nothing
    is consumed here."""
    rows = [r for r in relevant(c, actor, reader) if group_of(r, actor)]
    reports, awaiting = _pending(c, actor)
    shown = {}
    for report in reports:  # newest first: the latest answer to an item wins
        for key, text in report["answers"].items():
            shown.setdefault(key, (text, report))
    for row in rows:
        if row["key"] in shown:
            row["answer"] = _clip(shown[row["key"]][0], 4000)
            row["_report"] = shown[row["key"]][1]["message"]
        elif row["key"] in awaiting:
            row["awaiting"] = awaiting[row["key"]]
    notes = [
        {
            "key": f"report:{r['message']}",
            "kind": "report",
            "title": "Notes from your last batch",
            "from": r["to"],
            "body": _clip(r["loose"], 4000),
            "_report": r["message"],
        }
        for r in reports
        if r["loose"]
    ]
    return rows, notes, reports


def start(c, actor, snapshot, scope=None, reader=None):
    """The open batch if there is one (resume), otherwise a new one from Needs you: the whole list, or one group of it
    when `scope` names a bot or `next`. `reader` is the privacy actor the person reads with (task_privacy.actor): only
    the tasks it may read are in it."""
    rows, notes, reports = candidates(c, actor, reader)
    line = lineup(c, actor, notes + rows)
    open_ = current(c, actor)
    if open_:
        return {**view(c, open_, resumed=True), "lineup": line}
    group = _scope(c, actor, line, scope)
    if group:
        rows = [r for r in rows if group_of(r, actor) == group]
        notes = [n for n in notes if group_of(n, actor) == group]
    answered = [r for r in rows if r.get("answer")]
    rows = notes + answered + [r for r in rows if not r.get("answer")]
    # A reply is shown once: consumed when any part of it (an answer or its notes) is in this batch.
    used = {r.get("_report") for r in rows if r.get("_report")}
    for report in reports:
        if report["message"] in used:
            _consume(c, report)
    for row in rows:
        row.pop("_report", None)
    batch = {
        "id": H.new_id(),
        "person": actor,
        "state": "open",
        "created_at": H.now(),
        "committed_at": None,
        "items": json.dumps(rows),
        "cursor": 0,
        "responses": "{}",
        "alerts": json.dumps(alerts(snapshot)),
        "message_id": None,
        "report": None,
        "scope": group,
    }
    c.execute(
        "INSERT INTO batches(id,person,state,created_at,committed_at,items,cursor,responses,"
        "alerts,message_id,report,scope) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        tuple(
            batch[k]
            for k in (
                "id",
                "person",
                "state",
                "created_at",
                "committed_at",
                "items",
                "cursor",
                "responses",
                "alerts",
                "message_id",
                "report",
                "scope",
            )
        ),
    )
    H.event(
        c, actor, "batch.started", batch["id"], {"items": len(rows), "alerts": len(alerts(snapshot)), "scope": group}
    )
    return {**view(c, batch), "lineup": line}


def _items(batch):
    return json.loads(batch["items"] or "[]")


def _responses(batch):
    return json.loads(batch["responses"] or "{}")


def view(c, batch, resumed=False):
    items, responses = _items(batch), _responses(batch)
    cursor = int(batch["cursor"] or 0)
    out = {
        "id": batch["id"],
        "state": batch["state"],
        "total": len(items),
        "responded": len(responses),
        "alerts": json.loads(batch["alerts"] or "[]"),
        "resumed": resumed,
        "scope": batch.get("scope"),
    }
    if batch["state"] != "open":
        return out
    if cursor < len(items):
        item = {**items[cursor], "n": cursor + 1}
        if item["key"] in responses:
            item["response"] = responses[item["key"]]
        out.update({"position": f"{cursor + 1} of {len(items)}", "item": item})
    else:
        out.update(
            {
                "end": True,
                "summary": summary(batch),
                "note": "The list is done. Say the summary and ask whether to commit.",
            }
        )
    from .files import readable_page_refs
    from .teammate_access import _viewer_for

    return readable_page_refs(c, None, _viewer_for(c, batch["person"]), out)


def _load(c, actor, batch_id):
    row = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
    if not row or row["person"] != actor:
        raise Problem("not_found", "Batch not found", 404)
    return dict(row)


def next_item(c, actor, batch_id, reader=None):
    batch = _load(c, actor, batch_id)
    if batch["state"] != "open":
        raise Problem("state", f"This batch is {batch['state']}", 409)
    items = _items(batch)
    cursor = min(int(batch["cursor"] or 0) + 1, len(items))
    c.execute("UPDATE batches SET cursor=? WHERE id=?", (cursor, batch["id"]))
    batch["cursor"] = cursor
    return view(c, batch)


def respond(c, actor, batch_id, kind, text="", n=None, decision=None, heard="", until=None):
    """Record the person's response to an item (the current one unless `n` names another)."""
    batch = _load(c, actor, batch_id)
    if batch["state"] != "open":
        raise Problem("state", f"This batch is {batch['state']}", 409)
    if kind not in KINDS:
        raise Problem("kind", f"A response is {'|'.join(KINDS)}, not {kind}", 422)
    items = _items(batch)
    idx = (int(n) - 1) if n else int(batch["cursor"] or 0)
    if not 0 <= idx < len(items):
        raise Problem("not_found", f"No item {n or idx + 1} in this batch", 404)
    item = items[idx]
    text = str(text or "").strip()
    response = {"kind": kind, "text": text[:4000], "at": H.now()}
    if heard:
        response["heard"] = str(heard).strip()[:2000]
    if kind == "decide":
        if decision not in DECISIONS:
            raise Problem("decision", f"A decision is {'|'.join(DECISIONS)}, not {decision}", 422)
        allowed = {
            "ok": ("ok", "send_back"),
            "answer": ("answer",),
            "task": ("done", "close"),
        }.get(item["kind"], ())
        if item["kind"] == "report":
            raise Problem("decision", "Notes are not decided; instruct or skip", 422)
        if decision not in allowed:
            raise Problem("decision", f"{item['title']}: {' or '.join(allowed) or 'nothing to decide'}", 422)
        if decision == "ok" and item.get("preview_only"):
            raise Problem("decision", f"{item['title']}: {task_asks.FROM_PREVIEW}", 422)
        if decision in ("answer", "send_back") and not text:
            raise Problem("decision", "Say the answer" if decision == "answer" else "Say what to change", 422)
        response["decision"] = decision
    elif kind in ("needs_info", "instruct", "rule") and not text:
        raise Problem("text", f"A {kind.replace('_', ' ')} response needs the text", 422)
    elif kind == "later":
        response["until"] = until or H.shift(H.now(), days=LATER_DAYS)
    responses = _responses(batch)
    # One verdict per item (a decision, a skip or a later replaces the last); questions,
    # instructions and rules accumulate: "approve it, and always archive these" is two responses.
    kept = [r for r in responses.get(item["key"], []) if not (kind in VERDICTS and r["kind"] in VERDICTS)]
    responses[item["key"]] = kept + [response]
    c.execute("UPDATE batches SET responses=? WHERE id=?", (json.dumps(responses), batch["id"]))
    return {
        "recorded": True,
        "n": idx + 1,
        "key": item["key"],
        "response": response,
        "responded": len(responses),
        "total": len(items),
    }


def summary(batch):
    """What the commit will do, counted, OKs named: the read-back before a yes."""
    items = {it["key"]: it for it in _items(batch)}
    responses = _responses(batch)
    counts, total = {}, 0
    oks, sent_back = [], []
    for key, rs in responses.items():
        for r in rs:
            total += 1
            label = r["kind"] if r["kind"] != "decide" else r.get("decision")
            counts[label] = counts.get(label, 0) + 1
            if r["kind"] == "decide" and r.get("decision") == "ok":
                oks.append(items[key]["title"])
            if r["kind"] == "decide" and r.get("decision") == "send_back":
                sent_back.append(items[key]["title"])
    return {
        "responses": total,
        "items": len(responses),
        "unanswered": len(items) - len(responses),
        "counts": counts,
        "ok": oks,
        "sent_back": sent_back,
    }


def _apply_decide(c, who, item, r):
    """One decision with the person's own identity: exactly what the Needs-you buttons do, through the task calls."""
    decision, text = r["decision"], (r.get("text") or "")[:2000]
    kind, ident = item["key"].split(":", 1)
    if kind == "ask":
        task_id = task_asks.task_of(c, ident)
        if not task_id:
            raise Problem("not_found", f"{item['title']} is not asked any more", 404)
        if decision == "ok":
            if task_asks.grant(c, ident):
                raise Problem("decision", task_asks.FROM_PREVIEW, 409)
            refused = task_asks.refuses_ok_by(c, who, ident, "ok")  # the same check as the answer route
            if refused:
                # An OK is the person's own, in Tico: whoever walks the list for them may send back, never OK.
                raise Problem("ok_not_yours", refused, 403)
            H.task_answer(c, who.actor, task_id, ident, "ok", text or None, own_click=task_asks.own_click(who))
            return f"OK'd: {item['title']}"
        if decision == "send_back":
            H.task_answer(c, who.actor, task_id, ident, "no", text)
            return f"Sent back: {item['title']}"
        H.task_answer(c, who.actor, task_id, ident, text)
        return f"Answered \"{item['title']}\""
    if decision == "done":
        H.task_done(c, who.actor, ident, text)
        return f"Done: {item['title']}"
    H.task_close(c, who.actor, ident, text)
    return f"Closed: {item['title']}"


def _task_of_key(c, key):
    """The task an item is about: the task itself, or the task an ask is on."""
    kind, ident = key.split(":", 1)
    if kind == "task":
        return H.task(c, ident)
    if kind == "ask":
        found = task_asks.task_of(c, ident)
        return H.task(c, found) if found else None
    return None


def signature(c, who):
    """Who went through the batch, as the bot reads it: the person, and the assistant acting for
    them when it came in on a labelled personal token ("Ana (via grok-bot)")."""
    name = (H.human(c, who.actor) or {}).get("name") or H.actor_id(who.actor)
    return f"{name} (via {who.token_label})" if getattr(who, "token_label", "") else name


ACT = (
    "For each item below: a question gets a short, verifiable answer; an instruction gets done, "
    "with one line saying what you did; a standing rule is how to handle this kind of item from now "
    "on, so keep it where you will find it next time. Reply per item, starting each with its marker "
    "exactly as written, [item N], so the answer reaches {person} the next time you come up."
)


def _brief(person, done, act):
    """The one message a bot gets from a batch: what was already applied, then what it must do."""
    parts = [f"{person} went through what you are waiting on them for."]
    if done:
        parts.append("Already done (applied in the hub; nothing to redo):\n" + "\n".join(done))
    if act:
        parts.append("For you to act on. " + ACT.format(person=person) + "\n\n" + "\n\n".join(act))
    else:
        parts.append("Nothing for you to act on; no reply needed.")
    return "\n\n".join(parts)


@contextlib.contextmanager
def _savepoint(c, name):
    """A write that may fail without taking the batch with it: on an error, only what it wrote is rolled back."""
    c.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        c.execute(f"ROLLBACK TO {name}")
        c.execute(f"RELEASE {name}")
        raise
    c.execute(f"RELEASE {name}")


def _comment_instead(c, who, to, lines, detail):
    """A bot the person may not message still sees what it must act on: each line becomes a
    comment on its task. Notes with no task are a failure, said."""
    applied, errors = [], []
    for key, line in lines:
        task = _task_of_key(c, key)
        if not task:
            errors.append(f"{H.actor_name(c, to)} could not be told ({detail}): {line.splitlines()[0]}")
            continue
        try:
            with _savepoint(c, "batch_comment"):
                H.task_comment(c, who.actor, task["id"], line.split("\n", 1)[-1])
            applied.append(f"Commented on {line.splitlines()[0]} (could not message {H.actor_name(c, to)})")
        except Exception as e:  # a refusal is a line in the record, not an abort; its half-write is rolled back
            errors.append(f"{line.splitlines()[0]}: {_clip(str(e), 200)}")
    return applied, errors


def _deliver(c, who, batch_id, outbox, record, send):
    """One message per bot in the person's chat with it: the record of what was decided and what it
    must act on, so the bot's own conversation holds the whole batch. A record alone is quiet (read,
    not run: the decisions already reached the bot their own way). A bot the person may not message
    gets its part as task comments instead, and the result says so. Returns (applied lines, errors,
    sent counts, recorded counts, first message id)."""
    applied, errors, sent, recorded, first = [], [], {}, {}, None
    person = signature(c, who)
    for to in dict.fromkeys(list(outbox) + list(record)):
        lines, done = outbox.get(to) or [], record.get(to) or []
        public_lines, public_done = [], []
        for collection, output in ((lines, public_lines), (done, public_done)):
            for key, line in collection:
                task = _task_of_key(c, key)
                private = bool(task and H.task_private(c, task))
                if private:
                    # Each private record stays on its task, never copied to a bot room.
                    if task:
                        try:
                            with _savepoint(c, "batch_record"):
                                H.task_comment(c, who.actor, task["id"], line)
                            applied.append("Recorded on its private task")
                        except Exception as e:
                            errors.append(_clip(str(e), 200))
                    continue
                output.append((key, line))
        lines, done = public_lines, public_done
        if not lines and not done:
            continue
        keys = [key for key, _ in lines + done]
        text = _brief(person, [line for _, line in done], [line for _, line in lines])
        refs = {
            "live": {
                "kind": "batch",
                "batch": batch_id,
                "items": keys,
                **({"via": who.token_label} if getattr(who, "token_label", "") else {}),
            }
        }
        if not lines:
            refs["quiet"] = True
        try:
            msg = send(c, who, text, refs, to)
        except Problem as e:
            more, failed = _comment_instead(c, who, to, lines, e.detail)
            applied += more
            errors += failed
            continue
        if lines:
            first = first or msg["id"]
            sent[to] = len(lines)
        if done:
            recorded[to] = len(done)
        applied.append(
            f"Sent {len(lines)} to {H.actor_name(c, to)}"
            if lines
            else f"Recorded {len(done)} for {H.actor_name(c, to)}"
        )
    return applied, errors, sent, recorded, first


def _record_line(item, r, line):
    """What the bot's record says about one applied response, with the person's own words."""
    text = r.get("text") or ""
    if r["kind"] == "decide" and r.get("decision") == "answer":
        return f"- Answered \"{item['title']}\": {text}"
    return f"- {line}" + (f": \"{text}\"" if text and r["kind"] == "decide" else "")


# What each response's words are, as the org rule check reads them (backend/task_rule_gate.py): the operation.
SAID = {
    "ok": "task_ask_answer",
    "send_back": "task_ask_answer",
    "answer": "task_ask_answer",
    "done": "task_done",
    "close": "task_move",
}


def said(c, who, batch_id):
    """Each response with words of the person's own, for the rule check before commit: ((key, i), task id or None,
    the bot it reaches, operation, text). The words reach the bot (a decision's note, an answer, an instruction or a
    rule, by message or as a comment on the task) and the task's people."""
    batch = _load(c, who.actor, batch_id)
    out = []
    for item in _items(batch):
        task = _task_of_key(c, item["key"])
        for i, r in enumerate(_responses(batch).get(item["key"], [])):
            if not str(r.get("text") or "").strip() or r["kind"] in ("skip", "later"):
                continue
            operation = SAID.get(r.get("decision"), "task_move") if r["kind"] == "decide" else "batch_note"
            out.append(
                ((item["key"], i), task["id"] if task else None, group_of(item, who.actor), operation, r["text"])
            )
    return out


def commit(c, who, batch_id, send, rules=None):
    """Apply every response. Decisions and snoozes here; then one message to each bot the items
    came from, in the person's chat with it: what was decided, and the questions, instructions and
    rules it must act on, whose reply comes back on those items. One item's failure is recorded,
    not fatal: each decision runs in its own savepoint, so a refused one leaves nothing half done.
    `rules(c, key, i, r)`: the org rule check's verdict on a response's words (`said`), applied before any of its
    item's responses is: (r, None) to go with r (its text with a warning line when warned), (r, line) when held, a
    Problem when the check refuses it. Gated first, so one response's write does not make the next one's check stale."""
    batch = _load(c, who.actor, batch_id)
    if batch["state"] != "open":
        raise Problem("state", f"This batch is {batch['state']}", 409)
    items = {it["key"]: it for it in _items(batch)}
    order = [it["key"] for it in _items(batch)]
    responses = _responses(batch)
    applied, errors, outbox, record = [], [], {}, {}
    for n, key in enumerate(order, 1):
        item = items[key]
        to = group_of(item, who.actor)
        gated = []
        for i, r in enumerate(responses.get(key, [])):
            try:
                r, held = rules(c, key, i, r) if rules else (r, None)
            except Problem as e:
                errors.append(f"{item['title']}: {e.detail}")
                continue
            if held:
                errors.append(f"{item['title']}: {held}")
                continue
            gated.append(r)
        for r in gated:
            try:
                line = None
                if r["kind"] == "decide":
                    c.execute("SAVEPOINT batch_decide")
                    try:
                        line = _apply_decide(c, who, item, r)
                    except Exception:
                        c.execute("ROLLBACK TO batch_decide")
                        c.execute("RELEASE batch_decide")
                        raise
                    c.execute("RELEASE batch_decide")
                elif r["kind"] == "rule":
                    H.event(
                        c,
                        who.actor,
                        "batch.rule",
                        key,
                        {"text": r["text"], "bot": to, "batch": batch["id"], "item": key},
                    )
                    applied.append(f"Rule for {H.actor_name(c, to)}: {_clip(r['text'], 120)}")
                    outbox.setdefault(to, []).append(
                        (
                            key,
                            f"[item {n}] {item['title']} ({item['kind']}, {key})\n"
                            f"{who.actor} sets a standing rule: {r['text']}",
                        )
                    )
                elif r["kind"] == "later":
                    H.event(c, who.actor, "batch.later", key, {"until": r["until"], "batch": batch["id"], "item": key})
                    line = f"Later ({r['until'][:10]}): {item['title']}"
                elif r["kind"] == "skip":
                    applied.append(f"Skipped: {item['title']}")
                else:
                    what = "asks" if r["kind"] == "needs_info" else "says"
                    outbox.setdefault(to, []).append(
                        (key, f"[item {n}] {item['title']} ({item['kind']}, {key})\n{who.actor} {what}: {r['text']}")
                    )
                if line:
                    applied.append(line)
                    record.setdefault(to, []).append((key, _record_line(item, r, line)))
            except Problem as e:
                errors.append(f"{item['title']}: {e.detail}")
            except Exception as e:  # a refusal from the write layer is a line in the record, not an abort
                errors.append(f"{item['title']}: {_clip(str(e), 200)}")
    delivered, failed, sent, recorded, message_id = _deliver(c, who, batch["id"], outbox, record, send)
    applied += delivered
    errors += failed
    c.execute(
        "UPDATE batches SET state='committed', committed_at=?, message_id=? WHERE id=?",
        (H.now(), message_id, batch["id"]),
    )
    H.event(
        c,
        who.actor,
        "batch.committed",
        batch["id"],
        {
            "responses": len(responses),
            "applied": applied,
            "errors": errors,
            "sent": sent,
            "items": order,
            "scope": batch.get("scope"),
        },
    )
    return {
        "committed": True,
        "id": batch["id"],
        "scope": batch.get("scope"),
        "applied": applied,
        "errors": errors,
        "sent": {H.actor_id(k): v for k, v in sent.items()},
        "recorded": {H.actor_id(k): v for k, v in recorded.items()},
        "unanswered": len(items) - len(responses),
    }


def up_next(line, scope):
    """The next group after a commit: the first with something new, never the one just done."""
    fresh = [g for g in line if g["who"] != scope and g["items"] > g["awaiting"]]
    return fresh[0] if fresh else None


def abandon(c, actor, batch_id):
    batch = _load(c, actor, batch_id)
    if batch["state"] != "open":
        raise Problem("state", f"This batch is {batch['state']}", 409)
    c.execute("UPDATE batches SET state='abandoned' WHERE id=?", (batch["id"],))
    H.event(c, actor, "batch.abandoned", batch["id"])
    return {"abandoned": True, "id": batch["id"]}
