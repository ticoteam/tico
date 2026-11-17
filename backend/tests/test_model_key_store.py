"""One store for the model key (migration 20261117061243, backend/computer_keys.py): keys kept for computers in
Settings > Computers move into model_subscriptions + computer_subscriptions, seeded here as live installs hold them,
and a computer keeps signing in with what it ran on before, its own Claude or ChatGPT login included."""

import json
import uuid

from backend import model_subscriptions as MS
from backend.store import H
from backend.tests.helpers.computer_keys import (
    MOVE,
    SHARED,
    call,
    computer,
    fetch,
    heartbeat,
    report,
    setup,
    shared_before,
)
from backend.tests.helpers.computer_signin import store
from backend.tests.helpers.getting_started import add_bot
from backend.tests.helpers.onboarding import as_person, environment, signed_in  # noqa: F401

OWN = "sk-proj-own-key-0123456789"
TEAM = "sk-ant-team-key-0123456789"
NAMED = "sk-ant-named-key-987654321"


def beat(api, machine, runtime, row):
    """A heartbeat reporting `row` for `runtime` (what the computer's tool said)."""
    r = api.post(
        "/api/v2/runners/heartbeat",
        headers={"Authorization": "Bearer " + machine["token"], "Idempotency-Key": uuid.uuid4().hex},
        json={
            "version": "test",
            "platform": "linux",
            "capabilities": ["model_sign_out_v1", "model_keys_v1"],
            "readiness": {"schema_version": 1, "runtimes": {runtime: row}, "bots": {}},
        },
    )
    assert r.status_code == 200, r.text


def own_key(api, machine, runtime, env, value, state="active"):
    """A computer's own key as Settings > Computers kept it before: a vault credential and a computer_keys row."""
    cid = store(api, env, value, name=f"{env} for {machine['runner_id']}")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credentials SET source=? WHERE id=?", ("computer:" + machine["runner_id"], cid))
        c.execute(
            "INSERT INTO computer_keys(credential_id,runner_id,runtime,state,login_id,created_at,created_by) "
            "VALUES(?,?,?,?,?,?,'human:ana')",
            (cid, machine["runner_id"], runtime, state, "login-1" if state == "staged" else None, H.now()),
        )
    return cid


def granted(api, cid, subject):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) VALUES(?,?,?,?,?)",
            (H.new_id(), cid, subject, "human:ana", H.now()),
        )


def move(api):
    with api.app.state.store.transaction() as c:
        MOVE.up(c)


def given(api):
    with api.app.state.store.read() as c:
        return {
            (r["runner_id"], r["runtime"]): (r["name"], r["credential_id"])
            for r in c.execute(
                "SELECT g.runner_id,g.runtime,s.name,s.credential_id FROM computer_subscriptions g "
                "JOIN model_subscriptions s ON s.id=g.subscription_id"
            )
        }


def snapshot(api):
    with api.app.state.store.read() as c:
        return [
            sorted(tuple(r) for r in c.execute(f"SELECT * FROM {t}"))
            for t in ("model_subscriptions", "computer_subscriptions", "computer_keys", "credential_grants")
        ]


def test_computer_keys_move_into_subscriptions_and_each_computer_keeps_what_it_ran_on(environment):
    api = setup(environment)
    alpha = computer(api, "Alpha", source="computer")  # its own OpenAI key
    bravo = computer(api, "Bravo", source="shared", runtime="claude", name="ANTHROPIC_API_KEY")
    charlie = computer(api, "Charlie", beat=False)  # never reported
    login = computer(api, "Delta", beat=False)  # its own Claude login (a subscription it pays for), no key
    beat(api, login, "claude", {"installed": True, "authenticated": "ready"})
    terminal = computer(api, "Echo", source="terminal", runtime="claude", name="ANTHROPIC_API_KEY")
    cleared = computer(api, "Foxtrot", beat=False)
    named = computer(api, "Golf", source="shared", runtime="claude", name="ANTHROPIC_API_KEY")
    held = computer(api, "Hotel", source="computer")  # given a Settings > Models subscription already
    failed = computer(api, "India", beat=False)  # its Claude check failed: it never said which key it ran on
    beat(api, failed, "claude", {"installed": True, "authenticated": "failed", "detail": "timed out"})
    alpha_key = own_key(api, alpha, "codex", "OPENAI_API_KEY", OWN)
    hotel_key = own_key(api, held, "codex", "OPENAI_API_KEY", "sk-proj-hotel-0123456789")
    staged = own_key(api, alpha, "claude", "ANTHROPIC_API_KEY", "sk-ant-staged-0123456789", state="staged")
    team = store(api, "ANTHROPIC_API_KEY", TEAM, name="Team Claude key")
    by_name = store(api, "ANTHROPIC_API_KEY", NAMED, name="Golf's Claude key")
    granted(api, team, "computers")
    granted(api, by_name, "computer:" + named["runner_id"])
    revoked = store(api, "OPENAI_API_KEY", "sk-proj-revoked-0123456789", name="Revoked")
    granted(api, revoked, "computers")
    add_bot(api, "mailer")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credential_grants SET revoked_at=? WHERE credential_id=?", (H.now(), revoked))
        c.execute(
            "INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by,clear_key) "
            "VALUES(?,'claude','',?,'human:ana',0)",
            (cleared["runner_id"], H.now()),
        )
        c.execute(
            "INSERT INTO model_subscriptions(id,name,kind,sealed,added_by,created_at,updated_at) "
            "VALUES('plan','Company OpenAI','openai_key','sealed-elsewhere','human:ana',?,?)",
            (H.now(), H.now()),
        )
        c.execute(
            "INSERT INTO computer_subscriptions(runner_id,runtime,subscription_id,set_by,updated_at) "
            "VALUES(?,'codex','plan','human:ana',?)",
            (held["runner_id"], H.now()),
        )
        bot = H.resolve_bot(c, "mailer")["id"]
        c.execute(
            "INSERT INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,1,?,'human:ana')",
            (bot, alpha["runner_id"], H.now()),
        )
        c.execute(
            "INSERT INTO bot_sessions(bot,runtime,model,thread_id,runner_id,updated_at) VALUES(?,?,?,?,?,?)",
            (bot, "codex", "gpt", "thread-1", alpha["runner_id"], H.now()),
        )
    before = snapshot(api)
    move(api)
    assert given(api) == {
        # 1. Its own key, given to it, the same vault row.
        (alpha["runner_id"], "codex"): ("OpenAI API · Alpha", alpha_key),
        # 2. The "Every computer" key, only where the computer said it ran on it. Alpha, Charlie, Hotel and India never
        # said (no Claude report, or a failed check): they may be on a login of their own, so they are not given it.
        (bravo["runner_id"], "claude"): ("Team Claude key", team),
        # The one shared with it by name comes first.
        (named["runner_id"], "claude"): ("Golf's Claude key", by_name),
        # A Settings > Models subscription stays; the computer's own key is moved in but given to nothing.
        (held["runner_id"], "codex"): ("Company OpenAI", None),
    }
    with api.app.state.store.read() as c:
        moved = c.execute("SELECT * FROM model_subscriptions WHERE credential_id=?", (hotel_key,)).fetchone()
        assert (moved["name"], moved["kind"], moved["sealed"], moved["owner_id"]) == (
            "OpenAI API · Hotel",
            "openai_key",
            "",
            "",
        )
        assert [r[0] for r in c.execute("SELECT credential_id FROM computer_keys")] == [staged], "checked later"
        assert c.execute("SELECT count(*) FROM credential_grants WHERE revoked_at IS NULL").fetchone()[0] == 0
        assert not c.execute("SELECT 1 FROM model_subscriptions WHERE credential_id=?", (revoked,)).fetchone()
        assert c.execute("SELECT count(*) FROM credentials WHERE ciphertext IS NOT NULL").fetchone()[0] == 6
        # 3. Codex on Alpha signs in from a home of its own now: its sessions start fresh.
        assert c.execute("SELECT count(*) FROM bot_sessions").fetchone()[0] == 0
        assert c.execute("SELECT epoch FROM bot_session_epochs WHERE bot=?", (bot,)).fetchone()[0] == 1
    # Each computer fetches what it fetched before, in the reply runners of v0.6.0-rc.1 read.
    assert fetch(api, alpha) == {"credentials": [{"env": "OPENAI_API_KEY", "value": OWN, "source": "computer"}]}
    assert fetch(api, bravo, runtime="claude") == {
        "credentials": [{"env": "ANTHROPIC_API_KEY", "value": TEAM, "source": "shared"}]
    }
    assert fetch(api, named, runtime="claude")["credentials"][0]["value"] == NAMED
    assert fetch(api, cleared, runtime="claude") == {"credentials": [], "signed_out": True}
    assert fetch(api, terminal, runtime="claude") == {"credentials": []}
    assert fetch(api, held) == {"credentials": []}, "a Settings > Models subscription is not sent here"
    # Billing does not change: Delta keeps its own Claude login, Echo its terminal key, and a computer that never said
    # which key it ran on is handed none.
    assert fetch(api, login, runtime="claude") == {"credentials": []}
    for unsure in (charlie, failed):
        assert fetch(api, unsure, runtime="claude") == {"credentials": []}
    with api.app.state.store.read() as c:
        for box in (login, terminal, charlie, failed):
            assert MS.in_effect(c, box["runner_id"], "claude") is None
    # Running it again changes nothing.
    after = snapshot(api)
    assert after != before
    move(api)
    assert snapshot(api) == after


def test_a_moved_key_reads_from_the_vault_and_a_new_computer_gets_none_by_itself(environment):
    api = setup(environment)
    one = computer(api, "Alpha", beat=False)
    cursor = own_key(api, one, "cursor", "CURSOR_API_KEY", "cursor-key-0123456789")
    team = store(api, "OPENAI_API_KEY", OWN, name="Team OpenAI")
    granted(api, team, "computers")
    move(api)
    assert fetch(api, one, runtime="cursor")["credentials"] == [
        {"env": "CURSOR_API_KEY", "value": "cursor-key-0123456789", "source": "computer"}
    ]
    with api.app.state.store.read() as c:
        sub = c.execute("SELECT * FROM model_subscriptions WHERE credential_id=?", (cursor,)).fetchone()
        assert (sub["kind"], sub["name"]) == ("cursor_key", "Cursor API · Alpha")
        assert MS.value_of(c, api.app.state.vault.cipher, sub) == "cursor-key-0123456789"
        shared = c.execute("SELECT * FROM model_subscriptions WHERE credential_id=?", (team,)).fetchone()
    # A computer enrolled after the upgrade is not given the shared key by itself.
    fresh = computer(api, "Bravo", beat=False)
    assert fetch(api, fresh) == {"credentials": []}
    # Settings > Models lists both, each with its kind; no value leaves.
    body = call(api, "GET", "model-subscriptions")
    listed = {s["id"]: s for s in body["subscriptions"]}
    assert {listed[sub["id"]]["kind"], listed[shared["id"]]["kind"]} == {"cursor_key", "openai_key"}
    assert OWN not in json.dumps(body) and "cursor-key" not in json.dumps(body)
    # Given from there, it reaches that computer, from the vault.
    call(
        api,
        "PUT",
        "computer-subscriptions",
        {"runner_id": fresh["runner_id"], "runtime": "codex", "subscription": shared["id"], "confirm": True},
    )
    assert fetch(api, fresh)["credentials"] == [{"env": "OPENAI_API_KEY", "value": OWN, "source": "shared"}]
    # Deleting it in Settings > Models takes it off the computers and out of the vault, and each computer it was on is
    # told to clear its copy: a v0.6.0-rc.1 runner logs out the Codex login the key made instead of keeping auth.json.
    call(api, "DELETE", f"model-subscriptions/{shared['id']}?confirm=true")
    assert fetch(api, fresh) == {"credentials": [], "signed_out": True, "clear_key": True}
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM credentials WHERE id=? AND ciphertext IS NOT NULL", (team,)).fetchone()


def test_a_computer_key_cannot_go_on_a_computer_given_a_models_subscription(environment):
    """Add key on a computer given a subscription in Settings > Models is refused, pointing there: a computer has one
    sign-in per runtime, and it is set in one place."""
    api = setup(environment)
    box = computer(api, "Team box", source="terminal")
    sid = call(
        api,
        "POST",
        "model-subscriptions",
        {"name": "Company OpenAI", "kind": "openai_key", "secret": "sk-proj-company-0123456789"},
    )["id"]
    call(
        api,
        "PUT",
        "computer-subscriptions",
        {"runner_id": box["runner_id"], "runtime": "codex", "subscription": sid, "confirm": True},
    )
    refused = call(
        api,
        "POST",
        f"computers/{box['runner_id']}/keys",
        {"runtime": "codex", "secret": OWN},
        expected=409,
    )
    assert "Settings > Models" in refused["error"]["detail"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM computer_keys").fetchone()[0] == 0


def test_cursor_and_meta_keys_are_subscriptions_a_computer_takes(environment):
    api = setup(environment)
    box = computer(api, "Team box", beat=False)
    kinds = {k["kind"]: k for k in call(api, "GET", "model-subscriptions")["kinds"]}
    assert (kinds["cursor_key"]["runtime"], kinds["meta_key"]["runtime"]) == ("cursor", "musecode")
    for kind, runtime, secret in (
        ("cursor_key", "cursor", "cursor-0123456789"),
        ("meta_key", "musecode", "meta-01234567"),
    ):
        sid = call(api, "POST", "model-subscriptions", {"name": kind, "kind": kind, "secret": secret})["id"]
        call(
            api,
            "PUT",
            "computer-subscriptions",
            {"runner_id": box["runner_id"], "runtime": runtime, "subscription": sid, "confirm": True},
        )
    runtimes = {
        g["runtime"]
        for g in call(api, "GET", "computer-subscriptions")["assignments"]
        if g["runner_id"] == box["runner_id"]
    }
    assert runtimes == {"cursor", "musecode"}
    # A runtime it has no kind for stays refused.
    r = api.put(
        "/api/v2/computer-subscriptions",
        json={"runner_id": box["runner_id"], "runtime": "gemini", "subscription": None},
        headers=signed_in(),
    )
    assert r.status_code == 422


def given_to(api, machine, runtime="codex"):
    with api.app.state.store.read() as c:
        found = c.execute(
            "SELECT subscription_id FROM computer_subscriptions WHERE runner_id=? AND runtime=?",
            (machine["runner_id"], runtime),
        ).fetchone()
    return found and found["subscription_id"]


def test_use_the_shared_key_brings_back_only_the_key_a_member_s_computer_was_given(environment):
    """Codex review P1-1: Use the shared key with no key named gives a member's computer back the shared key it was
    given before a Clear key, and nothing else; putting a company key on a computer that had none is a credential
    administrator's change."""
    api = setup(environment)
    mine = computer(api, "Quinn's box", operator="quinn", source="shared")
    shared_before(api)
    quinn = as_person(api, "quinn")
    laptop = computer(api, "Quinn's laptop", operator="quinn")  # enrolled after the upgrade: no key
    before = given_to(api, mine)
    # Her box's shared key, cleared there and brought back: the same key.
    out = call(api, "POST", f"computers/{mine['runner_id']}/keys/clear", {"runtime": "codex"}, who=quinn)
    assert fetch(api, mine) == {"credentials": [], "signed_out": True, "clear_key": True}
    report(api, mine, out["computers"][0]["login"]["id"], "signed_out")
    assert given_to(api, mine) == before, "a Clear key keeps the shared key given, held back"
    call(api, "POST", f"computers/{mine['runner_id']}/shared-key", {"runtime": "codex"}, who=quinn)
    assert fetch(api, mine)["credentials"] == [{"env": "OPENAI_API_KEY", "value": SHARED, "source": "shared"}]
    # Her laptop was never given one: Use the shared key lifts its sign-out and hands it no company key.
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by,clear_key) "
            "VALUES(?,'codex','',?,'human:quinn',0)",
            (laptop["runner_id"], H.now()),
        )
    call(api, "POST", f"computers/{laptop['runner_id']}/shared-key", {"runtime": "codex"}, who=quinn)
    assert given_to(api, laptop) is None and fetch(api, laptop) == {"credentials": []}
    # A credential administrator's Use the shared key there gives it the company's.
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by,clear_key) "
            "VALUES(?,'codex','',?,'human:owner',0)",
            (laptop["runner_id"], H.now()),
        )
    call(api, "POST", f"computers/{laptop['runner_id']}/shared-key", {"runtime": "codex"})
    assert given_to(api, laptop) == before
    assert fetch(api, laptop)["credentials"][0]["value"] == SHARED


def test_replacing_a_computers_own_key_given_to_other_computers_changes_it_on_that_computer_only(environment):
    """Codex review P1-2: Replace key, this computer only, on a computer whose own key is given to other computers in
    Settings > Models makes a new key for it; the others keep the one they were given, unchanged."""
    api = setup(environment)
    alpha = computer(api, "Alpha")
    bravo = computer(api, "Bravo")
    first = call(api, "POST", f"computers/{alpha['runner_id']}/keys", {"runtime": "codex", "secret": OWN})
    report(api, alpha, first["id"], "done")
    sid = given_to(api, alpha)
    call(
        api,
        "PUT",
        "computer-subscriptions",
        {"runner_id": bravo["runner_id"], "runtime": "codex", "subscription": sid, "confirm": True},
    )
    assert fetch(api, bravo)["credentials"][0]["value"] == OWN
    with api.app.state.store.read() as c:
        revision = MS.row(c, sid)["revision"]
    heartbeat(api, alpha["token"], "computer")
    new = "sk-proj-alpha-new-0123456789"
    again = call(api, "POST", f"computers/{alpha['runner_id']}/keys", {"runtime": "codex", "secret": new})
    report(api, alpha, again["id"], "done")
    assert fetch(api, alpha)["credentials"] == [{"env": "OPENAI_API_KEY", "value": new, "source": "computer"}]
    assert given_to(api, alpha) != sid, "a key of its own"
    assert fetch(api, bravo)["credentials"][0]["value"] == OWN, "Bravo keeps the key it was given"
    with api.app.state.store.read() as c:
        assert MS.row(c, sid)["revision"] == revision
    # Clearing Alpha's key now takes only Alpha's; Bravo's stays in the vault.
    call(api, "POST", f"computers/{alpha['runner_id']}/keys/clear", {"runtime": "codex"})
    assert fetch(api, bravo)["credentials"][0]["value"] == OWN


def test_a_bots_own_key_grant_signs_its_turns_in_before_the_computers_key(environment):
    """Codex review P1-4: a bot granted its own model key ran on it before the computer's key (v0.6.0-rc.1 runners set
    the computer's key only where no grant set one). A computer's key, now a subscription, is not handed to that bot's
    turns, since a runner takes the granted key out for a subscription handed; other bots still get it."""
    api = setup(environment)
    box = computer(api, "Alpha", source="shared")
    shared_before(api)
    add_bot(api, "mailer")
    add_bot(api, "writer")
    with api.app.state.store.transaction() as c:
        mailer = H.resolve_bot(c, "mailer")["id"]
        writer = H.resolve_bot(c, "writer")["id"]
    granted(api, store(api, "OPENAI_API_KEY", "sk-proj-mailer-0123456789", name="Mailer key"), "bot:" + mailer)
    # A grant of another runtime's key leaves its Codex turns on the computer's key.
    granted(api, store(api, "ANTHROPIC_API_KEY", "sk-ant-writer-0123456789", name="Writer key"), "bot:" + writer)
    config = {"runtime": "codex", "harness": "codex", "model": "gpt-5.5"}
    runner, sid = {"id": box["runner_id"]}, given_to(api, box)
    with api.app.state.store.read() as c:
        assert MS.for_claim(c, runner, config, bot=mailer) == []
        [item] = MS.for_claim(c, runner, config, bot=writer)
        assert item["runtime"] == "codex" and item["subscription"]["id"] == sid


def test_deleting_a_shared_codex_key_in_credentials_clears_it_on_each_computer(environment):
    """Codex review P2-1: a key deleted while computers run on it tells each to clear its copy (`clear_key`): a
    v0.6.0-rc.1 runner logs Codex out of the login the key made, which it otherwise keeps in auth.json."""
    api = setup(environment)
    one = computer(api, "Alpha", source="shared")
    two = computer(api, "Bravo", source="shared")
    team = shared_before(api)
    r = api.delete(f"/api/v2/credentials/{team}", headers=signed_in())
    assert r.status_code in (200, 204), r.text
    for box in (one, two):
        assert fetch(api, box) == {"credentials": [], "signed_out": True, "clear_key": True}
        assert given_to(api, box) is None


def test_replacing_the_shared_key_for_all_moves_its_subscription_to_a_new_revision(environment):
    """Codex review P2-2: Replace key for every computer a shared key serves is a new value, so its subscription moves
    to a new revision and counts again; a refusal of the old value said late leaves the new one alone."""
    api = setup(environment)
    box = computer(api, "Alpha", source="shared")
    shared_before(api)
    sid = given_to(api, box)
    with api.app.state.store.transaction() as c:
        old = MS.row(c, sid)["revision"]
        c.execute(
            "INSERT INTO attempt_subscriptions(attempt_id,runtime,subscription_id,revision) VALUES('a-old','codex',?,?)",
            (sid, old),
        )
    rotated = "sk-team-rotated-0123456789"
    call(api, "POST", f"computers/{box['runner_id']}/keys", {"runtime": "codex", "secret": rotated, "scope": "all"})
    with api.app.state.store.transaction() as c:
        sub = MS.row(c, sid)
        assert (sub["revision"], sub["status"], sub["preview"]) == (old + 1, "new", "sk-…789")
        MS.turn_rejected(c, api.app.state.store.settings, "a-old", "ops", "codex", "invalid key", "")
        assert MS.row(c, sid)["status"] == "new", "the old value's refusal says nothing of the new one"
    assert fetch(api, box)["credentials"][0]["value"] == rotated
