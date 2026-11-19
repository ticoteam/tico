"""Obligations with proof (Risk plan, slice 3; backend/rule_due.py, backend/org_rules.py, backend/risk.py): a rule's
due date says where it comes from (its basis) and, for a matter's own date, the task it belongs to; an occurrence is met
with proof (a file the marker reads, a sent email, an https link), waived with a note, or missed. A date with a task
shows only to that task's readers. An unconfirmed date, or a binding one within 14 days, makes its risk live.

Days are counted from today in UTC with a margin of days either side, so a test crossing midnight reads the same."""

from datetime import timedelta

from backend import rule_due as D
from backend.store import H
from backend.tests.helpers.org_rules import rule
from backend.tests.helpers.tasks_board import api, bot_token, get, post  # noqa: F401

PEOPLE = "risk:people-benefits"
THEFT = "risk:customer-theft-claims"


def day(offset):
    return (D.local_today("UTC") + timedelta(days=offset)).isoformat()


def standing_rule(api, text, risk):
    found = rule(api, {"kind": "internal", "text": text, "who": {"bots": ["ops"]}})
    post(api, f"risks/{risk}/links", {"linkable_type": "org_rule", "linkable_id": found["id"]})
    return found


def due(api, rule_id, first, token="ana-test", expected=200, **facts):
    body = {"first_due": first, "repeats": "once", "owner": "ops", "timezone": "UTC", **facts}
    return post(api, f"rules/{rule_id}/due", body, token=token, expected=expected)


def satisfied(api, due_id, body=None, token="ana-test", expected=200):
    return post(api, f"rule-due/{due_id}/satisfied", body or {}, token=token, expected=expected)


def obligations(api, token="ana-test", query=""):
    return get(api, "risk/obligations" + query, token=token)["obligations"]


def risk_of(api, key, token="ana-test"):
    return next(r for r in get(api, "risks", token=token)["risks"] if r["key"] == key)


def email(api, ident, state, actor="human:ana"):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO email_sends(id,mailbox,actor,bot,via,content_sha256,idempotency_key,recipients,attachments,"
            "subject,body_sha256,raw,draft_id,thread_id,reply_to_id,approval_id,classification,decision,state,"
            "created_at,send_after) VALUES(?,'legal@acme.example',?,'','','h',?,'[]','[]','Premium paid','b','','',"
            "'','','','{}','{}',?,?,?)",
            (ident, actor, "k-" + ident, state, H.now(), H.now()),
        )


def task_file(api, ident, task_id):
    """A file of the ops bot on a task: readable by whoever reads the task."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,task_id,current_version,"
            "first_activity_at,last_activity_at) VALUES(?,'ops',?,?,'receipt.pdf','file','',?,1,?,?)",
            (ident, "task:" + task_id, ident, task_id, H.now(), H.now()),
        )


def test_an_unproved_premium_makes_its_risk_live_until_proof_and_a_draft_is_not_proof(api):
    post(api, "risks", {"label": "People & benefits", "key": "people-benefits"})
    premiums = standing_rule(api, "Pay each insurance premium on time", PEOPLE)
    quiet = risk_of(api, PEOPLE)
    assert (quiet["level"], quiet["unconfirmed"], quiet["next_date"]) == ("quiet", 0, None)
    lapsed = due(api, premiums["id"], day(-17), title="Principal premium, $443", basis="contract")
    assert (lapsed["title"], lapsed["basis"], lapsed["task_id"]) == ("Principal premium, $443", "contract", None)
    renewal = due(api, premiums["id"], day(60), title="Principal renewal", basis="contract")
    seen = risk_of(api, PEOPLE)
    assert (seen["level"], seen["unconfirmed"], seen["next_date"]) == ("live", 1, day(60))
    [first, later] = obligations(api)
    assert (first["id"], first["state"], first["group"], first["due_on"]) == (
        lapsed["id"],
        "unconfirmed",
        "unconfirmed",
        day(-17),
    )
    assert first["risks"] == [{"id": seen["id"], "key": PEOPLE, "label": "People & benefits"}]
    assert first["rule"]["text"] == "Pay each insurance premium on time" and first["mark"] is None
    assert first["can_satisfy"] and first["can_waive"] and first["can_edit"]
    assert (later["id"], later["state"], later["group"]) == (renewal["id"], "due", "later")
    # Closing it without proof marks the day, and a contract date stays unconfirmed.
    marked = satisfied(api, lapsed["id"])
    assert marked["state"] == "unconfirmed" and marked["mark"]["proof"] is None
    assert risk_of(api, PEOPLE)["level"] == "live"
    # A draft is not proof; nor is a link that is not https, or a bank line by hand.
    email(api, "es-draft", "pending")
    email(api, "es-sent", "sent")
    satisfied(api, lapsed["id"], {"proof": {"kind": "email", "ref": "es-draft"}}, expected=422)
    satisfied(api, lapsed["id"], {"proof": {"kind": "link", "ref": "http://bank.example/receipt"}}, expected=422)
    satisfied(api, lapsed["id"], {"proof": {"kind": "bank", "ref": "txn-1"}}, expected=422)
    satisfied(api, lapsed["id"], {"proof": {"kind": "email", "ref": "es-missing"}}, expected=404)
    # The sent mail proves the mark already there: its doer and time are kept.
    proved = satisfied(api, lapsed["id"], {"proof": {"kind": "email", "ref": "es-sent"}, "note": "Paid by phone"})
    assert proved["state"] == "met"
    assert (proved["mark"]["satisfied_by"], proved["mark"]["satisfied_at"]) == (
        marked["mark"]["satisfied_by"],
        marked["mark"]["satisfied_at"],
    )
    assert proved["mark"]["proof"] == {"kind": "email", "ref": "es-sent", "name": "Premium paid"}
    assert proved["mark"]["note"] == "Paid by phone"
    # A proved mark is final.
    satisfied(api, lapsed["id"], {"proof": {"kind": "link", "ref": "https://bank.example/r/1"}}, expected=409)
    seen = risk_of(api, PEOPLE)
    assert (seen["level"], seen["unconfirmed"]) == ("quiet", 0)
    [past] = obligations(api, query="?group=past")
    assert (past["state"], past["mark"]["proof"]) == (
        "met",
        {"kind": "email", "ref": "es-sent", "name": "Premium paid"},
    )
    assert past["mark"]["outcome"] == "met" and past["mark"]["satisfied_by"] == "human:ana"
    # A binding date due within 14 days makes it live again; one only asked for does not.
    asked = due(api, premiums["id"], day(5), title="Winberg reply", basis="asked")
    assert risk_of(api, PEOPLE)["level"] == "quiet"
    due(api, premiums["id"], day(5), title="Lincoln premium", basis="contract")
    seen = risk_of(api, PEOPLE)
    assert (seen["level"], seen["next_date"]) == ("live", day(5))
    assert asked["id"] in [o["id"] for o in obligations(api, query="?state=due&days=30")]
    assert {o["title"] for o in obligations(api, query="?group=next")} == {"Winberg reply", "Lincoln premium"}
    # The contract answers say so.
    get(api, "risk/obligations?days=0", expected=422)
    get(api, "risk/obligations?state=late", expected=422)


def test_waived_and_missed_keep_the_name_and_note_and_only_a_person_waives(api):
    post(api, "risks", {"label": "Customer theft claims"})
    court = standing_rule(api, "Meet every date a court or arbitrator sets", THEFT)
    hearing = due(api, court["id"], day(-14), title="Murphy hearing link", basis="order")
    review = due(api, court["id"], day(-12), title="Rodriguez review", basis="asked")
    ops = bot_token(api, "ops")  # the dates' owner
    satisfied(api, review["id"], {"outcome": "waived"}, expected=422)  # a waiver needs a note
    satisfied(api, review["id"], {"outcome": "waived", "note": "They withdrew"}, token=ops, expected=403)
    satisfied(
        api, review["id"], {"outcome": "missed", "proof": {"kind": "link", "ref": "https://x.example"}}, expected=422
    )
    waived = satisfied(api, review["id"], {"outcome": "waived", "note": "They withdrew"})
    assert (waived["state"], waived["mark"]["outcome"], waived["mark"]["note"]) == ("waived", "waived", "They withdrew")
    assert waived["mark"]["satisfied_by"] == "human:ana"
    # The owner bot says it was missed; a person may later find the proof after all.
    missed = satisfied(api, hearing["id"], {"outcome": "missed", "note": "No link came"}, token=ops)
    assert (missed["state"], missed["mark"]["satisfied_by"]) == ("missed", "bot:ops")
    found = satisfied(api, hearing["id"], {"proof": {"kind": "link", "ref": "https://aaa.example/webfile/28"}})
    assert (found["state"], found["mark"]["satisfied_by"], found["mark"]["outcome"]) == ("met", "human:ana", "met")
    states = {o["title"]: (o["state"], o["group"]) for o in obligations(api)}
    assert states == {"Murphy hearing link": ("met", "past"), "Rodriguez review": ("waived", "past")}
    # Someone who may not edit the rule and does not own the date marks nothing.
    late = due(api, court["id"], day(-3), title="Randazzo reply", basis="theirs")
    satisfied(api, late["id"], {}, token="priya-test", expected=403)
    [watched] = obligations(api, query="?state=watched")
    assert (watched["title"], watched["state"], watched["can_satisfy"]) == ("Randazzo reply", None, True)
    assert obligations(api, token="priya-test", query="?state=watched")[0]["can_satisfy"] is False


def test_a_matters_date_shows_only_to_its_tasks_readers_and_a_file_they_cannot_read_is_no_proof(api):
    post(api, "risks", {"label": "Customer theft claims"})
    court = standing_rule(api, "Meet every date a court or arbitrator sets", THEFT)
    murphy = post(api, "tasks", {"owner": "ops", "title": "Murphy claim", "body": "AAA.", "private": True})
    version = get(api, "tasks/" + murphy["id"])["task"]["version"]
    post(api, "tasks/" + murphy["id"], {"version": version, "labels": [THEFT]})
    discovery = due(api, court["id"], day(20), title="Murphy discovery", basis="order", task_id=murphy["id"])
    assert discovery["task_id"] == murphy["id"]
    # Priya reads neither the task nor its date: not in Risk, not in the Rules lists, not by id.
    open_date = due(api, court["id"], day(25), title="Paiva records", basis="order", owner="priya")
    assert [o["id"] for o in obligations(api)] == [discovery["id"], open_date["id"]]
    assert [o["id"] for o in obligations(api, token="priya-test")] == [open_date["id"]]
    assert obligations(api)[0]["task"]["id"] == murphy["id"]
    listed = [d["id"] for d in get(api, "rules/due", token="priya-test")["due"]]
    assert discovery["id"] not in listed and open_date["id"] in listed
    assert discovery["id"] in [d["id"] for d in get(api, "rules/due")["due"]]
    assert [d["id"] for d in get(api, f"rules/{court['id']}", token="priya-test")["due"]] == [open_date["id"]]
    get(api, f"rule-due/{discovery['id']}", token="priya-test", expected=404)
    satisfied(api, discovery["id"], {}, token="priya-test", expected=404)
    assert get(api, "rules/counts", token="priya-test")["due"] == get(api, "rules/counts")["due"] - 1
    # A file on that task is no proof from Priya, who owns the open date but cannot read it.
    task_file(api, "file-murphy", murphy["id"])
    satisfied(api, open_date["id"], {"proof": {"kind": "file", "ref": "file-murphy"}}, token="priya-test", expected=404)
    proved = satisfied(
        api, open_date["id"], {"proof": {"kind": "link", "ref": "https://aaa.example/p"}}, token="priya-test"
    )
    assert proved["state"] == "met"
    # Ana reads it: her file proves the matter's date, and Priya is not told its name.
    satisfied(api, discovery["id"], {"proof": {"kind": "file", "ref": "file-murphy"}})
    # The task's readers hear of its dates and marks live (the tasks topic, kind obligations); a date with no task is
    # not sent there.
    with api.app.state.store.read() as c:
        sent = [r[0] for r in c.execute("SELECT subject_id FROM changes WHERE kind='obligations' ORDER BY rowid")]
    assert sent == [murphy["id"], murphy["id"]]
    shown = get(api, f"rule-due/{discovery['id']}")
    assert shown["history"][0]["proof"] == {"kind": "file", "ref": "file-murphy", "name": "receipt.pdf"}
    # Taking the date off the task shows it to everyone; the file's name still only to its readers.
    post(api, f"rule-due/{discovery['id']}", {"task_id": None})
    seen = get(api, f"rule-due/{discovery['id']}", token="priya-test")
    assert seen["task_id"] is None and seen["history"][0]["proof"] == {"kind": "file", "ref": None, "name": None}
    # A task one cannot read is no task to put a date on.
    due(api, court["id"], day(30), token="priya-test", expected=403, task_id=murphy["id"])


def test_a_date_with_no_basis_behaves_as_before_and_a_reopen_keeps_a_proved_mark(api):
    found = rule(api, {"kind": "internal", "text": "File the Acme sales tax return.", "who": {"bots": ["ops"]}})
    start = day(-2)
    plain = due(api, found["id"], start)
    assert (plain["title"], plain["basis"], plain["task_id"], plain["status"]) == ("", "", None, "overdue")
    marked = satisfied(api, plain["id"], {"due_on": start})
    assert marked["satisfied"] == start and marked["due"]["status"] == "satisfied" and marked["state"] == "met"
    assert marked["due"]["history"][0]["outcome"] == "met" and marked["due"]["history"][0]["proof"] is None
    satisfied(api, plain["id"], {"due_on": start}, expected=409)
    satisfied(api, plain["id"], {}, expected=409)
    # A row from before the migration reads as met, with no proof.
    with api.app.state.store.read() as c:
        row = dict(c.execute("SELECT * FROM rule_due_status WHERE due_date_id=?", (plain["id"],)).fetchone())
    assert (row["outcome"], row["proof_kind"], row["proof_ref"], row["note"]) == ("met", "", "", "")
    # An edit of the first day carries the title and basis to the new row.
    edited = post(api, f"rule-due/{plain['id']}", {"title": "Q4 return", "basis": "law"})
    assert (edited["id"], edited["title"], edited["basis"]) == (plain["id"], "Q4 return", "law")
    moved = post(api, f"rule-due/{plain['id']}", {"first_due": day(40)})
    assert moved["replaces"] == plain["id"] and (moved["title"], moved["basis"]) == ("Q4 return", "law")
    post(api, f"rule-due/{moved['id']}", {"basis": "fortnightly"}, expected=422)
    # A routine's mark that someone proved stays when its task is reopened; an unproved one goes.
    with api.app.state.store.transaction() as c:
        for due_on, kind in ((day(-40), "link"), (day(-41), "")):
            c.execute(
                "INSERT INTO rule_due_status(due_date_id,due_on,satisfied_by,via,task_id,satisfied_at,proof_kind,"
                "proof_ref) VALUES(?,?,'bot:ops','routine','t-routine',?,?,?)",
                (plain["id"], due_on, H.now(), kind, "https://x.example" if kind else ""),
            )
        D.on_due_task_reopened(c, {"id": "t-routine"}, "human:ana")
        left = [r[0] for r in c.execute("SELECT due_on FROM rule_due_status WHERE task_id='t-routine'")]
    assert left == [day(-40)]


def test_a_date_moved_onto_a_task_the_owner_cannot_read_keeps_that_task_out_of_its_chain_audit_and_events(api):
    from backend import audit_log

    court = rule(api, {"kind": "internal", "text": "Meet every date a court sets", "who": {"bots": ["ops"]}})
    public = due(api, court["id"], day(10), title="Paiva records", basis="order")
    # Priya's private warrant, which the owner does not read; she owns a date on it and one in the open.
    body = {"owner": "human:priya", "title": "LAPD warrant", "body": "x", "private": True}
    warrant = post(api, "tasks", body, token="priya-test")
    warrant = warrant.get("task", warrant)
    get(api, "tasks/" + warrant["id"], expected=404)
    with api.app.state.store.transaction() as c:
        for ident, replaces, task_id in (("due-warrant", public["id"], warrant["id"]), ("due-open", None, None)):
            c.execute(
                "INSERT INTO rule_due_dates(id,rule_id,first_due_on,repeats,timezone,owner,replaces,created_by,"
                "created_at,title,basis,task_id) VALUES(?,?,?,'once','UTC','human:priya',?,'human:priya',?,?,'order',?)",
                (
                    ident,
                    court["id"],
                    day(-1),
                    replaces,
                    H.now(),
                    "Warrant return" if task_id else "Open return",
                    task_id,
                ),
            )
        c.execute("UPDATE rule_due_dates SET ended_on=? WHERE id=?", (day(0), public["id"]))
    # Its chain from the old date leaves the warrant's row out for the owner, and keeps it for Priya.
    assert [link["id"] for link in get(api, f"rule-due/{public['id']}")["chain"]] == [public["id"]]
    assert [link["id"] for link in get(api, f"rule-due/{public['id']}", token="priya-test")["chain"]] == [
        public["id"],
        "due-warrant",
    ]
    # Marking it: Priya's answer names her file; the owner's audit row and events show neither the task nor the file.
    task_file(api, "file-warrant", warrant["id"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET bot_id='ops', scope=? WHERE id='file-warrant'", ("task:" + warrant["id"],))
    proof = {"kind": "file", "ref": "file-warrant"}
    marked = satisfied(api, "due-warrant", {"proof": proof}, token="priya-test")
    assert marked["mark"]["proof"] == {**proof, "name": "receipt.pdf"}
    proved = satisfied(api, "due-open", {"proof": proof}, token="priya-test")
    assert proved["due"]["history"][0]["proof"] == {**proof, "name": "receipt.pdf"}
    rows = get(api, f"audit?object=org_rule:{court['id']}")["rows"]
    after = {r["after"]["due"]: r["after"] for r in rows if r["after"] and "satisfied" in r["after"]}
    assert after["due-open"]["proof"] == {"kind": "file", "ref": None, "name": None}
    assert [r["after"] for r in rows if r["after"] == {"due": "due-warrant"}]  # only which date it was
    events = "SELECT detail FROM events WHERE action='org_rule.due_satisfied' ORDER BY created_at"
    seen = [r[0] for r in post(api, "sql", {"sql": events})["rows"]]
    assert len(seen) == 1 and "due-open" in seen[0]
    assert len(post(api, "sql", {"sql": events}, token="priya-test")["rows"]) == 2
    # A date moved in place from an open task onto the warrant keeps its id: its audit row is read by the task each side
    # names, not by the first one seen.
    open_task = post(api, "tasks", {"owner": "ops", "title": "Paiva claim", "body": "x"})
    with api.app.state.store.transaction() as c:
        audit_log.write(
            c,
            "human:priya",
            "org_rule:" + court["id"],
            {"due": {"id": "due-open", "task_id": open_task["id"], "title": "Open return"}},
            {"due": {"id": "due-open", "task_id": warrant["id"], "title": "Warrant return"}},
        )
    row = get(api, f"audit?object=org_rule:{court['id']}&limit=1")["rows"][0]
    assert row["before"]["due"]["title"] == "Open return" and row["after"] == {"due": "due-open"}


def test_a_risks_level_counts_a_day_marked_early_without_proof_and_finds_a_date_over_a_year_away(api):
    post(api, "risks", {"label": "People & benefits", "key": "people-benefits"})
    premiums = standing_rule(api, "Pay each insurance premium on time", PEOPLE)
    weekly = post(
        api,
        f"rules/{premiums['id']}/due",
        {"first_due": day(3), "repeats": "weekly", "owner": "ops", "timezone": "UTC", "basis": "contract"},
    )
    satisfied(api, weekly["id"], {"due_on": day(10)})  # paid early, no proof yet
    seen = risk_of(api, PEOPLE)
    listed = [o for o in obligations(api) if o["state"] == "unconfirmed"]
    assert (seen["unconfirmed"], len(listed), seen["level"], seen["next_date"]) == (1, 1, "live", day(3))
    # A link with no host, or one that does not parse, is no proof.
    for ref in ("https://:", "https://["):
        satisfied(api, weekly["id"], {"due_on": day(10), "proof": {"kind": "link", "ref": ref}}, expected=422)
    post(api, f"rule-due/{weekly['id']}/remove", {})
    due(api, premiums["id"], day(400), title="Five-year bond renewal", basis="contract")
    seen = risk_of(api, PEOPLE)
    assert (seen["next_date"], seen["unconfirmed"], seen["level"]) == (day(400), 0, "quiet")
