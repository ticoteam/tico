"""Personal API tokens: a person from a script, with the same rights, unable to mint more."""

import pytest

from backend.personal_tokens import FIELDS
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401

OWNER, ADMIN, PLAIN = "ana-test", "ben-test", "cara-test"


@pytest.fixture
def tokens(api):
    """The roster's access file names Ben a bot administrator; pin it so the test does not
    depend on registry/hub-access.yaml staying that way."""
    api.app.state.auth.bot_admins = {"ben@acme.example"}
    return api


def mint(api, token=ADMIN, label="laptop script", **fields):
    return post(api, "me/tokens", {"label": label, **fields}, token=token)


def test_the_list_never_carries_the_secret_or_its_hash(tokens):
    issued = mint(tokens)
    r = tokens.get("/api/v2/me/tokens", headers=headers(ADMIN))
    listed = r.json()["tokens"]
    assert [row["id"] for row in listed] == [issued["id"]]
    assert set(listed[0]) == set(FIELDS)
    with tokens.app.state.store.read() as c:
        stored = c.execute("SELECT token_hash FROM human_tokens WHERE id=?", (issued["id"],)).fetchone()[0]
    assert issued["token"] not in r.text and stored not in r.text and stored != issued["token"]
    # Each person sees only their own list.
    assert get(tokens, "me/tokens", token=OWNER)["tokens"] == []
    # Not through SQL either, not even for the owner (backend/sql.py denies unlisted tables).
    r = tokens.post("/api/v2/sql", json={"sql": "SELECT token_hash FROM human_tokens"}, headers=headers(OWNER))
    assert r.status_code == 422 and stored not in r.text
    r = tokens.post("/api/v2/sql", json={"sql": "SELECT token_hash FROM human_tokens"}, headers=headers(issued["token"]))
    assert r.status_code == 422 and stored not in r.text


def test_revoked_and_expired_tokens_are_refused_like_unknown_ones(tokens):
    revoked, expired = mint(tokens, label="revoked"), mint(tokens, label="expired")
    assert get(tokens, "me", token=revoked["token"])["actor"] == "human:ben"
    post(tokens, "me/tokens/" + revoked["id"] + "/revoke", {}, token=ADMIN)
    with tokens.app.state.store.transaction() as c:
        c.execute("UPDATE human_tokens SET expires_at=? WHERE id=?", (H.shift(H.now(), seconds=-1), expired["id"]))
    for token in (revoked["token"], expired["token"], "tico_pt_" + "x" * 40):
        r = tokens.get("/api/v2/me", headers=headers(token))
        assert r.status_code == 401 and r.json()["error"]["detail"] == "Invalid credential"
    listed = {row["label"]: row for row in get(tokens, "me/tokens", token=ADMIN)["tokens"]}
    assert listed["revoked"]["revoked_at"] and not listed["expired"]["revoked_at"]
    post(tokens, "me/tokens/" + revoked["id"] + "/revoke", {}, token=ADMIN, expected=409)


def test_a_token_cannot_list_mint_or_revoke_tokens(tokens):
    issued = mint(tokens)
    refused = "Manage tokens from a signed-in browser"
    r = tokens.get("/api/v2/me/tokens", headers=headers(issued["token"]))
    assert r.status_code == 403 and r.json()["error"]["detail"] == refused
    r = tokens.post("/api/v2/me/tokens", json={"label": "another"}, headers=headers(issued["token"]))
    assert r.status_code == 403 and r.json()["error"]["detail"] == refused
    r = tokens.post("/api/v2/me/tokens/" + issued["id"] + "/revoke", json={}, headers=headers(issued["token"]))
    assert r.status_code == 403 and r.json()["error"]["detail"] == refused
    assert len(get(tokens, "me/tokens", token=ADMIN)["tokens"]) == 1
    # Machines and bots have no tokens either.
    from backend.tests.test_api import runner
    r = tokens.get("/api/v2/me/tokens", headers=headers(runner(tokens)["token"]))
    assert r.status_code == 403


def test_the_owner_and_admins_see_and_revoke_everyones_tokens_and_members_only_their_own(tokens):
    bens, anas, caras = mint(tokens, ADMIN), mint(tokens, OWNER), mint(tokens, PLAIN)
    # A member sees no one else's tokens and cannot revoke them; nobody does it with a token.
    assert tokens.get("/api/v2/access/tokens", headers=headers(PLAIN)).status_code == 403
    post(tokens, "me/tokens/" + bens["id"] + "/revoke", {}, token=PLAIN, expected=404)
    assert tokens.get("/api/v2/access/tokens", headers=headers(bens["token"])).status_code == 403
    r = tokens.get("/api/v2/access/tokens", headers=headers(ADMIN))
    listed = {row["id"]: row for row in r.json()["tokens"]}
    assert set(listed) == {bens["id"], anas["id"], caras["id"]} and listed[caras["id"]]["human"] == "cara"
    with tokens.app.state.store.read() as c:
        hashes = [row[0] for row in c.execute("SELECT token_hash FROM human_tokens")]
    assert not any(secret in r.text for secret in [bens["token"], anas["token"], caras["token"], *hashes])
    # An admin revokes anyone's, the owner's included, and the audit names who did it.
    post(tokens, "me/tokens/" + anas["id"] + "/revoke", {}, token=ADMIN)
    assert tokens.get("/api/v2/me", headers=headers(anas["token"])).status_code == 401
    with tokens.app.state.store.read() as c:
        row = c.execute("SELECT actor FROM events WHERE action='token.revoke' AND target=?", (anas["id"],)).fetchone()
    assert row["actor"] == "human:ben"
    post(tokens, "me/tokens/" + caras["id"] + "/revoke", {}, token=OWNER)
    assert tokens.get("/api/v2/me", headers=headers(caras["token"])).status_code == 401


def test_a_token_cannot_make_other_credentials(tokens):
    from backend.tests.test_agents import hermes_bot
    from backend.tests.test_hermes_pairing import approve, pair
    hermes_bot(tokens)
    issued = mint(tokens, OWNER)
    refused = "an API token cannot make other credentials"
    for path, body in (("bots/scout/agent-credential", {}),
                       ("agents/pairings/approve", {"code": pair(tokens).json()["code"], "bot": "scout"})):
        r = tokens.post("/api/v2/" + path, json=body, headers=headers(issued["token"]))
        assert r.status_code == 403 and refused in r.json()["error"]["detail"], (path, r.text)
    with tokens.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM agents").fetchone()[0] == 0
    # The same person, signed in, still can.
    assert post(tokens, "bots/scout/agent-credential", {}, token=OWNER)["token"]
    assert approve(tokens, pair(tokens).json()["code"]).status_code == 200
