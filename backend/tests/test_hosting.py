"""A server Tico hosts keeps no Claude sign-in token (backend/hosting.py, Anthropic's terms): one is refused as a
Computer key or a Credential, one stored before is never handed out and is deleted at the next start, and a server
its company runs itself (TICO_HOSTED unset) keeps OAuth-token keys as before. API keys work on both."""

import json

import pytest
from fastapi.testclient import TestClient

from backend import hosting
from backend.config import Settings
from backend.tests.test_api import api  # noqa: F401
from backend.tests.test_computer_keys import call, computer, fetch, report, setup
from backend.tests.test_computer_signin import grant, store
from backend.tests.test_onboarding import environment, signed_in  # noqa: F401

TOKEN = "sk-ant-oat01-synthetic-claude-token-0123456789"
API_KEY = "sk-ant-api03-synthetic-api-key-0123456789"


def everything(api):
    """Every row of every table, as text: where a value must not be."""
    with api.app.state.store.read() as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return json.dumps({t: [tuple(r) for r in c.execute(f"SELECT * FROM [{t}]")] for t in tables}, default=str)


def hosted(api, value="own"):
    api.app.state.store.settings.hosted = value


def restart(api):
    """The server starts again on the same database (an update): its start-up runs once more."""
    api.__exit__(None, None, None)
    again = TestClient(api.app)
    again.__enter__()
    return again


def claude_box(api, label="Hosted box"):
    return computer(api, label, source="terminal", runtime="claude", name="ANTHROPIC_API_KEY")


@pytest.mark.parametrize("mode", ["own", "invited"])
def test_a_hosted_server_refuses_a_claude_token_as_a_computer_key_and_stores_nothing(environment, mode):
    """Acceptance 1: refused with the one-line reason and both ways that work; nothing stored, no job queued."""
    api = setup(environment)
    hosted(api, mode)
    rid = claude_box(api)["runner_id"]
    info = call(api, "GET", f"runners/{rid}/keys?runtime=claude")
    assert info["names"] == ["ANTHROPIC_API_KEY"] and info["refused"] == hosting.REFUSED
    for body in ({"runtime": "claude", "secret": TOKEN, "env": "CLAUDE_CODE_OAUTH_TOKEN"},
                 {"runtime": "claude", "secret": "a-token-of-another-shape-0123456789", "env": "CLAUDE_CODE_OAUTH_TOKEN"},
                 {"runtime": "claude", "secret": TOKEN},
                 {"runtime": "claude", "secret": TOKEN, "env": "ANTHROPIC_API_KEY"}):
        refused = call(api, "POST", f"runners/{rid}/keys", body, expected=422)["error"]
        assert refused["code"] == "claude_token" and refused["detail"] == hosting.REFUSED
        assert "Settings > Computers > Sign in" in refused["detail"] and "Anthropic API key" in refused["detail"]
    for name, secret in (("CLAUDE_CODE_OAUTH_TOKEN", "a-token-of-another-shape-0123456789"), ("MY_TOKEN", TOKEN)):
        r = api.post("/api/v2/credentials", json={"name": name, "env": name, "secret": secret}, headers=signed_in())
        assert r.status_code == 422 and r.json()["error"]["detail"] == hosting.REFUSED, r.text
    after = everything(api)
    assert TOKEN not in after and "another-shape" not in after
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM model_logins").fetchone()[0] == 0


def test_an_api_key_works_on_a_hosted_server_as_today(environment):
    """Acceptance 4, hosted: an Anthropic API key is added, checked and handed to the computer as before."""
    api = setup(environment)
    hosted(api)
    box = claude_box(api)
    job = call(api, "POST", f"runners/{box['runner_id']}/keys", {"runtime": "claude", "secret": API_KEY})
    assert job["key"]["env"] == "ANTHROPIC_API_KEY"
    report(api, box, job["id"], "done")
    assert fetch(api, box, runtime="claude")["credentials"] == [
        {"env": "ANTHROPIC_API_KEY", "value": API_KEY, "source": "computer"}]


def test_a_self_hosted_server_keeps_claude_token_keys_as_today(environment):
    """Acceptance 3 and 4, self-hosted: an OAuth token and an API key are both stored and handed out; nothing removed
    at a start."""
    api = setup(environment)
    box = claude_box(api, "Own box")
    info = call(api, "GET", f"runners/{box['runner_id']}/keys?runtime=claude")
    assert info["names"] == ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"] and "refused" not in info
    job = call(api, "POST", f"runners/{box['runner_id']}/keys", {"runtime": "claude", "secret": TOKEN})
    assert job["key"]["env"] == "CLAUDE_CODE_OAUTH_TOKEN"
    report(api, box, job["id"], "done")
    grant(api, store(api, "ANTHROPIC_API_KEY", API_KEY), "computers")
    api = restart(api)
    assert fetch(api, box, runtime="claude")["credentials"] == [
        {"env": "CLAUDE_CODE_OAUTH_TOKEN", "value": TOKEN, "source": "computer"}]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials").fetchone()[0] == 2


def test_claude_tokens_stored_before_are_withheld_then_deleted_at_the_next_start(environment):
    """Acceptance 2: after the update a computer is given no Claude token (it takes its copy out, as for a key no
    longer given) and the secrets are gone from storage; API keys and other credentials stay."""
    api = setup(environment)
    own_box, shared_box = claude_box(api, "Own key"), claude_box(api, "Shared key")
    job = call(api, "POST", f"runners/{own_box['runner_id']}/keys",
               {"runtime": "claude", "secret": "own-oauth-token-0123456789", "env": "CLAUDE_CODE_OAUTH_TOKEN"})
    report(api, own_box, job["id"], "done")
    shared_token = store(api, "CLAUDE_CODE_OAUTH_TOKEN", "shared-oauth-token-0123456789")
    grant(api, shared_token, "computers")
    api_key = store(api, "ANTHROPIC_API_KEY", API_KEY)
    grant(api, api_key, "computers")
    pasted = store(api, "PASTED_TOKEN", TOKEN, kind="password")      # a token under another name: found by its value
    other = store(api, "GITHUB_TOKEN", "ghp-synthetic-0123456789")
    assert {r["env"] for r in fetch(api, own_box, runtime="claude")["credentials"]} == {"CLAUDE_CODE_OAUTH_TOKEN"}
    # Hosted from here: before the purge runs, nothing hands a token out.
    hosted(api)
    assert fetch(api, own_box, runtime="claude")["credentials"] == [
        {"env": "ANTHROPIC_API_KEY", "value": API_KEY, "source": "shared"}]
    assert fetch(api, shared_box, runtime="claude")["credentials"] == [
        {"env": "ANTHROPIC_API_KEY", "value": API_KEY, "source": "shared"}]
    api = restart(api)
    with api.app.state.store.read() as c:
        left = {r["id"] for r in c.execute("SELECT id FROM credentials")}
        assert left == {api_key, other}
        assert c.execute("SELECT count(*) FROM computer_keys").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM credential_grants WHERE credential_id IN (?,?)",
                         (shared_token, pasted)).fetchone()[0] == 0
        removed = [json.loads(r[0]) for r in c.execute(
            "SELECT detail_json FROM events WHERE action='credential.claude_token_removed'")]
    assert len(removed) == 3 and all(r["reason"] == "hosted" for r in removed)
    dump = everything(api)
    assert "oauth-token-0123456789" not in dump and TOKEN not in dump
    assert fetch(api, own_box, runtime="claude")["credentials"] == [
        {"env": "ANTHROPIC_API_KEY", "value": API_KEY, "source": "shared"}]
    # A second start finds nothing more.
    with api.app.state.store.transaction() as c:
        assert hosting.purge(c, api.app.state.store.settings, api.app.state.vault) == 0


def test_a_computer_with_only_a_claude_token_is_given_no_key_on_a_hosted_server(environment):
    """With no API key to fall back on, the reply is empty: the runner takes the token out of its key file."""
    api = setup(environment)
    box = claude_box(api)
    job = call(api, "POST", f"runners/{box['runner_id']}/keys", {"runtime": "claude", "secret": TOKEN})
    hosted(api)
    assert fetch(api, box, job["id"], runtime="claude") == {"credentials": []}, "a staged token is not handed out"
    report(api, box, job["id"], "done")
    assert fetch(api, box, runtime="claude") == {"credentials": []}


def test_the_hosted_setting_is_own_invited_or_unset():
    for value, expected in (("", ""), ("own", "own"), (" Invited ", "invited")):
        assert Settings(db_path="x.db", hosted=value, local_owner_token_file=None).hosted == expected
    with pytest.raises(ValueError, match="TICO_HOSTED is own or invited"):
        Settings(db_path="x.db", hosted="1")


def test_a_bot_run_and_a_computers_migration_get_no_claude_token_on_a_hosted_server(api):
    """A Claude token granted to a bot before the server was hosted does not reach its turn or its watcher, and a
    computer's credential migration leaves one behind instead of storing it."""
    from backend.credentials import FILE_MIGRATION
    from backend.tests.test_api import assign, claim, get, post, ready, runner
    from backend.tests.test_credentials import setup as vault_setup
    vault_setup(api)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    token = post(api, "credentials", {"name": "CLAUDE_CODE_OAUTH_TOKEN", "secret": TOKEN, "env": "CLAUDE_CODE_OAUTH_TOKEN"})
    other = post(api, "credentials", {"name": "PostHog", "secret": "phx-synthetic-private-987654", "env": "POSTHOG_API_KEY"})
    for row in (token, other):
        post(api, f"credentials/{row['id']}/grants", {"subject": "bot:ops"})
    hosted(api)
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    assert [r["env"] for r in get(api, "credential-runtime", attempt["token"])["credentials"]] == ["POSTHOG_API_KEY"]
    watched = get(api, "runner-watcher-credentials?bot=ops", machine["token"])["credentials"]
    assert [r["env"] for r in watched] == ["POSTHOG_API_KEY"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key=?", (json.dumps(["ops"]), FILE_MIGRATION))
    post(api, "runner-credential-migration", {"bot": "ops", "credentials": [
        {"env": "OLD_CLAUDE", "value": TOKEN}, {"env": "OLD_SENTRY", "value": "sentry-synthetic-0123456789"}]},
        machine["token"])
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT env FROM credentials WHERE source='Computer migration'")] == ["OLD_SENTRY"]
