"""Org rule checks (docs/rules-plan.md §3): a bot's output is checked against the org rules that apply to it before it
is delivered (`questions/rule-check.json`, asked through the server's Decide engine on the rules' own budget).

Like ask ingest (backend/ask_ingest.py): one call per output, outside the write's transaction (`preflight`); plain
code then applies the verdict inside the write (`apply`). Nothing here calls an engine under `store.transaction()` or
`mutate`: `apply` only reads the verdict and writes rows.

- Whose writes: a bot's (the actor is a bot, BotOps acts for a bot, or BotOps acts as a person: `author_of`). A
  person's own writes, and their Assistant's, are never checked.
- Which rules: `org_rules.matching(c, author, recipients)`, the full audience of the write. No matching rule is no
  call, which is most outputs.
- What is judged: the whole output, short or long. An output whose state is over Decide's limit is held, not passed,
  when any rule it meets is Block or Escalate; when all are Warn it is delivered with a line saying it was too long to
  check, and a `rules.unchecked` event (reason `too_large`).
- If broken, per rule, strictest wins: Block holds it (a 200 `held` answer, never a refusal, so no quarantine) and
  the bot is told the rule and whom to appeal to; Escalate also files one review task for the reviewer; Warn
  delivers it with a warning line on the recipient's copy. The 3rd block of one rule by one bot in a UTC day
  escalates for the rest of that day, with one review task.
- Holds: the payload lives only in `org_rule_holds`, read by the author bot and the reviewer alone. Send anyway
  replays the normal write once, as the bot (`REPLAY`), with a one-shot nonce and no second Decide call.
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
from . import team_rules
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
}
DRAFT_AS = {"message": "message", "comment": "comment", "task": "task_create", "assign": "task_owner"}

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS org_rule_verdicts(digest TEXT PRIMARY KEY, broken TEXT NOT NULL, "
    "call_id TEXT, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS org_rule_verdicts_created ON org_rule_verdicts(created_at)",
    "CREATE INDEX IF NOT EXISTS org_rule_holds_task ON org_rule_holds(task_id) WHERE task_id IS NOT NULL",
    # Mails a bot's connector sent after a check that got no answer from Tico, by the id the connector gave each:
    # one `rules.unchecked` per id, however often it is reported (`record_misses`).
    "CREATE TABLE IF NOT EXISTS org_rule_misses(author TEXT NOT NULL, id TEXT NOT NULL, created_at TEXT NOT NULL, "
    "PRIMARY KEY (author, id))",
    "CREATE INDEX IF NOT EXISTS org_rule_misses_created ON org_rule_misses(created_at)",
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
def author_of(who, assistant_bot=""):
    """The bot a write is checked as, or None for a person's own write, their Assistant's included (`via` assistant:
    the Assistant acting as that person). The Assistant writing as itself is a bot like any other: Who all bots
    covers it, a group Who never does (it is in no group)."""
    if who is None or who.via == "assistant":
        return None
    if who.role == "bot" and str(who.actor).startswith("bot:"):
        return who.actor
    if who.via == "botops" and str(who.task_actor or "").startswith("bot:"):
        return who.task_actor                     # BotOps acting as a person: checked as BotOps
    if str(who.task_actor or "").startswith("bot:"):
        return who.task_actor
    return None


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
    """The ids of the bots `watched` is true for, read once: for a reader that filters many runs together (SQL)."""
    try:
        if not R.any_active(c) or not on(c):
            return []
        rules, ctx = R._active(c), R._ctx(c)
        return [row["id"] for row in H.bots(c)
                if any(R._hits(rule["who"], R.who_key(c, "bot:" + row["id"], ctx)) for rule in rules)]
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
        if json.loads(attempt["result"] or "{}").get("held_reply"):
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
             "text": r["text"]} for r in rules]


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


def calls_today(c):
    """Today's rule-check calls: their own daily cap, apart from ask ingest's and every other keeper call's."""
    return B.used_today(c, H.KEEPER, target=RULES_TARGET)


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


def preflight(store, primary, who, operation, output, recipients=(), context=None, obj="", draft=False, skip=()):
    """Check one output outside any transaction. Returns the verdict `apply` reads, or None when nothing is checked
    (a person's write, checks Off, no rule matches). Never raises: a failure is a verdict marked unchecked.
    `skip`: (rule id, version) pairs already decided (Send anyway asks only the rules changed or added since its
    hold); the verdict's `matched` is every pair that matched, to check again inside the write."""
    author = author_of(who, store.settings.assistant_bot)
    if not author:
        return None
    verdict = None
    try:
        recipients = sorted({r for r in recipients or () if r and r != author})
        with store.read() as c:
            if not in_force(c):
                return None             # no rule in force: nothing more is read, and the output is never serialized
            rules = _rule_rows(R.matching(c, author, recipients))
            matched = sorted((r["id"], r["version"]) for r in rules)
            skip = {tuple(x) for x in skip or ()}
            rules = [r for r in rules if (r["id"], r["version"]) not in skip]
            if not rules:
                return None
            output = output if isinstance(output, str) else json.dumps(output, sort_keys=True, default=str)
            level = store.settings.rule_check_level or "medium"
            qset = J.load_set(QUESTION_SET)
            key = digest(operation, author, recipients, output, obj, rules, qset["label"], level, context)
            verdict = {"author": author, "operation": operation, "recipients": recipients, "rules": rules,
                       "digest": key, "broken": [], "outcome": "pass", "error": None, "call": None, "cached": False,
                       "draft": draft, "matched": matched, "attempt_id": getattr(who, "attempt_id", "") or "",
                       "context": context or {}, "obj": obj or ""}
            hit = c.execute("SELECT broken, call_id FROM org_rule_verdicts WHERE digest=?", (key,)).fetchone()
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
            if calls_today(c) >= B.daily_cap(store.settings, "bot"):
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
                  **({"draft": True} if draft else {})}
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
                c.execute("INSERT OR REPLACE INTO org_rule_verdicts(digest, broken, call_id, created_at) "
                          "VALUES(?,?,?,?)", (key, json.dumps(stored), call["id"], H.now()))
                c.execute("DELETE FROM org_rule_verdicts WHERE created_at<?", (H.shift(H.now(), days=-CACHE_DAYS),))
        if len(rules) > J.MAX_QUESTIONS and broken is not None:
            verdict["unchecked_rules"] = [r["id"] for r in rules[J.MAX_QUESTIONS:]]
        if broken is None:
            return {**verdict, "outcome": "unchecked", "error": error or "shape", "call": call["id"]}
        return {**verdict, "broken": broken, "outcome": "broken" if broken else "pass", "call": call["id"]}
    except Exception as exc:  # noqa: BLE001 - the check never stops the write
        LOG.warning("Rule check preflight failed: %s", type(exc).__name__)
        if verdict is not None:
            return {**verdict, "outcome": "unchecked", "error": "error"}
        return None


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
    """Who reviews a hold: a broken rule's named reviewer, else the first person up the bot's chain, else its operator,
    else the default person (screening's `person_for`), else the team owner, else an Admin; each one still on the team.
    Never a bot. None only when the team has none of them: the hold is then the owner's and Admins' (`_reader`)."""
    for rule in rules:
        pid = rule.get("reviewer")
        if pid and _present(c, pid):
            return H.human_actor(pid)
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
                     "('rules.blocked','rules.escalated') AND target=? AND created_at>=?",
                     (author, "org_rule:" + rule_id, _day())).fetchone()[0]


def _tell_bot(c, author, text):
    """A note on the bot's next run, as the keeper (a plain row: a paused bot's note waits, nothing is refused)."""
    if not H.is_bot(author):
        return
    row = {"id": H.new_id(), "from_actor": H.KEEPER, "to_actor": author, "body": text[:H.NOTE_MAX], "created_at": H.now()}
    c.execute("INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES (:id,:from_actor,:to_actor,:body,:created_at)",
              row)
    H.event(c, H.KEEPER, "note.create", row["id"], {"to": author, "org_rule": True})


def _bot_name(c, author):
    return (H.bot(c, H.actor_id(author)) or {}).get("display_name") or H.actor_id(author)


def _review_task(c, hold_id, author, rule, reviewer, appeal=False):
    title = ("Appeal: " if appeal else "Held: ") + f"{_bot_name(c, author)} and the rule '{rule['text'][:80]}'"
    body = ("A bot's output was held by an org rule. Open the hold to Keep blocked, Send anyway or Edit rule. "
            "Nothing from the output is copied here.\n\nHold: " + hold_id)
    task = H.task_create(c, H.KEEPER, title[:200], body, reviewer, labels=[REVIEW_LABEL], lint=False,
                         deduplicate=False, quiet_notice=True)
    return task["id"]


def appeal_line(c, hold_id, reviewer):
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


def changed(c, verdict, author=None, operation="", recipients=()):
    """A write whose destination or object moved between its check and its write: delivered unchecked (the engine is
    never called inside the write), with a `rules.unchecked` event, reason `changed`. With no verdict (nothing was
    checked for the old destination), one is made from the rules matching the new one."""
    if verdict:
        return {**verdict, "outcome": "unchecked", "error": "changed", "broken": []}
    if not author or not on(c):
        return None
    recipients = sorted({r for r in recipients if r and r != author})
    rules = _rule_rows(R.matching(c, author, recipients))
    if not rules:
        return None
    return {"author": author, "operation": operation, "recipients": recipients, "rules": rules, "digest": "",
            "broken": [], "outcome": "unchecked", "error": "changed", "call": None, "cached": False, "draft": False,
            "matched": sorted((r["id"], r["version"]) for r in rules), "attempt_id": "", "context": {}, "obj": ""}


def apply(c, verdict, payload=None, replay=None, tell=False, obj_now=None):
    """The verdict inside the write. Returns None to deliver as is; {"warn": line, ...} to deliver with the warning
    line on the recipient's copy; {"held": view} to write nothing and answer with the hold. `payload` is the text the
    reviewer reads, `replay` what Send anyway needs to run the normal write. `tell` also leaves the bot a note (a
    write the bot does not see answered: a completion reply, an automation). `obj_now`: the object as the write reads
    it (`current_obj`). Moved since the check, a verdict that holds still holds (a version bump never releases a
    Block); any other comes back as {"moved": True}, and the caller does not write: its next try checks again."""
    if verdict and "applied" in verdict:
        return verdict["applied"]
    if not verdict or verdict.get("draft"):
        return None
    if obj_now is not None and verdict.get("obj") and obj_now != verdict["obj"] and not holds(verdict):
        return {"moved": True}
    author, operation = verdict["author"], verdict["operation"]
    if verdict["outcome"] in ("broken", "too_large"):
        # The rules in force now must be the ones checked: a rule edited, retired or added since is a new check, which
        # cannot run here (no engine under the write lock), so the output goes through unchecked.
        now = {(r["id"], r["version"]) for r in R.matching(c, author, verdict["recipients"])}
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
        line = f"Warning: {_bot_name(c, author)}'s message was too long to check against the rule '{strictest['text']}'."
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
        line = f"Warning: {_bot_name(c, author)}'s message may break the rule '{strictest['text']}'."
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
            row = c.execute("SELECT task_id FROM org_rule_holds WHERE author=? AND created_at>=? AND task_id IS NOT NULL "
                            "AND json_extract(verdict,'$.rule')=? AND json_extract(verdict,'$.repeat')=1 "
                            "ORDER BY created_at LIMIT 1", (author, _day(), strictest["id"])).fetchone()
            task_id = row[0] if row else None
        if task_id is None and reviewer:
            task_id = _review_task(c, hold_id, author, strictest, reviewer)
    text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str)
    # The run the output came from: Send anyway writes as the bot with it, so the run's private tasks and meetings are
    # checked against the destination again, as the original write was.
    # The check's own input (`input`) too: a rule changed since is checked on what this check was shown, and Send anyway
    # refuses once the object moved. `path` is the write's route, whose privacy guard Send anyway runs again.
    stored = {"text": text, "replay": replay,
              "provenance": {"attempt_id": verdict.get("attempt_id") or "", "path": PATH.get()},
              "input": {"context": verdict.get("context") or {}, "obj": verdict.get("obj") or ""}}
    matched = verdict.get("matched") or sorted((r["id"], r["version"]) for r in verdict["rules"])
    detail = {"rule": strictest["id"], "outcome": mode, "broken": [r["id"] for r in broken], "repeat": repeat,
              "too_large": too_large, "cached": verdict["cached"], "call": verdict.get("call"),
              "digest": verdict["digest"], "versions": {r["id"]: r["version"] for r in verdict["rules"]},
              "matched": [list(x) for x in matched]}
    c.execute("INSERT INTO org_rule_holds(id, operation, payload, author, destinations, rule_ids, "
              "verdict, nonce, state, reviewer, task_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
            + appeal_line(c, hold_id, reviewer))
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
    return f"{_bot_name(c, author)}'s reply was held: it broke the rule '{rule['text']}'."


# ----------------------------------------------------------------------------- holds
def _hold(c, hold_id):
    row = c.execute("SELECT * FROM org_rule_holds WHERE id=?", (hold_id,)).fetchone()
    if not row:
        raise Problem("not_found", "There is no hold " + str(hold_id), 404)
    return dict(row)


def _reader(who, hold, c=None, auth=None):
    """The author bot and the reviewer read a hold; nobody else (an admin included), except that a hold with no
    reviewer, or one no longer on the team, is the owner's and Admins' to review (with `c` and `auth` given), so no hold
    is ever left with nobody to release it."""
    actor = who.actor
    if who.role == "bot" and actor == hold["author"] and not who.via:
        return "author"
    if not H.is_human(actor) or who.via in ("botops", "assistant"):
        return None
    if actor == hold["reviewer"]:
        return "reviewer"
    if c is not None and auth is not None and auth.bot_admin(who) and \
            not (hold["reviewer"] and _present(c, hold["reviewer"])):
        return "reviewer"
    return None


def view_hold(c, who, hold, role=None, summary=False, auth=None):
    """One hold as its reader sees it. `summary` leaves out the held text, the appeal and the nonce (a list's row:
    the reader opens each hold for those)."""
    role = role or _reader(who, hold, c, auth)
    if not role:
        raise Problem("not_found", "There is no hold " + hold["id"], 404)
    stored = {} if summary else json.loads(hold["payload"] or "{}")
    verdict = json.loads(hold["verdict"] or "{}")
    rule_ids = json.loads(hold["rule_ids"] or "[]")
    destinations = json.loads(hold["destinations"] or "[]")
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
           "author_name": cut(_bot_name(c, hold["author"])), "state": hold["state"],
           "outcome": "escalated" if verdict.get("outcome") == "escalate" else "blocked", "rules": rules,
           "to": [cut(n) for n in _names(c, destinations)],
           "reviewer": hold["reviewer"], "reviewer_name": cut(reviewer_name), "task_id": hold["task_id"],
           "created_at": hold["created_at"], "decided_at": hold["decided_at"], "decided_by": hold["decided_by"],
           "can_send": role == "reviewer" and hold["state"] == "held" and hold["operation"] in REPLAY}
    if summary:
        out["appealed"] = bool(hold["why"])
        out["rules_count"] = len(json.loads(hold["rule_ids"] or "[]"))
        out["to_count"] = len(json.loads(hold["destinations"] or "[]"))
        return out
    text = stored.get("text") or ""
    out["text"] = text[:HOLD_TEXT_CHARS]
    if len(text) > HOLD_TEXT_CHARS:
        out["text_cut"] = len(text)
    if role == "reviewer":
        out["why"] = hold["why"]
        if hold["state"] == "held":
            out["nonce"] = hold["nonce"]
    return out


def _reader_sql(who, c=None, auth=None):
    """`_reader` as SQL, so a page counts only the holds this caller may read. The owner and Admins (with `c` and
    `auth`) also read holds whose reviewer is missing or gone: those rows come back with `fallback` True, for
    `_reader` to settle row by row."""
    if who.role == "bot" and not who.via:
        return "author=?", (who.actor,), False
    if H.is_human(who.actor) and who.via not in ("botops", "assistant"):
        if c is not None and auth is not None and auth.bot_admin(who):
            return "1", (), True
        return "reviewer=?", (who.actor,), False
    return None, (), False


def holds_for_task(c, who, task_id, cursor=None, limit=HOLD_LIST_MAX, auth=None):
    """One page of a task's holds the caller may read, oldest first, as summaries: at most `limit` and at most
    HOLD_LIST_BYTES. `next_cursor` continues it."""
    where, args, fallback = _reader_sql(who, c, auth)
    if not where:
        return [], None
    limit = max(1, min(int(limit or HOLD_LIST_MAX), HOLD_LIST_MAX))
    after = ("", "")
    if cursor:
        created, _, hid = str(cursor).partition("|")
        if not created or not hid:
            raise Problem("validation", "That cursor is not valid", 422)
        after = (created, hid)
    query = c.execute(f"SELECT * FROM org_rule_holds WHERE task_id=? AND {where} AND (created_at>? OR (created_at=? AND "
                      "id>?)) ORDER BY created_at, id", (task_id, *args, after[0], after[0], after[1]))
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
    for row, role in rows[:limit]:
        item = view_hold(c, who, row, role=role, summary=True)
        size = len(json.dumps(item, default=str)) + 1
        if used + size > HOLD_LIST_BYTES:
            break
        out.append(item)
        used += size
    more = len(out) < len(rows)
    last = out[-1] if out else None
    return out, (f"{last['created_at']}|{last['id']}" if more and last else None)


def _finish_task(c, hold):
    """The review task closes once none of its holds waits."""
    tid = hold["task_id"]
    if not tid:
        return
    left = c.execute("SELECT 1 FROM org_rule_holds WHERE task_id=? AND state='held' LIMIT 1", (tid,)).fetchone()
    task = H.task(c, tid)
    if not left and task and task["status"] not in ("done", "closed"):
        H.task_close(c, H.KEEPER, tid, "Every hold on this task is decided.", quiet=True)


def _decide(c, who, hold, state):
    c.execute("UPDATE org_rule_holds SET state=?, decided_at=?, decided_by=?, nonce=? WHERE id=? AND state='held'",
              (state, H.now(), who.actor, "used:" + secrets.token_hex(8), hold["id"]))
    from . import audit_log
    audit_log.write(c, who.actor, "org_rule_hold:" + hold["id"], {"state": "held"}, {"state": state},
                    who.via or H.VIA.get() or "api")
    H.event(c, who.actor, "org_rule_hold." + state, "org_rule_hold:" + hold["id"],
            {"operation": hold["operation"], "author": hold["author"]})
    _finish_task(c, {**hold, "state": state})


def keep(c, who, hold_id, auth=None):
    hold = _hold(c, hold_id)
    if _reader(who, hold, c, auth) != "reviewer":
        raise Problem("not_found", "There is no hold " + hold_id, 404)
    if hold["state"] != "held":
        raise Problem("conflict", "This hold is already decided", 409)
    _decide(c, who, hold, "kept")
    _tell_bot(c, hold["author"], f"Your held {OPERATIONS.get(hold['operation'], hold['operation'])} stays blocked "
              f"(hold {hold_id}).")
    return view_hold(c, who, _hold(c, hold_id), "reviewer")


def bot_identity(hold):
    """The bot the held write runs as on Send anyway, with the run it came from (its private tasks and meetings)."""
    from .auth import Identity
    stored = json.loads(hold["payload"] or "{}")
    attempt = (stored.get("provenance") or {}).get("attempt_id") or ""
    return Identity(hold["author"], "bot", agent="org-rule-hold", attempt_id=attempt)


def hold_matched(hold):
    """The (rule id, version) pairs that matched when the output was held."""
    verdict = json.loads(hold["verdict"] or "{}")
    if verdict.get("matched") is not None:
        return {tuple(x) for x in verdict["matched"]}
    return {(rid, v) for rid, v in (verdict.get("versions") or {}).items()}


def current_matched(c, hold):
    return {(r["id"], r["version"]) for r in R.matching(c, hold["author"], json.loads(hold["destinations"] or "[]"))}


def hold_input(hold):
    """What a hold's check was shown besides its text and audience: its context and its object."""
    stored = json.loads(hold["payload"] or "{}")
    return stored.get("input") or {"context": {}, "obj": ""}


def _unchanged(c, hold):
    """Refuse Send anyway once the object the output was checked against changed (a task edited since): its new
    content was never checked. The bot or a person writes it again, and that write is checked fresh."""
    obj = hold_input(hold).get("obj") or ""
    if obj and current_obj(c, obj) != obj:
        raise Problem("changed", "What this was about changed since it was held; it stays held. Write it again to "
                      "check it.", 409)


def current_audience(c, hold, audience=None):
    """Who the held write would reach now, as its check counts them, or None when its operation names no way to tell
    (a mail, addressed by the bot's own tool) or its replay would be refused anyway (the replayer's own checks)."""
    fn = (audience if audience is not None else AUDIENCE).get(hold["operation"])
    if fn is None:
        return None
    stored = json.loads(hold["payload"] or "{}")
    try:
        found = fn(c, bot_identity(hold), stored.get("replay") or {})
    except Problem:
        return None
    return sorted({r for r in found or () if r and r != hold["author"]})


def _same_audience(c, hold, audience=None):
    """Refuse Send anyway once the held write would reach someone else than its check was for (a task handed to a
    new owner since): a rule's With is matched on the actual recipients, so that is a new check."""
    now = current_audience(c, hold, audience)
    if now is not None and now != sorted(json.loads(hold["destinations"] or "[]")):
        raise Problem("changed", "Who this goes to changed since it was held; it stays held. Write it again to "
                      "check it.", 409)


def recheck(store, primary, hold, audience=None):
    """Outside the write, before Send anyway or an approved mail: when the rules that match now differ from those at
    the hold (a rule edited or added since), the rules changed or added are a new check, on the same input the first
    check had (text, audience, context, object). Returns (the pairs matching now, that verdict or None). The write
    checks the pairs again and refuses on a change in between. A changed audience refuses first (`_same_audience`)."""
    with store.read() as c:
        _same_audience(c, hold, audience)
        now = current_matched(c, hold)
    if now <= hold_matched(hold):
        return sorted(now), None
    stored = json.loads(hold["payload"] or "{}")
    shown = hold_input(hold)
    verdict = preflight(store, primary, bot_identity(hold), hold["operation"], stored.get("text") or "",
                        json.loads(hold["destinations"] or "[]"), context=shown.get("context") or None,
                        obj=shown.get("obj") or "", skip=hold_matched(hold))
    return sorted(now), verdict


def _rechecked(c, hold, check):
    """Inside the write: the rules matching now are those `recheck` saw, and a new check it made does not hold the
    output. A warn or an unchecked result is recorded and the send goes on (the reviewer chose to send it); the warn's
    gate comes back, for the replayed write to put its line on the recipient's copy."""
    if check is None:
        return None
    expected, verdict = check
    if current_matched(c, hold) != {tuple(x) for x in expected}:
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
    if _reader(who, hold, c, auth) != "reviewer":
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
        raise Problem("recheck", "This hold is from an older version and can't be sent as it is; it stays held. "
                      "Write it again to check it.", 409)
    _unchanged(c, hold)
    _same_audience(c, hold, audience)
    gate = _rechecked(c, hold, check)
    bot = bot_identity(hold)
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
    if _reader(who, hold) != "author":
        raise Problem("not_found", "There is no hold " + hold_id, 404)
    if hold["state"] != "held":
        raise Problem("conflict", "This hold is already decided", 409)
    why = str(why or "").strip()[:2000]
    task_id = hold["task_id"]
    if not hold["reviewer"]:
        raise Problem("no_reviewer", "Nobody on the team can review this hold", 409)
    if not task_id:
        rid = (json.loads(hold["verdict"] or "{}")).get("rule")
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
                    "AND json_extract(payload,'$.replay.mail_digest')=? "
                    "AND json_extract(verdict,'$.used') IS NULL ORDER BY decided_at LIMIT 1",
                    (author, key)).fetchone()
    return dict(row) if row else None


def claim_mail(c, hold_id):
    """Spend a mail approval inside the write: only one request ever changes the row."""
    return c.execute("UPDATE org_rule_holds SET verdict=json_set(verdict,'$.used',1) WHERE id=? "
                     "AND json_extract(verdict,'$.used') IS NULL", (hold_id,)).rowcount == 1


def mail_warning_line(gate):
    """The line a warned mail carries at the top of its body, for the recipient."""
    return warn_line(gate)


def open_mail_hold(c, author, key):
    """The hold already waiting on this exact mail, if any: a draft sent again while it waits is that hold, not a new
    one (one review task per mail)."""
    row = c.execute("SELECT * FROM org_rule_holds WHERE author=? AND operation='mail' AND state='held' "
                    "AND json_extract(payload,'$.replay.mail_digest')=? ORDER BY created_at DESC LIMIT 1",
                    (author, key)).fetchone()
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
    return {"id": hold["id"], "outcome": "escalated" if verdict.get("outcome") == "escalate" else "blocked",
            "rules": rules, "operation": "mail", "text": "This email is still held. "
            + appeal_line(c, hold["id"], hold["reviewer"])}


def _blocked_again(c, hold, verdict):
    """One more blocked attempt at a held mail: recorded as a block of its rule; the bot's third block of that rule
    today escalates the hold (a review task, the day's repeat task reused). The hold row comes back as it is now."""
    detail = json.loads(hold["verdict"] or "{}")
    rid, author = detail.get("rule"), hold["author"]
    if detail.get("outcome") != "block" or not rid:
        return hold
    rules = json.loads(hold["rule_ids"] or "[]")
    if _repeats(c, author, rid) < REPEAT_BLOCKS - 1:
        H.event(c, author, "rules.blocked", "org_rule:" + rid, {
            "hold": hold["id"], "operation": "mail", "rules": rules, "cached": bool(verdict.get("cached")),
            "reviewer": hold["reviewer"], "again": True})
        return hold
    row = c.execute("SELECT task_id FROM org_rule_holds WHERE author=? AND created_at>=? AND task_id IS NOT NULL "
                    "AND json_extract(verdict,'$.rule')=? AND json_extract(verdict,'$.repeat')=1 "
                    "ORDER BY created_at LIMIT 1", (author, _day(), rid)).fetchone()
    task_id = hold["task_id"] or (row[0] if row else None)
    if task_id is None and hold["reviewer"]:
        rule = c.execute("SELECT id, text FROM org_rules WHERE id=?", (rid,)).fetchone()
        task_id = _review_task(c, hold["id"], author, dict(rule) if rule else {"text": "a rule"}, hold["reviewer"])
    c.execute("UPDATE org_rule_holds SET verdict=json_set(verdict,'$.outcome','escalate','$.repeat',1), "
              "task_id=? WHERE id=?", (task_id, hold["id"]))
    H.event(c, author, "rules.escalated", "org_rule:" + rid, {
        "hold": hold["id"], "operation": "mail", "rules": rules, "cached": bool(verdict.get("cached")),
        "reviewer": hold["reviewer"], "repeat": True, **({"task": task_id} if task_id else {})})
    return _hold(c, hold["id"])


def supersede_mail(c, hold_id):
    """An approval a newer rule held again is spent: the new hold's Send anyway is the approval that counts."""
    c.execute("UPDATE org_rule_holds SET verdict=json_set(verdict,'$.used','superseded') WHERE id=? "
              "AND json_extract(verdict,'$.used') IS NULL", (hold_id,))


@replayer("mail")
def _replay_mail(c, bot, hold, replay, ingest, gate=None):
    """A mail is sent by its bot's own mail tool, not the server: Send anyway lets that exact mail pass its next check,
    once, and tells the bot to send it again."""
    _tell_bot(c, hold["author"], "The email you were told was held may go now: send that exact draft again "
              f"(hold {hold['id']}).")
    return {"approved": True}


# ----------------------------------------------------------------------------- stats and health
def stats(c, settings):
    day = _day()
    label = J.load_set(QUESTION_SET)["label"]
    checks = c.execute("SELECT count(*) FROM events INDEXED BY events_task_origin WHERE actor=? AND action='judge.call' "
                       "AND target=? AND created_at>=?", (H.KEEPER, label, day)).fetchone()[0]

    def count(*actions):
        return c.execute(f"SELECT count(*) FROM events WHERE action IN ({','.join('?' * len(actions))}) AND created_at>=?",
                         (*actions, day)).fetchone()[0]
    hits = count("rules.blocked", "rules.escalated", "rules.warned")
    cap = float(settings.rules_monthly_usd or 0)
    left = None if cap <= 0 else round(max(0.0, cap - B.spent_this_month(c, None, BUDGET)), 2)
    return {"checks_today": checks, "unchecked_today": count("rules.unchecked"),
            "blocked_today": count("rules.blocked", "rules.escalated"), "hits_today": hits,
            "usd_left": left, "on": on(c)}


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
        return c.execute("INSERT OR IGNORE INTO org_rule_misses(author, id, created_at) VALUES(?,?,?)",
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
    c.execute("DELETE FROM org_rule_misses WHERE created_at<?", (cutoff,))
    counted = on(c) and bool(R.matching(c, author))
    room = MISSES_DAY_MAX - c.execute(
        "SELECT count(*) FROM events WHERE action='rules.unchecked' AND actor=? AND created_at>=? "
        "AND json_extract(detail,'$.reason')='connector'", (author, _day())).fetchone()[0] if counted else 0
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
                rules = R.matching(c, "bot:" + bot, mail_recipients(c, [*body.to, *body.cc]))
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
        The same callers as mail-check: a person's own mail was never checked, so it records nothing."""
        who = request.state.identity
        author = author_of(who, store.settings.assistant_bot)
        if not author:
            return {"recorded": 0, "known": []}
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        if author != "bot:" + bot:
            raise Problem("forbidden", "A bot reports only its own mail", 403)
        ids = list(dict.fromkeys(body.ids))
        if any(not MISS_ID.fullmatch(i) for i in ids):
            raise Problem("validation", "Each id is a UUID", 422)
        return mutate(request, body, lambda c: record_misses(c, author, ids))

    @app.post("/api/v2/bots/{bot}/mail-check")
    def mail_check(request: Request, bot: str, body: MailCheck):
        """The org rule check of one mail, asked by the bot's mail tool before it sends (connectors/mail): held, the
        mail stays a draft. Only the bot itself asks. A person's own mail, their Assistant's included, is never
        checked (`not_checked`). Another bot's token is refused, and a refusal or a server error is recorded as
        unchecked: the connector sends on a failed check."""
        who = request.state.identity
        author = author_of(who, store.settings.assistant_bot)
        if not author:
            return {"outcome": "not_checked"}
        check_id = check_id_of(request)
        with store.read() as c:
            named = H.resolve_bot(c, bot) or {}
        bot = named.get("id", bot)
        if author != "bot:" + bot:
            LOG.warning("A mail check for %s came with another bot's token", named.get("slug", bot))
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
        """`author` is the bot the mail is checked as (BotOps for a person included): its holds and approvals are
        found under it; `who` stays the caller, for the write's permission and audit. A check counted unchecked keeps
        `check_id`, so the mail tool's later report of it (it never got this answer) counts nothing more."""
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
    def rule_holds(request: Request, task: str, cursor: str | None = None, limit: int = HOLD_LIST_MAX):
        """The holds on one review task the reviewer or the author bot may read, as summaries (open one for its text),
        a page at a time: pass `next_cursor` as `cursor` for the rest."""
        who = request.state.identity
        with store.read() as c:
            items, nxt = holds_for_task(c, who, task, cursor, limit, auth)
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

    @app.post("/api/v2/rule-holds/{hold_id}/send")
    def rule_hold_send(request: Request, hold_id: str, body: HoldSend):
        who = request.state.identity
        ingest, check = None, None
        with store.read() as c:
            hold = _hold(c, hold_id)
            ready = _reader(who, hold, c, auth) == "reviewer" and hold["state"] == "held" and \
                not store.saved(c, who, request.url.path, request.headers.get("idempotency-key"))
        if ready:
            # Both outside the write: rules changed since the hold are a new check, and ask ingest runs as it would
            # have for the original write (the rules it was held on are not asked again).
            check = recheck(store, app.state.judge, hold, audience)
            if hold["operation"] in prepare:
                stored = json.loads(hold["payload"] or "{}")
                try:
                    ingest = prepare[hold["operation"]](store, bot_identity(hold), stored.get("replay") or {})
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
        sending that exact draft costs no second call. A person's draft is never checked."""
        who = request.state.identity
        author = author_of(who, store.settings.assistant_bot)
        if not author:
            return {"outcome": "not_checked", "rules": [], "reason": "A person's own writes are not checked"}
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


__all__ = ["ACTIONS", "BUDGET", "OPERATIONS", "REPLAY", "answer", "apply", "author_of", "digest", "ensure",
           "health_findings", "held_note", "holds", "install", "on", "preflight", "replayer", "reviewer_for", "stats",
           "warned_text", "watched"]
