"""Docs: internal docs (versions, locks, import, search) and linked docs (docs/docs.md)."""

from backend.tests.helpers.api import assign, claim, ready, runner
from backend import bot_settings as BS
from backend.tests.helpers.docs import ANA, BEN, CARA, call, make


def edit(api, doc, who=ANA, expected=200, **fields):
    return call(api, "PATCH", "docs/" + doc["id"], {"version": doc["version"], **fields}, who, expected)


def test_create_edit_conflict_history_and_restore(api):
    doc = make(api)
    assert doc["path"] == "refund-policy.md" and doc["version"] == 1 and doc["updated_by_name"]
    assert make(api, "Refund policy", "again")["path"] == "refund-policy-2.md"  # a default path never collides
    call(api, "POST", "docs", {"title": "x", "path": "Refund-Policy.md"}, expected=409)  # a chosen one does
    second = edit(api, doc, body="Refund within 14 days.", note="Shorter window")["doc"]
    assert second["version"] == 2
    stale = edit(api, doc, who=BEN, body="mine", expected=409)  # ben still holds version 1
    assert stale["error"]["code"] == "version_conflict" and stale["error"]["version"] == 2
    assert call(api, "GET", "docs/" + doc["id"])["doc"]["body"] == "Refund within 14 days."
    versions = call(api, "GET", "docs/%s/versions" % doc["id"])["versions"]
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["note"] == "Shorter window"
    assert versions[0]["current"] and versions[0]["actor_name"] and "body" not in versions[0]
    assert call(api, "GET", "docs/%s/versions/1" % doc["id"])["version"]["body"] == "Refund within 30 days."
    restored = call(api, "POST", "docs/%s/restore" % doc["id"], {"version": 1})["doc"]
    assert restored["version"] == 3 and restored["body"] == "Refund within 30 days."
    assert call(api, "GET", "docs/%s/versions" % doc["id"])["versions"][0]["note"] == "Restored version 1"
    call(api, "POST", "docs/%s/restore" % doc["id"], {"version": 9}, expected=404)
    moved = edit(api, restored, path="policies/refunds")["doc"]  # .md is added
    assert moved["path"] == "policies/refunds.md"
    listed = call(api, "GET", "docs", params={"path_prefix": "policies/"})
    assert [d["id"] for d in listed["docs"]] == [doc["id"]] and listed["next_cursor"] is None
    assert edit(api, moved, archived=True)["doc"]["archived"] is True
    assert doc["id"] not in [d["id"] for d in call(api, "GET", "docs")["docs"]]
    archived = call(api, "GET", "docs", params={"archived": True, "path_prefix": "policies/"})
    assert [d["id"] for d in archived["docs"]] == [doc["id"]]
    assert call(api, "GET", "docs/search", params={"q": "refund"})["results"][0]["id"] != doc["id"]
    make(api, "Other", path="policies/refunds.md")  # its path is taken while it is archived
    back = call(api, "POST", "docs/%s/restore" % doc["id"], {"version": 4})["doc"]
    assert back["archived"] is False and back["path"] == "policies/refunds-2.md"
    for bad in ("../x.md", "a/../b.md", "a/./b.md"):
        call(api, "POST", "docs", {"title": "x", "path": bad}, expected=422)


def every_page(api, **params):
    seen, cursor = [], ""
    while True:
        page = call(api, "GET", "docs", params={**params, "limit": 1, "cursor": cursor})
        seen += [d["id"] for d in page["docs"]]
        cursor = page["next_cursor"]
        if not cursor or len(seen) > 10:
            return seen


def test_paging_docs_one_at_a_time_shows_each_doc_once(api):
    live = [
        make(api, "x", path=p)["id"] for p in ("straße.md", "Éclair.md", "éclair.md", "😀" * 300, "😁.md", "zeta.md")
    ]
    assert sorted(every_page(api)) == sorted(live)  # no repeat after ß or É, and a 300-emoji path's cursor is accepted
    ties = []
    for _ in range(2):  # archived docs may share a path
        doc = make(api, "x", path="old.md")
        ties.append(edit(api, doc, archived=True)["doc"]["id"])
    assert sorted(every_page(api, archived=True)) == sorted(ties)


def test_a_doc_body_reads_back_exactly_as_sent_while_its_title_and_path_trim(api):
    body = "    indented code on line 1\n\nText.\n\n"  # leading indentation, a final newline and a blank line
    doc = call(api, "POST", "docs", {"title": "  Runbook  ", "path": " ops/runbook.md ", "body": body})["doc"]
    assert (doc["title"], doc["path"], doc["body"]) == ("Runbook", "ops/runbook.md", body)
    assert call(api, "GET", "docs/" + doc["id"])["doc"]["body"] == body
    assert call(api, "GET", "docs/%s/versions/1" % doc["id"])["version"]["body"] == body
    edited = edit(api, doc, body="\tTabbed.\r\n", title=" Runbook 2 ")["doc"]
    assert (edited["title"], edited["body"], edited["version"]) == ("Runbook 2", "\tTabbed.\r\n", 2)
    again = edit(api, edited, body="\tTabbed.\r\n\n")["doc"]  # only the ends differ: still a new version
    assert again["version"] == 3 and call(api, "GET", "docs/" + doc["id"])["doc"]["body"] == "\tTabbed.\r\n\n"
    assert make(api, "Blank", "   ")["body"] == "   "


def test_a_locked_doc_belongs_to_owners_and_bot_administrators(api):
    doc = make(api, who=CARA)  # anyone may write
    call(api, "PATCH", "docs/" + doc["id"], {"version": 1, "locked": True}, CARA, 403)
    locked = edit(api, doc, who=BEN, locked=True)["doc"]  # a bot administrator may lock
    assert locked["locked"] is True and locked["version"] == 1  # locking is not a content change
    assert edit(api, locked, who=CARA, expected=403, body="edit")["error"]["code"] == "locked"
    call(api, "POST", "docs/%s/restore" % doc["id"], {"version": 1}, CARA, 403)
    assert call(api, "GET", "docs/" + doc["id"], who=CARA)["doc"]["locked"] is True  # everyone still reads it
    assert edit(api, locked, who=ANA, body="owner edit")["doc"]["body"] == "owner edit"
    assert edit(api, call(api, "GET", "docs/" + doc["id"])["doc"], who=BEN, locked=False)["doc"]["locked"] is False


def test_search_ranks_internal_docs_and_lists_linked_docs_beside_them(api):
    make(api, "Pricing and plans", "Starter is $35 a month. Growth is $79.", path="sales/pricing.md")
    make(api, "Refund policy", "Refunds happen within 30 days. Pricing questions go to sales.")
    call(
        api,
        "POST",
        "linked-docs",
        {
            "url": "https://help.acme.example/pricing",
            "title": "Public pricing page",
            "description": "What customers see",
        },
    )
    hits = call(api, "GET", "docs/search", params={"q": "pricing"})["results"]
    assert {h["type"] for h in hits} == {"internal", "linked"}
    assert (
        hits[0]["type"] == "internal" and hits[0]["title"] == "Pricing and plans"
    )  # title match outranks a body mention
    internal = next(h for h in hits if h["type"] == "internal")
    assert set(internal) == {"type", "id", "path", "title", "section", "anchor", "excerpt", "score"}
    linked = next(h for h in hits if h["type"] == "linked")
    assert set(linked) == {"type", "id", "title", "url", "kind", "description", "score"} and linked["kind"] == "website"
    assert [h["title"] for h in hits if h["type"] == "internal"] == ["Pricing and plans", "Refund policy"]
    assert call(api, "GET", "docs/search", params={"q": "pric"})["results"]  # a word being typed
    # A question in a sentence still finds what answers it (any meaningful word, best first).
    sentence = call(api, "GET", "docs/search", params={"q": "how do we handle a refund request"})["results"]
    assert sentence[0]["title"] == "Refund policy"
    assert call(api, "GET", "docs/search", params={"q": "zebra"})["results"] == []
    # The assistant's fast path sees the same docs.
    fast = call(api, "GET", "context/search", params={"q": "pricing", "source": "docs"})["results"]
    assert {r["kind"] for r in fast} == {"document", "linked_doc"}
    assert call(api, "GET", "context/document", params={"id": "sales/pricing.md"})["content"].startswith("Starter")
    # A pasted heading does not erase the document's searchable title.
    make(api, "Export guide", "# Steps\n\nChoose CSV.", path="notes/a.md")
    export = call(api, "GET", "docs/search", params={"q": "export"})["results"][0]
    assert export["title"] == "Export guide" and export["section"] == "Steps" and "CSV" in export["excerpt"]


def bot_token(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    call(api, "POST", "chat/ops", {"text": "go"}, BEN)
    return claim(api, machine, "ops")["token"]


def counting(api, name):
    """Count the write requests that reach Docs.<name> (create or edit) while the test runs."""
    real, seen = getattr(api.app.state.docs, name), []

    def counted(*args):
        seen.append(args[1:])
        return real(*args)

    setattr(api.app.state.docs, name, counted)
    return seen


def doc_events(api, action):
    import json

    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor, target, detail FROM events WHERE action=? ORDER BY rowid", (action,))
        return [(r["actor"], r["target"], json.loads(r["detail"] or "{}")) for r in rows.fetchall()]


def test_a_bots_docs_tools_read_write_list_and_respect_a_lock(api):
    from backend.tests.helpers.mcp import call as tool

    token = bot_token(api)
    err, made = tool(
        api, "tico_doc_write", {"path": "ops/runbook", "body": "# On-call runbook\n\nPage Ben."}, token=token
    )
    assert not err and made["doc"]["path"] == "ops/runbook.md" and made["doc"]["title"] == "On-call runbook"
    err, read = tool(api, "tico_doc_read", {"ref": "ops/runbook"}, token=token)
    assert not err and read["doc"]["body"].endswith("Page Ben.") and read["doc"]["version"] == 1
    # AC-2: written on the version it read, nobody changed it: the bot's text and note land.
    err, again = tool(
        api,
        "tico_doc_write",
        {"path": "ops/runbook.md", "body": "Bot rewrite", "note": "cleanup", "base_version": 1},
        token=token,
    )
    assert not err and again["doc"]["version"] == 2 and again["doc"]["body"] == "Bot rewrite"
    err, history = tool(api, "tico_doc_history", {"ref": made["doc"]["id"]}, token=token)
    assert [(v["actor"], v["note"]) for v in history["versions"]] == [("bot:ops", "cleanup"), ("bot:ops", "Created")]
    err, listing = tool(api, "tico_doc_list", {"prefix": "ops/"}, token=token)
    assert [d["path"] for d in listing["docs"]] == ["ops/runbook.md"]
    err, missing = tool(api, "tico_doc_read", {"ref": "nope"}, token=token)
    assert missing["refused"] == "docs"
    # AC-12: a locked doc refuses the bot with or without a base version.
    edit(api, call(api, "GET", "docs/" + made["doc"]["id"])["doc"], who=ANA, locked=True)
    for extra in ({}, {"base_version": 2}):
        err, refused = tool(api, "tico_doc_write", {"path": "ops/runbook.md", "body": "no", **extra}, token=token)
        assert err and refused["error"] == "locked"


def test_a_bot_never_overwrites_a_persons_edit_it_did_not_read(api):
    from backend.tests.helpers.mcp import call as tool

    token = bot_token(api)
    doc = make(api, "On-call runbook", "Call 555-0100.", who=CARA, path="ops/runbook.md")
    err, read = tool(api, "tico_doc_read", {"ref": "ops/runbook.md"}, token=token)
    assert not err and read["doc"]["version"] == 1
    fixed = edit(api, doc, who=CARA, body="Call 555-0199.", note="fix on-call number")["doc"]  # while the bot works
    edits = counting(api, "edit")
    write = {"path": "ops/runbook.md", "body": "Call 555-0100.\n\nEscalate to Ben.", "base_version": 1}
    for attempt in (1, 2):  # AC-6: the same call is refused again; the tool never resends on its own
        err, refused = tool(api, "tico_doc_write", write, token=token)
        assert err and refused["error"] == "version_conflict"  # AC-1
        assert refused["version"] == 2 and refused["updated_by_name"] == fixed["updated_by_name"]
        assert refused["updated_at"] == fixed["updated_at"]
        name = fixed["updated_by_name"]
        assert f"{name} saved version 2 after you read version 1" in refused["detail"]
        assert "--base-version 2" in refused["detail"]
        assert len(edits) == attempt  # exactly one write request per call
    current = call(api, "GET", "docs/" + doc["id"])["doc"]
    assert current["version"] == 2 and current["body"] == "Call 555-0199."
    # Read again and written on what it read: lands on top of Cara's fix.
    err, landed = tool(
        api, "tico_doc_write", {**write, "body": "Call 555-0199.\n\nEscalate.", "base_version": 2}, token=token
    )
    assert not err and landed["doc"]["version"] == 3
    accepted = [d for a, t, d in doc_events(api, "docs.updated") if a == "bot:ops"]
    assert accepted == [
        {"path": "ops/runbook.md", "version": 3, "base_version": True, "mode": "whole", "refused": False}
    ]
    refusals = doc_events(api, "docs.write_refused")
    assert [(a, t, d["code"], d["base_version"], d["mode"], d["refused"]) for a, t, d in refusals] == [
        ("bot:ops", doc["id"], "version_conflict", True, "whole", True)
    ] * 2


def test_without_a_base_version_a_bot_replaces_only_its_own_latest_version(api):
    from backend.tests.helpers.mcp import call as tool

    token = bot_token(api)
    err, mine = tool(api, "tico_doc_write", {"path": "ops/notes.md", "body": "v1"}, token=token)
    # AC-4: the latest version is this bot's own: it lands as today.
    err, again = tool(api, "tico_doc_write", {"path": "ops/notes.md", "body": "v2", "note": "more"}, token=token)
    assert not err and again["doc"]["version"] == 2 and again["doc"]["body"] == "v2"
    # AC-3: a person saved last: refused, and told to read the doc and pass its version.
    edit(api, again["doc"], who=CARA, body="Cara's v3")
    edits = counting(api, "edit")
    err, refused = tool(api, "tico_doc_write", {"path": "ops/notes.md", "body": "bot v4"}, token=token)
    assert err and refused["error"] == "base_version_required" and refused["version"] == 3
    assert "tico doc read ops/notes.md" in refused["detail"] and "--base-version 3" in refused["detail"]
    assert len(edits) == 1
    assert call(api, "GET", "docs/" + mine["doc"]["id"])["doc"]["body"] == "Cara's v3"
    accepted = [d for a, t, d in doc_events(api, "docs.updated") if a == "bot:ops"]
    assert accepted == [
        {"path": "ops/notes.md", "version": 2, "base_version": False, "mode": "whole", "refused": False}
    ]
    [(actor, _target, detail)] = doc_events(api, "docs.write_refused")
    assert actor == "bot:ops" and detail["code"] == "base_version_required" and detail["base_version"] is False


def test_a_bot_create_never_turns_into_an_overwrite_of_a_doc_made_meanwhile(api):
    from backend.tests.helpers.mcp import call as tool

    token = bot_token(api)
    real = api.app.state.docs.create
    made, creates, edits = {}, [], counting(api, "edit")

    def racing(request, body, imported=None):  # AC-5: Cara creates the doc after the bot looked, before its create
        creates.append(request.state.identity.actor)
        if not made:  # Cara's own create comes through here too: race only once
            made["doc"] = None
            made["doc"] = make(api, "Runbook", "Cara's runbook", who=CARA, path="ops/runbook.md")
        return real(request, body, imported)

    api.app.state.docs.create = racing
    err, refused = tool(api, "tico_doc_write", {"path": "ops/runbook.md", "body": "bot text"}, token=token)
    api.app.state.docs.create = real
    assert err and refused["error"] == "path_taken" and refused["version"] == 1
    assert "--base-version 1" in refused["detail"]
    assert creates.count("bot:ops") == 1 and edits == []  # one write request, never a retry as an edit
    assert call(api, "GET", "docs/" + made["doc"]["id"])["doc"]["body"] == "Cara's runbook"
    [(actor, _target, detail)] = doc_events(api, "docs.write_refused")
    assert actor == "bot:ops" and detail["code"] == "path_taken" and detail["path"] == "ops/runbook.md"


def test_upgrade_repairs_generated_docs_once_and_queues_a_map_refresh(api):
    from backend.store import H

    source = make(api, "Refund policy", path="finance/refunds.md")
    old = make(api, "Old prices", path="sales/pricing.md")
    edit(api, old, archived=True)
    make(api, "Prices", path="sales/pricing.md")
    idx = make(
        api,
        "Document index",
        "- `finance/refunds.md` (v1): Refunds.\n- `sales/pricing.md`: Prices.\n",
        path="_librarian/index.md",
    )
    gap = make(
        api, "Missing and outdated company information", "Sources I could not read: None", path="_librarian/missing.md"
    )
    faq = make(api, "FAQ", "Internal Hub docs with standing instructions. Read `knowledge/company.md`.", path="FAQ.md")
    custom = make(api, "Our terms", "Company policy.", path="_librarian/glossary.md")
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='librarian_fix33'")
        c.execute(
            "INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
            "VALUES('librarian','librarian','Librarian','','active',?)",
            (H.now(),),
        )
        c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('librarian','ana')")
        BS.write(c, 'librarian', {"runtime": "fake", "host": "keeper"})
        c.execute("UPDATE docs SET archived=1 WHERE id=?", (source["id"],))  # Older archive, stale cache.
        c.execute("UPDATE docs SET updated_by_id='bot:librarian' WHERE id=?", (faq["id"],))
    api.app.state.store.initialize(seed_market=False)
    fresh = call(api, "GET", "docs/" + idx["id"])["doc"]
    assert fresh["title"] == "Docs index" and "refunds.md`" not in fresh["body"]
    assert "sales/pricing.md" in fresh["body"]
    assert call(api, "GET", "docs/" + gap["id"])["doc"]["title"] == "Docs gaps"
    assert (
        call(api, "GET", "docs/" + faq["id"])["doc"]["body"]
        == "Internal Tico docs with Instructions. Read `knowledge/company.md`."
    )
    assert call(api, "GET", "docs/" + custom["id"])["doc"]["body"] == custom["body"]
    assert call(api, "GET", "docs/%s/versions/1" % idx["id"])["version"]["body"] == idx["body"]
    api.app.state.store.initialize(seed_market=False)
    assert call(api, "GET", "docs/" + idx["id"])["doc"]["version"] == fresh["version"]
    with api.app.state.store.read() as c:
        tasks = c.execute("SELECT * FROM tasks WHERE owner_id='bot:librarian' AND title='Refresh the map'").fetchall()
        assert len(tasks) == 1 and tasks[0]["status"] == "open"
        assert "unreadable" in tasks[0]["body"] and "archived" in tasks[0]["body"]
