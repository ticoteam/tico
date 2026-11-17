"""One store for the model key (migrations 20261117061243 and 20261117085930, backend/model_subscriptions.py): keys
kept for computers in Settings > Computers move into model_subscriptions + computer_subscriptions, seeded here as live
installs held them, and a computer keeps signing in with what it ran on before, its own Claude or ChatGPT login
included. The Settings > Computers key store and its jobs are gone; what is left reads the subscriptions."""

import json
import uuid

from backend import model_subscriptions as MS
from backend.store import H
from backend.tests.helpers.model_keys import (
    DROP,
    MOVE,
    SHARED,
    call,
    computer,
    fetch,
    legacy,
    listed,
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


def own_key(api, machine, runtime, env, value, state="active", login="login-1"):
    """A computer's own key as Settings > Computers kept it before: a vault credential and a computer_keys row (staged
    for the key job `login`)."""
    cid = store(api, env, value, name=f"{env} for {machine['runner_id']}")
    with api.app.state.store.transaction() as c:
        legacy(c)
        c.execute("UPDATE credentials SET source=? WHERE id=?", ("computer:" + machine["runner_id"], cid))
        c.execute(
            "INSERT INTO computer_keys(credential_id,runner_id,runtime,state,login_id,created_at,created_by) "
            "VALUES(?,?,?,?,?,?,'human:ana')",
            (cid, machine["runner_id"], runtime, state, login if state == "staged" else None, H.now()),
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
    kinds = {s["id"]: s["kind"] for s in body["subscriptions"]}
    assert {kinds[sub["id"]], kinds[shared["id"]]} == {"cursor_key", "openai_key"}
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


def cleared_here(api, machine, runtime="codex", clear_key=0):
    """A person signed this computer's login out here (or its key was deleted: `clear_key`)."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by,clear_key) "
            "VALUES(?,?,'',?,'human:ana',?)",
            (machine["runner_id"], runtime, H.now(), clear_key),
        )


def test_use_the_shared_key_brings_back_only_the_key_the_computer_is_given(environment):
    """Use the shared key lifts a sign-out, here and on the computer (the job a v0.6.0-rc.1 runner needs to drop its own
    mark), and the key the computer is given in Settings > Models reaches it again. With none given it hands nothing:
    no company key is put on a computer from here, by anyone."""
    api = setup(environment)
    quinn = as_person(api, "quinn")
    mine = computer(api, "Quinn's box", operator="quinn", source="shared")
    shared_before(api)
    laptop = computer(api, "Quinn's laptop", operator="quinn")  # enrolled after the upgrade: no key
    before = given_to(api, mine)
    cleared_here(api, mine)
    assert fetch(api, mine) == {"credentials": [], "signed_out": True}
    assert given_to(api, mine) == before, "a sign-out keeps the key given, held back"
    used = call(api, "POST", f"computers/{mine['runner_id']}/shared-key", {"runtime": "codex"}, who=quinn)
    assert used["kind"] == "use_shared_key"
    assert fetch(api, mine)["credentials"] == [{"env": "OPENAI_API_KEY", "value": SHARED, "source": "shared"}]
    for who in (quinn, None):  # the operator, then an administrator
        cleared_here(api, laptop)
        job = call(api, "POST", f"computers/{laptop['runner_id']}/shared-key", {"runtime": "codex"}, who=who)
        assert given_to(api, laptop) is None and fetch(api, laptop) == {"credentials": []}
        call(api, "POST", f"computers/{laptop['runner_id']}/logins/{job['id']}/cancel", {})
    # Not signed out: nothing to lift.
    call(api, "POST", f"computers/{laptop['runner_id']}/shared-key", {"runtime": "codex"}, expected=409)


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


def login_job(api, machine, kind, state, runtime="codex"):
    """A sign-in job for `machine`, as one was left running before the upgrade."""
    lid = H.new_id()
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO model_logins(id,runner_id,runtime,state,created_at,updated_at,expires_at,requested_by,kind) "
            "VALUES(?,?,?,?,?,?,?,'human:ana',?)",
            (lid, machine["runner_id"], runtime, state, H.now(), H.now(), "2999-01-01T00:00:00Z", kind),
        )
    return lid


def test_dropping_computer_keys_keeps_each_computer_on_what_it_signs_in_with(environment):
    """Migration 20261117085930: the Settings > Computers key store goes. A key still in use moves (the move is run
    again), a computer switched to its own subscription keeps signing in with it, a key staged for a job the runner
    never took leaves the vault unless someone else holds it, and only those jobs are canceled."""
    api = setup(environment)
    alpha = computer(api, "Alpha", source="computer")
    bravo = computer(api, "Bravo", beat=False)
    delta = computer(api, "Delta", source="shared")  # switched to its own ChatGPT login: key off
    golf = computer(api, "Golf", source="shared")  # still on the team key
    with api.app.state.store.transaction() as c:
        legacy(c)
        MOVE.up(c)  # an install that ran the first key migration already
    unsent = {kind: login_job(api, golf, kind, "requested") for kind in ("use_key", "clear_key", "to_key")}
    staged = own_key(api, alpha, "claude", "ANTHROPIC_API_KEY", "sk-ant-staged-0123456789", "staged", unsent["use_key"])
    held = own_key(api, bravo, "claude", "ANTHROPIC_API_KEY", "sk-ant-held-0123456789", "staged", unsent["to_key"])
    add_bot(api, "mailer")
    with api.app.state.store.read() as c:
        mailer = H.resolve_bot(c, "mailer")["id"]
    granted(api, held, "bot:" + mailer)
    team = store(api, "OPENAI_API_KEY", SHARED, name="Team OpenAI")
    granted(api, team, "computers")
    with api.app.state.store.transaction() as c:
        MOVE.up(c)
        c.execute(
            "INSERT INTO model_login_key_off(runner_id,runtime,profile,set_at,set_by) VALUES(?,'codex','',?,'human:ana')",
            (delta["runner_id"], H.now()),
        )
    alpha_key = own_key(api, alpha, "codex", "OPENAI_API_KEY", OWN)  # kept after the move ran: moved now
    assert {k for k, (_, cid) in given(api).items() if cid == team} == {
        (r["runner_id"], "codex") for r in (delta, golf)
    }
    sign_in = login_job(api, bravo, "sign_in", "requested")
    with api.app.state.store.transaction() as c:
        DROP.up(c)
    assert given(api) == {
        (alpha["runner_id"], "codex"): ("OpenAI API · Alpha", alpha_key),
        (golf["runner_id"], "codex"): ("Team OpenAI", team),
    }
    assert fetch(api, alpha)["credentials"] == [{"env": "OPENAI_API_KEY", "value": OWN, "source": "computer"}]
    assert fetch(api, delta) == {"credentials": []}, "its own login, as before"
    with api.app.state.store.read() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
        assert not tables & {"computer_keys", "computer_keys_by_runner", "model_login_key_off"}
        assert not c.execute("SELECT 1 FROM credentials WHERE id=?", (staged,)).fetchone()
        assert not c.execute("SELECT 1 FROM credential_grants WHERE credential_id=?", (staged,)).fetchone()
        assert c.execute("SELECT 1 FROM credentials WHERE id=? AND ciphertext IS NOT NULL", (held,)).fetchone()
        states = {r["id"]: r["state"] for r in c.execute("SELECT id,state FROM model_logins")}
    assert [states[lid] for lid in unsent.values()] == ["canceled"] * 3
    assert states[sign_in] == "requested", "not a key job"

    def state():
        with api.app.state.store.read() as c:
            return [
                sorted(tuple(r) for r in c.execute(f"SELECT * FROM {t}"))
                for t in ("model_subscriptions", "computer_subscriptions", "credential_grants", "model_logins")
            ]

    after = state()
    with api.app.state.store.transaction() as c:
        DROP.up(c)
    assert state() == after, "running it again changes nothing"


def test_a_switch_to_an_api_key_the_runner_committed_keeps_its_key(environment):
    """A Switch to API key (`to_key`) past its commit point when the server upgrades: the runner may have put the staged
    key in place and signed the subscription out, with its `done` report still to come. The key stays in the vault and
    is given to the computer, so the key fetch keeps handing it out, and the report is taken when it arrives."""
    api = setup(environment)
    box = computer(api, "Delta", source="shared")
    with api.app.state.store.transaction() as c:
        legacy(c)
        c.execute(
            "INSERT INTO model_login_key_off(runner_id,runtime,profile,set_at,set_by) VALUES(?,'codex','',?,'human:ana')",
            (box["runner_id"], H.now()),
        )
    lid = login_job(api, box, "to_key", "finishing")
    key = own_key(api, box, "codex", "OPENAI_API_KEY", OWN, "staged", lid)
    with api.app.state.store.transaction() as c:
        DROP.up(c)
    assert given(api) == {(box["runner_id"], "codex"): ("OpenAI API · Delta", key)}
    assert fetch(api, box)["credentials"] == [{"env": "OPENAI_API_KEY", "value": OWN, "source": "computer"}]
    assert report(api, box, lid, "done")["state"] == "done"
    assert fetch(api, box)["credentials"] == [{"env": "OPENAI_API_KEY", "value": OWN, "source": "computer"}]


def test_a_switch_to_a_subscription_with_a_lost_commit_keeps_the_key_it_runs_on(environment):
    """A Switch to subscription (`to_subscription`) already `finishing` when the server upgrades: the commit's answer may
    have been lost, or the install failed, and the runner kept or put back its key. The key stays given and handed
    out, the job keeps running, and its verdict, either way, is taken without taking the key away."""
    api = setup(environment)
    boxes = [computer(api, name, source="shared") for name in ("Echo", "Foxtrot")]
    with api.app.state.store.transaction() as c:
        legacy(c)
    team = store(api, "OPENAI_API_KEY", SHARED, name="Team OpenAI")
    granted(api, team, "computers")
    jobs = [login_job(api, box, "to_subscription", "finishing") for box in boxes]
    with api.app.state.store.transaction() as c:
        DROP.up(c)
    assert given(api) == {(box["runner_id"], "codex"): ("Team OpenAI", team) for box in boxes}
    with api.app.state.store.read() as c:
        assert [c.execute("SELECT state FROM model_logins WHERE id=?", (lid,)).fetchone()[0] for lid in jobs] == [
            "finishing"
        ] * 2
    for box, lid, verdict in zip(boxes, jobs, ("failed", "signed_in")):
        assert report(api, box, lid, verdict)["state"] == verdict
        assert fetch(api, box)["credentials"] == [{"env": "OPENAI_API_KEY", "value": SHARED, "source": "shared"}]
    assert given(api) == {(box["runner_id"], "codex"): ("Team OpenAI", team) for box in boxes}


def test_a_key_job_the_runner_never_took_is_canceled_and_its_key_deleted(environment):
    """A key job still `requested` (no runner has taken it) is canceled, and the key staged for it, which nothing else
    holds, leaves the vault. The key the computer runs on stays given."""
    api = setup(environment)
    box = computer(api, "Alpha", source="computer")
    key = own_key(api, box, "codex", "OPENAI_API_KEY", OWN)
    jobs = {kind: login_job(api, box, kind, "requested") for kind in ("to_key", "use_key")}
    staged = {
        kind: own_key(api, box, "codex", "OPENAI_API_KEY", f"sk-proj-{kind}-0123456789", "staged", lid)
        for kind, lid in jobs.items()
    }
    with api.app.state.store.transaction() as c:
        DROP.up(c)
    assert given(api) == {(box["runner_id"], "codex"): ("OpenAI API · Alpha", key)}
    assert fetch(api, box)["credentials"] == [{"env": "OPENAI_API_KEY", "value": OWN, "source": "computer"}]
    with api.app.state.store.read() as c:
        for cid in staged.values():
            assert not c.execute("SELECT 1 FROM credentials WHERE id=?", (cid,)).fetchone()
        assert {r["state"] for r in c.execute("SELECT state FROM model_logins")} == {"canceled"}


def test_the_computer_key_routes_say_where_keys_are_changed_now(environment):
    """Add, Replace and Clear key, and attaching a shared key, are gone from Settings > Computers (no v0.6.0-rc.1 runner
    or CLI calls them): a page drawn before is told once where keys are changed now, and a pasted key is not kept."""
    api = setup(environment)
    box = computer(api, "Alpha", source="computer")
    team = store(api, "OPENAI_API_KEY", SHARED, name="Team OpenAI")
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM credentials").fetchone()[0]
    rid = box["runner_id"]
    for method, path, body in (
        ("GET", "keys", None),
        ("POST", "keys", {"runtime": "codex", "value": "sk-proj-pasted-0123456789"}),
        ("POST", "keys/clear", {"runtime": "codex"}),
        ("POST", "shared-key", {"runtime": "codex", "credential": team}),
    ):
        refused = call(api, method, f"computers/{rid}/{path}", body, expected=410)
        assert refused["error"]["code"] == "moved" and "Settings > Models" in refused["error"]["detail"], path
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials").fetchone()[0] == before
    assert given_to(api, box) is None


def test_a_computers_operator_reads_which_computers_share_its_key_but_not_the_key(environment):
    """Settings > Computers shows the key each computer is given in Settings > Models: its own key with a preview for
    whoever may change the computer, and the shared one with every live computer it signs in (one read for the page).
    Someone who is not a credential administrator sees which computers share it, not its name or preview."""
    api = setup(environment)
    quinn = as_person(api, "quinn")
    box = computer(api, "Quinn's box", operator="quinn", source="computer")
    laptop = computer(
        api, "Quinn's laptop", operator="quinn", source="shared", runtime="claude", name="ANTHROPIC_API_KEY"
    )
    one = computer(api, "alpha", source="shared", runtime="claude", name="ANTHROPIC_API_KEY")
    two = computer(api, "Bravo", source="shared", runtime="claude", name="ANTHROPIC_API_KEY")
    gone = computer(api, "Charlie", source="shared", runtime="claude", name="ANTHROPIC_API_KEY")
    own_key(api, box, "codex", "OPENAI_API_KEY", OWN)
    team = shared_before(api, store(api, "ANTHROPIC_API_KEY", TEAM, name="Team Claude key"))
    cleared_here(api, gone, runtime="claude")
    sharing = [{"id": m["runner_id"], "label": label} for m, label in ((one, "alpha"), (two, "Bravo"))]
    sharing.append({"id": laptop["runner_id"], "label": "Quinn's laptop"})
    seen = listed(api, two)
    assert seen["shared_with"] == {"claude": {"id": team, "name": "Team Claude key", "computers": sharing}}
    assert seen["shared_keys"]["claude"]["env"] == "ANTHROPIC_API_KEY" and seen["shared_keys"]["claude"]["preview"]
    assert seen["keys"] == {}
    # Signed out here: it still shows the key it is given, but it signs nothing in.
    out = listed(api, gone)
    assert out["shared_with"] == {} and out["shared_keys"]["claude"]["env"] == "ANTHROPIC_API_KEY"
    mine = listed(api, box)
    assert mine["keys"]["codex"]["env"] == "OPENAI_API_KEY" and mine["keys"]["codex"]["preview"]
    # Quinn's view.
    page = call(api, "GET", "operations", who=quinn)
    hers = next(m for m in page["computers"] if m["id"] == laptop["runner_id"])
    assert hers["may_manage"] is True
    assert hers["shared_with"] == {"claude": {"computers": sharing}}
    assert hers["shared_keys"] == {"claude": {"env": "ANTHROPIC_API_KEY"}}
    assert next(m for m in page["computers"] if m["id"] == box["runner_id"])["keys"]["codex"]["preview"]
    assert OWN not in json.dumps(page) and TEAM not in json.dumps(page)
