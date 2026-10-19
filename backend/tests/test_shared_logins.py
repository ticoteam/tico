"""Bots that share one ChatGPT sign-in take turns on it (runner-rs/src/login_turns.rs): Settings > Computers says which
sign-ins several bots share, naming only the bots the reader may see."""
import uuid

from backend.tests.test_api import api, as_member, assign, get, post, restrict, runner  # noqa: F401


def beat(api, r, bots, detail="Signed in with ChatGPT", acme="Signed in with ChatGPT"):
    """A heartbeat where each of `bots` ({slug: profile}) runs Codex; the computer's own sign-in and the acme
    subscription read `detail` and `acme`."""
    logins = {"": {"installed": True, "authenticated": "ready", "detail": detail},
              "acme": {"installed": True, "authenticated": "ready", "detail": acme}}
    readiness = {"schema_version": 1,
                 "runtimes": {"codex": {"installed": True, "authenticated": "ready", "detail": detail, "profiles": logins}},
                 "bots": {bot: {"ready": True, "runtime": "codex", "profile": profile, "sign_in": "ready"}
                          for bot, profile in bots.items()}}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": readiness}, token=r["token"],
         key=uuid.uuid4().hex)


def shared(api, r, token="ana-test"):
    return next(m for m in get(api, "operations", token)["computers"] if m["id"] == r["runner_id"])["shared_logins"]


def test_computers_list_the_sign_ins_several_codex_bots_share_but_not_an_api_key_or_a_login_of_ones_own(api):
    r = runner(api)
    for bot in ("ops", "finance", "inbox"):
        assign(api, r, bot)
    beat(api, r, {"ops": "", "finance": "", "inbox": "acme"})
    assert shared(api, r) == [{"runtime": "codex", "profile": "", "display_name": "", "count": 2, "bots": ["finance", "ops"]}]
    # The same list on the member-facing computers page.
    mine = next(m for m in get(api, "computers")["computers"] if m["id"] == r["runner_id"])
    assert mine["shared_logins"] == shared(api, r)

    beat(api, r, {"ops": "acme", "finance": "", "inbox": "acme"})
    assert shared(api, r) == [{"runtime": "codex", "profile": "acme", "display_name": "acme", "count": 2, "bots": ["inbox", "ops"]}]
    # A reader who may not see a bot learns how many share the sign-in, not who.
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    beat(api, r, {"ops": "", "finance": "", "inbox": "acme"})
    theirs = next((m for m in get(api, "computers", "ben-test")["computers"] if m["id"] == r["runner_id"]), None)
    assert theirs["shared_logins"] == [
        {"runtime": "codex", "profile": "", "display_name": "", "count": 2, "bots": ["finance"]}]


def test_a_key_login_or_one_whose_kind_is_unknown_is_not_listed_as_shared(api):
    # Bots on a key run side by side; a login that does not say it is ChatGPT is not claimed to take turns.
    r = runner(api)
    for bot in ("ops", "finance"):
        assign(api, r, bot)
    for detail in ("Signed in with an API key", "Signed in with ChatGPT using API key", "Signed in", "Ready", ""):
        beat(api, r, {"ops": "", "finance": ""}, detail=detail)
        assert shared(api, r) == [], detail
    beat(api, r, {"ops": "", "finance": ""})
    assert shared(api, r)[0]["count"] == 2
