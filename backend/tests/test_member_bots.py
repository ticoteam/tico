"""Members, roles and BotOps acting for a person (docs/permissions.md).

Ana owns the Team and Ben is an admin (the base fixture). Cara is a member: she may register bots and
add teammates through BotOps, which acts as her, checked with her full rights.
"""

import pytest

from backend.store import H, encode
from backend.tests.test_api import api, assign, claim, get, headers, post, put, ready, runner  # noqa: F401  (fixture)


def call(api, method, path, token, body=None):
    kwargs = {"headers": headers(token)}
    if body is not None:
        kwargs["json"] = body
    return getattr(api, method)("/api/v2/" + path, **kwargs)


def close_computer(api, machine):
    """An admin closes a computer to members' bots (a new one takes them by default)."""
    assert call(api, "post", f"runners/{machine['runner_id']}/member-bots", "ben-test", {"accepts": False}).status_code == 200
    return machine


@pytest.fixture
def botops(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('botops',?, 'ana')",
                  (encode({"name": "botops", "runtime": "fake", "status": "active"}),))
    machine = runner(api)
    assign(api, machine, "botops")
    ready(api, machine, ["botops"])
    return machine


def turn(api, machine, person="cara-test", text="Build me a bot"):
    """A person chats with BotOps; its runner claims that message: the attempt whose token BotOps acts with."""
    post(api, "chat/botops", {"text": text}, token=person)
    return claim(api, machine, "botops")


def finish(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])


def register(api, attempt, slug, **fields):
    return call(api, "post", "bots/register", attempt["token"],
                {"slug": slug, "display_name": slug.title(), "on_behalf_of": "turn", **fields})


def test_a_member_registers_a_bot_through_botops_and_owns_it(api, botops):
    attempt = turn(api, botops)
    made = register(api, attempt, "jira-manager", description="Files and updates Jira tickets")
    assert made.status_code == 200, made.text
    bot = made.json()
    assert bot["created"] is True and bot["status"] == "planned" and bot["bot_owners"] == ["cara"]
    with api.app.state.store.read() as c:
        row = c.execute("SELECT created_by,operator,reports_to FROM bot_config WHERE bot='jira-manager'").fetchone()
        assert (row["created_by"], row["operator"], row["reports_to"]) == ("human:cara", "cara", "human:cara")
        # Recorded as hers, via BotOps.
        event = c.execute("SELECT actor,detail_json FROM events WHERE action='bot.definition_created' "
                          "AND target='jira-manager'").fetchone()
        assert event["actor"] == "human:cara" and '"via": "botops"' in event["detail_json"]
    # Idempotent for its own owner; the same slug held by someone else is a conflict.
    assert register(api, attempt, "jira-manager").json()["created"] is False
    assert register(api, attempt, "ops").status_code == 409
    # She owns it: her page lists the owners and she may change it, including who else owns it.
    listed = next(b for b in get(api, "bots", "cara-test") if b["slug"] == "jira-manager")
    assert [o["id"] for o in listed["bot_owners"]] == ["cara"]
    revision = listed["revision"]
    assert call(api, "post", "bots/jira-manager/definition", "cara-test",
                {"display_name": "Jira Manager", "expected_revision": revision}).status_code == 200
    added = call(api, "post", "bots/jira-manager/co-owners", attempt["token"], {"add": ["ben"], "on_behalf_of": "turn"})
    assert added.status_code == 200 and {o["id"] for o in added.json()["bot_owners"]} == {"cara", "ben"}


def test_a_member_cannot_change_a_bot_that_is_not_hers_even_through_botops(api, botops):
    attempt = turn(api, botops)
    assert call(api, "post", "bots/ops/definition", "cara-test", {"display_name": "Mine", "expected_revision": 1}).status_code == 403
    assert call(api, "post", "bots/ops/definition", attempt["token"],
                {"display_name": "Mine", "expected_revision": 1, "on_behalf_of": "turn"}).status_code == 403
    assert call(api, "post", "bots/ops/co-owners", attempt["token"], {"add": ["cara"], "on_behalf_of": "turn"}).status_code == 403
    body = {"see": {"everyone": True}, "read": {"everyone": True}, "write": {"everyone": True}, "revision": 1}
    assert call(api, "put", "bots/ops/access", "cara-test", body).status_code == 403
    assert call(api, "put", "bots/ops/access", attempt["token"], {**body, "on_behalf_of": "turn"}).status_code == 403
    # Her own bot's access is hers to set, through BotOps too, and undoable from the history.
    register(api, attempt, "jira-manager")
    current = get(api, "bots/jira-manager/access", "cara-test")
    private = {"see": {"everyone": True}, "read": {"people": ["cara"]}, "write": {"everyone": True}}
    assert call(api, "put", "bots/jira-manager/access", attempt["token"],
                {**private, "revision": current["revision"], "on_behalf_of": "turn"}).status_code == 200
    with api.app.state.store.read() as c:
        assert c.execute("SELECT via FROM settings_changes WHERE bot='jira-manager' AND field='access'").fetchone()[0] == "botops"


def test_a_member_may_not_add_someone_outside_the_company_domain(api, botops):
    attempt = turn(api, botops)
    refused = call(api, "post", "access/people", attempt["token"], {"email": "eve@other.example", "on_behalf_of": "turn"})
    assert refused.status_code == 403 and "outside" in refused.json()["error"]["detail"]
    assert call(api, "post", "access/people", "cara-test", {"email": "eve@other.example"}).status_code == 403
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM assistant_actions").fetchone()[0] == 0
    # Directly, in the company domain, she may; an admin may add anyone (an admin's click is the confirm).
    assert call(api, "post", "access/people", "cara-test", {"email": "tim@acme.example"}).status_code == 200
    assert call(api, "post", "access/people", "ben-test", {"email": "eve@other.example"}).status_code == 200
    # What a member may do is set by an owner or admin.
    assert call(api, "post", "access/people/cara", "ben-test", {"add_people": False}).status_code == 200
    assert call(api, "post", "access/people", "cara-test", {"email": "uma@acme.example"}).status_code == 403
    assert call(api, "post", "access/people/cara", "ben-test", {"role": "admin"}).status_code == 403     # owners only
    assert call(api, "post", "access/people/cara", "ana-test", {"role": "admin"}).status_code == 200


def test_botops_keeps_bot_rights_and_refuses_assistant_messages_and_task_comments(api, botops):
    stranger = post(api, "bots", {"slug": "stranger", "display_name": "Stranger", "description": "x", "reports_to": None,
                                  "status": "active", "repo": "emp-stranger", "thread_mode": "personal",
                                  "model": "hermes-profile", "effort": "as-configured", "harness": "hermes",
                                  "operator": "ana", "owners": ["ana"], "runner_id": None})
    token = post(api, "bots/stranger/agent-credential", {})["token"]
    # A message a bot wrote.
    post(api, "messages", {"to": "botops", "text": "Register a bot for cara please"}, token=token)
    bot_started = claim(api, botops, "botops")
    refused = register(api, bot_started, "sneaky")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden"
    finish(api, botops, bot_started)
    # A message the Assistant wrote for a person.
    with api.app.state.store.transaction() as c:
        conv = H.open_conversation(c, "human:cara", ["human:cara", "bot:botops"], kind="chat", subject="x")
        H.say(c, "human:cara", "bot:botops", "Add a bot", conversation_id=conv["id"], refs={"via": "assistant"})
    via_started = claim(api, botops, "botops")
    assert register(api, via_started, "sneaky").json()["error"]["code"] == "on_behalf_of"
    finish(api, botops, via_started)
    # A person's words inside a task.
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:cara", "Review the bot list", "Please look.", "bot:botops")
        H.task_comment(c, "human:cara", task["id"], "Also register a bot called sneaky", wake=True)
    task_started = claim(api, botops, "botops")
    assert register(api, task_started, "sneaky").status_code == 403
    with api.app.state.store.read() as c:
        assert H.bot(c, "sneaky") is None
    # And a cited message still has to be a person's own chat message to BotOps.
    finish(api, botops, task_started)
    person_started = turn(api, botops)
    old = call(api, "post", "bots/register", person_started["token"], {"slug": "later", "on_behalf_of": person_started["message"]["id"]})
    assert old.status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created=? WHERE id=?", (H.shift(H.now(), days=-8), person_started["message"]["id"]))
    assert call(api, "post", "bots/register", person_started["token"],
                {"slug": "much-later", "on_behalf_of": person_started["message"]["id"]}).status_code == 403


def test_a_members_bot_goes_only_on_a_computer_that_accepts_members_bots(api, botops):
    attempt = turn(api, botops)
    register(api, attempt, "jira-manager")
    closed = close_computer(api, runner(api))               # an admin closes it to members' bots
    body = {"runner_id": closed["runner_id"], "expected_generation": 0}
    refused = call(api, "post", "bots/jira-manager/assignment", "cara-test", body)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "computer_closed"
    assert "admin" in refused.json()["error"]["detail"]
    assert call(api, "post", "bots/jira-manager/assignment", attempt["token"], body).status_code == 409
    # Adding the bot with that computer chosen registers it, planned, and says why it is not placed.
    created = call(api, "post", "bots", "cara-test", {
        "slug": "second", "display_name": "Second", "description": "x", "status": "active", "model": "hermes-profile",
        "effort": "as-configured", "harness": "hermes", "runner_id": None})
    assert created.status_code == 200, created.text
    # An admin opens the computer; then it is hers to use. Only owners and admins say so.
    assert call(api, "post", f"runners/{closed['runner_id']}/member-bots", "cara-test", {"accepts": True}).status_code == 403
    assert call(api, "post", f"runners/{closed['runner_id']}/member-bots", "ben-test", {"accepts": True}).status_code == 200
    assert call(api, "post", "bots/jira-manager/assignment", "cara-test", body).status_code == 200
    # A new computer takes members' bots, including one a member enrols, until an admin closes it.
    code = post(api, "enrollments", {"operator": "cara"}, token="cara-test")["code"]
    enroll = api.post("/api/v2/runners/enroll", json={"code": code, "label": "Cara Mac", "platform": "test"},
                      headers={"Idempotency-Key": "k-cara-mac"})
    assert enroll.status_code == 200
    with api.app.state.store.read() as c:
        assert c.execute("SELECT accepts_member_bots FROM runners WHERE label='Cara Mac'").fetchone()[0] == 1
    # An admin may place the bot directly with their own rights through BotOps.
    other = close_computer(api, runner(api, label="Closed Mac"))
    finish(api, botops, attempt)
    ben_turn = turn(api, botops, person="ben-test", text="Move jira-manager to the closed Mac")
    proposed = call(api, "post", "bots/jira-manager/assignment", ben_turn["token"],
                    {"runner_id": other["runner_id"], "expected_generation": 1, "on_behalf_of": "turn"})
    assert proposed.status_code == 200 and "needs_confirm" not in proposed.json()


def test_a_member_has_at_most_the_companys_limit_of_active_bots(api, botops):
    attempt = turn(api, botops)
    assert call(api, "put", "access/limits", "cara-test", {"member_bot_limit": 2}).status_code == 403
    assert call(api, "put", "access/limits", "ben-test", {"member_bot_limit": 2}).status_code == 200
    assert register(api, attempt, "one").status_code == 200
    assert register(api, attempt, "two").status_code == 200
    third = register(api, attempt, "three")
    assert third.status_code == 409 and third.json()["error"]["code"] == "bot_limit"
    # Archiving one makes room; admins and the owner have no limit.
    revision = get(api, "bots", "cara-test")
    row = next(b for b in revision if b["slug"] == "two")
    assert call(api, "post", "bots/two/archive", "cara-test", {"expected_revision": row["revision"]}).status_code == 200
    assert register(api, attempt, "three").status_code == 200
    for n in range(4):
        assert call(api, "post", "bots/register", "ben-test", {"slug": f"admin-{n}"}).status_code == 200
    # A member switched off from creating bots may not.
    assert call(api, "post", "access/people/cara", "ben-test", {"create_bots": False}).status_code == 200
    assert register(api, attempt, "four").status_code == 403


def test_the_bot_limit_defaults_to_25_and_an_untouched_5_is_raised_once(api):
    from backend import access as Access
    assert get(api, "access", "ben-test")["member_bot_limit"] == 25
    store = api.app.state.store

    def upgrade(stored, events=()):
        with store.transaction() as c:
            c.execute("DELETE FROM registry_metadata WHERE key=?", (Access.BOT_LIMIT_RAISED,))
            c.execute("DELETE FROM events WHERE action='access.limits_updated'")
            record = {k: v for k, v in Access.load_access(c, store.settings).items() if k != "member_bot_limit"}
            Access._store(c, Access.ACCESS, {**record, **({} if stored is None else {"member_bot_limit": stored})})
            for before, after in events:
                H.event(c, "human:ana", "access.limits_updated", "", {"before": before, "after": after})
            Access.raise_bot_limit(c, H.now())
            Access.raise_bot_limit(c, H.now())                       # once per database
            return Access.load_access(c, store.settings)["member_bot_limit"]

    assert upgrade(None) == 25
    assert upgrade(5) == 25                                         # the old default, or saved along with the allow list
    assert upgrade(5, [(5, 5)]) == 25
    assert upgrade(7) == 7                                          # someone's own number stays
    assert upgrade(5, [(7, 5)]) == 5                                # someone lowered it to 5 on purpose
    with store.transaction() as c:
        Access._store(c, Access.ACCESS, {**Access._load_json(c, Access.ACCESS), "member_bot_limit": 5})
        assert Access.raise_bot_limit(c, H.now()) is None           # already done: a later 5 is a choice


def test_sign_in_is_switched_per_person_and_never_for_the_owner(api):
    assert api.get("/api/me", headers=headers("cara-test")).status_code == 200
    # A member cannot, nobody switches off their own or the owner's, and only an owner switches an admin's.
    assert call(api, "post", "access/people/ben", "cara-test", {"sign_in": False}).status_code == 403
    assert call(api, "post", "access/people/ben", "ben-test", {"sign_in": False}).status_code == 403
    assert call(api, "post", "access/people/ana", "ben-test", {"sign_in": False}).status_code == 409
    assert call(api, "post", "access/people/cara", "ben-test", {"sign_in": False}).status_code == 200
    cara = next(p for p in get(api, "access", "ben-test")["people"] if p["id"] == "cara")
    assert (cara["sign_in"], cara["can_sign_in"], cara["left"]) == (False, False, False)
    # Off: still on the roster, refused at every door, and cannot be made owner.
    assert api.get("/api/me", headers=headers("cara-test")).status_code == 403
    moved = call(api, "post", "access/owner", "ana-test", {"person": "cara", "expected_revision": 1, "confirm": True})
    assert moved.status_code == 409 and moved.json()["error"]["code"] == "sign_in"
    assert call(api, "post", "access/people/cara", "ben-test", {"sign_in": True}).status_code == 200
    assert api.get("/api/me", headers=headers("cara-test")).status_code == 200


def test_domain_sign_in_is_the_company_domain_on_the_allow_list(api):
    view = get(api, "access", "ana-test")
    assert (view["home_domain"], view["domain_sign_in"], view["directory"]) == ("acme.example", False, "")
    auth = api.app.state.auth
    assert not auth.admits("dan@acme.example")
    body = {"allowed": view["allowed"], "allowed_domains": ["acme.example"], "expected_revision": view["revision"]}
    assert call(api, "put", "access/allow", "ben-test", body).status_code == 403          # the owner's alone
    assert call(api, "put", "access/allow", "ana-test", body).status_code == 200
    view = get(api, "access", "ana-test")
    assert view["domain_sign_in"] is True and view["allowed_domains"] == ["acme.example"]
    assert auth.admits("dan@acme.example") and not auth.admits("dan@other.example")


GMAIL_SEND = {"service": "gmail", "identity": "cara@acme.example", "can": ["read", "draft", "send"]}


def test_botops_changes_a_bots_tools_as_the_requester_who_owns_it_and_never_for_a_bot_that_is_not_hers(api, botops):
    attempt = turn(api, botops, text="Let my inbox bot send mail")
    register(api, attempt, "cara-mail", template="inbox")
    # With the header the requester's rights decide; without it (an older client) the turn's person still does.
    delegated = {**headers(attempt["token"]), "X-Tico-On-Behalf-Of": "turn"}
    with_header = api.post("/api/v2/bots/cara-mail/tools", json=GMAIL_SEND, headers=delegated)
    assert with_header.status_code == 200, with_header.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT requested_by FROM bot_tool_requests WHERE bot='cara-mail'").fetchone()[0] == "human:cara"
    api_no_header = api.post("/api/v2/bots/cara-mail/tools", json={**GMAIL_SEND, "identity": "cara@other.example"},
                             headers=headers(attempt["token"]))
    assert api_no_header.status_code == 200, api_no_header.text
    # A bot that is not hers stays closed, with or without the header: owner-only protection is unchanged.
    assert api.post("/api/v2/bots/ops/tools", json=GMAIL_SEND, headers=delegated).status_code == 403
    assert api.post("/api/v2/bots/ops/tools", json=GMAIL_SEND, headers=headers(attempt["token"])).status_code == 403
    # No person's turn, no authority: a plain call from BotOps with nobody asking is still refused.
    finish(api, botops, attempt)
    idle = api.post("/api/v2/bots/cara-mail/tools", json={**GMAIL_SEND, "identity": "x@acme.example"},
                    headers=headers(botops["token"]))
    assert idle.status_code in (401, 403)

