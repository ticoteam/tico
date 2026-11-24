"""One rule for who reads a task. The list read (Auth.task_sql, the rows a list returns) and the point reads
(Auth.task_row, a task's page; task_privacy.task_readable, the shared rule hubdb.task_private_readable that wakes,
mentions and privacy_index.ReadIndex use; and that rule called directly with whom the request reads as, as batch.py
does) must agree for every reader and every task.

The fixture covers every way a task is read today: its owner, requester and person assignee; a bot party's org-chart
manager; a meeting participant; `readers`, by name and through a group; a call follow-up's thread; a Rules Owner page
copy, readable and denied; a relationship run; the shared Assistant in each scope (a person's own chat, its routine turn,
a person gone, nobody); a filed OK the person's own Assistant reads; a stage-held bot; a pipeline's manager; public tasks,
and public tasks whose bot party (owner or requester) a reader may not read, with that task's person assignee and a
person who reads past the fence acting through one who does not.

KNOWN holds the one kind of disagreement left: the shared rule does not fence a public task whose bot party the reader
may not read (Auth.task_row and task_sql's `clear` clause do). The stored reader list (step 2) closes it. Any other
disagreement fails the test, as does a known one that goes away."""

import pytest

from backend import relationship_runs as runs
from backend import rooms, task_privacy
from backend.auth import Identity
from backend.entities import store as entities
from backend.store import H, Problem
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post, restrict, unrelated_task_managers
from backend.tests.helpers.counsel import counsel_on, source
from backend.tests.helpers.meeting_items import add, push, record
from backend.tests.helpers.page_audience import edit, promoted
from backend.tests.threads_e2e import add_member, create_thread, ok

# The shared Assistant's runs, by the scope rooms.run_scope gives each (patched below: the scope is the input here).
RUN_SCOPES = {
    "run-person": ("person", "human:cara"),
    "run-ana": ("person", "human:ana"),
    "run-task": ("task", "turn"),
    "run-task-copy": ("task", "copy"),
    "run-gone": ("gone", ""),
    "run-nobody": ("", ""),
}

# Where the reads disagree: (reader, task) -> {read: answer}.
# Shared rule doesn't fence public tasks; closed by the stored reader list (step 2). For each public task behind the
# bot-activity fence, the readers the list and the page refuse and hubdb.task_private_readable admits.
FENCED = {
    "managed_open": (
        "assistant",
        "cara",
        "cara-chat",
        "cara-chat-nobody",
        "cara-chat-task",
        "cara-via-assistant",
        "cara-via-cpo",
        "cpo",
        "finance",
        "ops",
        "ops-for-ben",
        "product-design",
        "rules-owner",
        "run-ana",
        "run-nobody",
        "run-person",
        "run-task",
        "run-task-copy",
        "zed",
    ),
    "public_finance": (
        "assistant",
        "cara",
        "cara-chat",
        "cara-chat-nobody",
        "cara-chat-task",
        "cara-via-assistant",
        "cara-via-cpo",
        "inbox",
        "ops",
        "ops-for-ben",
        "product-design",
        "rules-owner",
        "run-ana",
        "run-nobody",
        "run-person",
        "run-task",
        "run-task-copy",
        "zed",
    ),
    "public_finance_assigned": (
        "assistant",
        "inbox",
        "ops",
        "ops-for-ben",
        "product-design",
        "rules-owner",
        "run-ana",
        "run-nobody",
        "run-person",
        "run-task",
        "run-task-copy",
        "zed",
    ),
    "public_inbox": (
        "assistant",
        "cara",
        "cara-chat",
        "cara-chat-nobody",
        "cara-chat-task",
        "cara-via-assistant",
        "cara-via-cpo",
        "cpo",
        "finance",
        "ops",
        "ops-for-ben",
        "product-design",
        "rules-owner",
        "run-ana",
        "run-nobody",
        "run-person",
        "run-task",
        "run-task-copy",
        "zed",
    ),
    "public_inbox_asked": (
        "assistant",
        "cara",
        "cara-chat",
        "cara-chat-nobody",
        "cara-chat-task",
        "cara-via-assistant",
        "cara-via-cpo",
        "cpo",
        "finance",
        "product-design",
        "rules-owner",
        "run-ana",
        "run-nobody",
        "run-person",
        "run-task",
        "run-task-copy",
        "zed",
    ),
}
# A computer's (runner) or another system's (service) key: the list and the page give it no task, the shared rule every
# public task, as it has to for a runner's own replies (its watcher's new task, test_urgent_news). Also step 2's.
PUBLIC = (
    "public",
    "public_finance",
    "public_finance_assigned",
    "public_inbox",
    "public_inbox_asked",
    "managed_open",
    "managed_owned",
    "rel_public",
)
KEYS = {task: ("runner", "service") for task in PUBLIC}
KNOWN: dict[tuple[str, str], dict[str, bool]] = {
    (reader, task): {"sql": False, "task_row": False, "rule": True, "raw": True}
    for known in (FENCED, KEYS)
    for task, readers in known.items()
    for reader in readers
}


def _make(c, requester, title, owner, private=True, **fields):
    return H.task_create(c, requester, title, "Details.", owner, private=private, lint=False, **fields)["id"]


@pytest.fixture
def world(api, monkeypatch):
    """(auth, {reader label: Identity}, {task label: task id})."""
    counsel_on(api)
    fid, _ = promoted(api)
    edit(api, fid, {"people": ["cara"], "bots": [H.RULES_OWNER]})
    copy = post(api, "rules/read", {"file_id": fid, "source_id": source(api)}, token="cara-test")["task_id"]
    legal = post(api, "groups", {"name": "Legal", "add": {"people": ["cara"]}})["id"]
    thread = create_thread(api, title="Launch call")["id"]
    ok(add_member(api, "ana-test", thread, "human:cara"))
    meeting = record(api, {"private": True, "participants": ["ben@acme.example"]})
    item = add(api, meeting, {"section": "task", "text": "Participant task", "detail": {"owner": "human:ana"}})
    met = push(api, meeting, item["item"]["id"])["item"]["result_ref"]
    tasks = {"copy": copy, "meeting": met}
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='finance'")
        restrict(c, "finance", see={"people": ["ana", "ben", "cara"]}, read={"people": ["ana"]}, write={})
        tasks["public"] = _make(c, "human:ana", "Plan the offsite", "bot:ops", private=False)
        tasks["public_inbox"] = _make(c, "human:ana", "Sort the inbox", "bot:inbox", private=False)
        tasks["public_finance"] = _make(c, "human:ana", "Close the month", "bot:finance", private=False)
        tasks["public_finance_assigned"] = _make(c, "human:ana", "Send the invoices", "bot:finance", private=False)
        c.execute("UPDATE tasks SET assignee_id='human:cara' WHERE id=?", (tasks["public_finance_assigned"],))
        tasks["public_inbox_asked"] = _make(c, "human:ana", "Answer the inbox's ask", "bot:ops", private=False)
        c.execute("UPDATE tasks SET requester_id='bot:inbox' WHERE id=?", (tasks["public_inbox_asked"],))
        # A pipeline's manager is woken to pick an owner (task_engine.wake_for) and when one stalls (never_stuck).
        X.pipeline(c, "mgd", "Managed", ({"name": "Do", "requirements": []},), manager="bot:product-design")
        tasks["managed_open"] = _make(
            c, "human:ana", "Triage the inbox", "bot:ops", private=False, pipeline="mgd", unassigned=True
        )
        c.execute("UPDATE tasks SET requester_id='bot:inbox' WHERE id=?", (tasks["managed_open"],))
        tasks["managed_owned"] = _make(c, "human:ana", "File the mail", "bot:inbox", private=False, pipeline="mgd")
        tasks["filed_ok"] = _make(c, "human:cara", "Cara's spend", "bot:ops")
        asked = X.ok_ask(c, tasks["filed_ok"], "bot:ops", "human:cara", "OK to spend $1,200?")
        c.execute(
            "INSERT INTO ok_grants(requirement_id,asked_by_id,asked_of_id,subject,content_hash,content,filed,created_at) "
            "VALUES(?,'bot:ops','human:cara','budget_raise:spend','x','{}',1,?)",
            (asked["requirement_id"], H.now()),
        )
        tasks["own"] = _make(c, "human:ben", "Ben's own note", "human:ben")
        tasks["requester"] = _make(c, "human:cara", "Cara's request", "bot:ops")
        tasks["turn"] = _make(c, "human:ana", "Ana's routine turn", "bot:ops")
        tasks["assignee"] = _make(c, "human:ana", "Ben's to do", "bot:ops")
        c.execute("UPDATE tasks SET assignee_id='human:ben' WHERE id=?", (tasks["assignee"],))
        tasks["manager"] = _make(c, "human:ana", "Finance's books", "bot:finance")
        tasks["readers"] = _make(c, "human:ana", "Shared with Ben", "bot:ops", readers=["human:ben"])
        tasks["group"] = _make(c, "human:ana", "Shared with Legal", "bot:ops", readers=["group:" + legal])
        tasks["follow_up"] = _make(c, "human:ana", "Check the date", "bot:ops")
        now = H.now()
        c.execute(
            "INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,created_at,updated_at,started_at) "
            "VALUES('lm-parity','human:ana','Launch call','ended',30000,?,?,?)",
            (now, now, now),
        )
        c.execute(
            "INSERT INTO calls(id,thread_id,live_meeting_id,connector_actor,title,state,room_name,started_at,"
            "created_at,updated_at) VALUES('call-parity',?,'lm-parity','human:ana','Launch call','ended',"
            "'room-parity',?,?,?)",
            (thread, now, now, now),
        )
        c.execute(
            "INSERT INTO call_follow_ups(task_id,call_id,bot_id) VALUES(?,'call-parity','ops')", (tasks["follow_up"],)
        )
        # The page copy is cara's: Finance and CPO, named as more readers, are not in the page's audience.
        c.execute("UPDATE tasks SET readers=? WHERE id=?", ('["bot:finance","bot:cpo"]', copy))
        X.pipeline(c, "eng", "Engineering", ({"name": "Build", "owner": "bot:ops", "requirements": []},))
        # Given to Ben, held by Ops at Build: Ops is no party but the stage's, so it reads the task by the hold alone.
        tasks["stage"] = _make(c, "human:cara", "Build the export", "human:ben", pipeline="eng")
        entity = entities.upsert(c, "human:ana", "organization", name="North", match={})["id"]
        tasks["rel_public"] = _make(c, "human:ana", "North renewal", "bot:ops", private=False)
        tasks["rel_private"] = _make(c, "human:ana", "North pricing", "bot:ops")
        tasks["rel_cara"] = _make(c, "human:cara", "North contract", "bot:ops")
        for key in ("rel_public", "rel_private", "rel_cara"):
            c.execute("UPDATE tasks SET entity_id=? WHERE id=?", (entity, tasks[key]))
    auth = api.app.state.auth
    scopes = {attempt: (kind, tasks.get(value, value)) for attempt, (kind, value) in RUN_SCOPES.items()}
    monkeypatch.setattr(rooms, "_run_scope", lambda c, attempt: scopes.get(attempt, ("", "")))
    bound = {
        "run-rel": runs.Scope(entity, "", "", "human:ana"),
        "run-rel-gone": runs.Scope(entity, "", "", "human:zed"),  # its sponsor is no longer on the roster
    }
    monkeypatch.setattr(runs, "scope", lambda c, attempt: bound.get(attempt))
    with api.app.state.store.read() as c:
        people = {p: auth.identity_for_actor(c, "human:" + p) for p in ("ana", "ben", "cara")}
    readers = {
        **people,
        **{s: Identity("bot:" + s, "bot") for s in ("ops", "finance", "cpo", "inbox", H.RULES_OWNER, "assistant")},
        **{attempt: Identity("bot:assistant", "bot", attempt_id=attempt) for attempt in RUN_SCOPES},
        # Cara's own chat with the Assistant, as Auth.chat_person makes it.
        "cara-chat": Identity(
            "human:cara",
            "human",
            email="cara@acme.example",
            via="assistant",
            task_actor="bot:assistant",
            attempt_id="run-person",
        ),
        # Cara through a bot that acts for her (Auth's delegated identity): both must read what she reads through it.
        "cara-via-cpo": Identity("human:cara", "human", email="cara@acme.example", task_actor="bot:cpo"),
        # Ana, who reads every bot, through one that may not read the inbox: the person is fenced, not the one she
        # acts through.
        "ana-via-cpo": Identity("human:ana", "owner", email="ana@acme.example", task_actor="bot:cpo"),
        "ana-chat": Identity(
            "human:ana",
            "owner",
            email="ana@acme.example",
            via="assistant",
            task_actor="bot:assistant",
            attempt_id="run-ana",
        ),
        # Cara's own Assistant outside a run (a confirmed click): it reads the task filed for her OK, and no other.
        "cara-via-assistant": Identity(
            "human:cara", "human", email="cara@acme.example", via="assistant", task_actor="bot:assistant"
        ),
        # Cara's chat form in each other scope: the OK filed for her is read only in her own chat.
        **{
            f"cara-chat-{scope}": Identity(
                "human:cara",
                "human",
                email="cara@acme.example",
                via="assistant",
                task_actor="bot:assistant",
                attempt_id=f"run-{scope}",
            )
            for scope in ("gone", "nobody", "task")
        },
        # A bot reading for a person (a visual's token, visuals.py): Ben, the assignee, reads his task through Ops.
        "ops-for-ben": Identity("bot:ops", "bot", task_actor="human:ben"),
        "zed": Identity("human:zed", "human", email="zed@acme.example"),  # no longer on the roster
        "runner": Identity("runner:r1", "runner", runner_id="r1"),
        "service": Identity("service:k1", "service"),
        "rel-plain": Identity(runs.ACTOR, "bot"),  # the relationship worker outside a run
        "product-design": Identity("bot:product-design", "bot"),
        "run-rel": Identity(runs.ACTOR, "bot", attempt_id="run-rel"),
        "run-rel-gone": Identity(runs.ACTOR, "bot", attempt_id="run-rel-gone"),
    }
    return api, auth, readers, tasks


def _reads(c, auth, who, tasks):
    """{task label: {read: answer}} for one reader."""
    ids = list(tasks.values())
    try:
        clause = auth.task_sql(c, who)
        listed = {
            r[0]
            for r in c.execute(f"SELECT id FROM tasks WHERE id IN ({','.join('?' * len(ids))}) AND ({clause})", ids)
        }
    except Problem:
        listed = set()
    out = {}
    for label, tid in tasks.items():
        try:
            auth.task_row(c, who, H.task(c, tid))
            row = True
        except Problem:
            row = False
        out[label] = {
            "sql": tid in listed,
            "task_row": row,
            "rule": bool(task_privacy.task_readable(c, who, H.task(c, tid))),
            "raw": bool(H.task_private_readable(c, task_privacy.actor(who), H.task(c, tid))),
        }
    return out


def test_every_reader_reads_every_task_by_one_rule(world):
    api, auth, readers, tasks = world
    with api.app.state.store.read() as c:
        reads = {label: _reads(c, auth, who, tasks) for label, who in readers.items()}
        staged = H.task(c, tasks["stage"])
        assert (staged["owner_id"], staged["assignee_id"]) == ("bot:ops", "human:ben")
        assert H.stage_read_held(c, "bot:ops", staged) == "Build"  # Ops reads it by its hold alone
        assert H.task(c, tasks["managed_open"])["owner_id"] is None
    # The fixture reaches every admission path it names (read by the list).
    for reader, task in (
        ("ben", "own"),
        ("cara", "requester"),
        ("ben", "assignee"),
        ("cpo", "manager"),
        ("ben", "meeting"),
        ("ben", "readers"),
        ("cara", "group"),
        ("cara", "follow_up"),
        ("cara", "copy"),
        ("ops", "stage"),
        ("run-rel", "rel_public"),
        ("run-rel", "rel_private"),
        ("run-person", "requester"),
        ("cara-chat", "requester"),
        ("run-task", "turn"),
        ("cara", "public"),
        ("cara", "public_finance_assigned"),  # a fenced task's person assignee
        ("cara-via-assistant", "filed_ok"),
        ("ana-via-cpo", "public_inbox"),  # the person passes the fence, whom she acts through does not
        ("ana-chat", "public_inbox"),
        ("ops-for-ben", "assignee"),
    ):
        assert reads[reader][task]["sql"], (reader, task, reads[reader][task])
    for reader, task in (
        ("cara", "public_finance"),  # bot-activity fence
        ("cara", "public_inbox"),
        ("ben", "rel_cara"),
        ("finance", "copy"),  # a denied page copy
        ("run-rel", "rel_cara"),
        ("run-nobody", "requester"),
        ("run-gone", "public"),
        ("assistant", "requester"),
        ("cara", "public_inbox_asked"),  # a restricted bot as requester
        ("cara", "managed_open"),
        ("cara-via-assistant", "requester"),  # no OK of hers was filed there
        ("run-ana", "public_inbox"),  # a run in Ana's own chat reads a public task as the Assistant
        ("run-task-copy", "copy"),  # a routine turn's own task that is a denied page copy
        ("run-rel-gone", "rel_public"),  # a relationship run whose sponsor is gone
        ("cara-chat-gone", "filed_ok"),
        ("cara-chat-nobody", "filed_ok"),
        ("cara-chat-task", "filed_ok"),
        ("zed", "public_inbox"),
        ("runner", "public_inbox"),
        ("service", "public_inbox"),
        ("rel-plain", "rel_public"),
    ):
        assert not reads[reader][task]["sql"], (reader, task, reads[reader][task])
    found = {
        (reader, task): answers
        for reader, by_task in reads.items()
        for task, answers in by_task.items()
        if len(set(answers.values())) > 1
    }
    assert found == KNOWN, "\n".join(f"{k}: {v}" for k, v in sorted(found.items()))


def test_a_question_on_a_company_task_its_person_may_not_read_writes_no_reader(world):
    """The never-stuck ladder asks a person on a stalled task (question_marks.ask, checked=False). On a company task
    behind the bot-activity fence they are not let in: no reader is written, so the task going private later opens
    nothing to them."""
    from backend import question_marks

    api, _, _, tasks = world
    with api.app.state.store.transaction() as c:
        # The ask may go through (the shared rule does not fence a public task, KNOWN above), or be refused only as
        # the chat not being hers to read; any other refusal fails here.
        try:
            question_marks.ask(
                c, "bot:ops", tasks["public_inbox"], "human:cara", "Can you look at this?", checked=False
            )
        except H.Refused as refused:
            assert refused.rule == "private", refused
        # Outside the refusal's check: a reader written before a refusal is caught too.
        assert H.task_readers(H.task(c, tasks["public_inbox"])) == []
