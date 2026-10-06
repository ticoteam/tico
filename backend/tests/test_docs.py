"""Docs: internal docs (versions, locks, import, search) and linked docs (docs/docs.md)."""

from backend.tests.test_api import api, assign, claim, headers, ready, runner  # noqa: F401  (the api fixture)

ANA, BEN, CARA = "ana-test", "ben-test", "cara-test"      # owner, bot administrator, everyone else


def call(api, method, path, body=None, who=ANA, expected=200, **kw):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(who), **kw)
    assert r.status_code == expected, (path, r.status_code, r.text)
    return r.json()


def make(api, title="Refund policy", body="Refund within 30 days.", who=ANA, **fields):
    return call(api, "POST", "docs", {"title": title, "body": body, **fields}, who)["doc"]


def edit(api, doc, who=ANA, expected=200, **fields):
    return call(api, "PATCH", "docs/" + doc["id"], {"version": doc["version"], **fields}, who, expected)


def test_create_edit_conflict_history_and_restore(api):
    doc = make(api)
    assert doc["path"] == "refund-policy.md" and doc["version"] == 1 and doc["updated_by_name"]
    assert make(api, "Refund policy", "again")["path"] == "refund-policy-2.md"          # a default path never collides
    call(api, "POST", "docs", {"title": "x", "path": "Refund-Policy.md"}, expected=409)  # a chosen one does
    second = edit(api, doc, body="Refund within 14 days.", note="Shorter window")["doc"]
    assert second["version"] == 2
    stale = edit(api, doc, who=BEN, body="mine", expected=409)              # ben still holds version 1
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
    moved = edit(api, restored, path="policies/refunds")["doc"]                     # .md is added
    assert moved["path"] == "policies/refunds.md"
    listed = call(api, "GET", "docs", params={"path_prefix": "policies/"})
    assert [d["id"] for d in listed["docs"]] == [doc["id"]] and listed["next_cursor"] is None
    assert edit(api, moved, archived=True)["doc"]["archived"] is True
    assert doc["id"] not in [d["id"] for d in call(api, "GET", "docs")["docs"]]
    archived = call(api, "GET", "docs", params={"archived": True, "path_prefix": "policies/"})
    assert [d["id"] for d in archived["docs"]] == [doc["id"]]
    assert call(api, "GET", "docs/search", params={"q": "refund"})["results"][0]["id"] != doc["id"]
    make(api, "Other", path="policies/refunds.md")                           # its path is taken while it is archived
    back = call(api, "POST", "docs/%s/restore" % doc["id"], {"version": 4})["doc"]
    assert back["archived"] is False and back["path"] == "policies/refunds-2.md"
    for bad in ("../x.md", "a/../b.md", "a/./b.md"):
        call(api, "POST", "docs", {"title": "x", "path": bad}, expected=422)


def test_a_locked_doc_belongs_to_owners_and_bot_administrators(api):
    doc = make(api, who=CARA)                                                # anyone may write
    call(api, "PATCH", "docs/" + doc["id"], {"version": 1, "locked": True}, CARA, 403)
    locked = edit(api, doc, who=BEN, locked=True)["doc"]                     # a bot administrator may lock
    assert locked["locked"] is True and locked["version"] == 1               # locking is not a content change
    assert edit(api, locked, who=CARA, expected=403, body="edit")["error"]["code"] == "locked"
    call(api, "POST", "docs/%s/restore" % doc["id"], {"version": 1}, CARA, 403)
    assert call(api, "GET", "docs/" + doc["id"], who=CARA)["doc"]["locked"] is True   # everyone still reads it
    assert edit(api, locked, who=ANA, body="owner edit")["doc"]["body"] == "owner edit"
    assert edit(api, call(api, "GET", "docs/" + doc["id"])["doc"], who=BEN, locked=False)["doc"]["locked"] is False


def test_search_ranks_internal_docs_and_lists_linked_docs_beside_them(api):
    make(api, "Pricing and plans", "Starter is $35 a month. Growth is $79.", path="sales/pricing.md")
    make(api, "Refund policy", "Refunds happen within 30 days. Pricing questions go to sales.")
    call(api, "POST", "linked-docs", {"url": "https://help.acme.example/pricing", "title": "Public pricing page",
                                       "description": "What customers see"})
    hits = call(api, "GET", "docs/search", params={"q": "pricing"})["results"]
    assert {h["type"] for h in hits} == {"internal", "linked"}
    assert hits[0]["type"] == "internal" and hits[0]["title"] == "Pricing and plans"      # title match outranks a body mention
    internal = next(h for h in hits if h["type"] == "internal")
    assert set(internal) == {"type", "id", "path", "title", "section", "anchor", "excerpt", "score"}
    linked = next(h for h in hits if h["type"] == "linked")
    assert set(linked) == {"type", "id", "title", "url", "kind", "description", "score"} and linked["kind"] == "website"
    assert [h["title"] for h in hits if h["type"] == "internal"] == ["Pricing and plans", "Refund policy"]
    assert call(api, "GET", "docs/search", params={"q": "pric"})["results"]                # a word being typed
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


def test_a_bots_docs_tools_read_write_and_survive_a_concurrent_edit(api):
    from backend.tests.test_mcp import call as tool
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    call(api, "POST", "chat/ops", {"text": "go"}, BEN)
    token = claim(api, machine, "ops")["token"]
    err, made = tool(api, "hub_doc_write", {"path": "ops/runbook", "body": "# On-call runbook\n\nPage Ben."}, token=token)
    assert not err and made["doc"]["path"] == "ops/runbook.md" and made["doc"]["title"] == "On-call runbook"
    err, read = tool(api, "hub_doc_read", {"ref": "ops/runbook"}, token=token)
    assert not err and read["doc"]["body"].endswith("Page Ben.")
    # Someone edits between the bot's read and its write: the tool re-reads and retries once.
    real = api.app.state.docs.edit
    state = {"raced": False}

    def racing(request, doc_id, body):
        if not state["raced"]:
            state["raced"] = True
            edit(api, call(api, "GET", "docs/" + doc_id)["doc"], who=CARA, body="Cara changed it first")
        return real(request, doc_id, body)
    api.app.state.docs.edit = racing
    err, again = tool(api, "hub_doc_write", {"path": "ops/runbook.md", "body": "Bot rewrite", "note": "cleanup"}, token=token)
    api.app.state.docs.edit = real
    assert not err and again["doc"]["version"] == 3 and again["doc"]["body"] == "Bot rewrite"
    err, history = tool(api, "hub_doc_history", {"ref": made["doc"]["id"]}, token=token)
    assert [(v["actor"], v["note"]) for v in history["versions"]] == [("bot:ops", "cleanup"), ("human:cara", ""), ("bot:ops", "Created")]
    err, listing = tool(api, "hub_doc_list", {"prefix": "ops/"}, token=token)
    assert [d["path"] for d in listing["docs"]] == ["ops/runbook.md"]
    err, missing = tool(api, "hub_doc_read", {"ref": "nope"}, token=token)
    assert missing["refused"] == "docs"
    edit(api, call(api, "GET", "docs/" + made["doc"]["id"])["doc"], who=ANA, locked=True)
    err, refused = tool(api, "hub_doc_write", {"path": "ops/runbook.md", "body": "no"}, token=token)
    assert err and refused["error"] == "locked"


def test_upgrade_repairs_generated_docs_once_and_queues_next_run_refresh(api):
    from backend.store import H
    source = make(api, "Refund policy", path="finance/refunds.md")
    old = make(api, "Old prices", path="sales/pricing.md")
    edit(api, old, archived=True)
    make(api, "Prices", path="sales/pricing.md")
    idx = make(api, "Document index", "- `finance/refunds.md` (v1): Refunds.\n- `sales/pricing.md`: Prices.\n",
               path="_librarian/index.md")
    gap = make(api, "Missing and outdated company information", "Sources I could not read: None",
               path="_librarian/missing.md")
    faq = make(api, "FAQ", "Internal Hub docs with standing instructions. Read `knowledge/company.md`.", path="FAQ.md")
    custom = make(api, "Our terms", "Company policy.", path="_librarian/glossary.md")
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='librarian_fix33'")
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('librarian','Librarian','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('librarian','{}','ana')")
        c.execute("UPDATE docs SET archived=1 WHERE id=?", (source["id"],))  # Older archive, stale cache.
        c.execute("UPDATE docs SET updated_by='bot:librarian' WHERE id=?", (faq["id"],))
    api.app.state.store.initialize(seed_market=False)
    fresh = call(api, "GET", "docs/" + idx["id"])["doc"]
    assert fresh["title"] == "Docs index" and "refunds.md`" not in fresh["body"]
    assert "sales/pricing.md" in fresh["body"]
    assert call(api, "GET", "docs/" + gap["id"])["doc"]["title"] == "Docs gaps"
    assert call(api, "GET", "docs/" + faq["id"])["doc"]["body"] == "Internal Tico docs with Instructions. Read `knowledge/company.md`."
    assert call(api, "GET", "docs/" + custom["id"])["doc"]["body"] == custom["body"]
    assert call(api, "GET", "docs/%s/versions/1" % idx["id"])["version"]["body"] == idx["body"]
    api.app.state.store.initialize(seed_market=False)
    assert call(api, "GET", "docs/" + idx["id"])["doc"]["version"] == fresh["version"]
    with api.app.state.store.read() as c:
        tasks = c.execute("SELECT * FROM tasks WHERE owner='bot:librarian' AND title='Refresh the map'").fetchall()
        assert len(tasks) == 1 and tasks[0]["next_run"] == 1
        assert "unreadable" in tasks[0]["body"] and "archived" in tasks[0]["body"]
