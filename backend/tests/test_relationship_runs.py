import json

from backend import (
    bot_settings,
    execution,
    holds,
    mailbox_access,
    model_subscriptions,
    models_preset,
    outside_intake,
    relationship_runs as runs,
    threads,
)
from backend.auth import Identity
from backend.entities import store as entities
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, headers, post, runner, setup_attempt
from backend.tests.helpers.credentials import create as create_credential, setup as setup_credentials
from clients.sessions import entity_lane


def worker(api):
    machine = runner(api)
    assign(api, machine, runs.BOT)
    return machine


def heartbeat(api, machine, *, relationship_lanes=True, authenticated="ready", runtime="claude", turn_user=None):
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "mac",
            "readiness": {
                "schema_version": 1,
                "isolated": turn_user is not None,
                **({"relationship_lanes": True} if relationship_lanes else {}),
                "bots": {
                    runs.BOT: {
                        "ready": True,
                        "repository_present": False,
                        "configuration_valid": True,
                        "runtime": runtime,
                        "sign_in": "ready",
                    }
                },
                "runtimes": {
                    runtime: {"installed": True, "authenticated": authenticated, "turn_user": turn_user or ""}
                },
            },
            "capabilities": ["task_lanes_v2"],
        },
        token=machine["token"],
    )


def memory(api, entity, name):
    fid = post(api, "pages", {"name": name, "text": "<p>Relationship notes</p>"})["page"]["file_id"]
    post(api, f"pages/{fid}/links", {"target": entity_lane(entity), "role": "memory"})
    return fid


def test_relationship_worker_claims_independent_lanes_and_reads_only_its_relationship(api, monkeypatch):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        scopes = [
            entities.upsert(c, owner.actor, "organization", name=name, match={})["id"] for name in ("North", "South")
        ]
        for index, entity in enumerate(scopes):
            runs.enqueue(c, auth, owner, entity, f"Summarize relationship {index}")
        runs.enqueue(c, auth, owner, scopes[0], "Next turn on the first relationship")

    tasks = [
        post(
            api,
            "tasks",
            {"title": name, "body": "Update requested.", "owner": "human:ana", "private": False, "entity": entity},
        )["id"]
        for name, entity in zip(("North task", "South task"), scopes, strict=True)
    ]
    pages = [memory(api, entity, f"memory-{i}.html") for i, entity in enumerate(scopes)]
    assert api.get(f"/api/v2/bots/{runs.BOT}", headers=headers("cara-test")).status_code == 404
    machine = worker(api)
    heartbeat(api, machine, relationship_lanes=False)
    assert claim(api, machine, runs.BOT) is None
    # The runner last reported the bot ready via a company subscription, which this worker never receives.
    monkeypatch.setattr(model_subscriptions, "covered_runtimes", lambda *_: {"claude": "claude"})
    heartbeat(api, machine, authenticated="missing")
    assert claim(api, machine, runs.BOT) is None
    heartbeat(api, machine)
    attempts = [claim(api, machine, runs.BOT), claim(api, machine, runs.BOT)]
    assert all(attempts)
    assert {a["lane"] for a in attempts} == {entity_lane(entity) for entity in scopes}
    assert claim(api, machine, runs.BOT) is None

    for index, attempt in enumerate(attempts):
        own = [entity_lane(e) for e in scopes].index(attempt["lane"])
        token = headers(attempt["token"])
        assert api.get(f"/api/v2/tasks/{tasks[own]}", headers=token).status_code == 200
        thread = attempt["conversation"]["id"]
        timeline = api.get(f"/api/v2/threads/{thread}/timeline", headers=token)
        assert timeline.status_code == 200
        assert attempt["message"]["body"].startswith(f"Summarize relationship {own}")
        assert [msg["id"] for msg in attempt["history"]] == [attempt["message"]["id"]]
        assert api.get(f"/api/v2/tasks/{tasks[1 - own]}", headers=token).status_code == 404
        assert api.get(f"/api/v2/pages/{pages[own]}", headers=token).status_code == 200
        assert api.get(f"/api/v2/pages/{pages[1 - own]}", headers=token).status_code == 404
        assert api.get(f"/api/v2/threads/{attempts[1 - index]['conversation']['id']}", headers=token).status_code == 404
        assert api.post(f"/api/v2/tasks/{tasks[own]}", json={"title": "Overwrite"}, headers=token).status_code == 403
        post(api, f"attempts/{attempt['id']}/renew", {}, token=attempt["token"], expected=403)
        for path in ("tasks", "threads/search", "files/search", "credentials"):
            assert api.get(f"/api/v2/{path}", headers=token).status_code == 403


def test_relationship_vault_delivers_only_the_workers_model_grants(api):
    setup_credentials(api)
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        entity = entities.upsert(c, owner.actor, "organization", name="North", match={})["id"]
        runs.enqueue(c, auth, owner, entity, "Summarize")
        bot_settings.write(c, runs.BOT, {**bot_settings.read(c, runs.BOT), "runtime": "gemini", "harness": "gemini"})

    model_key = create_credential(api, name="Gemini model key", env="GEMINI_API_KEY", secret="synthetic-model-key")
    tool_key = create_credential(api)
    sponsor_key = create_credential(api, name="Sponsor model key", env="GOOGLE_API_KEY", secret="synthetic-sponsor-key")
    for credential, subject in ((model_key, runs.ACTOR), (tool_key, runs.ACTOR), (sponsor_key, owner.actor)):
        post(api, f"credentials/{credential['id']}/grants", {"subject": subject})

    machine = worker(api)
    grants = get(api, "runner-credential-grants", machine["token"])["bots"]
    assert grants[runs.BOT] == ["GEMINI_API_KEY"]
    heartbeat(api, machine, runtime="gemini", turn_user="bot")
    attempt = claim(api, machine, runs.BOT)
    assert attempt["config"]["runtime"] == "gemini"
    assert attempt["credential_vault"] is True
    data = get(api, "credential-runtime", attempt["token"])
    assert set(data) == {"credentials"}
    assert [item["id"] for item in data["credentials"]] == [model_key["id"]]
    assert data["credentials"][0]["value"] == "synthetic-model-key"
    post(api, f"credentials/{tool_key['id']}/reveal", {}, token=attempt["token"], expected=403)


def test_relationship_gateway_waits_for_an_isolated_runners_turn_user_pool(api):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        entity = entities.upsert(c, owner.actor, "organization", name="North", match={})["id"]
        runs.enqueue(c, auth, owner, entity, "Summarize")
        bot_settings.write(
            c, runs.BOT, {**bot_settings.read(c, runs.BOT), "runtime": "pi", "model": models_preset.WAITING_MODEL}
        )

    machine = worker(api)
    heartbeat(api, machine, runtime="pi", turn_user="bot")
    assert claim(api, machine, runs.BOT) is None
    heartbeat(api, machine, runtime="pi", turn_user="own")
    assert claim(api, machine, runs.BOT)["gateway"] is True


def test_relationship_reads_use_current_sponsor_grants_and_strip_other_page_refs(api):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        north, south = [
            entities.upsert(c, owner.actor, "organization", name=name, match={})["id"] for name in ("North", "South")
        ]
        c.execute("UPDATE entity_sources SET visibility='company' WHERE entity_id=?", (north,))
        runs.enqueue(c, auth, auth.identity_for_actor(c, "human:cara"), north, "Summarize")
    task = post(
        api,
        "tasks",
        {
            "title": "Renewal",
            "body": "Review the renewal terms.",
            "owner": "ana",
            "private": True,
            "readers": ["cara"],
            "entity": north,
        },
    )
    tid = task["id"]
    north_page, south_page = memory(api, north, "north.html"), memory(api, south, "south.html")
    with store.transaction() as c:
        # Exercise stored audiences even though new memory links require a company-visible page.
        c.execute("UPDATE pages SET audience=? WHERE file_id=?", (json.dumps({"people": ["cara"]}), north_page))
        room = H.conversation(c, H.task(c, tid)["conversation_id"])
        H._write_message(
            c, owner.actor, "", "Renewal context", room, "comment", {"materials": {"file_id": south_page}}, None, None
        )
    machine = worker(api)
    heartbeat(api, machine)
    attempt = claim(api, machine, runs.BOT)
    token = headers(attempt["token"])
    shown = api.get(f"/api/v2/tasks/{tid}", headers=token)
    assert shown.status_code == 200
    assert "Renewal context" in shown.text
    assert south_page not in shown.text
    for path in (f"pages/{north_page}", f"files/{north_page}", f"files/{north_page}/versions/1"):
        assert api.get(f"/api/v2/{path}", headers=token).status_code == 200
    # Removing grants takes effect for the same attempt even after it has read the objects.
    with store.transaction() as c:
        c.execute("UPDATE tasks SET readers='[]' WHERE id=?", (tid,))
        c.execute("UPDATE pages SET audience=? WHERE file_id=?", (json.dumps({"people": ["ben"]}), north_page))
    for path in (f"tasks/{tid}", f"pages/{north_page}", f"files/{north_page}/versions/1"):
        assert api.get(f"/api/v2/{path}", headers=token).status_code == 404


def test_relationship_page_links_and_pins_follow_the_bound_entity(api):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        north, south = [
            entities.upsert(c, owner.actor, "organization", name=name, match={})["id"] for name in ("North", "South")
        ]
        runs.enqueue(c, auth, owner, north, "Summarize")
    page = memory(api, north, "north.html")
    post(api, f"pages/{page}/links", {"target": entity_lane(south), "role": "related"})
    with store.transaction() as c:
        c.execute(
            "UPDATE entity_sources SET source_type='note_me',source_id=?,visibility='restricted' WHERE entity_id=?",
            (owner.actor, north),
        )
    own, other = [
        post(api, "tasks", {"title": name, "body": "Review", "owner": "ana", "entity": entity})["id"]
        for name, entity in (("North task", north), ("South task", south))
    ]
    for place in ("dashboard", f"task:{own}", f"task:{other}"):
        post(api, "pins", {"place": place, "item": f"page:{page}"})
    machine = worker(api)
    heartbeat(api, machine)
    attempt = claim(api, machine, runs.BOT)
    response = api.get(f"/api/v2/pages/{page}", headers=headers(attempt["token"]))
    assert response.status_code == 200
    shown = response.json()
    assert [link["target"] for link in shown["page"]["links"] if link["target"].startswith("entity:")] == [
        entity_lane(north)
    ]
    assert [pin["place"] for pin in shown["pinned"]] == [f"task:{own}"]


def intake_source(c, mailbox, owner):
    sender = "person@customer.example"
    entity = entities.upsert(c, owner.actor, "person", match={"email": sender})["id"]
    outside_intake.save_message(
        c,
        channel="gmail",
        connection_id=mailbox,
        owner_id=H.actor_id(owner.actor),
        source_thread_id="support",
        source_message_id="delivery",
        sender=sender,
        verified_id=sender,
        title="Customer mail",
        body="Customer asks about renewal",
    )
    source = c.execute("SELECT id FROM conversations WHERE entity_id=?", (entity,)).fetchone()[0]
    return entity, source


def test_relationship_source_threads_apply_scope(api):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        mailbox = mailbox_access.ensure_mailbox(c, owner.email, person_id=H.actor_id(owner.actor))["id"]
        north, source = intake_source(c, mailbox, owner)
        south = entities.upsert(c, owner.actor, "organization", name="South", match={})["id"]
        mailbox_access.grant(c, owner.actor, mailbox, "person", "cara", "read")
    own, other = [
        post(
            api,
            "tasks",
            {
                "title": title,
                "body": "Review the renewal terms.",
                "owner": "ana",
                "private": True,
                "readers": ["cara"],
                "entity": entity,
            },
        )["id"]
        for title, entity in (("North renewal", north), ("South renewal", south))
    ]
    north_page, south_page = memory(api, north, "north.html"), memory(api, south, "south.html")
    _, _, foreign_run = setup_attempt(api)
    with store.transaction() as c:
        unrelated = threads.create(c, owner, audience="company", title="Unrelated")["id"]
        message = threads.post(c, owner, source, "In scope", refs={"task": own})["id"]
        threads.post(c, owner, source, "Outside scope", refs={"task": other})
        threads.post(c, owner, source, "Current page", refs={"target": north_page})
        foreign = threads.post(c, owner, source, "Foreign page", refs={"target": south_page})["id"]
        threads.post(c, owner, source, "Foreign reply", in_reply_to=foreign)
        c.execute(
            "UPDATE messages SET refs=? WHERE id=?",
            (json.dumps({"target": south_page}), foreign_run["message"]["id"]),
        )
        threads.post(c, owner, source, "Foreign run output", refs={"turn_id": foreign_run["id"]})
        sub = threads.create(c, owner, parent_message_id=message)["id"]
        c.execute("UPDATE conversations SET task_id=? WHERE id=?", (other, sub))
        runs.enqueue(c, auth, auth.identity_for_actor(c, "human:cara"), north, "Summarize source", thread_id=source)
    sponsor_timeline = api.get(f"/api/v2/threads/{source}/timeline", headers=headers("cara-test"))
    assert sponsor_timeline.status_code == 200
    hidden = ("Foreign page", "Foreign reply", "Foreign run output")
    assert all(body in sponsor_timeline.text for body in hidden)
    machine = worker(api)
    heartbeat(api, machine)
    attempt = claim(api, machine, runs.BOT)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "relationship-thread"}, token=machine["token"])
    token = headers(attempt["token"])
    timeline = api.get(f"/api/v2/threads/{source}/timeline", headers=token)
    assert timeline.status_code == 200
    assert "In scope" in timeline.text and "Outside scope" not in timeline.text
    assert "Current page" in timeline.text
    assert all(body not in timeline.text for body in hidden)
    for tid in (sub, unrelated):
        assert api.get(f"/api/v2/threads/{tid}", headers=token).status_code == 404
    identity = Identity(runs.ACTOR, "bot", attempt_id=attempt["id"])
    with store.read_transaction() as c:
        expected = {source, attempt["conversation"]["id"]}
        assert threads.readable_ids(c, threads.Viewer(auth, identity), [*expected, sub, unrelated]) == expected


def test_relationship_source_read_stops_when_the_sponsors_inbox_grant_is_revoked(api):
    store, auth = api.app.state.store, api.app.state.auth
    address = "support@acme.example"
    with store.transaction() as c:
        sponsor = auth.identity_for_actor(c, "human:cara")
        mailbox = mailbox_access.ensure_mailbox(c, address, person_id="cara")["id"]
        entity, source = intake_source(c, mailbox, sponsor)
        runs.enqueue(c, auth, sponsor, entity, "Summarize source", thread_id=source)
        mailbox_access.grant(c, sponsor.actor, mailbox, "bot", runs.BOT, "read")
        root = threads.thread_row(c, source)
        threads._with_participants(c, root, [sponsor.actor, "human:ana", runs.ACTOR])

    path = f"threads/{source}/timeline"
    get(api, path, expected=404)
    machine = worker(api)
    heartbeat(api, machine)
    attempt = claim(api, machine, runs.BOT)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "relationship-thread"}, token=machine["token"])
    assert "Customer asks about renewal" in json.dumps(get(api, path, token=attempt["token"]))

    # Personal ownership is implicit. Reconnect as a shared inbox so the sponsor's access is revocable.
    with store.transaction() as c:
        mailbox_access.stop(c, sponsor.actor, mailbox)
    shared = post(api, "mailboxes", {"address": address, "shared": True})["mailbox"]
    assert (shared["id"], shared["kind"]) == (mailbox, "shared")
    with store.transaction() as c:
        grant = mailbox_access.grant(c, "human:ana", mailbox, "person", "cara", "read")
    get(api, path, token=attempt["token"])
    with store.transaction() as c:
        mailbox_access.revoke(c, "human:ana", mailbox, grant["id"])

    get(api, path, token="cara-test", expected=404)
    get(api, path, token=attempt["token"], expected=403)
    with store.read_transaction() as c:
        active = threads.Viewer(auth, Identity(runs.ACTOR, "bot", attempt_id=attempt["id"]))
        assert threads.readable_ids(c, active, [source]) == set()
        assert mailbox_access.level(c, runs.ACTOR, address) == "read"
        standing = threads.Viewer(auth, Identity(runs.ACTOR, "bot"))
        assert not threads.readable(c, standing, source)
        assert threads.readable_ids(c, standing, [source]) == set()


def test_relationship_lane_head_in_claim_backoff_does_not_block_other_lanes(api):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        north, south = [
            entities.upsert(c, owner.actor, "organization", name=name, match={})["id"] for name in ("North", "South")
        ]
        first = runs.enqueue(c, auth, owner, north, "First north turn")
        second = runs.enqueue(c, auth, owner, north, "Second north turn")
        separate = runs.enqueue(c, auth, owner, south, "South turn")
    machine = worker(api)
    heartbeat(api, machine)
    with store.transaction() as c:
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (first,)).fetchone()["id"]
        hold = holds.put(
            c,
            holds.CLAIM_ERROR,
            "job",
            job,
            actor=H.TICO_ACTOR,
            bot_id=runs.BOT,
            until_at=H.shift(H.now(), seconds=300),
        )
    with store.read_transaction() as c:
        args = (c, store.settings, machine["runner_id"], runs.BOT)
        assert {job["message_id"]: why for job, why in execution.queued_reasons(*args)} == {
            first: holds.CLAIM_HELD,
            second: "lane_held",
            separate: None,
        }
    message = claim(api, machine, runs.BOT)["message"]
    assert message["id"] == separate, message["body"]
    assert claim(api, machine, runs.BOT) is None
    with store.read_transaction() as c:
        assert execution.bot_claimable(c, store.settings, machine["runner_id"], runs.BOT)[0] == holds.CLAIM_HELD
    with store.transaction() as c:
        c.execute("UPDATE holds SET until_at=? WHERE id=?", (H.shift(H.now(), seconds=-1), hold["id"]))
    assert claim(api, machine, runs.BOT)["message"]["id"] == first


def test_relationship_wake_names_only_records_the_sponsor_can_open_at_claim(api):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        north, south = [
            entities.upsert(c, owner.actor, "organization", name=name, match={})["id"] for name in ("North", "South")
        ]
        c.execute("UPDATE entity_sources SET visibility='company' WHERE entity_id=?", (north,))
        runs.enqueue(c, auth, auth.identity_for_actor(c, "human:cara"), north, "Find the open work")
    page, other_page = memory(api, north, "north.html"), memory(api, south, "south.html")
    open_ids = [
        post(
            api,
            "tasks",
            {
                "title": f"Open {index}",
                "body": "Review",
                "owner": "ana",
                "entity": north,
                "private": True,
                "readers": ["cara"],
            },
        )["id"]
        for index in range(28)
    ]
    revoked, closed, other = [
        post(
            api,
            "tasks",
            {"title": name, "body": "Review", "owner": "ana", "entity": entity, "private": True, "readers": ["cara"]},
        )["id"]
        for name, entity in (("Revoked", north), ("Closed", north), ("Other", south))
    ]
    with store.transaction() as c:
        c.execute("UPDATE tasks SET readers='[]' WHERE id=?", (revoked,))
        c.executemany(
            "UPDATE tasks SET created_at=? WHERE id=?",
            [(f"2026-01-{index:02d}T00:00:00Z", tid) for index, tid in enumerate(open_ids, 1)],
        )
    post(api, f"tasks/{closed}/move", {"to": "closed", "why": "Already handled."})
    machine = worker(api)
    heartbeat(api, machine)
    body = claim(api, machine, runs.BOT)["message"]["body"]
    assert page in body
    assert body.endswith("Open tasks " + ", ".join(open_ids[:2:-1]) + " (3 more).")
    assert all(ident not in body for ident in (other_page, revoked, closed, other, *open_ids[:3]))
