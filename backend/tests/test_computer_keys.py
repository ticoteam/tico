"""Clear key and Replace key from Settings > Computers (backend/computer_keys.py, backend/model_login.py): a computer's
own model key lives in the vault, reaches the computer only from the vault, and a key the company shares with every
computer asks "this computer only" or "all of them"."""

import json
import uuid

from backend.tests.test_computer_signin import computer_asks, grant, store, vault
from backend.tests.test_onboarding import as_person, environment, signed_in  # noqa: F401

CAPABLE = ["model_sign_out_v1", "model_keys_v1"]
SHARED = "sk-team-key-0123456789"


def computer(api, label, operator="morgan", source=None, capabilities=CAPABLE, beat=True, **key):
    """An enrolled computer; `source` is the key it reports signing Codex in with ("shared", "terminal", ...), or
    `runtime` with key `name`."""
    code = api.post("/api/v2/enrollments", json={"operator": operator}, headers=signed_in()).json()["code"]
    made = api.post("/api/v2/runners/enroll", json={"code": code, "label": label, "platform": "linux"}).json()
    if beat:
        heartbeat(api, made["token"], source, capabilities, **key)
    return made


def heartbeat(api, token, source=None, capabilities=CAPABLE, runtime="codex", name="OPENAI_API_KEY"):
    row = {"installed": True, "authenticated": "ready" if source else "missing"}
    if source:
        row["key"] = {"name": name, "source": source}
    r = api.post("/api/v2/runners/heartbeat", headers={"Authorization": "Bearer " + token,
                                                        "Idempotency-Key": uuid.uuid4().hex},
                 json={"version": "test", "platform": "linux", "capabilities": capabilities,
                       "readiness": {"schema_version": 1, "runtimes": {runtime: row}, "bots": {}}})
    assert r.status_code == 200, r.text


def call(api, method, path, body=None, who=None, expected=200):
    r = api.request(method, "/api/v2/" + path, json=body, headers=who or signed_in())
    assert r.status_code == expected, r.text
    return r.json()


def report(api, machine, lid, state, message=""):
    r = api.post(f"/api/v2/runner-logins/{lid}/report", json={"state": state, "message": message},
                 headers={"Authorization": "Bearer " + machine["token"], "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text


def pending(api, machine):
    r = api.get("/api/v2/runner-logins", headers={"Authorization": "Bearer " + machine["token"]})
    assert r.status_code == 200, r.text
    return r.json()["logins"]


def fetch(api, machine, login="", runtime="codex"):
    params = {"runtime": runtime, **({"login": login} if login else {})}
    r = api.get("/api/v2/runner-model-credentials", params=params,
                headers={"Authorization": "Bearer " + machine["token"]})
    assert r.status_code == 200, r.text
    return r.json()


def listed(api, machine):
    return next(m for m in call(api, "GET", "operations")["computers"] if m["id"] == machine["runner_id"])


def setup(environment):
    api = environment()
    vault(api)
    return api


def test_a_new_key_is_stored_in_the_vault_and_reaches_the_computer_only_from_there(environment):
    api = setup(environment)
    box = computer(api, "Team box", source="terminal")
    rid = box["runner_id"]
    new = "sk-proj-new-key-0123456789"
    job = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": new})
    assert job["kind"] == "use_key" and job["key"]["preview"] == "sk-…789" and job["key"]["env"] == "OPENAI_API_KEY"
    # The login relay carries no key: not the job the runner reads, not its row, not the request's receipt.
    assert pending(api, box) == [{"id": job["id"], "runtime": "codex", "profile": "", "kind": "use_key",
                                  "expires_at": job["expires_at"]}]
    with api.app.state.store.read() as c:
        everything = json.dumps([[tuple(r) for r in c.execute(f"SELECT * FROM {t}")]
                                 for t in ("model_logins", "idempotency", "events", "computer_keys")], default=str)
        stored = c.execute("SELECT ciphertext FROM credentials WHERE source=?", ("computer:" + rid,)).fetchone()
    assert new not in everything and stored and new.encode() not in bytes(stored[0])
    # Staged until the computer checks it: only the job's own fetch gets it.
    assert fetch(api, box) == {"credentials": []}
    assert fetch(api, box, job["id"]) == {"credentials": [{"env": "OPENAI_API_KEY", "value": new, "source": "computer"}]}
    assert listed(api, box)["keys"] == {}
    report(api, box, job["id"], "done")
    assert fetch(api, box) == {"credentials": [{"env": "OPENAI_API_KEY", "value": new, "source": "computer"}]}
    assert fetch(api, box, job["id"])["credentials"] == [], "a finished job fetches nothing more"
    assert listed(api, box)["keys"]["codex"]["preview"] == "sk-…789"
    # Replace it: once the computer has checked the new one, the old one is gone from the vault.
    newer = "sk-proj-newer-key-987654321"
    second = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": newer})
    assert fetch(api, box)["credentials"][0]["value"] == new, "the old key works while the new one is checked"
    report(api, box, second["id"], "done")
    assert fetch(api, box)["credentials"][0]["value"] == newer
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials WHERE source=?", ("computer:" + rid,)).fetchone()[0] == 1


def test_a_new_key_that_fails_its_check_is_deleted_and_the_old_one_stays(environment):
    api = setup(environment)
    box = computer(api, "Team box", source="terminal")
    rid = box["runner_id"]
    first = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-good-key-0123456789"})
    report(api, box, first["id"], "done")
    bad = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-bad-key-0123456789"})
    call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-third-0123456789"}, expected=409)
    report(api, box, bad["id"], "failed", "OpenAI refused the key")
    assert fetch(api, box)["credentials"][0]["value"] == "sk-proj-good-key-0123456789"
    # Cancelled or run out of time: deleted the same way.
    late = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-late-key-0123456789"})
    call(api, "POST", f"runners/{rid}/logins/{late['id']}/cancel", {})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials WHERE source=?", ("computer:" + rid,)).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM computer_keys").fetchone()[0] == 1
    # Checked, and the computer says it is putting it in place: from Tico's yes it is this computer's key, handed out
    # without the job (so a regular fetch meanwhile cannot bring the old one back), and a cancel comes too late.
    agreed = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-agreed-0123456789"})
    report(api, box, agreed["id"], "finishing")
    assert fetch(api, box)["credentials"] == [{"env": "OPENAI_API_KEY", "value": "sk-proj-agreed-0123456789",
                                               "source": "computer"}]
    late = call(api, "POST", f"runners/{rid}/logins/{agreed['id']}/cancel", {}, expected=409)
    assert "can't be cancelled now" in late["error"]["detail"]
    report(api, box, agreed["id"], "done")
    assert fetch(api, box)["credentials"][0]["value"] == "sk-proj-agreed-0123456789"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials WHERE source=?", ("computer:" + rid,)).fetchone()[0] == 1


def test_a_cleared_key_is_not_signed_back_in_by_the_shared_key(environment):
    """Acceptance: a cleared key connection is not re-signed by the shared key's re-apply; adding a key or "Use the
    shared key" is what restores it."""
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    box = computer(api, "Team box", source="shared")
    rid = box["runner_id"]
    assert fetch(api, box)["credentials"] == [{"env": "OPENAI_API_KEY", "value": SHARED, "source": "shared"}]
    out = call(api, "POST", f"runners/{rid}/keys/clear", {"runtime": "codex"})["computers"]
    assert [(c["id"], c["login"]["kind"], c["waiting"]) for c in out] == [(rid, "clear_key", False)]
    assert pending(api, box)[0]["kind"] == "clear_key"
    # Every re-apply after this, the ten-minute one included, gets nothing, and is told to clear its copy.
    for _ in range(2):
        assert fetch(api, box) == {"credentials": [], "signed_out": True, "clear_key": True}
    report(api, box, out[0]["login"]["id"], "signed_out")
    heartbeat(api, box["token"], None)
    assert fetch(api, box)["credentials"] == []
    assert listed(api, box)["signed_out"] == {"codex": [""]}
    assert listed(api, box)["key_cleared"] == ["codex"], "the page says No key, not Signed out"
    # Adding a key restores it with that key (still not the shared one) ...
    added = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-own-key-0123456789"})
    assert fetch(api, box)["credentials"] == [], "nothing until the computer has checked the new key"
    report(api, box, added["id"], "done")
    assert fetch(api, box)["credentials"][0] == {"env": "OPENAI_API_KEY", "value": "sk-proj-own-key-0123456789",
                                                 "source": "computer"}
    # ... and clearing that deletes it from the vault too.
    heartbeat(api, box["token"], "computer")
    again = call(api, "POST", f"runners/{rid}/keys/clear", {"runtime": "codex"})["computers"][0]["login"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials WHERE source=?", ("computer:" + rid,)).fetchone()[0] == 0
    assert fetch(api, box)["credentials"] == []
    report(api, box, again["id"], "signed_out")
    # "Use the shared key" is the other way back.
    call(api, "POST", f"runners/{rid}/shared-key", {"runtime": "codex"})
    assert fetch(api, box)["credentials"][0]["value"] == SHARED


def test_switch_to_subscription_keeps_the_key_it_runs_on_and_is_refused_once_the_key_is_cleared(environment):
    """#1298: a cancelled Switch to subscription leaves the key in use and the computer reading ready on it. After Clear
    key there is no login to keep, so a switch (a page drawn before the clear) is refused rather than run as a plain
    sign-in whose cancel would read "The API key is still in use" on a computer with no login; Sign in is the way."""
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    switches = CAPABLE + ["model_switch_type_v1"]
    box = computer(api, "Team box", source="shared", capabilities=switches)
    rid = box["runner_id"]
    first = call(api, "POST", f"runners/{rid}/logins", {"runtime": "codex", "to": "subscription"})
    report(api, box, first["id"], "waiting")
    call(api, "POST", f"runners/{rid}/logins/{first['id']}/cancel", {})
    assert fetch(api, box)["credentials"] == [{"env": "OPENAI_API_KEY", "value": SHARED, "source": "shared"}]
    assert (listed(api, box)["signed_out"], listed(api, box)["key_cleared"]) == ({}, [])
    cleared = call(api, "POST", f"runners/{rid}/keys/clear", {"runtime": "codex"})["computers"][0]["login"]
    report(api, box, cleared["id"], "signed_out")
    heartbeat(api, box["token"], None, switches)
    refused = call(api, "POST", f"runners/{rid}/logins", {"runtime": "codex", "to": "subscription"}, expected=409)
    assert "its key was cleared. Sign in" in refused["error"]["detail"]
    assert pending(api, box) == []
    assert listed(api, box)["key_cleared"] == ["codex"] and fetch(api, box)["credentials"] == []
    signed = call(api, "POST", f"runners/{rid}/logins", {"runtime": "codex"})
    report(api, box, signed["id"], "signed_in")
    assert listed(api, box)["signed_out"] == {}


def test_this_computer_only_or_all_of_them_for_a_key_serving_several_computers(environment):
    """Acceptance: the shared key's ask lists the computers it serves; "this computer only" leaves the others on it,
    "all of them" clears it everywhere, and an offline one clears its copy when it is next online."""
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    one = computer(api, "Alpha", source="shared")
    two = computer(api, "Bravo", source="shared")
    three = computer(api, "Charlie", source="shared")
    own = computer(api, "Delta", source="terminal")
    with api.app.state.store.transaction() as c:  # Charlie went offline
        c.execute("UPDATE runners SET last_seen_at='2026-01-01T00:00:00Z' WHERE id=?", (three["runner_id"],))
    ask = call(api, "GET", f"runners/{one['runner_id']}/keys?runtime=codex")
    assert ask["source"] == "shared" and ask["preview"] == "sk-…789" and ask["can_replace_all"] is True
    assert [(c["label"], c["online"]) for c in ask["computers"]] == [("Alpha", True), ("Bravo", True),
                                                                     ("Charlie", False)]
    assert call(api, "GET", f"runners/{own['runner_id']}/keys?runtime=codex")["computers"] == []
    # This computer only: Alpha stops using it, Bravo and Charlie keep it.
    call(api, "POST", f"runners/{one['runner_id']}/keys/clear", {"runtime": "codex", "scope": "this"})
    assert fetch(api, one)["credentials"] == []
    assert fetch(api, two)["credentials"][0]["value"] == SHARED
    assert fetch(api, three)["credentials"][0]["value"] == SHARED
    heartbeat(api, one["token"], None)
    # All of them, from Bravo: every computer still on it, online ones by a job, Charlie when it is back.
    out = call(api, "POST", f"runners/{two['runner_id']}/keys/clear", {"runtime": "codex", "scope": "all"})["computers"]
    assert [(c["label"], bool(c["login"]), c["waiting"]) for c in out] == [("Bravo", True, False),
                                                                          ("Charlie", False, True)]
    assert pending(api, two)[0]["kind"] == "clear_key" and pending(api, three) == []
    assert fetch(api, three) == {"credentials": [], "signed_out": True, "clear_key": True}
    assert "signed_out" not in fetch(api, own), "a computer on its own key is not touched"
    assert call(api, "GET", "operations")  # still lists fine
    # The shared key itself stays in Credentials for computers added later.
    fresh = computer(api, "Echo", source=None)
    assert fetch(api, fresh)["credentials"][0]["value"] == SHARED


def test_replacing_the_shared_key_for_all_of_them_is_a_credential_administrators_change(environment):
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    mine = computer(api, "Quinn's box", operator="quinn", source="shared")
    other = computer(api, "Team box", source="shared")
    quinn = as_person(api, "quinn")
    ask = call(api, "GET", f"runners/{mine['runner_id']}/keys?runtime=codex", who=quinn)
    assert ask["can_replace_all"] is False
    assert [c["may"] for c in ask["computers"]] == [True, False]
    rotated = "sk-team-rotated-0123456789"
    refused = call(api, "POST", f"runners/{mine['runner_id']}/keys", {"runtime": "codex", "secret": rotated, "scope": "all"},
                   who=as_person(api, "quinn"), expected=403)
    assert "credential administrator" in refused["error"]["detail"]
    refused = call(api, "POST", f"runners/{mine['runner_id']}/keys/clear", {"runtime": "codex", "scope": "all"},
                   who=as_person(api, "quinn"), expected=403)
    assert "Team box" in refused["error"]["detail"]
    # This computer only is theirs to do: Quinn's box gets its own key, the Team box keeps the shared one.
    job = call(api, "POST", f"runners/{mine['runner_id']}/keys", {"runtime": "codex", "secret": "sk-quinn-own-0123456789"},
               who=as_person(api, "quinn"))
    report(api, mine, job["id"], "done")
    assert fetch(api, mine)["credentials"][0]["source"] == "computer"
    assert fetch(api, other)["credentials"][0]["value"] == SHARED
    # The owner (a credential administrator) replaces it for all: every computer on it gets the new value.
    done = call(api, "POST", f"runners/{other['runner_id']}/keys", {"runtime": "codex", "secret": rotated, "scope": "all"})
    assert done["shared"]["preview"] == "sk-…789"
    assert fetch(api, other)["credentials"] == [{"env": "OPENAI_API_KEY", "value": rotated, "source": "shared"}]
    assert fetch(api, mine)["credentials"][0]["value"] == "sk-quinn-own-0123456789"
    with api.app.state.store.read() as c:
        told = c.execute("SELECT count(*) FROM changes WHERE topic='runner' AND kind='credentials' AND subject_id=?",
                         (other["runner_id"],)).fetchone()[0]
    assert told, "the computers were told to fetch the key again"


def test_rights_are_sign_ins_and_an_old_runner_is_asked_to_update(environment):
    api = setup(environment)
    box = computer(api, "Team box", source="terminal")
    rid = box["runner_id"]
    stranger = as_person(api, "quinn")
    call(api, "GET", f"runners/{rid}/keys?runtime=codex", who=stranger, expected=403)
    call(api, "POST", f"runners/{rid}/keys/clear", {"runtime": "codex"}, who=as_person(api, "quinn"), expected=403)
    call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-x-0123456789"},
         who=as_person(api, "quinn"), expected=403)
    call(api, "POST", f"runners/{rid}/keys", {"runtime": "gemini", "secret": "sk-proj-x-0123456789"}, expected=422)
    call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "two\nlines-0123456789"}, expected=422)
    old = computer(api, "Old box", source="terminal", capabilities=["model_sign_out_v1"])
    refused = call(api, "POST", f"runners/{old['runner_id']}/keys/clear", {"runtime": "codex"}, expected=409)
    assert "Update Old box" in refused["error"]["detail"]
    call(api, "POST", f"runners/{old['runner_id']}/keys", {"runtime": "codex", "secret": "sk-proj-x-0123456789"},
         expected=409)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM model_login_cleared").fetchone()[0] == 0


def test_botops_never_clears_or_replaces_a_key_and_all_of_them_waits_for_every_computer_to_update(environment):
    """Clear key stops bots and a new key can too, so BotOps does neither, even with the operator's rights; and "all of
    them" is refused while any computer on the shared key, online or not, runs a runner that cannot clear it."""
    from backend.auth import Identity
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    one = computer(api, "Alpha", source="shared")
    rid = one["runner_id"]
    api.app.state.store.settings.test_identities["morgan-via-botops"] = Identity(
        "human:morgan", "human", via="botops", confirmed=True)
    botops = signed_in("morgan-via-botops")
    refused = call(api, "POST", f"runners/{rid}/keys/clear", {"runtime": "codex"}, who=botops, expected=403)
    assert "BotOps cannot" in refused["error"]["detail"]
    call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-x-0123456789"}, who=botops,
         expected=403)
    old = computer(api, "Old box", source="shared", capabilities=["model_sign_out_v1"])
    older = computer(api, "Older box", source="shared", capabilities=[])
    stale = call(api, "POST", f"runners/{rid}/keys/clear", {"runtime": "codex", "scope": "all"}, expected=409)
    assert stale["error"]["detail"].startswith("Update Old box, Older box to clear"), "every one named, before any job"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM model_login_cleared").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM model_logins").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM credentials").fetchone()[0] == 1
    # Offline, an older runner still has nothing that clears its copy when it is back (it never reads the mark), so
    # it is not marked as if it would.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen_at='2026-01-01T00:00:00Z' WHERE id IN (?,?)",
                  (old["runner_id"], older["runner_id"]))
    stale = call(api, "POST", f"runners/{rid}/keys/clear", {"runtime": "codex", "scope": "all"}, expected=409)
    assert stale["error"]["detail"].startswith("Update Old box, Older box to clear")
    assert "Credentials" in stale["error"]["detail"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM model_login_cleared").fetchone()[0] == 0
    # Updated, an offline computer is marked to clear its copy when it is back.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capabilities=? WHERE id IN (?,?)",
                  (json.dumps(CAPABLE), old["runner_id"], older["runner_id"]))
    out = call(api, "POST", f"runners/{rid}/keys/clear", {"runtime": "codex", "scope": "all"})["computers"]
    assert [(c["label"], c["waiting"]) for c in out] == [("Alpha", False), ("Old box", True), ("Older box", True)]


def test_replacing_a_shared_key_for_all_of_them_changes_only_the_kind_chosen(environment):
    """Claude takes an OAuth token or an API key, each its own shared credential: All of them replaces the one the
    person chose, and each computer's row previews the one it runs on."""
    api = setup(environment)
    token, api_key = "oauth-team-token-0123456789", "sk-ant-team-key-0123456789"
    oauth_id = store(api, "CLAUDE_CODE_OAUTH_TOKEN", token)
    key_id = store(api, "ANTHROPIC_API_KEY", api_key)
    grant(api, oauth_id, "computers")
    grant(api, key_id, "computers")
    box = computer(api, "Team box", source="shared", runtime="claude", name="ANTHROPIC_API_KEY")
    other = computer(api, "Oauth box", source="shared", runtime="claude", name="CLAUDE_CODE_OAUTH_TOKEN")
    rid = box["runner_id"]
    info = call(api, "GET", f"runners/{rid}/keys?runtime=claude")
    assert (info["name"], info["preview"]) == ("ANTHROPIC_API_KEY", "sk-…789")
    assert listed(api, box)["shared_keys"]["claude"] == {"env": "ANTHROPIC_API_KEY", "preview": "sk-…789"}
    assert listed(api, other)["shared_keys"]["claude"] == {"env": "CLAUDE_CODE_OAUTH_TOKEN", "preview": "oau…789"}
    new = "sk-ant-new-team-key-987654321"
    out = call(api, "POST", f"runners/{rid}/keys",
               {"runtime": "claude", "secret": new, "env": "ANTHROPIC_API_KEY", "scope": "all"})
    assert out["shared"] == {"id": key_id, "env": "ANTHROPIC_API_KEY", "preview": "sk-…321"}
    values = {row["env"]: row["value"] for row in fetch(api, box, runtime="claude")["credentials"]}
    assert values == {"CLAUDE_CODE_OAUTH_TOKEN": token, "ANTHROPIC_API_KEY": new}
    assert listed(api, box)["shared_keys"]["claude"]["preview"] == "sk-…321"
    assert listed(api, other)["shared_keys"]["claude"]["preview"] == "oau…789"
    # No kind given: the one this computer runs on.
    call(api, "POST", f"runners/{other['runner_id']}/keys", {"runtime": "claude", "secret": "oauth-new-0123456789",
                                                              "scope": "all"})
    values = {row["env"]: row["value"] for row in fetch(api, box, runtime="claude")["credentials"]}
    assert values == {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-new-0123456789", "ANTHROPIC_API_KEY": new}
    # A kind the runtime does not take, or one not shared, changes nothing.
    call(api, "POST", f"runners/{rid}/keys", {"runtime": "claude", "secret": "x" * 20, "env": "OPENAI_API_KEY",
                                             "scope": "all"}, expected=422)
    call(api, "DELETE", f"credentials/{oauth_id}")
    refused = call(api, "POST", f"runners/{other['runner_id']}/keys",
                   {"runtime": "claude", "secret": "oauth-x-0123456789", "env": "CLAUDE_CODE_OAUTH_TOKEN",
                    "scope": "all"}, expected=409)
    assert "The shared key is ANTHROPIC_API_KEY" in refused["error"]["detail"]
    assert [row["value"] for row in fetch(api, box, runtime="claude")["credentials"]] == [new]


def test_a_computer_key_is_deleted_in_credentials_unless_it_is_being_checked(environment):
    """Credentials > Delete on a computer's own key: one in use goes and the computer falls back to the shared key; one
    a computer is still checking is refused, so its check never confirms a key that is gone."""
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    box = computer(api, "Team box", source="shared")
    rid = box["runner_id"]
    first = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-own-key-0123456789"})
    report(api, box, first["id"], "done")
    staged = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-next-key-0123456789"})
    refused = call(api, "DELETE", f"credentials/{staged['key']['id']}", expected=409)
    assert "Team box is still checking this key" in refused["error"]["detail"]
    assert fetch(api, box, staged["id"])["credentials"][0]["value"] == "sk-proj-next-key-0123456789"
    assert pending(api, box)[0]["id"] == staged["id"]
    call(api, "POST", f"runners/{rid}/logins/{staged['id']}/cancel", {})
    # The key in use goes: the computer gets the shared key at its next fetch, and its row no longer shows its own.
    active = call(api, "GET", f"runners/{rid}/keys?runtime=codex")
    assert active["source"] == "computer"
    with api.app.state.store.read() as c:
        own = c.execute("SELECT credential_id FROM computer_keys WHERE runner_id=? AND state='active'", (rid,)).fetchone()
    assert call(api, "DELETE", f"credentials/{own[0]}") == {"deleted": True, "id": own[0]}
    assert fetch(api, box)["credentials"] == [{"env": "OPENAI_API_KEY", "value": SHARED, "source": "shared"}]
    assert listed(api, box)["keys"] == {}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM computer_keys").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM credentials WHERE source=?", ("computer:" + rid,)).fetchone()[0] == 0
    # A staged key whose check already ended (an orphan) goes too.
    late = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-late-key-0123456789"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE model_logins SET state='failed' WHERE id=?", (late["id"],))
    call(api, "DELETE", f"credentials/{late['key']['id']}")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM computer_keys").fetchone()[0] == 0


def test_deleting_the_only_key_in_credentials_clears_it_on_the_computer(environment):
    """Review 2: with no shared key to fall back to, deleting a computer's key in Credentials is Clear key there, so
    the login the key made is logged out at the computer's next fetch, not left working on a key that is gone."""
    api = setup(environment)
    box = computer(api, "Team box", source="terminal")
    rid = box["runner_id"]
    added = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-own-key-0123456789"})
    report(api, box, added["id"], "done")
    with api.app.state.store.read() as c:
        own = c.execute("SELECT credential_id FROM computer_keys WHERE runner_id=?", (rid,)).fetchone()[0]
    call(api, "DELETE", f"credentials/{own}")
    assert fetch(api, box) == {"credentials": [], "signed_out": True, "clear_key": True}
    assert listed(api, box)["signed_out"]
    # Adding a key ends it, as after Clear key.
    again = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-again-0123456789"})
    report(api, box, again["id"], "done")
    assert fetch(api, box)["credentials"][0]["value"] == "sk-proj-again-0123456789"


def test_a_member_sees_another_persons_computer_without_its_key_previews(environment):
    """Every person now sees every working computer (#610), but only an administrator or its operator reads which key
    it signs in with: a member gets each key's kind and no preview."""
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    box = computer(api, "Team box", source="shared")
    job = call(api, "POST", f"runners/{box['runner_id']}/keys", {"runtime": "codex", "secret": "sk-proj-own-0123456789"})
    report(api, box, job["id"], "done")
    owner = listed(api, box)
    assert owner["keys"]["codex"]["preview"] == "sk-…789" and owner["shared_keys"]["codex"]["preview"] == "sk-…789"
    quinn = as_person(api, "quinn")   # neither an administrator nor its operator
    member = next(m for m in call(api, "GET", "operations", who=quinn)["computers"] if m["id"] == box["runner_id"])
    assert member["may_manage"] is False
    assert member["keys"] == {"codex": {k: v for k, v in owner["keys"]["codex"].items() if k != "preview"}}
    assert member["shared_keys"] == {"codex": {"env": "OPENAI_API_KEY"}}
    assert "sk-" not in json.dumps(member)


def test_switch_to_subscription_deletes_the_computers_own_key_and_a_key_added_later_is_in_use_again(environment):
    """Switch to subscription removes the old key, the computer's own one included; Add key afterwards is the person
    choosing a key again, so it is checked with that job and then handed out as before."""
    api = setup(environment)
    box = computer(api, "Team box", source="computer", capabilities=CAPABLE + ["model_switch_type_v1"])
    rid = box["runner_id"]
    own = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-own-key-0123456789"})
    report(api, box, own["id"], "done")
    switch = call(api, "POST", f"runners/{rid}/logins", {"runtime": "codex", "to": "subscription"})
    report(api, box, switch["id"], "finishing")
    assert fetch(api, box)["credentials"][0]["value"] == "sk-proj-own-key-0123456789", "in use until it worked"
    report(api, box, switch["id"], "signed_in")
    assert fetch(api, box) == {"credentials": [], "key_off": True}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials WHERE source=?", ("computer:" + rid,)).fetchone()[0] == 0
    assert listed(api, box)["keys"] == {}
    added = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-added-0123456789"})
    assert fetch(api, box, added["id"])["credentials"][0]["value"] == "sk-proj-added-0123456789", "its own job checks it"
    report(api, box, added["id"], "done")
    assert fetch(api, box)["credentials"] == [{"env": "OPENAI_API_KEY", "value": "sk-proj-added-0123456789",
                                               "source": "computer"}]


TO_KEY = CAPABLE + ["model_switch_type_v1", "model_switch_to_key_v1"]


def on_subscription(api, label="Team box", capabilities=TO_KEY):
    """A computer whose Codex was switched from its own key to a subscription (Computers part 2): marked key-off."""
    box = computer(api, label, source="computer", capabilities=capabilities)
    rid = box["runner_id"]
    own = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": "sk-proj-old-key-0123456789"})
    report(api, box, own["id"], "done")
    switch = call(api, "POST", f"runners/{rid}/logins", {"runtime": "codex", "to": "subscription"})
    report(api, box, switch["id"], "finishing")
    report(api, box, switch["id"], "signed_in")
    assert fetch(api, box) == {"credentials": [], "key_off": True}
    return box


def untouched(api, box):
    """Still on its subscription: the switch's mark is kept and no key of its own is in the vault."""
    rid = box["runner_id"]
    assert fetch(api, box) == {"credentials": [], "key_off": True}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials WHERE source=?", ("computer:" + rid,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM computer_keys WHERE runner_id=?", (rid,)).fetchone()[0] == 0
    assert listed(api, box)["keys"] == {}


def test_switch_to_subscription_then_to_an_api_key_and_the_next_turn_has_the_key(environment):
    """Switch to API key checks the key while the subscription stays in use; only once the computer says it is in
    place is it this computer's key and Switch to subscription's mark lifted, so the next fetch hands it out."""
    api = setup(environment)
    box = on_subscription(api)
    rid = box["runner_id"]
    new = "sk-proj-switched-0123456789"
    job = call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": new, "switch": True})
    assert job["kind"] == "to_key" and job["key"]["preview"] == "sk-…789"
    assert pending(api, box) == [{"id": job["id"], "runtime": "codex", "profile": "", "kind": "to_key",
                                  "expires_at": job["expires_at"]}], "the job carries no key"
    call(api, "POST", f"runners/{rid}/keys", {"runtime": "codex", "secret": new, "switch": True}, expected=409)
    # Only the job's own fetch gets the key; a regular one meanwhile is told a switch is running and changes nothing.
    assert fetch(api, box) == {"credentials": [], "switching": True}
    assert fetch(api, box, job["id"])["credentials"] == [{"env": "OPENAI_API_KEY", "value": new, "source": "computer"}]
    report(api, box, job["id"], "finishing")
    assert fetch(api, box) == {"credentials": [], "switching": True}, "not in use before the computer says so"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM model_login_key_off WHERE runner_id=?", (rid,)).fetchone()[0] == 1
    report(api, box, job["id"], "done", "Switched to the API key; the subscription is signed out")
    assert fetch(api, box) == {"credentials": [{"env": "OPENAI_API_KEY", "value": new, "source": "computer"}]}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM model_login_key_off WHERE runner_id=?", (rid,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM model_login_cleared WHERE runner_id=?", (rid,)).fetchone()[0] == 0
    assert listed(api, box)["keys"]["codex"]["preview"] == "sk-…789"


def test_a_switch_to_an_api_key_that_is_cancelled_fails_or_runs_out_leaves_the_subscription(environment):
    from backend.store import H
    api = setup(environment)
    box = on_subscription(api)
    rid = box["runner_id"]
    body = {"runtime": "codex", "secret": "sk-proj-switched-0123456789", "switch": True}
    cancelled = call(api, "POST", f"runners/{rid}/keys", body)
    call(api, "POST", f"runners/{rid}/logins/{cancelled['id']}/cancel", {})
    untouched(api, box)
    # Failed, after its commit point too: the computer undid what it changed, and Tico has nothing to undo.
    refused = call(api, "POST", f"runners/{rid}/keys", body)
    report(api, box, refused["id"], "failed", "The new key did not sign codex in; the subscription is still in use")
    untouched(api, box)
    late = call(api, "POST", f"runners/{rid}/keys", body)
    report(api, box, late["id"], "finishing")
    refused = call(api, "POST", f"runners/{rid}/logins/{late['id']}/cancel", {}, expected=409)
    assert "The new key is already being put in place" in refused["error"]["detail"]
    report(api, box, late["id"], "failed", "The subscription could not be signed out on codex")
    untouched(api, box)
    expired = call(api, "POST", f"runners/{rid}/keys", body)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE model_logins SET expires_at=? WHERE id=?", (H.shift(H.now(), seconds=-5), expired["id"]))
    assert call(api, "GET", f"runners/{rid}/logins/{expired['id']}")["state"] == "expired"
    untouched(api, box)


def test_switch_to_an_api_key_rights_and_an_old_or_offline_computer(environment):
    from backend.auth import Identity
    api = setup(environment)
    box = on_subscription(api)
    rid = box["runner_id"]
    body = {"runtime": "codex", "secret": "sk-proj-switched-0123456789", "switch": True}
    call(api, "POST", f"runners/{rid}/keys", body, who=as_person(api, "quinn"), expected=403)
    api.app.state.store.settings.test_identities["morgan-via-botops"] = Identity(
        "human:morgan", "human", via="botops", confirmed=True)
    call(api, "POST", f"runners/{rid}/keys", body, who=signed_in("morgan-via-botops"), expected=403)
    as_runner = {"Authorization": "Bearer " + box["token"], "Idempotency-Key": "runner-try"}
    call(api, "POST", f"runners/{rid}/keys", body, who=as_runner, expected=403)
    assert "own login" in call(api, "POST", f"runners/{rid}/keys", {**body, "scope": "all"},
                               expected=422)["error"]["detail"]
    # A runner that cannot switch to a key would use it next to the subscription: it is asked to update.
    old = on_subscription(api, "Old box", capabilities=CAPABLE + ["model_switch_type_v1"])
    refused = call(api, "POST", f"runners/{old['runner_id']}/keys", body, expected=409)
    assert "Update Old box to switch to an API key" in refused["error"]["detail"]
    untouched(api, old)
    # On a Mac Claude Code keeps its subscription in the keychain, where a logout that half worked could not be undone.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET platform='darwin' WHERE id=?", (rid,))
    mac = call(api, "POST", f"runners/{rid}/keys", {**body, "runtime": "claude", "secret": "sk-ant-switched-0123456789"},
               expected=422)
    assert "keychain" in mac["error"]["detail"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen_at='2026-01-01T00:00:00Z' WHERE id=?", (rid,))
    assert "offline" in call(api, "POST", f"runners/{rid}/keys", body, expected=409)["error"]["detail"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM model_logins WHERE kind='to_key'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM credentials WHERE source LIKE 'computer:%'").fetchone()[0] == 0


def test_a_poll_with_no_key_to_send_takes_no_write_lock_and_a_sent_key_is_audited(environment, monkeypatch):
    """Most polls send nothing (signed out, switching, subscription): they are answered from a read. A key that is
    sent is still audited, in the write transaction that sends it."""
    api = setup(environment)
    db = api.app.state.store
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    sends = computer(api, "Alpha", source="shared")
    idle = computer(api, "Bravo")
    real = db.transaction
    opened = []

    def counted():
        opened.append(1)
        return real()

    monkeypatch.setattr(db, "transaction", counted)
    assert fetch(api, idle, runtime="claude") == {"credentials": []}
    assert opened == [], "a computer with no key to send opens no write transaction"
    assert fetch(api, sends)["credentials"][0]["value"] == SHARED
    assert opened == [1], "a sent key opens exactly one write transaction"
    monkeypatch.setattr(db, "transaction", real)
    with db.read() as c:
        audited = c.execute("SELECT count(*) FROM events WHERE action='credential.sent_to_computer'").fetchone()[0]
    assert audited >= 1
