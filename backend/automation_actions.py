"""What an automation's steps do in the hub: its actions, the turns it starts and the items `find` reads.

Each runs inside one short write transaction (backend/automations.py opens it) and reuses what the hub already has:
the rules' task and event runners (backend/rules.py), outgoing webhooks, updates, and tasks. An action never raises
for an ordinary refusal; it answers {"ok": False, "detail": ...} and the run records it.

Mail is changed on the computer that reads the mailbox, not here: archive, label, leave for you, draft, send and
forward on a message from mail are queued in `automation_mail_actions` for its runner to claim
(POST .../mail-actions/claim, POST .../mail-actions/{id}/result; backend/automations.py). The step waits until the
result comes back. On a Slack message, send_reply posts in the thread (the `slack_posts` outbox) and draft_reply
leaves the draft with the owner as a task.

`send_reply` honors the inbox's send switch (`automation_inboxes`, on unless set off): off saves a draft and logs
`automation.send_off`. The switch is read again when the send leaves: a mail send claimed, or a Slack post delivered,
after it went off is a draft too (`slack_held`, the claim route).

An automation acts as its owner (`acts_as`), never as Tico: the owner's task rights decide what it may change, and
every event its writes leave says `via: automation:<id>` (backend/automations.py sets it). Only a notice to a bot is
Tico's, as a rule's is. What it writes carries its chain (`auto["chain"]`, the automations that led here) so a bot's
turn on it cannot start them again.
"""
import json
import re

from . import rules as R
from . import task_run_models
from .store import H, Problem

MAIL_ACTIONS = {"archive": "archive", "label": "label", "leave_for_you": "leave", "draft_reply": "draft",
                "send_reply": "send", "forward": "forward"}
MAX_TEXT = 8000
MAX_ITEM = 4000


def actor_of(owner):
    """The owner as the hub names an actor: a person as human:<id>, a bot as bot:<slug>; a group has none of its own."""
    kind, _, ident = str(owner).partition(":")
    return {"person": "human:" + ident, "bot": "bot:" + ident}.get(kind, "")


def acts_as(c, auto):
    """Who the automation acts as: its owner, or for a group the group's lead (else the person it reports to)."""
    owner = auto["flow"]["owner"]
    actor = actor_of(owner)
    if actor:
        return actor
    from .automations import Chart
    lead = Chart(c).lead(owner.partition(":")[2])
    return {"p": "human:", "b": "bot:"}.get(lead[:1], "") + lead[2:] if lead else ""


def _chain_refs(auto):
    return {"automation_chain": list(auto.get("chain") or [])}


def _text(args, *keys):
    for key in keys:
        if args.get(key):
            return str(args[key])[:MAX_TEXT]
    return ""


def _fail(detail):
    return {"ok": False, "detail": str(detail)[:300]}


def run(c, auto, run_id, do, args, values, step=None):
    """Carry out one action for the automation `auto` ({id, flow}). Wrapped in a savepoint: a refused action writes
    nothing but its refusal. `step` is the producing step's id (a create_task's occurrence task is per step)."""
    c.execute("SAVEPOINT automation_action")
    try:
        if do == "create_task":
            result = _create_task(c, auto, run_id, args or {}, values or {}, step=step)
        else:
            result = ACTIONS[do](c, auto, run_id, args or {}, values or {})
        c.execute("RELEASE automation_action")
        return result
    except (H.Refused, Problem, ValueError, KeyError) as exc:
        c.execute("ROLLBACK TO automation_action")
        c.execute("RELEASE automation_action")
        return _fail(getattr(exc, "detail", None) or getattr(exc, "message", None) or exc or type(exc).__name__)


# ---------------------------------------------------------------------------------------------------- messages
def _message(values):
    message = values.get("message")
    return message if isinstance(message, dict) else None


def _queue_mail(c, auto, run_id, action, message, args, result=None):
    """`result` starts the action's result: a send the switch turned into a draft keeps `send_off` once reported."""
    aid = H.new_id()
    c.execute("INSERT INTO automation_mail_actions(id,automation_id,run_id,mailbox,msg_id,action,args,state,created_at,"
              "result) VALUES(?,?,?,?,?,?,?,'queued',?,?)",
              (aid, auto["id"], run_id, str(message.get("mailbox") or "").strip().lower(), str(message.get("id") or ""),
               action, json.dumps(args, default=str), H.now(), json.dumps(result) if result else None))
    return {"ok": True, "detail": f"{action} queued", "mail_action": aid, "queued": True}


def send_on(c, inbox):
    """The inbox's send switch: on unless its person (or the top of the org) turned it off."""
    row = c.execute("SELECT send FROM automation_inboxes WHERE inbox=?", (str(inbox or "").strip().lower(),)).fetchone()
    return row is None or bool(row["send"])


def _inbox(message):
    return message.get("inbox") or message.get("mailbox") or ""


def _mail_or_slack(do):
    def act(c, auto, run_id, args, values):
        message = _message(values)
        if not message:
            return _fail(f"{do} needs a message")
        if message.get("source") == "slack":
            return _slack(c, auto, run_id, do, args, message)
        action = MAIL_ACTIONS[do]
        if do == "send_reply" and not send_on(c, _inbox(message)):
            H.event(c, acts_as(c, auto) or H.KEEPER, "automation.send_off", auto["id"],
                    {"run": run_id, "mailbox": message.get("mailbox"), "msg_id": message.get("id")})
            return {**_queue_mail(c, auto, run_id, "draft", message, args, {"send_off": True}), "detail": "send_off",
                    "send_off": True}
        if do == "forward" and args.get("webhook"):
            return _webhook(c, auto, "custom.forward.automation", args["webhook"], message)
        return _queue_mail(c, auto, run_id, action, message, args)
    return act


def _slack(c, auto, run_id, do, args, message):
    text = _text(args, "body", "text")
    if do == "send_reply" and send_on(c, _inbox(message)):
        if not text:
            return _fail("send_reply needs a body")
        bot = R._slug(args.get("bot")) or _owner_bot(c, auto)
        bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        if not bot:
            return _fail("send_reply in Slack needs a bot to post as")
        conv = R._bot_room(c, bot)
        msg = H._write_message(c, H.bot_actor(bot), conv.get("owner_id") or H.KEEPER, text, conv, "say",
                               {"automation": auto["id"], "automation_run": run_id,
                                "automation_inbox": _inbox(message), **_chain_refs(auto)}, None, None)
        now = H.now()
        c.execute("INSERT INTO slack_posts(message_id,channel,thread_ts,bot,text,state,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,'ready',?,?)",
                  (msg["id"], message.get("channel"), message.get("thread_ts") or message.get("ts"), bot, text, now, now))
        return {"ok": True, "detail": "posted", "message": msg["id"]}
    if do in ("send_reply", "draft_reply"):
        if do == "send_reply":
            H.event(c, acts_as(c, auto) or H.KEEPER, "automation.send_off", auto["id"], {"run": run_id})
        title = f"Draft reply: {str(message.get('text') or '')[:80]}".strip()
        return _create_task(c, auto, run_id, {"title": title, "body": text}, {})
    return {"ok": True, "detail": "nothing to do in Slack"}


def slack_held(c, post):
    """An automation's Slack reply whose inbox's send switch went off after it was queued (`slack_posts` row `post`):
    not posted but cancelled, and the owner gets the draft as a task, as with the switch off when it was queued.
    Returns whether it was held."""
    refs = (H.message(c, post["message_id"]) or {}).get("refs") or {}
    inbox = refs.get("automation_inbox")
    if not inbox or send_on(c, inbox):
        return False
    c.execute("UPDATE slack_posts SET state='cancelled',error='send_off',updated_at=? WHERE message_id=?",
              (H.now(), post["message_id"]))
    row = c.execute("SELECT id, flow FROM automations WHERE id=?", (refs.get("automation"),)).fetchone()
    if row:
        auto, run_id = {"id": row["id"], "flow": json.loads(row["flow"])}, refs.get("automation_run")
        H.event(c, actor_of(auto["flow"]["owner"]), "automation.send_off", auto["id"], {"run": run_id})
        made = _create_task(c, auto, run_id, {"title": f"Draft reply: {post['text'][:80]}", "body": post["text"]}, {})
        from . import automations
        automations.restate(c, run_id, "message", post["message_id"],
                            {"detail": "send_off", "send_off": True, "task": made.get("task")})
    return True


def _owner_bot(c, auto):
    kind, _, ident = auto["flow"]["owner"].partition(":")
    return ident if kind == "bot" else auto.get("assistant_bot") or ""


# ---------------------------------------------------------------------------------------------------- tasks
def task_of(c, auto, values):
    """The run's task, when its owner may read it; a task it may not read is refused, never acted on."""
    task = values.get("task")
    tid = task.get("id") if isinstance(task, dict) else None
    row = H.task(c, tid) if tid else None
    if row and not H.task_private_readable(c, acts_as(c, auto), row):
        raise ValueError(f"the automation's owner may not read task {tid}")
    return row


def _actor(c, auto):
    actor = acts_as(c, auto)
    if not actor:
        raise ValueError("this group's automation has no lead to act as")
    return actor


def _create_task(c, auto, run_id, args, values, step=None):
    title = " ".join(_text(args, "title").split())[:300]
    if not title:
        return _fail("create_task needs a title")
    actor = _actor(c, auto)
    owner = str(args.get("owner") or "")
    found = H.resolve_bot(c, owner) if owner and ":" not in owner else None
    owner = owner if ":" in owner else (("bot:" + found["id"]) if found else
                                        ("human:" + owner) if owner else actor)
    if owner.startswith("person:"):
        owner = "human:" + owner[7:]
    due, marker, refs = _occurrence(c, auto, args, values)
    row = H.task_create(c, actor, title, _text(args, "body") or title, owner, due=due, lint=False,
                        deduplicate=False, wake_refs={**refs, **_chain_refs(auto)}, occurrence=marker,
                        occurrence_step=str(step) if marker and step else None, rule_due_id=refs.get("rule_due_id"))
    return {"ok": True, "detail": f"task {row['id']}", "task": row["id"]}


def _occurrence(c, auto, args, values):
    """A task a run opens: its due, the occurrence marker and the refs that name it. A due-date run's task is due at
    the end of its due day in the trigger's zone unless the step says otherwise (the step's `due` wins), and the
    marker is "<automation_id>:<due_on>" (`tasks.occurrence`), so the task stays that day's deadline. Other runs get
    no marker: (None, None, {automation}).

    The rule due date the task stands for (`rule_due_id`) is never the run's input, a manual run's sample included: it
    is the automation's own trigger's, and only while the automation is that due date's routine (rule_due.links)."""
    occurrence = values.get("due") if isinstance(values.get("due"), dict) else {}
    if not occurrence.get("due_on"):
        return str(args.get("due") or "") or None, None, {"automation": auto["id"]}
    from . import automation_due as D
    from . import rule_due
    from datetime import date
    end = D.due_end(date.fromisoformat(occurrence["due_on"]), occurrence.get("tz") or "UTC")
    trigger = auto["flow"].get("trigger") or {}
    due_id = str(trigger.get("rule_due_id") or "") if trigger.get("type") == "due" else ""
    linked = bool(due_id) and H._has_table(c, "rule_due_dates") and rule_due.links(c, due_id, auto["id"])
    refs = {"automation": auto["id"], "due_on": occurrence["due_on"], **({"rule_due_id": due_id} if linked else {})}
    return (str(args.get("due") or "") or end), f"{auto['id']}:{occurrence['due_on']}", refs


def _on_task(name):
    def act(c, auto, run_id, args, values):
        task = task_of(c, auto, values)
        if not task:
            return _fail(f"{name} needs a task")
        actor = _actor(c, auto)
        said = _text(args, "text", "note") or f"Automation {auto['flow'].get('name') or auto['id']}"
        if name == "note":
            H.task_update(c, actor, task["id"], note=said)
            return {"ok": True, "detail": "noted"}
        if name == "assign":
            owner = str(args.get("owner") or "").replace("person:", "human:")
            if not owner:
                return _fail("assign needs an owner")
            found = H.resolve_bot(c, owner) if ":" not in owner else None
            owner = owner if ":" in owner else ("bot:" + found["id"] if found else "human:" + owner)
            if H.task_private(c, task) and not H.task_private_readable(c, owner, task):
                return _fail("the new owner cannot read this private task")
            H.task_update(c, actor, task["id"], owner=owner, note=said)
            return {"ok": True, "detail": f"assigned to {owner}"}
        tags = args.get("tag") or args.get("name")
        have = H.task_labels(task)
        keys = have + [k for k in (tags if isinstance(tags, list) else [tags]) if k not in have]
        if keys == have:
            return {"ok": True, "detail": "already labelled"}
        H.task_update(c, actor, task["id"], labels=keys, note=said)
        return {"ok": True, "detail": "labelled"}
    return act


def _label(c, auto, run_id, args, values):
    if _message(values) and not values.get("task"):
        return _mail_or_slack("label")(c, auto, run_id, args, values)
    if not (args.get("tag") or args.get("name")):
        return _fail("label needs a tag")
    return _on_task("label")(c, auto, run_id, args, values)


def _told(c, auto, bot):
    """Where a notice to `bot` goes, when the automation may tell it (backend/automations.py `may_tell`, checked on
    save and again here): the owner's own Assistant room for the Assistant, else the bot's own room (None)."""
    from .automations import may_tell
    owner, assistant_bot = auto["flow"]["owner"], auto.get("assistant_bot")
    if not may_tell(c, owner, bot, assistant_bot):
        raise ValueError(f"{owner} may not tell {bot}: only its own bots, a group it leads or its own Assistant")
    if bot == assistant_bot and owner.startswith("person:"):
        from . import assistant
        return assistant.ensure_room(c, "human:" + owner.partition(":")[2], assistant_bot)
    return None


def _note(c, auto, run_id, args, values):
    """On the run's task, else to `bot`; with neither (a sample task with no id, a mail, a schedule) the note is kept
    in the run log only, as its step's `note`."""
    bot = R._slug(args.get("bot"))
    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
    if task_of(c, auto, values):
        return _on_task("note")(c, auto, run_id, args, values)
    if not bot:
        return {"ok": True, "detail": "noted in the run log", "note": _text(args, "text", "note")}
    room = _told(c, auto, bot)
    ok, detail = R._tell_bot(c, "automation:" + auto["id"], bot, _text(args, "text", "note") or "A note.",
                             {"automation": auto["id"], "automation_run": run_id, **_chain_refs(auto)}, wake=False,
                             room=room)
    return {"ok": ok, "detail": detail}


def _wake_bot(c, auto, run_id, args, values):
    bot = R._slug(args.get("bot")) or _owner_bot(c, auto)
    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
    text = _text(args, "text", "prompt") or f"Automation {auto['flow'].get('name')}: take a look."
    task = task_of(c, auto, values)
    room = _told(c, auto, bot)
    ok, detail = R._tell_bot(c, "automation:" + auto["id"], bot, text,
                             {"automation": auto["id"], "automation_run": run_id, **_chain_refs(auto)}, wake=True,
                             task=task, room=room)
    return {"ok": ok, "detail": detail}


POST_TO = ("updates", "market")
BRIEFING_LINK = "#/strategy/market/briefing"
POINTER_WORDS = 14     # the Updates line for a briefing: its title cut to fit a daily bullet


def _turn_output(values):
    """The newest turn answer in the run's values (a `bot_turn` or `llm` step that waited): {ref, turn, text}."""
    found = [v for v in values.values() if isinstance(v, dict) and "ref" in v and isinstance(v.get("text"), str)]
    return found[-1] if found else {}


def _briefing(text, title):
    """A turn's briefing as Market keeps it: the title given, else its first line without markdown marks; the body
    without that line when it was a heading and more follows."""
    lines = [line for line in text.strip().splitlines()]
    first = re.sub(r"^\s*(?:#+|[-*•])\s*", "", lines[0] if lines else "").strip()
    rest = "\n".join(lines[1:]).strip()
    body = rest if not title and lines and lines[0].lstrip().startswith("#") and rest else text.strip()
    return (str(title or first or "Market briefing").strip()[:200]), body


def _may_write_market(c, author):
    """Market's writers (backend/market.py): the Librarian, and the top of the org."""
    from . import market as MK
    from .automations import top_person
    return author in MK.WRITERS or author == "human:" + str(top_person(c) or "")


def _post_update(c, auto, run_id, args, values):
    """Post the text (else what the turn before wrote) as the owner. `to` is where: `updates` (the default), and
    `market`, the week's briefing on Market. With both, Updates gets one line pointing at the briefing, added to the
    owner's daily update. Each post carries `source: {automation, run, task}`."""
    to = args.get("to", ["updates"])
    to = [to] if isinstance(to, str) else to
    if not isinstance(to, list) or not to or any(t not in POST_TO for t in to):
        return _fail("to lists where to post: updates, market")
    said = _turn_output(values)
    text = _text(args, "text", "body") or str(said.get("text") or "")[:MAX_TEXT]
    if not text.strip():
        return _fail("post_update needs text")
    author = actor_of(auto["flow"]["owner"])
    if not author:
        return _fail("a group's automation has no one to post an update as")
    from . import updates
    source = {"automation": auto["id"], "run": run_id, **({"task": said["turn"].get("task")}
                                                          if isinstance(said.get("turn"), dict) else {})}
    out = {"ok": True, "detail": "posted"}
    poster = H.actor_id(author) if author.startswith("bot:") else author
    if "market" in to:
        if not _may_write_market(c, author):
            return _fail("only the Librarian and the top of the org post to Market")
        from types import SimpleNamespace
        from . import market_briefing as MB
        title, body = _briefing(text, args.get("title"))
        out["briefing"] = MB.post(c, SimpleNamespace(actor=author), MB.this_week(), title, body, None, source=source)["week"]
        text = _pointer(c, poster, title)
    if "updates" in to:
        # A warned step's text leads with its warn line (_warned_args): it goes on after the writing checks.
        from .org_rule_check import unwarned
        bare = unwarned(text)
        posted = updates.post(c, poster, bare, kind="daily", source=source,
                              warning=text[:len(text) - len(bare)].rstrip("\n"))
        out["update"] = (posted or {}).get("id")
    return out


def _pointer(c, poster, title):
    """The owner's daily update with one more line for the briefing (the same line is not added twice)."""
    from . import updates
    words = str(title).split()
    line = f"- Market briefing: {' '.join(words[:POINTER_WORDS])}{'…' if len(words) > POINTER_WORDS else ''} " \
           f"([read]({BRIEFING_LINK}))"
    row = c.execute("SELECT body FROM updates WHERE bot=? AND kind='daily' AND day=?",
                    (poster, updates.today())).fetchone()
    # A warned daily's warn line stays its own paragraph on top (unwarned), out of the bullets kept.
    from .org_rule_check import unwarned
    body = str(row["body"] if row else "")
    bare = unwarned(body)
    warned = body[:len(body) - len(bare)]
    kept = [x for x in bare.splitlines() if x.strip() and not x.startswith("- Market briefing:")]
    return warned + "\n".join(kept[-(updates.BULLETS_MAX - 1):] + [line])


def _webhook(c, auto, kind, hook, data):
    done = R._send_out(c, "call_webhook", _actor(c, auto), str(hook or ""), kind, auto["id"],
                       data if isinstance(data, dict) else {"data": data}, None)
    return {"ok": done["ok"], "detail": done["detail"]}


def _call_webhook(c, auto, run_id, args, values):
    return _webhook(c, auto, str(args.get("type") or "custom.automation"), args.get("webhook"),
                    args.get("data") if args.get("data") is not None else {"automation": auto["id"], "run": run_id})


ACTIONS = {"archive": _mail_or_slack("archive"), "leave_for_you": _mail_or_slack("leave_for_you"),
           "draft_reply": _mail_or_slack("draft_reply"), "send_reply": _mail_or_slack("send_reply"),
           "forward": _mail_or_slack("forward"), "create_task": _create_task, "label": _label, "wake_bot": _wake_bot,
           "note": _note, "post_update": _post_update, "call_webhook": _call_webhook, "assign": _on_task("assign")}


# ---------------------------------------------------------------------------------------------------- turns
def start_turn(c, auto, run_id, step_id, who, prompt, assistant_bot, key="0", label=None, run_model=None,
               occurrence=None):
    """A turn for this run alone: a new task for the named bot, filed by the automation's owner the way a routine
    files its run (backend/routines.py `open_task`), so it runs in that task's own room and never in a person's chat,
    an Assistant room or another task's session. `assistant:<person>` is the person's Assistant (its room is made on
    first use) and only the person's own automation names it (checked on save). The task's wake carries the run and
    the chain, so the turn's end resumes the run and its writes cannot start this automation again. The title is the
    automation's name, plus the find item's title (`label`, or "item N") for a turn an item started. `key` is the
    record the turn is written to: each item of a find and each repeat of a run starts its turn from the same step,
    so the wake names the run, the step and that record, and the duplicate check compares those (backend/hubdb.py
    `task_create`), not the title, which items may share."""
    kind, _, ident = who.partition(":")
    actor = _actor(c, auto)
    if kind == "assistant":
        from . import assistant
        if auto["flow"]["owner"] != "person:" + ident or not H.human(c, ident):
            raise ValueError(f"only {ident}'s own automation runs a turn on their Assistant")
        bot = assistant_bot
        assistant.ensure_room(c, "human:" + ident, assistant_bot)
    else:
        found = H.resolve_bot(c, ident) or {}
        if not found or found["state"] not in R.BOT_STATES_OK:
            raise ValueError(f"No bot {ident}" if not found else f"{ident} is {found['state']}")
        bot = found["id"]
    name = str(auto["flow"].get("name") or auto["id"])
    title = f"{name} · {label}"[:300] if label else name[:300]
    # A due-date run's turn is that occurrence's task, due and marked the same way as a create_task's (`_occurrence`).
    due, marker, occ_refs = _occurrence(c, auto, {}, {"due": occurrence} if occurrence else {})
    task = H.task_create(c, actor, title, prompt, H.bot_actor(bot), due=due, lint=False, deduplicate=False,
                         routine=True, trigger="other", trigger_source="automation:" + auto["id"], occurrence=marker,
                         occurrence_step=str(step_id) if marker else None, rule_due_id=occ_refs.get("rule_due_id"),
                         wake_refs={**occ_refs, "automation_run": run_id, "automation_step": step_id,
                                    "automation_key": str(key), **_chain_refs(auto)})
    if run_model:
        # The step's model is the task's own (checked when the automation was saved, so kept as it is here).
        task_run_models.set_choice(c, actor, task["id"], task_run_models.stamped(
            run_model["model"], run_model.get("effort"), run_model.get("harness")),
            note=f"Set by routine step {step_id}")
    # The wake names its task; the turn's end finds the run by it (backend/automations.py `turn_done`).
    return {"ref": task["id"], "turn": {"bot": bot, "task": task["id"], "session": task.get("conversation_id")}}


# ---------------------------------------------------------------------------------------------------- find
# Each source: its query ({after} is the cursor test, {mine} the mailboxes), the time and id columns the cursor reads,
# and how many LIKEs the query takes.
FIND = {
    "mail": ("SELECT mailbox, msg_id AS id, thread_id AS thread, from_addr AS 'from', subject, snippet, date, "
             "updated_at AS at FROM mail_messages WHERE deleted_at IS NULL AND {after} AND lower(mailbox) IN ({mine}) "
             "AND (subject LIKE ? OR from_addr LIKE ? OR snippet LIKE ?)", "updated_at", "msg_id", 3),
    "tasks": ("SELECT id, title, status, owner_id, requester_id, created_at AS at FROM tasks WHERE {after} "
              "AND coalesce(private,0)=0 AND title LIKE ?", "created_at", "id", 1),
    "prs": ("SELECT id, task_id AS task, url, title, state, repo, number, created_at AS at FROM task_links "
            "WHERE kind='pr' AND {after} AND (coalesce(title,'') LIKE ? OR url LIKE ?)", "created_at", "id", 2),
    "listening": ("SELECT i.id, i.destination, l.url, l.author, l.content, i.created_at AS at FROM intake_items i "
                  "JOIN listen_items l ON l.id=i.item_id WHERE {after} AND l.content LIKE ?", "i.created_at", "i.id", 1),
    "docs": ("SELECT id, path, title, updated_at AS at FROM docs WHERE archived=0 AND {after} "
             "AND (title LIKE ? OR path LIKE ?)", "updated_at", "id", 2),
}


# The stale-PR find: an open or draft PR whose last activity (else its creation) is at or before the idle line. The
# cursor reads that activity, so a PR is found again only once new activity makes it idle again.
PR_IDLE = ("SELECT id, task_id AS task, url, title, state, repo, number, coalesce(activity_at, created_at) AS at "
           "FROM task_links WHERE kind='pr' AND coalesce(state,'') IN ('open','draft') AND {after} "
           "AND coalesce(activity_at, created_at)<=? AND (coalesce(title,'') LIKE ? OR url LIKE ?)",
           "coalesce(activity_at, created_at)", "id", 2)


def find(c, auto, source, query, limit, cursor, *, mailboxes=(), skip=(), idle_hours=None):
    """Items from `source` after `cursor` ((time, id): items with the same time are told apart by id, so a page that
    ends among them never skips the rest) whose text has `query` (plain words), oldest first. Mail is read from
    `mailboxes` only, the owner's; `skip` holds ids another run of the automation is still working on."""
    sql, at, ident, likes = FIND[source]
    if source == "mail" and not mailboxes:
        return []
    idle = []
    if idle_hours and source == "prs":
        sql, at, ident, likes = PR_IDLE
        idle = [H.shift(H.now(), hours=-int(idle_hours))]
    like = "%" + str(query or "").strip().replace("%", "").replace("_", "") + "%"
    since, last = cursor
    mine = ",".join("?" * len(mailboxes))
    sql = sql.format(after=f"({at}>? OR ({at}=? AND {ident}>?))", mine=mine) + f" ORDER BY {at}, {ident} LIMIT ?"
    args = [since, since, last, *([m.lower() for m in mailboxes] if source == "mail" else []), *idle,
            *[like] * likes, int(limit) + len(skip)]
    rows = [dict(r) for r in c.execute(sql, args) if str(r["id"]) not in skip][:int(limit)]
    for row in rows:
        for key in ("content", "snippet"):
            if isinstance(row.get(key), str):
                row[key] = row[key][:MAX_ITEM]
    return rows
