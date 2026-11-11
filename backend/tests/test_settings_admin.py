"""Archiving a bot (backend/settings_admin.py archive_bot): the open tasks it owns are reassigned to its successor the
way any reassign is made, and the tasks it asked for name the successor as their requester."""

from backend import settings_admin as SA
from backend.store import H
from backend.tests.helpers import engine as X

ANA = "human:ana"


def test_archiving_a_bot_gives_its_open_tasks_to_its_successor_through_the_engine(company):
    with company.transaction() as c:
        X.bot(c, "builder")
        X.bot(c, "helper")
        owned = H.task_create(c, ANA, "Write the launch note", "Draft it.", "bot:builder", lint=False)["id"]
        asked = H.task_create(c, "bot:builder", "Gather the numbers", "Collect them.", "bot:assistant", lint=False)[
            "id"
        ]
        finished = H.task_create(c, ANA, "Old note", "Done already.", "bot:builder", lint=False)["id"]
        H.task_close(c, ANA, finished, "Not needed.")
        before = H.task(c, owned)["version"]

        SA.archive_bot(c, ANA, "builder", successor="helper", revoke_agent=False)

        moved = H.task(c, owned)
        assert (moved["owner_id"], moved["status"], moved["version"]) == ("bot:helper", "open", before + 1)
        kinds = c.execute(
            "SELECT kind, note FROM history_events WHERE subject_type='task' AND subject_id=? ORDER BY rowid", (owned,)
        ).fetchall()
        assert any("was removed" in str(note or "") for _kind, note in kinds)
        assert X.wakes(c, owned, "bot:helper")  # the new owner is woken by the reassign
        assert H.task(c, asked)["requester_id"] == "bot:helper"
        assert H.task(c, finished)["owner_id"] == "bot:builder"  # an ended task keeps its owner
