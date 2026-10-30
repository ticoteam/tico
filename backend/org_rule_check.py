"""Org rule checks (docs/rules-plan.md §3): a bot's output is checked against the org rules that apply to it before it
is delivered (`questions/rule-check.json`, asked through the server's Decide engine on the rules' own budget).

Like ask ingest (backend/ask_ingest.py): one call per output, outside the write's transaction (`preflight`); plain
code then applies the verdict inside the write (`apply`). Nothing here calls an engine under `store.transaction()` or
`mutate`: `apply` only reads the verdict and writes rows.

- Whose writes: a bot's (the actor is a bot, BotOps acts for a bot, or BotOps acts as a person: `author_of`). A
  person's own writes, and their Assistant's, are never checked.
- Which rules: `org_rules.matching(c, author, recipients)`, the full audience of the write. No matching rule is no
  call, which is most outputs.
- What is judged: the whole output, short or long. An output whose state is over Decide's limit is held, not passed.
- If broken, per rule, strictest wins: Block holds it (a 200 `held` answer, never a refusal, so no quarantine) and
  the bot is told the rule and whom to appeal to; Escalate also files one review task for the reviewer; Warn
  delivers it with a warning line on the recipient's copy. The 3rd block of one rule by one bot in a UTC day
  escalates for the rest of that day, with one review task.
- Holds: the payload lives only in `org_rule_holds`, read by the author bot and the reviewer alone. Send anyway
  replays the normal write once, as the bot (`REPLAY`), with a one-shot nonce and no second Decide call.
- Fails open: checks Off, no engine, the rules budget, the day's call cap, the deadline, an error or a malformed
  answer let the output through with a `rules.unchecked` event. Events never carry the text.
- Cache: verdicts are kept by a digest of the check input, the matched rules and versions, the set and the level, so
  `hub rules check` on a draft and then sending it costs one call.
"""

import hashlib
import json
import logging
import secrets
import threading

from clients import judge as J
from . import judge as B
from . import org_rules as R
from . import team_rules
from .store import H, Problem

LOG = logging.getLogger("tico.org_rule_check")
QUESTION_SET = "rule-check"
DEADLINE = 10
BUDGET = ("rules_monthly_usd", "rules.spend")     # backend/judge.py: its own cap and spend rows, apart from Decide's
STRICT = {"warn": 1, "block": 2, "escalate": 3}
REPEAT_BLOCKS = 3             # the 3rd block of one rule by one bot in a UTC day escalates for the rest of that day
REVIEW_LABEL = "rule-review"
CACHE_DAYS = 7
HOLD_LIST_MAX = 50
TEAM_RULE = "org_rule_checks"
UNCHECKED_SHARE, UNCHECKED_MIN = 0.2, 10    # a day's unchecked share at or over this, of at least this many checks
ACTIONS = ("rules.blocked", "rules.escalated", "rules.warned", "rules.unchecked")
# What each operation is, in the words the model reads and the hold shows.
OPERATIONS = {
    "message": "a chat message", "comment": "a task comment", "comment_edit": "an edit to a task comment",
    "task_create": "a new task", "task_update": "a change to a task", "task_owner": "a task assignment",
    "task_ask": "a question on a task", "bulk": "a change to several tasks", "note": "a note to a bot",
    "reply": "a reply in a conversation", "automation": "an automation's write", "slack": "a Slack reply",
    "mail": "an email",
}
DRAFT_AS = {"message": "message", "comment": "comment", "task": "task_create", "assign": "task_owner"}

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS org_rule_verdicts(digest TEXT PRIMARY KEY, broken_json TEXT NOT NULL, "
    "call_id TEXT, created TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS org_rule_verdicts_created ON org_rule_verdicts(created)",
    "CREATE INDEX IF NOT EXISTS org_rule_holds_task ON org_rule_holds(task_id) WHERE task_id IS NOT NULL",
)

# Send anyway's writes, by operation: fn(c, bot identity, hold, replay data, ingest) -> the write's answer. Registered
# by the module that owns the write (backend/app.py, execution, automations), so the replay is the normal write.
REPLAY = {}
# An optional step outside the transaction before a replay (ask ingest's own call): fn(store, bot identity, replay).
PREPARE = {}


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def replayer(operation, prepare=None):
    def register(fn):
        REPLAY[operation] = fn
        if prepare:
            PREPARE[operation] = prepare
        return fn
    return register


# ----------------------------------------------------------------------------- whose writes
def author_of(who, assistant_bot=""):
    """The bot a write is checked as, or None for a person's own write (their Assistant's included)."""
    if who is None:
        return None
    assistant = ("bot:" + assistant_bot) if assistant_bot else ""
    if who.via == "assistant" or (assistant and (who.task_actor == assistant or who.actor == assistant)):
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


def watched(c, bot):
    """Whether any active rule's Who names this bot: its live reply text stays hidden until delivered."""
    slug = H.actor_id(bot)
    try:
        if not on(c) or not c.execute("SELECT 1 FROM org_rules WHERE status='active' LIMIT 1").fetchone():
            return False
        key = R.who_key(c, "bot:" + slug)
        return any(R._hits(row["who"], key) for row in R._active(c))
    except Exception:            # no tables yet (an old copy): nothing to hide
        return False


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


def digest(operation, author, recipients, output, obj, rules, label, level):
    raw = json.dumps([operation, author, sorted(set(recipients)), output, obj or "",
                      sorted((r["id"], r["version"]) for r in rules), label, level], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _questions(qset, rules):
    template = qset["questions"]["rule"]
    out = {}
    for n, rule in enumerate(rules, 1):
        instructions = dict(template["instructions"])
        instructions["question"] = instructions["question"].replace("{rule}", json.dumps(rule["text"]))
        out[f"r{n}"] = {**template, "instructions": instructions}
    return out


def _ask(engine, state, questions, label, level):
    """The engine's answer within DEADLINE seconds: (result, None), (None, error code) or (None, 'timeout')."""
    box = {}

    def work():
        try:
            box["result"] = B.ask(engine, state, questions, label, level)
        except J.JudgeError as exc:
            box["error"] = exc.code or "error"
        except Exception as exc:    # a fake, a provider bug: the write never breaks on it
            LOG.warning("Rule check call failed: %s", type(exc).__name__)
            box["error"] = "error"
    worker = threading.Thread(target=work, name="org-rule-check", daemon=True)
    worker.start()
    worker.join(DEADLINE)
    if worker.is_alive():
        return None, "timeout"
    return box.get("result"), box.get("error")


def preflight(store, primary, who, operation, output, recipients=(), context=None, obj="", draft=False):
    """Check one output outside any transaction. Returns the verdict `apply` reads, or None when nothing is checked
    (a person's write, checks Off, no rule matches). Never raises: a failure is a verdict marked unchecked."""
    author = author_of(who, store.settings.assistant_bot)
    if not author:
        return None
    verdict = None
    try:
        output = output if isinstance(output, str) else json.dumps(output, sort_keys=True, default=str)
        recipients = sorted({r for r in recipients or () if r and r != author})
        with store.read() as c:
            if not on(c):
                return None
            rules = _rule_rows(R.matching(c, author, recipients))
            if not rules:
                return None
            level = store.settings.rule_check_level or "medium"
            qset = J.load_set(QUESTION_SET)
            key = digest(operation, author, recipients, output, obj, rules, qset["label"], level)
            verdict = {"author": author, "operation": operation, "recipients": recipients, "rules": rules,
                       "digest": key, "broken": [], "outcome": "pass", "error": None, "call": None, "cached": False,
                       "draft": draft}
            hit = c.execute("SELECT broken_json, call_id FROM org_rule_verdicts WHERE digest=?", (key,)).fetchone()
            if hit:
                broken = json.loads(hit[0] or "[]")
                return {**verdict, "broken": broken, "outcome": "broken" if broken else "pass", "cached": True,
                        "call": hit[1]}
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
            if B.used_today(c, H.KEEPER) >= B.daily_cap(store.settings, "bot"):
                return {**verdict, "outcome": "unchecked", "error": "day_cap"}
        result, error = _ask(engine, state, questions, qset["label"], level)
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
                c.execute("INSERT OR REPLACE INTO org_rule_verdicts(digest, broken_json, call_id, created) "
                          "VALUES(?,?,?,?)", (key, json.dumps(broken), call["id"], H.now()))
                c.execute("DELETE FROM org_rule_verdicts WHERE created<?", (H.shift(H.now(), days=-CACHE_DAYS),))
        if len(rules) > J.MAX_QUESTIONS and broken is not None:
            verdict["unchecked_rules"] = [r["id"] for r in rules[J.MAX_QUESTIONS:]]
        if broken is None:
            return {**verdict, "outcome": "unchecked", "error": error or "shape", "call": call["id"]}
        return {**verdict, "broken": broken, "outcome": "broken" if broken else "pass", "call": call["id"]}
    except Exception as exc:          # the check never stops the write
        LOG.warning("Rule check preflight failed: %s", type(exc).__name__)
        if verdict is not None:
            return {**verdict, "outcome": "unchecked", "error": "error"}
        return None


def holds(verdict):
    """Whether a verdict will hold the write (so a caller skips ask ingest's own call: a block skips it)."""
    if not verdict:
        return False
    if verdict["outcome"] == "too_large":
        return True
    if verdict["outcome"] != "broken":
        return False
    return any(r["if_broken"] != "warn" for r in verdict["rules"] if r["id"] in verdict["broken"])


# ----------------------------------------------------------------------------- inside the write
def _day(at=None):
    return (at or H.now())[:10] + "T00:00:00"


def reviewer_for(c, author, rules):
    """Who reviews a hold: a broken rule's named reviewer, else the first person up the bot's chain, else its operator,
    else the default person. Never a bot."""
    for rule in rules:
        pid = rule.get("reviewer")
        if pid and H.human(c, pid):
            return H.human_actor(pid)
    from .report_managers import person_above
    from .screening import person_for
    slug = H.actor_id(author)
    above = person_above(c, slug)
    if above and H.human(c, H.actor_id(above)):
        return H.human_actor(H.actor_id(above))
    return person_for(c, slug)


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


def apply(c, verdict, payload=None, replay=None, tell=False):
    """The verdict inside the write. Returns None to deliver as is; {"warn": line, ...} to deliver with the warning
    line on the recipient's copy; {"held": view} to write nothing and answer with the hold. `payload` is the text the
    reviewer reads, `replay` what Send anyway needs to run the normal write. `tell` also leaves the bot a note (a
    write the bot does not see answered: a completion reply, an automation)."""
    if not verdict or verdict.get("draft"):
        return None
    author, operation = verdict["author"], verdict["operation"]
    if verdict["outcome"] in ("broken", "too_large"):
        # The rules in force now must be the ones checked: a rule edited, retired or added since is a new check, which
        # cannot run here (no engine under the write lock), so the output goes through unchecked.
        now = {(r["id"], r["version"]) for r in R.matching(c, author, verdict["recipients"])}
        if now != {(r["id"], r["version"]) for r in verdict["rules"]}:
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
        mode = "block"                             # an output too large to check is held, never passed
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
    stored = {"text": text, "replay": replay}
    detail = {"rule": strictest["id"], "outcome": mode, "broken": [r["id"] for r in broken], "repeat": repeat,
              "too_large": too_large, "cached": verdict["cached"], "call": verdict.get("call"),
              "digest": verdict["digest"], "versions": {r["id"]: r["version"] for r in verdict["rules"]}}
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


def _reader(who, hold):
    """The author bot and the reviewer read a hold; nobody else (an admin included)."""
    actor = who.actor
    if who.role == "bot" and actor == hold["author"] and not who.via:
        return "author"
    if H.is_human(actor) and actor == hold["reviewer"] and who.via not in ("botops", "assistant"):
        return "reviewer"
    return None


def view_hold(c, who, hold, role=None):
    role = role or _reader(who, hold)
    if not role:
        raise Problem("not_found", "There is no hold " + hold["id"], 404)
    stored = json.loads(hold["payload_json"] or "{}")
    verdict = json.loads(hold["verdict_json"] or "{}")
    rules = []
    for rid in json.loads(hold["rule_ids_json"] or "[]"):
        row = c.execute("SELECT id, text, version, status, if_broken FROM org_rules WHERE id=?", (rid,)).fetchone()
        if row:
            rules.append(dict(row))
    out = {"id": hold["id"], "operation": hold["operation"], "author": hold["author"],
           "author_name": _bot_name(c, hold["author"]), "state": hold["state"],
           "outcome": verdict.get("outcome"), "rules": rules, "text": stored.get("text") or "",
           "to": _names(c, json.loads(hold["destinations_json"] or "[]")),
           "reviewer": hold["reviewer"], "reviewer_name": H.person_name(c, H.actor_id(hold["reviewer"]))
           if hold["reviewer"] else None, "task_id": hold["task_id"], "created": hold["created"],
           "decided_at": hold["decided_at"], "decided_by": hold["decided_by"],
           "can_send": role == "reviewer" and hold["state"] == "held" and hold["operation"] in REPLAY}
    if role == "reviewer":
        out["why"] = hold["why"]
        if hold["state"] == "held":
            out["nonce"] = hold["nonce"]
    return out


def holds_for_task(c, who, task_id, limit=HOLD_LIST_MAX):
    rows = c.execute("SELECT * FROM org_rule_holds WHERE task_id=? ORDER BY created LIMIT ?",
                     (task_id, max(1, min(int(limit), HOLD_LIST_MAX)) + 1)).fetchall()
    out = [view_hold(c, who, dict(r)) for r in rows if _reader(who, dict(r))]
    return out[:limit], len(out) > limit


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


def keep(c, who, hold_id):
    hold = _hold(c, hold_id)
    if _reader(who, hold) != "reviewer":
        raise Problem("not_found", "There is no hold " + hold_id, 404)
    if hold["state"] != "held":
        raise Problem("conflict", "This hold is already decided", 409)
    _decide(c, who, hold, "kept")
    _tell_bot(c, hold["author"], f"Your held {OPERATIONS.get(hold['operation'], hold['operation'])} stays blocked "
              f"(hold {hold_id}).")
    return view_hold(c, who, _hold(c, hold_id), "reviewer")


def bot_identity(hold):
    from .auth import Identity
    return Identity(hold["author"], "bot", agent="org-rule-hold")


def send(c, who, hold_id, nonce, ingest=None, table=None):
    """Send anyway: the normal write runs once, as the bot, with the hold's one-shot nonce; no Decide call."""
    hold = _hold(c, hold_id)
    if _reader(who, hold) != "reviewer":
        raise Problem("not_found", "There is no hold " + hold_id, 404)
    if hold["state"] != "held":
        raise Problem("conflict", "This hold is already decided", 409)
    if not nonce or not secrets.compare_digest(str(nonce), hold["nonce"]):
        raise Problem("conflict", "This hold changed; open it again", 409)
    fn = (table or REPLAY).get(hold["operation"])
    if fn is None:
        raise Problem("unsupported", "This hold can't be sent from here", 409)
    stored = json.loads(hold["payload_json"] or "{}")
    _decide(c, who, hold, "sent")           # the nonce is spent first: a retry of this request cannot send twice
    result = fn(c, bot_identity(hold), hold, stored.get("replay") or {}, ingest)
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


def mail_approved(c, author, key):
    """A held mail the reviewer chose to send anyway, not yet sent: the next identical check passes it, once."""
    row = c.execute("SELECT id FROM org_rule_holds WHERE author=? AND operation='mail' AND state='sent' "
                    "AND json_extract(payload_json,'$.replay.mail_digest')=? "
                    "AND json_extract(verdict_json,'$.used') IS NULL ORDER BY decided_at LIMIT 1",
                    (author, key)).fetchone()
    return row[0] if row else None


@replayer("mail")
def _replay_mail(c, bot, hold, replay, ingest):
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
                       "AND target=? AND ts>=?", (H.KEEPER, label, day)).fetchone()[0]

    def count(*actions):
        return c.execute(f"SELECT count(*) FROM events WHERE action IN ({','.join('?' * len(actions))}) AND ts>=?",
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
    except Exception:
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
def install(app, store, auth, mutate):
    from fastapi import Request
    from pydantic import Field
    from typing import Literal
    from .models import Contract
    # This app's writes, as registered while it was built (one server builds one app; tests build many).
    table, prepare = dict(REPLAY), dict(PREPARE)

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
        """The org rule check of one mail, asked by the bot's mail tool before it sends (connectors/mail): held, the
        mail stays a draft. Only the bot itself (or BotOps for it) asks."""
        who = request.state.identity
        if who.role != "bot" or who.actor != "bot:" + bot:
            raise Problem("forbidden", "A bot checks only its own mail", 403)
        key = mail_digest(body.to, body.cc, body.subject, body.body)
        with store.read() as c:
            approved = mail_approved(c, who.actor, key)
            recipients = mail_recipients(c, [*body.to, *body.cc])
        if approved:
            def use(c):
                c.execute("UPDATE org_rule_holds SET verdict_json=json_set(verdict_json,'$.used',1) WHERE id=?",
                          (approved,))
                return {"outcome": "pass", "approved": approved}
            return mutate(request, body, use)
        output = (f"Subject: {body.subject}\n\n" if body.subject else "") + body.body
        verdict = preflight(store, app.state.judge, who, "mail", output, recipients,
                            context={"to": [*body.to, *body.cc][:20]})

        def work(c):
            gate = apply(c, verdict, output, {"mail_digest": key})
            if gate and gate.get("held"):
                return {"outcome": "held", **answer(gate)}
            if gate and gate.get("warned"):
                return {"outcome": "warn", **answer(gate)}
            return {"outcome": "unchecked" if verdict and verdict["outcome"] == "unchecked" else "pass"}
        return mutate(request, body, work)

    @app.get("/api/v2/rules/stats")
    def rules_stats(request: Request):
        """The Rules page header: checks today, unchecked, blocked, dollars left in the rules budget, and on/off."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return stats(c, store.settings)

    @app.get("/api/v2/rule-holds")
    def rule_holds(request: Request, task: str, limit: int = HOLD_LIST_MAX):
        """The holds on one review task, as the reviewer or the author bot reads them."""
        who = request.state.identity
        with store.read() as c:
            items, more = holds_for_task(c, who, task, limit)
        return {"holds": items, "more": more}

    @app.get("/api/v2/rule-holds/{hold_id}")
    def rule_hold(request: Request, hold_id: str):
        who = request.state.identity
        with store.read() as c:
            hold = _hold(c, hold_id)
            return view_hold(c, who, hold)

    @app.post("/api/v2/rule-holds/{hold_id}/keep")
    def rule_hold_keep(request: Request, hold_id: str, body: HoldEmpty):
        who = request.state.identity
        return mutate(request, body, lambda c: keep(c, who, hold_id))

    @app.post("/api/v2/rule-holds/{hold_id}/send")
    def rule_hold_send(request: Request, hold_id: str, body: HoldSend):
        who = request.state.identity
        ingest = None
        with store.read() as c:
            hold = _hold(c, hold_id)
            ready = _reader(who, hold) == "reviewer" and hold["state"] == "held"
        if ready and hold["operation"] in prepare:
            stored = json.loads(hold["payload_json"] or "{}")
            try:
                ingest = prepare[hold["operation"]](store, bot_identity(hold), stored.get("replay") or {})
            except Exception as exc:          # ask ingest's own preflight never stops the send
                LOG.warning("Hold replay preflight failed: %s", type(exc).__name__)
        return mutate(request, body, lambda c: send(c, who, hold_id, body.nonce, ingest, table))

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
