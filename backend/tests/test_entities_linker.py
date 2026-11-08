"""The linker (backend/entities/linker.py, spec §7.1 steps 1 and 2) on the API fixture's team: ana owns the workspace,
ben and cara are members, all at acme.example.

What these pin: a run trace links what its attempt read; an exact email or domain links with its quote and occurrence,
and a name links only when it names exactly one entity; an ambiguous name links nothing, a hidden entity counts toward
the ambiguity and is never linked itself; a new revision replaces the linker's mentions and keeps another method's; a
rerun on the same text writes the same mentions; and the cursor pass reads a changed text once, and again only when it
changes.
"""

from backend import mailbox_access as MA
from backend.entities import linker as LK
from backend.entities import store as ES
from backend.tests.test_api import api  # noqa: F401  (the pytest fixture)

ANA = "human:ana"


def tx(api):
    return api.app.state.store.transaction()


def org(c, domain, name):
    return ES.upsert(c, ANA, "organization", match={"domain": domain}, name=name)["id"]


def person(c, email, name):
    return ES.upsert(c, ANA, "person", match={"email": email}, name=name)["id"]


def shape(rows):
    return sorted((r["entity_id"], r["role"], r["quote"], r["occurrence"], r["method"], r["status"]) for r in rows)


def test_a_run_trace_links_the_entities_its_attempt_read(api):
    with tx(api) as c:
        acme = org(c, "acme.example", "Acme Lights")
        ES.record_lookup(c, "turn_run", acme)
        tally = LK.link_output(c, "message", "msg_run", "rev1", "Quick update, nothing new.", run_id="turn_run")
        assert tally == {"linked": 1, "suggested": 0}
        rows = ES.mentions(c, ANA, "message", "msg_run")
        assert [(r["entity_id"], r["role"], r["method"], r["confidence"], r["status"]) for r in rows] == [
            (acme, "subject", "run_trace", 0.9, "linked")
        ]


def test_an_exact_email_and_domain_link_with_their_quote_and_occurrence(api):
    with tx(api) as c:
        jane = person(c, "jane@example.test", "Jane Ortiz")
        acme = org(c, "acme.example", "Acme Lights")
        text = "Email jane@example.test today. Then Jane@example.test again, and the account is acme.example."
        LK.link_output(c, "message", "msg_exact", "rev1", text)
        rows = ES.mentions(c, ANA, "message", "msg_exact")
        assert shape(rows) == sorted(
            [
                (jane, "mentioned", "jane@example.test", 0, "exact", "linked"),
                (jane, "mentioned", "Jane@example.test", 1, "exact", "linked"),
                (acme, "mentioned", "acme.example", 0, "exact", "linked"),
            ]
        )
        assert {r["confidence"] for r in rows} == {1.0}


def test_an_ambiguous_name_links_nothing_and_a_unique_name_links(api):
    with tx(api) as c:
        org(c, "one.example", "Acme Lights")
        org(c, "two.example", "Acme Lights")
        quiet = org(c, "quiet.example", "Quiet Co")
        LK.link_output(c, "message", "msg_amb", "rev1", "Acme Lights shipped. Quiet Co did not.")
        rows = ES.mentions(c, ANA, "message", "msg_amb")
        assert shape(rows) == [(quiet, "mentioned", "Quiet Co", 0, "exact", "linked")]


def test_an_entity_its_readers_cannot_see_is_never_linked(api):
    with tx(api) as c:
        ben_box = MA.ensure_mailbox(c, "ben@acme.example", person_id="ben", created_by=ANA)
        hidden = org(c, "private.example", "Private Co")
        ES.add_source(c, ANA, hidden, "mailbox", ben_box["id"], visibility="restricted")
        acme = org(c, "acme.example", "Acme Lights")
        ES.record_lookup(c, "turn_hidden", hidden)
        LK.link_output(
            c,
            "message",
            "msg_hidden",
            "rev1",
            "Private Co and private.example are quiet; Acme Lights is the account.",
            run_id="turn_hidden",
        )
        rows = ES.mentions(c, ANA, "message", "msg_hidden")
        assert shape(rows) == [(acme, "mentioned", "Acme Lights", 0, "exact", "linked")]
        assert (
            c.execute("SELECT COUNT(*) AS n FROM entity_mentions WHERE entity_id = ?", (hidden,)).fetchone()["n"] == 0
        )


def test_a_hidden_entity_still_makes_a_shared_name_ambiguous(api):
    with tx(api) as c:
        ben_box = MA.ensure_mailbox(c, "ben@acme.example", person_id="ben", created_by=ANA)
        org(c, "acme.example", "Acme Lights")
        shadow = org(c, "shadow.example", "Acme Lights")
        ES.add_source(c, ANA, shadow, "mailbox", ben_box["id"], visibility="restricted")
        acme = c.execute("SELECT id FROM entities WHERE primary_domain = 'acme.example'").fetchone()["id"]
        LK.link_output(c, "message", "msg_shadow", "rev1", "Acme Lights shipped to acme.example.")
        rows = ES.mentions(c, ANA, "message", "msg_shadow")
        assert shape(rows) == [(acme, "mentioned", "acme.example", 0, "exact", "linked")]


def test_a_new_revision_replaces_the_linker_mentions_and_keeps_another_methods(api):
    with tx(api) as c:
        jane = person(c, "jane@example.test", "Jane Ortiz")
        acme = org(c, "acme.example", "Acme Lights")
        LK.link_output(c, "message", "msg_rev", "rev1", "Ask jane@example.test about it.")
        c.execute(
            "INSERT INTO entity_mentions (id, mentionable_type, mentionable_id, mentionable_revision, entity_id, role, "
            "quote, occurrence, confidence, method, status, created_at) "
            "VALUES ('men_inline', 'message', 'msg_rev', 'rev1', ?, 'mentioned', 'Jane', 0, 1.0, 'inline', 'linked', "
            "'2026-11-07T10:00:00Z')",
            (acme,),
        )
        LK.link_output(c, "message", "msg_rev", "rev2", "Now acme.example is the account.")
        rows = ES.mentions(c, ANA, "message", "msg_rev")
        assert shape(rows) == sorted(
            [
                (acme, "mentioned", "acme.example", 0, "exact", "linked"),
                (acme, "mentioned", "Jane", 0, "inline", "linked"),
            ]
        )
        assert {r["mentionable_revision"] for r in rows if r["method"] == "exact"} == {"rev2"}
        counts = {r["id"]: r["mentions_count"] for r in c.execute("SELECT id, mentions_count FROM entities").fetchall()}
        assert counts[jane] == 0 and counts[acme] == 2


def test_a_rerun_on_the_same_text_writes_the_same_mentions(api):
    with tx(api) as c:
        person(c, "jane@example.test", "Jane Ortiz")
        org(c, "acme.example", "Acme Lights")
        text = "Ask jane@example.test about Acme Lights and acme.example."
        LK.link_output(c, "message", "msg_again", "rev1", text)
        first = shape(ES.mentions(c, ANA, "message", "msg_again"))
        LK.link_output(c, "message", "msg_again", "rev1", text)
        again = shape(ES.mentions(c, ANA, "message", "msg_again"))
        assert again == first and len(first) == 3


def test_the_pass_links_a_changed_doc_once_and_again_only_when_it_changes(api):
    store = api.app.state.store
    with tx(api) as c:
        acme = org(c, "acme.example", "Acme Lights")
        c.execute(
            "INSERT INTO docs (id, path, title, body, locked, version, created_by_id, created_at, updated_by_id, "
            "updated_at, archived) VALUES ('doc_pass', 'notes/pass.md', 'Pass', 'Shipping to acme.example.', 0, 1, ?, "
            "?, ?, ?, 0)",
            (ANA, "2026-11-07T09:00:00Z", ANA, "2026-11-07T09:00:00Z"),
        )
    LK.run_pass(store)
    with tx(api) as c:
        assert shape(ES.mentions(c, ANA, "doc", "doc_pass")) == [
            (acme, "mentioned", "acme.example", 0, "exact", "linked")
        ]
    assert LK.run_pass(store) == {"texts": 0, "linked": 0, "suggested": 0}
    with tx(api) as c:
        c.execute(
            "UPDATE docs SET body = 'Shipping to somewhere else.', updated_at = ? WHERE id = 'doc_pass'",
            ("2026-11-07T09:30:00Z",),
        )
    again = LK.run_pass(store)
    assert again["texts"] == 1 and again["linked"] == 0
    with tx(api) as c:
        assert ES.mentions(c, ANA, "doc", "doc_pass") == []
