"""The threads core contracts (docs/dev/threads-contract.md) on the demo world: C1's three renderings agree, a
held post writes nothing, a write that fails mid-way leaves nothing behind, sub-thread starts race to one row,
revisions apply highest-first, the loop guard defers and resumes, inbox readers read deliveries, reactions keep to
readers, a run's reply lands in its sub-thread, and session keys keep private and public apart.

The migration's reader sets are pinned by test_threads_migration_e2e.py (goldens of the pre-threads predicates).
"""

import pytest

from backend import org_rule_check, threads
from backend.store import H, Problem
from backend.tests.test_threads_migration_e2e import build, close, identities, open_store
from clients import sessions


@pytest.fixture
def world(tmp_path):
    settings = build("demo", tmp_path)
    store, auth = open_store(settings)
    with store.transaction() as c:
        auth.sync_access(c)
        who = identities(c, auth)
    yield store, auth, who
    close(store)


def owner_of(c, auth):
    return auth.owner_identity(c)


def people(who):
    return sorted(a for a in who if a.startswith("human:"))


def made_threads(c, auth, who):
    """One stored-audience thread of each kind a person makes, as the owner, plus a bot's Home."""
    owner = threads.viewer(auth, owner_of(c, auth))
    other = next(a for a in people(who) if a != owner.actor)
    from backend import views
    groups = sorted((views.roster(c, shared=True).get("org_groups") or {}).keys())
    made = {
        "company": threads.create(c, owner, title="All hands", audience="company")["id"],
        "members": threads.create(c, owner, title="Launch", audience="members", members=[other])["id"],
        "personal": threads.create(c, owner, title="Notes", audience="personal")["id"],
    }
    if groups:
        made["team"] = threads.create(c, owner, title="Team", audience="team", audience_group_id=groups[0])["id"]
    bot = sorted(a for a in who if a.startswith("bot:"))[0]
    made["home"] = threads.home(c, bot, create=True)["id"]
    return made


# ----------------------------------------------------------------------------- C1: three renderings
def test_python_sql_and_sse_renderings_agree_for_every_teammate(world):
    store, auth, who = world
    with store.transaction() as c:
        made = made_threads(c, auth, who)
    with store.read_transaction() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM conversations WHERE COALESCE(room_key,'') NOT LIKE 'live-meeting:%'")]
        assert set(made.values()) <= set(ids)
        for actor, identity in who.items():
            v = threads.viewer(auth, identity)
            python = {i for i in ids if threads.readable(c, v, i)}
            marks = ",".join("?" * len(ids))
            sql = {r[0] for r in c.execute(f"SELECT id FROM conversations WHERE id IN ({marks}) AND "
                                           + threads.readable_sql(c, v, "conversations"), ids)}
            sse = threads.readable_ids(c, threads.viewer(auth, identity), ids)
            assert sql <= python, (actor, "SQL shows what Python hides", sorted(sql - python)[:5])
            assert sse == python, (actor, sorted(sse ^ python)[:5])
            # Every stored-audience thread agrees exactly (legacy rows keep main's own SQL/Python split).
            stored = set(made.values())
            assert sql & stored == python & stored, (actor, sorted((sql ^ python) & stored))


def test_a_members_thread_reaches_only_its_members_and_a_mention_grants_nothing(world):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        member, outsider = [a for a in people(who) if a != owner.actor][:2]
        tid = threads.create(c, owner, title="Pricing", audience="members", members=[member])["id"]
        name = H.human(c, H.actor_id(outsider))["name"] or H.actor_id(outsider)
        sent = threads.post(c, owner, tid, f"cc @{H.actor_id(outsider)} @{name}")
        assert {(w["rule"], w["actor"]) for w in sent.get("warnings") or []} == {("mention_outside", outsider)}
    with store.read_transaction() as c:
        assert threads.readable(c, threads.viewer(auth, who[member]), tid)
        assert not threads.readable(c, threads.viewer(auth, who[outsider]), tid)
        with pytest.raises(Problem) as hidden:
            threads.require(c, threads.viewer(auth, who[outsider]), tid)
        assert hidden.value.status == 404


# ----------------------------------------------------------------------------- the write door
def test_a_held_post_writes_no_message_and_no_delivery(world, monkeypatch):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        member = next(a for a in people(who) if a != owner.actor)
        tid = threads.create(c, owner, title="Held", audience="members", members=[member])["id"]
    seen = {}

    def apply(c, verdict, payload=None, replay=None, **_):
        seen["payload"], seen["replay"] = payload, replay
        return {"held": {"id": "hold-1", "rule": "No prices"}}

    monkeypatch.setattr(org_rule_check, "apply", apply)
    with store.transaction() as c:
        before = (c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                  c.execute("SELECT COUNT(*) FROM message_deliveries").fetchone()[0])
        answer = threads.post(c, owner, tid, "The price is $10", checked={"outcome": "broken"})
        after = (c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                 c.execute("SELECT COUNT(*) FROM message_deliveries").fetchone()[0])
    assert answer == {"held": {"id": "hold-1", "rule": "No prices"}}
    assert after == before
    assert seen["payload"] == "The price is $10"
    assert seen["replay"]["thread_id"] == tid and seen["replay"]["body"] == "The price is $10"


def test_a_held_first_message_makes_no_thread_sub_thread_or_dm(world, monkeypatch):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        member = next(a for a in people(who) if a != owner.actor)
        tid = threads.create(c, owner, title="Parent", audience="members", members=[member])["id"]
        parent = threads.post(c, owner, tid, "Start here")
    seen = []
    verdict = {"held": {"id": "hold-1", "rule": "No prices"}}
    monkeypatch.setattr(org_rule_check, "apply", lambda c, v, p=None, r=None, **_: seen.append(r) or verdict)

    def rows(c):
        return (c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    checked = {"outcome": "broken"}
    with store.transaction() as c:
        before = rows(c)
        assert threads.post(c, owner, tid, "$10", parent_message_id=parent["id"], checked=checked) == verdict
        assert threads.post(c, owner, None, "$10", checked=checked,
                            start={"title": "New", "audience": "members", "members": [member]}) == verdict
        assert threads.post(c, owner, None, "$10", checked=checked, start={"dm": [member]}) == verdict
        assert rows(c) == before, "a held first message makes nothing"
    assert seen[0]["thread_id"] == tid and seen[0]["parent_message_id"] == parent["id"]
    assert seen[1]["thread_id"] is None and seen[1]["start"]["title"] == "New"
    assert seen[2]["start"] == {"dm": [member]}
    # The replay (Send anyway, checked=None) makes the thread and writes the message in one go.
    with store.transaction() as c:
        sub = threads.post(c, owner, seen[0]["thread_id"], seen[0]["body"], parent_message_id=parent["id"])
        dm = threads.post(c, owner, None, seen[2]["body"], start=seen[2]["start"])
        again = threads.post(c, owner, None, "again", start={"dm": [member]})
        sub_row = threads.thread_row(c, sub["conversation_id"])
        assert sub_row["parent_message_id"] == parent["id"]
        assert threads.audience_of(threads.thread_row(c, dm["conversation_id"])) == "personal"
        assert again["conversation_id"] == dm["conversation_id"], "a DM start reuses the open DM"


def test_a_warned_post_writes_the_warned_text(world, monkeypatch):
    store, auth, who = world
    monkeypatch.setattr(org_rule_check, "apply", lambda c, v, p=None, r=None, **_: {"warn": "Warning: careful."})
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        tid = threads.create(c, owner, title="Warned", audience="personal")["id"]
        message = threads.post(c, owner, tid, "Hello", checked={"outcome": "broken"})
    assert message["body"].startswith("Warning: careful.") and message["body"].endswith("Hello")


def test_a_write_that_fails_mid_way_leaves_no_message_seq_or_delivery(world):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        member = next(a for a in people(who) if a != owner.actor)
        tid = threads.create(c, owner, title="Crash", audience="members", members=[member])["id"]

    def counts(c):
        return (c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM message_deliveries").fetchone()[0],
                c.execute("SELECT value FROM sequences WHERE name='messages'").fetchone()[0],
                c.execute("SELECT last_seq FROM conversations WHERE id=?", (tid,)).fetchone()[0])

    with store.read_transaction() as c:
        before = counts(c)
    with pytest.raises(RuntimeError):
        with store.transaction() as c:
            threads.post(c, owner, tid, f"@{H.actor_id(member)} look")
            raise RuntimeError("the process died after the insert")
    with store.read_transaction() as c:
        assert counts(c) == before


def test_two_sub_thread_starts_make_one_sub_thread(world, monkeypatch):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        tid = threads.create(c, owner, title="Race", audience="personal")["id"]
        parent = threads.post(c, owner, tid, "Start here")
        first = threads.sub_thread(c, owner, parent["id"])
        # The second start read "no sub-thread yet" before the first committed: its insert loses on the unique
        # parent_message_id index and it returns the winner's row.
        real = c.execute

        class Missing:
            def __init__(self, conn):
                self.conn, self.missed = conn, False

            def execute(self, sql, *args):
                if not self.missed and sql.startswith("SELECT id FROM conversations WHERE parent_message_id=?"):
                    self.missed = True
                    return real("SELECT NULL WHERE 0")
                return real(sql, *args)

            def __getattr__(self, name):
                return getattr(self.conn, name)

        second = threads.sub_thread(Missing(c), owner, parent["id"])
        assert second["id"] == first["id"]
        assert c.execute("SELECT COUNT(*) FROM conversations WHERE parent_message_id=?",
                         (parent["id"],)).fetchone()[0] == 1


# ----------------------------------------------------------------------------- C5: revisions
def test_revisions_apply_highest_first_and_a_delete_is_final(world):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        tid = threads.create(c, owner, title="Mirror", audience="personal")["id"]
        message = threads.post(c, owner, tid, "v0", via="peer",
                               origin={"origin_id": "0190-acme@peer-1", "revision": 0, "install": "peer-1"})
        again = threads.post(c, owner, tid, "v0", via="peer",
                             origin={"origin_id": "0190-acme@peer-1", "revision": 0, "install": "peer-1"})
        assert again.get("duplicate") and again["id"] == message["id"]
        origin = {"origin_id": message["origin_id"]}
        assert threads.apply_revision(c, {**origin, "revision": 2}, body="v2")
        assert threads.apply_revision(c, {**origin, "revision": 1}, body="v1") is None       # arrived late
        assert H.message(c, message["id"])["body"] == "v2"
        assert threads.apply_revision(c, {**origin, "revision": 3}, deleted=True)
        assert threads.apply_revision(c, {**origin, "revision": 4}, body="back") is None     # a delete is final
        row = H.message(c, message["id"], include_deleted=True)
        assert row["deleted_at"] and not row["body"]
        kept = [r[0] for r in c.execute("SELECT body FROM message_revisions WHERE message_id=?", (message["id"],))]
        assert kept and not any(kept), "a deleted message keeps no words in its history"


# ----------------------------------------------------------------------------- C3: loop guard
def test_the_loop_guard_defers_bot_wakes_and_lets_them_go(world, monkeypatch):
    store, auth, who = world
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    # Bot-to-bot messages wake (no `bot_to_bot` wait), so each would start a run.
    from backend import rules
    monkeypatch.setattr(rules, "bot_to_bot_decision", lambda *a, **k: None)
    monkeypatch.setattr(rules, "judge", lambda *a, **k: None)
    bots = sorted(a for a in who if a.startswith("bot:"))[:2]
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        tid = threads.create(c, owner, title="Ping pong", audience="members", members=bots)["id"]
        root = threads.thread_row(c, tid)["root_id"]
        for n in range(4):
            sender, target = bots[n % 2], bots[(n + 1) % 2]
            threads.post(c, sender, tid, f"turn {n}", to_actor=target)
        capped = c.execute("SELECT COUNT(*) FROM message_deliveries WHERE root_id=? AND state='capped'",
                           (root,)).fetchone()[0]
        assert capped >= 1, "past the guard a bot-to-bot wake waits"
        assert c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? AND body LIKE 'turn %'",
                         (tid,)).fetchone()[0] == 4, "the messages themselves are all written"
        attention = threads.sidebar(c, owner)["attention"]
        assert [t["capped"] for t in attention if t["id"] == root] == [capped], "the owner sees the held wakes"
        threads.resume_capped(c, root)
        assert c.execute("SELECT COUNT(*) FROM message_deliveries WHERE root_id=? AND state='capped'",
                         (root,)).fetchone()[0] == 0
        assert not [t for t in threads.sidebar(c, owner)["attention"] if t.get("capped")]


# ----------------------------------------------------------------------------- C3: inbox readers read deliveries
def test_the_inbox_reads_deliveries_not_to_actor(world):
    store, auth, who = world
    bot = sorted(a for a in who if a.startswith("bot:"))[0]
    with store.transaction() as c:
        owner = owner_of(c, auth)
        sent = H.say(c, owner.actor, bot, "Please check the numbers")
        assert sent["id"] in {m["id"] for m in H.undelivered(c, bot)}
        c.execute("DELETE FROM message_deliveries WHERE message_id=? AND recipient=?", (sent["id"], bot))
        assert sent["id"] not in {m["id"] for m in H.undelivered(c, bot)}, "no delivery, nothing to deliver"
        # A secondary recipient (a mention) acks only its own delivery.
        tid = threads.create(c, threads.viewer(auth, owner), title="Mentions", audience="company")["id"]
        reader = next(a for a in people(who) if a != owner.actor)
        posted = threads.post(c, threads.viewer(auth, owner), tid, f"@{H.actor_id(reader)} see this")
        row = c.execute("SELECT state FROM message_deliveries WHERE message_id=? AND recipient=?",
                        (posted["id"], reader)).fetchone()
        assert row is not None
        H.mark_read(c, reader, posted["id"], recipient=reader)
        assert c.execute("SELECT state FROM message_deliveries WHERE message_id=? AND recipient=?",
                         (posted["id"], reader)).fetchone()[0] == "read"


# ----------------------------------------------------------------------------- reactions
def test_reactions_need_read_access_and_are_one_per_emoji(world):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        member, outsider = [a for a in people(who) if a != owner.actor][:2]
        tid = threads.create(c, owner, title="React", audience="members", members=[member])["id"]
        message = threads.post(c, owner, tid, "Shipped")
        mine = threads.viewer(auth, who[member])
        threads.react(c, mine, message["id"], ":tada:")
        assert threads.react(c, mine, message["id"], ":tada:") == [
            {"emoji": ":tada:", "count": 1, "actors": [member]}]
        with pytest.raises(Problem) as hidden:
            threads.react(c, threads.viewer(auth, who[outsider]), message["id"], ":tada:")
        assert hidden.value.status == 404
        assert threads.unreact(c, mine, message["id"], ":tada:") == []
        assert threads.timeline(c, owner, tid)["messages"][-1].get("reactions") is None


# ----------------------------------------------------------------------------- runs reply in their sub-thread
def test_a_runs_reply_goes_to_the_sub_thread_of_its_parent_and_never_across_trees(world):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        tid = threads.create(c, owner, title="Run", audience="personal")["id"]
        parent = threads.post(c, owner, tid, "Kick off")
        sub = threads.sub_thread(c, owner, parent["id"])
        assert threads.reply_destination(c, "bot:x", threads.thread_row(c, tid), parent["id"]) == sub["id"]
        assert threads.reply_destination(c, "bot:x", sub, parent["id"]) == sub["id"]
        other = threads.create(c, owner, title="Other", audience="personal")["id"]
        assert threads.reply_destination(c, "bot:x", threads.thread_row(c, other), parent["id"]) == other
        assert threads.reply_destination(c, "bot:x", threads.thread_row(c, tid), None) == tid


# ----------------------------------------------------------------------------- sessions (contract section 8)
def test_session_keys_never_share_a_thread_across_audience_classes():
    cfg = {}
    assert sessions.session_key(cfg, "ops", None, "sub", "r1", "public") == "root:r1:public"
    assert sessions.session_key(cfg, "ops", None, "sub", "r1", "private") == "root:r1:private"
    assert sessions.session_key(cfg, "ops", "t1", "sub", "r1", "private") == "task:t1"
    on_bot = {"session": "bot"}
    assert sessions.session_key(on_bot, "ops", None, "c", "r1", "public") == sessions.PUBLIC_THREAD
    assert sessions.session_key(on_bot, "ops", None, "c", "r1", "private") == "root:r1:private"
    assert sessions.session_key(on_bot, "ops", "t1", "c", "r1", "public") == sessions.BOT_THREAD
    # An older server (no root) keys as before.
    assert sessions.session_key({"session": "task"}, "ops", None, "c1") == "conversation:c1"
    assert sessions.session_key(on_bot, "ops", None, "c1") == sessions.BOT_THREAD
    assert sessions.adopted_key("root:r1:private") == "conversation:r1"
    assert sessions.adopted_key(sessions.PUBLIC_THREAD) is None
    assert sessions.lane(cfg, "ops", None, "c", "r1", "private") == "main"


# ----------------------------------------------------------------------------- migration: crafted per-message cases
from backend.tests.test_api import post as api_post  # noqa: E402
from backend.tests.test_live_meetings import api, live  # noqa: E402,F401  (fixtures)

UNSTAMPED = ("UPDATE messages SET seq=NULL, origin_id=NULL, revision=0, restricted=1, legacy_audience=NULL",
             "UPDATE conversations SET root_id=id, path='/'||id||'/', last_seq=0, last_unread_seq=0",
             "DELETE FROM message_deliveries", "DELETE FROM registry_metadata WHERE key='threads-migration'")


def message_readers(c, auth, ids):
    viewers = {a: threads.viewer(auth, w) for a, w in identities(c, auth).items()}
    out = {}
    for mid in ids:
        row = dict(c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone())
        out[mid] = {a for a, v in viewers.items() if threads.readable(c, v, row)}
    return out


def test_migration_keeps_each_crafted_messages_readers_and_a_roster_change_only_narrows(live):
    """A private task's reference, a meeting's provenance and a shared room's history: per message, the same readers
    before the migration (columns unset, restricted: the full pre-threads check) and after it, and a removal only narrows."""
    store, auth = live.app.state.store, live.app.state.auth
    meeting = api_post(live, "live-meetings", {"title": "Synthetic restricted planning", "visibility": "private"})["id"]
    with store.transaction() as c:
        secret = H.task_create(c, "human:ana", "Acme acquisition", "", "bot:finance", private=True, lint=False)
        direct = H.open_conversation(c, "human:ana", ["human:ana", "human:ben", "bot:ops"], kind="chat",
                                     subject="Plans")
        plain = H.say(c, "human:ana", "bot:ops", "Lunch at noon", conversation_id=direct["id"])
        task_ref = H.say(c, "human:ana", "bot:ops", "See the acquisition", conversation_id=direct["id"],
                         refs={"task": secret["id"]})
        from backend.store import encode
        provenance = H.say(c, "human:ana", "bot:ops", "From the meeting", conversation_id=direct["id"])
        c.execute("UPDATE messages SET refs_json=? WHERE id=?",
                  (encode({"live_meeting": {"meeting_id": meeting}}), provenance["id"]))
        shared = H.open_conversation(c, "human:ben", ["human:ben", "bot:cpo"], kind="chat", subject="CPO room",
                                     scope="shared", room_key="cpo", allow_held=True)
        in_room = H.say(c, "human:ben", "bot:cpo", "Roadmap draft", conversation_id=shared["id"])
        ids = [plain["id"], task_ref["id"], provenance["id"], in_room["id"]]
        for statement in UNSTAMPED:
            c.execute(statement)
    with store.read_transaction() as c:
        before = message_readers(c, auth, ids)
    assert "human:cara" not in before[task_ref["id"]] and "human:ben" in before[plain["id"]]
    with store.transaction() as c:
        threads.migrate(c)
        stamped = {r["id"]: dict(r) for r in c.execute(
            "SELECT id, seq, origin_id, restricted, legacy_audience FROM messages WHERE id IN (?,?,?,?)", ids)}
    assert all(r["seq"] and r["origin_id"] for r in stamped.values())
    assert stamped[task_ref["id"]]["restricted"] == 1 and stamped[in_room["id"]]["legacy_audience"] == "shared:cpo"
    with store.read_transaction() as c:
        home = threads.home(c, "bot:cpo", create=False)
        assert home["audience"] == "home" and home["owner_actor"] == "bot:cpo"
    with store.read_transaction() as c:
        after = message_readers(c, auth, ids)
    assert after == before
    api_post(live, "people/cara", {"left": True})
    with store.read_transaction() as c:
        removed = message_readers(c, auth, ids)
    assert all(removed[m] <= after[m] for m in ids)
    assert all("human:cara" not in removed[m] for m in ids)


def test_a_migrated_home_shows_old_readers_only_its_history_and_home_readers_only_whats_new(world, monkeypatch):
    """Spec section 4: the shared room is its bot's Home. Its history keeps exactly its old readers until a manager
    opens it; everything new follows Home Read. Pinned in Python, in SQL and in the legacy per-message check."""
    from backend import task_privacy
    store, auth, who = world
    with store.transaction() as c:
        homeless = [a for a in sorted(who) if a.startswith("bot:") and threads.home(c, a, create=False) is None]
        bot = homeless[0]
        slug = H.actor_id(bot)
        old_reader, home_reader = people(who)[:2]
        room = H.open_conversation(c, old_reader, [old_reader, bot], kind="chat", subject="Shared room",
                                   scope="shared", room_key=slug, allow_held=True)
        old = H.say(c, old_reader, bot, "Old roadmap", conversation_id=room["id"])
        # What the migration does to a shared room: its history keeps its readers, the room becomes the Home.
        c.execute("UPDATE messages SET legacy_audience=?, restricted=1 WHERE id=?", ("shared:" + slug, old["id"]))
        threads._store_shared_homes(c, slug)
        assert threads.home(c, bot, create=False)["id"] == room["id"]
        new = threads.post(c, bot, room["id"], "New plan")
    real_home = threads.Viewer.can_read_home
    monkeypatch.setattr(threads.Viewer, "shared_slugs",
                        lambda self, c, slugs: {x for x in slugs if x == slug and self.actor == old_reader})
    monkeypatch.setattr(threads.Viewer, "can_read_home", lambda self, c, owner: (
        self.actor == home_reader if owner == bot else real_home(self, c, owner)))

    def sees(c, actor):
        v = threads.viewer(auth, who[actor])
        python = {m for m in (old["id"], new["id"]) if threads.readable(c, v, m)}
        sql = {r[0] for r in c.execute(
            "SELECT id FROM messages WHERE conversation_id=? AND " + threads.message_sql(c, v, "messages") + " AND "
            "conversation_id IN (SELECT id FROM conversations WHERE " + threads.readable_sql(c, v, "conversations")
            + ")", (room["id"],))}
        legacy = {m for m in (old["id"], new["id"]) if task_privacy.message_readable(c, actor, H.message(c, m))}
        return python, sql, legacy, threads.readable(c, v, room["id"])

    with store.read_transaction() as c:
        assert sees(c, old_reader) == ({old["id"]}, {old["id"]}, {old["id"]}, True)
        listed = threads.timeline(c, threads.viewer(auth, who[old_reader]), room["id"])
        assert [m["id"] for m in listed["messages"]] == [old["id"]], "an old reader never sees what is new"
        assert listed["thread"]["last_seq"] == old["seq"] and listed["thread"]["me"]["unread"] == 0
        python, sql, legacy, opens = sees(c, home_reader)
        assert (python, sql, opens) == ({new["id"]}, {new["id"]}, True)
        assert old["id"] not in legacy
    with store.transaction() as c:
        threads.open_earlier_messages(c, H.KEEPER, slug)
    with store.read_transaction() as c:
        python, sql, _legacy, opens = sees(c, home_reader)
        assert python == sql == {old["id"], new["id"]} and opens
        assert sees(c, old_reader)[3] is False, "once opened, the Home is read by its Read audience alone"


def test_outside_members_come_off_only_by_a_manager_and_their_unsent_copies_go_moot(world):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        member = next(a for a in people(who) if a != owner.actor)
        tid = threads.create(c, owner, title="Bridge", audience="members", members=[member])["id"]
        threads.add_outside_delivery(c, tid, "slack:C123")
        sent = threads.post(c, owner, tid, "Out to Slack")
        assert c.execute("SELECT state FROM message_deliveries WHERE message_id=? AND recipient='slack:C123'",
                         (sent["id"],)).fetchone()[0] == "pending"
        plain = threads.viewer(auth, who[member])
        if not plain.admin(c):
            with pytest.raises(Problem) as refused:
                threads.remove_outside_member(c, plain, tid, "slack:C123")
            assert refused.value.status == 403
        assert threads.remove_outside_member(c, owner, tid, "slack:C123")["removed"] is True
        assert threads.remove_outside_member(c, owner, tid, "slack:C123")["removed"] is False
        assert c.execute("SELECT state FROM message_deliveries WHERE message_id=? AND recipient='slack:C123'",
                         (sent["id"],)).fetchone()[0] == "moot"


def test_a_sub_thread_is_titled_by_its_parents_first_line_and_keeps_it(world):
    store, auth, who = world
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        tid = threads.create(c, owner, title="Titles", audience="personal")["id"]
        parent = threads.post(c, owner, tid, "**Budget** for [Q3](http://acme.example) is ready\nmore detail")
        sub = threads.sub_thread(c, owner, parent["id"])
        assert sub["subject"] == "Budget for Q3 is ready"
        threads.edit_message(c, owner, parent["id"], "Changed")
        assert threads.thread_row(c, sub["id"])["subject"] == "Budget for Q3 is ready"
        threads.post(c, owner, sub["id"], "A reply")
        assert {m["depth"] for m in threads.timeline(c, owner, tid)["messages"]} == {0}
        assert {m["depth"] for m in threads.timeline(c, owner, sub["id"])["messages"]} == {1}
    assert threads.sub_thread_title("word " * 30).endswith("…") and len(threads.sub_thread_title("word " * 30)) <= 61


# ----------------------------------------------------------------------------- federation: remote authors are guests
def test_a_remote_author_writes_only_through_the_keeper_in_its_installs_namespace_and_only_where_linked(world):
    store, auth, who = world
    origin = {"origin_id": "0190aaaa-0000-7000-8000-000000000001", "revision": 0, "install": "peer.acme.example"}
    with store.transaction() as c:
        owner = threads.viewer(auth, owner_of(c, auth))
        linked = threads.create(c, owner, title="Partner", audience="members", members=[])["id"]
        other = threads.create(c, owner, title="Inside", audience="members", members=[])["id"]
        threads.add_member(c, owner, linked, "peer:peer.acme.example")
        with pytest.raises(Problem) as owner_role:
            threads.add_member(c, owner, linked, "human:ana@peer.acme.example", role="owner")
        assert owner_role.value.status == 422, "a remote member is never an owner"
        for principal, actor, via in ((owner, "human:ana@peer.acme.example", "peer"),
                                      (H.KEEPER, "human:ana@peer.acme.example", None)):
            with pytest.raises(Problem) as refused:
                threads.post(c, principal, linked, "hi", as_actor=actor, via=via, origin=origin)
            assert refused.value.status == 403
        with pytest.raises(Problem) as spoofed:
            threads.post(c, H.KEEPER, linked, "hi", as_actor="human:ana@other.acme.example", via="peer", origin=origin)
        assert spoofed.value.status == 422, "a peer writes only in its own namespace"
        with pytest.raises(Problem) as unlinked:
            threads.post(c, H.KEEPER, other, "hi", as_actor="human:ana@peer.acme.example", via="peer", origin=origin)
        assert unlinked.value.status == 404, "C1 for a remote author is membership"
        sent = threads.post(c, H.KEEPER, linked, "Hello from the partner", as_actor="human:ana@peer.acme.example",
                            via="peer", origin=origin)
        assert sent["from_actor"] == "human:ana@peer.acme.example"
        again = threads.post(c, H.KEEPER, linked, "Hello from the partner", as_actor="human:ana@peer.acme.example",
                             via="peer", origin=origin)
        assert again["id"] == sent["id"], "a repeat (origin_id, revision) writes nothing"
        assert not c.execute("SELECT 1 FROM thread_followers WHERE actor=?", ("human:ana@peer.acme.example",)).fetchone()
