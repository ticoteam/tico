"""Shared connections on Settings > Computers (backend/computer_keys.py `serving`, backend/model_login.py `share_key`,
`attach_shared_key`): a key shared with some computers by name, or with every computer in Credentials, shows on each
computer it serves with the same computers listed, and "this computer only" takes one computer off it for good."""

import json

from backend.store import H
from backend.tests.test_computer_keys import SHARED, call, computer, fetch, heartbeat, listed, pending, report, setup
from backend.tests.test_computer_signin import grant, store
from backend.tests.test_onboarding import as_person, environment  # noqa: F401

PASTED = "sk-proj-shared-pasted-0123456789"


def labels(machine_or_info):
    return [c["label"] for c in machine_or_info["computers"]]


def by_name(api, cid, machine):
    """Share credential `cid` with `machine` by name, as Add connection does."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) VALUES(?,?,?,?,?)",
                  (H.new_id(), cid, "computer:" + machine["runner_id"], "human:ana", H.now()))


def test_a_key_shared_with_two_computers_shows_on_both_and_lists_both(environment):
    """Acceptance 19: Add connection > API key > paste > Shared with: [Bravo] from Alpha. Both run on it, both rows
    list both computers; a computer it was not shared with gets nothing."""
    api = setup(environment)
    one, two, three = (computer(api, label) for label in ("Alpha", "Bravo", "Charlie"))
    out = call(api, "POST", f"computers/{two['runner_id']}/keys",
               {"runtime": "codex", "secret": PASTED, "share": [one["runner_id"]]})
    assert out["shared"]["preview"] == "sk-…789" and labels(out) == ["Alpha", "Bravo"], "by name, not as listed"
    # The ask on either computer names both.
    info = call(api, "GET", f"computers/{two['runner_id']}/keys?runtime=codex")
    assert info["source"] == "shared" and labels(info) == ["Alpha", "Bravo"] and info["shared"]["every"] is False
    assert "value" not in str(out) and PASTED not in str(out)
    for machine in (one, two):
        assert fetch(api, machine)["credentials"] == [{"env": "OPENAI_API_KEY", "value": PASTED, "source": "shared"}]
        row = listed(api, machine)["shared_with"]["codex"]
        assert row["every"] is False and labels(row) == ["Alpha", "Bravo"] and row["id"] == out["shared"]["id"]
        assert listed(api, machine)["shared_keys"]["codex"] == {"env": "OPENAI_API_KEY", "preview": "sk-…789"}
    assert fetch(api, three)["credentials"] == [] and listed(api, three)["shared_with"] == {}
    # Credentials lists it with a grant per computer, named; revoking one there stops sharing it with that computer.
    vault = call(api, "GET", "credentials")
    row = next(r for r in vault["credentials"] if r["id"] == out["shared"]["id"])
    assert sorted(g["subject"] for g in row["grants"]) == sorted("computer:" + m["runner_id"] for m in (one, two))
    assert {m["id"]: m["label"] for m in vault["computers"]}[two["runner_id"]] == "Bravo"
    gid = next(g["id"] for g in row["grants"] if g["subject"] == "computer:" + two["runner_id"])
    call(api, "POST", f"credentials/{out['shared']['id']}/grants/{gid}/revoke", {})
    assert fetch(api, two)["credentials"] == [] and labels(listed(api, one)["shared_with"]["codex"]) == ["Alpha"]


def test_the_every_computer_key_is_a_shared_connection_on_every_computer(environment):
    """Acceptance 21: today's "Every computer" key in Credentials shows on every computer, a computer whose tool has
    not reported yet included; one signing in with its own terminal key does not claim it."""
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    ready = computer(api, "Alpha", source="shared")
    fresh = computer(api, "Bravo")          # reports codex installed, not signed in yet
    own = computer(api, "Charlie", source="terminal")
    account = computer(api, "Delta", source="")   # signed in, but with no key: an account login
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT readiness FROM runners WHERE id=?", (account["runner_id"],)).fetchone()
        assert '"authenticated": "missing"' in row[0] or '"authenticated":"missing"' in row[0]
        c.execute("UPDATE runners SET readiness=replace(readiness, 'missing', 'ready') WHERE id=?",
                  (account["runner_id"],))
    for machine in (ready, fresh):
        row = listed(api, machine)["shared_with"]["codex"]
        assert row["every"] is True and labels(row) == ["Alpha", "Bravo"]
    assert listed(api, own)["shared_with"] == {} and listed(api, account)["shared_with"] == {}
    # Bravo's own report still says what its tool did: the page shows the key, not a reading it has not taken.
    assert listed(api, fresh)["readiness"]["runtimes"]["codex"]["authenticated"] == "missing"


def test_a_key_shared_by_name_comes_before_the_every_computer_key(environment):
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    one, two = computer(api, "Alpha"), computer(api, "Bravo")
    call(api, "POST", f"computers/{one['runner_id']}/keys",
         {"runtime": "codex", "secret": PASTED, "share": [one["runner_id"]]})
    assert fetch(api, one)["credentials"][0]["value"] == PASTED
    assert fetch(api, two)["credentials"][0]["value"] == SHARED
    assert labels(listed(api, one)["shared_with"]["codex"]) == ["Alpha"]
    assert labels(listed(api, two)["shared_with"]["codex"]) == ["Bravo"]


def test_this_computer_only_takes_one_computer_off_a_shared_key_for_good(environment):
    """Acceptance 36: Clear key from Alpha on a key shared with Alpha and Bravo asks with both named; "this computer
    only" leaves Bravo working and Alpha off it, after a refetch too, and the "Every computer" key does not step in."""
    api = setup(environment)
    grant(api, store(api, "OPENAI_API_KEY", SHARED), "computers")
    one, two = computer(api, "Alpha"), computer(api, "Bravo")
    call(api, "POST", f"computers/{one['runner_id']}/keys",
         {"runtime": "codex", "secret": PASTED, "share": [two["runner_id"]]})
    assert labels(call(api, "GET", f"computers/{one['runner_id']}/keys?runtime=codex")) == ["Alpha", "Bravo"]
    call(api, "POST", f"computers/{one['runner_id']}/keys/clear", {"runtime": "codex", "scope": "this"})
    assert fetch(api, one)["credentials"] == []
    assert fetch(api, one)["credentials"] == [], "still off at the next fetch"
    assert fetch(api, two)["credentials"] == [{"env": "OPENAI_API_KEY", "value": PASTED, "source": "shared"}]
    assert listed(api, one)["shared_with"] == {}
    assert labels(listed(api, two)["shared_with"]["codex"]) == ["Bravo"]
    assert labels(call(api, "GET", f"computers/{two['runner_id']}/keys?runtime=codex")) == ["Bravo"]


def test_all_listed_computers_clears_every_computer_the_key_serves(environment):
    api = setup(environment)
    one, two, three = (computer(api, label, capabilities=["model_sign_out_v1", "model_keys_v1"])
                       for label in ("Alpha", "Bravo", "Charlie"))
    call(api, "POST", f"computers/{one['runner_id']}/keys",
         {"runtime": "codex", "secret": PASTED, "share": [two["runner_id"]]})
    out = call(api, "POST", f"computers/{two['runner_id']}/keys/clear", {"runtime": "codex", "scope": "all"})
    assert labels(out) == ["Alpha", "Bravo"]
    assert fetch(api, one)["credentials"] == [] and fetch(api, two)["credentials"] == []
    assert pending(api, three) == []


def test_editing_a_shared_key_from_one_computer_changes_every_listed_computer(environment):
    """Acceptance 20 (server side): Replace key > All listed computers from Bravo; Alpha and Bravo both run on the new
    value, with the same preview on both rows."""
    api = setup(environment)
    one, two = computer(api, "Alpha"), computer(api, "Bravo")
    call(api, "POST", f"computers/{one['runner_id']}/keys",
         {"runtime": "codex", "secret": PASTED, "share": [two["runner_id"]]})
    new = "sk-proj-shared-rotated-987654321"
    out = call(api, "POST", f"computers/{two['runner_id']}/keys", {"runtime": "codex", "secret": new, "scope": "all"})
    assert labels(out) == ["Alpha", "Bravo"] and out["shared"]["preview"] == "sk-…321"
    for machine in (one, two):
        assert fetch(api, machine)["credentials"][0]["value"] == new
        assert listed(api, machine)["shared_keys"]["codex"]["preview"] == "sk-…321"


def test_use_a_shared_key_attaches_an_existing_one_to_this_computer(environment):
    api = setup(environment)
    one, two, three = (computer(api, label) for label in ("Alpha", "Bravo", "Charlie"))
    made = call(api, "POST", f"computers/{one['runner_id']}/keys",
                {"runtime": "codex", "secret": PASTED, "share": [two["runner_id"]]})["shared"]
    choices = call(api, "GET", f"computers/{three['runner_id']}/keys?runtime=codex")["shared_choices"]
    assert [(c["id"], c["preview"], c["every"]) for c in choices] == [(made["id"], "sk-…789", False)]
    assert "value" not in str(choices) and PASTED not in str(choices)
    out = call(api, "POST", f"computers/{three['runner_id']}/shared-key", {"runtime": "codex", "credential": made["id"]})
    assert labels(out) == ["Alpha", "Bravo", "Charlie"]
    assert fetch(api, three)["credentials"][0]["value"] == PASTED
    for machine in (one, two, three):
        assert labels(listed(api, machine)["shared_with"]["codex"]) == ["Alpha", "Bravo", "Charlie"]
    # A key nobody shared, or one for another tool, is not on offer.
    own = store(api, "OPENAI_API_KEY", "sk-proj-not-shared-0123456789")
    call(api, "POST", f"computers/{three['runner_id']}/shared-key", {"runtime": "codex", "credential": own}, expected=422)
    call(api, "POST", f"computers/{three['runner_id']}/shared-key", {"runtime": "claude", "credential": made["id"]},
         expected=422)


def test_sharing_or_attaching_a_key_is_an_administrators_change(environment):
    """An operator who is not a credential administrator pastes a key for this computer only; sharing it, or
    attaching a shared one, widens a company secret and is refused. A member sees which computers, not the key's
    name or preview."""
    api = setup(environment)
    mine = computer(api, "Quinn's box", operator="quinn")
    other = computer(api, "Team box")
    quinn = as_person(api, "quinn")
    refused = call(api, "POST", f"computers/{mine['runner_id']}/keys",
                   {"runtime": "codex", "secret": PASTED, "share": [other["runner_id"]]}, who=quinn, expected=403)
    assert "credential administrator" in refused["error"]["detail"]
    made = call(api, "POST", f"computers/{other['runner_id']}/keys",
                {"runtime": "codex", "secret": PASTED, "share": [other["runner_id"]]})["shared"]
    call(api, "POST", f"computers/{mine['runner_id']}/shared-key", {"runtime": "codex", "credential": made["id"]},
         who=as_person(api, "quinn"), expected=403)
    info = call(api, "GET", f"computers/{mine['runner_id']}/keys?runtime=codex", who=as_person(api, "quinn"))
    assert "shared_choices" not in info
    assert fetch(api, mine)["credentials"] == []
    member = next(m for m in call(api, "GET", "operations", who=as_person(api, "quinn"))["computers"]
                  if m["id"] == other["runner_id"])
    assert member["shared_with"]["codex"] == {"every": False, "computers": [{"id": other["runner_id"],
                                                                             "label": "Team box"}]}
    # Only connected computers.
    call(api, "POST", f"computers/{other['runner_id']}/keys",
         {"runtime": "codex", "secret": PASTED, "share": ["no-such-computer"]}, expected=422)
    # Switch to API key gives the computer a key of its own: it is never also a share.
    call(api, "POST", f"computers/{other['runner_id']}/keys",
         {"runtime": "codex", "secret": PASTED, "share": [mine["runner_id"]], "switch": True}, expected=422)


def test_a_cleared_computer_given_a_shared_key_is_told_to_take_it_again(environment):
    """A computer cleared from here keeps a mark of its own and fetches no key while it has it: picking a shared key
    for it, or sharing a new one with it, sends the Use the shared key job that lifts that mark. One that is offline
    is refused by name rather than told it has a key it will not fetch."""
    api = setup(environment)
    one, two = computer(api, "Alpha", source="shared"), computer(api, "Bravo")
    made = call(api, "POST", f"computers/{two['runner_id']}/keys",
                {"runtime": "codex", "secret": PASTED, "share": [two["runner_id"]]})["shared"]
    job = call(api, "POST", f"computers/{one['runner_id']}/keys/clear", {"runtime": "codex"})["computers"][0]["login"]
    report(api, one, job["id"], "signed_out")
    call(api, "POST", f"computers/{one['runner_id']}/shared-key", {"runtime": "codex", "credential": made["id"]})
    assert [j["kind"] for j in pending(api, one)] == ["use_shared_key"]
    assert fetch(api, one)["credentials"][0]["value"] == PASTED
    # Shared from another computer while this one, cleared again, is offline: refused, naming it, and nothing changes.
    report(api, one, pending(api, one)[0]["id"], "done")
    job = call(api, "POST", f"computers/{one['runner_id']}/keys/clear", {"runtime": "codex"})["computers"][0]["login"]
    report(api, one, job["id"], "signed_out")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen_at=? WHERE id=?", (H.shift(H.now(), seconds=-3600), one["runner_id"]))
    refused = call(api, "POST", f"computers/{two['runner_id']}/keys",
                   {"runtime": "codex", "secret": "sk-proj-second-key-0123456789", "share": [one["runner_id"]]},
                   expected=409)
    assert "Alpha is offline" in refused["error"]["detail"]
    assert fetch(api, one)["credentials"] == [] and fetch(api, two)["credentials"][0]["value"] == PASTED


def test_the_shared_key_picked_for_a_computer_is_the_one_it_runs_on(environment):
    """Two shared keys of the same kind: the one picked in Add connection replaces the other on that computer,
    whichever is older, in what the dialog says, the computers listed and what the computer fetches."""
    api = setup(environment)
    one, two, three = (computer(api, label) for label in ("Alpha", "Bravo", "Charlie"))
    # The key Charlie has sorts first, so credential order alone would keep it.
    value = {store(api, "OPENAI_API_KEY", key): key for key in (SHARED, PASTED)}
    old, picked = sorted(value)
    by_name(api, old, one)
    by_name(api, old, three)
    by_name(api, picked, two)
    assert fetch(api, three)["credentials"][0]["value"] == value[old]
    out = call(api, "POST", f"computers/{three['runner_id']}/shared-key", {"runtime": "codex", "credential": picked})
    assert out["shared"]["id"] == picked and labels(out) == ["Bravo", "Charlie"]
    assert fetch(api, three)["credentials"] == [{"env": "OPENAI_API_KEY", "value": value[picked], "source": "shared"}]
    assert call(api, "GET", f"computers/{three['runner_id']}/keys?runtime=codex")["shared"]["id"] == picked
    assert labels(listed(api, three)["shared_with"]["codex"]) == ["Bravo", "Charlie"]
    assert labels(listed(api, one)["shared_with"]["codex"]) == ["Alpha"], "the old key still serves Alpha"
    # Sharing a new pasted key with a computer that has one replaces it there too.
    new = call(api, "POST", f"computers/{two['runner_id']}/keys",
               {"runtime": "codex", "secret": "sk-proj-third-key-0123456789", "share": [one["runner_id"]]})["shared"]
    assert fetch(api, one)["credentials"][0]["value"] == "sk-proj-third-key-0123456789"
    assert listed(api, one)["shared_with"]["codex"]["id"] == new["id"]


def test_a_computer_still_reporting_a_key_that_stopped_serving_it_is_described_as_it_now_is(environment):
    """Cleared here, or its grant revoked, before the computer's next heartbeat: its last report still says the
    shared key signs it in, and Add / Replace / Clear key still open, describing no shared key."""
    api = setup(environment)
    one, two = computer(api, "Alpha", source="shared"), computer(api, "Bravo", source="shared")
    cid = call(api, "POST", f"computers/{two['runner_id']}/keys",
               {"runtime": "codex", "secret": PASTED, "share": [one["runner_id"]]})["shared"]["id"]
    call(api, "POST", f"computers/{one['runner_id']}/keys/clear", {"runtime": "codex"})
    info = call(api, "GET", f"computers/{one['runner_id']}/keys?runtime=codex")
    assert info["source"] == "" and "shared" not in info and info["computers"] == [] and info["cleared"] is True
    vault = call(api, "GET", "credentials")
    gid = next(g["id"] for r in vault["credentials"] if r["id"] == cid for g in r["grants"]
               if g["subject"] == "computer:" + two["runner_id"])
    call(api, "POST", f"credentials/{cid}/grants/{gid}/revoke", {})
    info = call(api, "GET", f"computers/{two['runner_id']}/keys?runtime=codex")
    assert info["source"] == "" and "shared" not in info
    heartbeat(api, two["token"], "shared")
    assert call(api, "GET", f"computers/{two['runner_id']}/keys?runtime=codex")["source"] == ""


def test_a_computers_operator_reads_which_computers_share_its_key_but_not_the_key(environment):
    """A shared key is a company credential: its name and preview are a credential administrator's to read, in the
    Clear / Replace key ask and on the Computers page alike. The computer's operator who is not one still reads which
    computers it serves, and its own key's preview."""
    api = setup(environment)
    mine = computer(api, "Quinn's box", operator="quinn")
    other = computer(api, "Team box")
    made = call(api, "POST", f"computers/{other['runner_id']}/keys",
                {"runtime": "codex", "secret": PASTED, "share": [mine["runner_id"]]})["shared"]
    admin = call(api, "GET", f"computers/{mine['runner_id']}/keys?runtime=codex")
    assert admin["preview"] == "sk-…789" and admin["shared"]["id"] == made["id"] and admin["shared"]["name"]
    info = call(api, "GET", f"computers/{mine['runner_id']}/keys?runtime=codex", who=as_person(api, "quinn"))
    assert info["source"] == "shared" and info["preview"] == "" and labels(info) == ["Quinn's box", "Team box"]
    assert info["shared"] == {"env": "OPENAI_API_KEY", "every": False} and info["can_replace_all"] is False
    page = next(m for m in call(api, "GET", "operations", who=as_person(api, "quinn"))["computers"]
                if m["id"] == mine["runner_id"])
    assert page["may_manage"] is True
    assert page["shared_with"]["codex"] == {"every": False, "computers": [
        {"id": mine["runner_id"], "label": "Quinn's box"}, {"id": other["runner_id"], "label": "Team box"}]}
    assert page["shared_keys"]["codex"] == {"env": "OPENAI_API_KEY"} and "sk-" not in json.dumps(page)
    # Its own key, once it has one, it reads in full preview.
    job = call(api, "POST", f"computers/{mine['runner_id']}/keys", {"runtime": "codex", "secret": "sk-quinn-own-0123456789"},
               who=as_person(api, "quinn"))
    report(api, mine, job["id"], "done")
    page = next(m for m in call(api, "GET", "operations", who=as_person(api, "quinn"))["computers"]
                if m["id"] == mine["runner_id"])
    assert page["keys"]["codex"]["preview"] == "sk-…789"
