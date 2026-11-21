"""Access requests for Tools tools (backend/access_requests.py): Read where it is enforceable is granted at once, the
rest reaches everyone who can grant it as one card each, the first tap resolves every copy, a repeat notifies nobody, and
a request closes itself however the access arrives, waking the task that asked. Ana owns the company; Ana and Ben are
credential administrators; Cara is a member; the ops bot asks; the Tools page's owner is the support bot."""

import json

from backend import access_requests as AR
from backend import reconnects
from backend.tests.helpers.api import get, headers, post, put
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.cards import task
from backend.tests.helpers.credentials import setup

SECRET = "acme-close-synthetic-rw-0011223344"
OTHER = "acme-close-synthetic-rw-5566778899"
DB_URL = "postgresql://writer:acme-synthetic-db-7788@db.acme.example/app"


def world(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('support','support','Tools Owner','active')")
    put(api, "page-owners/tools", {"bot": "support"})


def ask(api, resource, level="read", token="ana-test", expected=200, **more):
    return post(
        api,
        "access-requests",
        {"resource": resource, "level": level, "for_bot": "ops", **more},
        token,
        expected=expected,
    )


def switch(api, tid, on):
    r = api.patch(f"/api/v2/api-tools/{tid}", json={"everyone_reads": on}, headers=headers())
    assert r.status_code == 200, r.text


def lv(api, tid, subject="bot:ops"):
    from backend import api_tools

    with api.app.state.store.read() as c:
        return api_tools.subject_level(c, tid, subject)


def row(api, rid):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM access_requests WHERE id=?", (rid,)).fetchone())


def cards(api, rid):
    """{person: the card as their copy shows it}."""
    with api.app.state.store.read() as c:
        found = json.loads(c.execute("SELECT cards FROM access_requests WHERE id=?", (rid,)).fetchone()[0])
        return {
            person: json.loads(c.execute("SELECT refs FROM messages WHERE id=?", (mid,)).fetchone()[0])[
                "access_request"
            ]
            for person, mid in found.items()
        }


def card_count(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM messages WHERE json_extract(refs,'$.card')='access_request'").fetchone()[
            0
        ]


def test_read_is_granted_at_once_only_where_it_is_enforceable_and_writes_and_sensitive_kinds_ask(api):
    world(api)
    # Two PostHog projects deliver one variable: every bot reads neither by default (several to choose from).
    cro = tool(api, "PostHog CRO", base_url="https://eu.posthog.acme.example", service="posthog")["id"]
    web = tool(api, "PostHog Web", base_url="https://us.posthog.acme.example", service="posthog")["id"]
    for tid, value in ((cro, SECRET), (web, OTHER)):
        post(
            api,
            f"api-tools/{tid}/keys",
            {"name": tid, "value": value, "access_level": "read_write", "env": "POSTHOG_KEY"},
        )
    assert lv(api, cro) is None
    done = ask(api, "PostHog CRO", why="the funnel report")
    assert (done["state"], done["auto"], done["kind"]) == ("granted", True, "tool_read") and lv(api, cro) == "read"
    assert lv(api, web) is None  # the request names which one
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor, detail FROM events WHERE action='credential.granted'").fetchall()[-1]
    assert event["actor"] == "keeper" and json.loads(event["detail"])["request"] == done["id"]
    # The connector (Ana added the tool) gets one notice card, and takes it back in one tap.
    assert list(cards(api, done["id"])) == ["human:ana"] and cards(api, done["id"])["human:ana"]["auto"] is True
    post(api, f"access-requests/{done['id']}/take-back", {}, "cara-test", expected=403)
    assert post(api, f"access-requests/{done['id']}/take-back", {})["state"] == "revoked" and lv(api, cro) is None

    # A tool every bot already reads answers at once and keeps nothing.
    crm = tool(api)["id"]
    key(api, crm, SECRET + "-crm")
    assert ask(api, crm)["already"] is True

    # Writes, a tool switched off, Only listed and a Read that cannot be held to reads all ask a person.
    assert ask(api, crm, "read_write")["state"] == "open"
    off = tool(api, "Acme Ads", base_url="https://ads.acme.example")["id"]
    key(api, off, SECRET + "-ads")
    switch(api, off, False)
    assert ask(api, off)["state"] == "open"
    db = tool(api, "Acme Warehouse")["id"]
    post(api, f"api-tools/{db}/keys", {"name": "Warehouse", "value": DB_URL, "access_level": "read_write"})
    assert ask(api, db)["state"] == "open"
    bank = tool(api, "Acme Bank", base_url="https://api.bank.acme.example")["id"]
    key(api, bank, SECRET + "-bank")
    r = api.patch(
        f"/api/v2/api-tools/{bank}",
        json={"audience": {"everyone": False, "people": [], "teams": [], "bots": []}},
        headers=headers(),
    )
    assert r.status_code == 200, r.text
    assert ask(api, bank)["state"] == "open"
    assert lv(api, off) is None and lv(api, db) is None and lv(api, bank) is None


def test_a_request_reaches_everyone_who_can_grant_it_and_the_first_tap_resolves_every_copy(api):
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    asked = ask(api, crm, "read_write", why="update deals")
    assert asked["state"] == "open" and len(asked["asked"]) == 2
    copies = cards(api, asked["id"])
    assert set(copies) == {"human:ana", "human:ben"} and all(c["state"] == "open" for c in copies.values())
    assert list(json.loads(row(api, asked["id"])["approvers"]))[0] == "human:ana"  # the connector first
    post(api, f"access-requests/{asked['id']}/grant", {}, "cara-test", expected=403)  # not someone who can grant it
    done = post(api, f"access-requests/{asked['id']}/grant", {}, "ben-test")
    assert done["state"] == "granted" and lv(api, crm) == "read_write"
    copies = cards(api, asked["id"])
    assert {c["state"] for c in copies.values()} == {"granted"} and copies["human:ana"]["by"] == "Ben"
    assert row(api, asked["id"])["resolved_via"] == "card"
    # Ana's later tap finds it resolved and changes nothing; a decline after it does nothing either.
    assert post(api, f"access-requests/{asked['id']}/grant", {})["state"] == "granted"
    assert post(api, f"access-requests/{asked['id']}/decline", {})["state"] == "granted"


def test_a_repeat_counts_on_the_open_request_and_notifies_nobody_again(api):
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    first = ask(api, crm, "read_write")
    before = card_count(api)
    again = ask(api, crm, "write")
    assert again["id"] == first["id"] and again["repeat"] is True and card_count(api) == before
    assert row(api, first["id"])["repeats"] == 1
    # Another level is another request.
    assert ask(api, "Close CRM", "read")["already"] is True


def test_a_request_closes_itself_by_every_grant_path(api):
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    # The Tools page's level.
    on_page = ask(api, crm, "read_write")
    level(api, crm, "bot:ops", "read_write")
    assert (row(api, on_page["id"])["state"], row(api, on_page["id"])["resolved_via"]) == ("granted", "tools")
    assert {c["state"] for c in cards(api, on_page["id"]).values()} == {"granted"}
    # Everyone can read turning on.
    ads = tool(api, "Acme Ads", base_url="https://ads.acme.example")["id"]
    key(api, ads, SECRET + "-ads")
    switch(api, ads, False)
    waiting = ask(api, ads)
    switch(api, ads, True)
    assert (row(api, waiting["id"])["state"], row(api, waiting["id"])["resolved_via"]) == ("granted", "everyone-reads")
    # A stored credential granted straight to the bot (the vault's own route, the Tools Owner's `tico credential grant`).
    files = tool(api, "Acme Files")["id"]
    stored = post(
        api, f"api-tools/{files}/keys", {"name": "Files", "value": DB_URL + "/files", "access_level": "read_write"}
    )
    by_api = ask(api, files)
    post(api, f"credentials/{stored['id']}/grants", {"subject": "bot:ops"})
    assert row(api, by_api["id"])["state"] == "granted"
    # A path no write announces: the hourly sweep finds it.
    wh = tool(api, "Acme Warehouse")["id"]
    held = post(api, f"api-tools/{wh}/keys", {"name": "Wh", "value": DB_URL + "/wh", "access_level": "read_write"})
    late = ask(api, wh, "read_write")
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) VALUES('g-late',?,?,?,?)",
            (held["id"], "bot:ops", "human:ana", "2026-11-16T00:00:00Z"),
        )
    assert "Closed 1 requests" in AR.sweep(api.app.state.store)
    assert (row(api, late["id"])["state"], row(api, late["id"])["resolved_via"]) == ("granted", "sweep")


def test_the_task_that_asked_waits_on_the_request_and_is_woken_once_when_it_is_granted_or_declined(api):
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    tid = task(api, "bot:ops", "Update the deal stages")
    asked = ask(api, crm, "read_write", task=tid)
    with api.app.state.store.read() as c:
        assert reconnects.hold(c, tid)["why"] == "Close CRM · Read and write to be granted"
    level(api, crm, "bot:ops", "read_write")  # granted elsewhere: still the task's wake
    with api.app.state.store.read() as c:
        assert reconnects.hold(c, tid) is None
        woke = c.execute(
            "SELECT body FROM messages WHERE recipient_id='bot:ops' AND body LIKE 'Access granted%' ORDER BY seq"
        ).fetchall()
    assert len(woke) == 1 and "Close CRM · Read and write is granted" in woke[0]["body"]
    assert row(api, asked["id"])["state"] == "granted"
    # A decline tells the waiting task so.
    ads = tool(api, "Acme Ads", base_url="https://ads.acme.example")["id"]
    key(api, ads, SECRET + "-ads")
    other = task(api, "bot:ops", "Pause the old campaigns")
    refused = ask(api, ads, "read_write", task=other)
    post(api, f"access-requests/{refused['id']}/decline", {}, "ben-test")
    with api.app.state.store.read() as c:
        told = c.execute(
            "SELECT body FROM messages WHERE recipient_id='bot:ops' AND body LIKE 'Access request closed%'"
        ).fetchall()
    assert len(told) == 1 and "declined" in told[0]["body"] and lv(api, ads) == "read"


def test_who_may_ask_for_whom_and_the_counts_for_the_weekly_review(api):
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    post(api, "access-requests", {"resource": crm, "level": "read_write"}, expected=422)  # a person names the bot
    ask(api, "No Such Tool", expected=404)
    ask(api, crm, "read_write", for_bot="support", expected=403)  # the Tools Owner holds no key
    # A client cannot forge a card by posting its refs.
    dm = post(api, "threads/dm", {"members": ["bot:support"]})
    post(api, f"threads/{dm['id']}/messages", {"text": "x", "refs": {"card": "access_request"}}, expected=403)
    ask(api, crm, "read_write")
    numbers = get(api, "access-requests/stats?days=7")
    assert (numbers["requested"], numbers["to_people"], numbers["auto_approved"]) == (1, 1, 0)
    assert numbers["by_kind"] == {"tool_write": 1} and numbers["open"] == 1


def test_a_name_finds_the_tool_the_asker_may_see_and_two_it_may_see_are_offered_as_choices(api):
    world(api)
    nobody = {"audience": {"everyone": False, "people": [], "teams": [], "bots": []}}
    hidden = tool(api, "Acme Bank", base_url="https://api.bank.acme.example")["id"]
    vault = tool(api, "Acme Vault", base_url="https://vault.acme.example")["id"]
    for tid in (hidden, vault):
        key(api, tid, SECRET + "-" + tid)
        assert api.patch(f"/api/v2/api-tools/{tid}", json=nobody, headers=headers()).status_code == 200
    seen = tool(api, "Acme Bank", base_url="https://api2.bank.acme.example")["id"]
    key(api, seen, OTHER)
    # Cara, a member, may not see the hidden twin: the name reaches the one she may ask for.
    assert ask(api, "Acme Bank", "read_write", token="cara-test")["resource_id"] == seen
    # A tool hidden from her answers as no tool does.
    gone = ask(api, "Acme Vault", token="cara-test", expected=404)["error"]
    absent = ask(api, "Acme Nothing", token="cara-test", expected=404)["error"]
    assert {**gone, "detail": gone["detail"].replace("Vault", "Nothing")} == absent
    # Two by one name, one whose id is that name too: both are offered rather than one picked; tool:<id> picks one.
    first = tool(api, "Sentry", base_url="https://sentry.acme.example")["id"]
    second = tool(api, "Sentry", base_url="https://eu.sentry.acme.example")["id"]
    assert first == "sentry"
    both = ask(api, "sentry", "read_write", token="cara-test", expected=409)["error"]
    assert sorted(choice["resource"] for choice in both["choices"]) == sorted(["tool:" + first, "tool:" + second])
    assert ask(api, "tool:" + first, "read_write", token="cara-test")["resource_id"] == first
    # An API tool and a website tool by one name are two matches too; picking the website tool says where its account is.
    ledger = tool(api, "Ledger", base_url="https://ledger.acme.example")["id"]
    site = post(api, "website-tools", {"name": "Ledger", "sites": ["ledger.acme.example"]})["id"]
    two = ask(api, "Ledger", token="cara-test", expected=409)["error"]
    assert sorted(choice["resource"] for choice in two["choices"]) == sorted(["tool:" + ledger, "tool:" + site])
    assert ask(api, "tool:" + site, token="cara-test", expected=422)["error"]["code"] == "resource"
    assert ask(api, "api:Ledger", token="cara-test")["resource_id"] == ledger  # `api:` names the API tool
    # A website tool named Pager and an API tool whose id is `pager` are two matches for a bare "pager" too.
    pager = tool(api, "Pager", base_url="https://pager.acme.example")["id"]
    assert pager == "pager"
    assert api.patch("/api/v2/api-tools/pager", json={"name": "Paging API"}, headers=headers()).status_code == 200
    page = post(api, "website-tools", {"name": "Pager", "sites": ["pager.acme.example"]})["id"]
    three = ask(api, "pager", token="cara-test", expected=409)["error"]
    assert sorted(choice["resource"] for choice in three["choices"]) == sorted(["tool:pager", "tool:" + page])


def test_titles_of_old_access_tasks_tie_only_when_certain(api):
    assert AR.parse_title("Give Sentry Triage read access to gotidy?") == ("Sentry Triage", ["gotidy"], "read", "")
    assert AR.parse_title(
        "Grant: Acme/app and Acme/web no-token GitHub App read for QA Owner (still missing after earlier grant closed)"
    ) == ("QA Owner", ["Acme/app", "Acme/web"], "read", "repository")
    assert AR.parse_title("Add Raygun (error monitoring) to General Manager's tools, read only") == (
        "General Manager",
        ["Raygun"],
        "read",
        "",
    )
    for vague in (
        "Give Marketing Engineer GitHub access to grader, tfront and guidebook",  # no level for a repository
        "Grant: Acme/app and Acme/web no-token GitHub App read",  # no bot
        "Give Arthur's Frontend Reviewer access to the five front-end repos",
        "Set two bots' repo access to read",
    ):
        assert AR.parse_title(vague) is None, vague
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Ops Bot' WHERE id='ops'")
    tied = task(api, "human:ana", "Give Ops Bot read access to Close CRM")
    loose = task(api, "human:ana", "Give Ops Bot read access to Close CRM from its cloud runner")
    with api.app.state.store.transaction() as c:
        found = {t["task_id"]: t["close"] for t in AR.reconcile_tasks(c)}
        assert found == {tied: True}
        assert c.execute("SELECT status FROM tasks WHERE id=?", (tied,)).fetchone()[0] == "closed"
        assert c.execute("SELECT status FROM tasks WHERE id=?", (loose,)).fetchone()[0] == "open"
    # An Everyone tool behind an Only listed namesake ties; two Everyone tools by the name do not.
    hidden = tool(api, "Acme Bank", base_url="https://api.bank.acme.example")["id"]
    nobody = {"audience": {"everyone": False, "people": [], "teams": [], "bots": []}}
    assert api.patch(f"/api/v2/api-tools/{hidden}", json=nobody, headers=headers()).status_code == 200
    seen = tool(api, "Acme Bank", base_url="https://api2.bank.acme.example")["id"]
    title = "Give Ops Bot read access to Acme Bank"
    with api.app.state.store.read() as c:
        assert AR.tie(c, title) == [("bot:ops", "tool", seen, "read")]
    tool(api, "Acme Bank", base_url="https://api3.bank.acme.example")
    with api.app.state.store.read() as c:
        assert AR.tie(c, title) is None


def test_a_bot_asks_for_itself_from_its_turn_and_that_task_waits(api):
    from backend.tests.helpers.api import assign, claim, ready, runner

    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    created = post(api, "tasks", {"owner": "ops", "title": "Update the deal stages", "body": "Move the won deals."})
    bot = claim(api, machine)["token"]
    post(api, "access-requests", {"resource": crm, "level": "read_write", "for_bot": "support"}, bot, expected=403)
    asked = post(api, "access-requests", {"resource": "Close CRM", "level": "read_write", "why": "move deals"}, bot)
    assert (asked["subject"], asked["task_id"], asked["state"]) == ("bot:ops", created["id"], "open")
    with api.app.state.store.read() as c:
        assert reconnects.hold(c, created["id"]) is not None
    post(api, f"access-requests/{asked['id']}/grant", {}, "ben-test")
    with api.app.state.store.read() as c:
        assert reconnects.hold(c, created["id"]) is None
        assert (
            c.execute(
                "SELECT count(*) FROM messages WHERE recipient_id='bot:ops' AND body LIKE 'Access granted%'"
            ).fetchone()[0]
            == 1
        )


def test_a_tool_with_a_template_is_granted_one_key_per_variable(api):
    world(api)
    fields = [
        {"id": "key", "env": "ACME_KEY", "role": "secret"},
        {"id": "secret", "env": "ACME_SECRET", "role": "secret"},
    ]
    pair = {"fields": fields, "ways": [{"headers": {"X-Api-Key": "{key}", "X-Api-Secret": "{secret}"}}]}
    tid = tool(api, "Acme Pair", base_url="https://api.acme.example")["id"]
    post(api, f"api-tools/{tid}/keys", {"name": "K", "value": SECRET, "env": "ACME_KEY", "access_level": "read_write"})
    put(api, f"api-tools/{tid}/template", {"template": pair})
    post(
        api, f"api-tools/{tid}/keys", {"name": "S", "value": OTHER, "env": "ACME_SECRET", "access_level": "read_write"}
    )
    # Another tool gives ACME_KEY too: several to choose from, so the bot reads neither by default.
    other = tool(api, "Acme Pair EU", base_url="https://eu.api.acme.example")["id"]
    post(api, f"api-tools/{other}/keys", {"name": "K2", "value": SECRET + "-eu", "env": "ACME_KEY"})
    done = ask(api, "Acme Pair")
    assert (done["state"], done["auto"]) == ("granted", True)

    def held():
        from backend import credentials

        with api.app.state.store.read() as c:
            keys = c.execute("SELECT id, env FROM credentials WHERE tool_id=?", (tid,)).fetchall()
            return {
                k["env"]: (credentials.effective_grant(c, k["id"], "bot:ops") or {"access_level": None})["access_level"]
                for k in keys
            }

    assert held() == {"ACME_KEY": "read", "ACME_SECRET": "read"}
    # Read and write on the card: each variable's Read and write key.
    asked = ask(api, "Acme Pair", "read_write")
    post(api, f"access-requests/{asked['id']}/grant", {}, "ben-test")
    assert held() == {"ACME_KEY": "read_write", "ACME_SECRET": "read_write"} and lv(api, tid) == "read_write"


def test_read_only_frees_the_task_waiting_on_the_request_and_tells_it_read_only(api):
    # Read only narrows the request to Read: the task's wait, made for Read and write, still ends, and says Read only.
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    tid = task(api, "bot:ops", "Update the deal stages")
    asked = ask(api, crm, "read_write", task=tid)
    assert post(api, f"access-requests/{asked['id']}/grant", {"level": "read"}, "ben-test")["level"] == "read"
    with api.app.state.store.read() as c:
        assert reconnects.hold(c, tid) is None
        assert c.execute("SELECT count(*) FROM task_reconnects WHERE fixed_at IS NULL").fetchone()[0] == 0
        woke = c.execute(
            "SELECT body FROM messages WHERE recipient_id='bot:ops' AND body LIKE 'Access granted%'"
        ).fetchall()
    assert len(woke) == 1 and "Close CRM · Read only is granted" in woke[0]["body"]
    assert lv(api, crm) == "read"


def test_a_refused_tap_names_nobody_who_could_grant_it(api):
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    asked = ask(api, crm, "read_write")
    for what in ("grant", "decline"):
        r = api.post(f"/api/v2/access-requests/{asked['id']}/{what}", json={}, headers=headers("cara-test"))
        assert r.status_code == 403 and r.json()["error"]["detail"] == "You can't grant this"
        assert "Ana" not in r.text and "Ben" not in r.text and "human:" not in r.text


def test_read_only_frees_a_task_that_waits_on_a_repeat_of_a_request_first_made_in_chat(api):
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    asked = ask(api, crm, "read_write")  # in chat: no task
    tid = task(api, "bot:ops", "Update the deal stages")
    assert ask(api, crm, "read_write", task=tid)["id"] == asked["id"]  # the repeat waits on the same request
    with api.app.state.store.read() as c:
        assert reconnects.hold(c, tid) is not None
    post(api, f"access-requests/{asked['id']}/grant", {"level": "read"}, "ben-test")
    with api.app.state.store.read() as c:
        assert reconnects.hold(c, tid) is None


def test_a_task_whose_older_wait_was_fixed_is_still_woken_when_its_request_is_granted(api):
    world(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    tid = task(api, "bot:ops", "Update the deal stages")
    asked = ask(api, crm, "read_write", task=tid)
    with api.app.state.store.transaction() as c:  # its wait was fixed already, by an older path
        c.execute("UPDATE task_reconnects SET fixed_at=?, ended='fixed', woke_at=? WHERE task_id=?", ("t", "t", tid))
    post(api, f"access-requests/{asked['id']}/grant", {}, "ben-test")
    with api.app.state.store.read() as c:
        woke = c.execute("SELECT body FROM messages WHERE recipient_id='bot:ops' AND body LIKE 'Granted:%'").fetchall()
    assert len(woke) == 1 and "Close CRM · Read and write" in woke[0]["body"]
