"""Settings > Google, the Workspace key on this server (backend/google_workspace.py): setup codes, the key upload and its
checks, what a computer's token may be used for, and the OAuth client Connect Google uses.

Google is never asked: `google_call` is replaced. The keys are throwaway RSA keys made in the test, the domain is
acme.example, and nothing here reads a real secret.
"""

import hashlib
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import census, devcopy, google_connect, routines
from backend import google_workspace as W
from backend.auth import Identity
from backend.credentials import SEALED
from backend.store import H, Problem
from backend.tests.test_api import api, headers  # noqa: F401

SA = "tico@acme-project.acme.example"
MAILBOX = "inbox@acme.example"
GMAIL = "https://www.googleapis.com/auth/gmail.modify"
CALENDAR = "https://www.googleapis.com/auth/calendar"
KEY = "/api/v2/google/workspace/key"
KEEP = "/api/v2/google/workspace/key/keep"


@pytest.fixture(scope="module")
def keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


@pytest.fixture
def private(keys):
    return keys[0]


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Each test starts with no wrong codes, no lockout, no cached tokens and no audit throttle."""
    monkeypatch.setattr(W, "_BAD", [])
    monkeypatch.setattr(W, "_AUDITED", {})
    monkeypatch.setitem(W._LOCKOUT, "until", 0.0)
    W.forget_tokens()


@pytest.fixture
def google(monkeypatch):
    """Google's answers: the token endpoint (`state["token"]`), the API probes (`state["probe"]`), and every call made."""
    state = {"token": (200, {"access_token": "tok-1", "expires_in": 3600}), "probe": 200}
    calls = []

    def fake(url, *, data=None, token=None):
        calls.append((url, data, token))
        if url == W.TOKEN_URI:
            return state["token"]
        return state["probe"], {}

    monkeypatch.setattr(W, "google_call", fake)
    return state, calls


@pytest.fixture
def covered(monkeypatch):
    """Sets the message bot mailboxes the roster would list: (address, bot) pairs."""
    def set_boxes(*pairs):
        monkeypatch.setattr(routines, "message_bot_mailboxes", lambda c, roster=None: [
            {"address": address, "person_id": address.split("@")[0], "bot": bot, "declared": True}
            for address, bot in pairs])
    return set_boxes


def key_json(private, key_id="key-1", **changes):
    body = {"type": "service_account", "project_id": "acme-project", "private_key_id": key_id,
            "private_key": private, "client_email": SA, "client_id": "1000001", "token_uri": W.TOKEN_URI}
    body.update(changes)
    return body


def new_code(api, domain="acme.example", token="ana-test"):
    r = api.post("/api/v2/google/workspace/setup-code", json={"domain": domain}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["code"]


def upload(api, code, key, expected=200):
    """A setup command's upload: the key goes in the body as {"key": ...}, as scripts/google-setup.sh sends it."""
    r = api.post(KEY, json={"key": key}, headers={"Authorization": "Setup " + code})
    assert r.status_code == expected, r.text
    return r.json()


def google_view(api, token="ana-test"):
    r = api.get("/api/v2/google", headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def check(api, body=None, token="ana-test", expected=200):
    r = api.post("/api/v2/google/workspace/check", json=body or {}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def rows(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM google_workspaces ORDER BY created_at, id")]


def connected(api, private, key_id="key-1", mailbox=MAILBOX):
    """A key uploaded and checked through to connected. The message bot mailboxes must be set first (`covered`)."""
    upload(api, new_code(api), key_json(private, key_id))
    answer = check(api, {"mailbox": mailbox})
    assert answer["ok"] is True, answer
    return answer


def personal(api, person, mailbox, status="connected", holder="r1", grant="g1", client=None):
    """A Connect Google grant as google_connect keeps it: a holder computer and a grant fingerprint make it live."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO google_connects(person_id,mailbox,status,holder,grant_hash,client_id,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?,?)", (person, mailbox, status, holder, grant, client, H.now(), H.now()))


# ----------------------------------------------------------------------------- the key and its sealing

def test_the_key_is_sealed_at_rest_and_never_comes_back_in_a_view(api, private):
    upload(api, new_code(api), key_json(private))
    [row] = rows(api)
    store = api.app.state.store
    assert row["key_sealed"].startswith(SEALED) and private not in row["key_sealed"]
    with store.read() as c:
        opened = json.loads(W._cipher().unseal(c, "google-workspace:" + row["id"], row["key_sealed"]))
        with pytest.raises(Exception):
            W._cipher().unseal(c, "google-workspace:someone-else", row["key_sealed"])
    assert opened["private_key"] == private.strip() and opened["client_email"] == SA   # fields are stripped
    text = json.dumps(google_view(api))
    assert private not in text and "key_sealed" not in text and "private_key" not in text


def test_each_bad_key_is_named_by_its_field_and_nothing_is_echoed(api, private):
    code = new_code(api)
    bad = [
        "SECRETMARK",
        key_json(private, type="user"),
        key_json(private, client_email="nope-SECRETMARK"),
        key_json(private, token_uri="https://evil.example/token"),
        {field: value for field, value in key_json(private).items() if field != "private_key"},
        key_json("-----BEGIN SECRETMARK-----"),
    ]
    for key in bad:
        answer = upload(api, code, key, expected=422)
        assert answer["error"]["code"] == "key"
        text = json.dumps(answer)
        assert "SECRETMARK" not in text and "evil.example" not in text and private not in text
    for raw in (b"{nope", b"[]"):
        r = api.post(KEY, content=raw, headers={"Authorization": "Setup " + code, "Content-Type": "application/json"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "key"
    assert rows(api) == []
    upload(api, code, key_json(private))                 # rejected keys used no code


def test_an_admin_session_uploads_a_key_for_a_domain_and_a_non_admin_cannot(api, private):
    r = api.post(KEY, json={"key": key_json(private), "domain": "acme.example"}, headers=headers("ana-test"))
    assert r.status_code == 200 and r.json()["keep_key_ids"] == ["key-1"]
    [row] = rows(api)
    assert row["status"] == "pending" and row["generation"] == 1 and row["domain"] == "acme.example"
    refused = api.post(KEY, json={"key": key_json(private, "key-2"), "domain": "acme.example"},
                       headers=headers("cara-test"))
    assert refused.status_code == 403 and len(rows(api)) == 1


# ----------------------------------------------------------------------------- setup codes

def test_a_setup_code_uploads_once_and_only_its_hash_is_kept(api, private):
    code = new_code(api)
    upload(api, code, key_json(private))
    again = upload(api, code, key_json(private, "key-2"), expected=401)
    assert again["error"]["code"] == "setup_code"
    store = api.app.state.store
    with store.read() as c:
        stored = [dict(r) for r in c.execute("SELECT * FROM google_setup_codes")]
    assert len(stored) == 1 and stored[0]["code_hash"] == hashlib.sha256(code.encode()).hexdigest()
    assert stored[0]["used_at"]
    assert code not in json.dumps(stored)


def test_the_command_names_this_server_and_the_code(api):
    r = api.post("/api/v2/google/workspace/setup-code", json={"domain": "acme.example"}, headers=headers("ana-test"))
    body = r.json()
    base = api.app.state.store.settings.runner_url
    assert body["command"] == f"curl -fsSL {base}/api/v2/google/setup.sh | bash -s -- {body['code']}"
    assert body["expires_at"] and len(body["code"]) >= 40


def test_a_setup_code_expires_after_thirty_minutes(api, private):
    code = new_code(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE google_setup_codes SET expires_at=?", (H.shift(H.now(), seconds=-1),))
    assert upload(api, code, key_json(private), expected=401)["error"]["code"] == "setup_code"


def test_two_racing_uploads_cannot_both_use_one_code(api):
    code = new_code(api)
    digest = W.code_hash(code)
    store = api.app.state.store
    with store.transaction() as c:
        assert W._consume_code(c, digest, H.now()) is True
    with store.transaction() as c:
        assert W._consume_code(c, digest, H.now()) is False


def test_a_code_from_someone_who_lost_admin_cannot_upload(api, private, monkeypatch):
    code = new_code(api)
    monkeypatch.setattr(api.app.state.store.settings, "credential_admins", ("ben@acme.example",))
    assert upload(api, code, key_json(private), expected=403)["error"]["code"] == "forbidden"
    assert rows(api) == []


def test_an_older_code_cannot_upload_over_a_newer_one_and_keep_says_which_is_latest(api, private):
    first = new_code(api)
    second = new_code(api)
    assert upload(api, first, key_json(private, "key-old"), expected=409)["error"]["code"] == "superseded"
    answer = upload(api, second, key_json(private, "key-new"))
    assert answer["client_id"] == "1000001" and answer["delegation_url"].startswith(W.DELEGATION_URL)
    assert answer["keep_key_ids"] == ["key-new"]
    keep = api.post(KEEP, headers={"Authorization": "Setup " + second}).json()
    assert keep == {"latest": True, "keep_key_ids": ["key-new"]}
    new_code(api)                                        # a newer command now exists
    keep = api.post(KEEP, headers={"Authorization": "Setup " + second}).json()
    assert keep["latest"] is False
    never = api.post(KEEP, headers={"Authorization": "Setup " + first})
    assert never.status_code == 403                      # never used: nothing to keep keys for
    with api.app.state.store.read() as c:
        assert c.execute("SELECT used_at FROM google_setup_codes WHERE code_hash=?",
                         (W.code_hash(first),)).fetchone()[0] is None


def test_keep_answers_only_for_an_upload_made_within_ten_minutes(api, private):
    code = new_code(api)
    upload(api, code, key_json(private))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE google_setup_codes SET used_at=?", (H.shift(H.now(), seconds=-601),))
    r = api.post(KEEP, headers={"Authorization": "Setup " + code})
    assert r.status_code == 403


def test_wrong_codes_lock_the_setup_for_a_minute(api, private):
    for i in range(W.BAD_CODES):
        upload(api, "wrong-%d" % i, key_json(private), expected=401)
    good = new_code(api)
    assert upload(api, good, key_json(private), expected=429)["error"]["code"] == "slow_down"
    W._LOCKOUT["until"] = 0.0
    upload(api, good, key_json(private))


def test_the_setup_upload_is_capped_at_sixteen_kilobytes(api, private):
    code = new_code(api)
    big = b'{"key": "' + b"x" * (W.KEY_BYTES + 10) + b'"}'
    r = api.post(KEY, content=big, headers={"Authorization": "Setup " + code, "Content-Type": "application/json"})
    assert r.status_code == 413 and rows(api) == []
    upload(api, code, key_json(private))                 # the refused upload used no code


# ----------------------------------------------------------------------------- the guard's exemptions and the script

def test_only_the_exact_setup_requests_pass_without_a_session(api, tmp_path, monkeypatch):
    script = tmp_path / "google-setup.sh"
    script.write_text('TICO_BASE="__TICO_BASE__"\nTICO_INSTALL="__TICO_INSTALL__"\n')
    monkeypatch.setattr(W, "SCRIPT", script)
    assert api.get("/api/v2/google/setup.sh").status_code == 200
    assert api.post("/api/v2/google/setup.sh").status_code == 401
    assert api.get("/api/v2/google/setup.sh.bak").status_code == 401
    assert api.post(KEY, json={}).status_code == 401
    assert api.post(KEY, json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert api.get(KEY, headers={"Authorization": "Setup abc"}).status_code == 401
    assert api.post("/api/v2/google/workspace/check", json={}, headers={"Authorization": "Setup abc"}).status_code == 401
    assert api.get("/api/v2/google").status_code == 401
    keep = api.post(KEEP, headers={"Authorization": "Setup abc"})
    assert keep.status_code == 401 and keep.json()["error"]["code"] == "setup_code"


def test_the_setup_script_gets_this_server_and_the_install_label(api, tmp_path, monkeypatch):
    script = tmp_path / "google-setup.sh"
    script.write_text('TICO_BASE="__TICO_BASE__"\nTICO_INSTALL="__TICO_INSTALL__"\n')
    monkeypatch.setattr(W, "SCRIPT", script)
    r = api.get("/api/v2/google/setup.sh")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-shellscript")
    store = api.app.state.store
    with store.transaction() as c:
        install_id = census.install_id_in(c)
    label = hashlib.sha256(install_id.encode()).hexdigest()[:12]
    assert f'TICO_BASE="{store.settings.runner_url}"' in r.text and f'TICO_INSTALL="{label}"' in r.text
    assert "__TICO_" not in r.text
    monkeypatch.setattr(W, "SCRIPT", tmp_path / "missing.sh")
    missing = api.get("/api/v2/google/setup.sh")
    assert missing.status_code == 503 and missing.json()["error"]["code"] == "not_built"


# ----------------------------------------------------------------------------- checks

def test_a_pending_key_becomes_connected_when_its_check_passes(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    upload(api, new_code(api), key_json(private))
    view = google_view(api)
    assert view["workspace"]["status"] == "pending" and view["pending"] is None
    answer = check(api, {"mailbox": MAILBOX})
    assert answer == {"ok": True, "mailbox": MAILBOX, "error_kind": None,
                      "message": "Google accepts this key for this mailbox.", "workspace": answer["workspace"]}
    assert answer["workspace"]["status"] == "connected" and answer["workspace"]["probe"] == {"mailbox": MAILBOX}
    assert answer["workspace"]["scopes"] == [CALENDAR, GMAIL]
    assert [row["status"] for row in rows(api)] == ["connected"]
    assert check(api, {"mailbox": "other@elsewhere.example"}, expected=422)["error"]["code"] == "mailbox"


def test_the_check_is_signed_for_the_mailbox_with_the_key(api, private, keys, covered, google):
    covered((MAILBOX, "inbox-cara"))
    _, calls = google
    connected(api, private)
    exchanges = [data["assertion"] for url, data, _ in calls if url == W.TOKEN_URI]
    assert len(exchanges) == 2
    claims = [jwt.decode(assertion, keys[1], algorithms=["RS256"], audience=W.TOKEN_URI) for assertion in exchanges]
    assert {c["sub"] for c in claims} == {MAILBOX} and {c["iss"] for c in claims} == {SA}
    assert {c["scope"] for c in claims} == {GMAIL, CALENDAR}


def test_a_failed_check_keeps_a_pending_key_pending_with_the_reason(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    state, _ = google
    state["token"] = (401, {"error": "unauthorized_client"})
    upload(api, new_code(api), key_json(private))
    answer = check(api, {"mailbox": MAILBOX})
    assert answer["ok"] is False and answer["error_kind"] == "unauthorized_client"
    assert answer["message"] == W.MESSAGES["unauthorized_client"]
    view = google_view(api)
    assert view["workspace"]["status"] == "pending" and view["workspace"]["last_error"] == answer["message"]


def test_a_check_that_finds_no_such_mailbox_leaves_an_active_key_connected(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    state, _ = google
    connected(api, private)
    state["probe"] = 404
    answer = check(api, {"mailbox": MAILBOX})
    assert answer["ok"] is False and answer["error_kind"] == "not_found"
    (row,) = rows(api)
    assert row["status"] == "connected" and row["mint_failing_since"] is None
    assert row["last_error"] == W.MESSAGES["not_found"] and row["probe_mailbox"] == MAILBOX


def test_a_failed_check_turns_a_connected_key_failing_and_a_passing_one_restores_it(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    state, _ = google
    connected(api, private)
    state["probe"] = 403
    answer = check(api, {"mailbox": MAILBOX})
    assert answer["ok"] is False and answer["error_kind"] == "other"
    assert google_view(api)["workspace"]["status"] == "failing"
    state["probe"] = 200
    check(api, {"mailbox": MAILBOX})
    assert google_view(api)["workspace"]["status"] == "connected"


def test_a_replacement_waits_while_the_active_key_works_then_replaces_it(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    connected(api, private, key_id="key-1")
    second = upload(api, new_code(api), key_json(private, key_id="key-2"))
    assert set(second["keep_key_ids"]) == {"key-1", "key-2"}
    view = google_view(api)
    assert view["workspace"]["status"] == "connected" and view["pending"]["status"] == "pending"
    third = upload(api, new_code(api), key_json(private, key_id="key-3"))
    assert set(third["keep_key_ids"]) == {"key-1", "key-3"}
    assert [row["key_id"] for row in rows(api) if row["status"] == "pending"] == ["key-3"]
    check(api, {"mailbox": MAILBOX})
    assert [(row["key_id"], row["status"]) for row in rows(api)] == [("key-3", "connected")]


def test_a_result_for_a_key_that_changed_meanwhile_is_dropped(api, private, covered, google, monkeypatch):
    covered((MAILBOX, "inbox-cara"))
    upload(api, new_code(api), key_json(private))
    store = api.app.state.store
    [row] = rows(api)

    def probe_then_change(store_, row_, mailbox):
        with store.transaction() as c:
            c.execute("UPDATE google_workspaces SET revision=revision+1 WHERE id=?", (row_["id"],))
        return True, None, ""

    monkeypatch.setattr(W, "_probe", probe_then_change)
    settled, ok, _, _ = W.run_check(store, row, MAILBOX, "test")
    assert settled is False and ok is True
    assert rows(api)[0]["status"] == "pending"


def test_a_pending_key_is_checked_again_every_fifteen_minutes_for_a_day(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    state, calls = google
    state["token"] = (401, {"error": "unauthorized_client"})
    upload(api, new_code(api), key_json(private))
    store = api.app.state.store
    start = H.parse_ts(rows(api)[0]["created_at"]).timestamp()

    def token_calls():
        return len([call for call in calls if call[0] == W.TOKEN_URI])

    W.tick(store)                                        # never checked: due now
    last = H.parse_ts(rows(api)[0]["checked_at"]).timestamp()
    before = token_calls()
    W.tick(store, now=last + 100)
    assert token_calls() == before                       # not yet fifteen minutes
    W.tick(store, now=last + 1000)
    assert token_calls() > before                        # due again
    before = token_calls()
    W.tick(store, now=start + W.RECHECK_WINDOW + 10000)
    assert token_calls() == before                       # past its day: waits for Check
    assert rows(api)[0]["status"] == "pending"


def test_disconnecting_the_workspace_drops_its_tokens_and_its_keys(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    connected(api, private)
    with api.app.state.store.read() as c:
        W.mint(c, MAILBOX, "gmail")
    assert W._CACHE
    r = api.delete("/api/v2/google/workspace", headers=headers("ana-test"))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert not W._CACHE and rows(api) == [] and google_view(api)["workspace"] is None


def test_only_an_admin_makes_codes_checks_and_changes_the_key_or_the_client(api):
    assert api.post("/api/v2/google/workspace/setup-code", json={"domain": "acme.example"},
                    headers=headers("cara-test")).status_code == 403
    assert api.post("/api/v2/google/workspace/check", json={}, headers=headers("cara-test")).status_code == 403
    assert api.delete("/api/v2/google/workspace", headers=headers("cara-test")).status_code == 403
    assert api.put("/api/v2/google/oauth-client", json={"client_id": "c", "client_secret": "s"},
                   headers=headers("cara-test")).status_code == 403
    assert api.delete("/api/v2/google/oauth-client", headers=headers("cara-test")).status_code == 403
    bad = api.post("/api/v2/google/workspace/setup-code", json={"domain": "not a domain"}, headers=headers("ana-test"))
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "domain"


# ----------------------------------------------------------------------------- authority and what a computer may hold

def test_authority_follows_a_live_personal_grant_then_the_key_then_legacy(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    store = api.app.state.store

    def who(box):
        with store.read() as c:
            return W.authority(c, box)

    assert who("eve@acme.example") == "legacy"
    connected(api, private)
    assert who("eve@acme.example") == "workspace" and who("x@other.example") == "legacy"
    personal(api, "cara", "cara@acme.example", holder="r1", grant="g1")
    assert who("cara@acme.example") == "personal"               # a live grant always wins
    personal(api, "dan", "dan@acme.example", status="disconnected", holder=None, grant=None)
    assert who("dan@acme.example") == "workspace"               # not live: the key covers the domain
    personal(api, "fay", "fay@acme.example", status="failed", holder="r1", grant="g2", client="old")
    assert who("fay@acme.example") == "personal"                # a failed reconnect keeps the earlier grant
    with store.transaction() as c:
        c.execute("UPDATE google_workspaces SET status='failing'")
    assert who("eve@acme.example") == "workspace"               # a failing key keeps its authority
    with store.transaction() as c:
        c.execute("UPDATE google_workspaces SET status='pending', connected_at=NULL")
    assert who("eve@acme.example") == "legacy"                  # a pending key alone does not


def test_a_cloud_computer_publishes_the_workspace_mailboxes_its_bots_run_on(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    store = api.app.state.store
    with store.transaction() as c:
        H.sync_registry(c, {"inbox-cara": {"name": "inbox-cara", "runtime": "fake", "status": "active"}},
                        {"people": []})
        for runner_id in ("cloud-1", "box-2"):
            c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES(?,?,'ana',?,?)",
                      (runner_id, runner_id, "h-" + runner_id, H.now()))
        c.execute("INSERT INTO assignments VALUES('inbox-cara','cloud-1',1,?,'human:ana')", (H.now(),))
    connected(api, private)
    personal(api, "cara", "cara@acme.example", holder="cloud-1", grant="g1")
    with store.read() as c:
        assert W.owned_mailboxes(c, "cloud-1") == {MAILBOX, "cara@acme.example"}
        assert W.owned_mailboxes(c, "box-2") == set()


def test_a_token_is_minted_per_mailbox_and_service_and_cached_until_near_expiry(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    state, calls = google
    connected(api, private)
    store = api.app.state.store

    def token_calls():
        return len([call for call in calls if call[0] == W.TOKEN_URI])

    with store.read() as c:
        gmail = W.mint(c, MAILBOX, "gmail")
        calendar = W.mint(c, MAILBOX, "calendar")
        before = token_calls()
        assert W.mint(c, MAILBOX, "gmail") == gmail
    assert token_calls() == before                               # the second gmail token came from the cache
    assert gmail["access_token"] == "tok-1" and gmail["scopes"] == [GMAIL] and gmail["expiry"].endswith("Z")
    assert calendar["scopes"] == [CALENDAR]
    with pytest.raises(Problem) as unknown:
        with store.read() as c:
            W.mint(c, MAILBOX, "drive")
    assert unknown.value.status == 422
    state["token"] = (401, {"error": "unauthorized_client"})
    with store.read() as c, pytest.raises(Problem) as refused:
        W.mint(c, "fay@acme.example", "gmail")
    assert refused.value.status == 403 and refused.value.extra["kind"] == "unauthorized_client"


def test_a_refusal_about_the_key_starts_a_clock_and_a_success_clears_it(api, private, covered, google, monkeypatch):
    covered((MAILBOX, "inbox-cara"))
    connected(api, private)
    store = api.app.state.store
    row = rows(api)[0]
    monkeypatch.setattr(W, "FAILING_SECONDS", 0)
    with store.transaction() as c:
        W.record_mint(c, row, MAILBOX, "gmail", "r1", False, "not_found")
    assert rows(api)[0]["status"] == "connected"                 # one mailbox's refusal fails no key
    with store.transaction() as c:
        W.record_mint(c, row, MAILBOX, "gmail", "r1", False, "unauthorized_client")
    assert rows(api)[0]["status"] == "failing"
    with store.transaction() as c:
        W.record_mint(c, row, MAILBOX, "gmail", "r1", True)
    assert rows(api)[0]["status"] == "connected" and rows(api)[0]["mint_failing_since"] is None
    with store.read() as c:
        audits = c.execute("SELECT COUNT(*) FROM events WHERE action=?", ("google.workspace.mint",)).fetchone()[0]
    assert audits == 1                                           # one audit row per mailbox, service and computer an hour


# ----------------------------------------------------------------------------- the OAuth client

def test_the_oauth_client_is_the_environments_or_the_admins_and_a_replacement_asks_for_reconnects(api, monkeypatch):
    store = api.app.state.store
    settings = store.settings
    assert google_view(api)["oauth"]["configured"] is False
    r = api.put("/api/v2/google/oauth-client", json={"client_id": "old-client", "client_secret": "S-ONE"},
                headers=headers("ana-test"))
    assert r.status_code == 200 and "S-ONE" not in r.text
    oauth = r.json()["oauth"]
    assert oauth["configured"] is True and oauth["source"] == "settings" and oauth["client_id"] == "old-client"
    assert oauth["redirect_uri"].endswith(google_connect.CALLBACK)
    personal(api, "cara", "cara@acme.example", client="old-client")
    personal(api, "eve", "eve@acme.example", client=None)
    api.put("/api/v2/google/oauth-client", json={"client_id": "new-client", "client_secret": "S-TWO"},
            headers=headers("ana-test"))
    with store.read() as c:
        statuses = {r["person_id"]: (r["status"], r["kind"]) for r in
                    c.execute("SELECT person_id, status, kind FROM google_connects")}
        assert W.oauth_client(c, settings) == ("new-client", "S-TWO", "settings")
    assert statuses["cara"] == ("failed", "reconnect")           # its client is not the one now set
    assert statuses["eve"] == ("connected", None)                # no client recorded: left alone
    monkeypatch.setattr(settings, "google_oauth_client_id", "env-client")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "env-secret")
    with store.read() as c:
        assert W.oauth_client(c, settings) == ("env-client", "env-secret", "env")
    refused = api.put("/api/v2/google/oauth-client", json={"client_id": "x", "client_secret": "y"},
                      headers=headers("ana-test"))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "env_client"


# ----------------------------------------------------------------------------- GET /google

def test_me_says_whether_the_key_covers_your_own_mailbox(api, private, covered, google, monkeypatch):
    covered((MAILBOX, "inbox-cara"))
    connected(api, private)
    monkeypatch.setattr(google_connect, "mailbox_for", lambda c, pid: ("Cara@acme.example", None))
    assert google_view(api, "cara-test")["me"] == {"mailbox": "cara@acme.example", "covered_by_workspace": True}


def test_me_is_null_without_a_roster_or_for_someone_not_acting_as_themselves(api, monkeypatch):
    store = api.app.state.store
    monkeypatch.setitem(store.settings.test_identities, "ana-via-test",
                        Identity("human:ana", "owner", "ana@acme.example", via="assistant"))
    def not_on_roster(c, person_id):                # auth already refuses people off the roster; this is the
        raise Problem("not_found", "You are not on this team's roster", 404)   # view's own fallback
    monkeypatch.setattr(google_connect, "mailbox_for", not_on_roster)
    no_roster = google_view(api, "cara-test")
    assert no_roster["me"] is None and no_roster["can_admin"] is False
    assert google_view(api, "ana-via-test")["me"] is None        # the Assistant acting for her is not her
    assert google_view(api, "ana-test")["can_admin"] is True


def test_a_devcopy_clears_the_workspace_tables_and_the_connection_client():
    assert {"google_workspaces", "google_setup_codes", "google_oauth_clients", "connector_reports",
            "google_connects"} <= set(devcopy.EMPTY)
