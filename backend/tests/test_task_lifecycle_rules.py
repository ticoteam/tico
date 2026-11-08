"""Closing, reopening, privacy and ownership rules on a task: who may end it, which times it keeps, who it reaches."""
import json
import tempfile
import unittest
from pathlib import Path

from backend import hubdb as H
from backend import task_relations as TR

EMPLOYEES = {
    "cmo": {"display_name": "CMO", "status": "active"},
    "seo": {"display_name": "SEO", "status": "active"},
    "legal": {"display_name": "Legal", "status": "active"},
}
PEOPLE = {"people": [
    {"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "team": "leadership"},
    {"id": "ben", "name": "Ben", "email": "ben@acme.example", "team": "product"},
    {"id": "cara", "name": "Cara", "email": "cara@acme.example", "team": "sales"}]}

ANA, BEN, CARA = H.human_actor("ana"), H.human_actor("ben"), H.human_actor("cara")
CMO, SEO, LEGAL = H.bot_actor("cmo"), H.bot_actor("seo"), H.bot_actor("legal")


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.conn = H.connect(Path(self.dir.name) / "hub.db")
        self.addCleanup(self.conn.close)
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)

    def make(self, actor, title, owner, **kw):
        return H.task_create(self.conn, actor, title, "Do the work described here.", owner, lint=False, **kw)

    def refused(self, rule, fn, *a, **kw):
        with self.assertRaises(H.Refused) as got:
            fn(*a, **kw)
        self.assertEqual(got.exception.rule, rule, got.exception.detail)
        return got.exception

    def wakes(self, target, task_id, text):
        return [r for r in self.conn.execute(
            "SELECT body, refs FROM messages WHERE recipient_id=? AND json_extract(refs,'$.task')=? "
            "AND body LIKE ?", (target, task_id, f"%{text}%"))]


class LeavingClosed(Case):
    def test_done_after_closed_drops_the_close_time_so_the_close_rule_sees_it_again(self):
        t = self.make(ANA, "Draft the launch post", CMO)
        H.task_close(self.conn, ANA, t["id"], note="Not now")
        H.task_update(self.conn, ANA, t["id"], status="done", note="It went out after all")
        row = H.task(self.conn, t["id"])
        self.assertEqual((row["status"], row["closed_at"], row["closed_by_id"]), ("done", None, None))
        self.assertIsNotNone(row["done_at"])
        self.assertEqual({f for (f,) in self.conn.execute(
            "SELECT field FROM task_events WHERE task_id=? AND new IS NULL", (t["id"],))}, {"closed_at", "closed_by"})

    def test_a_bot_refinishing_its_own_closed_task_drops_the_close_time(self):
        t = self.make(CMO, "Check the launch post links", CMO)
        H.task_close(self.conn, CMO, t["id"])
        H.task_update(self.conn, CMO, t["id"], status="done")
        self.assertIsNone(H.task(self.conn, t["id"])["closed_at"])

    def test_declined_after_closed_or_done_keeps_neither_time(self):
        closed = self.make(ANA, "Draft the partner post", CMO)
        H.task_close(self.conn, ANA, closed["id"])
        H.task_update(self.conn, ANA, closed["id"], status="declined")
        done = self.make(ANA, "Draft the pricing post", CMO)
        H.task_update(self.conn, CMO, done["id"], status="done")
        H.task_update(self.conn, ANA, done["id"], status="declined")
        for tid in (closed["id"], done["id"]):
            row = H.task(self.conn, tid)
            self.assertEqual((row["status"], row["done_at"], row["closed_at"], row["closed_by_id"]),
                             ("declined", None, None, None))

    def test_closing_a_done_task_keeps_when_it_was_done(self):
        t = self.make(ANA, "Draft the hiring post", CMO)
        H.task_update(self.conn, CMO, t["id"], status="done")
        done_at = H.task(self.conn, t["id"])["done_at"]
        H.task_close(self.conn, ANA, t["id"])
        row = H.task(self.conn, t["id"])
        self.assertEqual((row["status"], row["done_at"], row["closed_by_id"]), ("closed", done_at, ANA))
        self.assertIsNotNone(done_at)

    def test_start_clears_close_times_left_on_finished_tasks_once(self):
        stuck = self.make(ANA, "Draft the hiring post", CMO)
        kept = self.make(ANA, "Draft the events post", CMO)
        H.task_update(self.conn, CMO, stuck["id"], status="done")
        H.task_close(self.conn, ANA, kept["id"])
        self.conn.execute("UPDATE tasks SET closed_at='2026-10-04T16:17:20Z', closed_by_id=? WHERE id=?",
                          (ANA, stuck["id"]))      # as an old move out of closed left it
        self.assertEqual(H.clear_finished_close_times(self.conn), [stuck["id"]])
        self.assertEqual((H.task(self.conn, stuck["id"])["closed_at"], H.task(self.conn, stuck["id"])["closed_by_id"]),
                         (None, None))
        self.assertIsNotNone(H.task(self.conn, kept["id"])["closed_at"], "a closed task keeps its close time")
        self.assertEqual(H.clear_finished_close_times(self.conn), [])
        self.assertEqual(len(self.conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND actor=? AND field "
                                               "IN ('closed_at','closed_by')", (stuck["id"], H.KEEPER)).fetchall()), 2)


class ClosingNeedsEdit(Case):
    def test_a_person_who_can_only_read_a_task_cannot_close_it(self):
        t = self.make(ANA, "Draft the launch post", CMO)
        self.refused("identity", H.task_update, self.conn, CARA, t["id"], status="declined")
        self.refused("close", H.task_close, self.conn, CARA, t["id"], note="Not needed")
        self.assertEqual(H.task(self.conn, t["id"])["status"], "open")

    def test_requester_owner_mover_and_a_party_above_close(self):
        for closer in (ANA, BEN):           # the requester; a person on a mover team
            t = self.make(ANA, f"Draft the post for {closer}", CMO)
            self.assertEqual(H.task_close(self.conn, closer, t["id"])["status"], "closed")
        mine = self.make(ANA, "Review the launch plan", CARA)
        self.assertEqual(H.task_close(self.conn, CARA, mine["id"])["status"], "closed")     # its owner
        parent = self.make(CARA, "Plan the sales launch", CMO)
        child = self.make(CMO, "Draft the sales email", SEO, parent_id=parent["id"])
        self.assertEqual(H.task_close(self.conn, CARA, child["id"])["status"], "closed")    # requester of the parent

    def test_the_person_a_task_waits_on_closes_it(self):
        """A bot's question waits on a person; "not now" is theirs to say even when they can't edit the task."""
        t = self.make(CMO, "Approve the launch budget?", CMO)
        self.refused("close", H.task_close, self.conn, CARA, t["id"])
        # As a bot's question to her leaves it (hub task question): waiting on her, the bot still its owner.
        self.conn.execute("UPDATE tasks SET status='waiting', waiting_on=? WHERE id=?", (CARA, t["id"]))
        self.assertEqual(H.task_close(self.conn, CARA, t["id"], note="Not now")["status"], "closed")

    def test_the_caller_says_when_a_person_moves_tasks(self):
        t = self.make(ANA, "Draft the launch post", CMO)
        self.assertEqual(H.task_close(self.conn, CARA, t["id"], mover=True)["status"], "closed")


class PrivateParent(Case):
    def test_making_a_parent_private_takes_every_task_below_it(self):
        parent = self.make(ANA, "Plan the board pack", CMO)
        child = self.make(CMO, "Collect the revenue numbers", SEO, parent_id=parent["id"])
        grandchild = self.make(SEO, "Export the revenue table", LEGAL, parent_id=child["id"])
        H.task_update(self.conn, ANA, parent["id"], private=True)
        for tid in (child["id"], grandchild["id"]):
            row = H.task(self.conn, tid)
            self.assertTrue(H.task_private(self.conn, row))
            self.assertEqual(row["visibility"], "private")
            self.assertFalse(H.task_private_readable(self.conn, BEN, row))

    def test_attaching_under_a_private_parent_takes_the_subtasks_too(self):
        parent = self.make(ANA, "Plan the board pack", CMO, private=True)
        child = self.make(ANA, "Collect the revenue numbers", SEO)
        grandchild = self.make(SEO, "Export the revenue table", LEGAL, parent_id=child["id"])
        TR.set_parent(self.conn, ANA, child["id"], parent["id"])
        self.assertTrue(H.task_private(self.conn, H.task(self.conn, grandchild["id"])))

    def test_the_bot_owning_a_private_parent_hears_its_subtasks_finished(self):
        parent = self.make(ANA, "Plan the board pack", CMO, private=True)
        child = self.make(CMO, "Collect the revenue numbers", SEO, parent_id=parent["id"])
        self.assertTrue(H.task_private(self.conn, H.task(self.conn, child["id"])))
        H.task_update(self.conn, SEO, child["id"], status="done", note="Numbers attached")
        told = self.wakes(CMO, parent["id"], "All subtasks done")
        self.assertEqual(len(told), 1)
        self.assertEqual(json.loads(told[0][1]).get("trigger_source"), child["id"])

    def test_a_private_parent_left_quiet_is_a_stalled_task_for_its_bot_only(self):
        parent = self.make(ANA, "Plan the board pack", CMO, private=True)
        later = H.shift(H.now(), hours=3)
        self.assertIn(parent["id"], [r["id"] for r in H.stalled_tasks(self.conn, at=later)])
        self.assertIn("cannot read a private task", H._no_diagnosis(self.conn, H.task(self.conn, parent["id"]), 4))


class Renaming(Case):
    def test_renaming_to_a_finished_tasks_title_is_allowed_as_creating_one_is(self):
        old = self.make(ANA, "Draft the launch post", CMO)
        H.task_update(self.conn, CMO, old["id"], status="done")
        new = self.make(ANA, "Draft the post", CMO)
        H.task_update(self.conn, ANA, new["id"], title="Draft the launch post")
        self.assertEqual(H.task(self.conn, new["id"])["title"], "Draft the launch post")
        live = self.make(ANA, "Draft the pricing post", CMO)
        self.refused("duplicate", H.task_update, self.conn, ANA, new["id"], title=live["title"])


class Ownership(Case):
    def test_the_same_owner_in_another_form_is_no_reassignment(self):
        t = self.make(ANA, "Draft the launch post", CMO)
        before = self.conn.execute("SELECT count(*) FROM messages WHERE recipient_id=?", (CMO,)).fetchone()[0]
        H.task_update(self.conn, BEN, t["id"], owner="cmo")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM messages WHERE recipient_id=?", (CMO,)).fetchone()[0],
                         before)
        self.assertFalse(self.conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='owner'",
                                           (t["id"],)).fetchone())

    def test_a_bot_cannot_hand_on_a_task_a_person_closed(self):
        t = self.make(CMO, "Draft the launch post", SEO)
        H.task_close(self.conn, ANA, t["id"], note="Not needed")
        self.refused("close", H.task_update, self.conn, CMO, t["id"], owner="legal")
        self.assertEqual(H.task(self.conn, t["id"])["owner_id"], SEO)
        self.assertEqual(self.wakes(LEGAL, t["id"], "Open:"), [])
        own = self.make(CMO, "Draft the pricing post", SEO)
        H.task_close(self.conn, CMO, own["id"])
        self.assertEqual(H.task_update(self.conn, CMO, own["id"], owner="legal")["owner_id"], LEGAL)
