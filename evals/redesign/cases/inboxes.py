"""Inboxes (PLAN §7, scenarios 16-21): a message trigger on an inbox with its Filter, Decide, Paths and LLM steps, the
send switch and batch timing. Mail arrives through the mail sync's own route (publish_mail, the runner's token), and
every check reads what the engine did with it: its runs, the day's counts, the judge's calls, the Assistant and the
send switch."""
import json

from ..fakes import ReplayJudge, attach_judge
from ..harness import (DANA_ID, DANA_REF, W3, Check, Scenario, assistant_of, auto_id, automation, body, find, got,
                       inbox_read, inbox_send, mail_computer, mail_message, publish_mail, replied, rows, setup,
                       today_counts, turn_of)

INBOX = "dana@acme.example"
TIMES = ["08:00", "13:00", "17:00"]
DAY = 86400
BUCKET = {"bucket": {"type": "choice", "instructions": "Reply, or archive as noise?",
                     "criteria": {"reply": "needs an answer", "archive": "noise"}}}


# ---------------------------------------------------------------------------------------------- helpers
def cond(field, op, value, **extra):
    return {"field": field, "op": op, "value": value, **extra}


def node(sid, kind, **fields):
    return {"id": sid, "type": kind, "enabled": True, **fields}


def inbox_trigger(**extra):
    return {"type": "message", "inbox": INBOX, **extra}


def bucket_answer(choice, confidence):
    """The judge's answer for the bucket question, in the shape the judge returns (backend/judge.py, matrix.py)."""
    other = "archive" if choice == "reply" else "reply"
    return {"bucket": {"type": "choice", "choice": choice, "confidence": confidence,
                       "probabilities": {choice: confidence, other: round(1 - confidence, 2)}}}


def flow_of(data):
    return data.get("automation", data) if isinstance(data, dict) else {}


def read_auto(org, facts, key="auto"):
    return org.api("get", f"/api/v2/automations/{auto_id(facts, key)}", wave=W3)


def read_flow(f):
    """The saved flow, read back from the server; raises unless the create and the read were both 2xx."""
    setup(f, "auto", "read")
    return flow_of(body(f, "read"))


def steps_of(flow, kind):
    return [s for s in flow.get("steps") or [] if s.get("type") == kind]


def runs_of(org, facts, key="auto"):
    return org.api("get", f"/api/v2/automations/{auto_id(facts, key)}/runs", wave=W3)


def first_run(data):
    """The newest run in a runs listing (newest first), or None."""
    runs = rows(data)
    return runs[0] if runs else None


def run_for(data, msg_id):
    """The run a given mail started (its trigger_ref names the message): runs started in the same frozen second tie on
    time, so newest-first order alone cannot tell them apart."""
    return next((run for run in rows(data) if msg_id in str(run.get("trigger_ref") or "")), None)


def step_row(run, sid):
    return next((s for s in run.get("steps") or [] if s.get("id") == sid), None)


def ran_ok(run, sid):
    row = step_row(run, sid)
    return bool(row) and row.get("status") == "ok"


def outcome(run, sid):
    """A step's row as text, for a check that looks for a named action or a logged event in it."""
    return json.dumps(step_row(run, sid) or {}, default=str)


def whole(run):
    return json.dumps(run, default=str)


def outputs_of(run, sid):
    return (step_row(run, sid) or {}).get("outputs") or {}


def archive_runs(data):
    """The runs whose Filter ran and archived a mail (its rule's action is archive)."""
    return [run for run in rows(data) if ran_ok(run, "sort") and "archive" in outcome(run, "sort")]


def norm(slug):
    return str(slug or "").removeprefix("bot:")


def shift_day(org):
    """Moves the fake clock a day on in one jump, then lets the hub run what is due (a batch fires in any 24 hours)."""
    org.clock.shift(DAY)
    org.scheduler_pass()


def counted(f, key):
    """A step that returned a number (a judge's call count): raised as Unmet when the step could not run."""
    return got(f, key)


def judge_calls(f):
    """How many times the judge was asked, read through the judge step: an unmet judge stays Unmet, not a zero."""
    return len(got(f, "judge").calls)


# ---------------------------------------------------------------------------------------------- 16 filter archive
def s16_steps(org, f):
    org.step(f, "auto", automation, org, "Inbox · dana", DANA_REF,
             trigger=inbox_trigger(filters=[cond("message.from", "contains", "promo")]),
             steps=[node("sort", "filter", rules=[{"when": [cond("message.from", "contains", "promo")],
                                                   "match": "all", "then": [{"do": "archive", "args": {}}],
                                                   "stop": True}])])
    org.step(f, "match", publish_mail, org, INBOX, [mail_message("m16-promo", "promo@shop.acme.example", "Sale")])
    org.step(f, "computer1", mail_computer, org, INBOX)   # the archive is done by the mail computer
    org.step(f, "other", publish_mail, org, INBOX, [mail_message("m16-ops", "ops@acme.example", "Deploy notes")])
    org.step(f, "computer2", mail_computer, org, INBOX)
    org.step(f, "read", read_auto, org, f)
    org.step(f, "runs", runs_of, org, f)


def filtered_saved(f):
    trigger = read_flow(f).get("trigger") or {}
    filters = trigger.get("filters") or []
    ok = trigger.get("type") == "message" and trigger.get("inbox") == INBOX and bool(filters) \
        and filters[0].get("field") == "message.from"
    return ok, f"trigger {trigger}"


def match_archived(f):
    setup(f, "auto", "match", "runs")
    hits = archive_runs(body(f, "runs"))
    return len(hits) >= 1, f"{len(hits)} run(s) archived a mail"


def other_stays(f):
    """Only the matching mail is archived: the second archive would mean the other mail was archived too."""
    setup(f, "auto", "other", "runs")
    hits = archive_runs(body(f, "runs"))
    return len(hits) == 1, f"{len(hits)} run(s) archived a mail, want 1 (the matching one)"


def dropped_counted(f):
    setup(f, "auto", "read", "runs")
    today = today_counts(body(f, "read")) or {}
    runs = rows(body(f, "runs"))
    return today.get("dropped") == 1 and len(runs) == 1, \
        f"today {today}, {len(runs)} run row(s); want one dropped and one run row"


# ---------------------------------------------------------------------------------------------- 17 decide questions
def s17_steps(org, f):
    org.step(f, "judge", attach_judge, org, ReplayJudge([bucket_answer("reply", 0.8), bucket_answer("reply", 0.6)]))
    org.step(f, "auto", automation, org, "Triage · dana", DANA_REF, trigger=inbox_trigger(), steps=[
        node("rank", "decide", questions=BUCKET, level="medium", out="d1"),
        node("reply", "filter", rules=[{"when": [cond("d1.bucket", "is", "reply", min=0.7)], "match": "all",
                                         "then": [{"do": "draft_reply", "args": {}}], "stop": True}])])
    org.step(f, "read", read_auto, org, f)
    org.step(f, "mail1", publish_mail, org, INBOX,
             [mail_message("m17-a", "client@acme.example", "Deck?", "Can you send the deck?")])
    org.step(f, "computer1", mail_computer, org, INBOX)   # the draft is made by the mail computer
    org.step(f, "runs1", runs_of, org, f)
    org.step(f, "mail2", publish_mail, org, INBOX,
             [mail_message("m17-b", "client@acme.example", "Deck again?", "Did you get my note?")])
    org.step(f, "computer2", mail_computer, org, INBOX)
    org.step(f, "runs2", runs_of, org, f)


def decide_saved(f):
    decide = next(iter(steps_of(read_flow(f), "decide")), {})
    ok = "bucket" in (decide.get("questions") or {}) and decide.get("level") == "medium" and decide.get("out") == "d1"
    return ok, f"decide {decide}"


def judge_at_level(f):
    setup(f, "auto", "mail1")
    calls = got(f, "judge").calls
    level = calls[0].get("level") if calls else None
    return level == "medium", f"{len(calls)} judge call(s), first at level {level!r}, want 'medium'"


def answer_stored(f):
    setup(f, "auto", "runs1")
    run = run_for(body(f, "runs1"), "m17-a")
    if not run:
        return False, "no run for the mail"
    hit = find(outputs_of(run, "rank"), lambda d: "bucket" in d) or {}
    ans = hit.get("bucket") or {}
    return ans.get("choice") == "reply" and ans.get("confidence") == 0.8, f"bucket answer {ans}"


def level_stored(f):
    setup(f, "auto", "runs1")
    run = run_for(body(f, "runs1"), "m17-a")
    level = (outputs_of(run, "rank") if run else {}).get("level")
    return level == "medium", f"Decide outputs level {level!r}, want 'medium'"


def drafted_at_08(f):
    setup(f, "auto", "runs1")
    run = run_for(body(f, "runs1"), "m17-a")
    ok = bool(run) and ran_ok(run, "reply") and "draft_reply" in outcome(run, "reply")
    return ok, f"reply rule outcome {outcome(run, 'reply') if run else 'no run'}"


def not_drafted_at_06(f):
    setup(f, "auto", "runs2")
    run = run_for(body(f, "runs2"), "m17-b")
    ok = bool(run) and ran_ok(run, "reply") and "draft_reply" not in outcome(run, "reply")
    return ok, f"reply rule outcome {outcome(run, 'reply') if run else 'no run'}"


# ---------------------------------------------------------------------------------------------- 18 paths on outputs
def s18_steps(org, f):
    org.step(f, "judge", attach_judge, org, ReplayJudge([bucket_answer("reply", 0.8)]))
    org.step(f, "auto", automation, org, "Route mail · dana", DANA_REF, trigger=inbox_trigger(), steps=[
        node("rank", "decide", questions=BUCKET, level="medium", out="d1"),
        node("fork", "paths", paths=[
            {"name": "reply", "when": [cond("d1.bucket", "is", "reply", min=0.7)],
             "steps": [node("r", "action", do="draft_reply", args={})]},
            {"name": "archive", "when": [cond("d1.bucket", "is", "archive", min=0.7)],
             "steps": [node("a", "action", do="archive", args={})]}],
         otherwise={"steps": [node("o", "action", do="note", args={"text": "unsure"})]})])
    org.step(f, "first", automation, org, "Route first match · dana", DANA_REF, trigger=inbox_trigger(), steps=[
        node("fork", "paths", paths=[
            {"name": "first", "when": [cond("message.from", "contains", "client")],
             "steps": [node("f1", "action", do="note", args={"text": "first"})]},
            {"name": "second", "when": [cond("message.from", "contains", "client")],
             "steps": [node("f2", "action", do="note", args={"text": "second"})]}],
         otherwise={"steps": []})])
    # Paths never rejoin: a step after a fork is refused on save (CONTRACT §2 Rules), so no run can reach one.
    org.step(f, "rejoin", org.api, "post", "/api/v2/automations", {
        "name": "Rejoin · dana", "owner_id": DANA_REF, "enabled": True, "trigger": inbox_trigger(), "steps": [
            node("fork", "paths", paths=[{"name": "one", "when": [], "steps": [node("p1", "action", do="note",
                                                                                        args={"text": "one"})]}],
                 otherwise={"steps": []}),
            node("after", "action", do="note", args={"text": "after the fork"})]}, wave=W3)
    org.step(f, "read", read_auto, org, f)
    org.step(f, "mail", publish_mail, org, INBOX,
             [mail_message("m18-a", "client@acme.example", "Which plan?", "Which plan?")])
    org.step(f, "runs", runs_of, org, f)
    org.step(f, "first_runs", runs_of, org, f, "first")


def paths_saved(f):
    fork = next(iter(steps_of(read_flow(f), "paths")), {})
    ok = len(fork.get("paths") or []) == 2 and fork.get("otherwise") is not None
    return ok, f"paths step {fork}"


def path_output(f):
    setup(f, "auto", "runs")
    run = first_run(body(f, "runs"))
    path = outputs_of(run, "fork").get("path") if run else None
    return path == "reply", f"paths output {path!r}, want 'reply'"


def first_match_only(f):
    """Two paths match the same mail: the first is the one taken, and only its step ran."""
    setup(f, "first", "first_runs")
    run = first_run(body(f, "first_runs"))
    if not run:
        return False, "no run for the mail"
    path = outputs_of(run, "fork").get("path")
    ok = path == "first" and ran_ok(run, "f1") and not ran_ok(run, "f2")
    return ok, f"paths output {path!r}; first ran {ran_ok(run, 'f1')}, second ran {ran_ok(run, 'f2')}"


# ---------------------------------------------------------------------------------------------- 19 assistant turn
def s19_steps(org, f):
    org.step(f, "auto", automation, org, "Assistant · dana", DANA_REF, trigger=inbox_trigger(), steps=[
        {"id": "ask", "type": "llm", "enabled": True, "as": "assistant:dana", "prompt": "Draft a reply",
         "out": "txt"}])
    org.step(f, "read", read_auto, org, f)
    org.step(f, "before", assistant_of, org, DANA_ID)
    org.step(f, "mail", publish_mail, org, INBOX,
             [mail_message("m19-a", "client@acme.example", "Hello", "Can we talk?")])
    org.step(f, "after", assistant_of, org, DANA_ID)
    org.step(f, "runs", runs_of, org, f)


def assistant_saved(f):
    llm = next(iter(steps_of(read_flow(f), "llm")), {})
    return llm.get("as") == "assistant:dana", f"llm step {llm}"


def none_before(f):
    setup(f, "before")
    data = body(f, "before")
    return isinstance(data, dict) and data.get("bot") is None, f"before the first mail: {data}"


def assistant_created(f):
    setup(f, "after")
    bot = (body(f, "after") or {}).get("bot")
    return isinstance(bot, str) and bool(bot), f"Dana's Assistant: {bot!r}"


def turn_names_assistant(f):
    setup(f, "after", "runs")
    bot = (body(f, "after") or {}).get("bot")
    run = first_run(body(f, "runs"))
    turn = turn_of(run) if run else None
    ok = bool(turn) and bool(bot) and norm(turn.get("bot")) == norm(bot)
    return ok, f"turn {turn}, want bot {bot!r}"


# ---------------------------------------------------------------------------------------------- 20 send switch
def s20_steps(org, f):
    org.step(f, "auto", automation, org, "Replies · dana", DANA_REF, trigger=inbox_trigger(),
             steps=[node("send", "action", do="send_reply", args={})])
    org.step(f, "read", read_auto, org, f)
    org.step(f, "on", inbox_send, org, INBOX, True)
    org.step(f, "read_on", inbox_read, org, INBOX)
    org.step(f, "mail_on", publish_mail, org, INBOX,
             [mail_message("m20-a", "client@acme.example", "Lunch?", "Friday?")])
    org.step(f, "computer_on", mail_computer, org, INBOX)   # the send is made by the mail computer
    org.step(f, "runs_on", runs_of, org, f)
    org.step(f, "off", inbox_send, org, INBOX, False)
    org.step(f, "read_off", inbox_read, org, INBOX)
    org.step(f, "mail_off", publish_mail, org, INBOX,
             [mail_message("m20-b", "client@acme.example", "Monday?", "Monday?")])
    org.step(f, "computer_off", mail_computer, org, INBOX)
    org.step(f, "runs_off", runs_of, org, f)


def send_saved(f):
    sends = [s for s in read_flow(f).get("steps") or [] if s.get("do") == "send_reply"]
    return len(sends) == 1, f"{len(sends)} send_reply step(s), want 1"


def reads_on(f):
    setup(f, "on", "read_on")
    send = (body(f, "read_on") or {}).get("send")
    return send is True, f"send {send!r}, want true"


def sent_when_on(f):
    setup(f, "auto", "mail_on", "runs_on")
    run = run_for(body(f, "runs_on"), "m20-a")
    ok = bool(run) and ran_ok(run, "send") and "send_off" not in whole(run)
    return ok, f"send step {outcome(run, 'send') if run else 'no run'}"


def reads_off(f):
    setup(f, "off", "read_off")
    send = (body(f, "read_off") or {}).get("send")
    return send is False, f"send {send!r}, want false"


def draft_when_off(f):
    setup(f, "auto", "mail_off", "runs_off")
    run = run_for(body(f, "runs_off"), "m20-b")
    ok = bool(run) and "send_off" in whole(run) and "draft" in whole(run)
    return ok, "send_off logged with a draft" if ok else f"run {whole(run) if run else 'none'}"


# ---------------------------------------------------------------------------------------------- 21 batched
def s21_steps(org, f):
    judge = ReplayJudge([bucket_answer("reply", 0.8), bucket_answer("reply", 0.8)])
    org.step(f, "judge", attach_judge, org, judge)
    org.step(f, "auto", org.api, "post", "/api/v2/automations", {
        "name": "Batch · dana", "owner_id": DANA_REF, "enabled": True, "builtin": False,
        "batch": {"times": TIMES}, "trigger": inbox_trigger(),
        "steps": [node("gate", "filter", rules=[]),
                  node("rank", "decide", questions=BUCKET, level="medium", out="d1")]}, wave=W3)
    org.step(f, "read", read_auto, org, f)
    org.step(f, "mail1", publish_mail, org, INBOX, [mail_message("m21-a", "client@acme.example", "One", "First one")])
    org.step(f, "receipt", runs_of, org, f)
    org.step(f, "held1", judge_calls, f)
    org.step(f, "day1", shift_day, org)
    org.step(f, "batch1", judge_calls, f)
    org.step(f, "mail2", publish_mail, org, INBOX, [mail_message("m21-b", "client@acme.example", "Two", "Second one")])
    org.step(f, "held2", judge_calls, f)
    org.step(f, "day2", shift_day, org)
    org.step(f, "batch2", judge_calls, f)


def batch_saved(f):
    batch = read_flow(f).get("batch")
    return batch == {"times": TIMES}, f"batch {batch}, want {{'times': {TIMES}}}"


def filter_on_receipt(f):
    setup(f, "auto", "receipt")
    run = first_run(body(f, "receipt"))
    ok = bool(run) and ran_ok(run, "gate") and not ran_ok(run, "rank")
    return ok, f"gate {'ran' if run and ran_ok(run, 'gate') else 'did not run'}, decide held"


def held_until_batch(f):
    setup(f, "auto", "mail1")
    calls = counted(f, "held1")
    return calls == 0, f"{calls} judge call(s) on receipt, want 0"


def ran_at_batch(f):
    setup(f, "auto", "mail1")
    before, after = counted(f, "held1"), counted(f, "batch1")
    return after > before, f"judge calls {before} -> {after} across a batch time, want an increase"


def next_batch_waits(f):
    setup(f, "auto", "mail1", "mail2")
    after_day, on_arrival, after_next = counted(f, "batch1"), counted(f, "held2"), counted(f, "batch2")
    ok = on_arrival == after_day and after_next > on_arrival
    return ok, f"calls {after_day} before the arrival, {on_arrival} on it, {after_next} after the next batch"


SCENARIOS = [
    Scenario(16, "inbox-filter-archive", "Inbox filter: matching mail archived, the rest left; dropped mail counted",
             s16_steps, [
                 Check("an inbox automation with a filtered message trigger is saved", W3, filtered_saved),
                 Check("mail matching the filter is archived", W3, match_archived),
                 Check("mail the filter does not match is not archived", W3, other_stays),
                 Check("a trigger its filters drop writes a daily count, no run row", W3, dropped_counted),
             ]),
    Scenario(17, "inbox-decide-questions",
             "Inbox Decide: answers stored per question with the level; the reply rule gated on min confidence",
             s17_steps, [
                 Check("a Decide step on a question set is saved with level and out", W3, decide_saved),
                 Check("the judge is asked at the decide level", W3, judge_at_level),
                 Check("the Decide outputs hold each question's answer, with its confidence", W3, answer_stored),
                 Check("the Decide outputs carry the level it ran at", W3, level_stored),
                 Check("a reply at confidence 0.8 passes min 0.7 and drafts", W3, drafted_at_08),
                 Check("a reply at confidence 0.6 fails min 0.7 and does not draft", W3, not_drafted_at_06),
             ]),
    Scenario(18, "inbox-paths-on-outputs", "Inbox paths: the path output names the path taken; first match only",
             s18_steps, [
                 Check("a paths step on a Decide output is saved with two paths and otherwise", W3, paths_saved),
                 Check("the paths output reads the path taken, reply", W3, path_output),
                 Check("two paths match one mail: the first runs, the second does not", W3, first_match_only),
                 Check("a path does not rejoin: a step after the fork is refused on save (422)", W3,
                       lambda f: replied(f, "rejoin", 422)),
             ]),
    Scenario(19, "inbox-assistant-turn",
             "Inbox LLM as Dana's Assistant: created on first use, the turn names that bot",
             s19_steps, [
                 Check("an LLM step as assistant:dana is saved", W3, assistant_saved),
                 Check("no Assistant exists before the first mail", W3, none_before),
                 Check("the first mail creates Dana's Assistant, a bot", W3, assistant_created),
                 Check("the llm turn names Dana's Assistant, not another session", W3, turn_names_assistant),
             ], bots={"coo": ("assistant", "claude")}),   # the team's Assistant bot (settings assistant_bot)
    Scenario(20, "inbox-send-switch", "Inbox send switch: reply sent when on; draft and send_off logged when off",
             s20_steps, [
                 Check("a send_reply step is saved", W3, send_saved),
                 Check("the send switch reads back on", W3, reads_on),
                 Check("with the send switch on, the reply is sent", W3, sent_when_on),
                 Check("the send switch reads back off", W3, reads_off),
                 Check("with the switch off, a draft is saved and send_off is logged", W3, draft_when_off),
             ]),
    Scenario(21, "inbox-batched", "Inbox batch: Filter on receipt, Decide and LLM at the batch times", s21_steps, [
        Check("batch times are saved and read back", W3, batch_saved),
        Check("the Filter runs on receipt; the Decide waits", W3, filter_on_receipt),
        Check("no Decide call on receipt: the batch holds it", W3, held_until_batch),
        Check("the Decide runs at a batch time", W3, ran_at_batch),
        Check("mail that arrives between batch times waits for the next one", W3, next_batch_waits),
    ]),
]
