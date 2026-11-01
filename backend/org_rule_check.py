"""Org rule checks (docs/rules-plan.md §3): a bot's output is checked against the org rules that apply to it before it
is delivered (`questions/rule-check.json`, asked through the server's Decide engine on the rules' own budget).

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
  message, or one from another Tico): `principal` is the teammate it is held for (or None, and then it is held as
  `source:origin`), `source` names the route (`slack`, `federation`), `authenticated` is the actor the caller proved,
  and `origin` the message's id on its side. `apply(c, verdict, payload=None, replay=None, tell=False, obj_now=None)`
  writes the verdict inside the write and returns None (deliver), `{"warn": line}` (deliver with the line), or
  `{"held": view}` (write nothing); `answer(gate)` is what the write answers with. `changed(...)` answers a write whose
  destination moved. A thread message is checked as operation `threads` (not `message`, the chat's replayer), with
  `payload` the body text (the reviewer reads it) and `replay={"thread_id", "body", "kind", "refs", "parent_message_id",
  "to_actor", "via", "origin", "attachments"}`: the `threads` replayer posts it again as the person it was held for.
  `place` is a dict, or a function of (c, principal, recipients) that reads it inside the check
  (`place_for`: the thread, its root and audience, kind, teams and outside members).
- Where: a rule's `where:` (`thread:<id>`, `team:<group>`, `kind:dm`, `has_outside_members`) is matched on the place.
  Messages and task comments have a place through their thread (a task comment's is its task's conversation). Mail and
  drafts have none, so a `where` rule never meets them.
- Fails open: checks Off, no engine, the rules budget, the day's call cap, the deadline, an error or a malformed
  answer let the output through with a `rules.unchecked` event. Events never carry the text.
- Bounded: at most `rule_check_concurrency` calls run at once in this process (`call_slots.Calls`); with every slot in
  use the output goes through unchecked at once (`busy`), never waiting for one. After BREAKER_FAILURES timeouts or
  errors in a row no call is made for BREAKER_SECONDS (`breaker`), then one call probes. Each level has its own
  deadline (DEADLINES), and the call itself is cut off then (`J.within`), so an abandoned call never runs on to the
  Gateway's own timeout holding its slot.
- Cache: verdicts are kept by a digest of the check input, the matched rules and versions, the set and the level, so
  `hub rules check` on a draft and then sending it costs one call.
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
from datetime import datetime, timedelta, timezone

from clients import judge as J
from . import judge as B
from . import org_rules as R
from . import groups as G
from . import team_rules
from . import threads as T
from .call_slots import NEUTRAL, Calls
from .store import H, Problem

LOG = logging.getLogger("tico.org_rule_check")
QUESTION_SET = "rule-check"
RULES_TARGET = B.RULES_TARGET             # the judge.call rows a rule check writes (`rule-check@<version>`)
# The most one check waits for its answer, by Decide level (Low is Jev; Medium and High are language models).
DEADLINES = {"low": 5, "medium": 10, "high": 15}
CONCURRENCY = 8               # calls at once, per process, when the setting names none
BUDGET = ("rules_monthly_usd", "rules.spend")     # backend/judge.py: its own cap and spend rows, apart from Decide's
STRICT = {"warn": 1, "block": 2, "escalate": 3}
REPEAT_BLOCKS = 3             # the 3rd block of one rule by one bot in a UTC day escalates for the rest of that day
REVIEW_LABEL = "rule-review"
CACHE_DAYS = 7
HOLD_LIST_MAX = 50
# One hold's text as its readers get it: past this many characters it is cut (`text_cut`); Send anyway sends it whole.
HOLD_TEXT_CHARS = 20_000
HOLD_LIST_BYTES = 100_000     # one page of hold summaries stops here too (a page always holds at least one)
TEAM_RULE = "org_rule_checks"
UNCHECKED_SHARE, UNCHECKED_MIN = 0.2, 10    # a day's unchecked share at or over this, of at least this many checks
ACTIONS = ("rules.blocked", "rules.escalated", "rules.warned", "rules.unchecked")
# What each operation is, in the words the model reads and the hold shows.
OPERATIONS = {
    "message": "a chat message", "comment": "a task comment", "comment_edit": "an edit to a task comment",
    "task_create": "a new task", "task_update": "a change to a task", "task_owner": "a task assignment",
    "task_ask": "a question on a task", "bulk": "a change to several tasks", "note": "a note to a bot",
    "reply": "a reply in a conversation", "automation": "an automation's write", "slack": "a Slack reply",
    "mail": "an email", "update": "an update", "task_question": "a question put to a person",
    "slack_message": "a Slack message", "federated_message": "a message from another Tico",
    "threads": "a thread message", "threads_edit": "an edit to a thread message",
    "chat_upload": "a chat message with files", "assistant_message": "a message to the Assistant",
}
DRAFT_AS = {"message": "message", "comment": "comment", "task": "task_create", "assign": "task_owner"}

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS org_rule_verdicts(digest TEXT PRIMARY KEY, broken_json TEXT NOT NULL, "
    "call_id TEXT, created TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS org_rule_verdicts_created ON org_rule_verdicts(created)",
    "CREATE INDEX IF NOT EXISTS org_rule_holds_task ON org_rule_holds(task_id) WHERE task_id IS NOT NULL",
    # Mails a bot's connector sent after a check that got no answer from Tico, by the id the connector gave each:
    # one `rules.unchecked` per id, however often it is reported (`record_misses`).
    "CREATE TABLE IF NOT EXISTS org_rule_misses(author TEXT NOT NULL, id TEXT NOT NULL, created TEXT NOT NULL, "
    "PRIMARY KEY (author, id))",
    "CREATE INDEX IF NOT EXISTS org_rule_misses_created ON org_rule_misses(created)",
)
MISSES_MAX = 100              # ids one report carries (connectors/mail keeps at most this many per bot)
MISSES_KEPT_DAYS = 90         # how long a reported id is remembered, so a late repeat still counts once
MISSES_DAY_MAX = 200          # connector-reported misses one bot can add to a UTC day's unchecked count

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
SUMMARY_RULES, SUMMARY_CHARS, SUMMARY_NAMES = 3, 200, 10   # a hold summary's rule previews, their length, its names


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


def in_force(c):
    """Whether any output can be checked now: an active rule exists and checks are on. One `SELECT ... LIMIT 1` when
    none is: a caller asks this before it resolves a write's audience (a roster read), so an install with no rules
    reads nothing more."""
    return bool(R.any_active(c)) and on(c)


def watched(c, bot):
    """Whether any active rule's Who names this bot: its live reply text stays hidden until delivered."""
    slug = H.actor_id(bot)
    try:
        if not R.any_active(c) or not on(c):
            return False
        key = R.who_key(c, "bot:" + slug)
        return any(R._hits(row["who"], key) for row in R._active(c))
    except Exception:  # noqa: BLE001 - no tables yet (an old copy): nothing to hide
        return False


def watched_bots(c):
    """The slugs of the bots `watched` is true for, read once: for a reader that filters many runs together (SQL)."""
    try:
        if not R.any_active(c) or not on(c):
            return []
        rules, ctx = R._active(c), R._ctx(c)
        return [row["slug"] for row in H.bots(c)
                if any(R._hits(rule["who"], R.who_key(c, "bot:" + row["slug"], ctx)) for rule in rules)]
    except sqlite3.OperationalError:    # no tables yet (an old copy): nothing to hide
        return []


# A run in these states has not handed in its reply yet: nothing it said is checked.
UNSETTLED = ("leased", "running", "expired")


def hides_run(c, attempt):
    """Whether a run's said text and raw stream stay hidden: its bot is watched and its reply is not checked yet, or
    its reply was held (`held_reply` on its result). `attempt` is its attempts row."""
    if not attempt:
        return False
    try:
        if json.loads(attempt["result_json"] or "{}").get("held_reply"):
            return True
    except (TypeError, ValueError):
        pass
    return attempt["state"] in UNSETTLED and watched(c, attempt["bot"])


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
    return [{"id": r["id"], "version": r["version"], "if_broken": r["if_broken"], "reviewer": r.get("reviewer"),
             "suggested_by": r.get("suggested_by") or "", "text": r["text"]} for r in rules]


def digest(operation, author, recipients, output, obj, rules, label, level, context=None):
    """The cache key: everything the model is shown (the context too: a mail's outside addresses, an automation's
    step) and everything that decides the verdict (the rules and versions, the set, the level)."""
    raw = json.dumps([operation, author, sorted(set(recipients)), output, obj or "", context or {},
                      sorted((r["id"], r["version"]) for r in rules), label, level],
                     separators=(",", ":"), sort_keys=True, default=str)
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
    return c.execute("SELECT count(*) FROM events WHERE actor=? AND action='judge.call' AND target LIKE ? AND ts>=? "
                     f"AND json_extract(detail_json,'$.people') {flag}", (H.KEEPER, RULES_TARGET, day)).fetchone()[0]


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
    `breaker` at once, asking nothing. The deadline is the call's own too (`J.within`): the Gateway, Jev and a
    company's own model each stop at it, so the thread and its slot end with it."""
    refused, probe = calls.admit()
    if refused:
        return None, refused
    seconds = deadline(level)
    box = {}

    def work():
        try:
            with J.within(seconds):
                box["result"] = B.ask(engine, state, questions, label, level)
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
    except RuntimeError:                    # no thread to be had: the slot goes back and the output goes unchecked
        calls.slots.release()
        calls.done(None, probe)
        return None, "busy"
    worker.join(seconds)
    if worker.is_alive():
        calls.done(False, probe)
        return None, "timeout"
    error = box.get("error")
    calls.done(None if error in NEUTRAL else not error, probe)
    return box.get("result"), error


def place_for(c, author, recipients=(), conversation_id=""):
    """Where a write happens, as `where:` reads it. `thread`: the conversation's id, which is the thread's id (core's
    threads are rows of `conversations`). `root` and `audience`: the thread's root and the audience its root names
    (backend/threads.py), so a sub-thread has its root's. `kind`: `dm` for a direct chat, a personal thread (a DM under
    core's audiences) or a write with no conversation, else `thread`. `teams`: the groups the sender and the thread's
    owner are in, and the team an audience names. `outside`: someone in it is outside the roster, or a Slack or
    federated actor. A write with no conversation (a direct message) has an empty `thread` and `root`."""
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
    for actor in (author, (conv or {}).get("owner_actor")):
        if actor:
            teams |= set(R.who_key(c, actor)["chain"])
    if root is not None and root.get("audience_group_id"):
        teams.add(root["audience_group_id"])
    # The native audience decides whether it is a DM; the legacy scope only where no audience is stored.
    dm = conv is None or (audience == "personal" if audience else conv.get("scope") == "direct")
    return {"thread": str(conversation_id or ""), "root": root_id if conv is not None else "", "audience": audience,
            "kind": "dm" if dm else "thread", "teams": sorted(teams), "outside": _outside(c, members)}


def _outside(c, people):
    """Whether someone in `people` is outside the roster, or a Slack or federated actor."""
    roster = {p.get("id") for p in (G._roster(c).get("people") or []) if p.get("id")}
    return any((H.is_human(a) and H.actor_id(a) not in roster) or (a and not H.is_human(a) and not H.is_bot(a))
               for a in people)


def place_for_start(c, author, recipients=(), *, audience="members", audience_group_id=None, members=()):
    """The place of a write that makes its thread (core's `post` with `start`). The thread is not there yet, so `thread`
    and `root` are empty and no `thread:` rule can name it; its audience, teams and outside members are the ones it
    will have. `members` are the names the caller gave."""
    named = [H.resolve_actor(c, m) or str(m) for m in members or () if m]
    teams = set(R.who_key(c, author)["chain"]) if author else set()
    if audience_group_id:
        teams.add(audience_group_id)
    return {"thread": "", "root": "", "audience": audience, "kind": "dm" if audience == "personal" else "thread",
            "teams": sorted(teams), "outside": _outside(c, list(recipients or ()) + named)}


def _place_now(c, place, author, recipients):
    """A write's place as the check reads it: a dict as it is, or the answer of a function, which is read only when a
    rule names a place."""
    if callable(place):
        return place(c, author, recipients) if R.any_where(c) else None
    return place or None


def check_as(store, primary, principal, sender, via, operation, output, recipients=(), *, context=None, obj="",
             draft=False, skip=(), place=None, attempt_id="", origin=None):
    """Check one output held as `principal` (a person or a bot). `sender` is who made it and `via` the route
    (`checked_as` reads both from an identity). `place`: the write's place, or a function of (c, principal,
    recipients) that reads it inside the check's read (`place_for`). Returns the verdict `apply` reads, or None when
    nothing is checked (no rule matches, checks are off, no principal). Never raises: a failure is a verdict marked
    unchecked. `skip`: (rule id, version) pairs already decided (Send anyway asks only the rules changed or added
    since its hold); the verdict's `matched` is every pair that matched, to check again inside the write. `origin`:
    where an outside message came from (`inbound`), kept on the verdict and its hold."""
    author = principal
    if not author:
        return None
    verdict = None
    try:
        recipients = sorted({r for r in recipients or () if r and r != author})
        is_person = H.is_human(author)
        with store.read() as c:
            if not in_force(c):
                return None             # no rule in force: nothing more is read, and the output is never serialized
            where = _place_now(c, place, author, recipients)
            rules = _rule_rows(R.matching(c, author, recipients, where))
            matched = sorted((r["id"], r["version"]) for r in rules)
            skip = {tuple(x) for x in skip or ()}
            rules = [r for r in rules if (r["id"], r["version"]) not in skip]
            if not rules:
                return None
            if where is None and callable(place):
                where = place(c, author, recipients)     # a held write keeps its place, whether or not a rule reads it
            output = output if isinstance(output, str) else json.dumps(output, sort_keys=True, default=str)
            level = store.settings.rule_check_level or "medium"
            qset = J.load_set(QUESTION_SET)
            key = digest(operation, author, recipients, output, obj, rules, qset["label"], level, context)
            verdict = {"author": author, "sender": sender or author, "via": via or "", "operation": operation,
                       "recipients": recipients, "rules": rules, "digest": key, "broken": [], "outcome": "pass",
                       "error": None, "call": None, "cached": False, "draft": draft, "matched": matched, "place": where,
                       "origin": origin, "attempt_id": attempt_id or "", "context": context or {}, "obj": obj or ""}
            hit = c.execute("SELECT broken_json, call_id FROM org_rule_verdicts WHERE digest=?", (key,)).fetchone()
            if hit:
                broken, unchecked = _cached(hit[0])
                return {**verdict, "broken": broken, "outcome": "broken" if broken else "pass", "cached": True,
                        "call": hit[1], **({"unchecked_rules": unchecked} if unchecked else {})}
            questions = _questions(qset, rules[:J.MAX_QUESTIONS])
            state = {"rules": [{"id": f"r{n}", "text": r["text"]} for n, r in enumerate(rules[:J.MAX_QUESTIONS], 1)],
                     "operation": OPERATIONS.get(operation, operation), "output": output,
                     "from": _names(c, [author])[0], "to": _names(c, recipients), "context": context or {}}
            if len(json.dumps(state, default=str)) > J.MAX_STATE_CHARS:
                return {**verdict, "outcome": "too_large", "broken": [r["id"] for r in rules]}
            engine = B.server_engine(store, c, primary, BUDGET)
            if engine is None:
                capped = B.over_spend_cap(c, store.settings, None, BUDGET)
                return {**verdict, "outcome": "unchecked", "error": "budget" if capped else "no_engine"}
            if calls_today(c, people=is_person) >= B.daily_cap(store.settings, "human" if is_person else "bot"):
                return {**verdict, "outcome": "unchecked", "error": "day_cap"}
        result, error = _ask(calls_for(store), engine, state, questions, qset["label"], level)
        if error in ("busy", "breaker"):
            return {**verdict, "outcome": "unchecked", "error": error}      # nothing was asked: no judge.call row
        if result is not None and not isinstance(result, dict):
            result, error = None, "shape"
        broken = None
        if result is not None and not error:
            try:
                answers = J.check_answers(result.get("answers"), questions)
                cut = float(qset["thresholds"]["broken"])
                broken = [rules[int(qid[1:]) - 1]["id"] for qid in questions if J.noul(answers, qid) >= cut]
            except (J.JudgeError, AttributeError, KeyError, TypeError, ValueError, IndexError):
                error = "shape"
        detail = {"label": qset["label"], "questions": len(questions), "operation": operation, "level": level,
                  **({"draft": True} if draft else {}), **({"people": True} if is_person else {})}
        if result is not None:
            detail.update(B.audit_detail(result))
        if error:
            detail["error"] = error
        else:
            detail["broken"] = len(broken)
        with store.transaction() as c:
            call = H.event(c, H.KEEPER, "judge.call", qset["label"], detail)
            if broken is not None:
                # A partial check (past MAX_QUESTIONS rules) keeps which rules it left out, so a retry that hits the
                # cache still records them unchecked.
                unchecked = [r["id"] for r in rules[J.MAX_QUESTIONS:]]
                stored = {"broken": broken, "unchecked": unchecked} if unchecked else broken
                c.execute("INSERT OR REPLACE INTO org_rule_verdicts(digest, broken_json, call_id, created) "
                          "VALUES(?,?,?,?)", (key, json.dumps(stored), call["id"], H.now()))
                c.execute("DELETE FROM org_rule_verdicts WHERE created<?", (H.shift(H.now(), days=-CACHE_DAYS),))
        if len(rules) > J.MAX_QUESTIONS and broken is not None:
            verdict["unchecked_rules"] = [r["id"] for r in rules[J.MAX_QUESTIONS:]]
        if broken is None:
            return {**verdict, "outcome": "unchecked", "error": error or "shape", "call": call["id"]}
        return {**verdict, "broken": broken, "outcome": "broken" if broken else "pass", "call": call["id"]}
    except Exception as exc:  # noqa: BLE001 - the check never stops the write
        LOG.warning("Rule check failed: %s", type(exc).__name__)
        if verdict is not None:
            return {**verdict, "outcome": "unchecked", "error": "error"}
        return None


def preflight(store, primary, who, operation, output, recipients=(), context=None, obj="", draft=False, skip=(),
              place=None):
    """The stable entry for a write one identity makes (core's `threads.post` calls it): `check_as` for the principal,
    sender and route `checked_as` reads from `who`. `place`: the write's place, or a function of (c, principal,
    recipients) (`place_for`). Returns the verdict `apply` reads, or None when nothing is checked."""
    found = checked_as(who, store.settings.assistant_bot)
    if not found:
        return None
    return check_as(store, primary, *found, operation, output, recipients, context=context, obj=obj, draft=draft,
                    skip=skip, place=place, attempt_id=getattr(who, "attempt_id", "") or "")


def inbound(store, primary, principal, operation, output, recipients=(), *, source, authenticated="", origin="",
            place=None, context=None, obj=""):
    """The check for a message that came in from outside this server: a Slack message, or one from another Tico. The
    bridge and federation builders call it. `principal` is who the message is held as: a roster person or bot, or the
    outside sender's actor. With no principal it is held as `source:origin`, which only Who everyone and With rules
    name, so no message skips the check. `authenticated` is the bridge's own actor (default: the principal), `source`
    the route ("slack", "federation") and `origin` the outside id. The verdict and any hold keep all of them. A message
    held here needs its own replayer (`replayer`), registered by the bridge, or Send anyway refuses it."""
    held_as = principal or f"{source}:{origin}"
    return check_as(store, primary, held_as, authenticated or held_as, source, operation, output, recipients,
                    context=context, obj=obj, place=place, origin={"source": str(source), "origin": str(origin or "")})


def holds(verdict):
    """Whether a verdict will hold the write (so a caller skips ask ingest's own call: a block skips it)."""
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
    return c.execute("SELECT count(*) FROM events INDEXED BY events_task_origin WHERE actor=? AND action IN "
                     "('rules.blocked','rules.escalated') AND target=? AND ts>=?",
                     (author, "org_rule:" + rule_id, _day())).fetchone()[0]


def _tell_bot(c, author, text):
    """A note on the bot's next run, as the keeper (a plain row: a paused bot's note waits, nothing is refused)."""
    if not H.is_bot(author):
        return
    row = {"id": H.new_id(), "from_actor": H.KEEPER, "to_actor": author, "body": text[:H.NOTE_MAX], "created": H.now()}
    c.execute("INSERT INTO notes(id,from_actor,to_actor,body,created) VALUES (:id,:from_actor,:to_actor,:body,:created)",
              row)
    H.event(c, H.KEEPER, "note.create", row["id"], {"to": author, "org_rule": True})


def _author_name(c, author):
    """The name a held write's author is called by: a person's, or a bot's display name."""
    if H.is_human(author):
        return H.person_name(c, H.actor_id(author)) or H.actor_id(author)
    return (H.bot(c, H.actor_id(author)) or {}).get("display_name") or H.actor_id(author)


def _review_task(c, hold_id, author, rule, reviewer, appeal=False):
    title = ("Appeal: " if appeal else "Held: ") + f"{_author_name(c, author)} and the rule '{rule['text'][:80]}'"
    body = ("A message or output was held by an org rule. Open the hold to Keep blocked, Send anyway or Edit rule. "
            "Nothing from the output is copied here.\n\nHold: " + hold_id)
    task = H.task_create(c, H.KEEPER, title[:200], body, reviewer, labels=[REVIEW_LABEL], lint=False,
                         deduplicate=False, quiet_notice=True)
    return task["id"]


def appeal_line(c, hold_id, reviewer, author=""):
    """How a bot's held output is appealed (its own `hub rules appeal`). A person's hold has no such line: they ask the
    reviewer themselves."""
    if author and H.is_human(author):
        return ""
    name = H.person_name(c, H.actor_id(reviewer)) if reviewer else ""
    return f"Think it's wrong? Ask {name or 'your manager'}: `hub rules appeal {hold_id}`"


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
    return {"author": author, "sender": author, "via": "", "operation": operation, "recipients": recipients,
            "rules": rules, "digest": "", "broken": [], "outcome": "unchecked", "error": "changed", "call": None,
            "cached": False, "draft": False, "matched": sorted((r["id"], r["version"]) for r in rules), "place": place,
            "origin": None, "attempt_id": "", "context": {}, "obj": ""}


def _people(recipients, author):
    """The recipients as a check names them: sorted, without blanks and without the author."""
    return sorted({r for r in recipients or () if r and r != author})


def apply(c, verdict, payload=None, replay=None, tell=False, obj_now=None, audience_now=None):
    """The verdict inside the write. Returns None to deliver as is; {"warn": line, ...} to deliver with the warning
    line on the recipient's copy; {"held": view} to write nothing and answer with the hold. `payload` is the text the
    reviewer reads, `replay` what Send anyway needs to run the normal write. `tell` also leaves the bot a note (a
    write the bot does not see answered: a completion reply, an automation). `obj_now`: the object as the write reads
    it (`current_obj`). Moved since the check, a verdict that holds still holds (a version bump never releases a
    Block); any other comes back as {"moved": True}, and the caller does not write: its next try checks again.
    `audience_now`: the recipients the write reads now (the route's audience, read inside the write). A hold is only made
    for the audience the check named, so a different one comes back as {"moved": True} as well."""
    if verdict and "applied" in verdict:
        return verdict["applied"]
    if not verdict or verdict.get("draft"):
        return None
    if obj_now is not None and verdict.get("obj") and obj_now != verdict["obj"] and not holds(verdict):
        return {"moved": True}
    author, operation = verdict["author"], verdict["operation"]
    if verdict["outcome"] in ("broken", "too_large"):
        if audience_now is not None and _people(audience_now, author) != list(verdict["recipients"]):
            return {"moved": True}
        # The rules in force now must be the ones checked: a rule edited, retired or added since is a new check, which
        # cannot run here (no engine under the write lock), so the output goes through unchecked.
        now = {(r["id"], r["version"])
               for r in R.matching(c, author, verdict["recipients"], verdict.get("place"))}
        checked = verdict.get("matched")
        checked = {tuple(x) for x in checked} if checked is not None else \
            {(r["id"], r["version"]) for r in verdict["rules"]}
        if now != checked:
            verdict = {**verdict, "outcome": "unchecked", "error": "rules_changed"}
    if verdict["outcome"] == "unchecked" or verdict.get("unchecked_rules"):
        H.event(c, author, "rules.unchecked", operation, {
            "operation": operation, "reason": verdict.get("error") or "unchecked",
            "rules": verdict.get("unchecked_rules") or [r["id"] for r in verdict["rules"]],
            **({"call": verdict["call"]} if verdict.get("call") else {})})
    if verdict["outcome"] not in ("broken", "too_large"):
        return None
    broken = [r for r in verdict["rules"] if r["id"] in verdict["broken"]]
    if not broken:
        return None
    too_large = verdict["outcome"] == "too_large"
    strictest = max(broken, key=lambda r: STRICT[r["if_broken"]])
    mode = strictest["if_broken"]
    if too_large and mode == "warn":
        # Every rule it meets is Warn: delivered, its line saying it went unchecked. A Block or Escalate rule holds it.
        H.event(c, author, "rules.unchecked", operation, {
            "operation": operation, "reason": "too_large", "rules": [r["id"] for r in broken]})
        line = (f"Warning: {_author_name(c, author)}'s message was too long to check against the rule "
                f"'{strictest['text']}'.")
        told = f"Your {OPERATIONS.get(operation, operation)} was delivered with a warning: it is too long to check " \
               f"against the rule '{strictest['text']}'."
        if tell:
            _tell_bot(c, author, told)
        return {"warn": line, "warned": {"rules": [{"id": r["id"], "text": r["text"]} for r in broken],
                                         "text": told, "too_large": True}}
    repeat = False
    if mode == "block" and _repeats(c, author, strictest["id"]) >= REPEAT_BLOCKS - 1:
        mode, repeat = "escalate", True
    if mode == "warn":
        line = f"Warning: {_author_name(c, author)}'s message may break the rule '{strictest['text']}'."
        H.event(c, author, "rules.warned", "org_rule:" + strictest["id"], {
            "operation": operation, "rules": [r["id"] for r in broken], "cached": verdict["cached"]})
        told = f"Your {OPERATIONS.get(operation, operation)} was delivered with a warning: it may break the rule " \
               f"'{strictest['text']}'."
        if tell:
            _tell_bot(c, author, told)
        return {"warn": line, "warned": {"rules": [{"id": r["id"], "text": r["text"]} for r in broken],
                                         "text": told}}
    reviewer = reviewer_for(c, author, broken)
    if mode == "escalate" and not reviewer:
        mode = "block"          # nobody to file a review task for: a Block the owner or an Admin can still release
    hold_id, nonce = H.new_id(), secrets.token_urlsafe(24)
    task_id = None
    if mode == "escalate":
        if repeat:
            row = c.execute("SELECT task_id FROM org_rule_holds WHERE author=? AND created>=? AND task_id IS NOT NULL "
                            "AND json_extract(verdict_json,'$.rule')=? AND json_extract(verdict_json,'$.repeat')=1 "
                            "ORDER BY created LIMIT 1", (author, _day(), strictest["id"])).fetchone()
            task_id = row[0] if row else None
        if task_id is None and reviewer:
            task_id = _review_task(c, hold_id, author, strictest, reviewer)
    text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str)
    # The run the output came from: Send anyway writes as the bot with it, so the run's private tasks and meetings are
    # checked against the destination again, as the original write was.
    # The check's own input (`input`) too: a rule changed since is checked on what this check was shown, and Send anyway
    # refuses once the object moved. `path` is the write's route, whose privacy guard Send anyway runs again.
    stored = {"text": text, "replay": replay,
              "provenance": {"attempt_id": verdict.get("attempt_id") or "", "path": PATH.get(),
                             "authenticated": verdict.get("sender") or author, "via": verdict.get("via") or "",
                             "place": verdict.get("place"), "origin": verdict.get("origin")},
              "input": {"context": verdict.get("context") or {}, "obj": verdict.get("obj") or ""}}
    matched = verdict.get("matched") or sorted((r["id"], r["version"]) for r in verdict["rules"])
    detail = {"rule": strictest["id"], "outcome": mode, "broken": [r["id"] for r in broken], "repeat": repeat,
              "too_large": too_large, "cached": verdict["cached"], "call": verdict.get("call"),
              "digest": verdict["digest"], "versions": {r["id"]: r["version"] for r in verdict["rules"]},
              "matched": [list(x) for x in matched]}
    c.execute("INSERT INTO org_rule_holds(id, operation, payload_json, author, destinations_json, rule_ids_json, "
              "verdict_json, nonce, state, reviewer, task_id, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              (hold_id, operation, json.dumps(stored, default=str), author, json.dumps(verdict["recipients"]),
               json.dumps([r["id"] for r in broken]), json.dumps(detail), nonce, "held", reviewer, task_id, H.now()))
    action = "rules.escalated" if mode == "escalate" else "rules.blocked"
    H.event(c, author, action, "org_rule:" + strictest["id"], {
        "hold": hold_id, "operation": operation, "rules": [r["id"] for r in broken], "cached": verdict["cached"],
        "reviewer": reviewer, **({"repeat": True} if repeat else {}), **({"too_large": True} if too_large else {}),
        **({"task": task_id} if task_id else {})})
    why = "it is too long to check against the rule" if too_large else "it broke the rule"
    told = (f"Your {OPERATIONS.get(operation, operation)} was held: {why} '{strictest['text']}'. "
            + ("It is waiting for review. " if mode == "escalate" else "")
            + appeal_line(c, hold_id, reviewer, author)).strip()
    if tell:
        _tell_bot(c, author, told)
    view = {"id": hold_id, "outcome": "escalated" if mode == "escalate" else "blocked",
            "rules": [{"id": r["id"], "text": r["text"]} for r in broken], "operation": operation,
            "reviewer": H.person_name(c, H.actor_id(reviewer)) if reviewer else None, "text": told,
            **({"too_large": True} if too_large else {})}
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
WARN_LINE = re.compile(r"Warning: [^\n]*'s message (?:may break|was too long to check against) the rule '[^\n]*'\.")


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


def _person_reviewer(c, who, hold, auth):
    """Whether a person may keep or send a person's held write: the reviewer it names, the author of a rule it broke, or
    anyone who may edit one of those rules (`R.may_manage`: a lead of a group in its Who, a bot's manager, the owner and
    the Admins)."""
    if hold["reviewer"] == who.actor:
        return True
    ctx = None
    for rid in json.loads(hold["rule_ids_json"] or "[]"):
        row = c.execute("SELECT * FROM org_rules WHERE id=?", (rid,)).fetchone()
        if not row:
            continue
        if row["suggested_by"] == who.actor:
            return True
        ctx = ctx or R._ctx(c)
        if R.may_manage(c, auth, who, R._row(row)["who"], ctx):
            return True
    return False


WITHDRAW_ROLES = ("author", "represented")   # the readers who may take a held write back (it stays blocked)


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
    or an Admin, reviews their own hold too. A bot's hold is reviewed by its reviewer, and by an Admin when the reviewer
    is missing or gone (with `c` and `auth`); a person's hold by the reviewer it names and the editors and Admins of its
    rules (`_person_reviewer`). An Assistant or BotOps acting for a person releases nothing."""
    actor = who.actor
    if not H.is_human(actor) or who.via in ("botops", "assistant"):
        return False
    if H.is_human(hold["author"]):
        return c is not None and auth is not None and _person_reviewer(c, who, hold, auth)
    if actor == hold["reviewer"]:
        return True
    return c is not None and auth is not None and auth.bot_admin(who) and \
        not (hold["reviewer"] and _present(c, hold["reviewer"]))


def _is_represented(who, hold, c=None):
    """Whether `who` is the person a bot's hold is held for (`represented`): they read it and may withdraw it, not
    release it. An Assistant or BotOps acting for them does neither."""
    return (c is not None and H.is_human(who.actor) and who.via not in ("botops", "assistant")
            and not H.is_human(hold["author"]) and who.actor == represented(c, hold["author"]))


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
        author, reviewer, stands_in = _is_author(who, hold), _is_reviewer(who, hold, c, auth), _is_represented(who, hold, c)
    if not (author or reviewer or stands_in):
        raise Problem("not_found", "There is no hold " + hold["id"], 404)
    stored = {} if summary else json.loads(hold["payload_json"] or "{}")
    verdict = json.loads(hold["verdict_json"] or "{}")
    rule_ids = json.loads(hold["rule_ids_json"] or "[]")
    destinations = json.loads(hold["destinations_json"] or "[]")
    if summary:
        # A list row is bounded whatever the hold names: a few short rule previews and names, and the counts.
        rule_ids, destinations = rule_ids[:SUMMARY_RULES], destinations[:SUMMARY_NAMES]
    rules = []
    for rid in rule_ids:
        row = c.execute("SELECT id, kind, text, version, status, if_broken FROM org_rules WHERE id=?", (rid,)).fetchone()
        if row:
            rules.append({**dict(row), "text": row["text"][:SUMMARY_CHARS]} if summary else dict(row))
    reviewer_name = H.person_name(c, H.actor_id(hold["reviewer"])) if hold["reviewer"] else None
    cut = (lambda v: v[:SUMMARY_CHARS] if summary and isinstance(v, str) else v)
    out = {"id": hold["id"], "operation": hold["operation"], "author": hold["author"],
           "author_name": cut(_author_name(c, hold["author"])), "state": hold["state"],
           "outcome": "escalated" if verdict.get("outcome") == "escalate" else "blocked", "rules": rules,
           "to": [cut(n) for n in _names(c, destinations)],
           "reviewer": hold["reviewer"], "reviewer_name": cut(reviewer_name), "task_id": hold["task_id"],
           "created": hold["created"], "decided_at": hold["decided_at"], "decided_by": hold["decided_by"],
           "can_send": reviewer and hold["state"] == "held" and hold["operation"] in REPLAY,
           "can_withdraw": (author or stands_in) and hold["state"] == "held"}
    if summary:
        out["appealed"] = bool(hold["why"])
        out["rules_count"] = len(json.loads(hold["rule_ids_json"] or "[]"))
        out["to_count"] = len(json.loads(hold["destinations_json"] or "[]"))
        return out
    text = stored.get("text") or ""
    out["text"] = text[:HOLD_TEXT_CHARS]
    if len(text) > HOLD_TEXT_CHARS:
        out["text_cut"] = len(text)
    if reviewer:
        out["why"] = hold["why"]
        if hold["state"] == "held":
            out["nonce"] = hold["nonce"]
    return out


def hold_view(c, who, hold_id, auth=None):
    """A held write's hold as `who` reads it: its text and its actions (Edit and resend, Send anyway with its nonce when
    `who` may send it too). The answer of a held write carries this, not apply's bare view of the reason."""
    return view_hold(c, who, _hold(c, hold_id), auth=auth)


def held_answer(c, who, gate, auth=None):
    """A held write's answer as its sender reads it: the hold as `who` reads it (`hold_view`: its text, its actions, the
    nonce when `who` may send it), the reason the sender was told (`reason`), and `too_large` when the check could not
    read the whole text."""
    told = gate["held"]
    return {"held": {**hold_view(c, who, gate["hold_id"], auth=auth), "reason": told["text"],
                     **({"too_large": True} if told.get("too_large") else {})}}


def _reader_sql(who):
    """`_reader` as SQL, so a page counts only holds this caller may read: a bot's own (author), or, for a person, every
    row, each settled by `_reader` (`fallback`), which needs the database and the caller's permissions."""
    if who.role == "bot" and not who.via:
        return "author=?", (who.actor,), False
    if H.is_human(who.actor):
        return "1", (), True
    return None, (), False


def held_for(c, who, operations):
    """The held writes of these operations that this person reads as their reviewer, as their author, or as the person
    it is held for, oldest first, each with its `role`. The owner's and Admins' fallback is not listed: a hold with no reviewer is for
    their review page, not a person's list."""
    marks = ",".join("?" * len(operations))
    rows = c.execute(f"SELECT id, author, reviewer, created FROM org_rule_holds WHERE state='held' AND operation IN "
                     f"({marks}) ORDER BY created, id", tuple(operations)).fetchall()
    out = []
    for row in rows:
        role = _reader(who, dict(row), c)
        if role in ("reviewer", "represented", "author"):     # author: a person's own held post
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
    query = c.execute(f"SELECT * FROM org_rule_holds WHERE {where} AND (created>? OR (created=? AND id>?)) "
                      "ORDER BY created, id", (*args, after[0], after[0], after[1]))
    rows = []
    for row in query:                       # one row past the page says there is more
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
    return out, (f"{last['created']}|{last['id']}" if more and last else None)


def holds_listed(c, who, task_id="", thread="", cursor=None, limit=HOLD_LIST_MAX, auth=None):
    """The holds one caller may read, in one of three lists: a review task's (any state), a thread's held writes, or
    every held write the caller may read. Summaries, paged (`_page`)."""
    where, args, fallback = _reader_sql(who)
    if not where:
        return [], None
    if task_id:
        scope, scope_args = "task_id=?", (task_id,)
    elif thread:
        scope = "state='held' AND json_extract(payload_json,'$.provenance.place.thread')=?"
        scope_args = (thread,)
    else:
        scope, scope_args = "state='held'", ()
    return _page(c, who, f"{scope} AND {where}", (*scope_args, *args), cursor, limit, auth, fallback)


def holds_for_task(c, who, task_id, cursor=None, limit=HOLD_LIST_MAX, auth=None):
    """One page of a review task's holds the caller may read (`holds_listed`)."""
    return holds_listed(c, who, task_id=task_id, cursor=cursor, limit=limit, auth=auth)


def _finish_task(c, hold):
    """The review task closes once none of its holds waits."""
    tid = hold["task_id"]
    if not tid:
        return
    left = c.execute("SELECT 1 FROM org_rule_holds WHERE task_id=? AND state='held' LIMIT 1", (tid,)).fetchone()
    task = H.task(c, tid)
    if not left and task and task["status"] not in ("done", "closed"):
        H.task_close(c, H.KEEPER, tid, "Every hold on this task is decided.", quiet=True)


def _decide(c, who, hold, state, event=""):
    c.execute("UPDATE org_rule_holds SET state=?, decided_at=?, decided_by=?, nonce=? WHERE id=? AND state='held'",
              (state, H.now(), who.actor, "used:" + secrets.token_hex(8), hold["id"]))
    from . import audit_log
    audit_log.write(c, who.actor, "org_rule_hold:" + hold["id"], {"state": "held"}, {"state": state},
                    who.via or H.VIA.get() or "api")
    H.event(c, who.actor, event or "org_rule_hold." + state, "org_rule_hold:" + hold["id"],
            {"operation": hold["operation"], "author": hold["author"]})
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
    _tell_bot(c, hold["author"], f"Your held {OPERATIONS.get(hold['operation'], hold['operation'])} stays blocked "
              f"(hold {hold_id}).")
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
        _tell_bot(c, hold["author"], f"Your held {OPERATIONS.get(hold['operation'], hold['operation'])} was withdrawn "
                  f"and stays blocked (hold {hold_id}).")
    return view_hold(c, who, _hold(c, hold_id), auth=auth)


def hold_provenance(hold):
    """What a hold keeps of its check: the attempt, the route, the actor that sent it (`authenticated`) and its `via`,
    the place, the outside origin, and the write's route (`path`)."""
    return json.loads(hold["payload_json"] or "{}").get("provenance") or {}


def replay_identity(c, hold, auth):
    """The identity a held write runs as on Send anyway: the person it was held for, rebuilt with their own permissions
    (`auth.identity_for_actor`, refused once they are off the team), or for a bot's hold the bot, with the run it came
    from (its private tasks and meetings)."""
    if H.is_human(hold["author"]):
        return auth.identity_for_actor(c, hold["author"])
    from .auth import Identity
    return Identity(hold["author"], "bot", agent="org-rule-hold",
                    attempt_id=hold_provenance(hold).get("attempt_id") or "")


def hold_matched(hold):
    """The (rule id, version) pairs that matched when the output was held."""
    verdict = json.loads(hold["verdict_json"] or "{}")
    if verdict.get("matched") is not None:
        return {tuple(x) for x in verdict["matched"]}
    return {(rid, v) for rid, v in (verdict.get("versions") or {}).items()}


def _replay(hold):
    return json.loads(hold["payload_json"] or "{}").get("replay") or {}


def current_people(c, hold, audience=None, auth=None):
    """Who the held write reaches now: its audience as the route counts it, or the destinations it was held for when its
    operation names no way to count them."""
    now = current_audience(c, hold, audience, auth)
    return now if now is not None else json.loads(hold["destinations_json"] or "[]")


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
        return place_for_start(c, hold["author"], people, audience=audience,
                               audience_group_id=start.get("audience_group_id"),
                               members=start.get("dm") or start.get("members") or ())
    return place_for(c, hold["author"], people, stored.get("thread") or "")


def current_matched(c, hold, audience=None, auth=None):
    """The (rule id, version) pairs that match the held write now: its people and place as they are now."""
    people = current_people(c, hold, audience, auth)
    place = place_now(c, hold, people)
    return {(r["id"], r["version"]) for r in R.matching(c, hold["author"], people, place)}


def hold_input(hold):
    """What a hold's check was shown besides its text and audience: its context and its object."""
    stored = json.loads(hold["payload_json"] or "{}")
    return stored.get("input") or {"context": {}, "obj": ""}


def _unchanged(c, hold):
    """Refuse Send anyway once the object the output was checked against changed (a task edited since): its new
    content was never checked. The bot or a person writes it again, and that write is checked fresh."""
    obj = hold_input(hold).get("obj") or ""
    if obj and current_obj(c, obj) != obj:
        raise Problem("changed", "What this was about changed since it was held; it stays held. Write it again to "
                      "check it.", 409)


def current_audience(c, hold, audience=None, auth=None):
    """Who the held write would reach now, as its check counts them, or None when its operation names no way to tell
    (a mail, addressed by the bot's own tool) or its replay would be refused anyway (the replayer's own checks)."""
    fn = (audience if audience is not None else AUDIENCE).get(hold["operation"])
    if fn is None:
        return None
    stored = json.loads(hold["payload_json"] or "{}")
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
    if now is not None and now != sorted(json.loads(hold["destinations_json"] or "[]")):
        raise Problem("changed", "Who this goes to changed since it was held; it stays held. Write it again to "
                      "check it.", 409)
    stored = hold_provenance(hold).get("place")
    if stored and place_now(c, hold, current_people(c, hold, audience, auth)) != stored:
        raise Problem("changed", "Where this goes changed since it was held; it stays held. Write it again to check "
                      "it.", 409)


def recheck(store, primary, hold, audience=None, auth=None):
    """Outside the write, before Send anyway or an approved mail: when the rules that match now differ from those at the
    hold (a rule edited or added since), the rules changed or added are a new check, on the same input the first check
    had (text, audience, context, object, place). Returns (the pairs matching now, that verdict or None). The write
    checks the pairs again and refuses on a change in between. A changed audience refuses first (`_same_audience`)."""
    with store.read() as c:
        _same_audience(c, hold, audience, auth)
        people = current_people(c, hold, audience, auth)
        place = place_now(c, hold, people)
        now = {(r["id"], r["version"]) for r in R.matching(c, hold["author"], people, place)}
    if now <= hold_matched(hold):
        return sorted(now), None
    stored = json.loads(hold["payload_json"] or "{}")
    shown, prov = hold_input(hold), hold_provenance(hold)
    verdict = check_as(store, primary, hold["author"], prov.get("authenticated") or hold["author"],
                       prov.get("via") or "", hold["operation"], stored.get("text") or "", people,
                       context=shown.get("context") or None, obj=shown.get("obj") or "", skip=hold_matched(hold),
                       place=place, attempt_id=prov.get("attempt_id") or "", origin=prov.get("origin"))
    return sorted(now), verdict


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
        broken = [r for r in verdict["rules"] if r["id"] in verdict["broken"]] or verdict["rules"]
        raise Problem("rule_changed", f"It now breaks the rule '{broken[0]['text']}', changed since it was held. It "
                      "stays held: edit that rule, or keep it blocked.", 409)
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
    stored = json.loads(hold["payload_json"] or "{}")
    if "path" not in (stored.get("provenance") or {}):
        raise Problem("recheck", "This hold is from an older version and can't be sent as it is; it stays held. "
                      "Write it again to check it.", 409)
    _unchanged(c, hold)
    _same_audience(c, hold, audience, auth)
    gate = _rechecked(c, hold, check, audience, auth)
    bot = replay_identity(c, hold, auth)
    path = stored["provenance"]["path"] or ""
    if auth is not None and path:
        from . import task_privacy
        task_privacy.guard_write(c, bot, path, auth)
    _decide(c, who, hold, "sent")           # the nonce is spent first: a retry of this request cannot send twice
    result = fn(c, bot, hold, stored.get("replay") or {}, ingest, gate)
    return {"hold": view_hold(c, who, _hold(c, hold_id), "reviewer"), "result": result}


def appeal(c, who, hold_id, why):
    """The author bot sends its hold to the reviewer as a review task. `why` stays on the hold, for the reviewer."""
    hold = _hold(c, hold_id)
    if not _is_author(who, hold):
        raise Problem("not_found", "There is no hold " + hold_id, 404)
    if hold["state"] != "held":
        raise Problem("conflict", "This hold is already decided", 409)
    why = str(why or "").strip()[:2000]
    task_id = hold["task_id"]
    if not hold["reviewer"]:
        raise Problem("no_reviewer", "Nobody on the team can review this hold", 409)
    if not task_id:
        rid = (json.loads(hold["verdict_json"] or "{}")).get("rule")
        rule = c.execute("SELECT id, text FROM org_rules WHERE id=?", (rid,)).fetchone()
        task_id = _review_task(c, hold_id, hold["author"], dict(rule) if rule else {"text": "a rule"},
                               hold["reviewer"], appeal=True)
    c.execute("UPDATE org_rule_holds SET why=?, task_id=? WHERE id=?", (why, task_id, hold_id))
    H.event(c, who.actor, "org_rule_hold.appealed", "org_rule_hold:" + hold_id, {"task": task_id})
    return {"id": hold_id, "task_id": task_id, "reviewer": H.person_name(c, H.actor_id(hold["reviewer"]))}


# ----------------------------------------------------------------------------- mail
def mail_digest(to, cc, subject, body):
    raw = json.dumps([sorted(a.lower() for a in to), sorted(a.lower() for a in cc), subject, body],
                     separators=(",", ":"))
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


def mail_approved(c, author, key):
    """A held mail the reviewer chose to send anyway, not yet sent: the next identical check passes it, once."""
    row = c.execute("SELECT * FROM org_rule_holds WHERE author=? AND operation='mail' AND state='sent' "
                    "AND json_extract(payload_json,'$.replay.mail_digest')=? "
                    "AND json_extract(verdict_json,'$.used') IS NULL ORDER BY decided_at LIMIT 1",
                    (author, key)).fetchone()
    return dict(row) if row else None


def claim_mail(c, hold_id):
    """Spend a mail approval inside the write: only one request ever changes the row."""
    return c.execute("UPDATE org_rule_holds SET verdict_json=json_set(verdict_json,'$.used',1) WHERE id=? "
                     "AND json_extract(verdict_json,'$.used') IS NULL", (hold_id,)).rowcount == 1


def mail_warning_line(gate):
    """The line a warned mail carries at the top of its body, for the recipient."""
    return warn_line(gate)


def open_mail_hold(c, author, key):
    """The hold already waiting on this exact mail, if any: a draft sent again while it waits is that hold, not a new
    one (one review task per mail)."""
    row = c.execute("SELECT * FROM org_rule_holds WHERE author=? AND operation='mail' AND state='held' "
                    "AND json_extract(payload_json,'$.replay.mail_digest')=? ORDER BY created DESC LIMIT 1",
                    (author, key)).fetchone()
    return dict(row) if row else None


def held_again(c, hold, verdict=None):
    """The answer for a mail whose hold already waits: the same hold. `verdict` (this attempt's, which holds): the
    attempt counts as a block of the hold's rule toward the repeats rule, and the third escalates that same hold."""
    if verdict is not None:
        hold = _blocked_again(c, hold, verdict)
    verdict = json.loads(hold["verdict_json"] or "{}")
    rules = []
    for rid in json.loads(hold["rule_ids_json"] or "[]")[:SUMMARY_RULES]:
        row = c.execute("SELECT id, text FROM org_rules WHERE id=?", (rid,)).fetchone()
        if row:
            rules.append(dict(row))
    return {"id": hold["id"], "outcome": "escalated" if verdict.get("outcome") == "escalate" else "blocked",
            "rules": rules, "operation": "mail", "text": "This email is still held. "
            + appeal_line(c, hold["id"], hold["reviewer"])}


def _blocked_again(c, hold, verdict):
    """One more blocked attempt at a held mail: recorded as a block of its rule; the bot's third block of that rule
    today escalates the hold (a review task, the day's repeat task reused). The hold row comes back as it is now."""
    detail = json.loads(hold["verdict_json"] or "{}")
    rid, author = detail.get("rule"), hold["author"]
    if detail.get("outcome") != "block" or not rid:
        return hold
    rules = json.loads(hold["rule_ids_json"] or "[]")
    if _repeats(c, author, rid) < REPEAT_BLOCKS - 1:
        H.event(c, author, "rules.blocked", "org_rule:" + rid, {
            "hold": hold["id"], "operation": "mail", "rules": rules, "cached": bool(verdict.get("cached")),
            "reviewer": hold["reviewer"], "again": True})
        return hold
    row = c.execute("SELECT task_id FROM org_rule_holds WHERE author=? AND created>=? AND task_id IS NOT NULL "
                    "AND json_extract(verdict_json,'$.rule')=? AND json_extract(verdict_json,'$.repeat')=1 "
                    "ORDER BY created LIMIT 1", (author, _day(), rid)).fetchone()
    task_id = hold["task_id"] or (row[0] if row else None)
    if task_id is None and hold["reviewer"]:
        rule = c.execute("SELECT id, text FROM org_rules WHERE id=?", (rid,)).fetchone()
        task_id = _review_task(c, hold["id"], author, dict(rule) if rule else {"text": "a rule"}, hold["reviewer"])
    c.execute("UPDATE org_rule_holds SET verdict_json=json_set(verdict_json,'$.outcome','escalate','$.repeat',1), "
              "task_id=? WHERE id=?", (task_id, hold["id"]))
    H.event(c, author, "rules.escalated", "org_rule:" + rid, {
        "hold": hold["id"], "operation": "mail", "rules": rules, "cached": bool(verdict.get("cached")),
        "reviewer": hold["reviewer"], "repeat": True, **({"task": task_id} if task_id else {})})
    return _hold(c, hold["id"])


def supersede_mail(c, hold_id):
    """An approval a newer rule held again is spent: the new hold's Send anyway is the approval that counts."""
    c.execute("UPDATE org_rule_holds SET verdict_json=json_set(verdict_json,'$.used','superseded') WHERE id=? "
              "AND json_extract(verdict_json,'$.used') IS NULL", (hold_id,))


@replayer("mail")
def _replay_mail(c, bot, hold, replay, ingest, gate=None):
    """A mail is sent by its bot's own mail tool, not the server: Send anyway lets that exact mail pass its next check,
    once, and tells the bot to send it again."""
    _tell_bot(c, hold["author"], "The email you were told was held may go now: send that exact draft again "
              f"(hold {hold['id']}).")
    return {"approved": True}


THREAD_REPLAY = ("kind", "refs", "parent_message_id", "to_actor", "via", "origin", "attachments", "in_reply_to", "start")


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
    to = replay.get("to_actor")
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
    message = T.post(c, bot, replay.get("thread_id"), replay["body"], checked=None, **args)
    return {"id": (message or {}).get("id")}


# ----------------------------------------------------------------------------- stats and health
def stats(c, settings):
    day = _day()
    label = J.load_set(QUESTION_SET)["label"]
    checks = c.execute("SELECT count(*) FROM events INDEXED BY events_task_origin WHERE actor=? AND action='judge.call' "
                       "AND target=? AND ts>=?", (H.KEEPER, label, day)).fetchone()[0]

    def count(*actions):
        return c.execute(f"SELECT count(*) FROM events WHERE action IN ({','.join('?' * len(actions))}) AND ts>=?",
                         (*actions, day)).fetchone()[0]
    hits = count("rules.blocked", "rules.escalated", "rules.warned")
    cap = float(settings.rules_monthly_usd or 0)
    left = None if cap <= 0 else round(max(0.0, cap - B.spent_this_month(c, None, BUDGET)), 2)
    # What the people's checks cost this month, out of the same budget: the judge.call rows carry each call's cost.
    month = H.now()[:7] + "-01T00:00:00"
    people = c.execute("SELECT coalesce(sum(json_extract(detail_json,'$.cost_usd')),0) FROM events WHERE actor=? AND "
                       "action='judge.call' AND target LIKE ? AND ts>=? AND json_extract(detail_json,'$.people')=1",
                       (H.KEEPER, RULES_TARGET, month)).fetchone()[0]
    return {"checks_today": checks, "unchecked_today": count("rules.unchecked"),
            "blocked_today": count("rules.blocked", "rules.escalated"), "hits_today": hits,
            "usd_left": left, "people_usd": round(float(people or 0), 2), "on": on(c)}


def health_findings(c, settings):
    """A high share of unchecked outputs in a day: the rules are not being enforced (backend/health_monitor.py)."""
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
    return [{"key": "org_rules_unchecked", "check_id": "org_rules_unchecked",
             "title": f"{share}% of rule checks went unchecked today",
             "detail": f"{s['unchecked_today']} of {total} bot outputs that rules apply to went out unchecked today "
                       "(no decision model, the rules budget, the day's call cap, the deadline or an error).",
             "fix": "Check the Tico AI Gateway key and the rules budget (TICO_RULES_MONTHLY_USD); the Rules page "
                    "header shows what is left."}]


# ----------------------------------------------------------------------------- HTTP
MISS_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def keep_miss(c, author, check_id):
    """The mail check `check_id` (its Idempotency-Key) is counted unchecked for this bot: a later report of the same
    id counts nothing. Inside the write that counted it."""
    if check_id:
        return c.execute("INSERT OR IGNORE INTO org_rule_misses(author, id, created) VALUES(?,?,?)",
                         (author, check_id, H.now())).rowcount == 1
    return False


def check_id_of(request):
    """A mail check's id: its Idempotency-Key, when the mail tool made it a UUID (connectors/mail)."""
    key = str(request.headers.get("idempotency-key") or "").strip().lower()
    return key if MISS_ID.fullmatch(key) else ""


def record_misses(c, author, ids):
    """Inside the write: each id not yet counted for this bot (by the check itself, or an earlier report) is claimed
    and is one `rules.unchecked`, when a rule in force covers the bot whatever the recipients (Who names it, With
    empty: an id says nothing of whom the mail went to), up to MISSES_DAY_MAX a UTC day. An id not counted is not
    kept. Every id is taken (the connector forgets them on the 2xx). Ids older than MISSES_KEPT_DAYS are forgotten."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MISSES_KEPT_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    c.execute("DELETE FROM org_rule_misses WHERE created<?", (cutoff,))
    counted = on(c) and bool(R.matching(c, author))
    room = MISSES_DAY_MAX - c.execute(
        "SELECT count(*) FROM events WHERE action='rules.unchecked' AND actor=? AND ts>=? "
        "AND json_extract(detail_json,'$.reason')='connector'", (author, _day())).fetchone()[0] if counted else 0
    known, recorded, over = [], 0, 0
    for miss in ids:
        if counted and recorded < room:
            # Claimed before counted: the check itself, still running, then finds it claimed and counts nothing.
            if keep_miss(c, author, miss):
                H.event(c, author, "rules.unchecked", "mail", {"operation": "mail", "reason": "connector",
                                                              "bot": H.actor_id(author)})
                recorded += 1
            else:
                known.append(miss)
            continue
        # Not counted here (no rule without With, or over the day's cap): not kept either, so the check, if it is
        # still running, can count it.
        if c.execute("SELECT 1 FROM org_rule_misses WHERE author=? AND id=?", (author, miss)).fetchone():
            known.append(miss)
        elif counted:
            over += 1
    if over:
        LOG.warning("%s reported %s more unchecked mails than a day counts (%s)", author, over, MISSES_DAY_MAX)
    return {"recorded": recorded, "known": known}


def install(app, store, auth, mutate):
    from fastapi import Request
    from pydantic import Field
    from typing import Literal
    from .models import Contract
    # This app's writes, as registered while it was built (one server builds one app; tests build many).
    table, prepare, audience = dict(REPLAY), dict(PREPARE), dict(AUDIENCE)

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

    class MailMisses(Contract):
        ids: list[str] = Field(min_length=1, max_length=MISSES_MAX)

    def mail_unchecked(author, bot, body, reason, check_id=""):
        """A mail check that could not run (refused, or the server failed): the connector sends the mail unchecked, so
        it counts as unchecked here, for the Rules header and the Health finding, when a rule applies to that bot."""
        try:
            with store.transaction() as c:
                if not R.any_active(c) or not on(c):
                    return
                # The rule is read for whose mail it is: the person's, when a person's mail is sent, else the bot's. The
                # event keeps the caller as its actor, as before.
                subject = author if H.is_human(author) else "bot:" + bot
                rules = R.matching(c, subject, mail_recipients(c, [*body.to, *body.cc]))
                # Claimed before counted: a report of this check that landed first already counted it.
                if rules and (not check_id or keep_miss(c, author, check_id)):
                    H.event(c, author, "rules.unchecked", "mail", {"operation": "mail", "reason": reason,
                                                                  "rules": [r["id"] for r in rules], "bot": bot})
        except Exception as exc:  # noqa: BLE001 - recording a failure never adds one
            LOG.warning("Recording a failed mail check failed: %s", type(exc).__name__)

    @app.post("/api/v2/bots/{bot}/mail-check/misses")
    def mail_check_misses(request: Request, bot: str, body: MailMisses):
        """Mails the bot's mail tool sent after a check that brought back no verdict (any failure), reported once Tico
        answers again by each check's id, its Idempotency-Key: one `rules.unchecked` each (reason `connector`,
        `record_misses`). An id the check itself already counted, or an earlier report, counts nothing (`known`).
        A person's own token reports nothing: a person's mail is not sent through the bot's mail tool."""
        who = request.state.identity
        found = checked_as(who, store.settings.assistant_bot)
        if not found or not H.is_bot(found[1]):
            return {"recorded": 0, "known": []}
        author, sender = found[0], found[1]
        if sender != "bot:" + bot:
            raise Problem("forbidden", "A bot reports only its own mail", 403)
        ids = list(dict.fromkeys(body.ids))
        if any(not MISS_ID.fullmatch(i) for i in ids):
            raise Problem("validation", "Each id is a UUID", 422)
        return mutate(request, body, lambda c: record_misses(c, author, ids))

    @app.post("/api/v2/bots/{bot}/mail-check")
    def mail_check(request: Request, bot: str, body: MailCheck):
        """The org rule check of one mail, asked by the bot's mail tool before it sends (connectors/mail): held, the
        mail stays a draft. Only the bot itself asks, and BotOps asks for a person it acts for, whose mail is checked as
        that person (`checked_as`). A person's own mail is not sent by the bot's tool and is not checked here
        (`not_checked`). Another bot's token is refused, and a refusal or a server error is recorded as unchecked: the
        connector sends on a failed check."""
        who = request.state.identity
        found = checked_as(who, store.settings.assistant_bot)
        if not found or not H.is_bot(found[1]):
            return {"outcome": "not_checked"}
        author, sender = found[0], found[1]
        check_id = check_id_of(request)
        if sender != "bot:" + bot:
            LOG.warning("A mail check for %s came with another bot's token", bot)
            mail_unchecked(author, bot, body, "forbidden", check_id)
            raise Problem("forbidden", "A bot checks only its own mail", 403)
        try:
            return mail_checked(request, who, author, body, check_id)
        except Problem as exc:
            if exc.status >= 500:
                mail_unchecked(author, bot, body, "error", check_id)
            raise
        except Exception:
            mail_unchecked(author, bot, body, "error", check_id)
            raise

    def mail_checked(request, who, author, body, check_id=""):
        """`author` is the teammate the mail is checked as (a person, when BotOps sends it for one): its holds and
        approvals are found under it; `who` stays the caller, for the write's permission and audit. A check counted
        unchecked keeps `check_id`, so the mail tool's later report of it (it never got this answer) counts nothing
        more."""
        key = mail_digest(body.to, body.cc, body.subject, body.body)
        output = (f"Subject: {body.subject}\n\n" if body.subject else "") + body.body
        with store.read() as c:
            if not in_force(c):
                return {"outcome": "pass"}      # no rule in force: no roster read for the recipients, nothing written
            approved = mail_approved(c, author, key)
            recipients = mail_recipients(c, [*body.to, *body.cc])
        # An approved mail (Send anyway) is not judged again on the rules it was held on; rules changed or added
        # since are a new check, as for any Send anyway. Otherwise the whole mail is checked.
        check = recheck(store, app.state.judge, approved) if approved else None
        verdict = check[1] if approved else \
            preflight(store, app.state.judge, who, "mail", output, recipients,
                      context={"to": [a.lower() for a in [*body.to, *body.cc]]})

        def outcome(gate):
            if gate and gate.get("held"):
                return {"outcome": "held", **answer(gate)}
            if gate and gate.get("warned"):
                return {"outcome": "warn", "warning_line": mail_warning_line(gate), **answer(gate)}
            return {"outcome": "unchecked" if verdict and verdict["outcome"] == "unchecked" else "pass"}

        def work(c):
            mark = c.execute("SELECT coalesce(max(rowid), 0) FROM events").fetchone()[0]
            out = decide(c)
            # The check claims its id with the unchecked it wrote; a report of it that landed first (the mail tool gave
            # up waiting) already counted it, and this write's own unchecked comes out again.
            if check_id and c.execute("SELECT 1 FROM events WHERE rowid>? AND action='rules.unchecked' AND actor=? "
                                      "LIMIT 1", (mark, author)).fetchone() and not keep_miss(c, author, check_id):
                c.execute("DELETE FROM events WHERE rowid>? AND action='rules.unchecked' AND actor=?", (mark, author))
            return out

        def decide(c):
            waiting = open_mail_hold(c, author, key) if holds(verdict) else None
            if approved:
                if current_matched(c, approved) != {tuple(x) for x in check[0]}:
                    return {"outcome": "held", "held": {"id": approved["id"], "text": "The rules changed while this "
                            "email was checked. Send it again to check it."}}
                if holds(verdict):
                    # A newer rule holds an approved mail: the old approval is spent, and the mail has one hold.
                    supersede_mail(c, approved["id"])
                    if waiting:
                        return {"outcome": "held", "held": held_again(c, waiting, verdict)}
                    return outcome(apply(c, verdict, output, {"mail_digest": key}))
                if not claim_mail(c, approved["id"]):
                    # Another send already used this approval: this one stays a draft.
                    return {"outcome": "held", "held": {"id": approved["id"], "text": "This email's approval was "
                            "already used. It stays a draft."}}
                gate = apply(c, verdict, output, {"mail_digest": key}) if verdict else None
                return {**outcome(gate), "outcome": "warn" if gate and gate.get("warned") else "pass",
                        "approved": approved["id"]}
            if waiting:
                return {"outcome": "held", "held": held_again(c, waiting, verdict)}
            return outcome(apply(c, verdict, output, {"mail_digest": key}))
        return mutate(request, body, work)

    @app.get("/api/v2/rules/stats")
    def rules_stats(request: Request):
        """The Rules page header: checks today, unchecked, blocked, dollars left in the rules budget, and on/off."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return stats(c, store.settings)

    @app.get("/api/v2/rule-holds")
    def rule_holds(request: Request, task: str | None = None, thread: str | None = None, cursor: str | None = None,
                   limit: int = HOLD_LIST_MAX):
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
            ready = _is_reviewer(who, hold, c, auth) and hold["state"] == "held" and \
                not store.saved(c, who, request.url.path, request.headers.get("idempotency-key"))
            if ready and hold["operation"] in prepare:
                ident = replay_identity(c, hold, auth)
        if ready:
            # Both outside the write: rules changed since the hold are a new check, and ask ingest runs as it would
            # have for the original write (the rules it was held on are not asked again).
            check = recheck(store, app.state.judge, hold, audience, auth)
            if ident is not None:
                stored = json.loads(hold["payload_json"] or "{}")
                try:
                    ingest = prepare[hold["operation"]](store, ident, stored.get("replay") or {})
                except Exception as exc:  # noqa: BLE001 - ask ingest's own preflight never stops the send
                    LOG.warning("Hold replay preflight failed: %s", type(exc).__name__)
        return mutate(request, body, lambda c: send(c, who, hold_id, body.nonce, ingest, table, check, auth, audience))

    @app.post("/api/v2/rule-holds/{hold_id}/appeal")
    def rule_hold_appeal(request: Request, hold_id: str, body: HoldAppeal):
        """The author bot asks the reviewer to look. Never checked by rules: it delivers nothing."""
        who = request.state.identity
        return mutate(request, body, lambda c: appeal(c, who, hold_id, body.why))

    @app.post("/api/v2/rules/check")
    def rules_check(request: Request, body: DraftCheck):
        """A bot checks a draft as the write it will be (`hub rules check`): the same input, and the verdict cached, so
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
            raise Problem("budget", f"{author} has checked {used} drafts today; the cap is {cap} until midnight UTC", 429)
        operation = DRAFT_AS[body.as_]
        verdict = preflight(store, app.state.judge, who, operation, body.text, [to], draft=True)
        with store.transaction() as c:
            H.event(c, author, "rules.draft", operation, {"operation": operation})
        if not verdict:
            return {"outcome": "pass", "rules": [], "cached": False}
        broken = [{"id": r["id"], "text": r["text"], "if_broken": r["if_broken"]}
                  for r in verdict["rules"] if r["id"] in verdict["broken"]]
        return {"outcome": verdict["outcome"], "rules": broken, "cached": verdict["cached"],
                **({"reason": verdict["error"]} if verdict.get("error") else {})}


__all__ = ["ACTIONS", "BUDGET", "OPERATIONS", "REPLAY", "answer", "apply", "author_of", "check_as", "checked_as",
           "digest", "ensure", "health_findings", "held_for", "held_note", "holds", "inbound", "install", "on", "place_for",
           "preflight", "replayer", "reviewer_for", "stats", "warned_text", "watched"]
