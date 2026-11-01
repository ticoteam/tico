"""The ten fake-day workflows (TESTING L2c): python -m evals.redesign.run --workflows [names or numbers].

Each workflow is a day across bots on the fake clock, fed through the product's real paths (CONTRACT §2 Feeding
input): mail through the mail sync's publish route, Slack through the gateway's events route, listening through the
listening ingest and judge routes, events through H.hook (backend/hubdb.py, as producers send them), webhooks through the
incoming trigger's URL, outgoing webhooks into fakes.Receiver. Checks read what the day left behind: runs, tasks,
turns, deliveries. Each fails with the wave it waits on until the redesign is built.

Read shapes this script assumes where the CONTRACT names none (each listed as a gap in the report):
- a run names what started it (a message id, a task id, an event subject, an item url) somewhere in its row, most
  likely `trigger_ref`; checks find a run by that text
- action `args` (CONTRACT §6 gap): create_task {owner, title, body}, send_reply {text}, label {label}, post_update
  {to, text}, call_webhook {webhook, type, data}; `{{step.text}}` reads a step's output
- Find on prs: query "open, no activity for 24 hours" (no query language is named)
- batch times are read in the team's time zone (America/Los_Angeles); the CONTRACT names none"""
import datetime as dt
import json
import os
import re
import traceback
from collections import Counter
from zoneinfo import ZoneInfo

from .cases.strategy import canon
from .fakes import MissingRecording, ReplayJudge, Receiver, attach_judge, deliver, hook, stripe_sig
from .harness import (CLAUDE, CLAUDE_ID, CLAUDE_REF, DANA, DANA_ID, DANA_REF, LEAD_ID, LEAD_REF, OPENAI,
                      OPENAI_ID, OPENAI_REF, PRIYA_ID, RELEASE_ID, W2, W3, W4, W5, W6, Check, Reply, Scenario, Unmet,
                      body, close_task, comment, get_caps, got, inbox_read, inbox_send, mail_computer, publish_mail,
                      put_budget, put_cap, rows, send, setup, task_id_of, turn_of)
from .record import INPUTS, load_inputs

try:   # the migration fixture's org bots, for workflow 10's Scenario.bots; the fixture itself is imported in its steps
    from .cases.migration_fixture import FIXTURE_BOTS
except ImportError:   # pragma: no cover (the fixture ships beside this file)
    FIXTURE_BOTS = {}

UTC, LA = dt.timezone.utc, ZoneInfo("America/Los_Angeles")   # LA: the team's time zone (backend/updates.py ZONE)
DANA_MAIL, PRIYA_MAIL = "dana@acme.example", "priya@acme.example"
NEWS, BOUNCE = "news@acme.example", "mailer-daemon@acme.example"
DEBUG = os.environ.get("WORKFLOWS_DEBUG") == "1"   # print the traceback of every step error turned into a FAIL


# ---------------------------------------------------------------------------------------------- inputs
def inputs():
    """The recorded inputs (recordings/inputs/*.json) by id. A file with problems is left out."""
    return {stem: data for stem, data, problems in load_inputs(INPUTS) if data and not problems}


def iso(when):
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def when_of(value):
    """An ISO time as an aware datetime (naive = UTC), or None."""
    try:
        at = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def mail_from(state, msg_id, mailbox=None, date=None):
    """A recorded mail input as the mail sync publishes it (MailMessage). The recorded fields are kept as they are, so
    the Decide state the hub builds from it matches the recording key (CONTRACT §2 Decide state)."""
    date = date or state["date"]
    return {"msg_id": msg_id, "thread_id": msg_id, "epoch": int(when_of(date).timestamp()), "date": date,
            "from_addr": state["from"], "from_header": f'"{state["from_name"]}" <{state["from"]}>',
            "to": [mailbox or state["to"]], "cc": [], "subject": state["subject"], "snippet": state["snippet"],
            "body": state["snippet"], "labels": list(state["labels"]), "attachments": list(state["attachments"]),
            "list_id": state["list_id"] or "", "is_internal": bool(state["is_internal"]),
            "has_unsubscribe": bool(state["unsubscribe"])}


def made_mail(msg_id, sender, subject, text, now, list_id="", unsubscribe=False):
    date = iso(now)
    return {"msg_id": msg_id, "thread_id": msg_id, "epoch": int(now.timestamp()), "date": date, "from_addr": sender,
            "from_header": sender, "to": [DANA_MAIL], "cc": [], "subject": subject, "snippet": text[:200],
            "body": text, "labels": ["INBOX"], "attachments": [], "list_id": list_id, "is_internal": False,
            "has_unsubscribe": unsubscribe}


# ---------------------------------------------------------------------------------------------- steps and time
def do(org, facts, key, on, fn, *args, **kwargs):
    """org.step, plus: an error the product raised inside a request (TestClient re-raises it), a missing recording or
    a refused fake-GitHub call is kept as an Unmet of wave `on` naming the error, so every check reads a reason."""
    try:
        facts[key] = fn(*args, **kwargs)
    except Unmet as unmet:
        facts[key] = unmet
    except MissingRecording as exc:
        facts[key] = Unmet(on, f"{key}: {exc}")
    except Exception as exc:  # noqa: BLE001 (a product error is a FAIL with its reason, never a crash of the day)
        if DEBUG:
            traceback.print_exc()
        facts[key] = Unmet(on, f"{key}: {type(exc).__name__}: {str(exc)[:200]}")
    return facts[key]


def at(org, when):
    """Move the fake clock to `when`. The runner reports in over the last 3 minutes (a longer silence reads as a
    computer that slept, execution.AWAKE_GAP/AWAKE_SETTLE), then the scheduler pass runs."""
    settle = when - dt.timedelta(minutes=3)
    if settle > org.now():
        org.clock.shift((settle - org.now()).total_seconds())
    if when > org.now():
        org.advance(when - org.now())
    org.scheduler_pass()
    return when


def la(day, hour, minute=0):
    return dt.datetime(*day, hour, minute, tzinfo=LA).astimezone(UTC)


def utc(day, hour, minute=0):
    return dt.datetime(*day, hour, minute, tzinfo=UTC)


def turn(org, slug, act=None, text="Done.", tokens=20_000, wave=W3):
    """The bot's next turn, run as a runner runs it: claim, started, `act(attempt_token, attempt)` (the bot's own
    calls during the turn, as that bot), complete with `text` and the token use. None when nothing was handed out."""
    org.scheduler_pass()
    claim = org.api("post", "/api/v2/jobs/claim", {"bot": slug, "start_places": True}, who=org.runner["token"],
                    wave=wave)
    attempt = claim.body.get("attempt") if isinstance(claim.body, dict) else None
    if not attempt:
        if claim.status in (200, 201, 204, 409, 423, 429):
            return None
        raise Unmet(wave, f"claim {slug}: {claim.status} {claim.excerpt()}")
    base = f"/api/v2/attempts/{attempt['id']}"
    started = org.api("post", base + "/started", {"thread_id": "workflow"}, who=org.runner["token"], wave=wave)
    if not 200 <= started.status < 300:
        raise Unmet(wave, f"start {slug}: {started.status} {started.excerpt()}")
    did = None
    if act:
        try:
            did = act(attempt.get("token"), attempt)
        except Unmet as unmet:
            did = unmet
    usage = {"input_tokens": tokens, "cached_tokens": 0, "output_tokens": 2_000, "model": "claude-opus-5-5",
             "runtime": "claude", "billing": "api"}
    done = org.api("post", base + "/complete", {"outcome": "completed", "text": text, "last_seq": 0, "limited": False,
                                                "usage": usage}, who=org.runner["token"], wave=wave)
    if not 200 <= done.status < 300:
        raise Unmet(wave, f"complete {slug}: {done.status} {done.excerpt()}")
    task = attempt.get("task") if isinstance(attempt.get("task"), dict) else {}
    return {"attempt": attempt["id"], "bot": slug, "task": task.get("id"),
            "about": canon([attempt.get("task"), attempt.get("message")]), "did": did}


def drain(org, slug, act=None, limit=8, **kwargs):
    """Every turn the bot has waiting, up to `limit`."""
    done = []
    for _ in range(limit):
        ran = turn(org, slug, act, **kwargs)
        if ran is None:
            break
        done.append(ran)
    return done


def emit(org, kind, subject, payload=None):
    """A catalog event through H.hook (outgoing webhooks and automations), in a transaction as its producers call it."""
    from backend.store import H
    with org.store.transaction() as c:
        return H.hook(c, kind, subject, lambda: payload or {}, H.KEEPER)


def sql(org, query, args=()):
    return org.sql(query, args)


# ---------------------------------------------------------------------------------------------- automations
def step(sid, kind, **fields):
    return {"id": sid, "type": kind, "enabled": True, **fields}


def act(sid, do_, **args):
    return step(sid, "action", do=do_, args=args)


def cond(field, op, value, least=None):
    found = {"field": field, "op": op, "value": value}
    if least is not None:
        found["min"] = least
    return found


def make_auto(org, name, owner, trigger, steps, who=DANA, **extra):
    """POST /api/v2/automations in the CONTRACT §2 shape."""
    payload = {"name": name, "owner": owner, "enabled": True, "builtin": False, "trigger": trigger, "steps": steps,
               **extra}
    reply = org.api("post", "/api/v2/automations", payload, who=who, wave=W3)
    if not 200 <= reply.status < 300:
        raise Unmet(W3, f"create {name}: {reply.status} {reply.excerpt()}")
    return reply


def aid(facts, key):
    data = body(facts, key)
    data = data.get("automation", data) if isinstance(data, dict) else {}
    if not data.get("id"):
        raise Unmet(W3, f"{key}: the answer carries no id: {got(facts, key).excerpt()}")
    return data["id"]


def all_runs(org, ident, who=DANA):
    """Every run of an automation, newest first, through the CONTRACT §2 pages (?limit=25&before=)."""
    found, before = [], None
    for _ in range(40):
        path = f"/api/v2/automations/{ident}/runs?limit=25" + (f"&before={before}" if before else "")
        reply = org.api("get", path, who=who, wave=W3)
        if reply.status != 200:
            return reply
        page = reply.body if isinstance(reply.body, dict) else {}
        found += [run for run in page.get("runs") or [] if isinstance(run, dict)]
        before = page.get("next")
        if not before:
            break
    return Reply(200, {"runs": found}, W3)


def runs_of(org, facts, key):
    return all_runs(org, aid(facts, key))


def read_auto(org, facts, key, who=DANA):
    return org.api("get", f"/api/v2/automations/{aid(facts, key)}", who=who, wave=W3)


def runs(facts, key):
    return body(facts, key)["runs"]


def naming(found, text):
    """The runs (or rows) whose text names `text` (none for an empty text)."""
    return [run for run in found if text and text in canon(run)] if text else []


def walk_steps(run):
    """Every step row of a run, nested ones (inside a path) included."""
    out = []

    def visit(value):
        if isinstance(value, dict):
            if {"id", "type", "status"} <= value.keys():
                out.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit((run or {}).get("steps") or [])
    return out


def step_row(run, sid):
    return next((row for row in walk_steps(run) if row.get("id") == sid), None)


def status_of(run, sid):
    row = step_row(run, sid)
    return row.get("status") if row else None


def path_of(run, sid):
    """The path a paths step took (CONTRACT §2 Paths output {"path": name})."""
    row = step_row(run, sid) or {}
    outputs = row.get("outputs") if isinstance(row.get("outputs"), dict) else {}
    return outputs.get("path")


def started_at(run):
    return when_of((run or {}).get("started_at"))


def one_each(found, names):
    """{name: how many runs name it}, and whether each is exactly one."""
    counts = {name: len(naming(found, name)) for name in names}
    return all(n == 1 for n in counts.values()), counts


# ---------------------------------------------------------------------------------------------- judges
class TimedJudge(ReplayJudge):
    """The replay judge, keeping the fake-clock time of each call (calls[n]["at"]). `answers` maps a key the state
    carries (a mail's subject, a listening item's url) to a scripted answer: a scripted answer stands in for the
    model (fakes.ReplayJudge), so the checks read routing and timing, not the model. A call without a level is asked at
    the company's level (fakes.COMPANY_LEVEL), as the listening judge's is."""

    def __init__(self, org, answers=None):
        super().__init__()
        self.org, self.answers = org, dict(answers or {})

    def __call__(self, state, questions, label=None, level=None):
        now = self.org.now()
        key = state_key(state)
        if key in self.answers:
            self.script.append(self.answers[key])
        try:
            return super().__call__(state, questions, label, level)
        finally:
            self.calls[-1]["at"] = now


class SteadyJudge(TimedJudge):
    """Answers every call with the same middle answer for its questions (workflow 10): the day checks the cut-over
    install, not the judge, so it gets the same answer whatever it is asked."""

    def __call__(self, state, questions, label=None, level=None):
        if level is not None:
            self.script.append(middle_answers(questions))
        return super().__call__(state, questions, label, level)


def state_key(state):
    state = state if isinstance(state, dict) else {}
    message = state.get("message") if isinstance(state.get("message"), dict) else {}
    return state.get("subject") or state.get("url") or message.get("ts") or message.get("text")


def middle_answers(questions):
    pairs = questions.items() if isinstance(questions, dict) else (questions or [])
    answers = {}
    for qid, q in pairs:
        q = q if isinstance(q, dict) else {}
        options = list(q.get("criteria") or [])
        if q.get("type") == "choice" and options:
            answers[qid] = {"choice": options[-1], "confidence": 0.5,
                            "probabilities": {o: round(1 / len(options), 4) for o in options}}
        elif q.get("type") == "score" and options:
            answers[qid] = {"score": 0, "confidence": 0.5, "probabilities": [round(1 / len(options), 4)] * len(options)}
        else:
            answers[qid] = {"noul": 0.5}
    return answers


def triage(bucket, confidence=0.9, money=0.1, ask=0.5, legal=0.05, urgency=1):
    """A mail-triage@1 answer (questions/mail-triage.json)."""
    options = ["needs-owner", "reply", "route", "archive", "watch"]
    rest = round((1 - confidence) / (len(options) - 1), 4)
    levels = [0.1, 0.2, 0.2, 0.1]
    levels[urgency] = 0.4
    return {"bucket": {"choice": bucket, "confidence": confidence,
                       "probabilities": {o: confidence if o == bucket else rest for o in options}},
            "is_ask": {"noul": ask}, "money": {"noul": money}, "legal": {"noul": legal},
            "urgency": {"score": urgency, "confidence": 0.8, "probabilities": levels}}


def item(market=0.05, content=0.05, lead=0.05, vendor=0.05):
    """A listening-item answer (questions/listening-item.json)."""
    return {"market": {"noul": market}, "content": {"noul": content}, "lead": {"noul": lead},
            "vendor_pitch": {"noul": vendor}, "creator": {"noul": 0.05}, "partner": {"noul": 0.05}}


# ================================================================================================ 1 inbox day
DAY1 = (2026, 10, 27)
# Each recorded mail Decide sees, with the bucket the scripted judge answers and the path that bucket takes.
ROUTES1 = {
    "01-mail-cold-pitch": "archive", "02-mail-receipt": "archive", "03-mail-customer-question": "reply",
    "04-mail-refund-request": "owner", "05-mail-investor-update": "owner", "06-mail-legal-demand": "owner",
    "07-mail-calendar-invite": "reply", "09-mail-deadline-ask": "owner", "10-mail-thread-answered": "archive",
    "11-mail-candidate-question": "route", "12-mail-press-inquiry": "owner", "14-mail-product-feedback": "route",
    "15-mail-shipping-notice": "watch", "16-mail-regulator-request": "owner"}
BUCKET = {"archive": "archive", "reply": "reply", "owner": "needs-owner", "route": "route", "watch": "watch"}
FILTERED1 = ("08-mail-newsletter", "13-mail-bounce-notice")   # the recorded ones the Filter catches
WAVES1 = {"a": (6, 8, [f"0{n}" for n in range(1, 6)], range(1, 5)),          # (sent at, batch at, inputs, made mail)
          "b": (10, 13, ["06", "07", "08", "09", "10"], range(5, 9)),
          "c": (15, 17, ["11", "12", "13", "14", "15", "16"], range(9, 13))}
BATCHES1 = {8: 5, 13: 4, 17: 5}   # Decide calls each batch time should make (the 14 not filtered, by wave)
SLACK_AUTOS = {"dm": "slack:dm", "release": "slack:#release", "platform": "slack:#platform",
               "general": "slack:#general", "product": "slack:#product"}
# A DM hears only its owner's automations (backend/automations.py may_read_inbox): Priya's DM to Tico is no one's.
CHANNEL = {"D-dana": "dm", "C-release": "release", "C-platform": "platform",
           "C-general": "general", "C-product": "product"}
RANDOM1 = ["Lunch order closes at noon.", "Anyone seen the blue umbrella?", "Friday demo moved to 3pm.",
           "Coffee machine is fixed.", "Photos from the offsite are up.", "Parking lot repaint on Monday.",
           "Happy birthday to the support team!"]


def mail_id1(stem):
    return "wf1-" + stem


def made1(kind, n):
    return f"wf1-{kind}-{n:02d}"


def inbox_flow():
    owner = {"name": "owner", "match": "all", "when": [cond("d1.bucket", "is", "needs-owner", 0.6)],
             "steps": [act("leave", "leave_for_you")]}
    reply = {"name": "reply", "match": "all", "when": [cond("d1.bucket", "is", "reply", 0.7)],
             "steps": [step("draft", "llm", **{"as": "assistant:" + DANA_ID, "out": "draft",
                                                "prompt": "Write a short, friendly reply to this message."}),
                       act("send", "send_reply", text="{{draft.text}}")]}
    route = {"name": "route", "match": "all", "when": [cond("d1.bucket", "is", "route", 0.7)],
             "steps": [act("hand", "create_task", owner=LEAD_REF, title="{{message.subject}}",
                           body="{{message.snippet}}")]}
    archive = {"name": "archive", "match": "all", "when": [cond("d1.bucket", "is", "archive", 0.7)],
               "steps": [act("drop", "archive")]}
    return [
        step("sort", "filter", rules=[{"when": [cond("message.from", "is", NEWS), cond("message.from", "is", BOUNCE)],
                                       "match": "any", "then": [{"do": "archive", "args": {}}], "stop": True}]),
        step("triage", "decide", set="mail-triage@1", level="low", out="d1"),
        step("route", "paths", paths=[owner, reply, route, archive],
             otherwise={"steps": [act("watch", "label", tag="watch")]}),
    ]


def slack_channels(org):
    """Dana lists the channels her automations hear (Settings > Tools > Slack, POST /api/v2/slack/channels). A channel
    off the list is not heard, so #random stays unlisted."""
    for key in ("release", "platform", "general", "product"):
        reply = org.api("post", "/api/v2/slack/channels", {"channel": "#" + key}, who=DANA, wave=W3)
        if reply.status >= 300:
            raise Unmet(W3, f"list #{key}: {reply.status} {reply.excerpt()}")
    return Reply(200, {"listed": 4}, W3)


def make_slack_autos(org):
    ids = {}
    for key, inbox in SLACK_AUTOS.items():
        made = make_auto(org, f"Slack {inbox}", DANA_REF, {"type": "message", "inbox": inbox}, [
            step("ask", "decide", set="slack-route@1", level="low", out="s1"),
            step("route", "paths", paths=[{"name": "asks", "match": "all", "when": [cond("s1.asks", "is", "yes", 0.6)],
                                           "steps": [act("wake", "wake_bot", bot=LEAD_REF)]}],
                 otherwise={"steps": []})])
        ids[key] = (made.body.get("automation", made.body) if isinstance(made.body, dict) else {}).get("id")
    return Reply(201, {"ids": ids}, W3)


def slack_messages(found):
    msgs = []
    for stem, data in sorted(found.items()):
        if data.get("kind") == "slack":
            m = data["state"]["message"]
            msgs.append({"ts": "wf1-" + stem, "channel": m["channel"], "user": m["from"], "text": m["text"]})
    for n, text in enumerate(RANDOM1, 1):
        msgs.append({"ts": f"wf1-random-{n:02d}", "channel": "C-random", "user": "omar", "text": text})
    return msgs


def feed_slack(org, msgs):
    """Each recorded message reaches the gateway as Socket Mode delivers it: one envelope to `receive`, then the
    gateway's pass (`tick`), then the scheduler runs what the automations queued. The gateway runs in this process
    (backend/slack_gateway.py `Gateway`) against the Slack Web API fake (fakes.FakeSlack)."""
    from backend import slack_gateway
    from .fakes import TEAM, FakeSlack, slack_envelope
    gateway = slack_gateway.Gateway(org.store, FakeSlack(), judge_engine=org.app.state.judge)
    gateway.team_id = TEAM
    gateway.verify_app()
    outcomes = [gateway.receive(slack_envelope(m)) for m in msgs]
    ticked = gateway.tick()
    diag = slack_diag(org, gateway)
    org.scheduler_pass()
    return Reply(200, {"outcomes": outcomes, "events": ticked["events"], "diag": diag}, W3)


def slack_diag(org, gateway):
    """What the gateway's offer pass saw for each stored channel message, read after the tick (diagnostic only)."""
    with org.store.read() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM slack_events ORDER BY received, ts")]
        since = c.execute("SELECT MIN(created_at) FROM automations WHERE trigger_type='message' AND enabled=1 "
                          "AND deleted_at IS NULL AND flow LIKE '%slack:#%'").fetchone()[0]
        autos = [dict(r) for r in c.execute("SELECT name,trigger_type,enabled,created_at,deleted_at,owner FROM automations "
                                            "WHERE flow LIKE '%slack:#%'")]
        senders = {}
        for r in rows:
            if r["state"] == "stored" and r["channel_kind"] == "channel":
                person, _user, denied = gateway.verify_sender(c, r)
                senders[r["ts"]] = {"person": (person or {}).get("id"), "denied": denied}
    stored = [{k: r[k] for k in ("ts", "channel", "channel_kind", "author", "state", "processed", "received")}
              for r in rows if str(r["ts"]).startswith("wf1-")]
    return {"since": since, "autos": autos, "stored": stored, "senders": senders}


def assistant_slug(org, person, who=DANA):
    reply = org.api("get", f"/api/v2/people/{person}/assistant", who=who, wave=W3)
    if reply.status != 200:
        raise Unmet(W3, f"assistant of {person}: {reply.status} {reply.excerpt()}")
    slug = reply.body.get("bot") if isinstance(reply.body, dict) else None
    if slug and slug not in org.bot_slugs:
        org.bot_slugs.append(slug)   # the runner reports the new bot ready, as a runner picks up an added bot
    return slug


def drain_assistant(org, person):
    slug = assistant_slug(org, person)
    if not slug:
        return []
    return drain(org, slug, text="Thanks for writing. Here is a short answer; more soon.")


def mail_pass(org):
    """The mail computer's passes over both mailboxes until nothing is queued: an archive, draft or send the Filter
    or Decide queued is claimed and reported done (CONTRACT §1 Mail actions). Returns what it claimed."""
    claimed = []
    for _ in range(3):
        this = [action for box in (DANA_MAIL, PRIYA_MAIL) for action in mail_computer(org, box)]
        if not this:
            break
        claimed += this
    return claimed


def wf1_steps(org, f):
    found = inputs()
    judge = TimedJudge(org, {found[stem]["state"]["subject"]: triage(BUCKET[path]) for stem, path in ROUTES1.items()
                             if stem in found})
    attach_judge(org, judge)
    f["judge"] = judge
    f["mail_ids"] = {}
    do(org, f, "send_off", W3, inbox_send, org, DANA_MAIL, False)
    do(org, f, "inbox", W3, make_auto, org, "Inbox · " + DANA_MAIL, DANA_REF,
       {"type": "message", "inbox": DANA_MAIL}, inbox_flow(),
       batch={"times": ["08:00", "13:00", "17:00"], "tz": "America/Los_Angeles"})
    do(org, f, "slack_channels", W3, slack_channels, org)
    do(org, f, "slack_autos", W3, make_slack_autos, org)
    for wave, (sent, batch, stems, made) in WAVES1.items():
        at(org, la(DAY1, sent))
        mail = [mail_from(found[stem]["state"], mail_id1(stem))
                for stem in sorted(found) if stem[:2] in stems and found[stem].get("kind") == "mail"]
        for n in made:
            mail.append(made_mail(made1("news", n), NEWS, f"Field Notes weekly #{n}", "This week in field notes.",
                                  org.now(), list_id="fieldnotes-weekly.example", unsubscribe=True))
            mail.append(made_mail(made1("bounce", n), BOUNCE, "Delivery Status Notification (Failure)",
                                  f"Your message to buyer{n}@acme.example could not be delivered.", org.now()))
        f["mail_ids"][wave] = [m["msg_id"] for m in mail]
        do(org, f, "mail_" + wave, W3, publish_mail, org, DANA_MAIL, mail)
        do(org, f, "computer_" + wave, W3, mail_pass, org)
        if wave == "a":
            do(org, f, "slack", W3, feed_slack, org, slack_messages(found))
            at(org, la(DAY1, 7, 55))
            do(org, f, "runs_early", W3, runs_of, org, f, "inbox")
        at(org, la(DAY1, batch))
        do(org, f, "batch_computer_" + wave, W3, mail_pass, org)
        do(org, f, "drafts_" + wave, W3, drain_assistant, org, DANA_ID)
    at(org, la(DAY1, 17, 30))
    do(org, f, "batch_computer_late", W3, mail_pass, org)
    do(org, f, "drafts_late", W3, drain_assistant, org, DANA_ID)
    do(org, f, "runs", W3, runs_of, org, f, "inbox")
    do(org, f, "assistant", W3, assistant_slug, org, DANA_ID)
    do(org, f, "switch", W3, inbox_read, org, DANA_MAIL)
    f["lead_tasks"] = sql(org, "SELECT id,title,body,created_at FROM tasks WHERE owner=?", (LEAD_REF,))
    do(org, f, "slack_runs", W3, wf1_slack_runs, org, f)


def wf1_slack_runs(org, f):
    per = {}
    for key, ident in body(f, "slack_autos")["ids"].items():
        found = all_runs(org, ident)
        if found.status != 200:
            raise Unmet(W3, f"runs of Slack {key}: {found.status} {found.excerpt()}")
        per[key] = found.body["runs"]
    return per


def wf1_filtered(f):
    """26 newsletters and bounces: each one run, archived by the Filter, and never sent to the model."""
    setup(f, "inbox", "mail_a", "mail_b", "mail_c")
    found = runs(f, "runs")
    caught = [i for ids in f["mail_ids"].values() for i in ids if i.startswith(("wf1-news", "wf1-bounce"))]
    caught += [mail_id1(stem) for stem in FILTERED1]
    ok, counts = one_each(found, caught)
    # Archived by the Filter: its rule (then archive, stop) matched, so the Filter step ran and Decide never did.
    not_archived = [i for i in caught if counts.get(i) == 1 and not filtered(naming(found, i)[0])]
    asked = [c for c in mail_calls(f) if (c["state"] or {}).get("from") in (NEWS, BOUNCE)]
    return (ok and len(caught) == 26 and not not_archived and not asked,
            f"{len(caught)} filtered; runs per mail off one: {[i for i, n in counts.items() if n != 1][:5]}; "
            f"no archive step: {not_archived[:5]}; model calls on them: {len(asked)}")


def filtered(run):
    return status_of(run, "sort") == "ok" and status_of(run, "triage") in (None, "skipped") and \
        status_of(run, "route") in (None, "skipped")


def mail_calls(f):
    """The judge calls about mail (a mail-triage state carries `mailbox`), not Slack's."""
    return [c for c in f["judge"].calls if isinstance(c["state"], dict) and "mailbox" in c["state"]]


def wf1_early(f):
    """At 07:55 the Filter has run on wave A (archived) and the five others wait for the 08:00 batch."""
    setup(f, "inbox", "mail_a", "runs_early")
    found = runs(f, "runs_early")
    made = [i for i in f["mail_ids"]["a"] if i.startswith(("wf1-news", "wf1-bounce"))]
    held = [i for i in f["mail_ids"]["a"] if not i.startswith(("wf1-news", "wf1-bounce"))]
    done = {i: [r.get("status") for r in naming(found, i)] for i in made}
    waiting = {i: [r.get("status") for r in naming(found, i)] for i in held}
    ok = all(v == ["ok"] for v in done.values()) and all(v == ["waiting"] for v in waiting.values())
    return ok and len(held) == 5, f"filtered: {done}; waiting for the batch: {waiting}"


def wf1_decided(f):
    """The 14 the Filter left are each decided once, at level low."""
    setup(f, "inbox", "mail_a", "mail_b", "mail_c")
    subjects = Counter(c["state"].get("subject") for c in mail_calls(f))
    want = {inputs()[stem]["state"]["subject"] for stem in ROUTES1}
    levels = {c.get("level") for c in mail_calls(f)}
    wrong = {s: n for s, n in subjects.items() if s not in want or n != 1}
    missing = sorted(want - set(subjects))
    return (not wrong and not missing and levels == {"low"},
            f"{sum(subjects.values())} judge calls; extra or repeated: {wrong}; never asked: {missing[:4]}; "
            f"levels {sorted(map(str, levels))}")


def wf1_batches(f):
    """Decide runs only at the batch times, 08:00, 13:00 and 17:00 Los Angeles: 5, 4 and 5 calls."""
    setup(f, "inbox", "mail_a", "mail_b", "mail_c")
    hours = Counter()
    off = []
    for c in mail_calls(f):
        local = c["at"].astimezone(LA)
        if local.hour in BATCHES1 and local.minute < 10:
            hours[local.hour] += 1
        else:
            off.append(local.strftime("%H:%M"))
    return dict(hours) == BATCHES1 and not off, f"calls by batch hour {dict(hours)}; outside a batch: {off[:6]}"


def wf1_runs_for(f, stems):
    found = runs(f, "runs")
    return {stem: naming(found, mail_id1(stem)) for stem in stems}


def wf1_send_off(f):
    """With the send switch off, both replies are saved as drafts (send_off logged) and the switch still reads off."""
    setup(f, "send_off", "inbox", "switch")
    switch = body(f, "switch")
    replies = [s for s, p in ROUTES1.items() if p == "reply"]
    sent = {}
    for stem, found in wf1_runs_for(f, replies).items():
        row = step_row(found[0], "send") if len(found) == 1 else None
        sent[stem] = (row or {}).get("status"), "send_off" in canon(row or {})
    ok = switch.get("send") is False and all(v == ("ok", True) for v in sent.values())
    return ok, f"switch {switch.get('send')}; send step per reply (status, send_off): {sent}"


def wf1_routed(f):
    """The two mails Decide routes become one Engineering Lead task each, and nothing else does."""
    setup(f, "inbox", "mail_a", "mail_b", "mail_c")
    found = inputs()
    tasks = f["lead_tasks"]
    counts = {}
    for stem, path in ROUTES1.items():
        subject = found[stem]["state"]["subject"]
        counts[stem] = sum(1 for t in tasks if subject in (t["title"] or ""))
    want = {s: (1 if p == "route" else 0) for s, p in ROUTES1.items()}
    wrong = {s: n for s, n in counts.items() if n != want[s]}
    return not wrong, f"lead tasks per mail off what the paths want: {wrong}" if wrong else "one task per routed mail"


def wf1_owner(f):
    """Owner-only mail takes the owner path and is left for Dana; the shipping notice takes otherwise."""
    setup(f, "inbox", "mail_a", "mail_b", "mail_c")
    want = {s: ("otherwise" if p == "watch" else p) for s, p in ROUTES1.items() if p in ("owner", "watch")}
    got_ = {}
    for stem, found in wf1_runs_for(f, want).items():
        run = found[0] if len(found) == 1 else None
        got_[stem] = (path_of(run, "route"), status_of(run, "leave") if want[stem] == "owner" else status_of(run, "watch"))
    wrong = {s: v for s, v in got_.items() if v != (want[s], "ok")}
    return not wrong, f"(path, step status) off: {wrong}" if wrong else f"{len(want)} left or labelled"


def wf1_assistant(f):
    """Both drafts are written in a turn on Dana's Assistant (CONTRACT §2 Turn origin), not in a sender's session."""
    setup(f, "inbox", "mail_a", "mail_b", "mail_c")
    slug = got(f, "assistant")
    if not slug:
        return False, "Dana has no Assistant after the drafts"
    turns = {}
    for stem, found in wf1_runs_for(f, [s for s, p in ROUTES1.items() if p == "reply"]).items():
        origin = turn_of({"steps": [step_row(found[0], "draft")]}) if len(found) == 1 else None
        turns[stem] = (origin or {}).get("bot"), status_of(found[0], "draft") if found else None
    ok = all(v == (slug, "ok") for v in turns.values())
    return ok, f"Assistant {slug}; (turn bot, draft status) per reply: {turns}"


def wf1_slack(f):
    """Each recorded Slack message runs its channel's automation once; the 7 in #random run nothing."""
    setup(f, "slack_autos")
    setup(f, "slack")
    per = got(f, "slack_runs")
    msgs = slack_messages(inputs())
    wrong = {}
    for m in msgs:
        key = CHANNEL.get(m["channel"])
        counts = {k: len(naming(per[k], m["ts"])) for k in per}
        want = {k: (1 if k == key else 0) for k in per}
        if counts != want:
            wrong[m["ts"]] = counts
    if not wrong:
        return True, "each Slack message ran its channel's automation once"
    states = {e.get("event_id"): e.get("state") for e in (got(f, "slack").body.get("events") or [])}
    gave = {ts: states.get("Ev-" + ts) for ts in wrong}
    diag = got(f, "slack").body.get("diag") or {}
    return False, f"runs per automation off for {len(wrong)}: {wrong}; gateway outcome {gave}; diag {diag}"


# ================================================================================================ 2 task flood
LEAD_CAP, DANA_CAP = 4, 2


def wf2_caps(org):
    for sender in (CLAUDE_REF, OPENAI_REF):
        for receiver, per_day in ((LEAD_REF, LEAD_CAP), (DANA_REF, DANA_CAP)):
            reply = put_cap(org, sender, receiver, per_day)
            if not 200 <= reply.status < 300:
                raise Unmet(W2, f"cap {sender}>{receiver}: {reply.status} {reply.excerpt()}")
    return reply


def wf2_steps(org, f):
    # A Filter with no rules: each task the lead receives makes a run, and nothing acts on the task for the lead
    # (an action could read as the lead acting, which would change what closing early counts).
    do(org, f, "lead_auto", W3, make_auto, org, "Lead intake", LEAD_REF, {"type": "task_received", "to": LEAD_REF},
       [step("gate", "filter", rules=[])])
    do(org, f, "caps", W2, wf2_caps, org)
    f["sent"], f["title_of"] = [], {}
    for n in range(1, 8):
        for who, name in ((CLAUDE, "claude"), (OPENAI, "openai")):
            key, title = f"{name}>lead{n}", f"{name.title()} ask {n} for the lead"
            do(org, f, key, W2, send, org, who, LEAD_ID, title)
            f["sent"].append(key)
            f["title_of"][key] = title
    for n in range(1, 5):
        for who, name in ((CLAUDE, "claude"), (OPENAI, "openai")):
            key, title = f"{name}>dana{n}", f"{name.title()} question {n} for Dana"
            do(org, f, key, W2, send, org, who, DANA_ID, title)
            f["sent"].append(key)
            f["title_of"][key] = title
    # Claude closes its first ask before the lead acts on it: it no longer counts, so a resubmit fits.
    do(org, f, "closed_early", W2, close_task, org, f, "claude>lead1", CLAUDE)
    do(org, f, "claude>lead_again", W2, send, org, CLAUDE, LEAD_ID, "Claude ask 1 again for the lead")
    do(org, f, "claude>lead_over", W2, send, org, CLAUDE, LEAD_ID, "Claude ask 8 for the lead")
    # Dana acts on one of OpenAI's questions, then OpenAI closes it: acted on, so it still counts.
    do(org, f, "acted", W2, comment, org, f, "openai>dana1", DANA, "Looking at this now.")
    do(org, f, "closed_late", W2, close_task, org, f, "openai>dana1", OPENAI)
    do(org, f, "openai>dana_again", W2, send, org, OPENAI, DANA_ID, "OpenAI question 1 again for Dana")
    do(org, f, "caps_read", W2, get_caps, org)
    do(org, f, "needs_you", W2, org.api, "get", "/api/v2/needs-you", who=DANA, wave=W2)
    org.scheduler_pass()
    do(org, f, "lead_runs", W3, runs_of, org, f, "lead_auto")
    f["titles"] = [r["title"] for r in sql(org, "SELECT title FROM tasks WHERE requester IN (?,?)",
                                           (CLAUDE_REF, OPENAI_REF))]


def wf2_refused_keys():
    return {f"{n}>lead{i}" for n in ("claude", "openai") for i in range(LEAD_CAP + 1, 8)} | \
           {f"{n}>dana{i}" for n in ("claude", "openai") for i in range(DANA_CAP + 1, 5)}


def capped(reply):
    data = reply.body if isinstance(reply.body, dict) else {}
    return reply.status == 409 and data.get("error") == "task_cap"


def wf2_flood(f):
    """22 sends: 12 accepted, the 10 over a cap refused with task_cap."""
    setup(f, "caps")
    refused = {k for k in f["sent"] if capped(got(f, k))}
    accepted = {k for k in f["sent"] if got(f, k).status in (200, 201)}
    want = wf2_refused_keys()
    return (refused == want and len(accepted) == 12,
            f"{len(accepted)} accepted, {len(refused)} refused; refused off what the caps want: "
            f"{sorted(refused ^ want)[:6]}")


def wf2_no_row(f):
    """A refused send writes no task."""
    setup(f, "caps")
    refused = [k for k in wf2_refused_keys() if capped(got(f, k))]
    if not refused:
        return False, "no send was refused"
    written = [f["title_of"][k] for k in refused if f["title_of"][k] in f["titles"]]
    return not written, f"refused sends with a task row: {written}" if written else "no refused send left a row"


def wf2_close_early(f):
    """Closing before the lead acted frees the slot: the resubmit fits, and the next one is refused at count 4."""
    setup(f, "caps", "closed_early")
    again, over = got(f, "claude>lead_again"), got(f, "claude>lead_over")
    count = over.body.get("count") if isinstance(over.body, dict) else None
    return (again.status in (200, 201) and capped(over) and count == LEAD_CAP,
            f"resubmit {again.status}; next {over.status} count {count}")


def wf2_close_late(f):
    """Closing after Dana acted still counts: the resubmit is refused at count 2."""
    setup(f, "caps", "acted", "closed_late")
    again = got(f, "openai>dana_again")
    count = again.body.get("count") if isinstance(again.body, dict) else None
    return capped(again) and count == DANA_CAP, f"resubmit {again.status} count {count}: {again.excerpt()}"


def wf2_needs_you(f):
    """Dana's Needs you holds the builders' 3 open questions; the closed one and the refused ones are not there."""
    setup(f, "caps", "needs_you", "closed_late")
    seen = canon(body(f, "needs_you"))
    open_ = [task_id_of(got(f, k)) for k in ("claude>dana1", "claude>dana2", "openai>dana2")]
    closed = task_id_of(got(f, "openai>dana1"))
    missing = [t for t in open_ if not t or t not in seen]
    shown = bool(closed) and closed in seen
    return not missing and bool(closed) and not shown, f"open questions missing: {missing}; closed shown: {shown}"


def wf2_budget_tab(f):
    """The Budget tab shows today's count and closed per cap (CONTRACT §1 read)."""
    setup(f, "caps", "caps_read")
    want = {(CLAUDE_REF, LEAD_REF): (4, 1), (OPENAI_REF, LEAD_REF): (4, 0), (CLAUDE_REF, DANA_REF): (2, 0),
            (OPENAI_REF, DANA_REF): (2, 0)}
    shown = {(r.get("sender"), r.get("receiver")): (r.get("count"), r.get("closed"))
             for r in rows(body(f, "caps_read")) if isinstance(r, dict)}
    wrong = {f"{s}>{r}": (shown.get((s, r)), w) for (s, r), w in want.items() if shown.get((s, r)) != w}
    return not wrong, f"(shown, wanted) count/closed: {wrong}" if wrong else "count and closed as wanted"


def wf2_lead_runs(f):
    """The lead's automation ran once per accepted task to the lead (9), never for a refused one."""
    setup(f, "caps", "lead_auto")
    found = runs(f, "lead_runs")
    accepted = [k for k in f["sent"] + ["claude>lead_again", "claude>lead_over"]
                if ">lead" in k and got(f, k).status in (200, 201)]
    ok, counts = one_each(found, [task_id_of(got(f, k)) for k in accepted])
    return ok and len(found) == len(accepted) == 9, f"{len(found)} runs for {len(accepted)} accepted lead tasks"


# ================================================================================================ 3 briefing
LIBRARIAN = "librarian"
BRIEF = "Morning briefing: Northwind raised a $24m Series B and is doubling its sales team."
DAY3 = (2026, 10, 20)
ITEMS3 = {"25-listening-competitor-funding": item(market=0.92), "26-listening-owner-question": item(content=0.85, lead=0.8),
          "27-listening-vendor-promo": item(market=0.3, vendor=0.9), "28-listening-hiring-post": item()}


def wf3_listen(org, found):
    """One listening run per source, saved through the ingest route as the listening runner saves it."""
    from backend.store import H
    by_source = {}
    for stem in ITEMS3:
        s = found[stem]["state"]
        # The recorded fixture names a source "job board"; a listening source is a slug (backend/listening.py SOURCE),
        # so the run is saved as "jobboard".
        slug = re.sub(r"\s+", "", str(s["source"]).lower())
        by_source.setdefault(slug, []).append(
            {"native_id": stem, "url": s["url"], "author": s["author"], "content": s["content"],
             "published_at": s["published_at"]})
    reply = None
    for source, items in by_source.items():
        reply = org.api("post", "/api/v2/listening/runs", {
            "source": source, "query": "project tracking for small teams", "status": "ok", "started_at": H.now(),
            "pages_read": 1, "items_seen": len(items), "note": "", "items": items}, who=DANA, wave=W6)
        if not 200 <= reply.status < 300:
            raise Unmet(W6, f"listening run {source}: {reply.status} {reply.excerpt()}")
    return reply


def wf3_steps(org, f):
    found = inputs()
    url = found["25-listening-competitor-funding"]["state"]["url"]
    f["url"] = url
    attach_judge(org, TimedJudge(org, {found[stem]["state"]["url"]: answer for stem, answer in ITEMS3.items()}))
    do(org, f, "destination", W6, org.api, "put", "/api/v2/listening/destinations/market-watch",
       {"category": "market", "threshold": 0.5, "receiver": "bot:" + LIBRARIAN, "readers": [],
        "what": "Competitors and market moves"}, who=DANA, wave=W6)
    do(org, f, "listen_auto", W3, make_auto, org, "Market watch to the Librarian", "bot:" + LIBRARIAN,
       {"type": "listening", "destination": "market-watch"},
       [step("add", "bot_turn", bot=LIBRARIAN, prompt="A listening match: {{listening.url}}\n\n{{listening.content}}\n\n"
                                                      "Add what it says to the market graph, citing it.")])
    do(org, f, "brief_auto", W3, make_auto, org, "Daily market briefing", "bot:" + LIBRARIAN,
       {"type": "schedule", "cron": "0 7 * * *", "tz": "America/Los_Angeles"},
       [step("brief", "bot_turn", bot=LIBRARIAN, prompt="Write today's market briefing from the graph."),
        act("post", "post_update", to=["market", "updates"], text="{{brief.text}}")])
    do(org, f, "listen", W6, wf3_listen, org, found)
    do(org, f, "judge", W6, org.api, "post", "/api/v2/listening/judge", {"limit": 20}, who=DANA, wave=W6)
    org.scheduler_pass()
    do(org, f, "graph_before", W6, org.api, "get", "/api/v2/market/graph", who=DANA, wave=W6)

    def add_entity(token, attempt):
        # The Librarian files the match as evidence, then cites it on the entity: the graph needs an evidence id
        # (backend/market.py entity_create, evidence_ids).
        filed = org.api("post", "/api/v2/market/evidence", {"source_url": url, "source_kind": "news",
                                                            "quote": "Northwind raised a $24m Series B."},
                        who=token, wave=W6)
        if not 200 <= filed.status < 300:
            raise Unmet(W6, f"evidence: {filed.status} {filed.excerpt()}")
        return org.api("post", "/api/v2/market/entities", {"type": "company", "name": "Northwind",
                                                           "evidence_ids": [filed.body["evidence"]["id"]],
                                                           "properties": {"source": url}}, who=token, wave=W6)
    do(org, f, "librarian", W3, drain, org, LIBRARIAN, add_entity, text="Added Northwind to the graph.")
    do(org, f, "graph_after", W6, org.api, "get", "/api/v2/market/graph", who=DANA, wave=W6)
    do(org, f, "listen_runs", W3, runs_of, org, f, "listen_auto")
    at(org, la(DAY3, 6, 55))
    do(org, f, "brief_early", W3, runs_of, org, f, "brief_auto")
    at(org, la(DAY3, 7, 0))
    do(org, f, "brief_turn", W3, drain, org, LIBRARIAN, text=BRIEF)
    org.scheduler_pass()
    do(org, f, "brief_runs", W3, runs_of, org, f, "brief_auto")
    do(org, f, "market_brief", W6, org.api, "get", "/api/v2/market/briefings/latest", who=DANA, wave=W6)
    do(org, f, "updates", W6, org.api, "get", "/api/v2/updates", who=DANA, wave=W6)


def wf3_one_run(f):
    """The one market match starts one run; the three misses start none. A run's trigger ref has no named format
    (CONTRACT section 2), so the run is matched on its kind: a listening trigger."""
    setup(f, "listen_auto", "destination", "listen", "judge")
    found = runs(f, "listen_runs")
    listened = [run for run in found if str(run.get("trigger_ref") or "").startswith("listening:")]
    return len(found) == 1 and len(listened) == 1, f"{len(found)} runs; listening runs: {len(listened)}"


def wf3_turn(f):
    """The match reaches the Librarian as a turn about that item."""
    setup(f, "listen_auto", "judge")
    found = runs(f, "listen_runs")
    origin = turn_of(found[0]) if found else None
    turns = got(f, "librarian")
    about = [t for t in turns if f["url"] in t["about"]]
    return ((origin or {}).get("bot") == LIBRARIAN and len(about) == 1,
            f"turn origin {origin}; Librarian turns about the item: {len(about)} of {len(turns)}")


def wf3_graph_before(f):
    """Nothing reached the market graph before the Librarian's turn."""
    setup(f, "listen_auto", "judge", "graph_before")
    seen = canon(body(f, "graph_before"))
    return f["url"] not in seen and "Northwind" not in seen, "graph before the turn names the item" \
        if f["url"] in seen else "graph unchanged before the turn"


def wf3_graph_after(f):
    """After its turn the graph holds the Librarian's entity, sourced to the item's url."""
    setup(f, "listen_auto", "judge", "graph_after")
    turns = got(f, "librarian")
    wrote = [t["did"] for t in turns if isinstance(t["did"], Reply) and 200 <= t["did"].status < 300]
    seen = canon(body(f, "graph_after"))
    return bool(wrote) and f["url"] in seen and "Northwind" in seen, f"writes ok: {len(wrote)}; " \
        f"graph names the url: {f['url'] in seen}"


def wf3_seven(f):
    """The briefing runs at 07:00 Los Angeles on the fake clock, and not before."""
    setup(f, "brief_auto")
    early, found = runs(f, "brief_early"), runs(f, "brief_runs")
    times = [started_at(r).astimezone(LA).strftime("%H:%M") for r in found if started_at(r)]
    return not early and len(found) == 1 and times[:1] in (["07:00"], ["07:01"]), \
        f"runs at 06:55: {len(early)}; after 07:00: {len(found)} at {times}"


def wf3_market(f):
    """The briefing is Market's latest briefing."""
    setup(f, "brief_auto", "brief_runs", "market_brief")
    return BRIEF[:40] in canon(body(f, "market_brief")), got(f, "market_brief").excerpt()


def wf3_updates(f):
    """The briefing is posted to Updates."""
    setup(f, "brief_auto", "brief_runs", "updates")
    return BRIEF[:40] in canon(body(f, "updates")), "Updates names the briefing" \
        if BRIEF[:40] in canon(body(f, "updates")) else "no briefing in Updates"


# ================================================================================================ 4 stale PR nudge
DAY4 = (2026, 10, 21)
STALE4, MERGED4 = (100, 101, 102, 104), 103


def wf4_open(org, f):
    """Five builder tasks, four PRs opened now, one 90 minutes later; PR 103 merges after two hours."""
    ids = []
    for n, owner in enumerate((CLAUDE_ID, OPENAI_ID, CLAUDE_ID, OPENAI_ID, CLAUDE_ID), 1):
        made = send(org, DANA, owner, f"Change {n}")
        if made.status >= 300:
            raise Unmet(W2, f"task {n}: {made.status} {made.excerpt()}")
        ids.append(task_id_of(made))
    org.connect_github()
    prs = [org.open_pr(ids[n], token=DANA) for n in range(4)]
    org.advance(dt.timedelta(minutes=20))   # CI finishes on the first heads
    at(org, org.now() + dt.timedelta(minutes=70))
    prs.append(org.open_pr(ids[4], token=DANA))
    org.advance(dt.timedelta(minutes=20))
    org.merge(MERGED4)
    return Reply(200, {"prs": prs}, W3)


def wf4_tasks(org):
    return sql(org, "SELECT id,title,body,created_at FROM tasks WHERE owner=?", (LEAD_REF,))


def wf4_steps(org, f):
    do(org, f, "prs", W3, wf4_open, org, f)
    do(org, f, "links", W3, sql, org, "SELECT number, kind, state, activity_at, created_at FROM task_links ORDER BY number")
    at(org, utc(DAY4, 9, 30))
    do(org, f, "auto", W3, make_auto, org, "Stale PR nudge", LEAD_REF, {"type": "schedule", "cron": "0 * * * *", "tz": "UTC"},
       [step("stale", "find", source="prs", query="", idle_hours=24, since="last_run", limit=20),
        act("nudge", "create_task", owner=LEAD_REF, title="Nudge stale PR {{item.url}}",
            body="This pull request has had no activity for a day: {{item.url}}")])
    for hour in (10, 11, 12):
        at(org, utc(DAY4, hour))
        f[f"tasks_{hour}"] = wf4_tasks(org)
        if hour == 10:
            at(org, utc(DAY4, 10, 30))
            do(org, f, "push", W3, org.push, 102)
            org.advance(dt.timedelta(minutes=20))
    do(org, f, "runs", W3, runs_of, org, f, "auto")


def nudges(tasks, number):
    url = f"/pull/{number}"
    return [t for t in tasks if re.search(re.escape(url) + r"\b", (t["title"] or "") + " " + (t["body"] or ""))]


def wf4_hourly(f):
    """The sweep runs at 10:00, 11:00 and 12:00 UTC."""
    setup(f, "prs", "auto")
    hours = sorted(started_at(r).hour for r in runs(f, "runs") if started_at(r))
    return hours == [10, 11, 12], f"run hours {hours}"


def find_diag(f):
    """Each run's find step as the run recorded it, and the PR rows the find reads (for a failure's reason)."""
    out = [json.dumps(step_row(r, "stale"), default=str)[:240] for r in runs(f, "runs")]
    links = f.get("links")
    return f"find steps {out}; PR rows {links[:6] if isinstance(links, list) else links}"


def wf4_one_each(f):
    """Each stale PR (100, 101, 102, 104) gets exactly one nudge over the three runs; the push to 102 adds none."""
    setup(f, "prs", "auto")
    counts = {n: len(nudges(f["tasks_12"], n)) for n in STALE4}
    ok = all(v == 1 for v in counts.values())
    return ok, f"nudges per PR {counts}" + ("" if ok else f"; {find_diag(f)}")


def wf4_merged(f):
    """The merged PR 103 gets no nudge."""
    setup(f, "prs", "auto")
    n = len(nudges(f["tasks_12"], MERGED4))
    return n == 0 and any(nudges(f["tasks_12"], s) for s in STALE4), f"nudges for 103: {n}"


def wf4_hour_three(f):
    """10:00 nudges 100-102, 11:00 nudges 104 (24 hours after it opened), 12:00 makes nothing new."""
    setup(f, "prs", "auto")
    per = {h: {n for n in STALE4 if nudges(f[f"tasks_{h}"], n)} for h in (10, 11, 12)}
    want = {10: {100, 101, 102}, 11: {100, 101, 102, 104}, 12: {100, 101, 102, 104}}
    return per == want and len(runs(f, "runs")) == 3, f"PRs nudged by hour {per}"


# ================================================================================================ 5 Stripe failure
COO = "coo"   # the Assistant bot (backend/config.py assistant_bot): every person's Assistant is this bot
BILLING = "billing"
STRIPE_SECRET = "whsec_acmepaymentsdesk0001"
EVENT5 = {"id": "evt_1Pay0001", "type": "invoice.payment_failed",
          "data": {"object": {"customer": "cus_acme_042", "amount_due": 4900, "currency": "usd"}}}


def wf5_trigger(org):
    made = org.api("post", "/api/v2/triggers", {"name": "Stripe payments", "id": "stripe-payments"}, wave=W3)
    if made.status >= 300:
        raise Unmet(W3, f"trigger: {made.status} {made.excerpt()}")
    signing = org.api("post", "/api/v2/triggers/stripe-payments/signing", {"preset": "stripe", "secret": STRIPE_SECRET},
                      wave=W3)
    if signing.status >= 300:
        raise Unmet(W3, f"signing: {signing.status} {signing.excerpt()}")
    return made


def endpoint(org, name, url, events):
    made = org.api("post", "/api/v2/webhooks", {"name": name, "url": url, "events": events}, wave=W3)
    if made.status >= 300:
        raise Unmet(W3, f"endpoint {name}: {made.status} {made.excerpt()}")
    return made


def endpoint_id(facts, key):
    data = body(facts, key)
    return (data.get("webhook") or data).get("id")


def post_hook(org, url, payload, secret):
    """A delivery to the incoming address as Stripe sends it, signed with `secret` at the fake clock's time."""
    raw = json.dumps(payload).encode()
    headers = {"Stripe-Signature": stripe_sig(secret, raw, int(org.now().timestamp()))}
    response = hook(org.client, url, raw, headers)
    try:
        data = response.json()
    except ValueError:
        data = response.text
    reply = Reply(response.status_code, data, W3)
    org.log.append(f"POST hook {url.rsplit('/', 2)[-2]} -> {reply.status} {reply.excerpt()}")
    return reply


def billing_sending(org):
    """Dana, who manages Billing, turns its sending on. A bot's webhook to an outside address needs that switch, and a
    bot may not set it itself (backend/mail_settings.py: only a person who manages the bot)."""
    reply = org.api("post", f"/api/v2/bots/{BILLING}/mail-settings", {"outbound_send": True}, wave=W3)
    if reply.status >= 300:
        raise Unmet(W3, f"sending: {reply.status} {reply.excerpt()}")
    return reply


def wf5_steps(org, f):
    receiver = Receiver()
    f["seen"] = []
    do(org, f, "trigger", W3, wf5_trigger, org)
    do(org, f, "endpoint", W3, endpoint, org, "Payments desk", "https://hooks.acme.example/payments",
       ["custom.payment_failed"])
    do(org, f, "sending", W3, billing_sending, org)
    do(org, f, "auto", W3, lambda: make_auto(
        org, "Failed payment", "bot:" + BILLING, {"type": "webhook", "id": "stripe-payments"},
        [act("ticket", "create_task", owner="bot:" + BILLING,
             title="Payment failed for {{webhook.data.data.object.customer}}", body="Stripe event {{webhook.data.id}}"),
         step("work", "bot_turn", bot=BILLING, prompt="Retry the payment and write to the customer."),
         act("tell", "call_webhook", webhook=endpoint_id(f, "endpoint"), type="custom.payment_failed",
             data={"customer": "{{webhook.data.data.object.customer}}", "event": "{{webhook.data.id}}"})]))
    url = (body(f, "trigger").get("trigger") or {}).get("url") if not isinstance(f["trigger"], Unmet) else None
    if not url:
        f["forged"] = f["signed"] = f["retry"] = Unmet(W3, "the trigger has no address")
    else:
        do(org, f, "forged", W3, post_hook, org, url, EVENT5, "whsec_notthedesksecret00")
        org.scheduler_pass()
        do(org, f, "runs_forged", W3, runs_of, org, f, "auto")
        do(org, f, "signed", W3, post_hook, org, url, EVENT5, STRIPE_SECRET)
        do(org, f, "retry", W3, post_hook, org, url, EVENT5, STRIPE_SECRET)
    org.scheduler_pass()
    do(org, f, "billing", W3, drain, org, BILLING, text="Retried the card; wrote to the customer.")
    org.scheduler_pass()
    do(org, f, "runs", W3, runs_of, org, f, "auto")
    f["tasks"] = sql(org, "SELECT id,title,body FROM tasks WHERE owner=?", ("bot:" + BILLING,))
    do(org, f, "delivered", W3, deliver, org.app, receiver)
    f["seen"] = [(r.headers.get("webhook-id") or "", r.content.decode(errors="replace")) for r in receiver.seen]


def wf5_forged(f):
    """A forged delivery is refused (401) and starts no run."""
    setup(f, "auto")
    forged = got(f, "forged")
    return forged.status == 401 and not runs(f, "runs_forged"), f"forged {forged.status}; runs after it: " \
        f"{len(runs(f, 'runs_forged'))}"


def wf5_once(f):
    """The signed delivery starts one run; Stripe's retry of the same event starts none."""
    setup(f, "auto", "signed")
    retry = got(f, "retry")
    found = runs(f, "runs")
    return len(found) == 1 and retry.status in (200, 202) and "duplicate" in canon(retry.body), \
        f"{len(found)} runs; retry {retry.status} {retry.excerpt()}"


def wf5_task(f):
    """One Billing task names the customer."""
    setup(f, "auto", "signed")
    named = [t for t in f["tasks"] if "cus_acme_042" in (t["title"] or "") + (t["body"] or "")]
    return len(named) == 1, f"{len(named)} Billing tasks name the customer"


def wf5_turn(f):
    """The Bot turn ran on Billing (CONTRACT §2 Turn origin)."""
    setup(f, "auto", "signed")
    found = runs(f, "runs")
    origin = turn_of({"steps": [step_row(found[0], "work")]}) if found else None
    return (origin or {}).get("bot") == BILLING and bool(got(f, "billing")), \
        f"turn origin {origin}; Billing turns {len(got(f, 'billing'))}"


def wf5_receiver(f):
    """The payments desk gets one request carrying the event and the customer."""
    setup(f, "auto", "signed", "endpoint", "sending")
    hits = [text for _, text in f["seen"] if "cus_acme_042" in text and "custom.payment_failed" in text]
    return len(hits) == 1 and len(f["seen"]) == 1, f"{len(f['seen'])} requests; with the event: {len(hits)}"


# ================================================================================================ 6 release
def release_sending(org):
    """The owner turns on the Release Manager's outbound sending: a bot's webhook to an outside address needs that
    switch (backend/webhooks.py), and a bot may not set it itself (as billing_sending does for Billing)."""
    reply = org.api("post", f"/api/v2/bots/{RELEASE_ID}/mail-settings", {"outbound_send": True}, wave=W3)
    if reply.status >= 300:
        raise Unmet(W3, f"sending: {reply.status} {reply.excerpt()}")
    return reply


def wf6_steps(org, f):
    receiver = Receiver()
    do(org, f, "endpoint", W3, endpoint, org, "Release feed", "https://hooks.acme.example/releases",
       ["custom.release_shipped"])
    do(org, f, "sending", W3, release_sending, org)
    released = {"type": "event", "name": "release.published"}
    do(org, f, "qa", W3, make_auto, org, "Post-release QA", "bot:" + RELEASE_ID, released,
       [step("check", "bot_turn", bot=RELEASE_ID, prompt="Smoke-test the release and report what broke.")])
    do(org, f, "notify", W3, lambda: make_auto(org, "Release notify", "bot:" + RELEASE_ID, released, [
        act("post", "post_update", to=["updates"], text="Released {{event.subject}}"),
        act("tell", "call_webhook", webhook=endpoint_id(f, "endpoint"), type="custom.release_shipped",
            data={"release": "{{event.subject}}"})]))
    do(org, f, "emit_1", W3, publish_release, org, "v1.4.0")
    org.scheduler_pass()
    do(org, f, "qa_runs_1", W3, runs_of, org, f, "qa")
    do(org, f, "notify_runs_1", W3, runs_of, org, f, "notify")
    do(org, f, "emit_dup", W3, publish_release, org, "v1.4.0")
    org.scheduler_pass()
    do(org, f, "qa_runs_dup", W3, runs_of, org, f, "qa")
    do(org, f, "notify_runs_dup", W3, runs_of, org, f, "notify")
    do(org, f, "qa_turns", W3, drain, org, RELEASE_ID, text="Smoke test passed on v1.4.0.")
    org.scheduler_pass()
    do(org, f, "updates", W3, org.api, "get", "/api/v2/updates", who=DANA, wave=W3)
    do(org, f, "delivered", W3, deliver, org.app, receiver)
    f["seen_1"] = [r.content.decode(errors="replace") for r in receiver.seen]
    at(org, org.now() + dt.timedelta(days=1))
    do(org, f, "emit_2", W3, publish_release, org, "v1.4.1")
    org.scheduler_pass()
    do(org, f, "qa_turns_2", W3, drain, org, RELEASE_ID, text="Smoke test passed on v1.4.1.")
    org.scheduler_pass()
    do(org, f, "qa_runs_2", W3, runs_of, org, f, "qa")
    do(org, f, "notify_runs_2", W3, runs_of, org, f, "notify")


def wf6_first(f):
    """v1.4.0 starts one run of each automation."""
    setup(f, "qa", "notify")
    counts = (len(runs(f, "qa_runs_1")), len(runs(f, "notify_runs_1")))
    return counts == (1, 1), f"QA, notify runs {counts}"


def wf6_dup(f):
    """The repeated v1.4.0 event starts nothing more."""
    setup(f, "qa", "notify", "qa_runs_1")
    before = (len(runs(f, "qa_runs_1")), len(runs(f, "notify_runs_1")))
    after = (len(runs(f, "qa_runs_dup")), len(runs(f, "notify_runs_dup")))
    return before == after == (1, 1), f"runs before {before}, after the repeat {after}"


def wf6_qa_turn(f):
    """The QA Bot turn runs on the Release Manager, about v1.4.0."""
    setup(f, "qa")
    found = runs(f, "qa_runs_2")
    origin = turn_of(found[-1]) if found else None
    turns = got(f, "qa_turns")
    return (origin or {}).get("bot") == RELEASE_ID and len(turns) >= 1, \
        f"turn origin {origin}; Release Manager turns {len(turns)}"


def wf6_told(f):
    """Updates names v1.4.0, and the release feed got it once."""
    setup(f, "notify", "updates")
    posted = "v1.4.0" in canon(body(f, "updates"))
    hits = [t for t in f["seen_1"] if "v1.4.0" in t]
    ok = posted and len(hits) == 1
    tell = [json.dumps(step_row(r, "tell"), default=str)[:300] for r in runs(f, "notify_runs_1")]
    return ok, f"Updates names it: {posted}; feed requests naming it: {len(hits)}" + ("" if ok else f"; tell step {tell}")


def wf6_next(f):
    """v1.4.1 the next day runs both again: two runs each, the newest about v1.4.1."""
    setup(f, "qa", "notify")
    qa, notify = runs(f, "qa_runs_2"), runs(f, "notify_runs_2")
    newest = [len(naming(found[:1], "v1.4.1")) for found in (qa, notify)]
    return len(qa) == len(notify) == 2 and newest == [1, 1], f"runs {len(qa)}, {len(notify)}; newest names v1.4.1: " \
        f"{newest}"


# ================================================================================================ 7 health
BOTOPS, HEALTH = "botops", "health-monitor"


def builtin_health(org):
    """The Health Monitor shows as a built-in automation (CONTRACT §4, PLAN §8), so wave 5."""
    reply = org.api("get", "/api/v2/automations", who=DANA, wave=W5)
    if reply.status != 200:
        raise Unmet(W5, f"list: {reply.status} {reply.excerpt()}")
    found = [r for r in rows(reply.body) if r.get("builtin") is True and "health" in str(r.get("name")).lower()]
    if not found:
        raise Unmet(W5, "no built-in Health automation in the list")
    return Reply(200, found[0], W5)


def wf7_steps(org, f):
    # The Health built-in is the cutover's mirror of the health-monitor bot (CONTRACT section 6). The cutover ran at
    # startup with nobody yet to own it; the automations tick (the Org's first, then this one) catches up for Dana.
    at(org, org.now() + dt.timedelta(minutes=2))
    do(org, f, "health", W5, builtin_health, org)
    do(org, f, "monitor", W5, org.api, "get", "/api/v2/health-monitor", who=DANA, wave=W5)
    # The run guard trips on its own (backend/turn_budgets.py): WF9's loop from one seed task. Its held notices are the
    # hub's (CONTRACT section 1). Health's event path is a later PR (CONTRACT section 6).
    do(org, f, "budget", W2, wf9_budget, org)
    do(org, f, "seed", W2, send, org, DANA, OPENAI_ID, "Start the loop")
    do(org, f, "loop", W2, wf9_loop, org)
    org.scheduler_pass()
    do(org, f, "notices", W2, hub_holds, org)


def wf7_builtin(f):
    """Health is a built-in automation, off the org chart."""
    data = body(f, "health")
    return data.get("builtin") is True, f"{data.get('name')} builtin {data.get('builtin')}"


def wf7_enabled(f):
    """The Health built-in is enabled exactly when the Health monitor is on, as the convert set them."""
    setup(f, "health", "monitor")
    enabled = body(f, "health").get("enabled")
    on = bool(body(f, "monitor").get("on"))
    return enabled is not None and bool(enabled) == on, f"built-in enabled {enabled}; monitor on {on}"


def wf7_notices(f):
    """A run-guard trip sends one held notice to BotOps and one to the Health monitor: hub notices from the keeper, kind
    notice, refs.held naming the bot (CONTRACT section 1)."""
    setup(f, "budget", "seed", "notices")   # "loop" is a list of turns, read by wf9_loop; it is not a reply
    held = [r for r in body(f, "notices") if OPENAI_ID in str(r["refs"])]
    to_botops = [r for r in held if "botops" in str(r["to_actor"])]
    to_health = [r for r in held if "health-monitor" in str(r["to_actor"])]
    return len(to_botops) == 1 and len(to_health) == 1 and len(held) == 2, \
        f"{len(held)} held notices; BotOps {len(to_botops)}, Health {len(to_health)}"


# ================================================================================================ 8 edit by chat
V1 = [act("n1", "note", text="New ask for the lead.")]
V2 = [step("route", "paths", paths=[{"name": "urgent", "match": "all",
                                     "when": [cond("task.title", "contains", "Urgent")],
                                     "steps": [act("tag", "label", tag="urgent"),
                                               act("n2", "note", text="Urgent ask for the lead.")]}],
           otherwise={"steps": [act("n1", "note", text="New ask for the lead.")]})]


def wf8_steps(org, f):
    """Dana asks BotOps for a change. BotOps works with her rights, so its edit is the live version 2 at once."""
    do(org, f, "auto", W3, make_auto, org, "Lead intake", LEAD_REF, {"type": "task_received", "to": LEAD_REF}, V1)
    do(org, f, "task_1", W2, send, org, DANA, LEAD_ID, "Urgent: payments page down")
    org.scheduler_pass()
    do(org, f, "chat", W3, lambda: org.api("post", f"/api/v2/chat/{BOTOPS}", {
        "text": "Label urgent tasks for the lead.", "refs": {"automation": aid(f, "auto")}}, who=DANA, wave=W3))

    def edit(token, attempt):
        return org.api("patch", f"/api/v2/automations/{aid(f, 'auto')}",
                       {"steps": V2, "summary": "Label urgent tasks"}, who=token, wave=W3)
    do(org, f, "helper", W3, drain, org, BOTOPS, edit, text="Saved v2: urgent tasks get the urgent label.")
    do(org, f, "pending", W3, lambda: org.api("get", f"/api/v2/automations/{aid(f, 'auto')}/versions?status=pending",
                                              wave=W3))
    do(org, f, "current", W3, read_auto, org, f, "auto")
    do(org, f, "task_2", W2, send, org, DANA, LEAD_ID, "Urgent: login broken")
    org.scheduler_pass()
    do(org, f, "restore", W3, lambda: org.api("post", f"/api/v2/automations/{aid(f, 'auto')}/versions/1/restore",
                                              {}, wave=W3))
    do(org, f, "task_3", W2, send, org, DANA, LEAD_ID, "Urgent: export slow")
    org.scheduler_pass()
    do(org, f, "runs", W3, runs_of, org, f, "auto")


def wf8_run(f, key):
    found = naming(runs(f, "runs"), task_id_of(got(f, key)) or "-")
    if len(found) != 1:
        raise Unmet(W3, f"{len(found)} runs for {key}")
    return found[0]


def wf8_turn(f):
    """Dana's chat reaches BotOps as a turn."""
    setup(f, "auto", "chat")
    turns = got(f, "helper")
    return len(turns) >= 1, f"{len(turns)} BotOps turns"


def wf8_applied(f):
    """Its edit, with Dana's rights, is the live version 2 at once: nothing pending."""
    setup(f, "auto", "chat", "pending", "current")
    turns = got(f, "helper")
    edit = turns[0]["did"] if turns else None
    if isinstance(edit, Unmet):
        raise edit
    pending = [v for v in rows(body(f, "pending")) if v.get("status") == "pending"]
    version = body(f, "current").get("version")
    answered = edit.body.get("version") if isinstance(edit, Reply) and isinstance(edit.body, dict) else None
    return (pending == [] and version == 2 and answered == 2,
            f"edit answer version {answered}; pending versions {[v.get('n') for v in pending]}; current {version}")


def wf8_before(f):
    """A task before the edit runs version 1: no label."""
    setup(f, "auto", "task_1")
    run = wf8_run(f, "task_1")
    return run.get("version") == 1 and status_of(run, "tag") is None, f"version {run.get('version')}, label " \
        f"{status_of(run, 'tag')}"


def wf8_after(f):
    """After the edit, version 2 runs and labels the urgent task."""
    setup(f, "auto", "chat", "task_2")
    run = wf8_run(f, "task_2")
    return run.get("version") == 2 and status_of(run, "tag") == "ok", f"version {run.get('version')}, label " \
        f"{status_of(run, 'tag')}"


def wf8_restore(f):
    """Restoring version 1 makes version 3 with the old flow: the next task runs it, with no label."""
    setup(f, "auto", "restore", "task_3")
    restored = body(f, "restore")
    restored = restored.get("automation", restored)
    run = wf8_run(f, "task_3")
    same = [s.get("id") for s in restored.get("steps") or []] == ["n1"]
    return (restored.get("version") == 3 and same and run.get("version") == 3 and status_of(run, "tag") is None,
            f"restore answer version {restored.get('version')}, old steps {same}; run version {run.get('version')}")


# ================================================================================================ 9 runaway
GUARD9 = 6


def wf9_budget(org):
    now = org.api("get", f"/api/v2/bots/{OPENAI_ID}/budget", wave=W2)
    if now.status != 200:
        raise Unmet(W2, f"budget read: {now.status} {now.excerpt()}")
    put = put_budget(org, OPENAI_ID, {"turns_per_hour": GUARD9, "expected_revision": now.body.get("revision")})
    if put.status >= 300:
        raise Unmet(W2, f"budget set: {put.status} {put.excerpt()}")
    return put


def hand_self(org):
    """The bot hands itself the next task during its turn, as the Self follow-up's prompt says. The bot makes it, not
    the automation, so the automation chain (MAX_CHAIN) does not stop it: this is the runaway the turn budget counts."""
    def act(token, attempt):
        # A new title each turn: the product refuses a second open task with the same owner and text (422 duplicate),
        # which would end the loop by itself instead of by the turn budget.
        title = f"Next step {attempt.get('id', '')}"
        made = org.api("post", "/api/v2/tasks", {"title": title, "body": "Scenario task.", "owner": OPENAI_REF},
                       who=token, wave=W2)
        if made.status >= 300:
            raise Unmet(W2, f"hand itself the next task: {made.status} {made.excerpt()}")
        return made.status
    return act


def wf9_loop(org):
    """Turn after turn while the bot keeps handing itself work, until two claims in a row hand nothing out."""
    ran, empty = [], 0
    for _ in range(16):
        result = turn(org, OPENAI_ID, act=hand_self(org), tokens=2_000_000, wave=W2)
        ran.append(result)
        empty = empty + 1 if result is None else 0
        if empty == 2:
            break
    return ran


def wf9_steps(org, f):
    do(org, f, "budget", W2, wf9_budget, org)
    do(org, f, "auto", W3, make_auto, org, "Self follow-up", OPENAI_REF, {"type": "task_received", "to": OPENAI_REF},
       [step("work", "bot_turn", bot=OPENAI_ID, prompt="Work the task, then hand yourself the next one."),
        act("next", "create_task", owner=OPENAI_REF, title="Follow-up: {{task.title}}")],
       spend_cap={"per_30m": 1.00})
    do(org, f, "seed", W2, send, org, DANA, OPENAI_ID, "Start the loop")
    do(org, f, "loop", W2, wf9_loop, org)
    # The automation's own follow-ups never start it again (the chain, backend/automations.py MAX_CHAIN): a fresh task
    # from a person starts a second run, which the spend cap then meets.
    do(org, f, "keep", W2, send, org, DANA, OPENAI_ID, "Keep going")
    org.scheduler_pass()
    do(org, f, "held_now", W2, org.api, "get", f"/api/v2/bots/{OPENAI_ID}/budget", wave=W2)
    at(org, org.now() + dt.timedelta(hours=1))
    do(org, f, "later", W2, turn, org, OPENAI_ID, wave=W2)
    do(org, f, "held_later", W2, org.api, "get", f"/api/v2/bots/{OPENAI_ID}/budget", wave=W2)
    do(org, f, "notices", W2, hub_holds, org)
    do(org, f, "pause", W3, lambda: org.api("patch", f"/api/v2/automations/{aid(f, 'auto')}", {"enabled": False},
                                            wave=W3))
    do(org, f, "runs_paused", W3, runs_of, org, f, "auto")
    do(org, f, "release", W2, org.api, "post", f"/api/v2/bots/{OPENAI_ID}/release", {}, wave=W2)
    do(org, f, "fresh", W2, send, org, DANA, OPENAI_ID, "After the release")
    do(org, f, "resumed", W2, turn, org, OPENAI_ID, wave=W2)
    org.scheduler_pass()
    do(org, f, "runs", W3, runs_of, org, f, "auto")
    # The fake clock stands still through the loop, so the runs share one `started_at`: the order they were made in
    # comes from the table.
    f["made"] = [r["id"] for r in sql(org, "SELECT id FROM automation_runs ORDER BY rowid")]


def wf9_spend_cap(f):
    """The first Bot turn step runs; once the $1 per 30 minutes is spent, later ones record spend_cap."""
    setup(f, "auto", "seed", "keep")
    made = f.get("made") or []
    found = sorted(runs(f, "runs"), key=lambda r: (str(r.get("started_at")),
                                                   made.index(r["id"]) if r.get("id") in made else len(made)))
    statuses = [status_of(r, "work") for r in found]
    return (statuses[:1] == ["ok"] and "spend_cap" in statuses[1:],
            f"Bot turn step per run, oldest first: {statuses[:8]}")


def wf9_held(f):
    """The run guard holds the bot at 6 turns, and the budget reads held."""
    setup(f, "budget", "auto", "held_now")
    loop = got(f, "loop")
    ran = [t for t in loop if t]
    held = body(f, "held_now").get("held")
    last = ran[-1].get("did") if ran else None
    return len(ran) == GUARD9 and loop[-1] is None and bool(held), \
        f"{len(ran)} turns ran; last hand-off {last}; held {held}"


def wf9_still(f):
    """An hour later the bot is still held: a person must release it."""
    setup(f, "budget", "auto", "held_later")
    later = got(f, "later")
    held = body(f, "held_later").get("held")
    return later is None and bool(held), f"turn an hour later {'none' if later is None else 'ran'}; held {held}"


def hub_holds(org):
    """The hub's held notices (kind notice, refs.held), as a reply."""
    return Reply(200, sql(org, "SELECT to_actor, refs FROM messages WHERE kind='notice' AND refs LIKE ?",
                          ("%held%",)), W2)


def wf9_notices(f):
    """One held notice each to BotOps and to the Health monitor: hub notices from the keeper, kind notice, refs.held
    naming the bot (CONTRACT section 1). They are hub messages, not Tico Team notices, so they are read from the hub."""
    setup(f, "budget", "auto", "notices")
    held = [r for r in body(f, "notices") if OPENAI_ID in str(r["refs"])]
    to_botops = [r for r in held if "botops" in str(r["to_actor"])]
    to_health = [r for r in held if "health-monitor" in str(r["to_actor"])]
    return len(to_botops) == 1 and len(to_health) == 1 and len(held) == 2, \
        f"{len(held)} held notices; BotOps {len(to_botops)}, Health {len(to_health)}"


def wf9_release(f):
    """Paused and released, the bot works again and the paused automation starts no run."""
    setup(f, "budget", "auto", "pause", "release", "fresh")
    resumed = got(f, "resumed")
    before, after = len(runs(f, "runs_paused")), len(runs(f, "runs"))
    return resumed is not None and before == after, f"turn after release {'ran' if resumed else 'none'}; runs " \
        f"{before} -> {after}"


# ================================================================================================ 10 migration day
DAY10 = (2026, 10, 21)
MAIL10 = {"03-mail-customer-question": DANA_MAIL, "04-mail-refund-request": DANA_MAIL,
          "09-mail-deadline-ask": DANA_MAIL, "07-mail-calendar-invite": PRIYA_MAIL,
          "14-mail-product-feedback": PRIYA_MAIL}
PEOPLE10 = (DANA_ID, PRIYA_ID)
WEBHOOKS10 = ("release.published", "market.briefing", "task.created", "task.commented")
HEALTH_BOT = "health-monitor"   # migration_fixture.HEALTH


def handler(org, f, actor):
    """The name a teammate is counted under, or None when it is left out (the Health Monitor, a built-in off the
    chart). The Inbox Managers retire and the person's Assistant handles the mail; the CEO Bot is the Strategy Bot."""
    from .cases.migration_fixture import HEALTH, INBOXES, STRATEGY
    slug = str(actor or "").split(":", 1)[-1]
    if slug == HEALTH or "health" in slug:
        return None
    for person, (bot, _mailbox) in INBOXES.items():
        if slug == bot or f["assistants"].get(slug) == person:
            return "inbox handler for " + person
    if slug in (STRATEGY, "strategy-bot", "strategy"):
        return "strategy"
    return slug


def masked(title):
    """A task title with a find item's ` · <item title>`, dates and the release tag dropped."""
    title = (title or "").split(" · ", 1)[0]
    title = re.sub(r"v1\.5\.\d+", "v1.5.N", title)
    return re.sub(r"\d{4}-\d{2}-\d{2}", "<date>", title)


def find_assistants(org, f):
    for person in PEOPLE10:
        try:
            slug = assistant_slug(org, person)
        except Unmet:
            return
        if slug:
            f["assistants"][slug] = person


def drain_all(org, f, errors):
    find_assistants(org, f)
    for slug in list(org.bot_slugs):
        try:
            drain(org, slug, None, limit=6, text="Done for today.")
        except Exception as exc:  # noqa: BLE001 (kept with the day: a claim the product refuses is reported)
            errors.append(f"{slug}: {type(exc).__name__}: {getattr(exc, 'what', exc)}"[:160])


def publish_release(org, tag):
    """A release arrives as GitHub's release webhook delivers it (backend/github.py `release`): the milestone is recorded
    once (a repeat delivery is ignored there) and the first recording runs release_flow.published, which tells the
    automations. Calling `published` directly would skip that dedupe."""
    from backend import github
    repo = org.store.settings.release_repo   # the server's release repository: the webhook's repository must match it
    payload = {"action": "published", "repository": {"full_name": repo},
               "release": {"tag_name": tag, "draft": False, "html_url": f"https://github.com/{repo}/releases/tag/{tag}"}}
    with org.store.transaction() as c:
        return github.release(c, payload, release_repo=repo)


def fake_day(org, f, receiver):
    """The install's working day after the cutover, measured over its window."""
    day, errors = DAY10, []

    def safe(label, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            if DEBUG:
                traceback.print_exc()
            errors.append(f"{label}: {type(exc).__name__}: {getattr(exc, 'what', exc)}"[:200])
    found = inputs()
    safe("settle", at, org, utc(day, 3, 55))
    drain_all(org, f, errors)
    start = org.now()
    safe("05:00", at, org, utc(day, 5))
    ids = {}
    for mailbox in (DANA_MAIL, PRIYA_MAIL):
        mail = [mail_from(found[stem]["state"], f"wf10-{stem}", mailbox=mailbox, date=iso(org.now()))
                for stem, box in MAIL10.items() if box == mailbox]
        ids[mailbox] = [m["msg_id"] for m in mail]
        reply = safe("mail " + mailbox, publish_mail, org, mailbox, mail)
        if reply is not None and reply.status >= 300:
            errors.append(f"mail {mailbox}: {reply.status} {reply.excerpt()}")
    safe("05:30", at, org, utc(day, 5, 30))
    tasks = {CLAUDE_ID: "Fix the CSV export", OPENAI_ID: "Review the billing page copy"}
    for bot, title in tasks.items():
        made = safe("task " + bot, send, org, DANA, bot, title)
        f["task:" + bot] = made if made is not None else Unmet(W5, "task not made")
    safe("06:00", at, org, utc(day, 6))
    f["commented_at"] = org.now()
    for bot in tasks:
        safe("comment " + bot, comment, org, f, "task:" + bot, DANA, "One more detail: keep the old format too.")
    drain_all(org, f, errors)
    fx = f.get("fixture") or {}
    for minutes in range(390, 16 * 60 + 1, 30):
        safe(f"{minutes // 60:02d}:{minutes % 60:02d}", at, org, utc(day, minutes // 60, minutes % 60))
        if minutes == 600:
            safe("release", publish_release, org, "v1.5.1")
        if minutes == 660:
            hook_ = fx.get("hook") or {}
            if hook_.get("id") and hook_.get("secret"):
                answer = safe("deploy hook", org.api, "post", f"/api/v2/hooks/{hook_['id']}/{hook_['secret']}",
                              {"service": "api", "status": "failed"})
                if answer is not None and answer.status >= 300:
                    errors.append(f"deploy hook: {answer.status} {answer.excerpt()}")
            else:
                errors.append("deploy hook: the fixture's trigger has no address")
        org.scheduler_pass()
        drain_all(org, f, errors)
    end = org.now()
    safe("deliver", deliver, org.app, receiver)
    return measure(org, f, start, end, ids, receiver, errors)


def measure(org, f, start, end, ids, receiver, errors):
    def inside(row, since=start):
        at_ = when_of(row["created_at"])
        return at_ is not None and since <= at_ <= end
    tasks = Counter()
    for row in sql(org, "SELECT owner,title,created_at FROM tasks"):
        who = handler(org, f, row["owner"])
        if who and inside(row):
            tasks[f"{who} | {masked(row['title'])}"] += 1
    woken, after_comment = Counter(), Counter()
    for row in sql(org, "SELECT bot, created_at FROM jobs"):
        who = handler(org, f, row["bot"])
        if who and not who.startswith("inbox handler") and inside(row):
            woken[who] += 1
            after_comment[who] += inside(row, f["commented_at"])
    hooks = Counter()
    for request in receiver.seen:
        text = request.content.decode(errors="replace")
        try:
            kind = str(json.loads(text).get("type"))
        except ValueError:
            kind = "unreadable"
        if not kind.startswith("health") and HEALTH_BOT not in text:
            hooks[kind] += 1
    mail = {}
    for mailbox, msg_ids in ids.items():
        stored = {r["msg_id"] for r in sql(org, "SELECT msg_id FROM mail_messages WHERE mailbox=?", (mailbox,))}
        mail[mailbox] = len([i for i in msg_ids if i in stored and i in mail_runs(org, f, mailbox)])
    return {"tasks": dict(tasks), "woken": dict(woken), "after_comment": dict(after_comment),
            "webhooks": dict(hooks), "mail": mail, "errors": sorted(set(errors))[:8], "window": [iso(start), iso(end)]}


def mail_runs(org, f, mailbox):
    """The text of every run of a message automation on `mailbox` (CONTRACT §2 trigger message {inbox})."""
    if mailbox in f.setdefault("mail_runs", {}):
        return f["mail_runs"][mailbox]
    listing = org.api("get", "/api/v2/automations", wave=W5)
    text = ""
    for auto in rows(listing.body) if listing.status == 200 else []:
        trigger = auto.get("trigger") or {}
        if trigger.get("type") == "message" and str(trigger.get("inbox", "")).lower() == mailbox:
            found = all_runs(org, auto["id"])
            text += canon(found.body) if found.status == 200 else ""
    f["mail_runs"][mailbox] = text
    return text


def close_open(org):
    """Dana closes what the install left open (a PR sweep run), so the day starts from an empty board."""
    left = 0
    for row in sql(org, "SELECT id FROM tasks WHERE status NOT IN ('closed','done')"):
        task = org.api("get", f"/api/v2/tasks/{row['id']}", wave=W5)
        version = (task.body.get("task") or task.body).get("version") if isinstance(task.body, dict) else None
        done = org.api("post", f"/api/v2/tasks/{row['id']}", {"version": version, "status": "closed"}, wave=W5)
        left += done.status >= 300
    return left


def wf10_steps(org, f):
    from .cases.migration_fixture import install_fixture, report, upgrade
    f["assistants"] = {}
    attach_judge(org, SteadyJudge(org))
    do(org, f, "endpoint", W5, endpoint, org, "Ops feed", "https://hooks.acme.example/ops", ["*"])
    install_fixture(org, f)
    do(org, f, "cutover", W5, upgrade, org)
    do(org, f, "report", W5, report, org)
    close_open(org)
    deliver(org.app, Receiver())   # what the install and the cutover sent belongs to no day
    do(org, f, "day", W5, fake_day, org, f, Receiver())


def day(f):
    data = got(f, "day")
    if not isinstance(data, dict):
        raise Unmet(W5, "the day was not measured")
    return data


def wf10_moved(f):
    """The cutover converted the install: every fixture routine, wake rule and inbox moved, nothing left pending or
    failed."""
    from .cases.migration_fixture import INBOXES, ROUTINES, WAKE_RULES, routine_id, rule_id
    made = got(f, "cutover")
    data = got(f, "report")
    if not isinstance(made, dict) or not isinstance(data, dict):
        return False, f"cutover {canon(made)[:80]}; report {canon(data)[:80]}"
    now = {i["id"]: i.get("now") for i in data.get("items") or []}
    wanted = [routine_id(n) for n in ROUTINES] + [rule_id(n) for n in WAKE_RULES] + \
        ["inbox:" + box for _bot, box in INBOXES.values()]
    off = {i: now.get(i) for i in wanted if now.get(i) not in ("moved", "runs as before")}
    stuck = sorted(i for i, n in now.items() if str(n).startswith("pending") or n == "failed")
    return not off and not stuck and not made.get("failed"), f"not moved {off}; pending or failed {stuck[:4]}; " \
        f"cutover failed {made.get('failed')}"


def wf10_day(f):
    """The converted day does the work: tasks opened, bots woken, webhooks sent, no step refused."""
    one = day(f)
    ok = one["tasks"] and one["woken"] and one["webhooks"] and not one["errors"]
    return bool(ok), f"{sum(one['tasks'].values())} tasks, {sum(one['woken'].values())} wakes, " \
        f"{sum(one['webhooks'].values())} webhooks; errors {one['errors'][:3]}"


def wf10_once(f):
    """No double fire: one release-notes task and one deploy-triage task."""
    one = day(f)
    release = sum(n for k, n in one["tasks"].items() if k.startswith(RELEASE_ID) and "Release notes" in k)
    deploy = sum(n for k, n in one["tasks"].items() if k.startswith(LEAD_ID) and "Deploy failure triage" in k)
    return release == 1 and deploy == 1, f"release-notes tasks {release}, deploy-triage tasks {deploy}"


def wf10_mail(f):
    """Each mailbox's mail is handled by its converted inbox automation."""
    mail = day(f)["mail"]
    return mail == {DANA_MAIL: 3, PRIYA_MAIL: 2}, f"handled per mailbox {mail}"


def wf10_woken(f):
    """Dana's comments wake both builders."""
    woke = day(f)["after_comment"]
    return all(woke.get(b) for b in (CLAUDE_ID, OPENAI_ID)), f"wakes after the comments {woke}"


def wf10_hooks(f):
    """The ops feed hears the release, the converted briefing's post and the day's tasks."""
    sent = day(f)["webhooks"]
    want = [k for k in WEBHOOKS10 if not sent.get(k)]
    return not want, f"missing {want}; sent {sent}"


# ================================================================================================ the list
WORKFLOWS = [
    Scenario("1", "workflow-1-inbox-day", "40 emails and 15 Slack messages through Filter, Decide and batches",
             wf1_steps, [
                 Check("26 newsletters and bounces archived by the Filter, no model call", W3, wf1_filtered),
                 Check("Filter runs on receipt; the rest wait for the 08:00 batch", W3, wf1_early),
                 Check("the 14 others decided once each, at level low", W3, wf1_decided),
                 Check("Decide runs only at 08:00, 13:00 and 17:00: 5, 4 and 5 calls", W3, wf1_batches),
                 Check("send switch off: both replies saved as drafts, nothing sent", W3, wf1_send_off),
                 Check("routed mail becomes one Engineering Lead task each", W4, wf1_routed),
                 Check("owner-only mail is left for Dana; the rest labelled", W4, wf1_owner),
                 Check("drafts are written on Dana's Assistant", W3, wf1_assistant),
                 Check("each Slack message runs its channel's automation once; #random none", W3, wf1_slack),
             ], bots={COO: ("coo", "claude")}),
    Scenario("2", "workflow-2-task-flood", "Builders flood the lead and Dana; caps refuse the excess", wf2_steps, [
        Check("22 sends: 12 accepted, 10 refused with task_cap", W2, wf2_flood),
        Check("a refused send writes no task", W2, wf2_no_row),
        Check("closed before the lead acted: resubmit fits, the next is refused at 4", W2, wf2_close_early),
        Check("closed after Dana acted still counts: resubmit refused at 2", W2, wf2_close_late),
        Check("Needs you holds the 3 open builder questions only", W2, wf2_needs_you),
        Check("the Budget tab shows today's count and closed per cap", W2, wf2_budget_tab),
        Check("the lead's automation runs once per accepted task", W3, wf2_lead_runs),
    ]),
    Scenario("3", "workflow-3-briefing", "Listening match to the Librarian's graph; 07:00 briefing to Market and Updates",
             wf3_steps, [
                 Check("the market match starts one run; misses none", W3, wf3_one_run),
                 Check("the match reaches the Librarian as a turn about it", W3, wf3_turn),
                 Check("nothing reaches the graph before the Librarian's turn", W6, wf3_graph_before),
                 Check("after the turn the graph holds the entity with the item's url", W6, wf3_graph_after),
                 Check("the briefing runs at 07:00 Los Angeles, not before", W3, wf3_seven),
                 Check("the briefing is Market's latest", W6, wf3_market),
                 Check("the briefing is posted to Updates", W6, wf3_updates),
             ], bots={LIBRARIAN: ("librarian", "claude")}),
    Scenario("4", "workflow-4-stale-pr-nudge", "Hourly sweep nudges PRs idle a day, once each", wf4_steps, [
        Check("the sweep runs at 10:00, 11:00 and 12:00 UTC", W3, wf4_hourly),
        Check("one nudge per stale PR (100, 101, 102, 104)", W3, wf4_one_each),
        Check("no nudge for merged PR 103", W3, wf4_merged),
        Check("each hour nudges only what newly went stale; 12:00 nothing", W3, wf4_hour_three),
    ]),
    Scenario("5", "workflow-5-stripe-failure", "A signed Stripe failure becomes a Billing task, a turn and a webhook",
             wf5_steps, [
                 Check("a forged delivery is refused and starts nothing", W3, wf5_forged),
                 Check("the signed delivery runs once; the retry does not", W3, wf5_once),
                 Check("one Billing task names the customer", W3, wf5_task),
                 Check("the Bot turn runs on Billing", W3, wf5_turn),
                 Check("the payments desk gets one request with the event", W3, wf5_receiver),
             ], bots={BILLING: ("billing", "claude")}),
    Scenario("6", "workflow-6-release", "A release event runs QA and notify once per release", wf6_steps, [
        Check("v1.4.0 runs each automation once", W3, wf6_first),
        Check("the repeated v1.4.0 event runs nothing more", W3, wf6_dup),
        Check("the QA turn runs on the Release Manager", W3, wf6_qa_turn),
        Check("Updates names v1.4.0 and the feed gets it once", W3, wf6_told),
        Check("v1.4.1 the next day runs both again", W3, wf6_next),
    ]),
    Scenario("7", "workflow-7-health", "A run-guard trip reaches BotOps and the Health monitor", wf7_steps, [
        Check("Health is a built-in automation", W5, wf7_builtin),
        Check("Health is enabled as its monitor is on", W5, wf7_enabled),
        Check("one held notice each to BotOps and Health", W2, wf7_notices),
    ], bots={BOTOPS: ("botops", "claude"), HEALTH: ("health", "claude")}),
    Scenario("8", "workflow-8-edit-by-chat", "Dana asks BotOps for a change; with her rights it is live at once",
             wf8_steps, [
                 Check("the chat reaches BotOps as a turn", W3, wf8_turn),
                 Check("its edit is the live version 2; nothing pending", W3, wf8_applied),
                 Check("a task before the edit runs version 1", W3, wf8_before),
                 Check("after the edit version 2 runs and labels", W3, wf8_after),
                 Check("restoring version 1 makes version 3 with the old flow", W3, wf8_restore),
             ], bots={BOTOPS: ("botops", "claude")}),
    Scenario("9", "workflow-9-runaway",
             "A self-feeding automation: its follow-ups stop at the chain, a fresh task hits the spend cap, then the run "
             "guard holds the bot", wf9_steps, [
        Check("the spend cap stops Bot turn steps after the first", W3, wf9_spend_cap),
        Check("the run guard holds the bot at 6 turns", W2, wf9_held),
        Check("still held an hour later", W2, wf9_still),
        Check("one held notice each to BotOps and Health", W2, wf9_notices),
        Check("paused and released, the bot works again", W2, wf9_release),
    ], bots={BOTOPS: ("botops", "claude"), HEALTH: ("health", "claude")}),
    Scenario("10", "workflow-10-migration-day",
             "The fixture install cut over on startup, then a working day on the automations", wf10_steps, [
                 Check("the cutover moved every routine, wake rule and inbox; none pending or failed", W5, wf10_moved),
                 Check("the day opens tasks, wakes bots and sends webhooks, no step refused", W5, wf10_day),
                 Check("no double fire: one release-notes and one deploy-triage task", W5, wf10_once),
                 Check("each mailbox's mail handled by its inbox automation", W5, wf10_mail),
                 Check("Dana's comments wake both builders", W5, wf10_woken),
                 Check("the ops feed hears the release, the briefing and the tasks", W5, wf10_hooks),
             ], bots=FIXTURE_BOTS, first_tick=False),
]
