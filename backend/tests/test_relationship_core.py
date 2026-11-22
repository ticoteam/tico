"""Relationship core: an outside party is one Contacts entity with one memory page, a status written together under a
compare-and-set version, an owner, and its tasks. What's next across someone's accounts is one list read that shows
only what the reader may see, and a merge moves the memory link and tasks so that unmerge puts them back exactly."""

import pytest

from backend import mailbox_access as MA
from backend.entities import store as ES
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.mcp import call
from clients import hubcli, remotecli
from backend.tests.test_directory_tools import Forward

ANA = "human:ana"


@pytest.fixture
def cli(api, monkeypatch):
    """`tico ...` argv in, as ana: the command's request goes to the app and its answer comes back."""
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: Forward(api, "ana-test"))
    return lambda *argv: remotecli.run(hubcli.parser().parse_args(list(argv)))


def org(api, domain, name):
    with api.app.state.store.transaction() as c:
        return ES.upsert(c, ANA, "organization", match={"domain": domain}, name=name)["id"]


def page(api, name="memory.html"):
    return post(api, "pages", {"name": name, "text": "<p>What we know</p>"})["page"]["file_id"]


def test_a_stale_status_write_is_refused_and_changes_nothing(api, cli):
    acme = org(api, "acme.example", "Acme")
    assert cli("contacts", "get", acme)["relationship"]["status_version"] == 0
    written = cli(
        "contacts", "status", acme, "0", "--status", "active", "--next-step", "Send the renewal", "--due", "2026-12-01",
        "--flag", "vip", "--flag", "billing issue",
    )  # fmt: skip
    assert written["status_version"] == 1 and written["flags"] == ["vip", "billing issue"]

    stale = api.post(
        "/api/v2/entities/status", json={"ref": acme, "version": 0, "status": "churned"}, headers=headers()
    )
    assert stale.status_code == 409 and stale.json()["error"]["status_version"] == 1
    shown = cli("contacts", "get", acme)["relationship"]
    assert (shown["status"], shown["next_step_due"], shown["status_version"]) == ("active", "2026-12-01", 1)
    by_domain = {"ref": "acme.example", "version": 1, "status": "x"}
    assert post(api, "entities/status", by_domain, expected=422)["error"]["code"] == "validation"  # by its id only

    isError, cleared = call(api, "tico_contacts_status", {"ref": acme, "version": 1, "flags": ["vip"]})
    assert isError is False and cleared["status"] is None and cleared["next_step_due"] is None
    assert cleared["flags"] == ["vip"] and cleared["status_version"] == 2


def test_a_write_naming_a_merged_away_id_is_refused_not_carried_to_the_kept_entity(api, cli):
    acme, dup = org(api, "acme.example", "Acme"), org(api, "acme-old.example", "Acme Old")
    cli("contacts", "status", dup, "0", "--status", "lead")  # dup at version 1, acme at 0
    cli("contacts", "merge", acme, dup)
    for path, body in (
        ("entities/status", {"ref": dup, "version": 0, "status": "x"}),
        ("entities/owner", {"ref": dup, "owner": "me"}),
    ):
        refused = post(api, path, body, expected=409)["error"]
        assert refused["code"] == "merged" and refused["merged_into"] == acme
    # A merge moves no status: each keeps its own, so unmerge is exact.
    assert cli("contacts", "get", acme)["relationship"]["status"] is None
    cli("contacts", "unmerge", dup)
    assert cli("contacts", "get", dup)["relationship"] == {
        "status": "lead",
        "next_step": None,
        "next_step_due": None,
        "flags": [],
        "status_version": 1,
    }


def test_an_entity_has_one_memory_page_found_by_the_page_list(api, cli):
    acme = org(api, "acme.example", "Acme")
    first, second = page(api), page(api, "other.html")
    post(api, f"pages/{first}/links", {"target": "entity:acme.example", "role": "memory"})  # a reference resolves
    post(api, f"pages/{first}/links", {"target": f"entity:{acme}", "role": "memory"})  # again: no change
    refused = post(api, f"pages/{second}/links", {"target": f"entity:{acme}", "role": "memory"}, expected=409)
    assert refused["error"]["code"] == "conflict"
    assert post(api, f"pages/{second}/links", {"target": "human:ana", "role": "memory"}, expected=422)

    found = get(api, f"pages?linked=entity:{acme}&role=memory")["pages"]
    assert [p["file_id"] for p in found] == [first]
    assert [p["file_id"] for p in get(api, "pages?linked=entity:acme.example&role=memory")["pages"]] == [first]

    shown = get(api, f"pages/{first}")["page"]["links"]
    assert {"target": f"entity:{acme}", "role": "memory", "name": "Acme"}.items() <= next(
        link for link in shown if link["role"] == "memory"
    ).items()
    # Unlink resolves as link does; a task may still have documents named Memory, two of them.
    post(api, f"pages/{second}/links", {"target": f"entity:{acme}", "role": "related"})
    api.delete(
        f"/api/v2/pages/{first}/links", params={"target": "entity:acme.example", "role": "memory"}, headers=headers()
    )
    assert get(api, f"pages?linked=entity:{acme}&role=memory")["pages"] == []
    tid = post(api, "tasks", {"owner": "ops", "title": "Keep notes"})["id"]
    for fid in (first, second):
        post(api, f"pages/{fid}/links", {"target": f"task:{tid}", "role": "Memory"})
    assert sum(link["role"] == "Memory" for link in get(api, f"pages/{first}")["page"]["links"]) == 1


def test_whats_next_is_one_list_of_my_accounts_by_due_date_that_hides_what_the_reader_cannot_see(api, cli):
    acme, beta, gamma = (
        org(api, "acme.example", "Acme"),
        org(api, "beta.example", "Beta"),
        org(api, "g.example", "Gamma"),
    )
    with api.app.state.store.transaction() as c:
        box = MA.ensure_mailbox(c, "ana@acme.example", person_id="ana", created_by=ANA)
        ES.add_source(c, ANA, beta, "mailbox", box["id"], visibility="restricted")  # only ana sees Beta
    for ref, due in ((acme, "2026-12-09"), (beta, "2026-12-01")):
        cli("contacts", "owner", ref, "me")
        cli("contacts", "status", ref, "0", "--next-step", "Call them", "--due", due)
    cli("contacts", "owner", gamma, "me")  # no next step: last

    mine = cli("contacts", "find", "organization", "--owner", "me", "--sort", "next")["rows"]
    assert [(r["id"], r["relationship"]["next_step_due"]) for r in mine] == [
        (beta, "2026-12-01"),
        (acme, "2026-12-09"),
        (gamma, None),
    ]
    assert mine[0]["relationship"]["next_step"] == "Call them"
    paged = get(api, "entities/find/organization?owner=human:ana&sort=next&limit=1")
    assert [
        r["id"] for r in get(api, f"entities/find/organization?owner=human:ana&sort=next&after={paged['next']}")["rows"]
    ] == [acme, gamma]

    assert (
        post(api, "entities/owner", {"ref": acme, "owner": "bot:nobody"}, expected=404)["error"]["code"] == "not_found"
    )
    bens = get(api, "entities/find/organization?owner=human:ana&sort=next", token="ben-test")["rows"]
    assert [r["id"] for r in bens] == [acme, gamma]
    assert post(api, "entities/owner", {"ref": beta, "owner": ""}, token="ben-test", expected=422)


def test_a_task_names_its_entity_and_merge_then_unmerge_puts_memory_and_tasks_back(api, cli):
    acme, dup = org(api, "acme.example", "Acme"), org(api, "acme-old.example", "Acme Old")
    kept_memory, dup_memory, dup_only = page(api), page(api, "dup.html"), page(api, "dup-only.html")
    post(api, f"pages/{kept_memory}/links", {"target": f"entity:{acme}", "role": "memory"})
    post(api, f"pages/{dup_memory}/links", {"target": f"entity:{dup}", "role": "memory"})
    post(api, f"pages/{dup_only}/links", {"target": f"entity:{dup}", "role": "related"})

    task = post(api, "tasks", {"owner": "ops", "title": "Send the renewal", "entity": "acme-old.example"})
    assert task["entity_id"] == dup
    other = post(api, "tasks", {"owner": "ops", "title": "Check the invoice"})
    post(api, f"tasks/{other['id']}", {"entity": acme})

    def tasks_for(ref):
        return sorted(t["id"] for t in get(api, f"tasks?status=all&entity={ref}")["tasks"])

    def links():
        with api.app.state.store.read() as c:
            return sorted(
                tuple(r) for r in c.execute("SELECT page_id, target, role FROM page_links WHERE target LIKE 'entity:%'")
            )

    before = links()
    assert tasks_for(dup) == [task["id"]] and tasks_for(acme) == [other["id"]]

    cli("contacts", "merge", acme, dup)
    # The kept entity has a memory page, so the dropped one's stays with it; its related page moves.
    assert (dup_memory, f"entity:{dup}", "memory") in links()
    shown = get(api, f"pages/{dup_memory}")["page"]["links"]
    assert all(not link["target"].startswith("entity:") for link in shown)  # kept on the merged-away entity: hidden
    assert [p["file_id"] for p in get(api, f"pages?linked=entity:{dup}&role=memory")["pages"]] == [kept_memory]
    assert (dup_only, f"entity:{acme}", "related") in links()
    assert tasks_for(acme) == sorted([task["id"], other["id"]])

    cli("contacts", "unmerge", dup)
    assert links() == before
    assert tasks_for(dup) == [task["id"]] and tasks_for(acme) == [other["id"]]

    post(api, f"tasks/{task['id']}", {"entity": ""})
    assert tasks_for(dup) == []
    isError, listed = call(api, "tico_task_list", {"entity": acme, "status": ["open"]})
    assert isError is False and [t["id"] for t in listed["result"]] == [other["id"]]


def test_a_merge_moves_the_memory_page_when_the_kept_entity_has_none(api):
    acme, dup = org(api, "acme.example", "Acme"), org(api, "acme-old.example", "Acme Old")
    memory = page(api)
    post(api, f"pages/{memory}/links", {"target": f"entity:{dup}", "role": "memory"})
    with api.app.state.store.transaction() as c:
        ES.merge(c, ANA, acme, dup)
    assert [p["file_id"] for p in get(api, f"pages?linked=entity:{acme}&role=memory")["pages"]] == [memory]
    with api.app.state.store.transaction() as c:
        ES.unmerge(c, ANA, dup)
    assert [p["file_id"] for p in get(api, f"pages?linked=entity:{dup}&role=memory")["pages"]] == [memory]
    assert get(api, f"pages?linked=entity:{acme}&role=memory")["pages"] == []


def test_a_restored_task_whose_entity_was_erased_names_none(api):
    from backend.entities import erase as ER

    acme = org(api, "acme.example", "Acme")
    task = post(api, "tasks", {"owner": "ops", "title": "Send the renewal", "entity": acme})
    post(api, f"tasks/{task['id']}/delete", {})
    with api.app.state.store.transaction() as c:
        ER.erase(c, ANA, acme, "request")
    post(api, f"tasks/{task['id']}/restore", {})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT entity_id FROM tasks WHERE id=?", (task["id"],)).fetchone()[0] == ""


def test_a_link_comes_off_by_its_old_id_after_a_merge_moved_it_and_a_memory_page_stays_shared(api):
    acme, dup = org(api, "acme.example", "Acme"), org(api, "acme-old.example", "Acme Old")
    memory = page(api)
    post(api, f"pages/{memory}/links", {"target": f"entity:{dup}", "role": "memory"})
    narrowed = api.patch(f"/api/v2/pages/{memory}", json={"audience": {"people": ["ana"]}}, headers=headers())
    assert narrowed.status_code == 409  # read by everyone who sees the entity
    with api.app.state.store.transaction() as c:
        ES.merge(c, ANA, acme, dup)  # acme had none: the link moved to acme
    gone = {"target": f"entity:{dup}", "role": "memory"}
    assert api.delete(f"/api/v2/pages/{memory}/links", params=gone, headers=headers()).status_code == 200
    assert get(api, f"pages?linked=entity:{acme}&role=memory")["pages"] == []
    again = api.delete(f"/api/v2/pages/{memory}/links", params=gone, headers=headers())
    assert again.status_code == 404  # nothing matched is never a silent 200


def test_status_and_owner_inputs_are_strict_and_a_replay_never_answers_through_a_merge(api):
    acme, dup = org(api, "acme.example", "Acme"), org(api, "acme-old.example", "Acme Old")
    assert post(api, "entities/status", {"ref": acme, "version": 0, "next_step_due": "20261201"}, expected=422)
    assert post(api, "entities/owner", {"ref": acme, "owner": "human:nobody"}, expected=404)
    body = {"ref": dup, "version": 0, "status": "lead"}
    post(api, "entities/status", body, key="status-once")
    with api.app.state.store.transaction() as c:
        ES.merge(c, ANA, acme, dup)
    replay = post(api, "entities/status", body, key="status-once", expected=409)["error"]
    assert replay["code"] == "merged"


def test_a_task_restored_after_its_entity_merged_names_the_kept_entity_and_dry_run_checks_it(api):
    acme, dup = org(api, "acme.example", "Acme"), org(api, "acme-old.example", "Acme Old")
    task = post(api, "tasks", {"owner": "ops", "title": "Send the renewal", "entity": dup})
    post(api, f"tasks/{task['id']}/delete", {})
    with api.app.state.store.transaction() as c:
        ES.merge(c, ANA, acme, dup)
    post(api, f"tasks/{task['id']}/restore", {})
    assert [t["id"] for t in get(api, f"tasks?status=all&entity={acme}")["tasks"]] == [task["id"]]
    checked = post(api, "tasks/dry-run", {"owner": "ops", "title": "Call them", "entity": "nobody.example"})
    assert checked["ok"] is False


def test_a_deleted_memory_page_frees_the_slot_and_a_retained_link_stays_until_unmerge(api):
    acme, dup = org(api, "acme.example", "Acme"), org(api, "acme-old.example", "Acme Old")
    first, second = page(api), page(api, "second.html")
    post(api, f"pages/{first}/links", {"target": f"entity:{acme}", "role": "memory"})
    assert api.delete(f"/api/v2/files/{first}", headers=headers()).status_code == 200
    post(api, f"pages/{second}/links", {"target": f"entity:{acme}", "role": "memory"})  # the slot is free

    third = page(api, "third.html")
    post(api, f"pages/{third}/links", {"target": f"entity:{dup}", "role": "memory"})
    with api.app.state.store.transaction() as c:
        ES.merge(c, ANA, acme, dup)  # acme has a memory page: dup's link stays on dup, hidden
    kept = {"target": f"entity:{dup}", "role": "memory"}
    assert api.delete(f"/api/v2/pages/{third}/links", params=kept, headers=headers()).status_code == 404
    assert get(api, "pages?linked=entity:&role=memory", expected=422)
    changes = get(api, "tasks?entity=acme.example&changed_after=x", expected=422)
    assert changes["error"]["code"] == "entity"
