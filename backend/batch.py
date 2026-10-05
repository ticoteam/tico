"""A batch: what needs a person, frozen, walked one item at a time, responses collected and
applied together on commit.

The person goes through the list giving a response per item; nothing is applied while they
talk. Commit applies the decisions with the person's own identity (only a person decides an
approval), records snoozes, and sends each bot one message in the person's chat with it: what was
decided, and the questions, instructions and rules it must act on. Each bot's reply, split per
item, comes back on those items the next time that bot comes up. Any front end runs the same
loop: the hub tools an outside agent (Grok Bot, Meta Muse) calls, a page.

A batch is what bots are waiting on the person for, never anything else: the
person's own tasks and other people's requests are on the Tasks page, not here, and nothing goes
to an assistant in between. It is the whole list, or one bot's part of it (`scope`). "Who needs
me" starts the bot that most needs the person, and commit says who is next: the bots come to the
person one at a time, most important first.
"""
import json
import re

from . import hubdb as H
from .store import Problem
from . import task_privacy as privacy
from .views import needs_order

KINDS = ("decide", "needs_info", "instruct", "rule", "skip", "later")
DECISIONS = ("approve", "decline", "done", "close", "answer")
VERDICTS = ("decide", "skip", "later")
LATER_DAYS = 1
REPORT_DAYS = 14              # how far back a bot's reply to a batch still comes back on its items
MARKER = re.compile(r"^\s*\[item (\d+)\]", re.M)
NEXT = "next"                 # the scope that means "whichever bot most needs the person"


def _clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _snoozed(c, actor):
    """Item keys the person said "later" to, still inside their window."""
    now = H.now()
    out = set()
    for row in c.execute("SELECT target, detail_json FROM events WHERE actor=? AND action='batch.later' "
                         "ORDER BY ts", (actor,)):
        until = (H._json(row["detail_json"], {}) or {}).get("until") or ""
        if until > now:
            out.add(row["target"])
        else:
            out.discard(row["target"])
    return out


def _children(c, task_id, owner):
    return H._rows(c.execute("SELECT * FROM tasks WHERE parent_id=? AND owner=? AND status IN ('open','doing','waiting','review','ready') "
                             "ORDER BY created", (task_id, owner)))


def _row(it, actor):
    """One item as a front end presents it: the whole question, never a clipped title."""
    key = ("approval:" if it["kind"] == "approval" else "task:") + str(it["id"])
    row = {"key": key, "kind": it["kind"], "title": _clip(it.get("title") or it.get("first_line"), 160)}
    frm = (it.get("requester") if it["kind"] == "approval" else it.get("owner") if it["kind"] == "waiting"
           else (it.get("ask") or {}).get("from_actor") or it.get("origin_actor") or it.get("requester"))
    if frm:
        row["from"] = frm
    if it.get("status"):
        row["status"] = it["status"]
    if it.get("rank") is not None:
        row["rank"] = it["rank"]
    if (it.get("ask") or {}).get("body"):
        row["question"] = _clip(it["ask"]["body"], 1500)
    elif it.get("body"):
        row["body"] = _clip(it["body"], 800)
    if isinstance(it.get("payload"), dict) and it["payload"]:
        row["payload"] = _clip(json.dumps(it["payload"], sort_keys=True, default=str), 800)
    if it.get("note"):
        row["note"] = _clip(it["note"], 300)
    if it.get("created"):
        row["created"] = it["created"]
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


def relevant(c, actor, items):
    """The relevance check, deterministic: drop what the person deferred, replace a task with the
    open children the person owns, let an approval stand for the task it belongs to, and order
    bot requests above the person's own tasks above mail."""
    snoozed = _snoozed(c, actor)
    covered = {it.get("task_id") for it in items if it["kind"] == "approval" and it.get("task_id")}
    out, seen = [], set()
    for it in items:
        # A person's own parked work belongs on their task board, not in a spoken decision pass.
        # It has no other requester waiting for an answer and "waiting" explicitly means there is
        # nothing for the owner to do now. A later bot ask on that task changes its kind to a
        # question and brings it back into the pass.
        if (it["kind"] == "task" and it.get("owner") == actor and it.get("requester") == actor
                and it.get("status") == "waiting" and not it.get("ask")):
            continue
        if it["kind"] != "approval":
            if it["id"] in covered:
                continue
            kids = _children(c, it["id"], actor)
            if kids:
                for kid in kids:
                    kid = {**kid, "kind": "task", "ask": H.unanswered_ask(c, kid, actor=actor), "origin_actor": H.task_origin(c, kid),
                           "first_line": (kid.get("body") or "").split("\n")[0]}
                    if kid["ask"]:
                        kid["kind"] = "question"
                    row = _row(kid, actor)
                    if row["key"] not in seen and row["key"] not in snoozed:
                        seen.add(row["key"]); out.append(row)
                continue
        row = _row(it, actor)
        if row["key"] in seen or row["key"] in snoozed:
            continue
        seen.add(row["key"]); out.append(row)
    # Decisive kinds first (an approval, a question), then the person's queue in its rank
    # order (views.needs_order: what needs a person is what is assigned to them, in order).
    out.sort(key=lambda r: needs_order(r.get("from"), r["kind"], r.get("created"),
                                       r.get("rank") if r["kind"] == "task" else None))
    return out


def group_of(row, person):
    """The bot an item is waiting with, or None when no bot is: the person's own task, or another
    person's request. Only a bot's items go in a batch."""
    frm = row.get("from") or ""
    return frm if H.is_bot(frm) else None


def _name(c, actor):
    return (H.bot(c, actor) or {}).get("name") or H.actor_id(actor)


def _rank(row):
    return needs_order(row.get("from"), row["kind"], row.get("created"),
                       row.get("rank") if row["kind"] == "task" else None)


def lineup(c, person, rows):
    """Who needs the person, most important first: each group ranked by its most pressing item
    (an approval, then a question, then the queue in rank order), more items breaking a tie.
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
    return [{"who": g["who"], "name": _name(c, g["who"]), "items": g["items"],
             "awaiting": g["awaiting"], "top": g["top"]} for g in ordered]


def _sent(c, person, since):
    """The batch messages this person sent since `since`, each with the item keys it carried."""
    rows = c.execute("SELECT m.id, m.to_actor, m.refs_json, b.id AS batch, b.items_json, b.report_json, "
                     "b.message_id AS first "
                     "FROM messages m JOIN batches b ON b.id=json_extract(m.refs_json,'$.live.batch') "
                     "WHERE m.from_actor=? AND json_extract(m.refs_json,'$.live.kind')='batch' "
                     "AND m.created>=? ORDER BY m.created DESC", (person, since)).fetchall()
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
        if _consumed(sent["report_json"], sent["id"], sent["first"]):
            continue
        keys = ((H._json(sent["refs_json"], {}) or {}).get("live") or {}).get("items") or []
        reply = c.execute("SELECT * FROM messages WHERE in_reply_to=? AND from_actor=? "
                          "ORDER BY created DESC LIMIT 1", (sent["id"], sent["to_actor"])).fetchone()
        if not reply:
            for key in keys:
                awaiting.setdefault(key, sent["to_actor"])
            continue
        if not privacy.message_readable(c, person, reply):
            continue
        items = json.loads(sent["items_json"] or "[]")
        parts = MARKER.split(reply["body"] or "")
        answers, loose = {}, parts[0].strip()
        for n, body in zip(parts[1::2], parts[2::2]):
            idx = int(n) - 1
            if 0 <= idx < len(items):
                answers[items[idx]["key"]] = body.strip()
            else:
                loose += ("\n" if loose else "") + body.strip()
        reports.append({"message": sent["id"], "reply": reply["id"], "batch": sent["batch"],
                        "to": sent["to_actor"], "answers": answers, "loose": loose})
    return reports, awaiting


def _consume(c, report):
    row = c.execute("SELECT report_json FROM batches WHERE id=?", (report["batch"],)).fetchone()
    data = H._json(row["report_json"] if row else None, {}) or {}
    data.setdefault("consumed", {})[report["message"]] = report["reply"]
    c.execute("UPDATE batches SET report_json=? WHERE id=?", (json.dumps(data), report["batch"]))


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
    row = c.execute("SELECT * FROM batches WHERE person=? AND state='open' ORDER BY created DESC LIMIT 1",
                    (actor,)).fetchone()
    return dict(row) if row else None


def candidates(c, actor, items):
    """What would go in a new batch: needs-you after the relevance check, with each bot's replies
    attached to the items they answer and its loose notes as a report item from that bot. Nothing
    is consumed here."""
    rows = [r for r in relevant(c, actor, items) if group_of(r, actor)]
    reports, awaiting = _pending(c, actor)
    shown = {}
    for report in reports:                     # newest first: the latest answer to an item wins
        for key, text in report["answers"].items():
            shown.setdefault(key, (text, report))
    for row in rows:
        if row["key"] in shown:
            row["answer"] = _clip(shown[row["key"]][0], 4000)
            row["_report"] = shown[row["key"]][1]["message"]
        elif row["key"] in awaiting:
            row["awaiting"] = awaiting[row["key"]]
    notes = [{"key": f"report:{r['message']}", "kind": "report", "title": "Notes from your last batch",
              "from": r["to"], "body": _clip(r["loose"], 4000), "_report": r["message"]}
             for r in reports if r["loose"]]
    return rows, notes, reports


def start(c, actor, items, snapshot, scope=None):
    """The open batch if there is one (resume), otherwise a new one from `items` (needs-you rows):
    the whole list, or one group of it when `scope` names a bot, a person, `me`, or `next`."""
    rows, notes, reports = candidates(c, actor, items)
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
    batch = {"id": H.new_id(), "person": actor, "state": "open", "created": H.now(), "committed_at": None,
             "items_json": json.dumps(rows), "cursor": 0, "responses_json": "{}",
             "alerts_json": json.dumps(alerts(snapshot)), "message_id": None, "report_json": None,
             "scope": group}
    c.execute("INSERT INTO batches(id,person,state,created,committed_at,items_json,cursor,responses_json,"
              "alerts_json,message_id,report_json,scope) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              tuple(batch[k] for k in ("id", "person", "state", "created", "committed_at", "items_json", "cursor",
                                       "responses_json", "alerts_json", "message_id", "report_json", "scope")))
    H.event(c, actor, "batch.started", batch["id"], {"items": len(rows), "alerts": len(alerts(snapshot)),
                                                     "scope": group})
    return {**view(c, batch), "lineup": line}


def _items(batch):
    return json.loads(batch["items_json"] or "[]")


def _responses(batch):
    return json.loads(batch["responses_json"] or "{}")


def view(c, batch, resumed=False):
    items, responses = _items(batch), _responses(batch)
    cursor = int(batch["cursor"] or 0)
    out = {"id": batch["id"], "state": batch["state"], "total": len(items), "responded": len(responses),
           "alerts": json.loads(batch["alerts_json"] or "[]"), "resumed": resumed,
           "scope": batch.get("scope")}
    if batch["state"] != "open":
        return out
    if cursor < len(items):
        item = {**items[cursor], "n": cursor + 1}
        if item["key"] in responses:
            item["response"] = responses[item["key"]]
        out.update({"position": f"{cursor + 1} of {len(items)}", "item": item})
    else:
        out.update({"end": True, "summary": summary(batch),
                    "note": "The list is done. Say the summary and ask whether to commit."})
    return out


def _load(c, actor, batch_id):
    row = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
    if not row or row["person"] != actor:
        raise Problem("not_found", "Batch not found", 404)
    return dict(row)


def next_item(c, actor, batch_id):
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
        if item["kind"] == "approval" and decision not in ("approve", "decline"):
            raise Problem("decision", f"{item['title']} is an approval: approve or decline it", 422)
        if item["kind"] not in ("approval", "task") and decision in ("approve", "decline"):
            raise Problem("decision", f"{item['title']} is a {item['kind']}, not a yes-or-no decision task", 422)
        if item["kind"] == "report":
            raise Problem("decision", "Notes are not decided; instruct or skip", 422)
        if decision == "answer" and not text:
            raise Problem("decision", "An answer needs the text to send", 422)
        if item["key"].startswith("task:") and decision in ("approve", "decline", "done", "close") and not text:
            task = H.task(c, item["key"].split(":", 1)[1])
            if (task and H.is_human(task.get("owner")) and H.is_bot(task.get("requester"))
                    and (decision == "done" or task["status"] not in ("done", "declined"))):
                raise Problem("decision", "Tell the requesting bot what you decided or completed", 422)
        response["decision"] = decision
    elif kind in ("needs_info", "instruct", "rule") and not text:
        raise Problem("text", f"A {kind.replace('_', ' ')} response needs the text", 422)
    elif kind == "later":
        response["until"] = until or H.shift(H.now(), days=LATER_DAYS)
    responses = _responses(batch)
    # One verdict per item (a decision, a skip or a later replaces the last); questions,
    # instructions and rules accumulate: "approve it, and always archive these" is two responses.
    kept = [r for r in responses.get(item["key"], [])
            if not (kind in VERDICTS and r["kind"] in VERDICTS)]
    responses[item["key"]] = kept + [response]
    c.execute("UPDATE batches SET responses_json=? WHERE id=?", (json.dumps(responses), batch["id"]))
    return {"recorded": True, "n": idx + 1, "key": item["key"], "response": response,
            "responded": len(responses), "total": len(items)}


def summary(batch):
    """What the commit will do, counted, approvals named: the read-back before a yes."""
    items = {it["key"]: it for it in _items(batch)}
    responses = _responses(batch)
    counts, total = {}, 0
    approving, declining = [], []
    for key, rs in responses.items():
        for r in rs:
            total += 1
            label = r["kind"] if r["kind"] != "decide" else r.get("decision")
            counts[label] = counts.get(label, 0) + 1
            if r["kind"] == "decide" and r.get("decision") == "approve":
                approving.append(items[key]["title"])
            if r["kind"] == "decide" and r.get("decision") == "decline":
                declining.append(items[key]["title"])
    return {"responses": total, "items": len(responses), "unanswered": len(items) - len(responses),
            "counts": counts, "approving": approving, "declining": declining}


def _apply_decide(c, auth, who, item, r):
    """One decision with the person's own identity: exactly what the Needs-you buttons do."""
    decision, text = r["decision"], r.get("text") or ""
    kind, ident = item["key"].split(":", 1)
    if kind == "approval":
        auth.approval(c, who, ident, decide=True)
        verdict = "approved" if decision == "approve" else "declined"
        H.approval_decide(c, who.actor, ident, verdict, note=text[:2000])
        return f"{verdict.capitalize()}: {item['title']}"
    row = auth.task(c, who, ident)
    if item["kind"] == "waiting" and decision in ("done", "answer"):
        # The bot's own task waits on the person: "done" or an answer goes back on the task and wakes
        # the bot (task_comment also takes it off the person's list). Its status stays the bot's.
        said = text or "Done."
        H.task_comment(c, who.actor, ident, said)
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (ident,))
        return f"Told {H.actor_id(row['owner'])}: {_clip(said, 120)}"
    if decision in ("approve", "decline"):
        status = "done" if decision == "approve" else "declined"
        note = text or ("Approved." if decision == "approve" else "Declined.")
        H.task_update(c, who.actor, ident, status=status, note=note)
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (ident,))
        return f"{'Approved' if decision == 'approve' else 'Declined'}: {item['title']}"
    if decision == "done":
        H.task_update(c, who.actor, ident, status="done", note=text or None)
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (ident,))
        return f"Done: {item['title']}"
    if decision == "close":
        H.task_close(c, who.actor, ident, note=text)
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (ident,))
        return f"Closed: {item['title']}"
    ask = H.unanswered_ask(c, row, actor=privacy.actor(who))
    if ask and ask.get("to_actor") == who.actor:
        H.answer(c, who.actor, ask["id"], text)
        return f"Answered {H.actor_id(ask['from_actor'])}: {_clip(text, 120)}"
    to = H.task_origin(c, row) or row.get("requester")
    if not to or to == who.actor:
        raise Problem("answer", f"{item['title']} has nobody to answer", 422)
    H.say(c, who.actor, to, text, conversation_id=row.get("conversation_id"))
    return f"Answered {H.actor_id(to)}: {_clip(text, 120)}"


def signature(c, who):
    """Who went through the batch, as the bot reads it: the person, and the assistant acting for
    them when it came in on a labelled personal token ("Ana (via grok-bot)")."""
    name = (H.human(c, who.actor) or {}).get("name") or H.actor_id(who.actor)
    return f"{name} (via {who.token_label})" if getattr(who, "token_label", "") else name


ACT = ("For each item below: a question gets a short, verifiable answer; an instruction gets done, "
       "with one line saying what you did; a standing rule is how to handle this kind of item from now "
       "on, so keep it where you will find it next time. Reply per item, starting each with its marker "
       "exactly as written, [item N], so the answer reaches {person} the next time you come up.")


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


def _comment_instead(c, who, to, lines, detail):
    """A bot the person may not message still sees what it must act on: each line becomes a
    comment on its task. An approval has no task to hold it; that is a failure, said."""
    applied, errors = [], []
    for key, line in lines:
        kind, ident = key.split(":", 1)
        if kind != "task":
            errors.append(f"{H.actor_id(to)} could not be told ({detail}): {line.splitlines()[0]}")
            continue
        try:
            H.task_comment(c, who.actor, ident, line.split("\n", 1)[-1])
            applied.append(f"Commented on {line.splitlines()[0]} (could not message {H.actor_id(to)})")
        except Exception as e:  # a refusal is a line in the record, not an abort
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
                kind, ident = key.split(":", 1)
                task = H.task(c, ident) if kind == "task" else None
                source = H.approval(c, ident) if kind == "approval" else None
                private = bool(task and H.task_private(c, task)) or bool(source and not privacy.public_message(
                    c, H.message(c, source["message_id"])))
                if private:
                    # Each private record stays on its task, never copied to a bot room.
                    if task:
                        try:
                            H.task_comment(c, who.actor, ident, line)
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
        refs = {"live": {"kind": "batch", "batch": batch_id, "items": keys,
                         **({"via": who.token_label} if getattr(who, "token_label", "") else {})}}
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
        applied.append(f"Sent {len(lines)} to {H.actor_id(to)}" if lines
                       else f"Recorded {len(done)} for {H.actor_id(to)}")
    return applied, errors, sent, recorded, first


def _record_line(item, r, line):
    """What the bot's record says about one applied response, with the person's own words."""
    text = r.get("text") or ""
    if r["kind"] == "decide" and r.get("decision") == "answer":
        return f"- Answered \"{item['title']}\": {text}"
    return f"- {line}" + (f": \"{text}\"" if text and r["kind"] == "decide" else "")


def commit(c, auth, who, batch_id, send):
    """Apply every response. Decisions and snoozes here; then one message to each bot the items
    came from, in the person's chat with it: what was decided, and the questions, instructions and
    rules it must act on, whose reply comes back on those items. One item's failure is recorded,
    not fatal."""
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
        for r in responses.get(key, []):
            try:
                line = None
                if r["kind"] == "decide":
                    line = _apply_decide(c, auth, who, item, r)
                elif r["kind"] == "rule":
                    H.event(c, who.actor, "batch.rule", key, {"text": r["text"], "bot": to, "batch": batch["id"], "item": key})
                    applied.append(f"Rule for {H.actor_id(to)}: {_clip(r['text'], 120)}")
                    outbox.setdefault(to, []).append(
                        (key, f"[item {n}] {item['title']} ({item['kind']}, {key})\n"
                              f"{who.actor} sets a standing rule: {r['text']}"))
                elif r["kind"] == "later":
                    H.event(c, who.actor, "batch.later", key, {"until": r["until"], "batch": batch["id"], "item": key})
                    line = f"Later ({r['until'][:10]}): {item['title']}"
                elif r["kind"] == "skip":
                    applied.append(f"Skipped: {item['title']}")
                else:
                    what = "asks" if r["kind"] == "needs_info" else "says"
                    outbox.setdefault(to, []).append(
                        (key, f"[item {n}] {item['title']} ({item['kind']}, {key})\n{who.actor} {what}: {r['text']}"))
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
    c.execute("UPDATE batches SET state='committed', committed_at=?, message_id=? WHERE id=?",
              (H.now(), message_id, batch["id"]))
    H.event(c, who.actor, "batch.committed", batch["id"],
            {"responses": len(responses), "applied": applied, "errors": errors, "sent": sent, "items": order,
             "scope": batch.get("scope")})
    return {"committed": True, "id": batch["id"], "scope": batch.get("scope"), "applied": applied,
            "errors": errors, "sent": {H.actor_id(k): v for k, v in sent.items()},
            "recorded": {H.actor_id(k): v for k, v in recorded.items()},
            "unanswered": len(items) - len(responses)}


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
