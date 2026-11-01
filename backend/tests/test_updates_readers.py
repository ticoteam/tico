"""A person's drafted update whose facts held a meeting summary, or a report's update that did, is flagged
`meetings` and read only by meeting readers (the owner and people with an email on file, as backend/sql.py
reads meetings): any bot, restricted or not, and a member with no email get it as not found, and the feed,
tree, counts, links and batch actions leave it out. Fixtures are made-up strings only."""

import json
import sqlite3

from backend import updates
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, post, restrict, setup_attempt  # noqa: F401

DAY = "2026-09-29"
NOTE = "Synthetic meeting note alpha"


def draft(store, author, body):
    """Run one person's draft the way the queue does, returning (the posted update, the facts the model saw)."""
    with store.transaction() as c:
        c.execute("INSERT INTO update_queue(id,bot,kind,day,rank,state,sent_at) VALUES(?,?,'daily',?,0,'sent',?) "
                  "ON CONFLICT(bot,kind,day) DO UPDATE SET state='sent', sent_at=excluded.sent_at",
                  (H.new_id(), author, DAY, H.now()))
    seen = []
    posted = updates.write_draft(store, author, lambda system, user: seen.append(user) or {"body": body}, "coo")
    return posted, seen[0]


def flag(store, uid):
    with store.read() as c:
        return c.execute("SELECT meetings FROM updates WHERE id=?", (uid,)).fetchone()[0]


def test_a_draft_that_drew_on_meetings_is_read_only_by_meeting_readers(api, monkeypatch):
    monkeypatch.setattr(updates, "today", lambda at=None: DAY)
    monkeypatch.setitem(api.app.state.auth.settings.test_identities, "dan-test", Identity("human:dan", "human", ""))
    post(api, "humans/ben", {"reports_to": "ana"})
    store = api.app.state.store
    with store.transaction() as c:
        restrict(c, "finance", people=["ana"])
        c.execute("INSERT INTO humans(id,name,email) VALUES('dan','Dan',NULL)")
        c.execute("INSERT INTO meetings(id,title,owner_id,metadata,notes,created_at,updated_at,review_state) "
                  "VALUES('m1','Synthetic sync','zed@acme.example',?,?,?,?,'live')",
                  (json.dumps({"kind": "meeting", "calendar": {"attendees": ["ben@acme.example"]}}), NOTE, H.now(), H.now()))
    ben, facts = draft(store, "human:ben", "- Synthetic draft bullet one")
    assert NOTE in facts and flag(store, ben["id"]) == 1
    cara, facts = draft(store, "human:cara", "- Synthetic draft bullet two")
    assert NOTE not in facts and flag(store, cara["id"]) == 0, "no meeting summary, no flag"
    ana, facts = draft(store, "human:ana", "- Synthetic draft bullet three")
    assert NOTE not in facts and "Synthetic draft bullet one" in facts and flag(store, ana["id"]) == 1, \
        "a report's flag carries up"
    # Ana replaces her draft with her own words: unflagged, still linked to Ben's update it was written from.
    own = post(api, "updates", {"body": "- Synthetic own words", "replace_draft": True})["update"]
    assert own["id"] == ana["id"] and flag(store, ana["id"]) == 0

    tokens = {"bot": setup_attempt(api, "ops")[2]["token"], "restricted bot": setup_attempt(api, "finance")[2]["token"],
              "member with no email": "dan-test"}
    missing = api.get("/api/v2/updates/" + H.new_id(), headers={"Authorization": "Bearer ana-test"})
    for who, token in tokens.items():
        auth = {"Authorization": "Bearer " + token}
        shown = api.get("/api/v2/updates/" + ben["id"], headers=auth)
        assert (shown.status_code, shown.json()) == (missing.status_code, missing.json()), who + ": exactly not found"
        feed = get(api, "updates", token=token)
        ids = [u["id"] for u in feed["updates"]]
        assert ben["id"] not in ids and cara["id"] in ids and ana["id"] in ids, who
        assert feed["unread"] == sum(not u["read"] for u in feed["updates"]), who + ": the badge counts the feed"
        assert get(api, "updates/unread", token=token)["unread"] == feed["unread"], who
        tree = json.dumps(get(api, "updates/tree?scope=all", token=token))
        assert ben["id"] not in tree and "Synthetic draft bullet one" not in tree and cara["id"] in tree, who
        assert get(api, "updates/" + ana["id"], token=token)["update"]["inputs"] == [], who + ": no link to it"
        assert ben["id"] not in json.dumps(feed), who
    post(api, "updates/read", {"ids": [ben["id"]]}, token="dan-test", expected=404)
    post(api, "updates/archive", {"ids": [ben["id"]], "archived": True}, token="dan-test", expected=404)
    assert post(api, "updates/read", {"all": True}, token="dan-test")["marked"] == 2, "mark all skips it too"

    for token in ("ana-test", "ben-test", "cara-test"):                 # the owner and people with an email
        assert get(api, "updates/" + ben["id"], token=token)["update"]["body"] == "- Synthetic draft bullet one"
        assert ben["id"] in [u["id"] for u in get(api, "updates", token=token)["updates"]]
        assert [i["id"] for i in get(api, "updates/" + ana["id"], token=token)["update"]["inputs"]] == [ben["id"]]

    # Ben edits his draft: still flagged. A redraft recomputes it; his own words clear it.
    post(api, "updates", {"body": "- Synthetic edited bullet"}, token="ben-test")
    assert flag(store, ben["id"]) == 1
    get(api, "updates/" + ben["id"], token=tokens["bot"], expected=404)
    with store.transaction() as c:
        c.execute("UPDATE updates SET edited_by=NULL WHERE id=?", (ben["id"],))   # as if never edited
        c.execute("DELETE FROM meetings WHERE id='m1'")
    again, facts = draft(store, "human:ben", "- Synthetic redraft bullet")
    assert again["id"] == ben["id"] and NOTE not in facts and flag(store, ben["id"]) == 0
    with store.transaction() as c:
        updates.post(c, "human:ben", "- Synthetic flagged bullet", day=DAY, drafted_by="coo", meetings=True)
    post(api, "updates", {"body": "- Synthetic own bullet", "replace_draft": True}, token="ben-test")
    assert flag(store, ben["id"]) == 0
    assert get(api, "updates/" + ben["id"], token=tokens["bot"])["update"]["body"] == "- Synthetic own bullet"


def test_a_bot_managers_request_names_a_flagged_report_without_its_text(api, monkeypatch):
    """No person reports to a bot today (org_parent never puts one there), so the chart is forced."""
    monkeypatch.setattr(updates, "today", lambda at=None: DAY)
    monkeypatch.setattr(updates, "direct_reports",
                        lambda c, author, chart=None: ["human:ben", "human:cara"] if author == "ops" else [])
    with api.app.state.store.transaction() as c:
        updates.post(c, "human:ben", "- Synthetic flagged report", day=DAY, drafted_by="coo", meetings=True)
        updates.post(c, "human:cara", "- Synthetic plain report", day=DAY, drafted_by="coo")
        text = updates.request_text(c, "ops", "daily", DAY)
        posted = updates.post(c, "ops", "- Synthetic manager bullet", day=DAY)
        ben = updates._label(c, "human:ben")
    assert "Synthetic plain report" in text and "Synthetic flagged report" not in text
    assert ben + ": posted an update" in text, "the report is named as having posted"
    assert [i["author"] for i in posted["inputs"]] == ["human:cara"], "a bot's own view links no flagged input"


def test_the_migration_flags_every_existing_assistant_drafted_person_update(tmp_path):
    c = sqlite3.connect(tmp_path / "old.db", isolation_level=None)
    c.executescript(updates.SCHEMA)
    for column in ("slides", "drafted_by", "edited_by", "inputs"):
        c.execute(f"ALTER TABLE updates ADD COLUMN {column} TEXT")
    rows = [("d", "human:ben", "coo", None), ("e", "human:cara", "coo", "human:cara"), ("o", "human:ana", None, None),
            ("b", "ops", None, None)]
    for uid, bot, drafted, edited in rows:
        c.execute("INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at,drafted_by,edited_by) "
                  "VALUES(?,?,'daily',?,'h','- b','t','t',?,?)", (uid, bot, uid, drafted, edited))
    updates.ensure_schema(c)
    assert dict(c.execute("SELECT id, meetings FROM updates")) == {"d": 1, "e": 1, "o": 0, "b": 0}
    c.execute("INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at,drafted_by,meetings) "
              "VALUES('n','human:dan','daily','n','h','- b','t','t','coo',0)")
    updates.ensure_schema(c)
    assert c.execute("SELECT meetings FROM updates WHERE id='n'").fetchone()[0] == 0, "it runs once, not on every start"


def test_a_member_with_no_email_posts_over_a_flagged_draft_in_their_own_words(api, monkeypatch):
    """They were never shown the flagged draft, so what they post is theirs: unflagged, readable by them, and the
    reply links no flagged input. The chart opens on the latest day they may read, not a later flagged one."""
    monkeypatch.setattr(updates, "today", lambda at=None: DAY)
    monkeypatch.setitem(api.app.state.auth.settings.test_identities, "cara-test", Identity("human:cara", "human", ""))
    monkeypatch.setattr(updates, "direct_reports",
                        lambda c, author, chart=None: ["human:ben"] if author == "human:cara" else [])
    store = api.app.state.store
    with store.transaction() as c:
        ben = updates.post(c, "human:ben", "- Synthetic flagged report", day=DAY, drafted_by="coo", meetings=True)
        cara = updates.post(c, "human:cara", "- Synthetic carried draft", day=DAY, drafted_by="coo", meetings=True)
    get(api, "updates/" + cara["id"], token="cara-test", expected=404)
    mine = post(api, "updates", {"body": "- Synthetic words from Cara"}, token="cara-test")["update"]
    assert mine["id"] == cara["id"] and flag(store, cara["id"]) == 0 and mine["inputs"] == []
    assert get(api, "updates/" + cara["id"], token="cara-test")["update"]["body"] == "- Synthetic words from Cara"
    assert ben["id"] not in json.dumps(mine)
    post(api, "updates", {"body": "- Synthetic edit from Ben"}, token="ben-test")
    assert flag(store, ben["id"]) == 1, "a meeting reader's edit keeps the flag"

    with store.transaction() as c:
        updates.post(c, "human:ben", "- Synthetic later flagged", day="2026-09-30", drafted_by="coo", meetings=True)
    assert get(api, "updates/tree?scope=all", token="cara-test")["day"] == DAY
    assert get(api, "updates/tree?scope=all", token="ana-test")["day"] == "2026-09-30"
