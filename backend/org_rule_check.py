"""Org rule checks (docs/rules-plan.md §3): a bot's output is checked against the org rules that apply to it before it
is delivered (`questions/rule-check.json`, asked through the server's Decide engine, on Decide's one monthly budget).

Like ask ingest (backend/ask_ingest.py): one call per output, outside the write's transaction (`preflight`); plain
code then applies the verdict inside the write (`apply`). Nothing here calls an engine under `store.transaction()` or
`mutate`: `apply` only reads the verdict and writes rows.

- Whose writes: every person's and every bot's, however it was sent. `checked_as` gives the principal a write is held
  as (a person, or a bot writing alone), the sender (the person, or the bot acting for them: their Assistant or
  BotOps, with `via` saying which) and the route. A person's own writes, their Assistant's and BotOps's for them
  included, are checked as that person.
- Which rules: `org_rules.matching(c, author, recipients, place)`, the full audience of the write and its place
  (`place_for`: its conversation, kind, teams and outside members, for `where:`). No matching rule is no call, which is
  most outputs.
- What is judged: the whole output, short or long. An output whose state is over Decide's limit is held, not passed,
  when any rule it meets is Block or Escalate; when all are Warn it is delivered with a line saying it was too long to
  check, and a `rules.unchecked` event (reason `too_large`).
- If broken, per rule, strictest wins: Block holds it (a 200 `held` answer, never a refusal, so no quarantine) and
  the bot is told the rule and whom to appeal to; Escalate also files one review task for the reviewer; Warn
  delivers it with a warning line on the recipient's copy. The 3rd block of one rule by one bot in a UTC day
  escalates for the rest of that day, with one review task.
- Holds: the payload lives only in `org_rule_holds`. Its author reads it (a person by any token; a bot by its own),
  and its reviewer does. For a person's hold, the editors of the rules it broke and the Admins read it too
  (`_person_reviewer`). Send anyway replays the normal write once, as the principal (`replay_identity`: the person,
  rebuilt with their own permissions, or the bot), with a one-shot nonce and no second Decide call. A held write's
  author may withdraw it (Edit and resend: the new send is a new check). A bot's hold is also read by the person the
  bot reports up to (`represented`), who may withdraw it (keep it blocked) but never release it.
- The API, stable for core's `threads.post` (backend/threads.py) and its outside callers:
  `preflight(store, primary, who, operation, output, recipients=(), context=None, obj="", draft=False, skip=(),
  place=None)` checks one write one identity makes; it reads the principal, the authenticated actor and the route from
  `who` (`checked_as`: a person's Assistant or BotOps writes are checked as the person, with `via` set).
  `check_as(store, primary, principal, sender, via, operation, output, recipients=(), *, context=None, obj="",
  draft=False, skip=(), place=None, attempt_id="", origin=None)` checks one output held as `principal`, sent by
  `sender` by route `via`. `inbound(store, primary, principal, operation, output, recipients=(), *, source,
  authenticated="", origin="", place=None, context=None, obj="")` checks a message from outside this server (a Slack
  message): `principal` is the teammate it is held for (or None, and then it is held as
  `source:origin`), `source` names the route (`slack`), `authenticated` is the actor the caller proved,
  and `origin` the message's id on its side. `apply(c, verdict, payload=None, replay=None, tell=False, obj_now=None)`
  writes the verdict inside the write and returns None (deliver), `{"warn": line}` (deliver with the line), or
  `{"held": view}` (write nothing); `answer(gate)` is what the write answers with. `changed(...)` answers a write whose
  destination moved. A thread message is checked as operation `threads` (not `message`, the chat's replayer), with
  `payload` the body text (the reviewer reads it) and `replay={"thread_id", "body", "kind", "refs", "parent_message_id",
  "recipient_id", "via", "origin", "attachments"}`: the `threads` replayer posts it again as the person it was held for.
  `place` is a dict, or a function of (c, principal, recipients) that reads it inside the check
  (`place_for`: the thread, its root and audience, kind, teams and outside members).
- Where: a rule's `where:` (`thread:<id>`, `team:<group>`, `kind:dm`, `has_outside_members`) is matched on the place.
  Messages and task comments have a place through their thread (a task comment's is its task's conversation). Mail and
  drafts have none, so a `where` rule never meets them.
- Fails open: checks Off, no engine, Decide's monthly cap, the day's call cap, the deadline, an error or a malformed
  answer let the output through with a `rules.unchecked` event. Events never carry the text. Any other failure of the
  check (a fault here, not the judge) is a 503 and the write is not made.
- Bounded: at most `rule_check_concurrency` calls run at once in this process (`call_slots.Calls`); with every slot in
  use the output goes through unchecked at once (`busy`), never waiting for one. After BREAKER_FAILURES timeouts or
  errors in a row no call is made for BREAKER_SECONDS (`breaker`), then one call probes. Each level has its own
  deadline (DEADLINES), and the call itself is cut off then (`J.within`), so an abandoned call never runs on to the
  Gateway's own timeout holding its slot.
- Cache: verdicts are kept by a digest of the check input, the matched rules and versions, the set and the level, so
  `tico rules check` on a draft and then sending it costs one call.
"""

import contextvars
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
import weakref
from types import SimpleNamespace

from clients import judge as J
from . import bot_settings as BS
from . import judge as B
from . import org_rules as R
from . import groups as G
from . import team_rules
from . import threads as T
from .batch_work import isolated
from .call_slots import NEUTRAL, Calls
from .store import H, Problem
from . import sources as SRC
from .entities import rules as DR

LOG = logging.getLogger("tico.org_rule_check")
QUESTION_SET = "rule-check"
RULES_TARGET = B.RULES_TARGET  # the judge.call rows a rule check writes (`rule-check@<version>`)
# The most one check waits for its answer, by Decide level (Low is Jev; Medium and High are language models).
DEADLINES = {"low": 5, "medium": 10, "high": 15}
CONCURRENCY = 8  # calls at once, per process, when the setting names none
# Rule checks ask Jev only, as every decision does (backend/judge.py `server_engine`): the Gateway at Low
# (`tico-decide-low`), or a customer's own TypeSafe key, so a check is always a Jev call or unchecked (`RULE_LEVEL`).
RULE_LEVEL = "low"
STRICT = {"warn": 1, "block": 2, "escalate": 3}
REPEAT_BLOCKS = 3  # the 3rd block of one rule by one bot in a UTC day escalates for the rest of that day
REVIEW_LABEL = "rule-review"
REVIEW_TITLE = "Rule review"  # a review task's title, and its thread's row title
CACHE_DAYS = 7
HOLD_LIST_MAX = 50
# One hold's text as its readers get it: past this many characters it is cut (`text_cut`); Send anyway sends it whole.
HOLD_TEXT_CHARS = 20_000
HOLD_LIST_BYTES = 100_000  # one page of hold summaries stops here too (a page always holds at least one)
BOTS_BELOW = 500  # bots read as the authors whose holds one person may be shown (`held_for`)
TEAM_RULE = "org_rule_checks"
UNCHECKED_SHARE, UNCHECKED_MIN = 0.2, 10  # a day's unchecked share at or over this, of at least this many checks
ACTIONS = ("rules.blocked", "rules.escalated", "rules.warned", "rules.unchecked")
# What each operation is, in the words the model reads and the hold shows.
OPERATIONS = {
    "message": "a chat message",
    "comment": "a task comment",
    "comment_edit": "an edit to a task comment",
    "task_create": "a new task",
    "task_update": "a change to a task",
    "task_owner": "a task assignment",
    "task_ask": "a question on a task",
    "bulk": "a change to several tasks",
    "note": "a note to a bot",
    "reply": "a reply in a conversation",
    "automation": "an automation's write",
    "slack": "a Slack reply",
    "mail": "an email",
    "letter": "a letter",
    "update": "an update",
    "task_question": "a question put to a person",
    "slack_message": "a Slack message",
    "slack_dm": "a Slack message to a bot",
    "threads": "a thread message",
    "threads_edit": "an edit to a thread message",
    "chat_upload": "a chat message with files",
    "assistant_message": "a message to the Assistant",
}
DRAFT_AS = {"message": "message", "comment": "comment", "task": "task_create", "assign": "task_owner"}

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS org_rule_verdicts(digest TEXT PRIMARY KEY, broken TEXT NOT NULL, "
    "call_id TEXT, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS org_rule_verdicts_created ON org_rule_verdicts(created_at)",
    "CREATE INDEX IF NOT EXISTS org_rule_holds_task ON org_rule_holds(task_id) WHERE task_id IS NOT NULL",
)

# Send anyway's writes, by operation: fn(c, bot identity, hold, replay data, ingest) -> the write's answer. Registered
# by the module that owns the write (backend/app.py, execution, automations), so the replay is the normal write.
REPLAY = {}
# An optional step outside the transaction before a replay (ask ingest's own call): fn(store, bot identity, replay).
PREPARE = {}
# Who a held write would reach now, from its replay data: fn(c, bot identity, replay) -> actors. Send anyway refuses
# when that is not the audience the hold was checked for (`_same_audience`).
AUDIENCE = {}
# The request path of the write being checked (app.mutate sets it): kept on a hold, so Send anyway runs that path's
# privacy guard again as the bot (task_privacy.guard_write).
PATH = contextvars.ContextVar("org_rule_path", default="")
CHECK_FAILED = "The rule check could not run, so nothing was written. Try again."
SUMMARY_RULES, SUMMARY_CHARS, SUMMARY_NAMES = 3, 200, 10  # a hold summary's rule previews, their length, its names


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def replayer(operation, prepare=None, audience=None):
    def register(fn):
        REPLAY[operation] = fn
        if prepare:
            PREPARE[operation] = prepare
        if audience:
            AUDIENCE[operation] = audience
        return fn

    return register


# ----------------------------------------------------------------------------- whose writes
def checked_as(who, assistant_bot=""):
    """(principal, sender, via) a write is checked as, or None when nothing is. The principal is the teammate the write
    is held as: a person, or a bot that writes alone. The sender is who made the write: the person themselves, their
    Assistant or BotOps acting for them (`via` says which), or the bot that sent it. A person's own write is checked
    as that person, whatever token sent it."""
    if who is None:
        return None
    actor, task = str(who.actor or ""), str(who.task_actor or "")
    if H.is_human(actor):
        if who.via == "assistant":
            sender = task if task.startswith("bot:") else "bot:" + str(assistant_bot or "")
        elif who.via == "botops":
            sender = task if task.startswith("bot:") else "bot:botops"
        else:
            sender = task if task.startswith("bot:") else actor
        return actor, sender, who.via or ""
    if actor.startswith("bot:"):
        return actor, actor, who.via or ""
    if task.startswith("bot:"):
        return task, task, who.via or ""
    return None


def author_of(who, assistant_bot=""):
    """The teammate a write is checked as (`checked_as`), or None when nothing is checked."""
    found = checked_as(who, assistant_bot)
    return found[0] if found else None


def on(c):
    """Whether the team checks outputs at all (Settings: the `org_rule_checks` team rule, on by default)."""
    return bool(team_rules.load(c).get(TEAM_RULE, True))


def in_force(c, ask=False):
    """Whether any output can be checked now: an active rule exists and checks are on. One `SELECT ... LIMIT 1` when
    none is: a caller asks this before it resolves a write's audience (a roster read), so an install with no rules
    reads nothing more. `ask`: the write is a bot's question to a person, which a `kind:ask` rule meets too."""
    return bool(R.any_active(c, ask)) and on(c)


def asking(place):
    """Whether a write's place (a dict, or the function that reads it) is a bot's question to a person: marked by
    task_rule_gate.TaskRules.place, read before anything else so no other write pays for a `kind:ask` rule."""
    if isinstance(place, dict):
        return bool(place.get("ask"))
    return bool(getattr(place, "asks_person", False))


def watched(c, bot):
    """Whether any active rule's Who names this bot: its live reply text stays hidden until delivered."""
    slug = H.actor_id(bot)
    try:
        if not R.any_active(c) or not on(c):
            return False
        key = R.who_key(c, "bot:" + slug)
        # A rule met only by a bot's question to a person (`kind:ask`) never reads a live reply.
        return any(R._hits(row["who"], key) for row in R._active(c) if not R.asks_only(row["where"]))
    except Exception:  # noqa: BLE001 - no tables yet (an old copy): nothing to hide
        return False


def watched_bots(c):
    """The ids of the bots `watched` is true for, read once: for a reader that filters many runs together (SQL)."""
    try:
        if not R.any_active(c) or not on(c):
            return []
        rules, ctx = [rule for rule in R._active(c) if not R.asks_only(rule["where"])], R._ctx(c)
        return [
            row["id"]
            for row in H.bots(c)
            if any(R._hits(rule["who"], R.who_key(c, "bot:" + row["id"], ctx)) for rule in rules)
        ]
    except sqlite3.OperationalError:  # no tables yet (an old copy): nothing to hide
        return []


# A run in these states has not handed in its reply yet: nothing it said is checked.
UNSETTLED = ("leased", "running", "expired")


def hides_run(c, attempt):
    """Whether a run's said text and raw stream stay hidden: its bot is watched and its reply is not checked yet, or
    its reply was held (`held_reply` on its result). `attempt` is its attempts row."""
    if not attempt:
        return False
    try:
        if json.loads(attempt["result"] or "{}").get("held_reply"):
            return True
    except (TypeError, ValueError):
        pass
    return attempt["state"] in UNSETTLED and watched(c, attempt["bot_id"])


# ----------------------------------------------------------------------------- the check
def _names(c, actors):
    out = []
    for actor in actors:
        if H.is_bot(actor):
            out.append((H.bot(c, H.actor_id(actor)) or {}).get("display_name") or H.actor_id(actor))
        elif H.is_human(actor):
            out.append(H.person_name(c, H.actor_id(actor)) or H.actor_id(actor))
        elif actor:
            out.append(str(actor))
    return out


def _rule_rows(rules):
    return [
        {
            "id": r["id"],
            "version": r["version"],
            "if_broken": r["if_broken"],
            "reviewer": r.get("reviewer"),
            "suggested_by": r.get("suggested_by") or "",
            "text": r["text"],
        }
        for r in rules
    ]


def digest(operation, author, recipients, output, obj, rules, label, level, context=None):
    """The cache key: everything the model is shown (the context too: a mail's outside addresses, an automation's
    step) and everything that decides the verdict (the rules and versions, the set, the level)."""
    raw = json.dumps(
        [
            operation,
            author,
            sorted(set(recipients)),
            output,
            obj or "",
            context or {},
            sorted((r["id"], r["version"]) for r in rules),
            label,
            level,
        ],
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _cached(raw):
    """A cached verdict: (broken ids, ids of the rules it did not check). A complete one is stored as a plain list."""
    data = json.loads(raw or "[]")
    if isinstance(data, dict):
        return list(data.get("broken") or []), list(data.get("unchecked") or [])
    return list(data), []


def calls_today(c, people=False):
    """Today's rule-check calls: the bots' (`people` False) or the people's, each under its own daily cap (`daily_cap`).
    A people's call's `judge.call` row says so, with `people`."""
    day = H.now()[:10] + "T00:00:00"
    flag = "IS 1" if people else "IS NOT 1"
    return c.execute(
        "SELECT count(*) FROM events WHERE actor=? AND action='judge.call' AND target LIKE ? AND created_at>=? "
        f"AND json_extract(detail,'$.people') {flag}",
        (H.KEEPER, RULES_TARGET, day),
    ).fetchone()[0]


def _questions(qset, rules):
    template = qset["questions"]["rule"]
    out = {}
    for n, rule in enumerate(rules, 1):
        instructions = dict(template["instructions"])
        instructions["question"] = instructions["question"].replace("{rule}", json.dumps(rule["text"]))
        out[f"r{n}"] = {**template, "instructions": instructions}
    return out


def deadline(level):
    """How long a check at `level` waits for its answer, in seconds."""
    return DEADLINES.get(str(level or "").lower(), DEADLINES["medium"])


_CALLS, _CALLS_LOCK = weakref.WeakKeyDictionary(), threading.Lock()


def calls_for(store):
    """The store's `Calls`, sized by its `rule_check_concurrency` setting (a new size starts a new set of slots)."""
    size = max(1, int(getattr(store.settings, "rule_check_concurrency", 0) or CONCURRENCY))
    with _CALLS_LOCK:
        calls = _CALLS.get(store)
        if calls is None or calls.size != size:
            calls = _CALLS[store] = Calls(size, "Rule checks")
        return calls


def _ask(calls, engine, state, questions, label, level):
    """The engine's answer within the level's deadline: (result, None) or (None, error code): `timeout`, or `busy` and
    `breaker` at once, asking nothing. The deadline is the call's own too (`J.within`): the Gateway and TypeSafe each
    stop at it, so the thread and its slot end with it."""
    refused, probe = calls.admit()
    if refused:
        return None, refused
    seconds = deadline(level)
    box = {}

    def work():
        try:
            with J.within(seconds):
                box["result"] = engine(state, questions, label)
        except J.JudgeError as exc:
            box["error"] = exc.code or "error"
        except Exception as exc:  # noqa: BLE001 - a fake, a provider bug: the write never breaks on it
            LOG.warning("Rule check call failed: %s", type(exc).__name__)
            box["error"] = "error"
        finally:
            calls.slots.release()

    worker = threading.Thread(target=work, name="org-rule-check", daemon=True)
    try:
        worker.start()
    except RuntimeError:  # no thread to be had: the slot goes back and the output goes unchecked
        calls.slots.release()
        calls.done(None, probe)
        return None, "busy"
    worker.join(seconds)
    if worker.is_alive():
        calls.done(False, probe)
        return None, "timeout"
    error = box.get("error")
    calls.done(None if error in NEUTRAL else not error, probe)
    # The meter refused the call before asking: the decisions cap had no room for it (other calls spent or hold it).
    return box.get("result"), "budget" if error == "spend_cap" else error


def place_for(c, author, recipients=(), conversation_id=""):
    """Where a write happens, as `where:` reads it. `thread`: the conversation's id, which is the thread's id (core's
    threads are rows of `conversations`). `root` and `audience`: the thread's root and the audience its root names
    (backend/threads.py), so a sub-thread has its root's. `kind`: `dm` for a direct chat, a personal thread (a DM under
    core's audiences) or a write with no conversation, else `thread`. `teams`: the groups the sender and the thread's
    owner are in, and the team an audience names. `outside`: someone in it is outside the roster, or a Slack actor. A write with no conversation (a direct message) has an empty `thread` and `root`."""
    conv = H.conversation(c, conversation_id) if conversation_id else None
    row = T.thread_row(c, conversation_id) if conv is not None else None
    root = T.root_of(c, row) if row is not None else None
    root_id = (root or {}).get("id") or str(conversation_id or "")
    audience = T.audience_of(root) if root is not None else ""
    members = list(recipients or ())
    if conv is not None:
        members += list((conv or {}).get("participants") or [])
        members += [r["actor"] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?", (root_id,))]
    teams = set()
    for actor in (author, (conv or {}).get("owner_id")):
        if actor:
            teams |= set(R.who_key(c, actor)["chain"])
    if root is not None and root.get("audience_group_id"):
        teams.add(root["audience_group_id"])
    # The native audience decides whether it is a DM; the legacy scope only where no audience is stored.
    dm = conv is None or (audience == "personal" if audience else conv.get("scope") == "direct")
    return {
        "thread": str(conversation_id or ""),
        "root": root_id if conv is not None else "",
        "audience": audience,
        "kind": "dm" if dm else "thread",
        "teams": sorted(teams),
        "outside": _outside(c, members),
    }


def _outside(c, people):
    """Whether someone in `people` is outside the roster, or a Slack actor."""
    roster = {p.get("id") for p in (G._roster(c).get("people") or []) if p.get("id")}
    return any(
        (H.is_human(a) and H.actor_id(a) not in roster) or (a and not H.is_human(a) and not H.is_bot(a)) for a in people
    )


def place_for_start(c, author, recipients=(), *, audience="members", audience_group_id=None, members=()):
    """The place of a write that makes its thread (core's `post` with `start`). The thread is not there yet, so `thread`
    and `root` are empty and no `thread:` rule can name it; its audience, teams and outside members are the ones it
    will have. `members` are the names the caller gave."""
    named = [H.resolve_actor(c, m) or str(m) for m in members or () if m]
    teams = set(R.who_key(c, author)["chain"]) if author else set()
    if audience_group_id:
        teams.add(audience_group_id)
    return {
        "thread": "",
        "root": "",
        "audience": audience,
        "kind": "dm" if audience == "personal" else "thread",
        "teams": sorted(teams),
        "outside": _outside(c, list(recipients or ()) + named),
    }


def _place_now(c, place, author, recipients):
    """A write's place as the check reads it: a dict as it is, or the answer of a function, which is read only when a
    rule names a place."""
    if callable(place):
        return place(c, author, recipients) if R.any_where(c) else None
    return place or None


def _judged(store, engine, qset, level, operation, batch, questions, state, draft, is_person):
    """One call of a check, on one batch of its rules: (the ids of the rules it found broken, or None; the error code;
    the `judge.call` event's id, or None when nothing was asked)."""
    result, error = _ask(calls_for(store), engine, state, questions, qset["label"], level)
    if error in ("busy", "breaker", "budget"):
        return None, error, None  # nothing was asked: no judge.call row
    if result is not None and not isinstance(result, dict):
        result, error = None, "shape"
    broken = None
    if result is not None and not error:
        try:
            answers = J.check_answers(result.get("answers"), questions)
            cut = float(qset["thresholds"]["broken"])
            broken = [batch[int(qid[1:]) - 1]["id"] for qid in questions if J.noul(answers, qid) >= cut]
        except (J.JudgeError, AttributeError, KeyError, TypeError, ValueError, IndexError):
            error = "shape"
    detail = {
        "label": qset["label"],
        "questions": len(questions),
        "operation": operation,
        "level": level,
        "rules": [r["id"] for r in batch],
        **({"draft": True} if draft else {}),
        **({"people": True} if is_person else {}),
    }
    if result is not None:
        detail.update(B.audit_detail(result))
    if error or broken is None:
        detail["error"] = error or "shape"
    else:
        detail["broken"] = len(broken)
    with store.transaction() as c:
        call = H.event(c, H.KEEPER, "judge.call", qset["label"], detail)
    return broken, (None if broken is not None else error or "shape"), call["id"]


@SRC.scoped("rule-check")
def check_as(
    store,
    primary,
    principal,
    sender,
    via,
    operation,
    output,
    recipients=(),
    *,
    context=None,
    obj="",
    draft=False,
    skip=(),
    place=None,
    attempt_id="",
    origin=None,
):
    """Check one output held as `principal` (a person or a bot). `sender` is who made it and `via` the route
    (`checked_as` reads both from an identity). `place`: the write's place, or a function of (c, principal,
    recipients) that reads it inside the check's read (`place_for`). Returns the verdict `apply` reads, or None when
    nothing is checked (no rule matches, checks are off, no principal). Only the judge being out (a timeout, an error
    or a malformed answer from it, the breaker, no engine, the budget) is a verdict marked unchecked; any other
    failure raises a 503 `Problem` (`CHECK_FAILED`), so the write fails. `skip`: (rule id, version) pairs already
    decided (Send anyway asks only the rules changed or added since its hold); the verdict's `matched` is every pair that matched, to check again inside the write. `origin`:
    where an outside message came from (`inbound`), kept on the verdict and its hold."""
    author = principal
    if not author:
        return None
    verdict = None
    try:
        recipients = sorted({r for r in recipients or () if r and r != author})
        is_person = H.is_human(author)
        with store.read() as c:
            if not in_force(c, asking(place)):
                return None  # no rule in force: nothing more is read, and the output is never serialized
            where = _place_now(c, place, author, recipients)
            rules = _rule_rows(R.matching(c, author, recipients, where))
            matched = sorted((r["id"], r["version"]) for r in rules)
            skip = {tuple(x) for x in skip or ()}
            rules = [r for r in rules if (r["id"], r["version"]) not in skip]
            if not rules:
                return None
            if where is None and callable(place):
                where = place(c, author, recipients)  # a held write keeps its place, whether or not a rule reads it
            output = output if isinstance(output, str) else json.dumps(output, sort_keys=True, default=str)
            level = RULE_LEVEL
            qset = J.load_set(QUESTION_SET)
            key = digest(operation, author, recipients, output, obj, rules, qset["label"], level, context)
            verdict = {
                "author": author,
                "sender": sender or author,
                "via": via or "",
                "operation": operation,
                "recipients": recipients,
                "rules": rules,
                "digest": key,
                "broken": [],
                "outcome": "pass",
                "error": None,
                "call": None,
                "cached": False,
                "draft": draft,
                "matched": matched,
                "place": where,
                "origin": origin,
                "attempt_id": attempt_id or "",
                "context": context or {},
                "obj": obj or "",
            }
            hit = c.execute("SELECT broken, call_id FROM org_rule_verdicts WHERE digest=?", (key,)).fetchone()
            if hit:
                broken, unchecked = _cached(hit[0])
                return {
                    **verdict,
                    "broken": broken,
                    "outcome": "broken" if broken else "pass",
                    "cached": True,
                    "call": hit[1],
                    **({"unchecked_rules": unchecked} if unchecked else {}),
                }
            # Every matching rule is judged, all in one call: more only when the rules do not fit beside the output in
            # one (`J.pack`), never silently the first ones only.
            sender_name, recipient_names = _names(c, [author])[0], _names(c, recipients)
            batches = J.pack(
                rules,
                lambda batch: (
                    _questions(qset, batch),
                    {
                        "rules": [{"id": f"r{n}", "text": r["text"]} for n, r in enumerate(batch, 1)],
                        "operation": OPERATIONS.get(operation, operation),
                        "output": output,
                        "from": sender_name,
                        "to": recipient_names,
                        "context": context or {},
                    },
                ),
            )
            if batches is None:
                return {**verdict, "outcome": "too_large", "broken": [r["id"] for r in rules]}
            engine = B.server_engine(store, c, primary)
            if engine is None:
                capped = B.over_spend_cap(c, store.settings)
                return {**verdict, "outcome": "unchecked", "error": "budget" if capped else "no_engine"}
            cap = B.daily_cap(store.settings, "human" if is_person else "bot")
            if calls_today(c, people=is_person) + len(batches) > cap:
                return {**verdict, "outcome": "unchecked", "error": "day_cap"}
        broken, failed, errors, calls = [], [], [], []
        for batch, questions, state in batches:
            found, error, call = _judged(
                store, engine, qset, level, operation, batch, questions, state, draft, is_person
            )
            calls += [call] if call else []
            if found is None:
                failed += [r["id"] for r in batch]
                errors.append(error)
            else:
                broken += found
        call = calls[0] if calls else None
        if failed and len(failed) == len(rules):
            return {**verdict, "outcome": "unchecked", "error": errors[0], "call": call}
        if failed:
            # Some calls answered and some did not (the judge out part-way): what was found holds or warns, and the
            # rules not judged are recorded unchecked. Not cached: the next try asks again.
            return {
                **verdict,
                "broken": broken,
                "outcome": "broken" if broken else "pass",
                "call": call,
                "error": errors[0],
                "unchecked_rules": failed,
            }
        with store.transaction() as c:
            c.execute(
                "INSERT OR REPLACE INTO org_rule_verdicts(digest, broken, call_id, created_at) VALUES(?,?,?,?)",
                (key, json.dumps(broken), call, H.now()),
            )
            c.execute("DELETE FROM org_rule_verdicts WHERE created_at<?", (H.shift(H.now(), days=-CACHE_DAYS),))
        return {**verdict, "broken": broken, "outcome": "broken" if broken else "pass", "call": call}
    except Exception as exc:
        # Only the judge being out fails open (`_ask` turns its failures into error codes: a timeout, an error, a
        # malformed answer, the breaker). Anything else is a fault here: the write fails, never goes unchecked.
        LOG.exception("Rule check failed")
        raise Problem("rule_check", CHECK_FAILED, 503, retryable=True) from exc


def structured_check(c, author, operation, *, targets=(), contacts=()):
    """The Directory's structured check (backend/entities/rules.py) of one write, read in the caller's transaction: None
    when no structured rule is in force."""
    if not DR.in_force(c):
        return None
    return DR.check(c, author, list(targets), operation, contacts=list(contacts))


def _emails(c, actors):
    """The addresses of the people among `actors`. A structured rule reads a person's address as its entity (the email
    identifier) and as the organization whose domain it is."""
    out = []
    for actor in actors:
        if H.is_human(actor):
            row = c.execute("SELECT email FROM humans WHERE id=?", (H.actor_id(actor),)).fetchone()
            if row and row[0]:
                out.append(str(row[0]).strip().lower())
    return out


def structured_verdict(
    c,
    author,
    sender,
    via,
    operation,
    recipients,
    *,
    targets=(),
    contacts=(),
    context=None,
    obj="",
    place=None,
    attempt_id="",
    origin=None,
    draft=False,
):
    """One write's structured verdict, shaped as `apply` reads a verdict: check "structured", outcome "held" (a block, an
    escalation, or a check that could not complete, with `error` its class) or "warn". None when no structured rule is in
    force or none applies. `targets` and `contacts` are kept on the verdict and its hold, so a later check reads the same
    input."""
    result = structured_check(c, author, operation, targets=targets, contacts=contacts)
    if result is None or result["outcome"] == "clear":
        return None
    rules = [*result["held"], *result["warned"]]
    return {
        "check": "structured",
        "author": author,
        "sender": sender or author,
        "via": via or "",
        "operation": operation,
        "recipients": list(recipients),
        "targets": sorted(set(targets)),
        "contacts": sorted(set(contacts)),
        "rules": rules,
        "digest": "",
        "broken": [r["id"] for r in result["held"] if r["id"]],
        "outcome": result["outcome"],
        "error": result["error"],
        "held": result["held"],
        "warned": result["warned"],
        "call": None,
        "cached": False,
        "draft": draft,
        "matched": sorted((r["id"], r["version"]) for r in rules if r["id"]),
        "place": place,
        "origin": origin,
        "attempt_id": attempt_id or "",
        "context": context or {},
        "obj": obj or "",
    }


def _structured_hold_verdict(c, hold, people, place):
    """A structured hold's input checked again now, as the hold was checked (its author, input, targets and contacts)."""
    shown, prov = hold_input(hold), hold_provenance(hold)
    return structured_verdict(
        c,
        hold["author"],
        prov.get("authenticated") or hold["author"],
        prov.get("via") or "",
        hold["operation"],
        people,
        targets=shown.get("targets") or [],
        contacts=shown.get("contacts") or [],
        context=shown.get("context") or None,
        obj=shown.get("obj") or "",
        place=place,
        attempt_id=prov.get("attempt_id") or "",
        origin=prov.get("origin"),
    )


def _warn_structured(c, verdict, warned, tell):
    """A structured Warn rule's line on the recipient's copy, written and told as a judged warn is."""
    author, operation = verdict["author"], verdict["operation"]
    strictest = max(warned, key=lambda r: STRICT[r["if_broken"]])
    H.event(
        c,
        author,
        "rules.warned",
        "org_rule:" + strictest["id"],
        {"operation": operation, "rules": [r["id"] for r in warned], "cached": False},
    )
    line = f"Warning: {_author_name(c, author)}'s message may break the rule '{strictest['text']}'."
    told = (
        f"Your {OPERATIONS.get(operation, operation)} was delivered with a warning: it may break the rule "
        f"'{strictest['text']}'."
    )
    if tell:
        _tell_bot(c, author, told)
    return {"warn": line, "warned": {"rules": [{"id": r["id"], "text": r["text"]} for r in warned], "text": told}}


def _structured_apply(c, verdict, payload, replay, tell, judged_text=None, private_to=None):
    """A structured verdict inside the write. The Directory's check runs again here on the same input (it calls no engine,
    so it runs under the write's lock), and what it finds now holds the write. A check that cannot complete holds it too
    (fail closed; its class is the reason). A judged block the verdict was checked with (`judged`) holds it with the
    structured rules, so one waiver covers the whole hold. Nothing that holds is ever let through as unchecked."""
    now = DR.check(
        c, verdict["author"], verdict.get("targets") or [], verdict["operation"], contacts=verdict.get("contacts") or []
    )
    judged = verdict.get("judged")
    judged_held = (
        [r for r in judged["rules"] if r["id"] in judged["broken"] and r["if_broken"] != "warn"]
        if holds(judged)
        else []
    )
    if now["outcome"] != "held" and not judged_held:
        return _warn_structured(c, verdict, now["warned"], tell) if now["outcome"] == "warn" else None
    found: dict[str, dict] = {}
    for r in [*now["held"], *judged_held]:
        found.setdefault(
            r["id"],
            {
                "id": r["id"],
                "text": r["text"],
                "if_broken": r.get("if_broken") or "block",
                "reviewer": r.get("reviewer"),
                "suggested_by": r.get("suggested_by") or "",
                "version": r.get("version") or 0,
            },
        )
    broken = list(found.values())
    strictest = max(broken, key=lambda r: STRICT[r["if_broken"]])
    matched = {(r["id"], r["version"]) for r in [*now["held"], *now["warned"]] if r["id"]}
    if judged:
        matched |= {tuple(x) for x in judged.get("matched") or []}
    hold_verdict = {
        **verdict,
        "rules": broken,
        "broken": [r["id"] for r in broken if r["id"]],
        "matched": sorted(matched),
        "digest": judged["digest"] if judged else "",
        "cached": bool(judged and judged["cached"]),
        "call": judged.get("call") if judged else None,
    }
    # The same repeat rule as a judged block (`apply`): the 3rd block of this rule by this author in a day escalates.
    mode, repeat = strictest["if_broken"], False
    if mode == "block" and strictest["id"] and _repeats(c, verdict["author"], strictest["id"]) >= REPEAT_BLOCKS - 1:
        mode, repeat = "escalate", True
    return _write_hold(
        c,
        hold_verdict,
        broken,
        strictest,
        mode,
        repeat,
        False,
        payload,
        replay,
        tell,
        check="structured",
        error=now["error"],
        judged=judged_text,
        private_to=private_to,
    )


def _rule_editor(c, who, hold, auth):
    """Whether `who` may change a rule the hold broke, so may waive a structured hold once: its editor (suggested_by), or
    whoever may manage the rule (`R.may_manage`: a lead of a group in its Who, a bot's manager, the owner and the Admins).
    A hold no rule names (a check that could not complete) is the owner's and the Admins' to waive."""
    rule_ids = json.loads(hold["rule_ids"] or "[]")
    ctx = R._ctx(c)
    if not rule_ids:
        return R.may_manage(c, auth, who, {"everyone": True}, ctx)
    for rid in rule_ids:
        row = c.execute("SELECT * FROM org_rules WHERE id=?", (rid,)).fetchone()
        if not row:
            continue
        if row["suggested_by"] == who.actor or R.may_manage(c, auth, who, R._row(row)["who"], ctx):
            return True
    return False


def mail_unprepared(c, found, to, cc, subject, body, contacts=()):
    """A mail whose judged check could not be prepared (rule_gate's fallback): the Directory's structured rules still decide
    it inside the write, and fail closed. No engine is asked, so there is no judged verdict to apply."""
    author, sender, via = found
    key = mail_digest(to, cc, subject, body)
    output = (f"Subject: {subject}\n\n" if subject else "") + body
    recipients = mail_recipients(c, [*to, *cc])
    verdict = structured_verdict(
        c,
        author,
        sender,
        via,
        "mail",
        _people(recipients, author),
        contacts=sorted(set(contacts)),
        context={"to": [a.lower() for a in [*to, *cc]]},
    )
    return mail_decide(
        c, {"author": author, "key": key, "output": output, "approved": None, "check": None, "verdict": verdict}
    )


def _letter_input(letter):
    """A letter's check input: its text for the reviewer (the addressee and address; the proof is the letter itself),
    the contact its addressee resolves by (`name:`, as backend/entities/store.py reads it) and its context."""
    to = json.loads(letter["recipient"] or "{}")
    lines = [to.get("name") or "", to.get("line1") or "", to.get("line2") or ""]
    lines.append(" ".join(x for x in (to.get("city"), to.get("state"), to.get("postal_code")) if x))
    address = ", ".join(x for x in lines if x)
    proof = f"\nProof: /api/v2/files/{letter['proof_blob_id']}" if letter.get("proof_blob_id") else ""
    output = f"Letter {letter['id']} to {address}{proof}"
    return output, ["name:" + str(to.get("name") or "")], {"to": [address]}


def letter_prepare(store, primary, found, letter):
    """The org rule check of one letter a bot sends, before its write: what `mail_decide` needs. Letters meet the
    Directory's structured rules (channel `letter`), which fail closed; its addressee resolves by name. A letter the
    reviewer chose to send anyway (Send anyway on its hold) passes once, as an approved mail does."""
    author, sender, via = found
    output, contacts, context = _letter_input(letter)
    with store.read() as c:
        approved = mail_approved(c, author, letter["id"], "letter")
        verdict = structured_verdict(c, author, sender, via, "letter", [], contacts=contacts, context=context)
    check = recheck(store, primary, approved) if approved else None
    return {
        "operation": "letter",
        "author": author,
        "key": letter["id"],
        "output": output,
        "approved": approved,
        "check": check,
        "verdict": check[1] if check else verdict,
    }


def letter_unprepared(c, found, letter):
    """A letter whose check could not be prepared: the structured rules still decide it inside the write (fail closed),
    as `mail_unprepared` does for a mail."""
    author, sender, via = found
    output, contacts, context = _letter_input(letter)
    verdict = structured_verdict(c, author, sender, via, "letter", [], contacts=contacts, context=context)
    return mail_decide(
        c,
        {
            "operation": "letter",
            "author": author,
            "key": letter["id"],
            "output": output,
            "approved": None,
            "check": None,
            "verdict": verdict,
        },
    )


def preflight(
    store,
    primary,
    who,
    operation,
    output,
    recipients=(),
    context=None,
    obj="",
    draft=False,
    skip=(),
    place=None,
    *,
    contacts=(),
    targets=(),
):
    """The stable entry for a write one identity makes (core's `threads.post` calls it): `check_as` for the principal,
    sender and route `checked_as` reads from `who`. `place`: the write's place, or a function of (c, principal,
    recipients) (`place_for`). Returns the verdict `apply` reads, or None when nothing is checked. The Directory's
    structured rules run on the same write: a structured hold carries the judged verdict it was checked with (`judged`),
    so a block of either holds it; a structured Warn rides on a judged verdict (`structured_warned`). `contacts` are the
    addresses the write goes to (a mail's), `targets` the entities it is about; a person recipient's address is one too."""
    found = checked_as(who, store.settings.assistant_bot)
    if not found:
        return None
    attempt_id = getattr(who, "attempt_id", "") or ""
    people = _people(recipients, found[0])
    structured = None
    with store.read() as c:
        if DR.in_force(c):
            where = _place_now(c, place, found[0], people)
            if where is None and callable(place):
                where = place(c, found[0], people)
            structured = structured_verdict(
                c,
                found[0],
                found[1],
                found[2],
                operation,
                people,
                targets=targets,
                contacts=sorted({*contacts, *_emails(c, people)}),
                context=context,
                obj=obj,
                place=where,
                attempt_id=attempt_id,
                draft=draft,
            )
    verdict = check_as(
        store,
        primary,
        *found,
        operation,
        output,
        recipients,
        context=context,
        obj=obj,
        draft=draft,
        skip=skip,
        place=place,
        attempt_id=attempt_id,
    )
    if structured is None:
        return verdict
    if structured["outcome"] == "held":
        return {**structured, "judged": verdict} if verdict else structured
    return {**verdict, "structured_warned": structured["warned"]} if verdict else structured


def inbound(
    store,
    primary,
    principal,
    operation,
    output,
    recipients=(),
    *,
    source,
    authenticated="",
    origin="",
    place=None,
    context=None,
    obj="",
):
    """The check for a message that came in from outside this server: a Slack message. The Slack bridge calls it.
    `principal` is who the message is held as: a roster person or bot, or the outside sender's actor. With no principal
    it is held as `source:origin`, which only Who everyone and With rules name, so no message skips the check.
    `authenticated` is the bridge's own actor (default: the principal), `source` the route ("slack") and `origin` the
    outside id. The verdict and any hold keep all of them. A message
    held here needs its own replayer (`replayer`), registered by the bridge, or Send anyway refuses it."""
    held_as = principal or f"{source}:{origin}"
    return check_as(
        store,
        primary,
        held_as,
        authenticated or held_as,
        source,
        operation,
        output,
        recipients,
        context=context,
        obj=obj,
        place=place,
        origin={"source": str(source), "origin": str(origin or "")},
    )


def refused(problem):
    """The verdict for a write whose audience the caller may not name (`Problem`: an unknown or hidden recipient, a thread
    they cannot read). Nothing can be judged, so nothing may go: `apply` raises it inside the write. There, a saved retry
    has already replayed its answer, so the retry of a write that went still answers as it did."""
    return {"refused": problem}


@SRC.scoped("rule-check")
def outside_edition(store, primary, output, to=""):
    """A Company page edition against the rules whose Who is Outside editions. Not `preflight`: that one skips a person's
    writes and fails open, and an edition goes outside the company whoever wrote it. Here anything short of the judge
    finding the whole text clean (a broken rule of any strength, checks Off, no engine, the budget, the day's cap, a
    failed or malformed answer) is `held`. Runs outside any transaction. Returns {"outcome": "pass"|"held", "reason",
    "broken", "rules"}; events and the answer never carry the text."""
    rules = []

    def result(outcome, reason=None, broken=()):
        return {
            "outcome": outcome,
            "reason": reason,
            "broken": list(broken),
            "rules": [{"id": r["id"], "version": r["version"], "text": r["text"]} for r in rules],
        }

    try:
        with store.read() as c:
            rules = _rule_rows(R.outside_edition_rules(c))
            if not rules:
                return result("pass", "no_rules")
            if not on(c):
                return result("held", "checks_off")
            level = RULE_LEVEL
            qset = J.load_set(QUESTION_SET)
            key = digest("outside_edition", "", [to], output, "", rules, qset["label"], level)
            hit = c.execute("SELECT broken FROM org_rule_verdicts WHERE digest=?", (key,)).fetchone()
            if hit:
                broken, _ = _cached(hit[0])
                return result("held", "broken", broken) if broken else result("pass")
            # Every rule is judged, in one call unless the rules do not fit beside the text in one (`J.pack`: its size
            # and the Gateway's MAX_QUESTIONS), never refused only for how many there are.
            batches = J.pack(
                rules,
                lambda batch: (
                    _questions(qset, batch),
                    {
                        "rules": [{"id": f"r{n}", "text": r["text"]} for n, r in enumerate(batch, 1)],
                        "operation": "an edition sent outside the company",
                        "output": output,
                        "from": "the company",
                        "to": [to] if to else [],
                        "context": {},
                    },
                ),
            )
            if batches is None:
                return result("held", "too_large")
            engine = B.server_engine(store, c, primary)
            if engine is None:
                return result("held", "budget" if B.over_spend_cap(c, store.settings) else "no_engine")
            if calls_today(c) + len(batches) > B.daily_cap(store.settings, "bot"):
                return result("held", "day_cap")
        cut = float(qset["thresholds"]["broken"])
        broken, calls = [], []
        for batch, questions, state in batches:
            answer, error = _ask(calls_for(store), engine, state, questions, qset["label"], level)
            if error:
                return result("held", error)
            if not isinstance(answer, dict):
                return result("held", "shape")
            answers = J.check_answers(answer.get("answers"), questions)
            found = [batch[int(qid[1:]) - 1]["id"] for qid in questions if J.noul(answers, qid) >= cut]
            broken += found
            with store.transaction() as c:
                calls.append(
                    H.event(
                        c,
                        H.KEEPER,
                        "judge.call",
                        qset["label"],
                        {
                            "label": qset["label"],
                            "questions": len(questions),
                            "operation": "outside_edition",
                            "level": level,
                            "rules": [r["id"] for r in batch],
                            "broken": len(found),
                            **B.audit_detail(answer),
                        },
                    )
                )
        with store.transaction() as c:
            c.execute(
                "INSERT OR REPLACE INTO org_rule_verdicts(digest, broken, call_id, created_at) VALUES(?,?,?,?)",
                (key, json.dumps(broken), calls[0]["id"], H.now()),  # the first call's id, as `preflight` caches
            )
        return result("held", "broken", broken) if broken else result("pass")
    except J.JudgeError:
        return result("held", "shape")
    except Exception as exc:  # noqa: BLE001 - whatever went wrong, the edition is held for a person
        LOG.warning("Edition rule check failed: %s", type(exc).__name__)
        return result("held", "error")


def holds(verdict):
    """Whether a verdict will hold the write (so a caller skips ask ingest's own call: a block skips it, and so does a
    refusal)."""
    if verdict and "refused" in verdict:
        return True
    if verdict and verdict.get("check") == "structured":
        return verdict["outcome"] == "held"
    if not verdict or verdict["outcome"] not in ("broken", "too_large"):
        return False
    return any(r["if_broken"] != "warn" for r in verdict["rules"] if r["id"] in verdict["broken"])


# ----------------------------------------------------------------------------- inside the write
def _day(at=None):
    return (at or H.now())[:10] + "T00:00:00"


def _present(c, actor):
    """A person who can act on a hold now: on the roster, not marked as left, their sign-in not turned off."""
    from .auth import standing

    pid = H.actor_id(actor) if actor else ""
    return bool(pid and H.human(c, pid) and not standing(c, pid))


def owner_or_admin(c):
    """The team owner, else the first Admin, who is on the team: whoever decides a hold nobody nearer the bot can."""
    from . import access

    for email in access.owner_and_admin_emails(c):
        row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
        if row and _present(c, row["id"]):
            return H.human_actor(row["id"])
    return None


def reviewer_for(c, author, rules):
    """Who reviews a hold: a broken rule's named reviewer, else for a person's hold the editor of a rule it broke (still
    on the team), else for a bot's the first person up its chain, its operator, the default person (screening's
    `person_for`), else the team owner, else an Admin; each one still on the team. Never a bot. None only when the team
    has none of them: the hold is then the owner's and Admins' (`_reader`)."""
    for rule in rules:
        pid = rule.get("reviewer")
        if pid and _present(c, pid):
            return H.human_actor(pid)
    if H.is_human(author):
        for rule in rules:
            editor = rule.get("suggested_by") or ""
            if H.is_human(editor) and _present(c, editor):
                return editor
        return owner_or_admin(c)
    from .report_managers import person_above
    from .screening import person_for

    slug = H.actor_id(author)
    above = person_above(c, slug)
    if above and _present(c, above):
        return H.human_actor(H.actor_id(above))
    found = person_for(c, slug)
    if found and _present(c, found):
        return found
    return owner_or_admin(c)


def _repeats(c, author, rule_id):
    return c.execute(
        "SELECT count(*) FROM events INDEXED BY events_task_origin WHERE actor=? AND action IN "
        "('rules.blocked','rules.escalated') AND target=? AND created_at>=?",
        (author, "org_rule:" + rule_id, _day()),
    ).fetchone()[0]


@SRC.scoped("rule-check")
def _tell_bot(c, author, text):
    """A note on the bot's next run, as the keeper (a plain row: a paused bot's note waits, nothing is refused)."""
    if not H.is_bot(author):
        return
    row = {
        "id": H.new_id(),
        "from_actor": H.KEEPER,
        "to_actor": author,
        "body": text[: H.NOTE_MAX],
        "created_at": H.now(),
    }
    c.execute(
        "INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES (:id,:from_actor,:to_actor,:body,:created_at)",
        row,
    )
    H.event(c, H.KEEPER, "note.create", row["id"], {"to": author, "org_rule": True})


def _author_name(c, author):
    """The name a held write's author is called by: a person's, or a bot's display name."""
    if H.is_human(author):
        return H.person_name(c, H.actor_id(author)) or H.actor_id(author)
    return (H.bot(c, H.actor_id(author)) or {}).get("display_name") or H.actor_id(author)


@SRC.scoped("rule-check")
def _review_task(c, hold_id, author, rule, reviewer):
    """The review task for a hold (or its appeal). Its title is the plain REVIEW_TITLE, which is also its thread's row in
    the sidebar, so it names no one and no rule: the hold's text is in the body's Hold line, opened from there."""
    body = (
        "A message or output was held by an org rule. Open the hold to Keep blocked, Send anyway or Edit rule. "
        "Nothing from the output is copied here.\n\nHold: " + hold_id
    )
    task = H.task_create(
        c,
        H.KEEPER,
        REVIEW_TITLE,
        body,
        reviewer,
        labels=[REVIEW_LABEL],
        lint=False,
        deduplicate=False,
    )
    return task["id"]


def appeal_line(c, hold_id, reviewer, author=""):
    """How a bot's held output is appealed (its own `tico rules appeal`). A person's hold has no such line: they ask the
    reviewer themselves."""
    if author and H.is_human(author):
        return ""
    name = H.person_name(c, H.actor_id(reviewer)) if reviewer else ""
    return f"Think it's wrong? Ask {name or 'your manager'}: `tico rules appeal {hold_id}`"


def current_obj(c, obj):
    """What a check's object (`<task id>@<version>`) is now: the task's current version, "" once it is gone. Any other
    object is taken as it is."""
    tid, sep, _ = str(obj or "").rpartition("@")
    if not sep or not tid:
        return obj or ""
    row = c.execute("SELECT version FROM tasks WHERE id=?", (tid,)).fetchone()
    return f"{tid}@{row[0]}" if row else ""


def decided(gate):
    """A verdict already applied (Send anyway's new check of rules changed since the hold): `apply` hands back its gate
    as it is, so the replayed write puts a new Warn's line on the recipient's copy with no second check."""
    return {"applied": gate} if gate else None


def changed(c, verdict, author=None, operation="", recipients=(), place=None):
    """A write whose destination or object moved between its check and its write: delivered unchecked (the engine is
    never called inside the write), with a `rules.unchecked` event, reason `changed`. With no verdict (nothing was
    checked for the old destination), one is made from the rules matching the new one and its place."""
    if verdict:
        return {**verdict, "outcome": "unchecked", "error": "changed", "broken": []}
    if not author or not on(c):
        return None
    recipients = sorted({r for r in recipients if r and r != author})
    rules = _rule_rows(R.matching(c, author, recipients, place))
    if not rules:
        return None
    return {
        "author": author,
        "sender": author,
        "via": "",
        "operation": operation,
        "recipients": recipients,
        "rules": rules,
        "digest": "",
        "broken": [],
        "outcome": "unchecked",
        "error": "changed",
        "call": None,
        "cached": False,
        "draft": False,
        "matched": sorted((r["id"], r["version"]) for r in rules),
        "place": place,
        "origin": None,
        "attempt_id": "",
        "context": {},
        "obj": "",
    }


def _people(recipients, author):
    """The recipients as a check names them: sorted, without blanks and without the author."""
    return sorted({r for r in recipients or () if r and r != author})


def apply(
    c, verdict, payload=None, replay=None, tell=False, obj_now=None, audience_now=None, judged=None, private_to=None
):
    """The verdict inside the write. Returns None to deliver as is; {"warn": line, ...} to deliver with the warning
    line on the recipient's copy; {"held": view} to write nothing and answer with the hold. `payload` is the text the
    reviewer reads, `replay` what Send anyway needs to run the normal write. `tell` also leaves the bot a note (a
    write the bot does not see answered: a completion reply, an automation). `obj_now`: the object as the write reads
    it (`current_obj`). Moved since the check, a verdict that holds still holds (a version bump never releases a
    Block); any other comes back as {"moved": True}, and the caller does not write: its next try checks again.
    `audience_now`: the recipients the write reads now (the route's audience, read inside the write). Every verdict
    belongs to the audience it checked, including a route recorded with `outcome=no_rules` when no rule matched,
    so a different one comes back as {"moved": True}. A refused verdict
    (`refused`) raises its error here, so the write fails with it. `judged`: the whole text the check judged, when the
    reviewer's `payload` leaves some of it out (a private task's words): a hold keeps it where only Send anyway's
    recheck reads it (`hold_input`), never shown. `private_to`: {private task id: the recipients the write reaches only
    through that task}; a hold lists them only to a reader who may read one of those tasks (`_shown_to`)."""
    if verdict and "refused" in verdict:
        raise verdict["refused"]
    if verdict and "applied" in verdict:
        return verdict["applied"]
    if not verdict or verdict.get("draft"):
        return None
    if obj_now is not None and verdict.get("obj") and obj_now != verdict["obj"] and not holds(verdict):
        return {"moved": True}
    author, operation = verdict["author"], verdict["operation"]
    if audience_now is not None and _people(audience_now, author) != list(verdict["recipients"]):
        return {"moved": True}
    if verdict.get("check") == "structured":
        return _structured_apply(c, verdict, payload, replay, tell, judged, private_to)
    if verdict["outcome"] in ("broken", "too_large"):
        # The rules in force now must be the ones checked: a rule edited, retired or added since is a new check, which
        # cannot run here (no engine under the write lock), so the output goes through unchecked.
        now = {(r["id"], r["version"]) for r in R.matching(c, author, verdict["recipients"], verdict.get("place"))}
        checked = verdict.get("matched")
        checked = (
            {tuple(x) for x in checked} if checked is not None else {(r["id"], r["version"]) for r in verdict["rules"]}
        )
        if now != checked:
            verdict = {**verdict, "outcome": "unchecked", "error": "rules_changed"}
    if verdict["outcome"] == "unchecked" or verdict.get("unchecked_rules"):
        H.event(
            c,
            author,
            "rules.unchecked",
            operation,
            {
                "operation": operation,
                "reason": verdict.get("error") or "unchecked",
                "rules": verdict.get("unchecked_rules") or [r["id"] for r in verdict["rules"]],
                **({"call": verdict["call"]} if verdict.get("call") else {}),
            },
        )
    if verdict["outcome"] not in ("broken", "too_large"):
        return (
            _warn_structured(c, verdict, verdict["structured_warned"], tell)
            if verdict.get("structured_warned")
            else None
        )
    broken = [r for r in verdict["rules"] if r["id"] in verdict["broken"]]
    if not broken:
        return None
    too_large = verdict["outcome"] == "too_large"
    strictest = max(broken, key=lambda r: STRICT[r["if_broken"]])
    mode = strictest["if_broken"]
    if too_large and mode == "warn":
        # Every rule it meets is Warn: delivered, its line saying it went unchecked. A Block or Escalate rule holds it.
        # `local` is a cause here the caller put on this path (the day's calls spent: backend/task_rule_gate.py).
        cause = verdict.get("local") or "too_large"
        H.event(
            c,
            author,
            "rules.unchecked",
            operation,
            {"operation": operation, "reason": cause, "rules": [r["id"] for r in broken]},
        )
        could_not = "was too long to check" if cause == "too_large" else "could not be checked"
        line = f"Warning: {_author_name(c, author)}'s message {could_not} against the rule '{strictest['text']}'."
        told = (
            f"Your {OPERATIONS.get(operation, operation)} was delivered with a warning: it "
            + ("is too long to check" if cause == "too_large" else "could not be checked today")
            + f" against the rule '{strictest['text']}'."
        )
        if tell:
            _tell_bot(c, author, told)
        return {
            "warn": line,
            "warned": {"rules": [{"id": r["id"], "text": r["text"]} for r in broken], "text": told, "too_large": True},
        }
    repeat = False
    if mode == "block" and _repeats(c, author, strictest["id"]) >= REPEAT_BLOCKS - 1:
        mode, repeat = "escalate", True
    if mode == "warn":
        line = f"Warning: {_author_name(c, author)}'s message may break the rule '{strictest['text']}'."
        H.event(
            c,
            author,
            "rules.warned",
            "org_rule:" + strictest["id"],
            {"operation": operation, "rules": [r["id"] for r in broken], "cached": verdict["cached"]},
        )
        told = (
            f"Your {OPERATIONS.get(operation, operation)} was delivered with a warning: it may break the rule "
            f"'{strictest['text']}'."
        )
        if tell:
            _tell_bot(c, author, told)
        return {"warn": line, "warned": {"rules": [{"id": r["id"], "text": r["text"]} for r in broken], "text": told}}
    return _write_hold(
        c,
        verdict,
        broken,
        strictest,
        mode,
        repeat,
        too_large,
        payload,
        replay,
        tell,
        judged=judged,
        private_to=private_to,
    )


def _write_hold(
    c,
    verdict,
    broken,
    strictest,
    mode,
    repeat,
    too_large,
    payload,
    replay,
    tell,
    *,
    check="judged",
    error=None,
    judged=None,
    private_to=None,
):
    """The hold a write is held in, written and told. `broken` are the rules that hold it, `strictest` the one the hold
    is on and `mode` its outcome (`block` or `escalate`; an escalation with no reviewer is a block). `check` is the check
    that held it: `judged`, or `structured` with `error`, the class of a check that could not complete. Returns apply's
    answer: {"held": view, "rule", "hold_id"}."""
    author, operation = verdict["author"], verdict["operation"]
    target = "org_rule:" + strictest["id"] if strictest["id"] else "org_rule_check"
    reviewer = reviewer_for(c, author, broken)
    if mode == "escalate" and not reviewer:
        mode = "block"  # nobody to file a review task for: a Block the owner or an Admin can still release
    hold_id, nonce = H.new_id(), secrets.token_urlsafe(24)
    task_id = None
    if mode == "escalate":
        if repeat:
            row = c.execute(
                "SELECT task_id FROM org_rule_holds WHERE author=? AND created_at>=? AND task_id IS NOT NULL "
                "AND json_extract(verdict,'$.rule')=? AND json_extract(verdict,'$.repeat')=1 "
                "ORDER BY created_at LIMIT 1",
                (author, _day(), strictest["id"]),
            ).fetchone()
            task_id = row[0] if row else None
        if task_id is None and reviewer:
            task_id = _review_task(c, hold_id, author, strictest, reviewer)
    text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str)
    # The whole text the check judged, kept only when the reviewer's copy leaves some of it out.
    judged = judged if isinstance(judged, str) or judged is None else json.dumps(judged, sort_keys=True, default=str)
    judged = judged if judged and judged != text else None
    body = (replay or {}).get("body") or {}
    if operation == "task_answer":
        asked = H.answer_question(c, body.get("target") or {})
        text, text_refs = H.answer_text(asked, body.get("answers") or {}, text=text)
    elif operation in ("answer", "reply"):
        text, text_refs = H.answer_text(
            H.message(c, replay["message"]), replay.get("text") or body.get("text") or "", text=text
        )
    else:
        text_refs = {}
    from .files import page_references

    text, text_refs = H.sourced_text(text, [{"file": fid} for fid in sorted(page_references(replay, c))], text_refs)
    # The run the output came from: Send anyway writes as the bot with it, so the run's private tasks and meetings are
    # checked against the destination again, as the original write was.
    # The check's own input (`input`) too: a rule changed since is checked on what this check was shown, and Send anyway
    # refuses once the object moved. `path` is the write's route, whose privacy guard Send anyway runs again.
    stored = {
        "text": text,
        **text_refs,
        "replay": replay,
        "provenance": {
            "attempt_id": verdict.get("attempt_id") or "",
            "path": PATH.get(),
            "authenticated": verdict.get("sender") or author,
            "via": verdict.get("via") or "",
            "place": verdict.get("place"),
            "origin": verdict.get("origin"),
        },
        "input": {
            "context": verdict.get("context") or {},
            "obj": verdict.get("obj") or "",
            # The entities and addresses the Directory walk started from: a structured recheck (Send anyway, an
            # approved mail or letter) walks from the same recipients. Older holds have neither and recheck without.
            **{k: list(verdict[k]) for k in ("targets", "contacts") if verdict.get(k)},
            # What the check read when the reviewer's text leaves some of it out (a private task's words). Only
            # `recheck` reads it: never a hold's view, its list, its events or its answer.
            **({"text": judged} if judged else {}),
        },
        # Recipients reached only through a private task, by task: never shown, read by `_shown_to` alone.
        **({"private_to": {t: sorted(set(a)) for t, a in private_to.items() if a}} if private_to else {}),
    }
    matched = (
        verdict["matched"]
        if verdict.get("matched") is not None
        else sorted((r["id"], r["version"]) for r in verdict["rules"])
    )
    detail = {
        "rule": strictest["id"],
        "outcome": mode,
        "broken": [r["id"] for r in broken],
        "repeat": repeat,
        "too_large": too_large,
        "cached": verdict["cached"],
        "call": verdict.get("call"),
        "digest": verdict["digest"],
        "versions": {r["id"]: r.get("version") for r in verdict["rules"] if r["id"]},
        "matched": [list(x) for x in matched],
    }
    if check == "structured":
        detail.update({"check": check, "error": error})
    c.execute(
        "INSERT INTO org_rule_holds(id, operation, payload, author, destinations, rule_ids, "
        "verdict, nonce, state, reviewer, task_id, created_at, check_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            hold_id,
            operation,
            json.dumps(stored, default=str),
            author,
            json.dumps(verdict["recipients"]),
            json.dumps([r["id"] for r in broken if r["id"]]),
            json.dumps(detail),
            nonce,
            "held",
            reviewer,
            task_id,
            H.now(),
            check,
        ),
    )
    action = "rules.escalated" if mode == "escalate" else "rules.blocked"
    H.event(
        c,
        author,
        action,
        target,
        {
            "hold": hold_id,
            "operation": operation,
            "rules": [r["id"] for r in broken],
            "cached": verdict["cached"],
            "reviewer": reviewer,
            **({"repeat": True} if repeat else {}),
            **({"too_large": True} if too_large else {}),
            **({"task": task_id} if task_id else {}),
        },
    )
    if check == "structured" and error:
        why = f"the Contacts rules could not be checked ({error}), so it is held until a person checks it"
        quoted = ""
    else:
        why = "it broke the rule"
        if too_large:
            local = verdict.get("local")
            why = f"it {'could not be checked today' if local else 'is too long to check'} against the rule"
        quoted = f" '{strictest['text']}'"
    told = (
        f"Your {OPERATIONS.get(operation, operation)} was held: {why}{quoted}. "
        + ("It is waiting for review. " if mode == "escalate" else "")
        + appeal_line(c, hold_id, reviewer, author)
    ).strip()
    if tell:
        _tell_bot(c, author, told)
    view = {
        "id": hold_id,
        "outcome": "escalated" if mode == "escalate" else "blocked",
        "rules": [{"id": r["id"], "text": r["text"]} for r in broken if r["id"]],
        "operation": operation,
        "reviewer": H.person_name(c, H.actor_id(reviewer)) if reviewer else None,
        "text": told,
        **({"too_large": True} if too_large else {}),
    }
    return {"held": view, "rule": strictest, "hold_id": hold_id}


def warned_text(gate, text):
    """The recipient's copy: the warning line, then the text."""
    if gate and gate.get("warn"):
        return gate["warn"] + "\n\n" + (text or "")
    return text


def warn_line(gate):
    """The warning line alone (a mail's top line, an answer the writer builds itself), or ""."""
    return (gate or {}).get("warn") or ""


# A warning line as apply writes it (`warn`), alone in its paragraph at the head of a text warned_note built.
WARN_LINE = re.compile(
    r"Warning: [^\n]*'s message (?:may break|was too long to check against|could not be checked against) "
    r"the rule '[^\n]*'\."
)


def unwarned(text):
    """`text` without the warning lines a warned write put before it (warned_note's shape), the text kept whole: a
    copy kept and warned again carries one line, the current one."""
    text = text or ""
    while True:
        head, sep, rest = text.partition("\n\n")
        if not WARN_LINE.fullmatch(head):
            return text
        text = rest if sep else ""


def warned_note(gate, note):
    """A note the warning line is put before, the note kept whole (a hand-off's note, a title change's note)."""
    line = warn_line(gate)
    if not line:
        return note
    return line + ("\n\n" + note if note else "")


def answer(gate):
    """What the write's answer carries for the bot: the hold, or the warning it was delivered with."""
    if not gate:
        return {}
    if gate.get("held"):
        return {"held": gate["held"]}
    if gate.get("warned"):
        return {"rule_warning": gate["warned"]}
    return {}


def held_note(c, author, rule):
    """The one line the person waiting on a held completion reply sees, without the text."""
    return f"{_author_name(c, author)}'s reply was held: it broke the rule '{rule['text']}'."


# ----------------------------------------------------------------------------- holds
def _hold(c, hold_id):
    row = c.execute("SELECT * FROM org_rule_holds WHERE id=?", (hold_id,)).fetchone()
    if not row:
        raise Problem("not_found", "There is no hold " + str(hold_id), 404)
    return dict(row)


def _reviewer_gone(c, hold):
    """The hold names no reviewer, or one who has left or whose sign-in is off."""
    return not (hold["reviewer"] and _present(c, hold["reviewer"]))


def _admin_reads_hold(c, who, hold, auth):
    """Whether an Admin (or the owner) reads a hold whose reviewer is missing or gone. App-only Tico support reads as an
    Admin everywhere else, but not here: a held output is the owner's and Admins' to read (PR 2004 follow-up)."""
    return who.support != "app" and auth.bot_admin(who) and _reviewer_gone(c, hold)


def _person_reviewer(c, who, hold, auth):
    """Whether a person may keep or send a person's held write: the reviewer it names, the author of a rule it broke, or
    anyone who may edit one of those rules (`R.may_manage`: a lead of a group in its Who, a bot's manager, the owner and
    the Admins). With the reviewer gone, app-only Tico support is left out of the Admins' part."""
    if hold["reviewer"] == who.actor:
        return True
    app_locked = who.support == "app" and _reviewer_gone(c, hold)
    ctx = None
    for rid in json.loads(hold["rule_ids"] or "[]"):
        row = c.execute("SELECT * FROM org_rules WHERE id=?", (rid,)).fetchone()
        if not row:
            continue
        if row["suggested_by"] == who.actor:
            return True
        ctx = ctx or R._ctx(c)
        if not app_locked and R.may_manage(c, auth, who, R._row(row)["who"], ctx):
            return True
    return False


WITHDRAW_ROLES = ("author", "represented")  # the readers who may take a held write back (it stays blocked)
HELD_HOURS = 24  # a held write nobody decides in this long is kept unsent (`expire_unreviewed`)
HELD_EXPIRE_MAX = 50  # holds expired per hourly sweep, oldest first; the rest follow on later sweeps


def represented(c, author):
    """The person a bot's hold is held for: the person its author bot reports up to, the one a default review goes to.
    Only that first person reads the bot's holds, not the rest of its chain. A person's own hold is theirs (`author`)."""
    if H.is_human(author):
        return author if _present(c, author) else None
    from .report_managers import person_above

    above = person_above(c, H.actor_id(author))
    return above if above and _present(c, above) else None


def _is_author(who, hold):
    """Whether `who` wrote the hold: the bot itself by its token, or the person, whatever token or Assistant carried
    them."""
    actor = who.actor
    if who.role == "bot" and actor == hold["author"] and not who.via:
        return True
    return H.is_human(actor) and actor == hold["author"]


def _is_reviewer(who, hold, c=None, auth=None):
    """Whether `who` reviews the hold: may send it or keep it. Not exclusive of authorship: a rule's author or editor,
    or an Admin, reviews their own hold too. A bot's hold is reviewed by its reviewer, by the owner always (whoever can
    change a block's source can waive it, reviewer named or not), and by an Admin when the reviewer is missing or gone
    (with `c` and `auth`; app-only Tico support is not an Admin here, see `_admin_reads_hold`); a person's hold by the
    reviewer it names and the editors and Admins of its rules (`_person_reviewer`). An Assistant or BotOps acting for a
    person releases nothing."""
    actor = who.actor
    if not H.is_human(actor) or who.via in ("botops", "assistant"):
        return False
    if hold.get("check_kind") == "structured":
        return c is not None and auth is not None and _rule_editor(c, who, hold, auth)
    if H.is_human(hold["author"]):
        return c is not None and auth is not None and _person_reviewer(c, who, hold, auth)
    if actor == hold["reviewer"]:
        return True
    if c is None or auth is None:
        return False
    if who.role == "owner":
        return True
    return _admin_reads_hold(c, who, hold, auth)


def _is_represented(who, hold, c=None):
    """Whether `who` is the person a bot's hold is held for (`represented`): they read it and may withdraw it, not
    release it. An Assistant or BotOps acting for them does neither."""
    return (
        c is not None
        and H.is_human(who.actor)
        and who.via not in ("botops", "assistant")
        and not H.is_human(hold["author"])
        and who.actor == represented(c, hold["author"])
    )


def _reader(who, hold, c=None, auth=None):
    """Who reads a hold first: "author", else "reviewer", else "represented", else None. A hold's author and its
    reviewer can be the same person, so callers that need one capability ask `_is_author`, `_is_reviewer` or
    `_is_represented`."""
    if _is_author(who, hold):
        return "author"
    if _is_reviewer(who, hold, c, auth):
        return "reviewer"
    return "represented" if _is_represented(who, hold, c) else None


def view_hold(c, who, hold, role=None, summary=False, auth=None):
    """One hold as its reader sees it. `summary` leaves out the held text, the appeal and the nonce (a list's row:
    the reader opens each hold for those)."""
    if role:
        author, reviewer, stands_in = role == "author", role == "reviewer", role == "represented"
    else:
        author, reviewer, stands_in = (
            _is_author(who, hold),
            _is_reviewer(who, hold, c, auth),
            _is_represented(who, hold, c),
        )
    if not (author or reviewer or stands_in):
        raise Problem("not_found", "There is no hold " + hold["id"], 404)
    stored = json.loads(hold["payload"] or "{}")
    hidden = _hidden_from(c, who, stored.get("private_to"))
    stored = {} if summary else stored
    if not summary:
        from .files import readable_page_refs

        stored = readable_page_refs(c, auth, who, stored)
    verdict = json.loads(hold["verdict"] or "{}")
    rule_ids = json.loads(hold["rule_ids"] or "[]")
    destinations = [a for a in json.loads(hold["destinations"] or "[]") if a not in hidden]
    to_count = len(destinations)
    if summary:
        # A list row is bounded whatever the hold names: a few short rule previews and names, and the counts.
        rule_ids, destinations = rule_ids[:SUMMARY_RULES], destinations[:SUMMARY_NAMES]
    rules = []
    for rid in rule_ids:
        row = c.execute(
            "SELECT id, kind, text, version, status, if_broken FROM org_rules WHERE id=?", (rid,)
        ).fetchone()
        if row:
            rules.append({**dict(row), "text": row["text"][:SUMMARY_CHARS]} if summary else dict(row))
    reviewer_name = H.person_name(c, H.actor_id(hold["reviewer"])) if hold["reviewer"] else None
    cut = lambda v: v[:SUMMARY_CHARS] if summary and isinstance(v, str) else v
    out = {
        "id": hold["id"],
        "operation": hold["operation"],
        "author": hold["author"],
        "author_name": cut(_author_name(c, hold["author"])),
        "state": hold["state"],
        "outcome": "escalated" if verdict.get("outcome") == "escalate" else "blocked",
        "rules": rules,
        "to": [cut(n) for n in _names(c, destinations)],
        "reviewer": hold["reviewer"],
        "reviewer_name": cut(reviewer_name),
        "task_id": hold["task_id"],
        "created_at": hold["created_at"],
        "decided_at": hold["decided_at"],
        "decided_by": hold["decided_by"],
        "can_send": reviewer and hold["state"] == "held" and hold["operation"] in REPLAY,
        "can_withdraw": (author or stands_in) and hold["state"] == "held",
    }
    if summary:
        out["appealed"] = bool(hold["why"])
        out["rules_count"] = len(json.loads(hold["rule_ids"] or "[]"))
        out["to_count"] = to_count
        return out
    text = stored.get("text") or ""
    out["text"] = text[:HOLD_TEXT_CHARS]
    edited = (stored.get("replay") or {}).get("message_id") if hold["operation"] == "threads_edit" else None
    if edited:
        out["message_id"] = edited  # the message a held edit would change: it stays under that message
    if len(text) > HOLD_TEXT_CHARS:
        out["text_cut"] = len(text)
    if reviewer:
        out["why"] = hold["why"]
        if hold["state"] == "held":
            out["nonce"] = hold["nonce"]
    return out


def _hidden_from(c, who, private_to):
    """The recipients a hold leaves out for `who`: those it reaches only through private tasks `who` may not read. The
    judge read them all (the hold's `destinations`); a reader who may not read a private task learns nothing of who is
    on it, nor that it exists."""
    if not private_to:
        return set()
    from . import task_privacy

    readable = {}
    for tid in private_to:
        row = H.task(c, tid)
        readable[tid] = bool(row) and task_privacy.task_readable(c, who, row)
    shown = {a for tid, actors in private_to.items() if readable[tid] for a in actors}
    return {a for actors in private_to.values() for a in actors} - shown


def hold_view(c, who, hold_id, auth=None):
    """A held write's hold as `who` reads it: its text and its actions (Edit and resend, Send anyway with its nonce when
    `who` may send it too). The answer of a held write carries this, not apply's bare view of the reason."""
    return view_hold(c, who, _hold(c, hold_id), auth=auth)


def held_answer(c, who, gate, auth=None):
    """A held write's answer as its sender reads it: the hold as `who` reads it (`hold_view`: its text, its actions, the
    nonce when `who` may send it), the reason the sender was told (`reason`), and `too_large` when the check could not
    read the whole text."""
    told = gate["held"]
    return {
        "held": {
            **hold_view(c, who, gate["hold_id"], auth=auth),
            "reason": told["text"],
            **({"too_large": True} if told.get("too_large") else {}),
        }
    }


def _reader_sql(who):
    """`_reader` as SQL, so a page counts only holds this caller may read: a bot's own (author), or, for a person, every
    row, each settled by `_reader` (`fallback`), which needs the database and the caller's permissions."""
    if who.role == "bot" and not who.via:
        return "author=?", (who.actor,), False
    if H.is_human(who.actor):
        return "1", (), True
    return None, (), False


def _bots_below(c, person):
    """The bots that may report up to `person` first (report_managers.person_above), as actors, at most BOTS_BELOW: the
    authors whose holds can be held for them. More than that is fine; `represented` settles each hold."""
    found, frontier, seen = [], [person], {person}
    while frontier and len(found) < BOTS_BELOW:
        rows = c.execute(
            f"SELECT bc.bot FROM bot_config bc {BS.JOIN} WHERE bc.reports_to IN (SELECT value FROM json_each(?)) OR "
            "(COALESCE(bc.reports_to,'')='' AND "
            "json_extract(s.extra,'$.reports_to') IN (SELECT value FROM json_each(?)))",
            (json.dumps(frontier), json.dumps(frontier)),
        ).fetchall()
        frontier = [r[0] for r in rows if r[0] not in seen]
        seen.update(frontier)
        found += frontier
    return [f"bot:{b}" for b in found[:BOTS_BELOW]]


def held_for(c, who, operations, limit=None):
    """The held writes of these operations that this person reads as their reviewer, as their author, or as the person
    it is held for, oldest first, each with its `role`, at most `limit`. The owner's and Admins' fallback is not listed:
    a hold with no reviewer is for their review page, not a person's list. Only their own candidates are read: their
    own holds and their bots' (the author index) and the ones they review (org_rule_holds_reviewer)."""
    marks = ",".join("?" * len(operations))
    authors = [who.actor, *(_bots_below(c, who.actor) if H.is_human(who.actor) else [])]
    most = -1 if limit is None else max(0, int(limit))
    pick = f"SELECT id, author, reviewer, created_at FROM org_rule_holds WHERE state='held' AND operation IN ({marks}) "
    rows = c.execute(
        f"SELECT * FROM ({pick}AND author IN (SELECT value FROM json_each(?)) ORDER BY created_at, id "
        f"LIMIT ?) UNION SELECT * FROM ({pick}AND reviewer=? ORDER BY created_at, id LIMIT ?) "
        "ORDER BY created_at, id LIMIT ?",
        (*operations, json.dumps(authors), most, *operations, who.actor, most, most),
    ).fetchall()
    out = []
    for row in rows:
        role = _reader(who, dict(row), c)
        if role is None and row["reviewer"] == who.actor and who.via not in ("botops", "assistant"):
            role = "reviewer"  # a person's hold names its reviewer: no rule rows here
        if role in ("reviewer", "represented", "author"):  # author: a person's own held post
            out.append({**dict(row), "role": role})
    return out


def _page(c, who, where, args, cursor, limit, auth, fallback):
    """One page of the holds `where` selects that the caller may read, oldest first, as summaries: at most `limit`
    and at most HOLD_LIST_BYTES. `next_cursor` continues it."""
    limit = max(1, min(int(limit or HOLD_LIST_MAX), HOLD_LIST_MAX))
    after = ("", "")
    if cursor:
        created, _, hid = str(cursor).partition("|")
        if not created or not hid:
            raise Problem("validation", "That cursor is not valid", 422)
        after = (created, hid)
    query = c.execute(
        f"SELECT * FROM org_rule_holds WHERE {where} AND (created_at>? OR (created_at=? AND id>?)) "
        "ORDER BY created_at, id",
        (*args, after[0], after[0], after[1]),
    )
    rows = []
    for row in query:  # one row past the page says there is more
        role = _reader(who, dict(row), c, auth) if fallback else None
        if fallback and not role:
            continue
        rows.append((dict(row), role))
        if len(rows) > limit:
            break
    # The whole answer counts: its envelope and the longest cursor it can carry, then each row as serialized.
    used = len(json.dumps({"holds": [], "next_cursor": "x" * 120}))
    out = []
    for row, _ in rows[:limit]:
        # Each row says what its reader may do (send and withdraw are separate capabilities), so no role is passed.
        item = view_hold(c, who, row, summary=True, auth=auth)
        size = len(json.dumps(item, default=str)) + 1
        if used + size > HOLD_LIST_BYTES:
            break
        out.append(item)
        used += size
    more = len(out) < len(rows)
    last = out[-1] if out else None
    return out, (f"{last['created_at']}|{last['id']}" if more and last else None)


def holds_listed(c, who, task_id="", thread="", cursor=None, limit=HOLD_LIST_MAX, auth=None):
    """The holds one caller may read, in one of three lists: a review task's (any state), a thread's held writes, or
    every held write the caller may read. Summaries, paged (`_page`)."""
    where, args, fallback = _reader_sql(who)
    if not where:
        return [], None
    if task_id:
        scope, scope_args = "task_id=?", (task_id,)
    elif thread:
        scope = "state='held' AND json_extract(payload,'$.provenance.place.thread')=?"
        scope_args = (thread,)
    else:
        scope, scope_args = "state='held'", ()
    return _page(c, who, f"{scope} AND {where}", (*scope_args, *args), cursor, limit, auth, fallback)


def holds_for_task(c, who, task_id, cursor=None, limit=HOLD_LIST_MAX, auth=None):
    """One page of a review task's holds the caller may read (`holds_listed`)."""
    return holds_listed(c, who, task_id=task_id, cursor=cursor, limit=limit, auth=auth)


@SRC.scoped("rule-check")
def _finish_task(c, hold):
    """The review task closes once none of its holds waits."""
    tid = hold["task_id"]
    if not tid:
        return
    left = c.execute("SELECT 1 FROM org_rule_holds WHERE task_id=? AND state='held' LIMIT 1", (tid,)).fetchone()
    task = H.task(c, tid)
    if not left and task and task["status"] == "open":
        H.attempt(c, H.task_close, H.KEEPER, tid, "Every hold on this task is decided.")


def _decide(c, who, hold, state, event=""):
    H._writer(c, who.actor, task_id=hold["task_id"])
    c.execute(
        "UPDATE org_rule_holds SET state=?, decided_at=?, decided_by=?, nonce=? WHERE id=? AND state='held'",
        (state, H.now(), who.actor, "used:" + secrets.token_hex(8), hold["id"]),
    )
    from . import audit_log

    audit_log.write(
        c,
        who.actor,
        "org_rule_hold:" + hold["id"],
        {"state": "held"},
        {"state": state},
        who.via or H.VIA.get() or "api",
    )
    H.event(
        c,
        who.actor,
        event or "org_rule_hold." + state,
        "org_rule_hold:" + hold["id"],
        {"operation": hold["operation"], "author": hold["author"]},
    )
    _finish_task(c, {**hold, "state": state})


def keep(c, who, hold_id, auth=None):
    """Keep a hold blocked: the reviewer's call. Its author, or the person a bot's hold is held for, keeping it is a
    withdraw."""
    hold = _hold(c, hold_id)
    if not _is_reviewer(who, hold, c, auth):
        if _reader(who, hold, c, auth) in WITHDRAW_ROLES:
            return withdraw(c, who, hold_id, auth)
        raise Problem("not_found", "There is no hold " + hold_id, 404)
    if hold["state"] != "held":
        raise Problem("conflict", "This hold is already decided", 409)
    _decide(c, who, hold, "kept")
    _tell_bot(
        c,
        hold["author"],
        f"Your held {OPERATIONS.get(hold['operation'], hold['operation'])} stays blocked (hold {hold_id}).",
    )
    return view_hold(c, who, _hold(c, hold_id), "reviewer", auth=auth)


def withdraw(c, who, hold_id, auth=None):
    """The author takes back a held write to send it again (Edit and resend, which is a new check), or the person a
    bot's hold is held for withdraws the bot's post: the hold is kept, decided by them, and nothing is delivered."""
    hold = _hold(c, hold_id)
    author = _is_author(who, hold)
    if not (author or _is_represented(who, hold, c)):
        raise Problem("not_found", "There is no hold " + hold_id, 404)
    if hold["state"] != "held":
        raise Problem("conflict", "This hold is already decided", 409)
    _decide(c, who, hold, "kept", "org_rule_hold.withdrawn")
    if not author:
        _tell_bot(
            c,
            hold["author"],
            f"Your held {OPERATIONS.get(hold['operation'], hold['operation'])} was withdrawn "
            f"and stays blocked (hold {hold_id}).",
        )
    return view_hold(c, who, _hold(c, hold_id), auth=auth)


def expire_unreviewed(c, at, limit=HELD_EXPIRE_MAX):
    """Keep, unsent, each held write nobody decided within HELD_HOURS. The keeper decides it as `keep` does, so its
    review task closes, and the bot that wrote it is told it was not sent, so it can write it again within the rules or
    drop it. Nothing here delivers a held payload. Oldest first, at most `limit` per sweep."""
    cutoff = H.shift(at, hours=-HELD_HOURS)
    keeper = SimpleNamespace(actor=H.KEEPER, via="", task_actor="")
    expired = []
    rows = c.execute(
        "SELECT * FROM org_rule_holds WHERE state='held' AND created_at<? ORDER BY created_at, id LIMIT ?",
        (cutoff, max(0, int(limit))),
    ).fetchall()
    for row in rows:
        hold = dict(row)
        with isolated(c, "held_expiry", hold["id"]):
            _decide(c, keeper, hold, "kept", "org_rule_hold.expired")
            _tell_bot(
                c,
                hold["author"],
                f"Your held {OPERATIONS.get(hold['operation'], hold['operation'])} was not sent: nobody reviewed it "
                f"within {HELD_HOURS} hours (hold {hold['id']}). Write it again within the rules, or drop it.",
            )
            expired.append(hold["id"])
    return expired


def hold_provenance(hold):
    """What a hold keeps of its check: the attempt, the route, the actor that sent it (`authenticated`) and its `via`,
    the place, the outside origin, and the write's route (`path`)."""
    return json.loads(hold["payload"] or "{}").get("provenance") or {}


def replay_identity(c, hold, auth):
    """The identity a held write runs as on Send anyway: the person it was held for, rebuilt with their own permissions
    (`auth.identity_for_actor`, refused once they are off the team), or for a bot's hold the bot, with the run it came
    from (its private tasks and meetings)."""
    if H.is_human(hold["author"]):
        return auth.identity_for_actor(c, hold["author"])
    from .auth import Identity

    return Identity(
        hold["author"], "bot", agent="org-rule-hold", attempt_id=hold_provenance(hold).get("attempt_id") or ""
    )


def hold_matched(hold):
    """The (rule id, version) pairs that matched when the output was held."""
    verdict = json.loads(hold["verdict"] or "{}")
    if verdict.get("matched") is not None:
        return {tuple(x) for x in verdict["matched"]}
    return {(rid, v) for rid, v in (verdict.get("versions") or {}).items()}


def _replay(hold):
    return json.loads(hold["payload"] or "{}").get("replay") or {}


def current_people(c, hold, audience=None, auth=None):
    """Who the held write reaches now: its audience as the route counts it, or the destinations it was held for when its
    operation names no way to count them."""
    now = current_audience(c, hold, audience, auth)
    return now if now is not None else json.loads(hold["destinations"] or "[]")


def place_now(c, hold, people, replay=None):
    """The place the held write has now, read again on the people it reaches now (teams, outside members, the thread's
    participants). A write that starts its thread (`start`, with no thread yet) has the place it would start."""
    stored = hold_provenance(hold).get("place")
    if not stored:
        return None
    replay = replay if replay is not None else _replay(hold)
    if replay.get("start") is not None and not stored.get("thread"):
        start = replay["start"]
        audience = "personal" if "dm" in start else (start.get("audience") or "members")
        return place_for_start(
            c,
            hold["author"],
            people,
            audience=audience,
            audience_group_id=start.get("audience_group_id"),
            members=start.get("dm") or start.get("members") or (),
        )
    return place_for(c, hold["author"], people, stored.get("thread") or "")


def current_matched(c, hold, audience=None, auth=None):
    """The (rule id, version) pairs that match the held write now: its people and place as they are now. A structured
    hold's pairs are the structured rules that hold or warn it now too."""
    people = current_people(c, hold, audience, auth)
    place = place_now(c, hold, people)
    pairs = {(r["id"], r["version"]) for r in R.matching(c, hold["author"], people, place)}
    if hold.get("check_kind") == "structured":
        fresh = _structured_hold_verdict(c, hold, people, place)
        pairs |= {(r["id"], r["version"]) for r in (fresh or {}).get("rules", []) if r["id"]}
    return pairs


def hold_input(hold):
    """What a hold's check was shown besides its text and audience: its context and its object, and for a structured hold
    the `targets` and `contacts` its Directory walk started from (absent on older holds)."""
    stored = json.loads(hold["payload"] or "{}")
    return stored.get("input") or {"context": {}, "obj": ""}


def _unchanged(c, hold):
    """Refuse Send anyway once the object the output was checked against changed (a task edited since): its new
    content was never checked. The bot or a person writes it again, and that write is checked fresh."""
    obj = hold_input(hold).get("obj") or ""
    if obj and current_obj(c, obj) != obj:
        raise Problem(
            "changed", "What this was about changed since it was held; it stays held. Write it again to check it.", 409
        )


def current_audience(c, hold, audience=None, auth=None):
    """Who the held write would reach now, as its check counts them, or None when its operation names no way to tell
    (a mail, addressed by the bot's own tool) or its replay would be refused anyway (the replayer's own checks)."""
    fn = (audience if audience is not None else AUDIENCE).get(hold["operation"])
    if fn is None:
        return None
    stored = json.loads(hold["payload"] or "{}")
    try:
        found = fn(c, replay_identity(c, hold, auth), stored.get("replay") or {})
    except Problem:
        return None
    return sorted({r for r in found or () if r and r != hold["author"]})


def _same_audience(c, hold, audience=None, auth=None):
    """Refuse Send anyway once the held write would reach someone else than its check was for (a task handed to a
    new owner since), or would be somewhere else (a team or an outside member since): a rule's With and Where are
    matched on the actual recipients and place, so that is a new check. Read inside Send anyway's transaction."""
    now = current_audience(c, hold, audience, auth)
    if now is not None and now != sorted(json.loads(hold["destinations"] or "[]")):
        raise Problem(
            "changed", "Who this goes to changed since it was held; it stays held. Write it again to check it.", 409
        )
    stored = hold_provenance(hold).get("place")
    if stored and place_now(c, hold, current_people(c, hold, audience, auth)) != stored:
        raise Problem(
            "changed", "Where this goes changed since it was held; it stays held. Write it again to check it.", 409
        )


def recheck(store, primary, hold, audience=None, auth=None):
    """Outside the write, before Send anyway or an approved mail: when the rules that match now differ from those at the
    hold (a rule edited or added since), the rules changed or added are a new check, on the same input the first check
    had (text, audience, context, object, place). Returns (the pairs matching now, that verdict or None). The write
    checks the pairs again and refuses on a change in between. A changed audience refuses first (`_same_audience`). A
    structured hold is checked again by the Directory's rules on its input: an error holds it, and a judged block of the
    new check holds it too."""
    structured = hold.get("check_kind") == "structured"
    with store.read() as c:
        _same_audience(c, hold, audience, auth)
        people = current_people(c, hold, audience, auth)
        place = place_now(c, hold, people)
        now = {(r["id"], r["version"]) for r in R.matching(c, hold["author"], people, place)}
        fresh = _structured_hold_verdict(c, hold, people, place) if structured else None
        now |= {(r["id"], r["version"]) for r in (fresh or {}).get("rules", []) if r["id"]}
    if now <= hold_matched(hold) and not (fresh or {}).get("error"):
        return sorted(now), None
    stored = json.loads(hold["payload"] or "{}")
    shown, prov = hold_input(hold), hold_provenance(hold)
    verdict = check_as(
        store,
        primary,
        hold["author"],
        prov.get("authenticated") or hold["author"],
        prov.get("via") or "",
        hold["operation"],
        shown.get("text") or stored.get("text") or "",
        people,
        context=shown.get("context") or None,
        obj=shown.get("obj") or "",
        skip=hold_matched(hold),
        place=place,
        attempt_id=prov.get("attempt_id") or "",
        origin=prov.get("origin"),
    )
    if fresh is None:
        return sorted(now), verdict
    if fresh["outcome"] == "held":
        return sorted(now), fresh
    if holds(verdict):
        return sorted(now), verdict
    if verdict is None:
        return sorted(now), fresh
    return sorted(now), {**verdict, "structured_warned": fresh["warned"]}


def _rechecked(c, hold, check, audience=None, auth=None):
    """Inside the write: the rules matching now are those `recheck` saw, and a new check it made does not hold the
    output. A warn or an unchecked result is recorded and the send goes on (the reviewer chose to send it); the warn's
    gate comes back, for the replayed write to put its line on the recipient's copy."""
    if check is None:
        return None
    expected, verdict = check
    if current_matched(c, hold, audience, auth) != {tuple(x) for x in expected}:
        raise Problem("conflict", "The rules changed while this was being sent; open it again", 409)
    if not verdict:
        return None
    if holds(verdict):
        if verdict.get("error"):
            raise Problem(
                "unchecked",
                f"The Contacts rules could not be checked ({verdict['error']}), so this stays held until a person "
                "checks it.",
                409,
            )
        broken = [r for r in verdict["rules"] if r["id"] in verdict["broken"]] or verdict["rules"]
        raise Problem(
            "rule_changed",
            f"It now breaks the rule '{broken[0]['text']}', changed since it was held. It "
            "stays held: edit that rule, or keep it blocked.",
            409,
        )
    return apply(c, verdict)


def send(c, who, hold_id, nonce, ingest=None, table=None, check=None, auth=None, audience=None):
    """Send anyway: the normal write runs once, as the bot, with the hold's one-shot nonce; no Decide call for the
    rules it was held on. `check` is `recheck`'s answer: rules changed since the hold are checked again first. The
    write's route guard runs again as the bot and its run (`auth` given), and the replayer runs the write's own access
    checks: a refusal keeps the hold, its nonce unspent. A hold that kept no route (written before holds kept one)
    cannot be guarded that way, so it is refused: the bot writes it again."""
    hold = _hold(c, hold_id)
    if not _is_reviewer(who, hold, c, auth):
        raise Problem("not_found", "There is no hold " + hold_id, 404)
    if hold["state"] != "held":
        raise Problem("conflict", "This hold is already decided", 409)
    if not nonce or not secrets.compare_digest(str(nonce), hold["nonce"]):
        raise Problem("conflict", "This hold changed; open it again", 409)
    fn = (table or REPLAY).get(hold["operation"])
    if fn is None:
        raise Problem("unsupported", "This hold can't be sent from here", 409)
    stored = json.loads(hold["payload"] or "{}")
    if "path" not in (stored.get("provenance") or {}):
        raise Problem(
            "recheck",
            "This hold is from an older version and can't be sent as it is; it stays held. Write it again to check it.",
            409,
        )
    _unchanged(c, hold)
    _same_audience(c, hold, audience, auth)
    gate = _rechecked(c, hold, check, audience, auth)
    bot = replay_identity(c, hold, auth)
    path = stored["provenance"]["path"] or ""
    if auth is not None and path:
        from . import task_privacy

        task_privacy.guard_write(c, bot, path, auth)
    # The nonce is spent first: a retry of this request cannot send twice. A structured hold's Send anyway is a waiver
    # by the rule's editor or an Admin, recorded as one (`org_rule_hold.waived`, with `decided_by`).
    waived = "org_rule_hold.waived" if hold.get("check_kind") == "structured" else ""
    _decide(c, who, hold, "sent", waived)
    result = fn(c, bot, hold, stored.get("replay") or {}, ingest, gate)
    return {"hold": view_hold(c, who, _hold(c, hold_id), "reviewer"), "result": result}


def appeal(c, who, hold_id, why):
    """The author bot sends its hold to the reviewer as a review task. `why` stays on the hold, for the reviewer."""
    hold = _hold(c, hold_id)
    if not _is_author(who, hold):
        raise Problem("not_found", "There is no hold " + hold_id, 404)
    if hold["state"] != "held":
        raise Problem("conflict", "This hold is already decided", 409)
    H._writer(c, who.actor, task_id=hold["task_id"])
    why = str(why or "").strip()[:2000]
    task_id = hold["task_id"]
    if not hold["reviewer"]:
        raise Problem("no_reviewer", "Nobody on the team can review this hold", 409)
    if not task_id:
        rid = (json.loads(hold["verdict"] or "{}")).get("rule")
        rule = c.execute("SELECT id, text FROM org_rules WHERE id=?", (rid,)).fetchone()
        task_id = _review_task(c, hold_id, hold["author"], dict(rule) if rule else {"text": "a rule"}, hold["reviewer"])
    c.execute("UPDATE org_rule_holds SET why=?, task_id=? WHERE id=?", (why, task_id, hold_id))
    H.event(c, who.actor, "org_rule_hold.appealed", "org_rule_hold:" + hold_id, {"task": task_id})
    return {"id": hold_id, "task_id": task_id, "reviewer": H.person_name(c, H.actor_id(hold["reviewer"]))}


# ----------------------------------------------------------------------------- mail and letters
# A held send the bot sends again (a mail, a letter) is found by its key in the hold's replay, by operation; NOUNS name it.
SEND_KEYS = {"mail": "mail_digest", "letter": "letter_id"}
NOUNS = {"mail": "email", "letter": "letter"}


def mail_digest(to, cc, subject, body):
    raw = json.dumps(
        [sorted(a.lower() for a in to), sorted(a.lower() for a in cc), subject, body], separators=(",", ":")
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def mail_recipients(c, addresses):
    """The roster people a mail's To and Cc name, as actors: they count for a rule's With. Other addresses match only
    rules whose With is empty."""
    from . import groups as G
    from . import people as P

    roster = G._roster(c)
    out = []
    for address in addresses:
        found = P.person_by_email(address, roster)
        if found:
            out.append(H.human_actor(found["id"]))
    return out


def company(c):
    """Everyone on the roster, as actors: the audience of a write the whole company reads (a public task, a company
    update)."""
    from . import groups as G

    return [H.human_actor(p["id"]) for p in (G._roster(c).get("people") or []) if p.get("id")]


def mail_approved(c, author, key, operation="mail"):
    """A held mail (or letter) the reviewer chose to send anyway, not yet sent: the next identical check passes it,
    once."""
    row = c.execute(
        "SELECT * FROM org_rule_holds WHERE author=? AND operation=? AND state='sent' "
        f"AND json_extract(payload,'$.replay.{SEND_KEYS[operation]}')=? "
        "AND json_extract(verdict,'$.used') IS NULL ORDER BY decided_at LIMIT 1",
        (author, operation, key),
    ).fetchone()
    return dict(row) if row else None


def claim_mail(c, hold_id):
    """Spend a mail approval inside the write: only one request ever changes the row."""
    return (
        c.execute(
            "UPDATE org_rule_holds SET verdict=json_set(verdict,'$.used',1) WHERE id=? "
            "AND json_extract(verdict,'$.used') IS NULL",
            (hold_id,),
        ).rowcount
        == 1
    )


def mail_warning_line(gate):
    """The line a warned mail carries at the top of its body, for the recipient."""
    return warn_line(gate)


def open_mail_hold(c, author, key, operation="mail"):
    """The hold already waiting on this exact mail (or letter), if any: a draft sent again while it waits is that hold,
    not a new one (one review task per mail)."""
    row = c.execute(
        "SELECT * FROM org_rule_holds WHERE author=? AND operation=? AND state='held' "
        f"AND json_extract(payload,'$.replay.{SEND_KEYS[operation]}')=? ORDER BY created_at DESC LIMIT 1",
        (author, operation, key),
    ).fetchone()
    return dict(row) if row else None


def held_again(c, hold, verdict=None):
    """The answer for a mail whose hold already waits: the same hold. `verdict` (this attempt's, which holds): the
    attempt counts as a block of the hold's rule toward the repeats rule, and the third escalates that same hold."""
    if verdict is not None:
        hold = _blocked_again(c, hold, verdict)
    verdict = json.loads(hold["verdict"] or "{}")
    rules = []
    for rid in json.loads(hold["rule_ids"] or "[]")[:SUMMARY_RULES]:
        row = c.execute("SELECT id, text FROM org_rules WHERE id=?", (rid,)).fetchone()
        if row:
            rules.append(dict(row))
    return {
        "id": hold["id"],
        "outcome": "escalated" if verdict.get("outcome") == "escalate" else "blocked",
        "rules": rules,
        "operation": hold["operation"],
        "text": f"This {NOUNS[hold['operation']]} is still held. " + appeal_line(c, hold["id"], hold["reviewer"]),
    }


def _blocked_again(c, hold, verdict):
    """One more blocked attempt at a held mail: recorded as a block of its rule; the bot's third block of that rule
    today escalates the hold (a review task, the day's repeat task reused). The hold row comes back as it is now."""
    detail = json.loads(hold["verdict"] or "{}")
    rid, author = detail.get("rule"), hold["author"]
    if detail.get("outcome") != "block" or not rid:
        return hold
    rules = json.loads(hold["rule_ids"] or "[]")
    if _repeats(c, author, rid) < REPEAT_BLOCKS - 1:
        H.event(
            c,
            author,
            "rules.blocked",
            "org_rule:" + rid,
            {
                "hold": hold["id"],
                "operation": hold["operation"],
                "rules": rules,
                "cached": bool(verdict.get("cached")),
                "reviewer": hold["reviewer"],
                "again": True,
            },
        )
        return hold
    row = c.execute(
        "SELECT task_id FROM org_rule_holds WHERE author=? AND created_at>=? AND task_id IS NOT NULL "
        "AND json_extract(verdict,'$.rule')=? AND json_extract(verdict,'$.repeat')=1 "
        "ORDER BY created_at LIMIT 1",
        (author, _day(), rid),
    ).fetchone()
    task_id = hold["task_id"] or (row[0] if row else None)
    if task_id is None and hold["reviewer"]:
        rule = c.execute("SELECT id, text FROM org_rules WHERE id=?", (rid,)).fetchone()
        task_id = _review_task(c, hold["id"], author, dict(rule) if rule else {"text": "a rule"}, hold["reviewer"])
    c.execute(
        "UPDATE org_rule_holds SET verdict=json_set(verdict,'$.outcome','escalate','$.repeat',1), task_id=? WHERE id=?",
        (task_id, hold["id"]),
    )
    H.event(
        c,
        author,
        "rules.escalated",
        "org_rule:" + rid,
        {
            "hold": hold["id"],
            "operation": hold["operation"],
            "rules": rules,
            "cached": bool(verdict.get("cached")),
            "reviewer": hold["reviewer"],
            "repeat": True,
            **({"task": task_id} if task_id else {}),
        },
    )
    return _hold(c, hold["id"])


def supersede_mail(c, hold_id):
    """An approval a newer rule held again is spent: the new hold's Send anyway is the approval that counts."""
    c.execute(
        "UPDATE org_rule_holds SET verdict=json_set(verdict,'$.used','superseded') WHERE id=? "
        "AND json_extract(verdict,'$.used') IS NULL",
        (hold_id,),
    )


@replayer("mail")
def _replay_mail(c, bot, hold, replay, ingest, gate=None):
    """A held mail is never queued: Send anyway lets that exact mail pass its next check in the send queue
    (backend/email_sending.py), once, and tells the bot to send it again."""
    _tell_bot(
        c,
        hold["author"],
        f"The email you were told was held may go now: send that exact draft again (hold {hold['id']}).",
    )
    return {"approved": True}


@replayer("letter")
def _replay_letter(c, bot, hold, replay, ingest, gate=None):
    """A held letter is never sent from here: Send anyway lets that letter pass its next check, once, and tells the bot
    to send it again (as a held mail)."""
    _tell_bot(
        c,
        hold["author"],
        f"The letter you were told was held may go now: send letter {replay.get('letter_id')} again (hold {hold['id']}).",
    )
    return {"approved": True}


THREAD_REPLAY = ("kind", "refs", "parent_message_id", "via", "origin", "attachments", "in_reply_to", "start")


def _audience_people(c, audience, group_id):
    """Everyone a company or team audience names (a thread's members are its own rows)."""
    if audience == "company":
        return set(company(c))
    if audience == "team" and group_id:
        from . import teammate_access

        return set(teammate_access.group_member_actors(c, group_id))
    return set()


def thread_people(c, replay):
    """The people a thread write reaches, as its check counts them: the named recipient, the members of the root of its
    thread and, for a company or team thread, everyone that audience names; or, for a write that starts its thread
    (`start`), the members and the audience it names. The route's preflight and the write inside `threads.post` both read
    this, so `apply` can tell the audience moved between them."""
    to = replay.get("recipient_id") or replay.get("to_actor")  # a hold stored before the rename
    out = {H.resolve_actor(c, to) or str(to)} if to else set()
    start = replay.get("start")
    if start is not None:
        out |= {H.resolve_actor(c, m) or str(m) for m in (start.get("dm") or start.get("members") or ()) if m}
        out |= _audience_people(c, start.get("audience"), start.get("audience_group_id"))
    elif replay.get("thread_id"):
        row = T.thread_row(c, replay["thread_id"])
        if row:
            root = T.thread_row(c, row.get("root_id") or row["id"]) or row
            out |= {r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?", (root["id"],))}
            out |= _audience_people(c, T.audience_of(root), root.get("audience_group_id"))
    return sorted(a for a in out if a)


def edit_people(c, replay):
    """The people a held edit of a thread message reaches: its thread's, as the check counted them."""
    message = H.message(c, replay.get("message_id") or "") or {}
    return thread_people(c, {"thread_id": message.get("conversation_id")})


@replayer("threads", audience=lambda c, bot, replay: thread_people(c, replay))
def _replay_thread(c, bot, hold, replay, ingest, gate=None):
    """A message held in a thread: Send anyway posts it again through `threads.post`, as the person it was held for
    (`bot` is their replay identity), with the check skipped; the hold's nonce and the reviewer's send are the check.
    `replay` is what core's post passed to `apply`: `thread_id` and `body`, and the post's own arguments
    (`THREAD_REPLAY`). A held post that would have started a thread, sub-thread or DM has no `thread_id` but its
    `start`, so Send anyway makes the thread then."""
    args = {k: replay[k] for k in THREAD_REPLAY if replay.get(k) is not None}
    recipient = replay.get("recipient_id") or replay.get("to_actor")  # a hold stored before the rename says to_actor
    if recipient:
        args["to_actor"] = recipient
    message = T.post(c, bot, replay.get("thread_id"), replay["body"], checked=None, **args)
    return {"id": (message or {}).get("id")}


# ----------------------------------------------------------------------------- stats and health
def stats(c, settings):
    day = _day()
    label = J.load_set(QUESTION_SET)["label"]
    checks = c.execute(
        "SELECT count(*) FROM events INDEXED BY events_task_origin WHERE actor=? AND action='judge.call' "
        "AND target=? AND created_at>=?",
        (H.KEEPER, label, day),
    ).fetchone()[0]

    def count(*actions):
        return c.execute(
            f"SELECT count(*) FROM events WHERE action IN ({','.join('?' * len(actions))}) AND created_at>=?",
            (*actions, day),
        ).fetchone()[0]

    hits = count("rules.blocked", "rules.escalated", "rules.warned")
    cap = float(B.budget_cap(c, settings))
    left = None if cap <= 0 else round(max(0.0, cap - B.spent_this_month(c)), 2)
    # What the people's checks cost this month, out of Decide's budget: the judge.call rows carry each call's cost.
    month = H.now()[:7] + "-01T00:00:00"
    people = c.execute(
        "SELECT coalesce(sum(json_extract(detail,'$.cost_usd')),0) FROM events WHERE actor=? AND "
        "action='judge.call' AND target LIKE ? AND created_at>=? AND json_extract(detail,'$.people')=1",
        (H.KEEPER, RULES_TARGET, month),
    ).fetchone()[0]
    return {
        "checks_today": checks,
        "unchecked_today": count("rules.unchecked"),
        "blocked_today": count("rules.blocked", "rules.escalated"),
        "hits_today": hits,
        "usd_left": left,
        "people_usd": round(float(people or 0), 2),
        "on": on(c),
    }


def health_findings(c, settings):
    """A high share of unchecked outputs in a day: the rules are not being enforced (backend/maintenance_health.py)."""
    try:
        if not on(c):
            return []
        s = stats(c, settings)
    except Exception:  # noqa: BLE001 - a Health check never fails the scan
        return []
    total = s["checks_today"] + s["unchecked_today"]
    if total < UNCHECKED_MIN or s["unchecked_today"] / total < UNCHECKED_SHARE:
        return []
    share = round(100 * s["unchecked_today"] / total)
    return [
        {
            "key": "org_rules_unchecked",
            "check_id": "org_rules_unchecked",
            "title": f"{share}% of rule checks went unchecked today",
            "detail": f"{s['unchecked_today']} of {total} bot outputs that rules apply to went out unchecked today "
            "(no decision model, the decisions monthly cap, the day's call cap, the deadline or an error).",
            "fix": "Check the Tico AI Gateway key and the Monthly cap in Automations > Maintenance; the Rules page "
            "header shows what is left.",
        }
    ]


# ----------------------------------------------------------------------------- HTTP
def mail_prepare(store, primary, who, author, to, cc, subject, body, contacts=()):
    """The org rule check of one mail, before its write: None when no rule is in force, else what `mail_decide` needs.
    `author` is the teammate the mail is checked as (a person, when BotOps sends it for one): its holds and approvals
    are found under it; `who` stays the caller, for the write's permission and audit. An approved mail (Send anyway) is
    not judged again on the rules it was held on; rules changed or added since are a new check, as for any Send anyway.
    Otherwise the whole mail is checked."""
    key = mail_digest(to, cc, subject, body)
    output = (f"Subject: {subject}\n\n" if subject else "") + body
    with store.read() as c:
        if not in_force(c):
            return None  # no rule in force: no roster read for the recipients, nothing written
        approved = mail_approved(c, author, key)
        recipients = mail_recipients(c, [*to, *cc])
    check = recheck(store, primary, approved) if approved else None
    verdict = (
        check[1]
        if approved
        else preflight(
            store,
            primary,
            who,
            "mail",
            output,
            recipients,
            context={"to": [a.lower() for a in [*to, *cc]]},
            contacts=contacts,
        )
    )
    return {"author": author, "key": key, "output": output, "approved": approved, "check": check, "verdict": verdict}


def mail_decide(c, prep):
    """Inside the mail's (or letter's, `prep["operation"]`) write: {"outcome": "pass" | "unchecked" | "warn" (with
    `warning_line`) | "held" (with `held`, whose `text` says the rule and whom to appeal to)}. A held mail is never
    sent."""
    if prep is None:
        return {"outcome": "pass"}
    author, key, output, approved, verdict = (prep[k] for k in ("author", "key", "output", "approved", "verdict"))
    operation = prep.get("operation") or "mail"
    noun, replay = NOUNS[operation], {SEND_KEYS[operation]: key}

    def outcome(gate):
        if gate and gate.get("held"):
            return {"outcome": "held", **answer(gate)}
        if gate and gate.get("warned"):
            return {"outcome": "warn", "warning_line": mail_warning_line(gate), **answer(gate)}
        return {"outcome": "unchecked" if verdict and verdict["outcome"] == "unchecked" else "pass"}

    waiting = open_mail_hold(c, author, key, operation) if holds(verdict) else None
    if approved:
        if current_matched(c, approved) != {tuple(x) for x in prep["check"][0]}:
            return {
                "outcome": "held",
                "held": {
                    "id": approved["id"],
                    "text": f"The rules changed while this {noun} was checked. Send it again to check it.",
                },
            }
        if holds(verdict):
            # A newer rule holds an approved mail: the old approval is spent, and the mail has one hold.
            supersede_mail(c, approved["id"])
            if waiting:
                return {"outcome": "held", "held": held_again(c, waiting, verdict)}
            return outcome(apply(c, verdict, output, replay))
        if not claim_mail(c, approved["id"]):
            # Another send already used this approval: this one stays a draft.
            return {
                "outcome": "held",
                "held": {"id": approved["id"], "text": f"This {noun}'s approval was already used. It stays a draft."},
            }
        gate = apply(c, verdict, output, replay) if verdict else None
        return {
            **outcome(gate),
            "outcome": "warn" if gate and gate.get("warned") else "pass",
            "approved": approved["id"],
        }
    if waiting:
        return {"outcome": "held", "held": held_again(c, waiting, verdict)}
    return outcome(apply(c, verdict, output, replay))


def seal(app):
    """Gives a built app every write registered while it was built, whatever order its parts installed in. Send anyway
    finds its replay here: a write missing from these tables is answered "can't be sent from here"."""
    table, prepare, audience = app.state.rule_tables
    table.update(REPLAY)
    prepare.update(PREPARE)
    audience.update(AUDIENCE)


def install(app, store, auth, mutate):
    from fastapi import Request
    from pydantic import Field
    from typing import Literal
    from .models import Contract

    # This app's writes, as registered while it was built (one server builds one app; tests build many). Routes that
    # install after this one register theirs later, so `seal` fills these once the app is whole.
    table, prepare, audience = dict(REPLAY), dict(PREPARE), dict(AUDIENCE)
    app.state.rule_tables = (table, prepare, audience)

    class HoldSend(Contract):
        nonce: str = Field(min_length=1, max_length=200)

    class HoldEmpty(Contract):
        pass

    class HoldAppeal(Contract):
        why: str = Field(default="", max_length=2000)

    class DraftCheck(Contract):
        text: str = Field(min_length=1, max_length=200_000)
        to: str = Field(min_length=1, max_length=200)
        as_: Literal["message", "comment", "task", "assign"] = Field(default="message", alias="as")

    class MailCheck(Contract):
        to: list[str] = Field(default_factory=list, max_length=200)
        cc: list[str] = Field(default_factory=list, max_length=200)
        subject: str = Field(default="", max_length=2000)
        body: str = Field(default="", max_length=500_000)

    @app.post("/api/v2/bots/{bot}/mail-check")
    def mail_check(request: Request, bot: str, body: MailCheck):
        """The org rule check of one mail without sending it (a draft read before it goes): the same check the send
        queue runs on every bot's email (backend/email_sending.py `rule_gate`). Only the bot itself asks, and BotOps
        asks for a person it acts for, whose mail is checked as that person (`checked_as`). A person's own mail is not
        checked (`not_checked`); another bot's token is refused."""
        who = request.state.identity
        found = checked_as(who, store.settings.assistant_bot)
        if not found or not H.is_bot(found[1]):
            return {"outcome": "not_checked"}
        with store.read() as c:
            named = H.resolve_bot(c, bot) or {}
        if found[1] != "bot:" + named.get("id", bot):
            raise Problem("forbidden", "A bot checks only its own mail", 403)
        prep = mail_prepare(
            store,
            app.state.judge,
            who,
            found[0],
            body.to,
            body.cc,
            body.subject,
            body.body,
            contacts=[*body.to, *body.cc],
        )
        return mutate(request, body, lambda c: mail_decide(c, prep))

    @app.get("/api/v2/rules/stats")
    def rules_stats(request: Request):
        """The Rules page header: checks today, unchecked, blocked, dollars left under Decide's monthly cap, and on/off."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return stats(c, store.settings)

    @app.get("/api/v2/rule-holds")
    def rule_holds(
        request: Request,
        task: str | None = None,
        thread: str | None = None,
        cursor: str | None = None,
        limit: int = HOLD_LIST_MAX,
    ):
        """The holds the caller may read, as summaries (open one for its text), a page at a time. `task` names a review
        task (its holds in any state), `thread` a conversation (its held writes), and neither lists every held write the
        caller may read. Pass `next_cursor` as `cursor` for the rest."""
        who = request.state.identity
        with store.read() as c:
            items, nxt = holds_listed(c, who, task or "", thread or "", cursor, limit, auth)
        return {"holds": items, "next_cursor": nxt}

    @app.get("/api/v2/rule-holds/{hold_id}")
    def rule_hold(request: Request, hold_id: str):
        who = request.state.identity
        with store.read() as c:
            hold = _hold(c, hold_id)
            return view_hold(c, who, hold, auth=auth)

    @app.post("/api/v2/rule-holds/{hold_id}/keep")
    def rule_hold_keep(request: Request, hold_id: str, body: HoldEmpty):
        who = request.state.identity
        return mutate(request, body, lambda c: keep(c, who, hold_id, auth))

    @app.post("/api/v2/rule-holds/{hold_id}/withdraw")
    def rule_hold_withdraw(request: Request, hold_id: str, body: HoldEmpty):
        """The author takes back a held write to send it again (Edit and resend): the hold is kept, nothing is sent."""
        who = request.state.identity
        return mutate(request, body, lambda c: withdraw(c, who, hold_id, auth))

    @app.post("/api/v2/rule-holds/{hold_id}/send")
    def rule_hold_send(request: Request, hold_id: str, body: HoldSend):
        who = request.state.identity
        ingest, check, ident = None, None, None
        with store.read() as c:
            hold = _hold(c, hold_id)
            ready = (
                _is_reviewer(who, hold, c, auth)
                and hold["state"] == "held"
                and not store.saved(c, who, request.url.path, request.headers.get("idempotency-key"))
            )
            if ready and hold["operation"] in prepare:
                ident = replay_identity(c, hold, auth)
        if ready:
            # Both outside the write: rules changed since the hold are a new check, and ask ingest runs as it would
            # have for the original write (the rules it was held on are not asked again).
            check = recheck(store, app.state.judge, hold, audience, auth)
            if ident is not None:
                stored = json.loads(hold["payload"] or "{}")
                try:
                    ingest = prepare[hold["operation"]](store, ident, stored.get("replay") or {})
                except Exception as exc:  # noqa: BLE001 - ask ingest's own preflight never stops the send
                    LOG.warning("Hold replay preflight failed: %s", type(exc).__name__)
        from . import ask_ingest

        return ask_ingest.judged(
            store,
            ingest,
            lambda: mutate(
                request, body, lambda c: send(c, who, hold_id, body.nonce, ingest, table, check, auth, audience)
            ),
        )

    @app.post("/api/v2/rule-holds/{hold_id}/appeal")
    def rule_hold_appeal(request: Request, hold_id: str, body: HoldAppeal):
        """The author bot asks the reviewer to look. Never checked by rules: it delivers nothing."""
        who = request.state.identity
        return mutate(request, body, lambda c: appeal(c, who, hold_id, body.why))

    @app.post("/api/v2/rules/check")
    def rules_check(request: Request, body: DraftCheck):
        """A bot checks a draft as the write it will be (`tico rules check`): the same input, and the verdict cached, so
        sending that exact draft costs no second call. A person's draft is checked as theirs."""
        who = request.state.identity
        found = checked_as(who, store.settings.assistant_bot)
        if not found:
            return {"outcome": "not_checked", "rules": [], "reason": "Nothing to check"}
        author = found[0]
        with store.read() as c:
            to = auth.target(c, who, body.to)
            used = B.used_today(c, author, action="rules.draft")
        cap = int(store.settings.rules_check_daily_drafts or 0)
        if used >= cap:
            raise Problem(
                "budget", f"{author} has checked {used} drafts today; the cap is {cap} until midnight UTC", 429
            )
        operation = DRAFT_AS[body.as_]
        verdict = preflight(store, app.state.judge, who, operation, body.text, [to], draft=True)
        with store.transaction() as c:
            H.event(c, author, "rules.draft", operation, {"operation": operation})
        if not verdict:
            return {"outcome": "pass", "rules": [], "cached": False}
        broken = [
            {"id": r["id"], "text": r["text"], "if_broken": r["if_broken"]}
            for r in verdict["rules"]
            if r["id"] in verdict["broken"]
        ]
        return {
            "outcome": verdict["outcome"],
            "rules": broken,
            "cached": verdict["cached"],
            **({"reason": verdict["error"]} if verdict.get("error") else {}),
        }


__all__ = [
    "ACTIONS",
    "OPERATIONS",
    "REPLAY",
    "answer",
    "apply",
    "author_of",
    "check_as",
    "checked_as",
    "digest",
    "ensure",
    "health_findings",
    "held_for",
    "held_note",
    "holds",
    "inbound",
    "install",
    "on",
    "place_for",
    "preflight",
    "replayer",
    "reviewer_for",
    "seal",
    "stats",
    "warned_text",
    "watched",
]
