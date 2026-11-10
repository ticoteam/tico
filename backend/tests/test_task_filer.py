"""A task filed on a person's rights by someone else shows who really filed it (`filer`, backend/sources.py): BotOps
working as the person, or a built-in such as Health check. The requester, whose rights the task carries, stays the
person."""

from backend import sources as SRC
from backend.maintenance_steps import check_health
from backend.store import H
from backend.tests.helpers.api import get, headers  # noqa: F401
from backend.tests.helpers.member_bots import botops  # noqa: F401  (fixture)


def _file(api, actor, title, owner="bot:ops", via=""):
    """`actor` files a task, as BotOps or the Assistant does when it works with that person's rights (H.VIA)."""
    token = H.VIA.set(via)
    try:
        with api.app.state.store.transaction() as c:
            return H.task_create(c, actor, title, "Do the thing.", owner, lint=False)["id"]
    finally:
        H.VIA.reset(token)


def _views(api, tid):
    one = get(api, "tasks/" + tid)["task"]
    listed = next(t for t in get(api, "tasks")["tasks"] if t["id"] == tid)
    return one, listed


def test_a_task_botops_files_on_a_persons_rights_shows_botops(api):
    tid = _file(api, "human:ana", "Check the shared access", via="botops")
    for view in _views(api, tid):
        assert (view["requester_id"], view["filer"]) == ("human:ana", "bot:botops")


def test_a_task_a_person_or_their_assistant_files_shows_no_other_source(api):
    for via, title in (("", "Read the contract"), ("assistant", "Book the venue")):
        tid = _file(api, "human:ana", title, via=via)
        for view in _views(api, tid):
            assert view["requester_id"] == "human:ana" and "filer" not in view, (via, view)


def test_a_health_check_task_shows_health_check(api, botops):
    with api.app.state.store.transaction() as c:
        made = check_health._task_create(c, "Watchers", "Fix the watcher.")
    assert made["requester_id"] == H.KEEPER
    for view in _views(api, made["id"]):
        assert view["filer"] == "check-health"


def test_only_a_keeper_task_names_a_built_in(api):
    """A person's task filed inside a built-in's scope is still theirs: only the server's own write names one."""
    with SRC.writing_as("check-health"):
        tid = _file(api, "human:ana", "Look at the logs")
    with api.app.state.store.read() as c:
        assert SRC.filers(c, [tid]) == {}
