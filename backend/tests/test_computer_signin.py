"""Computer sign-in keeps shared provider keys and agent credentials out of its fetch."""

from backend.tests.helpers.getting_started import add_bot, heartbeat  # noqa: F401
from backend.tests.helpers.onboarding import environment, signed_in
from backend.tests.helpers.model_keys import shared_before
from backend.tests.helpers.computer_signin import computer_asks, grant, new_computer, store, vault


def on_shared(name):
    """A runtime the computer reported signing in with the company's shared key `name`."""
    return {"installed": True, "authenticated": "ready", "key": {"name": name, "source": "shared"}}


def test_a_computer_gets_neither_the_team_provider_key_nor_credentials_a_bot_holds(environment):
    api = environment()
    vault(api)
    add_bot(api, "mailer")
    team = store(api, "OPENAI_API_KEY", "sk-team-key-0123456789")
    private = store(api, "OPENAI_API_KEY", "sk-only-for-mailer-999999", name="Mailer's own key")
    other = store(api, "POSTHOG_API_KEY", "phx-mailer-secret-123456")
    grant(api, private, "bot:mailer", expected=422)
    grant(api, other, "bot:mailer")
    machine = new_computer(api)
    # Shared with every computer in Credentials before keys became subscriptions, and the computer said it ran on it.
    heartbeat(api, machine["runner_id"], runtimes={"codex": on_shared("OPENAI_API_KEY")})
    shared_before(api, team)
    got = computer_asks(api, machine["token"])
    assert got.status_code == 200
    assert got.json() == {"credentials": []}
    assert computer_asks(api, machine["token"], "claude").json() == {"credentials": []}
    assert computer_asks(api, machine["token"], "gemini").status_code == 422
    # Only a computer asks, and the event that records it never holds the key.
    assert (
        api.get("/api/v2/runner-model-credentials", params={"runtime": "codex"}, headers=signed_in()).status_code == 403
    )
    with api.app.state.store.read() as c:
        dump = str([tuple(r) for r in c.execute("SELECT * FROM events WHERE action='credential.sent_to_computer'")])
    assert dump == "[]"
    # Once the runner has signed in, Health has nothing to say about it.
    heartbeat(api, machine["runner_id"], runtimes={"codex": {"installed": True, "authenticated": "ready"}})
    checks = {c["id"]: c for c in api.get("/api/v2/health", headers=signed_in()).json()["checks"]}
    assert "computer_signin" not in checks


def test_model_key_grants_do_not_put_a_provider_on_a_computer(environment):
    api = environment()
    vault(api)
    grant(api, store(api, "POSTHOG_API_KEY", "phx-mailer-secret-123456"), "computers", expected=422)
    grant(api, store(api, "OPENAI_API_KEY", "sk-a-password", name="Pass", kind="password"), "computers", expected=422)
    grant(api, store(api, "OPENAI_API_KEY", "sk-x", name="Empty-ish"), "human:nobody", expected=422)
    machine = new_computer(api)
    assert computer_asks(api, machine["token"]).json() == {"credentials": []}
    cid = store(api, "ANTHROPIC_API_KEY", "sk-ant-team-0123456789", name="Claude key")
    refused = grant(api, cid, "computers", expected=422).json()
    assert "Settings > Providers" in refused["error"]["detail"]
    assert "Paid by" in refused["error"]["detail"]
    assert computer_asks(api, machine["token"], "claude").json() == {"credentials": []}
    # A binding left by the old key migration still cannot supply a computer-wide provider.
    heartbeat(api, machine["runner_id"], runtimes={"claude": on_shared("ANTHROPIC_API_KEY")})
    shared_before(api, cid)
    assert computer_asks(api, machine["token"], "claude").json() == {"credentials": []}
