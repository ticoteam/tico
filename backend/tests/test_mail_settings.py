"""Mail sending without approval is a server-held switch only a person who manages the bot sets (backend/mail_settings.py).

bot.yaml's `outbound_send` and `forward_to` only ask: a bot, or BotOps in its own run or acting for a person, cannot
set the switch, and Health says when a bot.yaml asks for more than a person approved.
"""

from backend.tests.test_api import api, assign, claim, get, headers, post, runner, setup_attempt  # noqa: F401
from backend.tests.test_botops_parity import _wake, act
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixture)

RUNTIMES = {"codex": {"installed": True, "authenticated": "ready"}}


def setting(api, token="ana-test", bot="ops"):
    return get(api, f"bots/{bot}/mail-settings", token=token)


def change(api, body, token="ana-test", bot="ops"):
    return api.post(f"/api/v2/bots/{bot}/mail-settings", json=body, headers=headers(token))


def report(api, machine, bot, manifest):
    row = {"ready": True, "runtime": "codex", "model": "gpt-6-luna", "repository_present": True,
           "configuration_valid": True, "problems": []}
    # What the runner reports for bot.yaml's mail keys (runner-rs/src/declared_access.rs mail_request).
    row["mail_request"] = {"outbound_send": manifest.get("outbound_send") is True, "forward_to": manifest.get("forward_to", [])}
    r = api.post("/api/v2/runners/heartbeat", headers=headers(machine["token"]), json={
        "version": "test", "platform": "test", "readiness": {"schema_version": 1, "runtimes": RUNTIMES, "bots": {bot: row}}})
    assert r.status_code == 200, r.text


def mail_alert(api):
    checks = get(api, "health")["checks"]
    return next((c["summary"] for c in checks if c["id"] == "mail_sending"), None)


def test_only_a_person_who_manages_the_bot_turns_sending_on(api, botops):
    assert setting(api) == {**setting(api), "set": False, "outbound_send": False, "forward_to": []}
    _, _, attempt = setup_attempt(api, "ops")
    # The bot reads its own setting (its mail connector does) but never sets it.
    assert setting(api, attempt["token"])["set"] is False
    assert change(api, {"outbound_send": True}, attempt["token"]).status_code == 403
    # Nor BotOps: not in its own run (the daily update), not as the person who asked it.
    own = _wake(api, botops)
    assert act(api, own, "POST", "bots/ops/mail-settings", {"outbound_send": True}, ref=None).status_code == 403
    finish(api, botops, own)
    lent = turn(api, botops, person="ana-test", text="Let ops send mail")
    assert act(api, lent, "POST", "bots/ops/mail-settings", {"outbound_send": True}).status_code == 403
    # A person who does not manage the bot is refused too.
    assert change(api, {"outbound_send": True}, "cara-test").status_code == 403
    assert setting(api)["set"] is False

    assert change(api, {"forward_to": ["not an address"]}).status_code == 422
    r = change(api, {"outbound_send": True, "forward_to": ["Me@Personal.example"]}, "ben-test")
    assert r.status_code == 200, r.text
    seen = setting(api, attempt["token"])
    assert (seen["set"], seen["outbound_send"], seen["forward_to"], seen["updated_by"]) == \
        (True, True, ["me@personal.example"], "human:ben")


def test_health_alerts_while_bot_yaml_asks_for_more_than_a_person_approved(api):
    machine = runner(api)
    assign(api, machine, "ops")
    report(api, machine, "ops", {"outbound_send": True, "forward_to": ["me@personal.example"]})
    assert "ops asks to send mail without approval and to forward mail to me@personal.example; " \
           "a person must turn this on" in mail_alert(api)
    assert setting(api)["unapproved"] == {"outbound_send": True, "forward_to": ["me@personal.example"]}

    # The upgrade step: a person reads the request and takes it.
    assert change(api, {"from_request": True}).status_code == 200
    assert setting(api)["outbound_send"] is True and mail_alert(api) is None

    # A forward target added to bot.yaml later is a new request, not a new target.
    report(api, machine, "ops", {"outbound_send": True, "forward_to": ["me@personal.example", "x@elsewhere.example"]})
    assert setting(api)["forward_to"] == ["me@personal.example"]
    assert "ops asks to forward mail to x@elsewhere.example" in mail_alert(api)


def test_a_manager_sees_the_alert_for_the_bots_they_manage_and_no_other(api):
    from backend.tests.test_api import as_member
    # Ben operates cpo and not ops; as a plain member (not an admin) he manages cpo only. Cara manages nothing.
    as_member(api, "ben@acme.example")
    machine = runner(api)
    assign(api, machine, "ops")
    assign(api, machine, "cpo")
    rows = {}
    for bot in ("ops", "cpo"):
        rows[bot] = {"ready": True, "runtime": "codex", "model": "gpt-6-luna", "repository_present": True,
                     "configuration_valid": True, "problems": [],
                     "mail_request": {"outbound_send": True, "forward_to": []}}
    r = api.post("/api/v2/runners/heartbeat", headers=headers(machine["token"]), json={
        "version": "test", "platform": "test", "readiness": {"schema_version": 1, "runtimes": RUNTIMES, "bots": rows}})
    assert r.status_code == 200, r.text

    def seen(token):
        checks = get(api, "health", token=token)["checks"]
        return next((c["summary"] for c in checks if c["id"] == "mail_sending"), None)

    owner = seen("ana-test")
    assert "ops asks to send mail without approval" in owner and "cpo asks to send mail without approval" in owner
    ben = seen("ben-test")
    assert ben == "cpo asks to send mail without approval; a person must turn this on."
    assert seen("cara-test") is None


def test_a_refused_botops_write_names_the_person_and_the_place_not_go_ahead(api, botops):
    hint = ("A person who manages ops turns its mail sending on in Settings > Bots > ops > Mail sending, "
            "or with `hub bot mail ops --send`")
    own = _wake(api, botops)
    r = act(api, own, "POST", "bots/ops/mail-settings", {"outbound_send": True}, ref=None)
    assert r.status_code == 403 and r.json()["error"]["fix"] == hint, r.text
    assert "go ahead" not in r.text and r.json()["error"]["link"].endswith("#/settings")
    finish(api, botops, own)
    lent = turn(api, botops, person="ana-test", text="Let ops send mail")
    r = act(api, lent, "POST", "bots/ops/mail-settings", {"outbound_send": True})
    assert r.status_code == 403 and r.json()["error"]["fix"] == hint and "go ahead" not in r.text, r.text
    # Any other caller that is not a person gets the same pointer in the refusal itself.
    _, _, attempt = setup_attempt(api, "ops")
    r = change(api, {"outbound_send": True}, attempt["token"])
    assert r.status_code == 403 and "Settings > Bots > ops > Mail sending" in r.json()["error"]["detail"]
    assert "hub bot mail ops --send" in r.json()["error"]["detail"]


def test_the_ceo_bot_sends_nothing_outside_without_a_yes_for_that_message(api):
    """Sending outside the company stays the owner's at every level (docs/ceo-bot.md, "What stays yours"). The CEO Bot
    has no forward targets, whatever a person stored for it, and nobody can add one; its mail connector takes neither a
    forward target nor the sender of the thread it answers without a send approval for that exact message, an outside
    address beside a colleague on To needs one too, and one only on Cc is refused. Mail inside the company follows the switch a person
    sets (`internal_send`), as any bot's does; `outbound_send` reads off, for a connector from before this rule. Another
    bot with the same setting still sends to all of them. Synthetic addresses; nothing is sent and no mail provider is
    called (check_send only decides)."""
    import hashlib

    from backend.store import H
    from backend.tests.test_company import bot, with_ceo_bot
    from connectors.mail import policy as pl
    ceo, ops = with_ceo_bot(api), bot(api, "ops")
    sender, forward, body = "ava@creator.example", "me@personal.example", "Thanks, we have it.\n"
    colleague, lead = "ben@acme.example", "cy@acme.example"
    with api.app.state.store.transaction() as c:          # a person turned sending on for both, as before this rule
        for slug in ("goal-manager", "ops"):
            c.execute("INSERT INTO bot_mail_settings(bot,outbound_send,forward_to_json,updated,updated_by) "
                      "VALUES(?,1,?,?,'human:ana')", (slug, f'["{forward}"]', H.now()))
    seen = setting(api, ceo, bot="goal-manager")
    assert (seen["outbound_send"], seen["internal_send"], seen["forward_to"]) == (False, True, []), seen
    assert seen["per_message"] and setting(api, bot="goal-manager")["forward_to"] == []   # the owner reads what applies
    r = change(api, {"forward_to": [forward]}, bot="goal-manager")
    assert r.status_code == 422 and "each message" in r.text, r.text

    pol = pl.builtin("goal-manager", env={"TICO_INTERNAL_DOMAINS": "acme.example"})
    sha = hashlib.sha256(body.strip().encode()).hexdigest()
    yes = {"11111111-2222-3333-4444-555555555555": ([sender], []), "21111111-2222-3333-4444-555555555555": ([], [sender])}
    to_sender = next(iter(yes))
    saved = pl.SERVER_GET, pl.HUB_GET
    pl.HUB_GET = lambda path: next(({"id": n, "kind": "send", "decision": "approved", "consumed_at": None, "payload": {
        "to": to, "cc": cc, "subject": "Re: help", "mailbox": "box@acme.example", "body_sha256": sha}}
        for n, (to, cc) in yes.items() if path == f"approvals/{n}"), None)

    def send(slug, token, to, **kw):
        pl.SERVER_GET = lambda s: get(api, f"bots/{s}/mail-settings", token=token)
        return pl.check_send(pol, slug, "box@acme.example", to, manifest={}, verbs=["send"], body=body, **kw)

    reply = {"thread_id": "t-1", "thread_senders": [sender]}
    inside = (([colleague], {}), ([colleague], {"cc": [lead]}))
    outside = (([sender], reply), ([forward], {}), ([colleague], {"cc": [sender]}), ([colleague, sender], reply))
    try:
        for to, extra in inside + outside[:2] + outside[3:]:            # the ordinary bot's paths are as they were
            assert send("ops", ops, to, **extra).allowed, (to, extra)
        for to, extra in inside:
            assert send("goal-manager", ceo, to, **extra).allowed, (to, extra)
        for to, extra in outside:
            d = send("goal-manager", ceo, to, **extra)
            assert (d.allowed, d["gate"]) == (False, "outbound_send") and "each message" in d["reason"], (to, d)
        assert send("goal-manager", ceo, [sender], approval=to_sender, **reply).allowed
        assert send("goal-manager", ceo, [colleague, sender], approval=to_sender).allowed    # it names the outside one
        assert not send("goal-manager", ceo, [forward], approval=to_sender).allowed   # the yes names another address
        for approval in yes:                # an outside address only on Cc stays refused: a Tico yes names one on To
            assert not send("goal-manager", ceo, [colleague], cc=[sender], approval=approval).allowed

        assert change(api, {"outbound_send": False}, bot="goal-manager").status_code == 200
        assert setting(api, ceo, bot="goal-manager")["internal_send"] is False
        d = send("goal-manager", ceo, [colleague])
        assert (d.allowed, d["gate"]) == (False, "outbound_send"), d                  # off inside too, as for any bot
    finally:
        pl.SERVER_GET, pl.HUB_GET = saved


def test_only_an_exact_unspent_tico_send_approval_sends_the_ceo_bots_outside_mail(api):
    """The CEO Bot's outside message goes only under a Tico send approval that names it exactly: approved, unspent, its
    To and Cc, and its text's sha256. The owner's message telling it to send and a closed approval Issue bind neither the
    recipients and the text nor a single use, so neither lifts its switch, though both still lift another bot's.
    The server setting is the API's own answer; the approval, message and Issue reads are stubbed. Synthetic addresses;
    nothing is sent and no mail provider is called (check_send only decides)."""
    import hashlib
    import json

    from backend.tests.test_company import bot, with_ceo_bot
    from connectors.mail import policy as pl
    ceo, ops = with_ceo_bot(api), bot(api, "ops")
    seen = setting(api, ceo, bot="goal-manager")
    assert (seen["outbound_send"], seen["forward_to"], bool(seen["per_message"])) == (False, [], True), seen
    assert setting(api, ops)["outbound_send"] is False                       # another bot, its switch off too

    outside, other, body = "ava@creator.example", "eve@other.example", "Thanks, we have it.\n"
    digest = hashlib.sha256(body.strip().encode()).hexdigest()
    ids = {k: f"{n}1111111-2222-3333-4444-555555555555" for n, k in enumerate(
        ("exact", "recipient", "text", "spent", "order_ceo", "order_ops"), 1)}

    def approval(to=outside, sha=digest, consumed=None):
        return {"kind": "send", "decision": "approved", "consumed_at": consumed, "payload": {
            "to": [to], "cc": [], "subject": "Re: help", "mailbox": "box@acme.example", "body_sha256": sha}}

    def order(slug):            # the owner's Tico message telling the bot to email the outside address
        return {"from_actor": f"human:{pl.OWNER}", "to_actor": f"bot:{slug}",
                "body": f"Please email {outside} and tell them we have it."}

    rows = {f"approvals/{ids['exact']}": approval(), f"approvals/{ids['recipient']}": approval(to=other),
            f"approvals/{ids['text']}": approval(sha=hashlib.sha256(b"Another text").hexdigest()),
            f"approvals/{ids['spent']}": approval(consumed="2026-10-27T12:00:00Z"),
            f"messages/{ids['order_ceo']}": order("goal-manager"), f"messages/{ids['order_ops']}": order("ops")}
    reads = []

    def hub_get(path):
        reads.append(path)
        row = rows.get(path)
        return dict(row, id=path.split("/")[1]) if row else None

    class Issue:                # `gh issue view`: closed, labelled, its `Send to:` line matching, another text
        returncode, stderr = 0, ""
        stdout = json.dumps({"state": "CLOSED", "title": "Reply to Ava",
                             "labels": [{"name": f"owner:{pl.OWNER}"}, {"name": "type:decision"}],
                             "body": f"Send to: {outside}\n\nSay we will refund them in full today."})

    pol = pl.builtin("goal-manager", env={"TICO_INTERNAL_DOMAINS": "acme.example"})
    saved = pl.SERVER_GET, pl.HUB_GET, pl.RUN
    pl.HUB_GET, pl.RUN = hub_get, lambda *a, **k: Issue()

    def send(slug, token, approval_id):
        pl.SERVER_GET = lambda s: get(api, f"bots/{s}/mail-settings", token=token)
        return pl.check_send(pol, slug, "box@acme.example", [outside], manifest={}, verbs=["send"], body=body,
                             approval=approval_id)

    try:
        for ops_yes in (ids["order_ops"], "4242"):                  # the ordinary bot's paths are as they were
            assert send("ops", ops, ops_yes).allowed, ops_yes
        refusals = {"owner message": ids["order_ceo"], "closed Issue": "4242", "another recipient": ids["recipient"],
                    "another text": ids["text"], "spent": ids["spent"]}
        for case, approval_id in refusals.items():
            reads.clear()
            d = send("goal-manager", ceo, approval_id)
            assert (d.allowed, d["gate"]) == (False, "outbound_send"), (case, d)
            assert "exact message" in d["reason"] and not any(c["gate"] == "recipient" for c in d["checks"]), (case, d)
            assert not any(p.startswith("messages/") for p in reads), (case, reads)     # no owner-message fallback
        assert "another text" in send("goal-manager", ceo, ids["text"])["reason"]
        assert "already spent" in send("goal-manager", ceo, ids["spent"])["reason"]
        assert f"to=['{other}']" in send("goal-manager", ceo, ids["recipient"])["reason"]
        assert "not a Tico send approval" in send("goal-manager", ceo, "4242")["reason"]
        d = send("goal-manager", ceo, ids["exact"])
        assert d.allowed, d
        assert "per-message approval" in [c for c in d["checks"] if c["gate"] == "outbound_send"][0]["detail"], d
    finally:
        pl.SERVER_GET, pl.HUB_GET, pl.RUN = saved
