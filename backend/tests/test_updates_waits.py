"""What an update request and a person's draft say waits on someone (backend/updates.py): the tasks' real waits
(H.task_waits), on shared tasks only, and a person's answers from the engine's answers."""

import pytest

from backend import updates
from backend.store import H

ANA, BEN = "human:ana", "human:ben"
DAY = "2026-11-10"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def section(text, heading):
    """The lines under `heading` in a request or a draft's facts, up to the next heading."""
    lines = text.split("\n")
    start = lines.index(heading) + 1
    out = []
    for line in lines[start:]:
        if not line.startswith("- "):
            break
        out.append(line[2:])
    return out


def test_a_bots_task_with_an_open_ask_to_a_person_waits_on_that_person(c):
    asked = H.task_create(c, ANA, "Renew the Acme domain", "Please.", "bot:ops", lint=False)
    H.task_ask(c, "bot:ops", asked["id"], BEN, "Which card pays for the Acme domain?", seen_version=asked["version"])
    H.task_create(c, ANA, "Plain work", "Please.", "bot:ops", lint=False)
    private = H.task_create(c, ANA, "Private renewal", "Please.", "bot:ops", lint=False, private=True)
    H.task_ask(c, "bot:ops", private["id"], ANA, "Secret question?", seen_version=H.task(c, private["id"])["version"])
    waiting = section(updates.request_text(c, "ops", "daily", DAY), "Waiting on a person:")
    assert len(waiting) == 1 and waiting[0].startswith("Renew the Acme domain (")
    assert H.actor_name(c, BEN) in waiting[0]


def test_a_persons_draft_lists_what_waits_on_someone_else(c):
    mine = H.task_create(c, ANA, "Book the venue", "Please.", ANA, lint=False)
    H.task_ask(c, ANA, mine["id"], BEN, "Is the 12th free for you?")
    given = H.task_create(c, ANA, "Draft the brief", "Please.", "bot:ops", lint=False)
    H.task_create(c, ANA, "My own work", "Please.", ANA, lint=False)
    facts = updates.draft_facts(c, ANA, "daily", DAY)[0]
    waiting = section(facts, "Waiting on someone else:")
    assert sorted(w.split(" (")[0] for w in waiting) == ["Book the venue", "Draft the brief"], waiting
    assert given["id"]
