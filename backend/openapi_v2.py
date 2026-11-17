"""The stable v2 contract for a team's own frontend: `GET /api/v2/openapi.json`.

FastAPI would describe every route, runner and bot endpoint included. A frontend team needs the
resources a person's app is built from, so this module keeps only those, named and grouped by resource where each route
is declared (`**api_v2(...)` on its decorator, backend/api_contract.py), and describes the answers a person gets. A path
lists its methods in the order the app registers them. Handlers return
plain dictionaries rather than response models, so the answer shapes are declared here; a test
(backend/tests/test_openapi_v2.py) checks them against live responses. The document is generated, not committed:
`python3 -m backend.openapi_v2 > openapi-v2.json` writes it, `--check` confirms it generates the same bytes twice, and
the server serves it at `GET /api/v2/openapi.json`.

Anything not listed is internal and may change in any release. See docs/api.md.
"""

import copy
import json
import re
import sys
from pathlib import Path

from . import needs_shape
from .api_contract import MARK
from .legacy_spellings import legacy_of
from .sources import KINDS as SOURCE_KINDS
from .task_documents import NAMES as DOCUMENT_NAMES

VERSION = "2.0.0"

TAGS = {
    "Session": "Who is calling, and how a frontend signs in (docs/custom-frontend.md).",
    "Team": "This installation's names and settings.",
    "Repositories": "Team repositories, product-repository creation (the Owner, or a bot with Create product repositories), "
    "and bot repository access.",
    "Subscriptions": "Provider logins on Computers. Every bot runs each AI tool on its Computer's one login for it.",
    "Team chart": "Humans and bots, and who reports to whom.",
    "Bots": "The bots a person can see, and their live status.",
    "Computers": "The computers that run bots: what each one runs at once.",
    "Conversations": "Chats with bots: send, list, and stream replies.",
    "Tasks": "Work assigned to people and bots.",
    "Pipelines": "The stages a task goes through and what it takes to leave each one.",
    "Updates": "Daily and weekly updates bots post to people.",
    "Changelog": "Product announcements and the signed-in person's read state (docs/updates.md).",
    "Needs you": "What is waiting on the signed-in person: questions, tasks, approvals.",
    "Meetings": "Recorded meetings.",
    "Calls": "Calls in a thread (docs/meet.md): a Google Meet, or a Tico Meet call with audio and screen share, a live "
    "transcript posted to the thread when it ends, and bots that listen. Joining a Tico Meet call hands back a token for "
    "audio and screen share only, never a camera; joining a Google Meet hands back its link.",
    "Files": "What a bot creates, revises or delivers, listed on its page (docs/files.md).",
    "Pages": "HTML pages bots publish and keep current (docs/pages.md): a file with a cadence, a data date and a status line.",
    "Docs": "The team's docs (docs/docs.md): internal docs written or imported in Tico, with history and locks; external sources, including indexed public GitHub docs; and search across them.",
    "Assistant": "The signed-in person's own private Assistant chat (docs/assistant.md): ask, and confirm what it proposes.",
    "Goals": "What every person and bot is for (docs/goals-and-kpis.md): "
    "goals with a colour the Strategy Owner works out from their KPIs, "
    "unless a person set one; check-ins in the owner's words; proposals the owner confirms.",
    "KPIs": "Measures that stand on their own: a goal links to them and carries the target. Readings are facts with a period, "
    "evidence and a quality, never edited; a correction supersedes the old one.",
    "Usage": "Estimated model spend per bot (docs/usage.md): tokens counted by each run's computer, priced at list price.",
    "Health": "Whether the installation is working.",
    "Live events": "One server-sent event stream for a person's pages (docs/custom-frontend.md, Live events): tasks, "
    "messages, runs, bots and Needs you as they change, resumable from a change number.",
}

EVENTS_DESCRIPTION = (
    "text/event-stream. Each change is `id: <change number>`, `event: <topic>` and JSON `data` with `seq`, `actor` "
    "(who made it, when the viewer may know) and `at`:\n"
    "- `tasks`: `id`, `kind` (the last change), `kinds` (all distinct change kinds for this task in the batch) and "
    "`task` (as `GET /api/v2/tasks` shows it to the viewer), or `gone: true` for a deleted task (TaskEvent schema). "
    "When the batch moved its owner or status, `was` (`owner`, `status`) is what they were, only to a viewer who could "
    "read the task before and can now; a viewer who never could gets no event. "
    "Refetch files and linked pages when `kinds` includes `files`, even if `kind` is `update`. "
    "A `bulk: true` event without `id` means read task lists in full.\n"
    "- `meeting_items`: `meeting_id`, `kind`; refetch items. `presence`: `place`; refetch presence. "
    "Explicit meeting/task subscriptions receive one `gone: true` on access loss, rechecked within 60 seconds.\n"
    "- `messages`: `id`, `conversation_id` and `message` (as a conversation page shows it), or `deleted: true`; "
    "a chat goal set, paused, met or stopped is `goal_id` and `goal`.\n"
    "- `runs`: `run_id`, `conversation_id`, `bot`, and either `output` (one step of the run: `kind`, `payload`) or "
    "`state` (`job_id`, and the job's state or the run's `state`: docs/status-and-state.md, Runs).\n"
    "- `bots`: `bot` and `status` (as `GET /api/v2/status` lists it).\n"
    "- `threads`: `thread_id`, `root_id` and `kind` (docs/dev/threads-contract.md §9); an `activity` one also carries "
    "`counters`, `message_id` (the newest message it stands for that the viewer reads), `unread` and `unread_count` "
    "(how many of the messages it stands for are unread for the viewer: add that many) and `thread` (`id`, `title`, "
    "`name`, `audience`, `root_id`, `parent_id`, `kind`, `last_message_at`, and `dm_with`/`dm_with_count` for a DM), "
    "so a list bumps its row, or adds one it does not hold, without reading it again; `pinned` carries `actor`, `pinned` and `pinned_for_everyone`; `bot_status` (to the "
    "root's readers, at most one a run step every 5 s) carries `bots`, the root's whole list as "
    "`GET /api/v2/threads/{thread_id}/bot-status` gives it; `routed` carries `message_id` and `routed`.\n"
    "- `needs`: the viewer's own Needs-you `count`, `plate_count` and `items` (as `GET /api/v2/needs-you`; a mention "
    "item carries `message_id`, `conversation_id` and its thread's `root_id`), whenever anything on it may have moved. "
    "With `needs=changes`: `count`, `plate_count`, `version`, each bot's count (`bots`), each stage's count (`stages`), counts by kind (`by_kind`), "
    "task ids represented by other cards (`folded_task_ids`), and the ids `changed` and `removed` "
    "since `base` (null when more than 100 moved: read the list whole). `folded_task_ids` is omitted when unchanged "
    "(keep the held set), or null when the replacement or its difference exceeds 100 ids (read the list whole); read the changed items with "
    "`GET /api/v2/needs-you?ids=`, and the whole list (or its first pages) when the version held is not "
    "`base`. `ready` then carries `needs_version`.\n"
    "- `calendar`: `calendar` (the calendar row id, `human:<id>` for time away, `google:<id>` for a Google write), "
    "`id` and `kind` (event, away, google); refetch the calendar window on screen.\n"
    "- `letters`: `id` and `letter` (as `GET /api/v2/letters/{id}` shows it), only to readers of that record. Patch this row without rereading the list.\n"
    "Also `event: ready` (`seq`: where the log stands), `event: cursor` (the change number to resume from, past changes "
    "the viewer may not see), `event: reset` (the client was further behind than the log keeps, 24 hours or more than 5,000 changes: read in full), "
    "`event: expired` (sign-in no longer valid; sign in and reconnect), and a `: keepalive` comment every 15 seconds. "
    "Only what the viewer may read is sent. A stream ends after about fifteen minutes; reconnect with `after` (or the "
    "browser's own `Last-Event-ID`). People only: a bot is refused with 403."
)


def _t(kind):
    return {
        "s": {"type": "string"},
        "i": {"type": "integer"},
        "b": {"type": "boolean"},
        "o": {"type": "object"},
        "n": {"type": ["string", "null"]},
        "a": {"type": "array"},
        "f": {"type": "number"},
    }[kind]


def obj(fields, required=None, **arrays):
    """An object schema from {"name": "s|i|b|o|n|a|f"} or {"name": {schema}}. Other fields are allowed."""
    props = {k: (_t(v) if isinstance(v, str) else v) for k, v in fields.items()}
    props.update(arrays)
    return {
        "type": "object",
        "properties": props,
        "required": sorted(required if required is not None else props),
        "additionalProperties": True,
    }


NUM_N = {"type": ["number", "null"]}
PERIODS = (
    "per_30m",
    "hourly",
    "daily",
    "weekly",
    "monthly",
)  # the spend limit periods (backend/usage_limits.py); the 30-minute one
GRANOLA_SKIP_REASONS = {
    "type": "object",
    "additionalProperties": {"type": "integer"},
    "description": "Skipped items in the last attempt, by fixed code and step",
}
GRANOLA_ERROR_DETAIL = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "description": "Fixed facts about the last failure or skip; never provider text",
    "properties": {
        "step": {"type": "string"},
        "http_status": {"type": ["integer", "null"]},
        "rpc_code": {"type": ["integer", "null"]},
        "tool_error": {"type": "boolean"},
        "retry_after": {"type": ["integer", "null"]},
        "signal": {"type": ["string", "null"], "enum": ["rate limit", "slow down", "too many requests", None]},
        "batch": {"type": ["integer", "null"]},
    },
}


def items(schema):
    """An array schema of one item schema, written like an obj field: "s|i|b|o|n|a|f" or {schema}."""
    return {"type": "array", "items": _t(schema) if isinstance(schema, str) else schema}


ref = lambda name: {"$ref": "#/components/schemas/" + name}
ACTORS = {
    "type": "object",
    "additionalProperties": {"type": "string"},
    "description": "On reads: display names for every actor id in the answer, {\"human:ana\": \"Ana Alvarez\"}",
}

# One item of a person's Needs you (backend/views.py needs_items).
NEEDS_ITEM = obj(
    {
        "id": "s",
        "kind": {
            "type": "string",
            "enum": ["task", "ask", "paused", "mention"],
            "description": "task: a private task they must let someone see (`task.turn.share`); ask: a requirement "
            "with their name (its fields in "
            "`ask`); paused: a bot that cannot run, which they can fix; mention: an @ of them by a person they have "
            "not replied to",
        },
        "line": "s",
        "since": "n",
        "task": {"oneOf": [ref("TaskBrief"), {"type": "null"}], "description": "The brief task it is about"},
    },
    required=["id", "kind", "line", "since"],
    ask={"oneOf": [ref("TaskRequirement"), {"type": "null"}]},
    task_id={"type": ["string", "null"]},
    requirement_id={"type": ["string", "null"]},
    message_id={"type": "string", "description": "A mention's message"},
    conversation_id={"type": "string", "description": "The thread a mention was written in"},
    root_id={"type": "string", "description": "A mention's root thread: the rail row it moves"},
    href={"type": "string", "description": "A paused cause a person fixes in the app: where (Connect GitHub)"},
    snoozed_until={
        "type": "string",
        "description": "In GET /api/v2/needs-you/snoozed: when this snoozed ask comes back",
    },
)

# Who wrote a line (backend/sources.py): a message's, a search hit's and a task history entry's `source`.
SOURCE_SCHEMA = obj(
    {"kind": {"type": "string", "enum": list(SOURCE_KINDS)}, "name": "s", "link": "n"},
    required=["kind", "name", "link"],
)

SCHEMAS = {
    "MessageSource": SOURCE_SCHEMA,
    "SubscriptionList": obj(
        {
            "profiles_by_computer": items(
                obj(
                    {
                        "runner_id": "s",
                        "label": "s",
                        "profiles": items(obj({"name": "s", "id": "s", "display_name": "s", "runtimes": "o"})),
                    }
                )
            )
        }
    ),
    "SubscriptionIdentity": obj({"id": "s", "display_name": "s"}),
    "SubscriptionWeeklyResult": obj(
        {
            "weekly": {
                "anyOf": [
                    obj(
                        {
                            "used_percent": {"type": ["number", "null"]},
                            "resets_at": "n",
                            "reported_at": "s",
                            "source": {"enum": ["manual"]},
                        }
                    ),
                    {"type": "null"},
                ]
            }
        }
    ),
    "SubscriptionRefreshResult": obj(
        {
            "id": "s",
            "profile": "s",
            "runtime": "s",
            "state": {"enum": ["requested", "succeeded", "unavailable", "failed", "expired"]},
            "requested_at": "s",
            "updated_at": "s",
            "expires_at": "s",
        }
    ),
    "BotSubscription": obj(
        {
            "profile": "n",
            "id": "s",
            "display_name": "s",
            "source": {"enum": ["computer"]},
            "computer": {"anyOf": [obj({"runner_id": "s", "label": "s"}), {"type": "null"}]},
            "runtime": "s",
            "harness": "s",
            "signed_in": {"type": ["boolean", "null"]},
            "problem": "s",
            "warning": "s",
        },
        required=["profile", "id", "display_name", "source", "computer", "runtime", "harness", "signed_in", "problem"],
    ),
    "ChatGoal": obj(
        {
            "id": "s",
            "conversation_id": "s",
            "bot": "s",
            "objective": "s",
            "status": "s",
            "note": "s",
            "set_by": "s",
            "set_at": "s",
            "updated_at": "s",
            "ended_at": "n",
        },
        required=[
            "id",
            "conversation_id",
            "bot",
            "objective",
            "status",
            "note",
            "set_by",
            "set_at",
            "updated_at",
            "ended_at",
        ],
    ),
    "CatchUpResult": obj(
        {"thread_id": "s", "scope": "s", "messages": "i", "left_out": "i", "posted": "o", "words": "s"},
        required=["thread_id", "scope", "messages", "left_out"],
    ),
    "ChatGoalResult": obj(
        {"goal": {"anyOf": [ref("ChatGoal"), {"type": "null"}]}},
        required=["goal"],
        supported={"type": "boolean"},
        commands=items({"type": "object"}),
    ),
    "Message": obj(
        {
            "id": "s",
            "conversation_id": "s",
            "sender_id": "s",
            "recipient_id": "n",
            "kind": "s",
            "body": "s",
            "created_at": "s",
            "in_reply_to": "n",
            "refs": "o",
        },
        required=[
            "id",
            "conversation_id",
            "sender_id",
            "recipient_id",
            "kind",
            "body",
            "created_at",
            "in_reply_to",
            "refs",
        ],
        sender_name={"type": "string", "description": "Display name of sender_id, when it is a person or a bot"},
        recipient_name={"type": "string", "description": "Display name of recipient_id"},
        sender_can_approve_send={
            "type": "boolean",
            "description": "On GET /messages/{mid}: whether the human sender "
            "currently may manage the bot addressee's mail sending (Auth.bot_manager); false for bot senders "
            "or non-bot addressees. This checks authority only, not send intent or the draft's contents",
        },
        body_raw={
            "type": "string",
            "description": "A notice Tico wrote, as stored (with actor ids); `body` shows names to people",
        },
        edited_at={
            "type": ["string", "null"],
            "description": "When the author last changed a task comment's text; null when never edited",
        },
        deleted_at={
            "type": ["string", "null"],
            "description": "When the author deleted a task comment. A delete or "
            "retry acknowledgment can include a tombstone with an empty body; deleted comments are never listed",
        },
        run={
            "type": "object",
            "description": "The run that handled this message, once one has: on a person's message "
            "`{job_id, run_id, state}` where `state` is `started_run` (it started the run) or `added_to_run` "
            "(it was folded into a run already working); on a bot's reply `{job_id, run_id}` (the run that "
            "wrote it, plus a summary of what it did). Absent until a run takes the message",
            "properties": {
                "job_id": {"type": "string"},
                "run_id": {"type": "string"},
                "state": {"enum": ["started_run", "added_to_run"]},
            },
        },
        ask=ref("TaskReviewAskView"),
        answer=ref("ReviewAnswer"),
        answers=items({"oneOf": [{"type": "string"}, ref("ReviewAnswer")]})
        | {"description": "On a structured ask: all review answers, oldest first. On a bot reply: handled message ids"},
        woke={"type": "boolean", "description": "On a send: false when the message was held (see `held`)"},
        held={
            "type": "string",
            "description": "On a send: a bot's message to a bot held for that bot's next "
            "run, and how to wake it now (backend/bot_to_bot.py)",
        },
        sent_as=ref("SentAs")
        | {
            "description": "On a send to a bot: the way it went (steer, queue or defer) "
            "and a defer's deadline (backend/delivery.py)"
        },
    ),
    "Execution": obj(
        {"job_id": "s", "message_id": "s", "bot_id": "s", "run_id": "n", "state": "s", "label": "s", "text": "s"},
        required=["job_id", "message_id", "bot_id", "run_id", "state", "label", "text", "parts"],
        parts=items(obj({"kind": {"enum": ["progress", "reply", "tool"]}, "text": "s", "at": "s"}))
        | {
            "description": "The run's pieces so far, in order, while it is leased or running (empty otherwise). "
            "`reply` and `progress` are what the bot wrote (`progress` when a tool call follows it); "
            "`tool` is a short label such as \"Filing a task\" or \"Ran npm\", never its arguments or output"
        },
    ),
    "Conversation": obj(
        {
            "id": "s",
            "kind": "s",
            "subject": "s",
            "participants": items({"type": "string"}),
            "created_at": "s",
            "last_message_at": "s",
            "closed_at": "n",
        }
    ),
    "Task": obj(
        {
            "private": "b",
            "id": "s",
            "title": "s",
            "body": "s",
            "requester_id": "s",
            "owner_id": "n",
            "assignee_id": "n",
            "status": {"type": "string", "enum": ["open", "done", "closed"]},
            "created_at": "s",
            "updated_at": "s",
            "due_at": "n",
            "version": "i",
            "pipeline_id": "n",
            "stage_id": "n",
            "turn": {
                "oneOf": [ref("TaskWait"), {"type": "null"}],
                "description": "The headline wait: whose turn it is",
            },
            "late": {
                "oneOf": [obj({"since": "s", "level": {"enum": ["told", "person"]}}), {"type": "null"}],
            },
            "stuck": {
                "type": "boolean",
                "description": "Present and true when the never-stuck ladder brought it to a person (stuck or late)",
            },
            "lane": "s",
            "labels": items({"type": "string"}),
            "acceptance_criteria": items({"type": "string"}),
            "occurrence": {
                "type": ["string", "null"],
                "description": "A due-date routine's occurrence, \"<automation_id>:<due_on>\", on the task it opened: the due is its deadline, not a wake date.",
            },
        },
        required=[
            "id",
            "title",
            "requester_id",
            "owner_id",
            "status",
            "created_at",
            "updated_at",
            "due_at",
            "version",
            "pipeline_id",
            "stage_id",
            "turn",
            "late",
            "lane",
            "labels",
        ],
        owner_name={"type": "string", "description": "Display name of owner (`owner_id` stays the actor id)"},
        human_id={
            "type": ["string", "null"],
            "description": "The task's Human: the person accountable for it whoever owns it, first on its ladder (asks "
            "that name nobody go to them). Set with `human` on create and update; left out of a brief row when none",
        },
        requester_name={"type": "string", "description": "Display name of requester"},
        filer={
            "type": "string",
            "description": "Who really filed it, when not its requester: a bot id (BotOps on a person's rights) or a "
            "built-in source key (check-health, postal-mail). Display only; absent otherwise",
        },
        snoozed={
            "type": "boolean",
            "description": "On a list row: whether the task is set aside. A blocked task is not (see blocked_by), and "
            "snoozed=only leaves it out",
        },
        pipeline={
            "oneOf": [obj({"id": "s", "name": "s"}), {"type": "null"}],
            "description": "On the task's own read: the pipeline it is in",
        },
        stage={
            "oneOf": [
                obj({"id": "s", "name": "s", "kind": {"enum": ["work", "done", "closed"]}, "by_hand": "b"}),
                {"type": "null"},
            ],
            "description": "On the task's own read: the stage it is in",
        },
        pushed_past={"type": "boolean", "description": "On a list row: it entered its stage pushed past a requirement"},
        blocked_by=items(obj({"id": "n", "number": {"type": ["integer", "null"]}}))
        | {
            "description": "On a list row with a dependency: the open tasks it depends on (\"Blocked by #304\"), oldest "
            "first; one the reader may not open has a null id and number. Absent when there are none"
        },
        blocks=items(obj({"id": "s", "number": {"type": ["integer", "null"]}}))
        | {
            "description": "On a list row: the open tasks that depend on it and the reader may open (\"Blocks #307\"). "
            "Absent when there are none"
        },
        stage_since={
            "type": ["string", "null"],
            "description": "On a list row: when it entered its stage, else when it was filed",
        },
        waits=items(ref("TaskWait")) | {"description": "On the task's own read: every wait, headline first"},
        to_leave=items(ref("TaskRequirement"))
        | {"description": "On the task's own read: every requirement of the stage and each added to this visit"},
        inputs=items(
            obj(
                {
                    "kind": {"enum": ["pr", "page", "done", "missing", "signoff"]},
                    "line": "s",
                    "stage": "s",
                    "by": "n",
                    "url": "n",
                    "file_id": "s",
                    "version": "i",
                    "ref": "s",
                    "answer": {"enum": ["ok", "no"]},
                    "notes": "s",
                    "task_id": "s",
                },
                required=["kind", "line"],
            )
        )
        | {
            "description": "On the task's own read: what earlier stages handed on, oldest first (stage_inputs.of): "
            "its PRs, each earlier stage's page, what an owner said when it said done, a page that was never "
            "attached, and each OK or send back a person gave on work this task filed (`signoff`: answer, notes, "
            "the page version)"
        },
        next={
            "oneOf": [obj({"id": "s", "name": "s"}), {"type": "null"}],
            "description": "On the task's own read: the stage a forward move goes to",
        },
        snooze={"oneOf": [obj({"until": "n", "by": "s", "why": "n"}), {"type": "null"}]},
        filed_by={"type": ["string", "null"], "description": "The task that filed this one"},
        values={
            "type": "object",
            "description": "On the task's own read: the newest answer to each of its stages' named answers, by name, "
            "on any visit: {value, type, by, at, stage_id}; a number's value is a whole number",
            "additionalProperties": obj(
                {"value": {"type": ["string", "integer"]}, "type": "s", "by": "s", "at": "s", "stage_id": "s"}
            ),
        },
        filed=items(obj({"id": "s", "kind": "s", "status": "s", "title": "n"}))
        | {"description": "The tasks this one filed; title is null for one the reader may not read"},
        cover={
            "oneOf": [
                obj({"url": "s", "width": {"type": ["integer", "null"]}, "height": {"type": ["integer", "null"]}}),
                {"type": "null"},
            ]
        },
        relations={
            "type": "object",
            "description": "Related tasks the reader may open, by kind (parent, related, duplicate_of, follow_up, "
            "after, depends_on): each {id, title, status, owner, direction}; an `after` or `depends_on` entry also has "
            "the other task's number and stage (after out: this task merges after it; in: it merges after this one; "
            "depends_on out: this task is blocked by it; in: it blocks this one). A task waits on after, in Merge, and "
            "on depends_on. Sub-tasks are in the task's children.",
            "additionalProperties": items(
                obj(
                    {"id": "s", "title": "s", "status": "s", "owner_id": "s", "direction": "s"},
                    ["id", "title", "status", "owner_id", "direction"],
                    number={"type": ["integer", "null"]},
                    stage={"type": ["string", "null"]},
                    since={"type": "string", "description": "depends_on only: when the dependency was written"},
                )
            ),
        },
        body={
            "type": "string",
            "description": "Left out of a list asked for with brief=true, as is acceptance_criteria",
        },
        number={"type": ["integer", "null"], "description": "The task's number, unique across the team"},
        readers=items({"type": "string"})
        | {
            "description": "Who else can see a private task besides its "
            "requester and owner: people, bots and group:<id>s (a group by its members now). Empty on a "
            "company task. GET/PUT /api/v2/tasks/{id}/readers reads and changes it"
        },
        roles={
            "type": "object",
            "description": "Who is on the task, by role: each role a short name the team "
            "chooses (developer, reviewer, qa ...) with its actors in the order they were added; a role nobody "
            "holds is absent, and one actor may hold several roles. Tico gives roles no meaning or rights. Task "
            "update and create take the same shape to replace a role's people ([] clears one)",
            "additionalProperties": items({"type": "string"}),
        },
    ),
    "TaskWait": obj(
        {
            "state": {
                "type": "string",
                "enum": ["snoozed", "person", "paused", "bot", "jev", "source", "tasks", "room", "pick", "by_hand"],
            },
            "who": "n",
            "line": "s",
            "requirement_id": "n",
            "since": "n",
            "until": "n",
            "ask": "b",
        },
        required=["state", "who", "line", "requirement_id", "since", "until", "ask"],
        href={
            "type": ["string", "null"],
            "description": "A paused bot's cause: the app page that shows it (#/settings/computers, #/settings/billing)",
        },
        running={
            "type": "boolean",
            "description": "On a list row: a bot's turn (state bot) is a run going now; false while it waits to try "
            "again",
        },
        share={
            "type": "array",
            "items": {"type": "string"},
            "description": "A private task waits on these actors being let in (the next stage's owner and those it "
            "asks, or someone asked here, can't see it): move with to next and let_see set to exactly this list",
        },
    ),
    "TaskRequirement": obj(
        {
            "requirement_id": "s",
            "kind": "s",
            "line": "s",
            "met": "b",
            "standing": "s",
            "who": "n",
            "asked_by": "n",
            "choices": {"type": ["array", "null"], "items": {"type": "string"}},
            # A bot's question to a person: why it matters, a line per choice on what happens (in the choices' order)
            # and the choice it recommends (backend/ask_shape.py). Absent on an ask that carried none.
            "brief": obj(
                {
                    "why": "s",
                    "details": {"type": ["array", "null"], "items": {"type": "string"}},
                    "recommend": "n",
                },
                required=["why", "details", "recommend"],
            ),
            # The ask's short title (at most 60 characters), the decision view's heading. Absent on an ask without one.
            "title": "s",
            "shows": items({"type": "string"}),
            # A page requirement: the page attached now, at the version an OK of it is for.
            "page": obj({"file_id": "s", "version": "i", "ref": "s"}, required=["file_id", "version", "ref"]),
            # A stage's OK that names nothing to show: the page that counted last on an earlier visit of the task (the
            # QA page at OK release), to a reader who may open it; to anyone else {removed, label} or absent.
            "earlier": obj(
                {
                    "file_id": "s",
                    "version": "i",
                    "ref": "s",
                    "stage_id": "s",
                    "stage": "s",
                    "source": items({"type": "string"}),
                    "removed": "b",
                    "label": "s",
                    # What Jev passed on that page, one short line per part of its check (page_verdicts.checks_of).
                    "checks": items({"type": "string"}),
                },
                required=[],
            ),
            # An OK that shows a page first (Validate's): what Jev passed on that page, one short line per part of its
            # check (page_verdicts.checks_of). Absent until Jev passed it.
            "checks": items({"type": "string"}),
            # A stage's document approval: its key (the visit and the version submitted, sent back with an OK) and
            # `unchecked` when the decision model could not check this version. Absent on every other requirement.
            "approval": obj(
                {
                    "document": "s",
                    "visit_id": "s",
                    "file_id": "n",
                    "version": {"type": ["integer", "null"]},
                    "unchecked": "b",
                },
                required=["document", "visit_id", "file_id", "version", "unchecked"],
            ),
            "answer": {"oneOf": [obj({"id": "s", "value": "s", "note": "n", "by": "s", "at": "s"}), {"type": "null"}]},
            # A stage's answer: how it is named and what it takes. Absent on every other requirement.
            "typed": obj(
                {
                    "name": "s",
                    "type": {"enum": ["text", "number", "choice"]},
                    "min": {"type": ["integer", "null"]},
                    "max": {"type": ["integer", "null"]},
                    "question": "s",
                }
            ),
            "may": items({"enum": ["done", "ok", "no", "again", "answer", "give", "undo", "withdraw", "thumbs"]})
            | {"description": "What this reader may do with it: a hint, the engine decides"},
        },
        required=[
            "requirement_id",
            "kind",
            "line",
            "met",
            "standing",
            "who",
            "asked_by",
            "choices",
            "shows",
            "answer",
            "may",
        ],
    ),
    "Person": obj(
        {"id": "s", "name": "s", "email": "s", "title": "s", "team": "s", "reports_to": "n", "org_parent": "s"},
        required=["id", "name", "org_parent"],
    ),
    "Access": obj({"see": "b", "read": "b", "write": "b"}, required=["see", "read", "write"]),
    "OrgBot": obj(
        {
            "id": "s",
            "display_name": "s",
            "description": "s",
            "team": "s",
            "reports_to": "n",
            "org_parent": "s",
            "owners": items({"type": "string"}),
            "status": "s",
            "access": ref("Access"),
            "onboarding_state": "s",
            "stage": {
                "enum": ["onboarding", "active"],
                "description": "Onboarding (learns only: no tasks, routines or sends) until a person above it marks onboarding complete, then active",
            },
        },
        required=["id", "display_name", "org_parent", "status", "access"],
    ),
    "Seat": obj(
        {
            "id": "s",
            "kind": {"enum": ["seat"]},
            "stage": {"enum": ["recruiting"]},
            "title": "s",
            "description": "s",
            "reports_to": "s",
            "team": "s",
            "created_at": "s",
            "org_parent": "s",
        },
        required=["id", "kind", "stage", "title", "description", "reports_to"],
    ),
    "SeatList": obj({"seats": items(ref("Seat"))}, required=["seats"]),
    "SeatClosed": obj({"id": "s", "closed": "b"}, required=["id", "closed"]),
    "SeatDetail": obj(
        {
            "id": "s",
            "kind": {"enum": ["seat"]},
            "stage": {"enum": ["recruiting"]},
            "title": "s",
            "description": "s",
            "reports_to": "s",
            "team": "s",
            "created_at": "s",
            "description_by": {"enum": ["chart", "person"]},
            "can_edit": "b",
            "reports_now": obj({"name": "s", "until": "s"}, required=["name", "until"]),
        },
        required=["id", "kind", "stage", "title", "description", "reports_to", "can_edit"],
    ),
    "SeatDrafted": obj(
        {"description": "s", "source": {"enum": ["catalog", "model", "none"]}}, required=["description", "source"]
    ),
    "SeatNeed": obj(
        {"tool": "s", "label": "s", "required": "b", "state": {"enum": ["connected", "on_hire", "not_set"]}},
        required=["tool", "label", "required", "state"],
    ),
    "SeatCandidate": obj(
        {
            "template": "s",
            "name": "s",
            "icon": "s",
            "department": "s",
            "department_id": "s",
            "line": "s",
            "summary": "s",
            "tags": items({"type": "string"}),
            "owns": items({"type": "string"}),
            "never": items({"type": "string"}),
            "onboarding": items({"type": "string"}),
            "prerequisites": items(ref("SeatNeed")),
            "match": {"enum": ["strong", "good", ""]},
            "source": {"enum": ["template"]},
            "starter": "b",
            "model": "s",
            "model_name": "s",
            "effort": "s",
        },
        required=[
            "template",
            "name",
            "icon",
            "department",
            "line",
            "tags",
            "owns",
            "prerequisites",
            "match",
            "source",
            "model",
            "model_name",
        ],
    ),
    "SeatCandidates": obj(
        {
            "seat": ref("SeatDetail"),
            "candidates": items(ref("SeatCandidate")),
            "models": items(obj({"id": "s", "name": "s", "provider": "s"}, required=["id", "name"])),
        },
        required=["seat", "candidates", "models"],
    ),
    "SeatHired": obj(
        {
            "slug": "s",
            "name": "s",
            "seat": "s",
            "stage": {"enum": ["onboarding"]},
            "reports_to": "s",
            "template": "s",
            "model": "s",
            "setup_task_id": "n",
            "note": "n",
        },
        required=["slug", "seat", "stage", "reports_to", "template"],
    ),
    "TaskFlow": obj({"going": "i", "waiting": "i"}, required=["going", "waiting"]),
    "Bot": obj(
        {
            "id": {
                "type": "string",
                "description": "The bot's stable id (its slug for a bot made before ids, else a UUIDv7); the slug is a renameable label",
            },
            "slug": "s",
            "display_name": "s",
            "state": "s",
            "online": "b",
            "queued": "i",
            "description": "s",
            "folder": {
                "type": "string",
                "description": "Its checkout's folder on a computer; clients find its files by this, never by its slug.",
            },
            "reports_to": "n",
            "team": "n",
            "owner_id": "n",
            "owners": "a",
            "status": {"type": ["object", "null"]},
            "turns_at_once": {"type": ["integer", "null"]},
            "task_flow": ref("TaskFlow"),
            "access": ref("Access"),
            "onboarding_state": "s",
            "stage": {
                "enum": ["onboarding", "active"],
                "description": "Onboarding (learns only: no tasks, routines or sends) until a person above it marks onboarding complete, then active",
            },
            "can_complete_onboarding": {
                "type": "boolean",
                "description": "While onboarding: whether the caller may mark its onboarding complete",
            },
            "onboarding_ready": {
                "type": ["object", "null"],
                "description": "While onboarding: the bot's \"I'm ready\" ({at, summary}), or null",
            },
        },
        required=["id", "slug", "display_name", "state", "owners", "access"],
    ),
    "BotDetail": obj(
        {
            "id": {
                "type": "string",
                "description": "The bot's stable id (its slug for a bot made before ids, else a UUIDv7); the slug is a renameable label",
            },
            "slug": "s",
            "display_name": "s",
            "description": "s",
            "state": "s",
            "online": "b",
            "queued": "i",
            "status": {"type": ["object", "null"]},
            "reports_to": "n",
            "reports_to_name": "s",
            "owner_id": "n",
            "owner_name": "s",
            "team": "n",
            "owners": "a",
            "goals": "s",
            "turns_at_once": {"type": ["integer", "null"]},
            "task_flow": ref("TaskFlow"),
            "routines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "on": {"type": "boolean"}},
                },
                "description": "For a caller who can read it: the bot's routines (its automations, the first 20) and "
                "whether each is on now",
            },
            "cant_run": {
                "type": ["object", "null"],
                "properties": {
                    "cause": {"type": "string"},
                    "fix": {
                        "enum": [
                            "resume",
                            "release_quarantine",
                            "finish_setup",
                            "assign_computer",
                            "release_hold",
                            "resume_company",
                            "unlock_cloud",
                            "add_credit",
                            "open_computers",
                            "open_ask",
                            None,
                        ]
                    },
                    "task": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "title": {"type": "string"}},
                        "description": "The task a planned bot is being made on, for a caller who may open it",
                    },
                    "ask": {
                        "type": "object",
                        "properties": {
                            "kind": {"enum": ["ok", "answer"]},
                            "mine": {"type": "boolean"},
                            "line": {"type": "string"},
                        },
                        "description": "The ask on that task the bot waits on: whether it is the caller's, and its "
                        "words for a caller who may open the task",
                    },
                },
                "description": "For a caller who can read it: why the bot can't run (a sentence) and the one fix, "
                "or null while it can run. A bot being made on a task names it (`task`) and the ask it waits on "
                "(`ask`); `open_ask` opens that ask on its task",
            },
            "access": ref("Access"),
            "onboarding_state": "s",
            "template": "s",
            "template_version": "s",
            "stage": {
                "enum": ["onboarding", "active"],
                "description": "Onboarding (learns only: no tasks, routines or sends) until a person above it marks onboarding complete, then active",
            },
            "can_complete_onboarding": {
                "type": "boolean",
                "description": "While onboarding: whether the caller may mark its onboarding complete",
            },
            "onboarding_ready": {
                "type": ["object", "null"],
                "description": "While onboarding: the bot's \"I'm ready\" ({at, summary}), or null",
            },
        },
        required=["slug", "display_name", "state", "owners", "access"],
    ),
    "Audience": obj(
        {
            "everyone": "b",
            "people": items({"type": "string"}),
            "teams": items({"type": "string"}),
            "bots": items({"type": "string"}),
        },
        required=["everyone", "people", "teams", "bots"],
    ),
    "BotAccessView": obj(
        {
            "bot": "s",
            "see": ref("Audience"),
            "read": ref("Audience"),
            "write": ref("Audience"),
            "revision": "i",
            "you": ref("Access"),
            "teams": items({"type": "object"}),
        },
        required=["bot", "see", "read", "write", "revision"],
    ),
    "TeammateAccessView": obj(
        {
            "target": "s",
            "see": ref("Audience"),
            "read": ref("Audience"),
            "write": ref("Audience"),
            "default": {"type": "object"},
            "revision": "i",
            "you": obj({"see": "b", "read": "b", "write": "b", "manage": "b"}),
            "teams": items({"type": "object"}),
        },
        required=["target", "see", "read", "write", "revision"],
    ),
    "Me": obj(
        {
            "actor": "s",
            "role": "s",
            "email": "s",
            "slug": {"type": "string", "description": "A bot caller: its slug (a label; the actor holds its id)."},
        },
        required=["actor", "role", "email"],
    ),
    "Config": obj(
        {
            "company_name": "s",
            "app_name": "s",
            "assistant_name": "s",
            "assistant_bot": "s",
            "public_url": "s",
            "owner_email": "s",
            "version": "s",
            "administration_url": {
                "type": "string",
                "description": "Signed-in humans: the Tico account portal "
                "URL, or empty for an install without a Tico account or portal company. "
                "Open externally; carries no sign-in credentials.",
            },
            "visual_origin": {
                "type": "string",
                "description": "Configured non-secret visual origin, or empty "
                "when absent or invalid; grants no native IPC permission.",
            },
            "visuals": {
                "type": "string",
                "enum": ["full", "hosted", "media"],
                "description": "How visuals show, from the setup (no switch): full with a separate "
                "visual address; hosted without one while Tico file hosting takes new "
                "files (pages and SVG inline from the company's files address); media "
                "otherwise (pictures, video and audio inline, pages and SVG open in the "
                "file viewer).",
            },
            "visual_files": {
                "type": "string",
                "description": "Signed-in callers: the Tico file hosting origin "
                "pages and SVG show from when there is no visual address, else empty; grants no "
                "native IPC permission.",
            },
            "s3_view_urls": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Signed-in callers: bucket -> https base an s3://bucket/key opens at "
                "(base + '/' + key, each segment URL-encoded; TICO_S3_VIEW_URLS)",
            },
        },
        required=[
            "company_name",
            "app_name",
            "assistant_name",
            "assistant_bot",
            "public_url",
            "owner_email",
            "version",
        ],
    ),
    "Group": obj(
        {
            "id": "s",
            "name": "s",
            "parent": "s",
            "people": items({"type": "string"}),
            "bots": items({"type": "string"}),
            "bot_slugs": items({"type": "string"}),
            "order": "i",
        }
    ),
    "GroupList": items(ref("Group")),
    "ReportOrderView": obj(
        {
            "manager": "s",
            "kind": "s",
            "name": "s",
            "can_order": "b",
            "reports": items(
                obj({"id": "s", "kind": "s", "name": "s", "saved": "b"}, required=["id", "kind", "name", "saved"])
            ),
        },
        required=["manager", "kind", "name", "can_order", "reports"],
    ),
    "Org": obj(
        {
            "people": items(ref("Person")),
            "bots": items(ref("OrgBot")),
            "seats": items(ref("Seat")),
            "org_groups": "a",
            "teams": "o",
        }
    ),
    "OrgModelSeat": obj({"team": "s", "template": "s", "title": "s"}, required=["team", "template"]),
    "OrgModel": obj(
        {"model": "s", "name": "s", "about": "s", "seats": items(ref("OrgModelSeat"))},
        required=["model", "name", "about", "seats"],
    ),
    "OrgModels": obj({"models": items(ref("OrgModel"))}, required=["models"]),
    "SeatBatch": obj({"seats": items(ref("Seat"))}, required=["seats"]),
    "BotList": items(ref("Bot")),
    "BotInstructions": obj({"bot": "s", "content": "s", "published": "b", "updated_at": "n", "source": "s"}),
    "BotOnboarded": obj(
        {
            "bot": "s",
            "onboarding_state": "s",
            "stage": "s",
            "changed": "b",
            "ready": {
                "type": ["object", "null"],
                "description": "The bot's own call: its \"I'm ready\" ({at, summary})",
            },
            "routine": {
                "type": ["string", "null"],
                "description": "The first of the template's routines this call turned on (one a person "
                "turned off stays off); null when it turned none on",
            },
        },
        required=["bot", "onboarding_state", "changed"],
    ),
    "BotTool": obj(
        {
            "id": "s",
            "service": "s",
            "name": "s",
            "logo_key": "n",
            "identity": "s",
            "can": items({"type": "string"}),
            "scope": {
                "type": "object",
                "description": "Service-specific fields, a string or a list of strings each: "
                "database, channels, project, mailbox, sites, repo, page, effort ... (any short lowercase name)",
            },
            "note": "s",
            "status": {"enum": ["ready", "problem", "unknown", "pending"]},
        },
        required=["id", "service", "name", "logo_key", "identity", "can", "scope", "note", "status"],
        env={"type": "string", "description": "The environment variable's name, never its value"},
        detail={"type": "string", "description": "One sentence on what the tool is or how it was checked"},
        problem={"type": "string", "description": "Why status is `problem`, in words a person can act on"},
        url={"type": "string", "description": "Where the tool opens, when it has an address (the repository)"},
        pending={
            "enum": ["add", "remove", "update"],
            "description": "A request BotOps has not finished: `add` on a tool that is "
            "not on the bot yet (status `pending`), `remove` on a declared one being taken out, `update` on "
            "one whose can, scope or note is being changed",
        },
        task_id={"type": "string", "description": "The BotOps task carrying the request"},
        granted={
            "type": "object",
            "description": "The bot can use this tool through a grant in Tools: `level` (read or read_write), `kind` "
            "(api, mcp, website, or key for a key on no tool), `href` (where it opens in Tools), `tool_id`, and `default` "
            "true when it reads the tool because Everyone can read, with no grant of its own. A tool both declared in "
            "bot.yaml and granted is one row with this set",
        },
        kind={"enum": ["mcp"], "description": "`mcp` when the tool is a remote MCP server (see `mcp`)"},
        choose={
            "type": "array",
            "items": {"type": "string"},
            "description": "Several tools anyone can read give this variable (`env`), so the bot gets none of them "
            "until a person grants one: their ids. Shown to the bot, its managers and credential admins",
        },
        mcp={
            "type": "object",
            "description": "A remote MCP server: `host` (its URL's host), `transport` (http or sse) and "
            "`status` from the runner's cheap check (reachable, auth_failed, unreachable, unchecked). Never the headers",
        },
    ),
    "BotToolRegistered": obj(
        {"tool": ref("BotTool"), "task_id": "n", "yaml": "s", "credentials": "s"},
        granted={
            "type": "boolean",
            "description": "The bot already holds this tool through a grant in Tools: no entry is needed, so no task "
            "was opened (`task_id` null) and `tool` is the granted row",
        },
    ),
    "BotToolUpdated": obj(
        {"task_id": "s", "yaml": "s", "credentials": "s"},
        required=["task_id"],
        update={"type": "boolean"},
        tool={"type": "string"},
    ),
    "BotToolRemoval": obj(
        {"task_id": "s"},
        required=[],
        canceled={"type": "boolean"},
        removal={"type": "boolean"},
        tool={"type": "string"},
        detail={"type": "string"},
    ),
    "BotTools": obj(
        {"bot": "s", "tools": items(ref("BotTool")), "computer": "n", "online": "b", "reported_at": "n"},
        required=["bot", "tools", "computer", "online", "reported_at"],
    ),
    "Recent": obj({"actor": "s", "since": "s", "bots": "a"}),
    "ConversationList": obj(
        {"actor": "s", "conversations": items(ref("Conversation"))}, required=["actor", "conversations"], actors=ACTORS
    ),
    "MessagePage": obj(
        {"conversation": ref("Conversation"), "messages": items(ref("Message")), "has_more": "b", "next_before": "n"},
        required=["conversation", "messages", "has_more", "next_before"],
        actors=ACTORS,
    ),
    "Snapshot": obj(
        {
            "messages": items(ref("Message")),
            "has_more": "b",
            "next_before": "n",
            "execution": {
                "oneOf": [ref("Execution"), {"type": "null"}],
                "description": "The latest run: state, label, "
                "text (the reply so far; separate messages are joined by a blank line), parts, bot",
            },
        }
    ),
    "MessageResult": obj({"message": ref("Message")}),
    "ChatResult": obj({"conversation": ref("Conversation"), "message": ref("Message")}),
    "TaskList": obj(
        {
            "tasks": items(ref("Task")),
            "next_offset": {"type": ["integer", "null"]},
            "next_after": {
                "type": ["string", "null"],
                "description": "Opaque: pass as after for the next keyset page (search and filters kept); null at the end",
            },
            "cursor": {
                "type": "string",
                "description": "Opaque: pass as changed_after to read only what changed since",
            },
            "gone": {
                "type": "array",
                "items": {"type": "string"},
                "description": "With changed_after: ids that "
                "changed and no longer match (deleted, no longer readable, or out of the filters)",
            },
            "reset": {
                "type": "boolean",
                "description": "With changed_after: the cursor is too old or no longer applies; read in full",
            },
        },
        required=["tasks", "next_offset", "cursor"],
        actors=ACTORS,
    ),
    "TaskResult": obj({"task": ref("Task")}),
    "TaskMergeResult": obj({"task": ref("Task"), "closed": ref("Task"), "note": "s"}),
    "TaskMergeChecks": obj(
        {
            "task": {"type": "object"},
            "into": {"type": "object"},
            "same_owner": "b",
            "subtask": {"type": ["string", "null"]},
            "merge": {"type": ["string", "null"]},
            "moves": {"type": "object"},
        }
    ),
    "OkPreview": obj(
        {
            "requirement_id": "s",
            "task_id": "s",
            "subject": "s",
            "content": {"description": "What the OK is for, exactly as it was asked"},
            "shown_hash": "s",
            "standing": {"enum": ["waiting", "ok", "sent_back", "withdrawn", "used"]},
        }
    ),
    "PipelineLog": obj(
        {
            "log": items(obj({"at": "s", "text": "s", "task_id": "n", "by": "n", "why": "s"})),
            "learned": items(obj({"at": "s", "text": "s"})),
        }
    ),
    "FactoryMap": obj(
        {
            "factories": items(obj({"id": "s", "name": "s", "goal": "s", "manager_id": "n", "done": "i"})),
            "pipelines": items(
                obj(
                    {
                        "id": "s",
                        "name": "s",
                        "factory_id": "n",
                        "open": "i",
                        "files_into": items({"type": "string"}),
                        "mark": {"enum": ["cant_run", "needs_person", None]},
                    }
                )
            ),
        }
    ),
    "CompanyFloor": obj(
        {
            "factories": items(
                obj(
                    {
                        "id": "n",
                        "name": "s",
                        "manager_id": "n",
                        "pipelines": items(
                            obj(
                                {
                                    "id": "s",
                                    "name": "s",
                                    "manager_id": "n",
                                    "in": obj({"day": "i", "week": "i"}),
                                    "out": obj({"day": "i", "week": "i"}),
                                    "stages": items(
                                        obj(
                                            {
                                                "id": "s",
                                                "name": "s",
                                                "count": "i",
                                                "needs_you": "i",
                                                "owners": items(obj({"actor": "s", "count": "i", "needs_you": "i"})),
                                                "more": obj({"owners": "i", "count": "i"}),
                                                "into": "n",
                                            }
                                        )
                                    ),
                                }
                            )
                        ),
                    }
                )
            )
        }
    ),
    "FactoryCreated": obj({"factory": obj({"id": "s", "name": "s", "goal": "s"}, required=["id", "name"])}),
    "PreferenceValue": obj({"key": "s", "value": {"description": "Any JSON value, or null when unset"}}),
    "ReleaseSchedule": obj(
        {
            "on": "b",
            "times": items({"type": "string"}),
            "bot": "s",
            "writer": "s",
            "zone": "s",
            "release_manager": "n",
            "technical_writer": "n",
            "next": "n",
            "last": {"type": ["object", "null"]},
            "pending": {"type": "integer", "description": "Open tasks you may read waiting for a release"},
            "can_change": "b",
            "can_release": "b",
        }
    ),
    "ReleaseNowResult": obj(
        {
            "sent": "b",
            "reason": {"enum": ["no_release_manager", "already_open", None]},
            "task": {
                "type": ["string", "null"],
                "description": "The task filed, or the open one when you may read it",
            },
            "tasks": {"type": "integer", "description": "Open tasks you may read waiting for a release"},
        }
    ),
    "SupportReportResult": obj(
        {
            "status": {"enum": ["sent", "pending"], "description": "sent: filed now; pending: a person OKs it first"},
            "task_id": {"type": ["string", "null"], "description": "The task the report is on, or that holds its OK"},
            "requirement_id": {"type": "string", "description": "pending: the OK a person gives on the task"},
            "ticket": {"type": "object", "description": "sent: the ticket as filed"},
            "filed_as": {"type": "string", "description": "sent: the person it was filed as"},
        },
        required=["status", "task_id"],
    ),
    # A task's own model (backend/task_run_models.py `of`); null when its runs use the bot's task model.
    "TaskRunChoice": obj(
        {"model": "s", "harness": "s", "runtime": "s", "reasoning_effort": "s", "set_by": "n", "set_at": "n"}
    ),
    "TaskRunModelResult": obj(
        {
            "task": "s",
            "run_model": {"oneOf": [ref("TaskRunChoice"), {"type": "null"}]},
            "readiness": {"type": ["object", "null"]},
            "warning": "s",
        }
    ),
    "TaskMoveResult": obj(
        {
            "batch_id": "s",
            "moved": items("s"),
            "skipped": items(obj({"id": "s", "code": "s", "reason": "s"})),
        }
    ),
    "TaskBulkResult": obj(
        {
            "dry_run": "b",
            "matched": "i",
            "cap": "i",
            "changed": "i",
            "unchanged": "i",
            "skipped": "i",
            "tasks": obj(
                {
                    "changed": items(obj({"id": "s", "number": {"type": ["integer", "null"]}, "title": "s"})),
                    "skipped": items(
                        obj(
                            {
                                "id": "s",
                                "number": {"type": ["integer", "null"]},
                                "title": "s",
                                "code": "s",
                                "reason": "s",
                            }
                        )
                    ),
                },
            ),
        }
    ),
    "RanOn": obj(
        {
            "segments": items(
                obj({"model": "n", "provider": "n", "runtime": "n", "harness": "n", "effort": "n", "family": "n"})
            ),
            "task_model": {
                "type": ["object", "null"],
                "description": "For a turn in a task's own session on a bot "
                "with a task model: `source` is task (it ran on the task model), fallback (the task "
                "model could not run, so the bot's own model did) or check (a check subtask kept its "
                "check model), and `note` says it in plain words",
            },
            "fallback": {"type": ["string", "null"], "description": "The fallback harness that ran the turn, if any"},
            "family": {
                "type": ["string", "null"],
                "description": "The maker of the model that did the work, from "
                "the model catalog, when every counted segment agrees; null is unknown (mixed, Cursor "
                "auto, OpenRouter, an unlisted model, nothing counted). Never the bot's configured model.",
            },
        }
    ),
    "TaskRun": obj(
        {"run": "s", "bot": "s", "started_at": "n", "finished_at": "n", "outcome": "n", "ran_on": ref("RanOn")}
    ),
    # How a reader's comment reaches the task's bot (backend/delivery.py): the default way and a default defer's
    # deadline, whether they may steer or queue (wake it), whether its harness takes a steer, and whether they may set
    # the goal.
    "CommentDelivery": obj(
        {
            "default": {"type": ["string", "null"], "enum": ["queue", "defer", None]},
            "default_for": "n",
            "steer": "b",
            "queue": "b",
            "steers": "b",
            "goal": "b",
        },
        required=["default", "default_for", "steer", "queue", "steers", "goal"],
    ),
    # The way a send went (backend/delivery.py): steer, queue or defer, and a defer's deadline (null: none).
    "SentAs": obj(
        {"delivery": {"type": "string", "enum": ["steer", "queue", "defer"]}, "until": "n"},
        required=["delivery", "until"],
    ),
    "TaskDetail": obj(
        {
            "task": ref("Task"),
            "events": "a",
            "children": "a",
            # The parent ticket (null: none, or the reader may not read it), with its label and stage
            "parent": {
                "oneOf": [obj({"id": "s", "title": "s", "label": "n", "step": "n"}), {"type": "null"}],
            },
            "comments": items(ref("Message")),
            "messages": items(ref("Message")),
            "has_more": "b",
            "can_comment": "b",
            "can_set_run_model": "b",
            "mover": "b",
            "runs": items(ref("TaskRun")),
            "run_model": {"oneOf": [ref("TaskRunChoice"), {"type": "null"}]},
            "comment_delivery": {"oneOf": [ref("CommentDelivery"), {"type": "null"}]},
        },
        required=["task", "events", "children", "comments", "messages", "has_more"],
        actors=ACTORS,
    ),
    "TaskEvent": obj(
        {
            "seq": "i",
            "actor": "n",
            "at": "s",
            "id": "s",
            "kind": {"type": "string", "description": "The last change kind for this task in the batch"},
            "kinds": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
                "description": "All change kinds for this task in the batch; files invalidates files and linked pages",
            },
            "task": ref("Task"),
            "was": {
                "type": "object",
                "properties": {"owner": {"type": ["string", "null"]}, "status": {"type": ["string", "null"]}},
                "description": "The task's owner and status before this batch moved them, only to a viewer who could "
                "read the task then and can now (owner null where the viewer may not see it)",
            },
            "gone": "b",
        },
        required=["seq", "actor", "at", "id", "kind", "kinds"],
    ),
    "TaskReviewAskView": obj({"questions": "a", "who": "n", "by": "s"}),
    "ReviewAnswer": {
        "oneOf": [
            obj({"target": "o", "answers": "o", "other": "n", "dismiss": "b", "by": "s", "at": "s"}),
            obj({"by": "s", "text": "s", "at": "s"}),
        ]
    },
    "VersionReview": obj(
        {
            "note": "n",
            "comment_id": "n",
            "ask": {"oneOf": [ref("TaskReviewAskView"), {"type": "null"}]},
            "answers": items(ref("ReviewAnswer")),
        }
    ),
    "TaskFileVersion": obj(
        {
            "n": "i",
            "size": "i",
            "mime": "s",
            "sha256": "s",
            "created_at": "s",
            "by": "s",
            "comment_id": "n",
            "note": "n",
            "ask": {"oneOf": [ref("TaskReviewAskView"), {"type": "null"}]},
            "answers": items(ref("ReviewAnswer")),
            "url": "s",
            "poster_url": "n",
            "thumb_url": "n",
            **{k: {"type": ["integer", "null"]} for k in ("width", "height", "duration_ms")},
            "media_state": "n",
        }
    ),
    "TaskFileView": obj(
        {
            "id": "s",
            "name": "s",
            "mime": "s",
            "current_version": "i",
            "archived": "b",
            "versions": items(ref("TaskFileVersion")),
        }
    ),
    "TaskFileList": obj({"files": items(ref("TaskFileView"))}),
    "TaskFileResult": obj({"file_id": "s", "version": "i", "file": "o", "link": "s"}),
    "TaskComments": obj({"comments": items(ref("Message"))}),
    "TaskAnswers": obj({"answers": items(ref("ReviewAnswer"))}),
    "TaskAnswerResult": obj(
        {
            "comment": ref("Message"),
            "answer": ref("ReviewAnswer"),
            "comments": items(ref("Message")),
            "woke": "b",
            "delivered": "b",
        }
    ),
    # `warning`: the bot it went to is paused or quarantined; the comment is kept and runs when it is back.
    # `held`: a bot's comment to a bot waits for that bot's next run; it says how to wake it now.
    "CommentResult": obj(
        {
            "comment": ref("Message"),
            "comments": items(ref("Message")),
            "woke": "b",
            "delivered": "b",
            "warning": "s",
            "held": "s",
            "goal": "o",
            "sent_as": ref("SentAs"),
        },
        required=["comment", "comments", "woke"],
    ),
    "InboundTaskResult": obj({"task": {"oneOf": [obj({"id": "s"}), {"type": "null"}]}, "created": "b", "changed": "b"}),
    "UpdateList": obj({"updates": "a", "unread": "i", "next_before": "n"}, required=["updates", "unread"]),
    "UpdateArchiveResult": obj(
        {"updated": "i", "archived": "b", "unread": "i"}, required=["updated", "archived", "unread"]
    ),
    "ChangelogEntry": obj(
        {
            "id": "s",
            "title": "s",
            "bullets": items({"type": "string"}),
            "area": "s",
            "kind": {"type": "string", "enum": ["product", "activity"]},
            "shipped_at": "s",
            "published_at": "s",
            "unread": "b",
            "version": "s",
            "source": "s",
            "url": "s",
            "task_id": "s",
        },
        required=["id", "title", "bullets", "area", "kind", "shipped_at"],
    ),
    "ChangelogList": obj(
        {
            "entries": items(ref("ChangelogEntry")),
            "unread_count": "i",
            "current_version": "s",
            "next_offset": {"type": ["integer", "null"]},
            "drafts": "a",
            "can_review": "b",
            "can_add": "b",
        }
    ),
    "ChangelogReadResult": obj({"unread_count": "i"}),
    "Unread": obj(
        {"unread": "i"},
        meetings_pending={
            "type": "integer",
            "description": "Pending meetings filed for the caller; never part of Needs you",
        },
    ),
    "NeedsYouItem": NEEDS_ITEM,
    "NeedsYou": {
        "anyOf": [
            obj(
                {"actor": "s", "items": items(ref("NeedsYouItem"))},
                required=["actor", "items"],
                count={"type": "integer", "description": "Items in the whole list, whatever page this is"},
                by_kind={
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "description": "Items of each kind in the whole list, including unread pages",
                },
                next={
                    "type": ["string", "null"],
                    "description": "With limit or cursor: pass as cursor for the next page; null on the last page",
                },
                bots={
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "description": "With limit or cursor: how many items in the whole list each bot is behind",
                },
                stages={
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "description": "With limit or cursor: how many asks, tasks and mentions in the whole list wait in "
                    "each stage (by stage id), for a stage's review",
                },
                restart={
                    "type": "boolean",
                    "description": "The cursor was from an older version: this page is the first",
                },
                snoozed_until={
                    "type": ["string", "null"],
                    "description": "When the first ask the caller snoozed for themselves comes back: read the list again "
                    "then (POST /api/v2/needs-you/snooze)",
                },
            ),
            obj({"actor": "s", "count": "i"}),
        ]
    },
    "FlowOpinions": obj(
        {
            "opinions": items(
                obj(
                    {
                        "id": "s",
                        "state": {"type": "string", "enum": ["ready", "pending", "none"]},
                        "pick": "s",
                        "line": "s",
                        "note": "s",
                        "score": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                    }
                )
            )
        }
    ),
    "FlowPinged": obj(
        {
            "pinged": items(obj({"id": "s", "name": "s"})),
            "recent": items(obj({"id": "s", "name": "s"})),
        }
    ),
    "TaskBrief": obj(
        {
            "id": "s",
            "number": {"type": ["integer", "null"]},
            "title": "s",
            "status": {"type": "string", "enum": ["open", "done", "closed"]},
            "version": "i",
            "pipeline_id": "n",
            "stage_id": "n",
            "owner_id": "n",
            "assignee_id": "n",
            "requester_id": "n",
            "due_at": "n",
            "turn": {"oneOf": [ref("TaskWait"), {"type": "null"}]},
            "late": {"oneOf": [obj({"since": "s", "level": {"enum": ["told", "person"]}}), {"type": "null"}]},
            "stuck": {
                "type": "boolean",
                "description": "Present and true when the never-stuck ladder brought it to a person (stuck or late)",
            },
            "snoozed": "b",
            "pushed_past": "b",
            "stage_since": "n",
            "on_person": "b",
        },
        [
            "id",
            "number",
            "title",
            "status",
            "version",
            "pipeline_id",
            "stage_id",
            "owner_id",
            "assignee_id",
            "requester_id",
            "due_at",
            "turn",
            "late",
            "snoozed",
            "pushed_past",
            "stage_since",
            "on_person",
        ],
        blocked_by=items(obj({"id": "n", "number": {"type": ["integer", "null"]}})),
        blocks=items(obj({"id": "s", "number": {"type": ["integer", "null"]}})),
    ),
    "GranolaStatus": obj(
        {
            "mode": {"type": "string", "enum": ["account", "api_key", "off"]},
            "connected": "b",
            "email": "n",
            "plan_hint": {"type": ["string", "null"], "enum": ["free", "paid", None]},
            "last_synced_at": "n",
            "last_error": "n",
            "imported_count": "i",
            "needs_signin": "b",
            "syncing": "b",
            "skipped": "i",
        },
        skip_reasons=GRANOLA_SKIP_REASONS,
        last_error_detail=GRANOLA_ERROR_DETAIL,
    ),
    "GranolaSignIn": obj(
        {
            "state": "s",
            "mode": "s",
            "connected": "b",
            "email": "n",
            "plan_hint": "n",
            "last_synced_at": "n",
            "last_error": "n",
            "imported_count": "i",
            "needs_signin": "b",
            "syncing": "b",
            "skipped": "i",
        },
        skip_reasons=GRANOLA_SKIP_REASONS,
        last_error_detail=GRANOLA_ERROR_DETAIL,
    ),
    "GranolaDevice": obj(
        {
            "user_code": "s",
            "verification_uri": "s",
            "verification_uri_complete": "s",
            "expires_in": "i",
            "interval": "i",
        },
        required=["user_code", "verification_uri", "expires_in", "interval"],
    ),
    "GranolaSync": obj(
        {"state": {"type": "string", "enum": ["syncing", "recent", "off", "needs_signin"]}, "last_synced_at": "n"}
    ),
    "GranolaDisconnect": obj({"ok": "b"}),
    "MeetingSearch": obj({"results": "a", "next_offset": {"type": ["integer", "null"]}, "mode": "s"}),
    "ReviewedMeeting": obj(
        {"id": "s", "title": "s", "review_state": {"type": "string", "enum": ["pending", "live", "dismissed"]}}
    ),
    "MeetingReviewList": obj({"meetings": items(ref("ReviewedMeeting")), "count": "i", "pending_count": "i"}),
    "MyRecorder": obj(
        {
            "version": {
                "type": "string",
                "description": "Your most recently used copy's version; empty when none was seen",
            },
            "last_seen_at": "n",
            "recent": {"type": "boolean", "description": "A copy was seen in the last 30 days"},
            "latest": {"type": "string", "description": "The newest release's version; empty when unknown"},
            "update_available": "b",
            "download_url": "s",
        }
    ),
    "MeetingReviewPreferences": obj(
        {
            "auto_share": {"type": ["boolean", "null"]},
            "review_default": {"type": "string", "enum": ["review", "auto"]},
            "effective_auto_share": "b",
        }
    ),
    "InternalDocRow": obj(
        {"id": "s", "path": "s", "title": "s", "updated_at": "s", "updated_by_id": "s", "locked": "b", "version": "i"},
        updated_by_name={"type": "string", "description": "Display name of updated_by"},
    ),
    "InternalDoc": obj(
        {
            "id": "s",
            "path": "s",
            "title": "s",
            "body": "s",
            "locked": "b",
            "version": "i",
            "created_by_id": "s",
            "created_at": "s",
            "updated_by_id": "s",
            "updated_at": "s",
            "archived": "b",
        },
        created_by_name={"type": "string"},
        updated_by_name={"type": "string", "description": "Display name of updated_by"},
    ),
    "InternalDocList": obj({"docs": items(ref("InternalDocRow")), "next_cursor": "n"}),
    "InternalDocResult": obj({"doc": ref("InternalDoc")}),
    "InternalDocVersion": obj(
        {"version": "i", "title": "s", "path": "s", "actor": "s", "created_at": "s", "note": "s"},
        actor_name={"type": "string"},
        size={"type": "integer"},
        current={"type": "boolean"},
    ),
    "InternalDocVersions": obj({"doc": "s", "versions": items(ref("InternalDocVersion"))}),
    "InternalDocVersionResult": obj(
        {
            "version": obj(
                {"version": "i", "title": "s", "path": "s", "body": "s", "actor": "s", "created_at": "s", "note": "s"},
                actor_name={"type": "string"},
            )
        }
    ),
    "LinkedDoc": obj(
        {
            "id": "s",
            "title": "s",
            "url": "s",
            "kind": {"enum": ["website", "google_drive", "google_doc", "notion", "github", "other"]},
            "host": "s",
            "description": "s",
            "added_by": "s",
            "created_at": "s",
            "updated_at": "s",
        },
        added_by_name={"type": "string"},
    ),
    "LinkedDocList": obj({"linked": items(ref("LinkedDoc"))}),
    "LinkedDocResult": obj({"linked": ref("LinkedDoc")}),
    "DocSource": obj(
        {
            "id": "s",
            "title": "s",
            "url": "s",
            "kind": "s",
            "provider": "s",
            "description": "s",
            "added_by": "s",
            "repo": "s",
            "reference": "s",
            "reference_override": "s",
            "path": "s",
            "status": {
                "enum": ["link_only", "queued", "preparing", "updating", "ready", "empty", "failed", "access_needed"]
            },
            "last_checked_at": "n",
            "doc_count": "i",
            "skipped": "i",
            "error_code": "s",
        }
    ),
    "DocSourceList": obj({"sources": items(ref("DocSource")), "next_cursor": "n"}),
    "DocSourceResult": obj({"source": ref("DocSource")}),
    "SourceDocumentList": obj(
        {
            "source": ref("DocSource"),
            "documents": items(obj({"id": "s", "title": "s", "path": "s", "commit": "s"})),
            "next_cursor": "n",
        }
    ),
    "ExternalHit": obj(
        {
            "type": {"enum": ["external"]},
            "id": "s",
            "title": "s",
            "path": "s",
            "section": "s",
            "excerpt": "s",
            "score": "f",
            "trust": {"enum": ["untrusted_external"]},
            "source_id": "s",
            "source_title": "s",
            "source_url": "s",
            "commit": "s",
            "last_checked_at": "n",
            "citation_url": "s",
        },
        collection={"type": "string"},
    ),
    "ExternalDocResult": obj(
        {
            "doc": obj(
                {
                    "id": "s",
                    "type": {"enum": ["external"]},
                    "title": "s",
                    "path": "s",
                    "content": "s",
                    "trust": {"enum": ["untrusted_external"]},
                    "source_id": "s",
                    "source_title": "s",
                    "source_url": "s",
                    "commit": "s",
                    "last_checked_at": "n",
                    "citation_url": "s",
                    "reference_notice": "s",
                }
            )
        }
    ),
    "DocsSearchResults": obj(
        {
            "results": items(
                {
                    "oneOf": [
                        ref("ExternalHit"),
                        obj(
                            {
                                "type": {"enum": ["internal"]},
                                "id": "s",
                                "path": "s",
                                "title": "s",
                                "excerpt": "s",
                                "score": "f",
                            },
                            required=["type", "id", "path", "title", "excerpt", "score"],
                            section={"type": "string"},
                            anchor={"type": "string"},
                            collection={"type": "string"},
                        ),
                        obj(
                            {
                                "type": {"enum": ["linked"]},
                                "id": "s",
                                "title": "s",
                                "url": "s",
                                "kind": "s",
                                "description": "s",
                                "score": "f",
                            }
                        ),
                        obj(
                            {
                                "type": {"enum": ["manual"]},
                                "id": "s",
                                "title": "s",
                                "path": "s",
                                "url": "s",
                                "excerpt": "s",
                                "score": "f",
                            },
                            required=["type", "id", "title", "path", "url", "excerpt", "score"],
                            section={"type": "string"},
                            anchor={"type": "string"},
                            collection={"type": "string"},
                        ),
                    ]
                }
            )
        }
    ),
    "DocSearch": obj({"query": "s", "results": "a", "has_more": "b", "mode": "s"}),
    "Storage": obj(
        {
            "mode": {"type": "string", "enum": ["local", "s3"]},
            "bucket": "s",
            "region": "s",
            "files": "i",
            "bytes": "i",
            "copy": obj({"done": "i", "total": "i", "failed": "i"}),
        }
    ),
    "TurnCounts": obj(
        {
            "running": "i",
            "starting": "i",
            "waiting": "i",
            "queued": "i",
            "start_seconds": "i",
            "waits": items(obj({"reason": "s", "count": "i", "oldest": "s"})),
        }
    ),
    "Health": obj(
        {"audience": "s", "checks": "a", "attention": "i", "checked": "s"},
        computers=items(obj({"id": "s", "turn_counts": ref("TurnCounts"), "turn_summary": "s"}, required=["id"])),
        stalled_runs=items(
            obj(
                {
                    "run": "s",
                    "bot": "s",
                    "stalled": "b",
                    "stalled_at": "s",
                    "last_event": {"type": ["string", "null"]},
                    "last_event_at": {"type": ["string", "null"]},
                    "stall_minutes": "i",
                }
            )
        ),
        required=["audience", "checks", "attention", "checked"],
        storage={**ref("Storage"), "description": "Read-only file storage usage and copy progress; Team owners only"},
        stuck={
            "type": "array",
            "items": {"type": "object"},
            "description": "Bots with queued work that is not starting, or left in setup for over a day (the caller may "
            "read them): bot, name, queued, oldest, computer, reason (not_ready, computer_offline, no_computer, "
            "slow, limit, updating, restarting, setup, or a reason the claim holds the work: draining, maintaining, "
            "lane_held, uncertain, scoped, bot_limit, cooling_off, run_hold, budget, backoff, "
            "readiness_unknown, "
            "sign_in_rejected, not_isolated, "
            "isolation_unknown, profile, config, check_model, unreadable), why, drain_progress (counts of running "
            "and handed-back turns on update holds), since (setup only: when the "
            "bot was added), and fix (label plus href/tab, login, move or finish; null unless the caller "
            "administers bots, or for setup may manage the bot: finish is POST /api/v2/bots/{bot}/onboarded)",
        },
        on_fallback={
            "type": "array",
            "items": {"type": "object"},
            "description": "Bots whose latest run in the last day ran on something other than what they are set to "
            "(the caller may read them): bot, harness (what ran it), model (the model that did the work, or null), "
            "waiting_for_sign_in (the runtime it is set to, when the Gateway ran it because that runtime is not signed "
            "in on its computer, else null), set_to {runtime, model}, line (one plain sentence), changed_at (when the "
            "unbroken run of such turns began)",
        },
        limited={
            "type": "array",
            "items": {"type": "object"},
            "description": "Bots a usage limit holds now (the caller may read them): bot, since",
        },
    ),
    "BotFile": obj(
        {
            "id": "s",
            "bot": "s",
            "title": "s",
            "kind": "s",
            "mime": "s",
            "locator": "s",
            "scope": "s",
            "version": "i",
            "state": "s",
            "synced": "b",
            "size": {"type": ["integer", "null"]},
            "name": "n",
            "thumb_blob_id": {
                "type": ["string", "null"],
                "description": "set when an image has a small preview at /api/v2/files/{id}/thumb",
            },
            "open": {
                "type": ["object", "null"],
                "description": "{type: tico|external, url}: "
                "a Tico route, or the provider's address; null when nothing can be opened",
            },
            "provider": "s",
            "provider_label": "s",
            "note": "s",
            "source": "s",
            "task_id": "n",
            "task_title": "n",
            "working": "b",
            "github_url": "n",
            "actor": "n",
            "action": "n",
            "first_activity_at": "s",
            "last_activity_at": "s",
            "archived": "b",
        },
        required=[
            "id",
            "bot",
            "title",
            "kind",
            "locator",
            "scope",
            "version",
            "state",
            "synced",
            "open",
            "working",
            "last_activity_at",
        ],
    ),
    "BotFileList": obj(
        {
            "bot": "s",
            "files": items(ref("BotFile")),
            "total": "i",
            "next_cursor": "n",
            "has_more": "b",
            "can_manage": "b",
        },
        required=["bot", "files", "total", "next_cursor", "has_more", "can_manage"],
        actors=ACTORS,
    ),
    "BotFileMentions": obj(
        {
            "bot": "s",
            "files": {
                "type": "object",
                "additionalProperties": ref("BotFile"),
                "description": "{name as sent, trimmed: the file}; a name that is not exactly one file the caller may "
                "see (none, two or more, or one they may not read) is absent",
            },
        },
        required=["bot", "files"],
    ),
    "BotFileResult": obj({"file": "o", "created": "b"}, required=["file"]),
    "FileActivity": obj({"file": "s", "activity": "a"}, actors=ACTORS),
    "FileVersions": obj({"file": "s", "versions": "a"}),
    "Assistant": obj(
        {
            "available": "b",
            "state": "s",
            "bot": "s",
            "name": "s",
            "can_turn_on": "b",
            "room_id": "n",
            "messages": items(ref("Message")),
            "has_more": "b",
            "next_before": "n",
            "execution": {
                "oneOf": [ref("Execution"), {"type": "null"}],
                "description": "The assistant's current run: state, label, "
                "text so far; null when it is not working (show a thinking state while it is)",
            },
            "actions": {
                "type": "object",
                "description": "{action id: proposal} for every card in `messages` "
                "(a message whose refs.action names one is a Confirm / Cancel card)",
            },
            "pending": "a",
            "earlier": {
                "oneOf": [
                    obj({"room_id": "s", "bot": "s", "name": "s"}, required=["room_id", "bot", "name"]),
                    {"type": "null"},
                ],
                "description": "The owner's earlier chat with the Strategy Owner, read-only (GET "
                "/api/v2/conversations/{room_id}/messages); null for everyone else, or none",
            },
        },
        required=[
            "available",
            "state",
            "bot",
            "name",
            "can_turn_on",
            "room_id",
            "messages",
            "has_more",
            "next_before",
            "execution",
            "actions",
            "pending",
            "earlier",
        ],
        actors=ACTORS,
    ),
    "Librarian": obj(
        {"available": "b", "state": "s", "bot": "s", "name": "s", "can_turn_on": "b"},
        required=["available", "state", "bot", "name", "can_turn_on"],
    ),
    "DocsAsked": obj(
        {
            "conversation_id": "s",
            "message_id": "s",
            "results": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Matching internal, linked and manual docs, as GET /api/v2/docs/search?collection=all",
            },
        },
        required=["conversation_id", "message_id", "results"],
    ),
    "DocsConversations": obj(
        {
            "conversations": items(
                obj({"id": "s", "title": "s", "created_at": "s", "last_message_at": "n", "closed_at": "n"})
            ),
            "next_offset": {"type": ["integer", "null"]},
        }
    ),
    "DocsReopened": obj({"conversation_id": "s"}),
    "LibrarianResearchFiled": obj({"task_id": "s", "bot": "s", "kind": "s", "target": "s"}),
    "LibrarianMarketSetup": obj(
        {
            "can_answer": "b",
            "answers": obj({"sell": "s", "buyers": "s", "competitors": "s", "watch": "s"}),
            "step": "i",
            "skipped": "b",
            "task": {"type": ["object", "null"]},
        }
    ),
    "LibrarianSetup": obj(
        {
            "available": "b",
            "docs": obj({"show": "b", "empty": "b", "skipped": "b", "task": {"type": ["object", "null"]}}),
            "market": ref("LibrarianMarketSetup"),
        }
    ),
    "AssistantSent": obj({"message": ref("Message")}, required=["message"]),
    "AssistantActionResult": obj(
        {
            "action": {
                "type": "object",
                "description": "id, summary, method, path, body, status "
                "(pending, running, done, failed, canceled, expired), result",
            },
            "message_id": "s",
            "shown_once": {
                "type": "object",
                "description": "Confirm only, on a card that makes a "
                "one-time value (a new computer's code): {code, expires, operator}, "
                "in this answer alone and never stored on the card",
            },
        },
        required=["action"],
    ),
    # Goals and KPIs
    "Goal": obj(
        {
            "id": "s",
            "title": "s",
            "owner_id": "s",
            "parent_id": "n",
            "body": "s",
            "status": {
                "type": ["string", "null"],
                "description": "red, yellow, green, gray (no data), done, dropped; null while it has no colour yet",
            },
            "status_note": "s",
            "status_by_id": "n",
            "status_at": "n",
            "status_source": {
                "type": ["string", "null"],
                "description": "auto: worked out by the Strategy Owner; person: set by hand and kept until handed back",
            },
            "suggest_status": {
                "type": ["string", "null"],
                "description": "On a colour set by hand: the colour the Strategy Owner would choose, when it differs",
            },
            "suggest_note": "n",
            "suggest_at": "n",
            "rank": {"type": ["integer", "null"]},
            "created_at": "s",
            "created_by_id": "s",
            "updated_at": "s",
        },
        required=[
            "id",
            "title",
            "owner_id",
            "parent_id",
            "body",
            "status",
            "status_note",
            "status_by_id",
            "status_at",
            "status_source",
            "rank",
            "created_at",
            "created_by_id",
            "updated_at",
        ],
        owner_name={"type": "string"},
        status_by_name={"type": "string"},
        why={
            "type": "string",
            "description": "One line tying it to the company's vision; empty: not tied to the vision",
        },
        why_by_id={"type": ["string", "null"]},
        why_at={"type": ["string", "null"]},
    ),
    "KpiLink": obj(
        {
            "id": "s",
            "goal_id": "s",
            "kind": {"enum": ["none", "improve", "maintain"]},
            "baseline": NUM_N,
            "baseline_at": "n",
            "target": NUM_N,
            "deadline": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "min": NUM_N,
            "max": NUM_N,
        },
        required=["goal_id", "kind"],
    ),
    "KpiReadingView": obj(
        {
            "id": "s",
            "value": "f",
            "period_start": "s",
            "period_end": "s",
            "collected_at": "s",
            "quality": {"enum": ["measured", "estimate", "partial"]},
            "evidence": "s",
            "note": "s",
            "source": "s",
            "actor_id": {
                "type": ["string", "null"],
                "description": "The person who recorded it; null for a bot's automatic reading",
            },
            "definition_version": "i",
            "supersedes": "n",
        },
        required=["id", "value", "period_end", "quality", "definition_version"],
        kpi_id={"type": "string"},
        measured_at={"type": "string"},
        created_at={"type": "string"},
        superseded_by={"type": ["string", "null"], "description": "The reading that corrected this one, if any"},
        actor_name={"type": "string"},
    ),
    "Kpi": obj(
        {
            "id": {"type": "string", "description": "A UUID, or auto:<bot>:<metric> for a bot's automatic KPI"},
            "slug": "n",
            "name": "s",
            "definition": "s",
            "unit": "s",
            "direction": {"enum": ["up", "down", "range"]},
            "cadence": {"enum": ["daily", "weekly", "monthly"]},
            "owner_id": "s",
            "source_note": "s",
            "definition_version": "i",
            "created_at": "n",
            "created_by_id": "n",
            "updated_at": "n",
            "archived_at": {"type": ["string", "null"]},
            "archived_by_id": {"type": ["string", "null"]},
            "auto": "b",
            "latest": {"oneOf": [ref("KpiReadingView"), {"type": "null"}]},
            "readings": "i",
            "freshness": {
                "enum": ["fresh", "stale", "missing", "archived"],
                "description": "stale: one period missed; missing: never read or two missed. archived: a retained goal link, not counting. Never zero",
            },
            "spark": {"type": "array", "items": {"type": "number"}, "description": "The last values, oldest first"},
            "status": {
                "enum": ["green", "yellow", "red", "gray", "none", None],
                "description": "Against the target on the link (or a range); gray is stale or missing data; none is fresh with no target; null is an archived goal link with no colour",
            },
            "reason": "s",
        },
        required=[
            "id",
            "name",
            "unit",
            "direction",
            "cadence",
            "owner_id",
            "definition_version",
            "auto",
            "latest",
            "readings",
            "freshness",
            "spark",
            "status",
            "reason",
        ],
        link=ref("KpiLink"),
        target_label={"type": "string", "description": "'→ 65% by Dec 31' or 'range 40–60'"},
        expected={"type": ["number", "null"], "description": "Where the straight line from baseline to target is now"},
        owner_name={"type": "string"},
    ),
    "GoalCheckinView": obj(
        {
            "id": "s",
            "goal_id": "s",
            "kpi_id": "n",
            "created_at": "s",
            "author": "s",
            "sender_id": "s",
            "body": "s",
            "signal": {"type": ["string", "null"], "enum": ["on_track", "at_risk", "off_track", None]},
        },
        required=["id", "goal_id", "created_at", "author", "sender_id", "body", "signal"],
        author_name={"type": "string"},
        sender_name={"type": "string"},
    ),
    "GoalProposalView": obj(
        {
            "id": "s",
            "kind": {"enum": ["goal_wording", "goal_kpi", "kpi_definition", "kpi_target", "flag", "goal_why"]},
            "goal_id": "n",
            "kpi_id": "n",
            "payload": "o",
            "reason": "s",
            "proposed_by": "s",
            "proposed_at": "s",
            "status": {"enum": ["pending", "confirmed", "rejected"]},
            "decided_by": "n",
            "decided_at": "n",
            "decision_note": "s",
            "result": {"type": ["object", "null"]},
        },
        required=["id", "kind", "goal_id", "kpi_id", "payload", "reason", "proposed_by", "proposed_at", "status"],
        goal_title={"type": ["string", "null"]},
        kpi_name={"type": ["string", "null"]},
        card={
            "type": "object",
            "description": "What its card says beside the payload: goal_title, kpi_name; "
            "an Add a KPI suggestion's measure {name, definition, source_note, unit, direction, existing} "
            "and target_label; a new target's now_label and target_label; a new definition's or wording's "
            "changes [{label, now, proposed}]",
        },
        may_decide={"type": "boolean", "description": "Whether the caller may confirm or reject it"},
        may_reason={
            "type": "boolean",
            "description": "Whether the caller may still add the reason it "
            "was turned down: they turned it down, and it has none",
        },
    ),
    "GoalView": obj(
        {},
        required=["kpis", "children", "chain", "tasks", "events", "checkins", "proposals"],
        kpis=items(ref("Kpi")),
        children=items(ref("Goal")),
        chain=items(ref("Goal")),
        tasks=items({"type": "object"}),
        events=items({"type": "object"}),
        checkins=items(ref("GoalCheckinView")),
        proposals=items(ref("GoalProposalView")),
    ),
    "GoalResult": obj({"goal": {}}),
    "CompanyResult": obj(
        {
            "company": obj(
                {"vision": "s", "description": "s", "updated_at": "n", "updated_by": "n", "updated_by_name": "s"}
            ),
            "may_edit": {"type": "boolean", "description": "The owner on their own click, never an admin or a bot"},
        }
    ),
    "CompanyHistory": obj(
        {
            "events": items(
                obj(
                    {
                        "id": "s",
                        "created_at": "s",
                        "actor": "s",
                        "actor_name": "s",
                        "field": {"enum": ["vision", "description"]},
                        "old": "s",
                        "new": "s",
                    }
                )
            )
        }
    ),
    "GoalList": obj(
        {"goals": items(ref("Goal"))},
        required=["goals"],
        owner={"type": "string"},
        chain=items(ref("Goal")),
        reports=items(ref("Goal")),
        company=items(ref("Goal")),
    ),
    "GoalTreeRow": obj(
        {},
        required=["kpis", "open_tasks"],
        kpis=items(ref("Kpi")),
        open_tasks={"type": "integer"},
        checkin={"oneOf": [ref("GoalCheckinView"), {"type": "null"}], "description": "The latest check-in"},
    ),
    "GoalTree": obj(
        {
            "goals": {},
            "owners": {
                "type": "object",
                "description": "{actor: {kind: company|person|bot, id, name}} for every owner",
            },
            "unaligned": {"type": "object", "description": "{actor: open tasks that serve no goal}"},
            "other_kpis": items(ref("Kpi")),
            "proposals": items(ref("GoalProposalView")),
        },
        required=["goals", "owners", "unaligned", "other_kpis", "proposals"],
    ),
    "StrategyBotView": obj(
        {
            "name": "s",
            "regular_checks": {
                "type": "boolean",
                "description": "Whether the KPI pass and the automatic goal colours run",
            },
            "level": {"type": "integer", "enum": [1, 2, 3]},
            "level_chosen": {
                "type": "boolean",
                "description": "Whether the owner chose the level; if not, it is the default",
            },
            "levels": items(
                obj(
                    {"level": "i", "name": "s", "sentence": "s", "ready": "b"},
                    required=["level", "name", "sentence", "ready"],
                )
            ),
            "level_changed_by": "s",
            "level_changed_by_name": "s",
            "level_changed_at": "s",
            "level_history": items(
                obj(
                    {"level": "i", "was": "i", "by": "s", "by_name": "s", "at": "s"},
                    required=["level", "was", "by", "at"],
                )
            ),
            "asked_today": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
                "description": "{person actor: items the Strategy Owner put in their Needs you today (UTC)}",
            },
            "level3_offer": ref("StrategyBotLevel3Offer"),
        },
        required=["name", "regular_checks", "level", "levels"],
    ),
    "StrategyBotLogEntry": obj(
        {
            "id": "s",
            "created_at": "s",
            "kind": {
                "type": "string",
                "enum": [
                    "decision",
                    "cost",
                    "publish",
                    "send",
                    "bots",
                    "target",
                    "vision",
                    "instructions",
                    "person_task",
                ],
            },
            "reason": "s",
            "goal_id": "s",
            "state": {"type": "string", "enum": ["done", "proposed", "undone"]},
            "proposal_task": {"type": "string", "description": "The owner's Needs you item, when filed"},
            "note": "s",
            "done": items({"type": "object", "additionalProperties": True}),
            "left": items({"type": "object", "additionalProperties": True}),
            "undoable": "b",
            "undone_at": "s",
            "undone_by": "s",
            "undo": {
                "type": "object",
                "additionalProperties": True,
                "description": "What Undo restored and what it kept: {restored, kept, text}",
            },
        },
        required=["id", "created_at", "kind", "state", "done", "left", "undoable"],
    ),
    "StrategyBotLog": obj(
        {"items": items(ref("StrategyBotLogEntry")), "next_before": "s", "can_undo": "b"},
        required=["items", "can_undo"],
    ),
    "StrategyBotLevel3Offer": obj(
        {"streak": "i", "needed": "i", "due": "b", "last_offered_at": "s"}, required=["streak", "needed", "due"]
    ),
    "StrategyBotLevel3OfferResult": obj({"level3_offer": ref("StrategyBotLevel3Offer")}),
    "StrategyBotMergeResult": obj(
        {
            "task": {
                "type": "object",
                "description": "The merged item: the Strategy Owner's task, waiting on the person",
            },
            "originals": items(
                obj(
                    {"task_id": "s", "title": "s", "asker": "s", "asker_name": "s", "state": "s", "note": "s"},
                    required=["task_id", "asker", "state"],
                )
            ),
        }
    ),
    "GoalsNeedsYou": obj(
        {
            "actor": "s",
            "items": items(
                {
                    "type": "object",
                    "required": ["id", "kind", "title", "body", "subject", "created_at", "actions"],
                    "additionalProperties": True,
                    "properties": {
                        "id": {"type": "string", "description": "<kind>:<what it is about>"},
                        "title": {"type": "string"},
                        "body": {"type": ["string", "null"]},
                        "subject": {
                            "oneOf": [
                                obj({"type": {"enum": ["proposal", "kpi", "strategy"]}, "id": "s"}),
                                {"type": "null"},
                            ],
                            "description": "The one thing it is about; null for an ask standing for several things",
                        },
                        "created_at": {
                            "type": ["number", "null"],
                            "description": "When its task was filed; null while it is not filed yet",
                        },
                        "actions": items({"enum": list(needs_shape.ACTIONS)})
                        | {
                            "description": "The writes that settle it: "
                            + "; ".join(f"`{k}` {v}" for k, v in needs_shape.ACTIONS.items())
                        },
                        "kind": {"enum": ["vision", "kpi_red", "recovery", "why", "proposal", "kpi_stale"]},
                        "goal_id": {"type": "string"},
                        "options": {
                            "type": "array",
                            "description": "kind recovery: each option in plain words, "
                            "{label, pick, actions: [{text, note}]}",
                            "items": {"type": "object"},
                        },
                        "goals": {
                            "type": "array",
                            "description": "kind why: the goals whose proposed Why lines wait on you",
                            "items": {"type": "object"},
                        },
                        "goal_title": {"type": ["string", "null"]},
                        "kpi_id": {"type": "string"},
                        "kpi_name": {"type": ["string", "null"]},
                        "reason": {"type": "string"},
                        "kpis": {
                            "type": "array",
                            "description": "kind kpi_stale: each of the person's measures whose reading is out of "
                            "date, in the one ask: {kpi_id, kpi_name, freshness, reason}",
                            "items": {"type": "object"},
                        },
                        "proposal": {"type": "object"},
                        "task": {
                            "type": "object",
                            "required": ["id"],
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "status": {"type": "string"},
                            },
                        },
                    },
                }
            ),
        },
        required=["actor", "items"],
    ),
    "GoalRefreshView": obj({"changed": "a", "suggested": "a", "checked": "i"}),
    "GoalCheckins": obj({"goal_id": "s", "checkins": items(ref("GoalCheckinView"))}),
    "GoalCheckinResult": obj({"checkin": ref("GoalCheckinView")}),
    "GoalKpiResult": obj({"kpi": ref("Kpi")}),
    "GoalProposalList": obj({"proposals": items(ref("GoalProposalView"))}),
    "GoalProposalResult": obj({"proposal": ref("GoalProposalView")}),
    "KpiList": obj({"kpis": items(ref("Kpi"))}, required=["kpis"]),
    "KpiDetail": obj(
        {
            "kpi": ref("Kpi"),
            "links": {},
            "readings": items(ref("KpiReadingView")),
            "definitions": items(
                {
                    "type": "object",
                    "description": "version, ts, actor, name, definition, unit, direction, cadence, source_note; newest first",
                }
            ),
            "checkins": items(ref("GoalCheckinView")),
            "proposals": items(ref("GoalProposalView")),
            "may_edit": "b",
            "may_log": "b",
        },
        required=["kpi", "links", "readings", "definitions", "checkins", "proposals", "may_edit", "may_log"],
    ),
    "KpiReadings": obj(
        {"kpi": {"type": "object", "description": "The KPI record"}, "readings": items(ref("KpiReadingView"))}
    ),
    "KpiReadingResult": obj({"reading": ref("KpiReadingView")}),
    "BotKpis": obj({"bot": "s", "kpis": items(ref("Kpi"))}),
    "UsageFigures": obj(
        {
            "runs": "i",
            "input_tokens": {
                "type": "integer",
                "description": "Uncached input tokens, without cache writes (a run whose runner did not count cache "
                "writes apart includes them)",
            },
            "cached_tokens": {"type": "integer", "description": "Input tokens read from the provider's cache"},
            "cache_write_tokens": {
                "type": "integer",
                "description": "Input tokens written to the provider's prompt cache (Claude, Cursor, pi); 0 for runtimes "
                "that report none (Codex, Gemini, Antigravity, Muse Code) and for runs from before they were counted apart",
            },
            "output_tokens": "i",
            "est_cost_usd": {
                "type": ["number", "null"],
                "description": "List-price estimate for runs billed by API key; "
                "null when every such run used a model with no list price",
            },
            "subscription_equiv_usd": {
                "type": "number",
                "description": "What runs on a ChatGPT or Claude sign-in would "
                "cost through the API: not money spent, never added to est_cost_usd",
            },
            "unpriced_runs": {"type": "integer", "description": "Runs with tokens on a model with no list price"},
            "low_estimate_runs": {
                "type": "integer",
                "description": "Priced runs whose estimate may be low: the model "
                "charges more for long prompts and the runner did not report every request's tokens",
            },
        }
    ),
    "Usage": obj(
        {
            "from": "s",
            "to": "s",
            "prices_as_of": {"type": "string", "description": "The date the price table was read"},
            "group": {"enum": ["bot", "day", "routine", "task", "harness", "model", "effort", "subscription"]},
            "department": "n",
            "totals": ref("UsageFigures"),
            "rows": items(
                {
                    "type": "object",
                    "description": "By bot: bot, name, department; by day: day; by routine: "
                    "routine (null for runs that came from none), title, bot, name; by task: value and name (its title, "
                    "'A private task', or null and 'Chats and other runs' for runs with none). Every row has the "
                    "usage figures and `share`, its part of the total (estimate plus API-equivalent)",
                    "additionalProperties": True,
                }
            ),
            "dimensions": {"type": "object", "description": "Available dimension filters, scoped to visible runs"},
            "dimension_labels": {
                "type": "object",
                "description": "Display labels keyed by immutable filter value; subscription names include their Computer",
            },
            "departments": items({"type": "string"}),
            "bot": "s",
            "name": "s",
            "daily": items({"type": "object", "additionalProperties": True}),
            "routines": items({"type": "object", "additionalProperties": True}),
            "tasks": items(
                {
                    "type": "object",
                    "description": "?bot only: its top 10 tasks by spend, as group=task names them (value and name), plus runs with no task, "
                    "each with the usage figures and `share`, its part of the bot's spend",
                    "additionalProperties": True,
                }
            ),
            "tasks_more": {
                "type": "object",
                "description": "?bot only: `count`, the tasks past the top 10, and their usage figures summed",
                "additionalProperties": True,
            },
            "models": items(
                {
                    "type": "object",
                    "description": "?bot only: every model it used, value (null when not recorded) and name, with "
                    "the usage figures and `share`",
                    "additionalProperties": True,
                }
            ),
        },
        required=["from", "to", "prices_as_of", "totals"],
    ),
    "UsageLimitView": obj(
        {
            **{p + "_usd": NUM_N for p in PERIODS},
            "source": {"type": "object", "description": "Where each cap comes from: bot, company or null"},
            **{p + "_spent": NUM_N for p in ("hour", "day", "week", "month")},
            "percent": {"type": "integer", "description": "The highest share of one of the bot's own limits reached"},
            "blocked": {
                "enum": [*PERIODS, "quarterly", "yearly", "budget", None],
                "description": "Set while a "
                "limit is met, the bot's own, a pool's or a Finance budget's (quarterly, yearly, or budget "
                "for a one-time budget): the bot takes no new job",
            },
            "blocked_by": {
                "type": ["object", "null"],
                "description": "Whose limit holds the bot: scope (bot, company, "
                "group:<id> or budget), name (a pool's or budget's), period, until for a budget (\"\" "
                "when it never turns over), and spent and limit (a pool's to the owner and "
                "administrators only)",
                "additionalProperties": True,
            },
            **{"own_" + p + "_usd": NUM_N for p in PERIODS},
            "may_edit": "b",
        }
    ),
    "UsageDefaultView": obj({"default": obj({**{p + "_usd": NUM_N for p in PERIODS}, "count_subscription": "b"})}),
    "UsagePoolView": obj(
        {
            **{p + "_usd": NUM_N for p in PERIODS},
            **{p + "_spent": NUM_N for p in ("30m", "hour", "day", "week", "month")},
            "percent": "i",
            "blocked": {"enum": [*PERIODS, None]},
        }
    ),
    "UsagePoolLimit": obj(
        {"scope": {"type": "string", "description": "company or group:<id>"}, "limit": ref("UsagePoolView")}
    ),
    "UsageLimits": obj(
        {
            "default": obj({**{p + "_usd": NUM_N for p in PERIODS}, "count_subscription": "b"}),
            "may_edit_default": "b",
            "pools": {
                "type": ["object", "null"],
                "description": "Owner and administrators: company (UsagePoolView) and "
                "groups, {id: UsagePoolView with name and parent}. A lead who is "
                "not an administrator: company null and only the groups it leads and those below, each "
                "with may_edit true",
                "additionalProperties": True,
            },
            "bots": {"type": "object", "additionalProperties": ref("UsageLimitView")},
        }
    ),
    "BotUsageLimit": obj({"bot": "s", "limit": ref("UsageLimitView")}),
    "QueuePurgeResult": obj(
        {
            "scope": {"type": "string", "description": "company, or group:<id>"},
            "cancelled": "i",
            "kept_people": "i",
            "still_running": "i",
        }
    ),
    "Token": obj({"access_token": "s", "token_type": "s", "expires_in": "i", "idle_timeout": "i", "person": "s"}),
    "Revoked": obj({"revoked": "b"}),
}


def _merge(*schemas, **more):
    """One object schema from several: the properties of each, and what each requires."""
    props, required = {}, set()
    for schema in schemas:
        props.update(schema["properties"])
        required.update(schema.get("required", []))
    props.update(more)
    return {"type": "object", "properties": props, "required": sorted(required), "additionalProperties": True}


# A goal with what the page adds to it, a link with what says how it is doing: one object each, so a client sees every field.
SCHEMAS.update(
    {
        "BrowserExit": obj(
            {
                "sharing": "b",
                "bots": items(_t("s")),
                "daily_cap_bytes": "i",
                "computer": obj({"id": "s", "label": "s"}),
                "used_today_bytes": {"type": ["integer", "null"]},
                "may_change": "b",
                "may_turn_off": "b",
            }
        ),
        "BrowserExitUsage": obj(
            {
                "rows": items(obj({"day": "s", "bot": "s", "host": "s", "bytes": "i", "tunnels": "i"})),
                "days": items(obj({"day": "s", "bytes": "i"})),
                "next": {"type": ["integer", "null"]},
            }
        ),
        "BotBrowserExitView": obj(
            {
                "browser_exit": {
                    "type": ["object", "null"],
                    "properties": {"runner_id": {"type": "string"}, "name": {"type": "string"}},
                }
            },
            choices=items(obj({"runner_id": "s", "label": "s", "owner_id": "s"})),
            may_change=_t("b"),
            required=["browser_exit"],
        ),
    }
)
SCHEMAS["GoalResult"]["properties"]["goal"] = _merge(SCHEMAS["Goal"], SCHEMAS["GoalView"])
SCHEMAS["GoalTree"]["properties"]["goals"] = items(_merge(SCHEMAS["Goal"], SCHEMAS["GoalTreeRow"]))
SCHEMAS["KpiDetail"]["properties"]["links"] = items(
    _merge(
        SCHEMAS["KpiLink"],
        obj(
            {
                "goal_title": "s",
                "goal_owner": "s",
                "goal_status": "n",
                "target_label": "s",
                "status": "s",
                "reason": "s",
                "expected": NUM_N,
            },
            required=["goal_title", "status"],
        ),
    )
)
# Tags add rich display data beside the existing task label keys.
TAGS["Tags"] = "Tags, metadata, markdown checklists and reusable templates."
SCHEMAS.update(
    {
        'MeetingTaskProposalAccepted': obj(
            {'item': obj({'id': 's', 'proposed_task_id': 'n', 'result_ref': 'n', 'status': 's'}), 'pushed': 'b'}
        ),
        "Tag": obj(
            {
                "id": "s",
                "key": "s",
                "label": "s",
                "metadata": "o",
                "markdown": "s",
                "is_template": "b",
                "template_id": "n",
                "owner": "n",
                "bot_writers": items("s"),
                "version": "i",
                "created_at": "s",
                "updated_at": "s",
            }
        ),
        "TagList": obj({"tags": items(ref("Tag"))}),
        "TagResult": obj({"tag": ref("Tag")}),
        "TagDetail": obj(
            {
                "tag": ref("Tag"),
                "editable": "b",
                "tasks": items(ref("Task")),
                "next_offset": {"type": ["integer", "null"]},
            }
        ),
    }
)
SCHEMAS["Task"]["properties"]["tags"] = items(ref("Tag"))
# A task's documents (backend/task_documents.py), on GET /api/v2/tasks/{id} only: newest change first, each version
# readable at /api/v2/files/{file_id}?v=<n>. Written with POST /api/v2/tasks/{id}/files and `document`.
SCHEMAS["TaskDocument"] = obj(
    {
        "name": {
            "type": "string",
            "description": ", ".join(DOCUMENT_NAMES) + " (the names Tico knows), or a bot's own",
        },
        "file_id": "s",
        "version": "i",
        "by": "s",
        "at": "s",
        "versions": items(obj({"n": "i", "by": "s", "at": "s", "note": "n"})),
    }
)
SCHEMAS["Task"]["properties"]["documents"] = items(ref("TaskDocument"))
SCHEMAS["Message"]["properties"]["source"] = ref("MessageSource")
SCHEMAS["TaskDetail"]["properties"]["events"] = items(obj({"source": ref("MessageSource")}, required=["source"]))

# The named events automations and outgoing webhooks listen for (backend/event_catalog.py).
TAGS["Events"] = "The catalog of named events (docs/routines.md)."
SCHEMAS.update(
    {
        "EventType": obj(
            {
                "name": "s",
                "group": "s",
                "description": "s",
                "label": "s",
                "version": "i",
                "example": "o",
                "aliases": items({"type": "string"}),
                "sourceless": "b",
                "outbound": "b",
            },
            required=["name", "group", "description", "label", "version", "example"],
        ),
        "EventTypeList": obj({"items": items(ref("EventType"))}, required=["items"]),
    }
)

# Failed-run back-off and run budgets (backend/run_failures.py, backend/turn_budgets.py).
SCHEMAS.update(
    {
        "BotBudgetState": obj(
            {
                "bot": "s",
                "turns_per_hour": "i",
                "turns_per_day": "i",
                "own": {"type": "object"},
                "team": {"type": "object"},
                "runs": {"type": "object"},
                "held": {"type": ["object", "null"]},
                "run_hold": {"type": ["object", "null"]},
                "revision": "i",
                "released": "b",
            }
        ),
        "BotReleased": obj({"bot": "s", "released": {"type": "object"}}),
        "TeamBudgetState": obj({"turns_per_hour": "i", "turns_per_day": "i", "defaults": {"type": "object"}}),
        "TaskCapRow": obj(
            {
                "sender": "s",
                "receiver": "s",
                "per_day": "i",
                "count": "i",
                "closed": "i",
                "updated_at": "s",
                "updated_by": "s",
            }
        ),
        "TaskCapProposal": obj(
            {
                "id": "s",
                "sender": "s",
                "receiver": "s",
                "per_day": "i",
                "status": "s",
                "proposed_by": "s",
                "created_at": "s",
            }
        ),
        "TaskCaps": obj({"caps": items(ref("TaskCapRow")), "proposals": items(ref("TaskCapProposal"))}),
        "TaskCapWrite": obj({"cap": {"type": "object"}, "approval": "s", "status": "s"}),
    }
)

# The audit trail (backend/audit_log.py).
SCHEMAS.update(
    {
        "AuditPage": {
            "type": "object",
            "required": ["rows", "next"],
            "properties": {
                "rows": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "next": {"type": ["string", "null"]},
            },
        }
    }
)

# Finance (backend/finance_routes.py): every read is cut to the caller's own view.
DOC_CONTACT = {
    "type": ["object", "null"],
    "properties": {"id": {"type": ["string", "null"]}, "name": {"type": ["string", "null"]}},
}
DOC_PAYMENT = {
    "anyOf": [
        obj(
            {
                "id": "n",
                "status": {
                    "type": "string",
                    "enum": [
                        "queued",
                        "sending",
                        "awaiting_bank_approval",
                        "submitted",
                        "settled",
                        "returned",
                        "failed",
                        "canceled",
                        "unknown",
                        "denied",
                        "expired",
                        "withdrawn",
                    ],
                },
                "amount": "i",
                "on": "s",
                "error": "n",
            }
        ),
        {"type": "null"},
    ],
    "description": "A bill's latest payment: its state, the day it was asked for and why it ended, if it did. No id "
    "when the scheduled ask ended before a payment existed (denied, expired, withdrawn or failed)",
}
TAGS["Finance"] = (
    "Budgets, the P&L, expenses, bills, invoices, contacts, reports and the work inbox, as far as the "
    "caller sees; Finance actions, committed or sent to one person for approval."
)
SCHEMAS.update(
    {
        "FinanceBudgetState": obj(
            {
                "spent_from": {
                    "type": "string",
                    "enum": ["books", "platform", "mixed"],
                    "description": "Where an ad budget's actual comes from: the books, the ad platform's daily spend "
                    "or both (by month)",
                },
                "as_of": {
                    "type": ["string", "null"],
                    "description": "The ad platforms' last import, when their spend counts",
                },
                "reported": {
                    "type": ["integer", "null"],
                    "description": "Cents: an ad budget's platform spend shown beside actual and never in it (its vendor "
                    "is not the platform's own contact, so nothing is swapped); null otherwise",
                },
            }
        ),
        "FinanceBudgetRow": obj({"id": "s", "name": "s", "platform": "s", "state": ref("FinanceBudgetState")}),
        "FinanceBudgets": obj(
            {
                "budgets": items(ref("FinanceBudgetRow")),
                "more": "b",
                "on": "s",
                "view_version": "i",
            }
        ),
        "FinanceBrief": obj(
            {
                "lines": items("s"),
                "as_of": "s",
                "changed_since": "n",
                "version": "i",
            },
            required=["lines", "as_of", "version"],
        ),
        "FinanceReport": obj({"view_version": "i"}),
        "FinancePnl": obj(
            {
                "start": "s",
                "end": "s",
                "period": "s",
                "basis": "s",
                "view": "s",
                "team": "n",
                "teammate": "n",
                "budget_as_of": "n",
                "columns": items(
                    obj(
                        {
                            "start": "s",
                            "end": "s",
                            "label": "s",
                            "state": {"type": "string", "enum": ["past", "current", "future"]},
                        }
                    )
                ),
                "filter": obj(
                    {
                        "teams": items({"type": "string"}),
                        "teammates": items({"type": "string"}),
                        "who": {"type": "string", "enum": ["both", "bots", "people"]},
                        "unattributed": "b",
                    }
                ),
                "rows": items(
                    obj(
                        {
                            "account_id": "s",
                            "code": "s",
                            "name": "s",
                            "type": "s",
                            "parent_id": "s",
                            "private": "b",
                            "section": {"type": "string", "enum": ["revenue", "cost_of_revenue", "operating", "other"]},
                            "actual": {"type": ["array", "null"], "items": {"type": "integer"}},
                            "total": {"type": ["integer", "null"]},
                            "budget": {"type": ["array", "null"], "items": {"type": ["integer", "null"]}},
                            "projection": {"type": ["array", "null"], "items": {"type": "integer"}},
                        }
                    )
                ),
                "income": items({"type": "integer"}),
                "expense": items({"type": "integer"}),
                "net": items({"type": "integer"}),
                "excluded": "b",
                "view_version": "i",
                "teammates": items(
                    obj({"teammate": "s", "kind": "s", "spent": "i", "budget": {"type": ["integer", "null"]}})
                ),
            },
            required=[
                "start",
                "end",
                "period",
                "basis",
                "view",
                "columns",
                "rows",
                "income",
                "expense",
                "net",
                "excluded",
                "view_version",
            ],
        ),
        "FinanceBalanceSheet": obj(
            {
                "as_of": "s",
                "currency": "s",
                "scoped": "b",
                "view_version": "i",
                "closed_through": "n",
                "totals": obj({"asset": "i", "liability": "i", "equity": "i"}),
                **{
                    k: items(
                        obj(
                            {
                                "account_id": "n",
                                "code": "s",
                                "name": "s",
                                "subtype": "s",
                                "parent_id": "n",
                                "balance": "i",
                                "entry_sources": items({"type": "string"}),
                                "sources": items(
                                    obj(
                                        {
                                            "money_account_id": "s",
                                            "name": "s",
                                            "provider": "s",
                                            "last4": "n",
                                            "provider_balance": {"type": ["integer", "null"]},
                                            "balance_as_of": "n",
                                            "due_out": obj({"count": "i", "amount": "i"}),
                                        }
                                    )
                                ),
                            }
                        )
                    )
                    for k in ("assets", "liabilities", "equity")
                },
            }
        ),
        "FinanceTransactions": obj(
            {
                "rows": items(
                    obj(
                        {
                            "kind": {"type": "string", "enum": ["feed", "entry"]},
                            "id": "s",
                            "date": "s",
                            "amount": "i",
                            "currency": "s",
                            "counterparty": "s",
                            "description": "s",
                            "status": "s",
                            "pending": "b",
                            "card_id": "n",
                            "entry_id": "n",
                            "reconciled": "b",
                            "money_account": {"type": ["object", "null"]},
                            "lines": items({"type": "object", "additionalProperties": True}),
                            "account_id": "s",
                            "account": "s",
                            # a bank or card row: {id, name} (its vendor); an entry's line: the vendor's id
                            "vendor": {"anyOf": [{"type": "string"}, obj({"id": "s", "name": "s"})]},
                            "vendor_name": "s",
                            "memo": "s",
                            "source": "s",
                            "reversed": "b",
                            "tags": "o",
                        },
                        required=["kind", "id", "date", "amount", "currency"],
                    )
                ),
                "next": "n",
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceCards": obj(
            {
                "cards": items(
                    obj(
                        {
                            "id": "s",
                            "provider": "s",
                            "holder": "s",
                            "nickname": "s",
                            "last4": "n",
                            "kind": "s",
                            "status": "s",
                            "limit": {"type": ["integer", "null"]},
                            "limit_interval": "n",
                            "provider_limit": {"type": ["integer", "null"]},
                            "sync_status": "s",
                            "currency": "s",
                            "money_account_id": "n",
                            "budget": {"type": ["object", "null"]},
                            "allocation": {"type": ["integer", "null"]},
                            "spent": {"type": ["integer", "null"]},
                            "expires_at": "n",
                            "created_at": "s",
                        }
                    )
                ),
                "next": "n",
                "view_version": "i",
            }
        ),
        "FinanceSources": obj(
            {
                "groups": items(
                    obj(
                        {
                            "group": {
                                "type": "string",
                                "enum": [
                                    "banks",
                                    "cards",
                                    "payments",
                                    "payroll",
                                    "bills_invoices",
                                    "tico",
                                    "accounting",
                                    "manual",
                                ],
                            },
                            "name": "s",
                            "count": "i",
                            "rows": items({"type": "object", "additionalProperties": True}),
                        }
                    )
                ),
                "view_version": "i",
            }
        ),
        "FinanceAccess": obj(
            {
                "teams": items(obj({"id": "s", "name": "s", "parent": "n", "lead": "n"})),
                "teammates": items(
                    obj(
                        {
                            "teammate": "s",
                            "kind": "s",
                            "team": "n",
                            "reports_to": "n",
                            "default": {
                                "type": "string",
                                "enum": ["everything", "team_expenses", "own_expenses", "none"],
                            },
                            "default_team": "n",
                            "grants": items({"type": "object", "additionalProperties": True}),
                            "rights": {"type": ["array", "null"], "items": {"type": "string"}},
                            "attribute": {"type": ["boolean", "null"]},
                            "may_grant": "b",
                            "budgets": "i",
                        }
                    )
                ),
                "private_lines": items(
                    obj(
                        {
                            "account_id": "s",
                            "name": "s",
                            "others_see": "s",
                            "full_detail": {"type": ["array", "null"], "items": {"type": "string"}},
                        }
                    )
                ),
                "defaults": obj({"manager": "s", "member": "s", "manager_payroll": "s"}),
                "can_view_as": "b",
                "can_edit_defaults": "b",
                "view_version": "i",
            }
        ),
        "FinanceAction": obj(
            {"status": "s", "kind": "s", "action_id": "n", "code": "s", "reason": "s"}, required=["status", "kind"]
        ),
        "FinanceStatementReview": obj(
            {
                "token": "s",
                "expires_at": "s",
                "money_account_id": "s",
                "file_name": "s",
                "file_format": "s",
                "start": "s",
                "end": "s",
                "card": "b",
                "last4": "n",
                "signs": obj({"flipped": "b", "by": "s"}),
                "new": "i",
                "duplicate": "i",
                "tie": obj(
                    {
                        "opening": "i",
                        "added": "i",
                        "removed": "i",
                        "ends": "i",
                        "statement_says": {"type": ["integer", "null"]},
                        "off_by": "i",
                    }
                ),
                "rows": items(
                    obj(
                        {
                            "id": "s",
                            "date": "s",
                            "description": "s",
                            "amount": "i",
                            "vendor": obj({"id": "s", "name": "s"}),
                            "attribution": {"type": ["object", "null"]},
                            "status": "s",
                        }
                    )
                ),
            },
            required=["token", "expires_at", "tie", "rows"],
        ),
        "FinanceTransfers": obj(
            {
                "transfers": items(
                    obj(
                        {
                            "id": "s",
                            "status": {
                                "type": "string",
                                "enum": ["queued", "submitted", "unknown", "failed", "posted", "canceled"],
                            },
                            "amount": "i",
                            "currency": "s",
                            "memo": "s",
                            "from": obj({"id": "s", "name": "s", "last4": "n"}),
                            "to": obj({"id": "s", "name": "s", "last4": "n"}),
                            "created_by": "s",
                            "created_at": "s",
                            "settled_at": "n",
                            "last_error": "n",
                        },
                        required=["id", "status", "amount", "currency", "from", "to"],
                    )
                ),
                "next_cursor": "n",
            }
        ),
        "FinanceSalesTax": obj(
            {
                "year": "i",
                "currency": "s",
                "rows": items(
                    obj(
                        {
                            "period": "s",
                            "code": "s",
                            "jurisdiction": "s",
                            "taxable_sales": "i",
                            "exempt_sales": "i",
                            "tax_collected": "i",
                            "tax_remitted": "i",
                            "due": "i",
                            "due_on": "n",
                        },
                        required=["period", "code", "taxable_sales", "exempt_sales", "tax_collected", "due"],
                    )
                ),
            }
        ),
        "Finance1099": obj(
            {
                "year": "i",
                "currency": "s",
                "threshold": "i",
                "due_on": "s",
                "rows": items(
                    obj(
                        {
                            "vendor_id": "s",
                            "vendor": "s",
                            "tin_on_file": "b",
                            "cash_paid": "i",
                            "card_paid_excluded": "i",
                            "threshold": "i",
                            "reportable": "b",
                        },
                        required=["vendor_id", "vendor", "cash_paid", "reportable"],
                    )
                ),
            }
        ),
        "FinanceLineAttribution": obj(
            {
                "line_id": "s",
                "source": "s",
                "rule_id": "n",
                "rule_name": "n",
                "set_by": "n",
                "set_at": "n",
                "can_change": "b",
                "tags": "o",
                "amount": "i",
                "splits": items(obj({"teammate": "s", "team": "s", "share_bps": "i", "amount": "i"})),
                "history": items(
                    obj(
                        {
                            "id": "s",
                            "source": "s",
                            "rule_id": "n",
                            "splits": items(obj({"teammate": "s", "team": "s", "share_bps": "i"})),
                            "set_by": "s",
                            "set_at": "s",
                            "superseded_at": "n",
                        }
                    )
                ),
            }
        ),
        "FinanceAttributionRules": obj(
            {
                "rules": items(
                    obj(
                        {
                            "id": "s",
                            "name": "s",
                            "match": obj({"kind": "s", "id": "s", "label": "s"}),
                            "splits": items(obj({"teammate": "s", "team": "s", "share_bps": "i"})),
                            "enabled": "b",
                            "apply_from": "n",
                            "created_by": "s",
                            "updated_at": "s",
                        }
                    )
                ),
                "can_change": "b",
            }
        ),
        "FinanceAttributionDimensions": obj(
            {
                "dimensions": items(
                    obj(
                        {
                            "kind": "s",
                            "id": "s",
                            "name": "s",
                            "lines": "i",
                            "amount": "i",
                            "rule_id": "n",
                            "splits": items(obj({"teammate": "s", "team": "s", "share_bps": "i"})),
                        }
                    )
                ),
                "can_change": "b",
                "view_version": "i",
            }
        ),
        "FinanceMercuryUsers": obj(
            {"users": items(obj({"id": "s", "name": "s", "connection_id": "s", "email": "n", "updated_at": "s"}))}
        ),
        "FinanceAdAccount": obj(
            {
                "id": "s",
                "tool_id": "s",
                "external_id": "s",
                "name": "s",
                "currency": "s",
                "bot_slug": "n",
                "active": "b",
            }
        ),
        "FinanceAdAccounts": obj(
            {
                "tool_id": "s",
                "tool_name": "s",
                "platform": {"type": "string", "enum": ["google_ads", "meta_ads"]},
                "platform_label": "s",
                "import": obj(
                    {
                        "saved": "b",
                        "enabled": "b",
                        "history_from": "s",
                        "account_id": "s",
                        "account_name": "s",
                        "status": {"type": "string", "enum": ["never", "ok", "reconnect", "failed"]},
                        "last_import_at": "n",
                        "last_error": "s",
                        "run_requested": "b",
                        "every_hours": "i",
                    }
                ),
                "slots": items(
                    obj(
                        {
                            "slot": "s",
                            "label": "s",
                            "required": "b",
                            "credential_id": "s",
                            "options": items(obj({"id": "s", "name": "s"})),
                        }
                    )
                ),
                "books_lines": items(obj({"id": "s", "code": "s", "name": "s"})),
                "elsewhere": {
                    "type": ["object", "null"],
                    "description": "One of this tool's ad accounts another enabled import already reads (one import "
                    "per ad account), so this one can't be switched on; null when none",
                    "properties": {
                        "tool_id": {"type": "string"},
                        "tool_name": {"type": "string"},
                        "account": {"type": "string", "description": "The ad account, as its platform shows it"},
                    },
                },
                "month": "s",
                "month_label": "s",
                "accounts": items(
                    obj(
                        {
                            "id": "s",
                            "external_id": "s",
                            "name": "s",
                            "currency": "s",
                            "bot_slug": "n",
                            "active": "b",
                            "month_to_date": "i",
                        }
                    )
                ),
            }
        ),
        "FinanceConnectionResult": obj(
            {"action_id": "n", "kind": "s", "status": "s", "code": "s", "reason": "s", "approver": "n", "result": "o"},
            required=["status", "kind"],
        ),
        "FinanceBillsAddress": obj({"bills_email": "n", "inbox_id": "n"}),
        "FinanceBills": obj(
            {
                "bills": items(ref("FinanceDocumentRow")),
                "next_cursor": "n",
                "open": ref("FinanceOpenTotal"),
                "currency": "s",
                "bills_email": "n",
                "view_version": "i",
            }
        ),
        "FinanceChart": obj(
            {
                "accounts": items(
                    obj(
                        {
                            "id": "s",
                            "number": "s",
                            "name": "s",
                            "type": "s",
                            "subtype": "s",
                            "parent_id": "n",
                            "company_wide": "b",
                            "attributable": items(obj({"team": "s", "teammate": "s"}, required=[])),
                        },
                        required=["id", "number", "name", "type"],
                    )
                ),
                "more": "b",
                "view_version": "i",
            }
        ),
        "FinancePayFrom": obj(
            {
                "accounts": items(
                    obj(
                        {
                            "id": "s",
                            "name": "s",
                            "provider": "s",
                            "last4": "n",
                            "balance": {"type": ["integer", "null"]},
                            "available_balance": {"type": ["integer", "null"]},
                            "balance_as_of": "n",
                        },
                        required=["id", "name"],
                    )
                ),
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceFeedExpected": obj(
            {
                "feed_id": "s",
                "kind": {"type": "string", "enum": ["bill", "invoice"]},
                "amount": "i",
                "date": "s",
                "expected": items(
                    obj(
                        {
                            "settlement_id": "s",
                            "kind": "s",
                            "document_id": "s",
                            "number": "s",
                            "contact_id": "s",
                            "contact_name": "s",
                            "amount": "i",
                            "marked_on": "s",
                        }
                    )
                ),
                "more": "b",
                "view_version": "i",
            }
        ),
        "FinanceInvoices": obj(
            {
                "invoices": items(ref("FinanceDocumentRow")),
                "next_cursor": "n",
                "open": ref("FinanceOpenTotal"),
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceOpenTotal": obj(
            {
                "count": "i",
                "remaining": "i",
                "more": "b",
                "aging": obj({k: "i" for k in ("current", "1_30", "31_60", "61_90", "over_90")}),
            }
        ),
        "FinanceDocumentRow": obj(
            {
                "id": "s",
                "kind": {"type": "string", "enum": ["bill", "invoice"]},
                "number": "s",
                "vendor": DOC_CONTACT,
                "customer": DOC_CONTACT,
                "date": "s",
                "due_date": "n",
                "status": "s",
                "currency": "s",
                "total": "i",
                "paid": "i",
                "remaining": "i",
                "aging": "n",
                "days_late": "i",
                "memo": "s",
                "entry_id": "n",
                "attachments": "i",
                "created_by": "s",
                "created_at": "s",
                "origin": "s",
                "approved_by": "n",
                "approved_at": "n",
                "pay_on": "n",
                "payment": DOC_PAYMENT,
                "sent_at": "n",
                "written_off": "i",
                "payment_link": "n",
                "account": {"type": ["object", "null"], "additionalProperties": True},
                "split": "b",
                "team": "n",
                "paid_on": "n",
                "paid_by": "n",
            },
            required=["id", "kind", "number", "date", "status", "currency", "total", "paid", "remaining"],
        ),
        "FinanceDocument": {
            "allOf": [
                ref("FinanceDocumentRow"),
                obj(
                    {
                        "lines": items(
                            obj(
                                {
                                    "id": "s",
                                    "account": {"type": ["object", "null"], "additionalProperties": True},
                                    "amount": "i",
                                    "description": "s",
                                    "tags": "o",
                                    "taxable": "b",
                                },
                                required=["id", "account", "amount", "description", "tags"],
                            )
                        ),
                        "settlements": items(
                            obj(
                                {
                                    "id": "s",
                                    "from_kind": {
                                        "type": "string",
                                        "enum": ["payment", "feed", "credit", "account", "expected"],
                                    },
                                    "from_id": "n",
                                    "amount": "i",
                                    "settled_on": "s",
                                    "entry_id": "n",
                                    "voided_at": "n",
                                    "created_by": "s",
                                }
                            )
                        ),
                        "attachments": items(obj({"id": "s", "file_id": "s", "created_by": "s", "created_at": "s"})),
                        "actions": items(
                            obj(
                                {
                                    "action_id": "s",
                                    "kind": "s",
                                    "status": "s",
                                    "actor": "s",
                                    "approval_id": "n",
                                    "approval_to": "n",
                                    "rule_id": "n",
                                    "created_at": "s",
                                }
                            )
                        ),
                        "settlement_count": "i",
                        "settlements_more": "b",
                        "attachment_count": "i",
                        "attachments_more": "b",
                        "terms_days": {"type": ["integer", "null"]},
                        "schedule": {"type": ["object", "null"]},
                        "view_version": "i",
                    },
                    required=["lines", "settlements", "attachments", "actions"],
                ),
            ]
        },
        "FinanceContacts": obj(
            {
                "contacts": items(
                    obj(
                        {
                            "id": "s",
                            "name": "s",
                            "kind": {"type": "string", "enum": ["vendor", "customer", "both"]},
                            "emails": items({"type": "string"}),
                            "default_account": {"type": ["object", "null"], "additionalProperties": True},
                            "is_1099": "b",
                            "has_tax_id": "b",
                            "tax_id_last4": "n",
                            "recipient_id": "n",
                            "active": "b",
                            "owed": obj({"bills": "i", "invoices": "i"}, required=[]),
                            "created_at": "s",
                        },
                        required=["id", "name", "kind", "active"],
                    )
                ),
                "next_cursor": "n",
                "view_version": "i",
            }
        ),
        "FinanceInbox": obj(
            {
                "items": items(
                    obj(
                        {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "uncategorized",
                                    "bill_to_enter",
                                    "unmatched_receipt",
                                    "missing_receipt",
                                    "bill_due",
                                    "invoice_overdue",
                                    "budget_near_limit",
                                    "sync_failed",
                                    "period_ready",
                                    "unreconciled",
                                    "bill_email",
                                ],
                            },
                            "id": "s",
                            "label": "s",
                        }
                    )
                ),
                "counts": {"type": "object", "additionalProperties": {"type": "integer"}},
                "more": items({"type": "string"}),
                "next_cursor": "n",
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceReportOf": obj(
            {
                "kind": {
                    "type": "string",
                    "enum": [
                        "pnl",
                        "pnl_by",
                        "balance_sheet",
                        "cash_flow",
                        "budget_vs_actual",
                        "ap_aging",
                        "ar_aging",
                        "spend_by",
                        "campaigns",
                    ],
                },
                "prior": {"type": "object", "additionalProperties": True},
            },
            required=["kind"],
        ),
        "FinanceOrderRow": obj(
            {
                "id": "s",
                "number": "s",
                "contact": obj({"id": "s", "name": "s"}),
                "date": "s",
                "status": "s",
                "currency": "s",
                "total": "i",
                "memo": "s",
            },
            required=["id", "number", "contact", "date", "status", "currency", "total", "memo"],
        ),
        "Estimates": obj(
            {"estimates": items(ref("FinanceOrderRow")), "next_cursor": "n"}, required=["estimates", "next_cursor"]
        ),
        "Locations": obj(
            {"locations": items(ref("Location")), "next_cursor": "n"}, required=["locations", "next_cursor"]
        ),
        "Location": obj(
            {"id": "s", "name": "s", "kind": "s", "parent_id": "n", "address": "o", "archived_at": "n"},
            required=["id", "name", "kind", "address"],
        ),
        "Items": obj({"items": items(ref("Item")), "next_cursor": "n"}, required=["items", "next_cursor"]),
        "ItemOne": obj({"item": ref("Item")}, required=["item"]),
        "Item": obj(
            {
                "id": "s",
                "name": "s",
                "sku": "s",
                "kind": {"type": "string", "enum": ["service", "inventory", "non_inventory", "bundle", "category"]},
                "unit": "s",
                "price": {"type": ["integer", "null"]},
                "currency": "s",
                "archived_at": "n",
                "income_account_id": "n",
                "expense_account_id": "n",
                "asset_account_id": "n",
            },
            required=["id", "name", "sku", "kind", "unit", "currency"],
        ),
        "Projects": obj({"projects": items(ref("Project")), "next_cursor": "n"}, required=["projects", "next_cursor"]),
        "ProjectOne": obj({"project": ref("Project")}, required=["project"]),
        "Project": obj(
            {
                "id": "s",
                "name": "s",
                "customer_id": "n",
                "status": {"type": "string", "enum": ["open", "closed"]},
                "archived_at": "n",
            },
            required=["id", "name", "status"],
        ),
        "FinanceSearch": obj(
            {
                "results": items(
                    obj(
                        {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "entry",
                                    "feed",
                                    "bill",
                                    "invoice",
                                    "contact",
                                    "rule",
                                    "card",
                                    "item",
                                    "location",
                                    "project",
                                    "vendor",
                                ],
                            },
                            "id": "s",
                            "label": "s",
                        }
                    )
                ),
                "next_cursor": "n",
                "currency": "s",
                "view_version": "i",
            }
        ),
        "FinanceUnattributed": obj(
            {
                "lines": items(
                    obj(
                        {
                            "id": "s",
                            "date": "s",
                            "entry_id": "s",
                            "account": obj({"id": "s", "code": "s", "name": "s"}),
                            "amount": "i",
                            "description": "s",
                            "memo": "s",
                            "vendor": obj({"id": "s", "name": "s"}),
                            "source": "s",
                        }
                    )
                ),
                "next_cursor": "n",
                "start": "s",
                "end": "s",
                "as_of": "s",
                "currency": "s",
                "view_version": "i",
            },
            required=["lines", "start", "end", "as_of", "view_version"],
        ),
        "FinanceSubscriptionRow": obj(
            {
                "id": "s",
                "vendor": obj({"id": "s", "name": "s"}),
                "status": "s",
                "cadence": "s",
                "amount": "i",
                "monthly": "i",
                "seats": {"type": ["integer", "null"]},
                "next_charge_on": "n",
                "card": {
                    "type": ["object", "null"],
                    "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                },
                "attribution": items(obj({"to": "s", "share": "f"})),
                "source": "s",
                "as_of": "s",
            },
            required=[
                "id",
                "vendor",
                "status",
                "cadence",
                "amount",
                "monthly",
                "next_charge_on",
                "card",
                "attribution",
            ],
        ),
        "FinanceSubscriptions": obj(
            {"rows": items(ref("FinanceSubscriptionRow")), "monthly_total": "i", "as_of": "s"},
            required=["rows", "monthly_total", "as_of"],
        ),
        "FinanceCampaigns": obj(
            {
                "month": "s",
                "campaigns": items(
                    obj(
                        {
                            "id": "s",
                            "name": "s",
                            "platform": "s",
                            "ad_account": obj({"id": "s", "name": "s"}),
                            "owner": obj({"team": "s", "teammate": "s", "inherited": "b"}),
                            "month": obj(
                                {
                                    "spend": "i",
                                    "clicks": "i",
                                    "conversions": "f",
                                    "per_conversion": {"type": ["integer", "null"]},
                                }
                            ),
                            "as_of": "s",
                        }
                    )
                ),
                "totals": obj(
                    {
                        "spend": "i",
                        "clicks": "i",
                        "conversions": "f",
                        "per_conversion": {"type": ["integer", "null"]},
                    }
                ),
                "as_of": "s",
            },
            required=["month", "campaigns", "totals", "as_of"],
        ),
        "FinanceReceipts": obj(
            {
                "rows": items(
                    obj(
                        {
                            "id": "s",
                            "file": obj({"id": "s", "name": "s"}),
                            "read": obj(
                                {
                                    "amount": {"type": ["integer", "null"]},
                                    "date": {"type": ["string", "null"]},
                                    "vendor": {**obj({"id": "s", "name": "s"}), "type": ["object", "null"]},
                                }
                            ),
                            "match": {
                                **obj(
                                    {
                                        "feed": obj({"id": "s", "date": "s", "amount": "i", "description": "s"}),
                                        "confidence": {"type": ["number", "null"]},
                                    }
                                ),
                                "type": ["object", "null"],
                            },
                            "status": "s",
                            "as_of": "s",
                        }
                    )
                ),
                "next": {"type": ["string", "null"]},
                "as_of": "s",
            },
            required=["rows", "next", "as_of"],
        ),
        "FinanceMetric": obj(
            {
                "name": "s",
                "value": {"type": ["integer", "string", "null"]},
                "unit": {"type": "string", "enum": ["minor", "percent", "months", "count", "days"]},
                "as_of": "s",
                "data_as_of": "n",
                "stale": "b",
                "currency": "s",
                "view_version": "i",
            },
            required=["name", "value", "unit", "as_of"],
        ),
        "FinanceCloseChecklist": obj(
            {
                "month": "s",
                "start": "s",
                "end": "s",
                "status": "s",
                "ready": "b",
                "checks": items(obj({"key": "s", "ok": "b", "label": "s"})),
                "view_version": "i",
            }
        ),
        "FinanceActionKinds": obj(
            {
                "kinds": items(obj({"kind": "s", "words": "s", "live": "b"})),
                "kind": "s",
                "words": "s",
                "right": "s",
                "live": "b",
                "fields": items(obj({"name": "s", "type": "s", "required": "b", "words": "s"})),
            },
            required=[],
        ),
    }
)

# Automations (backend/automations.py).
TAGS["Automations"] = (
    "A trigger and a tree of steps (filter, decide, llm, find, bot_turn, paths, action), saved as versions; "
    "one permission rule for UI, CLI and MCP."
)
_RUN = obj(
    {
        "id": "s",
        "automation_id": "s",
        "version": "i",
        "trigger_ref": "s",
        "started_at": "s",
        "finished_at": {"type": ["string", "null"]},
        "status": {"enum": ["queued", "running", "waiting", "ok", "partial", "failed", "empty", "skipped", "held"]},
        "cost": "f",
        "summary": "n",
        "steps": {
            "type": ["array", "null"],
            "items": {"type": "object"},
            "description": "Each step's status, outputs and cost; null after 30 days",
        },
    },
    required=["id", "automation_id", "version", "status", "steps"],
)
SCHEMAS.update(
    {
        "Automation": obj(
            {
                "id": "s",
                "name": "s",
                "owner_id": "s",
                "enabled": "b",
                "action": {"type": "object", "description": "Assistant Message create: the pending Confirm card"},
                "message_id": "s",
                "needs_confirm": "b",
                "trigger": {"type": "object"},
                "steps": items({"type": "object"}),
                "version": "i",
                "can_edit": "b",
                "can_pause": "b",
                "destinations": {
                    "type": ["array", "null"],
                    "items": {"type": "object"},
                    "description": "Named chains a filter rule's `to` runs (see the automation guide)",
                },
                "spend_cap": {"type": ["object", "null"]},
                "batch": {"type": ["object", "null"]},
                "last_run": {"type": ["object", "null"]},
                "counts": obj({"today": obj({"runs": "i", "dropped": "i"})}),
                "next_run": {"type": ["string", "null"]},
                "next_due_at": {
                    "type": ["string", "null"],
                    "description": "A due-date trigger's next due day (YYYY-MM-DD); next_run is when its run starts",
                },
                "pending": "i",
                "warning": "s",
                "kind": {
                    "type": "string",
                    "description": "The trigger as the overview groups it; system for Tico's own jobs",
                },
                "problems": {
                    "type": "array",
                    "description": "Wake rules that name a step their task type "
                    "does not have (they never fire until changed): rule id, the step named and why",
                    "items": obj({"rule": "s", "step": "s", "why": "s"}),
                },
                "paused": {"type": "boolean", "description": "Off because an earlier run guard paused it"},
                "hold": {
                    **obj(
                        {
                            "rule": {"enum": ["flood", "sustained"]},
                            "since": "s",
                            "held": "i",
                            "dropped": "i",
                            "task": {"type": ["string", "null"]},
                        }
                    ),
                    "description": "Present while the run guard keeps its new runs held, not run, until "
                    "someone releases or drops them; `task` is the Needs-you task",
                },
                "schedule": {"type": "string", "description": "A system job's schedule in words"},
            },
            required=["id", "can_edit"],
        ),
        "AutomationList": obj({"automations": items(ref("Automation")), "next": {"type": ["integer", "null"]}}),
        "AutomationDeleted": obj({"id": "s", "deleted": "b"}),
        "AutomationRun": _RUN,
        "AutomationRunNow": obj({"run": ref("AutomationRun")}),
        "AutomationRuns": obj(
            {
                "runs": items(ref("AutomationRun")),
                "next": {"type": ["string", "null"]},
                "daily": items(obj({"day": "s", "runs": "i", "dropped": "i"})),
            }
        ),
        "AutomationOverviewRuns": obj(
            {
                "runs": items(
                    obj(
                        {
                            "id": "s",
                            "automation_id": "s",
                            "automation_name": "s",
                            "trigger": {
                                "enum": [
                                    "message",
                                    "task_received",
                                    "schedule",
                                    "webhook",
                                    "listening",
                                    "event",
                                    "due",
                                    "system",
                                ]
                            },
                            "owner_id": "s",
                            "started_at": "s",
                            "finished_at": {"type": ["string", "null"]},
                            "status": "s",
                            "cost": "f",
                            "started_by": "s",
                            "outcome": "s",
                            "step_count": {"type": ["integer", "null"]},
                            "count": {"type": "integer", "description": "Quiet system-job runs folded into this row"},
                        },
                        required=["id", "automation_id", "automation_name", "trigger", "started_at", "status"],
                    )
                ),
                "next": {"type": ["string", "null"]},
                "since": "s",
            }
        ),
        "AutomationStats": obj(
            {
                "range": "s",
                "since": "s",
                "until": "s",
                "bucket_seconds": "i",
                "automations": items(
                    obj(
                        {
                            "id": "s",
                            "runs": "i",
                            "failed": "i",
                            "cost": "f",
                            "buckets": items(obj({"ok": "i", "failed": "i"})),
                        }
                    )
                ),
                "totals": obj(
                    {"runs": "i", "failed": "i", "cost": "f", "buckets": items(obj({"ok": "i", "failed": "i"}))}
                ),
            }
        ),
        "AutomationVersions": obj(
            {
                "versions": items(
                    obj(
                        {
                            "n": "i",
                            "actor": "s",
                            "created_at": "s",
                            "status": {"enum": ["active", "pending"]},
                            "summary": "s",
                            "flow": {"type": "object"},
                        },
                        required=["n", "actor", "created_at", "status", "flow"],
                    )
                ),
                "next": {"type": ["integer", "null"]},
            }
        ),
        "AutomationGuide": obj({"text": "s"}),
        "QuestionSets": obj(
            {
                "sets": items(
                    obj(
                        {
                            "id": "s",
                            "summary": "s",
                            "questions": {
                                "type": "object",
                                "description": "Keyed by question id, in judge.py's format",
                            },
                        }
                    )
                )
            }
        ),
        "AutomationTemplates": obj(
            {"templates": items(obj({"id": "s", "name": "s", "destinations": items({"type": "string"})}))}
        ),
        "AutomationTemplate": obj(
            {
                "name": "s",
                "owner": "s",
                "enabled": "b",
                "trigger": "o",
                "steps": items({"type": "object"}),
                "destinations": items({"type": "object"}),
            }
        ),
        "InboxSwitch": obj({"inbox": "s", "send": "b"}),
        "PersonAssistant": obj({"bot": {"type": ["string", "null"]}}),
    }
)
SCHEMAS["AutomationHeld"] = _merge(
    SCHEMAS["Automation"], moved={"type": "integer", "description": "Runs released or dropped"}
)

# Bot profiles (backend/profiles.py).
SCHEMAS.update(
    {
        "ProfileChoice": obj(
            {
                "name": "s",
                "label": "s",
                "description": "s",
                "digest_hour": {"type": ["integer", "null"]},
                "pulls": "s",
                "sweeps": "b",
                "takes": "s",
                "rules": {"type": "array", "items": {"type": "object"}},
            }
        ),
        "ProfileChoices": obj({"profiles": items(ref("ProfileChoice"))}),
        "BotProfileState": obj(
            {"bot": "s", "profile": {"type": "object"}, "wake": {"type": "object"}, "rules": "i", "revision": "i"}
        ),
    }
)

SCHEMAS.update(
    {
        "Repository": obj(
            {
                "full_name": "s",
                "enabled": "b",
                "bot_repo": "b",
                "default_branch": "n",
                "setup_command": "n",
                "setup_source": "n",
                "reachable": "b",
                "last_seen_at": "n",
            }
        ),
        "ProductRepositoryPreview": obj(
            {
                "org": "s",
                "name": "s",
                "repository": "s",
                "visibility": {"enum": ["private"]},
                "auto_init": "b",
                "capability": {"enum": ["available", "missing", "unknown", "not_installed"]},
                "capability_detail": "s",
            }
        ),
        "ProductRepositoryResult": obj(
            {
                "repository": "s",
                "html_url": "s",
                "visibility": {"enum": ["private"]},
                "auto_init": "b",
                "installation_access": {"enum": ["available", "owner_action_required", "unverified"]},
                "note": "s",
            }
        ),
        "RepositoryList": obj(
            {
                "repositories": items(ref("Repository")),
                "new_bot_default": {"enum": ["own", "all"]},
                "github_connected": "b",
                "github_installed": "b",
            }
        ),
        # The workflow options are present only when on; a request's access defaults to write.
        "RepositoryGrant": obj(
            {"full_name": "s", "access": {"enum": ["read", "write"]}, "run_workflows": "b", "edit_workflows": "b"},
            required=["full_name"],
        ),
        "BotRepositories": obj(
            {
                "mode": {"enum": ["own", "all", "chosen"]},
                "all_access": {"enum": ["read", "write"]},
                "chosen": items(ref("RepositoryGrant")),
                "all_repos": items(ref("TickedRepository")),
                "effective": items(ref("RepositoryGrant")),
            }
        ),
        # Only the options that are on are present.
        "TickedRepository": obj(
            {"full_name": "s", "run_workflows": "b", "edit_workflows": "b"}, required=["full_name"]
        ),
    }
)
TAGS['Roadmaps'] = "Company roadmap settings and internal requesters (docs/roadmaps.md)."
SCHEMAS['Task']['properties'].update(
    {
        'visibility': {'enum': ['public', 'private']},
        'public_title': {'type': 'string'},
        'public_summary': {'type': 'string', 'description': 'Separate public text; omitted with brief=true'},
        'size': {'type': ['number', 'null']},
        'requester_count': {'type': 'integer'},
        'external_requesters': {'type': 'integer'},
        'internal_requesters': {'type': 'integer'},
        'important_requesters': {'type': 'integer'},
    }
)

TAGS['Support'] = 'Reports to Tico support (docs/support.md).'

TAGS['Rules'] = (
    "Org rules: what teammates follow (internal) and what someone outside requires (external), with their "
    "due dates and sources."
)
TAGS['Strategy'] = (
    "The team's strategy: a draft the owner edits, numbered published versions, and the bets goals serve."
)

TAGS['Inbound'] = 'Shared company inbound email and SMS for people and agents; reads never consume messages.'
SCHEMAS.update(
    {
        'InboundConnectionCompany': {
            **obj(
                {
                    'company_id': {'type': 'string', 'format': 'uuid'},
                    'name': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                }
            ),
            'additionalProperties': False,
        },
        'InboundCompanyConnection': {
            'oneOf': [
                {
                    **obj(
                        {
                            'connected': {'enum': [False]},
                            'scope': {'enum': ['inbox']},
                            'company': {'$ref': '#/components/schemas/InboundConnectionCompany'},
                            'expires_at': {'type': 'number'},
                        }
                    ),
                    'additionalProperties': False,
                },
                {
                    **obj(
                        {
                            'connected': {'enum': [True]},
                            'scope': {'enum': ['inbox']},
                            'company': {'$ref': '#/components/schemas/InboundConnectionCompany'},
                        }
                    ),
                    'additionalProperties': False,
                },
            ]
        },
        'InboundCursor': obj({'cursor': 'i'}),
        'InboundInbox': obj(
            {
                'inbox_id': 's',
                'kind': 's',
                'name': 's',
                'address': 's',
                'state': 's',
                'audience': {'enum': ['company', 'team']},
                'group_id': {'type': ['string', 'null']},
            }
        ),
        'InboundInboxes': obj(
            {
                'inboxes': items({'type': 'object'}),
                'configured': 'b',
                'last_synced_at': NUM_N,
                'error': 's',
                'gaps': items({'type': 'object'}),
            }
        ),
        'InboundMessages': obj(
            {'messages': items({'type': 'object'}), 'next': {'type': ['integer', 'null']}, 'cursor': 'i'}
        ),
        'InboundPhoneRequests': obj(
            {'phone_requests': items({'type': 'object'}), 'next': {'type': ['string', 'null']}}
        ),
    }
)

TAGS['Triggers'] = (
    'Webhook triggers: an address any sender can post to, how its calls are verified, kept and run again.'
)
TAGS['Tools'] = (
    'Every way the company reaches the outside world in one list, and API tools: their keys per level, who can use each, its checks and calls.'
)
TOOL_STATUS = obj({'tone': {'enum': ['ok', 'wait', 'fail', 'run', 'idle']}, 'text': 's'})
SCHEMAS['ToolIndexRow'] = obj(
    {
        'id': 's',
        'kind': {
            'enum': [
                'api',
                'mailbox',
                'number',
                'address',
                'slack',
                'github',
                'meetings',
                'listening',
                'webhook',
                'declared',
                'website',
            ]
        },
        'name': 's',
        'detail': 's',
        'way': 's',
        'status': TOOL_STATUS,
        'who': items(
            obj(
                {'kind': {'enum': ['bot', 'person', 'group']}, 'id': 's', 'name': 's', 'initials': 's', 'members': 'i'},
                required=['kind', 'id', 'name', 'initials'],
            )
        ),
        'who_total': 'i',
        'needs_you': 'b',
        'href': 's',
        'private': {'type': 'boolean', 'description': 'An API tool whose Who can see it is Only listed'},
        'service': {
            'type': 'string',
            'description': "An API or website tool's service, as stored (a person may set it)",
        },
        'vendor': {
            'type': 'string',
            'description': "Who makes it (google, aws, raygun); Tools lists one row a vendor. Empty for a row that is "
            'no vendor\'s (a mailbox, a number, Tico\'s own)',
        },
        'vendor_name': {'type': 'string', 'description': 'The vendor, by name'},
        'delivery': {
            'enum': ['proxy', 'computer'],
            'description': 'An API tool: proxy when Tico holds its key and adds it to each call',
        },
    },
    required=['id', 'kind', 'name', 'detail', 'way', 'status', 'who', 'who_total', 'needs_you', 'href'],
)
SCHEMAS['ToolIndex'] = obj(
    {
        'rows': items(ref('ToolIndexRow')),
        'counts': obj({'all': 'i', 'needs_you': 'i', 'api': 'i', 'mail': 'i', 'websites': 'i'}),
        'next': 'n',
    }
)
SCHEMAS['ToolVendor'] = obj({'vendor': 's', 'name': 's', 'rows': items(ref('ToolIndexRow'))})
SCHEMAS['ApiToolSettings'] = obj(
    {
        'id': 's',
        'name': 's',
        'service': 's',
        'base_url': 's',
        'auth_scheme': 's',
        'check_path': 's',
        'delivery': {'enum': ['proxy', 'computer']},
        'private': 'b',
        'everyone_reads': {
            'type': 'boolean',
            'description': 'Everyone can read: every bot reads it with no grant, where Read can be held to reads',
        },
        'template': {
            'type': ['object', 'null'],
            'description': 'How its several keys and settings reach the vendor (a known vendor\'s id, or its own): '
            'fields (variable, role: secret, id or setting) and ways (basic, headers, query); null for one key',
        },
    }
)
SCHEMAS['ApiTool'] = obj(
    {
        'tool': ref('ApiToolSettings'),
        'keys': items(
            obj(
                {
                    'id': 's',
                    'name': 's',
                    'preview': 's',
                    'access_level': {'enum': ['read', 'read_write']},
                    'expires_on': 'n',
                    'last_used_at': 'n',
                },
                required=['id', 'name', 'preview', 'access_level'],
            )
        ),
        'settings': items(
            obj({'id': 's', 'name': 's', 'value': 's', 'updated_at': 's'}),
        )
        | {'description': 'Non-secret values (a host, a region), each a variable every run that holds a level gets'},
        'access': items(
            obj(
                {
                    'subject': 's',
                    'kind': {'enum': ['bot', 'person', 'finance']},
                    'name': 's',
                    'initials': 's',
                    'level': {'enum': ['read', 'read_write']},
                    'last_used_at': 'n',
                    'fixed': {
                        'type': 'boolean',
                        'description': 'Finance reads this key; it goes when the source is disconnected, not here',
                    },
                },
                required=['subject', 'kind', 'name', 'initials', 'level', 'last_used_at'],
            )
        ),
        'checks': items(
            obj({'ok': 'b', 'status_code': {'type': ['integer', 'null']}, 'detail': 's', 'checked_at': 's'})
        ),
        'recent': items({'type': 'object'}),
        'status': TOOL_STATUS,
        'can_manage': 'b',
        'private': 'b',
        'can_set_audience': 'b',
        'audience': {
            'anyOf': [ref('AccessAudience'), {'type': 'null'}],
            'description': 'Only to whoever may change it; null is Everyone',
        },
        'seen_by': items(
            obj(
                {
                    'subject': 's',
                    'kind': {'enum': ['person', 'group', 'bot', 'finance']},
                    'id': 's',
                    'name': 's',
                    'why': {'enum': ['Owner', 'Listed', 'Can use it']},
                }
            )
        )
        | {'description': 'Only to whoever may change it: who can see an Only listed tool, and why'},
        'mcp': {
            'anyOf': [
                obj(
                    {
                        'url': 's',
                        'transport': {'enum': ['http', 'sse']},
                        'auth': {'enum': ['token', 'oauth']},
                        'header': 's',
                        'tools': {'type': ['integer', 'null']},
                        'read_only': 'i',
                        'names': items('s'),
                        'listed_at': 'n',
                        'signed_in_by': 'n',
                        'signed_in_at': 'n',
                        'client': {'enum': ['registered', 'given', None]},
                    }
                ),
                {'type': 'null'},
            ],
            'description': 'A remote MCP server (Tools > Add > MCP server): its address, how it signs in, who signed '
            'in, and the tools its last check listed; null for an API tool',
        },
        'everyone_reads': obj({'on': 'b', 'enforceable': 'b'})
        | {
            'description': 'Every bot reads the tool with no grant while `on` (backend/default_read.py); never where '
            'Read cannot be held to reads (`enforceable` false), whatever the switch says'
        },
    },
    required=['tool', 'keys', 'access', 'checks', 'recent', 'status', 'can_manage', 'private', 'can_set_audience'],
)
SCHEMAS['ApiToolActivity'] = obj(
    {
        'rows': items(
            obj(
                {
                    'id': 's',
                    'kind': {'enum': ['call', 'use']},
                    'actor': 's',
                    'result': {'enum': ['sent', 'refused', 'failed', 'use']},
                    'at': 's',
                }
            )
        ),
        'next': 'n',
    }
)
# Website tools (backend/website_tools.py): a site signed in to through a browser, and its accounts. No answer carries a
# password or a 2FA key; the 2FA code goes only to a signed-in person who uses the account.
WEBSITE_WHO = obj({'subject': 's', 'kind': {'enum': ['person', 'bot']}, 'id': 's', 'name': 's', 'initials': 's'})
SCHEMAS['WebsiteToolCreated'] = obj({'id': 's'})
SCHEMAS['WebsiteDeleted'] = obj({'deleted': 'b', 'id': 's'})
SCHEMAS['WebsiteAccountSaved'] = obj({'id': 's'})
# One row of an account's Who can use it: a person, a bot or (to an administrator) a granted group with its member count.
# `via` names the granted groups a person or bot granted directly is also in.
WEBSITE_ACCESS = obj(
    {
        'subject': 's',
        'kind': {'enum': ['person', 'bot', 'group']},
        'id': 's',
        'name': 's',
        'initials': 's',
        'members': 'i',
        'via': items(_t('s')),
    },
    required=['subject', 'kind', 'id', 'name', 'initials'],
)
SCHEMAS['WebsiteAccountAccessSet'] = obj({'subject': 's', 'on': 'b', 'access': items(WEBSITE_ACCESS)})
SCHEMAS['WebsiteToolGroups'] = items(obj({'id': 's', 'name': 's', 'members': 'i', 'actors': items(_t('s'))}))
SCHEMAS['WebsiteCode'] = obj({'code': 's', 'expires_in': 'i'})
SCHEMAS['WebsiteToolSettings'] = obj(
    {'id': 's', 'name': 's', 'kind': {'enum': ['website']}, 'sites': items(_t('s')), 'central_only': 'b'}
)
WEBSITE_ACTIVITY_ROW = obj(
    {
        'id': 's',
        'at': 's',
        'who': WEBSITE_WHO,
        'verb': {'enum': ['open', 'fill', 'copy', 'code', 'needs_you', 'control', 'sign_in', 'download', 'upload']},
        'page': 's',
        'account': {'enum': ['shared', 'own']},
        'where': {'enum': ['central', 'own']},
        'result': {'enum': ['sent', 'refused', 'failed', 'use']},
    }
)
SCHEMAS['WebsiteToolActivity'] = obj({'rows': items(WEBSITE_ACTIVITY_ROW), 'next': 'n'})
SCHEMAS['WebsiteTool'] = obj(
    {
        'tool': ref('WebsiteToolSettings'),
        'status': obj({'label': 's', 'tone': {'enum': ['ok', 'waiting', 'fail', 'neutral']}}),
        'accounts': items(
            obj(
                {
                    'id': 's',
                    'kind': {'enum': ['shared', 'own']},
                    'username': 's',
                    'has_password': 'b',
                    'has_totp': 'b',
                    'all_bots': 'b',
                    'holder': {'anyOf': [WEBSITE_WHO, {'type': 'null'}]},
                    'access': items(WEBSITE_ACCESS),
                    'session': obj(
                        {
                            'state': {'enum': ['needs_you', 'in_use', 'unknown']},
                            'where': {'type': ['string', 'null'], 'enum': ['central', 'own', None]},
                            'computer': 'n',
                            'by': {'anyOf': [WEBSITE_WHO, {'type': 'null'}]},
                        },
                        required=['state'],
                    ),
                    'can_use': 'b',
                    'can_manage': 'b',
                },
                # `username` is left out for a bot, a run or a computer.
                required=[
                    'id',
                    'kind',
                    'has_password',
                    'has_totp',
                    'all_bots',
                    'holder',
                    'access',
                    'session',
                    'can_use',
                    'can_manage',
                ],
            )
        ),
        'host': {
            'anyOf': [obj({'runner_id': 's', 'label': 's', 'online': 'b'}), {'type': 'null'}],
            'description': 'The company browser host, null when none is set',
        },
        'recent': items(WEBSITE_ACTIVITY_ROW),
        'can_manage': 'b',
        'private': 'b',
        'can_set_audience': 'b',
        'audience': {
            'anyOf': [ref('AccessAudience'), {'type': 'null'}],
            'description': 'Only to whoever may change it; null is Everyone',
        },
        'seen_by': items(
            obj(
                {
                    'subject': 's',
                    'kind': {'enum': ['person', 'group', 'bot', 'finance']},
                    'id': 's',
                    'name': 's',
                    'why': {'enum': ['Owner', 'Listed', 'Can use it']},
                }
            )
        )
        | {'description': 'Only to whoever may change it: who can see an Only listed tool, and why'},
    },
    required=['tool', 'status', 'accounts', 'host', 'recent', 'can_manage', 'private', 'can_set_audience'],
)
BROWSER = '/api/v2/website-tools/{tool_id}/accounts/{account_id}/browser'
SCHEMAS['BrowserOpened'] = obj({'ok': 'b', 'where': {'enum': ['central', 'own']}})
SCHEMAS['BrowserControlState'] = obj({'ok': 'b', 'control': 'b'})
SCHEMAS['BrowserInputDone'] = obj({'ok': 'b'})
TAGS['Webhook relay'] = (
    'Addresses for triggers through Tico Gateway: they keep deliveries while this server is off (backend/hook_relay.py).'
)
TAGS['Sleep when idle'] = (
    'A server Tico hosts stops after quiet hours and starts again on a webhook, a routine or a sign-in (backend/sleep_idle.py).'
)
TAGS['Screening'] = 'Prompt-injection screening of outside text at the doors it comes in by (backend/screening.py).'
TAGS['Notices'] = (
    'Notices from Tico Team: the ones that came through the Gateway relay and the current one from the release check.'
)
SCHEMAS['NoticeList'] = obj(
    {
        'notices': items(
            obj(
                {
                    'id': 's',
                    'kind': 's',
                    'body': 's',
                    'url': 's',
                    'sent_at': 's',
                    'received_at': 's',
                    'source': {'enum': ['relay', 'latest']},
                }
            )
        )
    }
)
SCHEMAS.update(
    {
        'SleepWhenIdle': obj(
            {
                'sleep': obj(
                    {
                        'hosted': 'b',
                        'layout': {'type': ['string', 'null']},
                        'hours': 'i',
                        'minutes': 'i',
                        'source': {'enum': ['cell', 'owner', 'default']},
                        'managed': 'b',
                        'runners': 'b',
                        'active': 'b',
                        'idle_since': {'type': ['string', 'null']},
                        'blocker': 's',
                        'wake_at': {'type': ['string', 'null']},
                        'last_request': {'type': ['object', 'null']},
                        'last_error': 's',
                    }
                )
            }
        ),
        'WebhookRelayState': obj(
            {
                'relay': obj(
                    {
                        'key': 's',
                        'relays': 'i',
                        'cursor': 'i',
                        'last_pull': {'type': ['string', 'null']},
                        'last_error': 's',
                        'pending': {'type': ['integer', 'null']},
                        'oldest': {'type': ['string', 'null']},
                        'deleting': 'i',
                        'active': 'b',
                    }
                )
            }
        ),
        'WebhookRelayAddress': obj(
            {'relay': obj({'trigger': 's', 'relay_id': 's', 'address_prefix': 's', 'address': 's', 'replayed': 'b'})}
        ),
        'WebhookRelayWake': obj({'relay': obj({'trigger': 's', 'relay_id': 's', 'wake': 'b'})}),
        'WebhookRelayDeleted': obj({'relay': obj({'trigger': 's', 'deleted': 'b', 'gateway_told': 'b'})}),
        'WebhookWakeUrl': obj(
            {
                'url': {'type': ['string', 'null']},
                'set_at': {'type': ['string', 'null']},
                'set_by': {'type': ['string', 'null']},
            }
        ),
        'WebhookWakeUrlSet': obj({'url': {'type': ['string', 'null']}, 'secret': {'type': ['string', 'null']}}),
    }
)
TAGS['Webhooks'] = (
    "Outgoing webhooks: endpoints a person owns, the events Tico sends them, and each send's log (docs/webhooks.md)."
)
SCHEMAS.update(
    {
        'Webhook': obj(
            {
                'id': 's',
                'name': 's',
                'url': 's',
                'host': 's',
                'events': items({'type': 'string'}),
                'enabled': 'b',
                'paused_reason': 's',
                'rate_per_hour': 'i',
                'allow_http': 'b',
                'created_by': 's',
                'requested_by': 'n',
                'created_at': 's',
                'updated_at': 's',
                'revision': 'i',
                'old_secret_until': 'n',
                'can_change': 'b',
                'last_7_days': obj({'delivered': 'i', 'failed': 'i', 'pending': 'i'}),
                'filter': {'type': 'object', 'description': 'departments, bots, pipelines; an empty list means all'},
                'headers': items(
                    {'type': 'object', 'description': 'name, secret, and value unless secret (then set: true)'}
                ),
                'disabled_at': 'n',
                'disabled_reason': 'n',
            }
        ),
        'WebhookSendRow': obj(
            {
                'id': 's',
                'webhook_id': 's',
                'event_id': 's',
                'type': 's',
                'subject': 's',
                'state': {'enum': ['ready', 'sending', 'delivered', 'failed', 'canceled', 'withheld']},
                'attempts': 'i',
                'last_status': {'type': ['integer', 'null']},
                'last_error': 's',
                'next_attempt_at': 'n',
                'created_at': 's',
                'delivered_at': 'n',
            }
        ),
    }
)
SCHEMAS.update(
    {
        'WebhookList': obj(
            {'items': items(ref('Webhook')), 'more': 'b', 'next_offset': 'i'}, required=['items', 'more']
        ),
        'WebhookCreated': obj(
            {
                'webhook': {
                    **ref('Webhook'),
                    'description': 'With `secret` once; `replayed: true` on a retry, without it',
                },
                'approval': {'type': 'object', 'description': 'A bot\'s request: {id, status: pending}'},
            },
            required=[],
        ),
        'WebhookShown': obj({'webhook': ref('Webhook'), 'recent_attempts': items({'type': 'object'})}),
        'WebhookChanged': obj({'webhook': ref('Webhook')}),
        'WebhookEnabled': obj(
            {
                'webhook': ref('Webhook'),
                'recover': {'type': ['object', 'null'], 'description': '{since, failed} after an automatic turn-off'},
            }
        ),
        'WebhookRecovered': obj({'webhook': 's', 'since': 's', 'recovered': 'i', 'more': 'b'}),
        'WebhookReplayed': obj({'webhook': 's', 'since': 's', 'checked': 'i', 'queued': 'i', 'more': 'b'}),
        'WebhookQueued': obj({'queued': 'i', 'type': 's'}),
        'WebhookSends': obj(
            {'items': items(ref('WebhookSendRow')), 'more': 'b', 'next_offset': 'i'}, required=['items', 'more']
        ),
    }
)

# Pages and Library › Files (backend/pages.py, backend/file_library.py).
SCHEMAS.update(
    {
        "Page": obj(
            {
                "audience": {
                    "anyOf": [ref("AccessAudience"), {"type": "null"}],
                    "description": "Only returned to managers on a single page read or write; null inherits where it is filed",
                },
                "restricted": {
                    "type": "boolean",
                    "description": "Only returned to managers, including on lists and cards",
                },
                "file_id": "s",
                "title": "s",
                "bot_id": "s",
                "bot_name": "s",
                "version": "i",
                "updated_at": "s",
                "updated_by": "n",
                "updated_by_name": "s",
                "cadence": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]},
                "as_of": {"type": ["string", "null"], "description": "The date the data is from (YYYY-MM-DD)"},
                "stale_days": {
                    "type": ["integer", "null"],
                    "description": "Days since the last version once past "
                    "1.5x the cadence; null while current or without a cadence",
                },
                "replaced_by_id": {
                    "type": ["string", "null"],
                    "description": "The newer page's file id, when the reader may see it",
                },
                "replaced_by_title": "n",
                "archived": "b",
                "task_id": "n",
                "src": {
                    "type": ["string", "null"],
                    "description": "Where to frame the page; null means open it "
                    "(GET /api/v2/files/{fid}/versions/{number})",
                },
            },
            required=[
                "file_id",
                "title",
                "bot_id",
                "bot_name",
                "version",
                "updated_at",
                "updated_by",
                "cadence",
                "as_of",
                "stale_days",
                "replaced_by_id",
                "archived",
                "task_id",
                "src",
            ],
        ),
        "PageShown": obj(
            {
                "page": ref("Page"),
                "can_manage": "b",
                "can_delete": "b",
                "pinned": {
                    "type": "array",
                    "description": "The places it is pinned that the caller may open",
                    "items": obj({"place": "s", "label": "s"}),
                },
            },
            required=["page"],
        ),
        "PagePublished": obj(
            {"page": ref("Page"), "unembedded": items({"type": "string"}), "created": "b"},
            required=["page", "unembedded"],
        ),
        "PageList": obj({"pages": items(ref("Page")), "has_more": "b", "next_cursor": "n"}, required=["pages"]),
        "LibraryFile": obj(
            {
                "id": "s",
                "bot": "s",
                "bot_name": "s",
                "title": "s",
                "kind": "s",
                "library_kind": {"type": "string", "enum": ["page", "document", "image", "data", "media", "other"]},
                "kind_label": "s",
                "version": "i",
                "last_activity_at": "s",
                "archived": "b",
                "page": {"type": ["object", "null"], "description": "{cadence, archived} for a page"},
                "pins": {"type": "array", "description": "Where the file is pinned, when pins exist"},
            },
            required=["id", "bot", "title", "kind", "library_kind", "version", "last_activity_at"],
        ),
        "FileLibrary": obj({"files": items(ref("LibraryFile")), "has_more": "b", "next_cursor": "n"}),
        "FileSearchHit": obj(
            {
                "id": "s",
                "title": "s",
                "kind": "s",
                "library_kind": "s",
                "bot": "s",
                "bot_name": "s",
                "version": "i",
                "updated_at": "s",
                "url": "n",
                "page": "b",
                "excerpt": "s",
                "score": "f",
            }
        ),
        "FileSearch": obj({"results": items(ref("FileSearchHit"))}),
        "FileDeleted": obj({"deleted": "s", "versions": "i"}),
    }
)

ANSWERS = SCHEMAS

# Documentation for the two sign-in routes whose bodies the handlers read by hand.
TOKEN_REQUEST = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["code", "code_verifier"],
                "additionalProperties": False,
                "properties": {
                    "code": {"type": "string", "description": "From the URL fragment: #tico_code=..."},
                    "code_verifier": {
                        "type": "string",
                        "minLength": 43,
                        "maxLength": 128,
                        "description": "The PKCE verifier whose S256 hash was the code_challenge.",
                    },
                },
            }
        }
    },
}

ERROR = {
    "description": "A problem: {\"error\": {\"code\", \"detail\", \"retryable\", ...}}",
    "content": {"application/json": {"schema": ref("Problem")}},
}
PROBLEM = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "detail"],
            "additionalProperties": True,
            "properties": {
                "code": {"type": "string"},
                "detail": {"type": "string"},
                "retryable": {"type": "boolean"},
                "sign_in": {"type": "string", "description": "On a 401 with built-in sign-in: the login path"},
            },
        }
    },
}

TAGS['Cards and pins'] = (
    "Tico's built-in cards and where pages and cards are pinned: the Dashboard, team pages, bot "
    "sidebars and tasks (docs/overview.md). A card shows each reader only what they may see."
)
_RULE_IDS = items({'type': 'string'})
SCHEMAS['RulesByTeammate'] = obj(
    {
        'rules': items(
            obj(
                {
                    'id': 's',
                    'kind': {'enum': ['internal', 'external']},
                    'text': 's',
                    'if_broken': {'enum': ['block', 'escalate', 'warn', None]},
                    'can_edit': 'b',
                }
            )
        ),
        'bots': items(obj({'slug': 's', 'who': _RULE_IDS, 'with': _RULE_IDS})),
        'people': items(obj({'id': 's', 'who': _RULE_IDS, 'with': _RULE_IDS})),
    }
)
SCHEMAS['Card'] = obj(
    {
        'id': 'n',
        'template': {
            'enum': [
                'tasks_by_status',
                'done_per_week',
                'needs_you',
                'spend',
                'kpi',
                'automation_runs',
                'latest_pages',
                'needs_attention',
                'unread_threads',
                'my_tasks',
                'updates',
                'growth_number',
                'growth_funnel',
                'growth_channels',
                'growth_retention',
                'growth_board',
                'floor',
            ]
        },
        'title': 's',
        'heading': 's',
        'name': 's',
        'team_name': 's',
        'settings': 'o',
        'hidden': 'b',
        'data': {'type': ['object', 'null'], 'description': 'What the card shows the caller; null when hidden'},
    },
    required=['id', 'template', 'heading', 'settings', 'hidden', 'data'],
)
SCHEMAS['CardList'] = obj({'cards': items(ref('Card')), 'next': 'n'})
SCHEMAS['Place'] = obj(
    {
        'place': 's',
        'kind': {'enum': ['dashboard', 'growth', 'team', 'bot', 'task', 'person']},
        'saved': 'b',
        'can_edit': 'b',
        'can_link': {
            'type': 'boolean',
            'description': 'Task places: whether the reader may link a readable page; does not grant card or pin management',
        },
        'month': {'type': 'string', 'description': 'growth: the month its cards read (YYYY-MM)'},
        'months': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': 'growth: the newest 24 months with cohort rows, oldest first',
        },
        'pins': items(
            obj(
                {
                    'id': 'n',
                    'item': 'n',
                    'position': 'i',
                    'size': {'enum': ['small', 'medium', 'large']},
                    'card': ref('Card'),
                    'can_unpin': {'type': 'boolean', 'description': 'Task pins: whether this reader may unlink it'},
                    'page': obj({'file_id': 's', 'title': 's', 'bot': 's', 'updated_at': 's', 'archived': 'b'}),
                },
                required=['id', 'item', 'position', 'size'],
            )
        ),
        'audience': {'type': 'object', 'description': 'A team page: who sees it (docs/permissions.md)'},
        'name': 's',
        'group': 's',
    },
    required=['place', 'kind', 'saved', 'can_edit', 'pins'],
)

# Settings > Models: the company's subscriptions (backend/model_subscriptions.py). Values are never in an answer.
TAGS['Model subscriptions'] = (
    "The company's saved model logins and keys, given to computers per runtime. A value is "
    "sent only to add or reconnect one; no answer carries it."
)
_SUB_KIND = {"enum": ["claude_token", "codex_login", "anthropic_key", "openai_key"]}
SCHEMAS['ModelSubscription'] = obj(
    {
        'id': 's',
        'name': 's',
        'kind': _SUB_KIND,
        'runtime': {'enum': ['claude', 'codex']},
        'label': 's',
        'status': {'enum': ['new', 'working', 'needs_reconnect']},
        'status_detail': 's',
        'status_at': 'n',
        'preview': 's',
        'revision': 'i',
        'computers': items({'type': 'string'}),
        'usage': {
            'anyOf': [
                obj(
                    {
                        'window': {'type': ['string', 'null'], 'enum': ['5h', 'week', None]},
                        'used_pct': NUM_N,
                        'cost_usd_month': NUM_N,
                        'resets_at': 'n',
                        'reason': {'type': ['string', 'null']},
                        'weekly_resets_at': 'n',
                        'weekly_used_pct': NUM_N,
                    },
                    required=['window', 'used_pct', 'cost_usd_month'],
                ),
                {'type': 'null'},
            ]
        },
        'added_by': 's',
        'added_by_name': 's',
        'owner_id': 's',
        'owner_name': 's',
        'created_at': 's',
        'updated_at': 's',
        'order': {'type': ['integer', 'null']},
        'in_use_on': items({'type': 'string'}),
    },
    required=[
        'id',
        'name',
        'kind',
        'runtime',
        'label',
        'status',
        'status_detail',
        'status_at',
        'preview',
        'revision',
        'computers',
        'usage',
        'added_by',
        'added_by_name',
        'owner_id',
        'owner_name',
        'created_at',
        'updated_at',
        'order',
        'in_use_on',
    ],
)
SCHEMAS['ModelSubscriptionList'] = obj(
    {
        'subscriptions': items(ref('ModelSubscription')),
        'kinds': items(
            obj(
                {
                    'kind': _SUB_KIND,
                    'label': 's',
                    'runtime': {'enum': ['claude', 'codex']},
                    'secret_label': 's',
                    'help': 's',
                    'connect': 'b',
                    'owned': 'b',
                }
            )
        ),
    }
)
SCHEMAS['ModelSubscriptionDeleted'] = obj(
    {'deleted': 'b', 'id': 's', 'computers': 'i', 'stopped': 'i', 'sessions_reset': 'i'}
)
_SET_WHY = obj({'kind': {'enum': ['soonest', 'held', 'reconnect', 'all_held', 'none']}, 'plan': 'n', 'until': 'n'})
_PERSON_SET = obj({'person': 's', 'person_name': 's', 'plans': 'i', 'on': 'n', 'on_name': 'n', 'why': _SET_WHY})
SCHEMAS['ComputerSubscriptionList'] = obj(
    {
        'assignments': items(
            obj(
                {
                    'runner_id': 's',
                    'runtime': {'enum': ['claude', 'codex']},
                    'subscription_id': 's',
                    'person': 's',
                    'set': {'anyOf': [_PERSON_SET, {'type': 'null'}]},
                }
            )
        ),
        'capable': items({'type': 'string'}),
        'people': items(obj({'id': 's', 'name': 's', 'claude': 'i', 'codex': 'i'}, required=['id', 'name'])),
    }
)
SCHEMAS['ComputerSubscriptionResult'] = obj(
    {
        'runner_id': 's',
        'runtime': {'enum': ['claude', 'codex']},
        'subscription_id': 'n',
        'person': 's',
        'stopped': 'i',
        'sessions_reset': 'i',
    }
)

TAGS['Slack links'] = (
    "A thread's link to a Slack channel, both ways (docs/slack.md): asked for by an admin, checked by the "
    "gateway, then active, suspended or refused. Admins only."
)
SCHEMAS['SlackLink'] = obj(
    {
        'id': 's',
        'thread_id': 's',
        'channel': obj({'id': 's', 'name': 's', 'private': 'b'}),
        'audience': 's',
        'state': {'enum': ['requested', 'active', 'suspended', 'refused', 'unlinked']},
        'reason': 's',
        'history': obj({'state': {'enum': ['off', 'pending', 'running', 'done', 'failed']}, 'count': 'i'}),
        'linked_by': 's',
        'created_at': 's',
        'updated_at': 's',
    }
)
SCHEMAS['SlackLinkList'] = obj({'links': items(ref('SlackLink')), 'next_cursor': 'n', 'available': 'b'})

# A bot's Dream (backend/dream.py): a cleanup task over its own memory, with up to three questions to its requester.
SCHEMAS['BotDreamView'] = obj(
    {
        'dream': {
            'oneOf': [
                obj(
                    {
                        'task': 's',
                        'status': 's',
                        'state': {'enum': ['starting', 'dreaming', 'asking', 'done', 'stopped']},
                        'questions': {
                            'oneOf': [
                                obj(
                                    {
                                        'task_id': 's',
                                        'requirement_id': 's',
                                        'text': 's',
                                        'mine': {
                                            'type': 'boolean',
                                            'description': 'The question is put to the caller, who answers it at POST /api/v2/tasks/{tid}/asks/{rid}/answer',
                                        },
                                    }
                                ),
                                {'type': 'null'},
                            ]
                        },
                        'summary': 'n',
                        'doing': {
                            'type': ['string', 'null'],
                            'description': "While dreaming: the bot's one line on what it is doing now (the task note)",
                        },
                        'closed_at': 'n',
                        'started_at': 's',
                        'auto': {
                            'type': 'boolean',
                            'description': 'Started by the nightly run (Auto dream), not by a person',
                        },
                        'reason': {
                            'type': ['string', 'null'],
                            'description': "An auto dream's reason: the first line of its body",
                        },
                    }
                ),
                {'type': 'null'},
            ]
        }
    }
)

# The methods that write. Each /api/v2 write declares the Idempotency-Key header, required unless an exception says not.
WRITE_METHODS = ("post", "put", "patch", "delete")
# PUTs whose handlers never pass through Store.mutate, so they accept a write without the key.
PUT_WITHOUT_KEY = frozenset(
    {
        "/api/v2/bots/{bot}/github-repos",
        "/api/v2/bots/{bot}/repositories",
        "/api/v2/finance/bills-address",
        "/api/v2/hooks/wake-url",
        "/api/v2/inbound/inboxes/{ident}/audience",
        "/api/v2/postal/addresses/{address_id}/grants/{grantee_type}/{grantee_id}",
        "/api/v2/postal/settings",
        "/api/v2/repositories/settings",
        "/api/v2/repositories/{owner}/{repo}",
    }
)

DESCRIPTION = (
    "The stable API for building your own frontend on Tico. Everything here keeps its shape within v2: fields are added, "
    "never removed or renamed, and a breaking change is a new /api/v3. Routes not listed are internal. "
    "Public roadmap writes require an allowlisted Origin; reads permit server-side requests. No sign-in or idempotency key (docs/roadmaps.md). "
    "Granola connection and sync writes do not require an idempotency key, nor does POST /api/v2/inbound/tasks, whose "
    "own `key` is its idempotency. Inbox company connection actions reconcile the same short-lived code under current grant checks. "
    "Other writes need an `Idempotency-Key` header (1-200 characters; a retry with the same "
    "key and body returns the first answer). Errors are `{\"error\": {code, detail, retryable}}`. See docs/custom-frontend.md."
)


def _refs(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/components/schemas/"):
                found.add(value.rsplit("/", 1)[1])
            else:
                _refs(value, found)
    elif isinstance(node, list):
        for value in node:
            _refs(value, found)


# Threads (docs/dev/threads-contract.md §10): a thread is a conversation read through threads.view, and its messages
# carry thread_id, root_id-free fields as _message_out writes them.
TAGS["Threads"] = (
    "Threads: the sidebar, a thread and its timeline, read marks, search, members, follow, and the messages "
    "in a thread. A thread the viewer may not read answers 404, never 403."
)
SCHEMAS.update(
    {
        "ThreadView": obj(
            {
                "id": "s",
                "title": "s",
                "audience": {
                    "type": "string",
                    "enum": ["company", "team", "members", "task", "home", "personal", "participants", "meeting"],
                },
                "audience_group_id": "n",
                "owner_id": "n",
                "parent_id": "n",
                "parent_message_id": "n",
                "root_id": "s",
                "path": "a",
                "kind": "n",
                "task_id": "n",
                "created_at": "s",
                "last_message_at": "n",
                "archived_at": "n",
                "last_seq": "i",
                "last_unread_seq": "i",
                "me": "o",
                "members": "o",
                "retention_days": {
                    "type": ["integer", "null"],
                    "description": "Days a root keeps its messages; null keeps them for good",
                },
                "home": {"type": ["object", "null"], "description": "Only for audience home: {actor}"},
                "pinned_for_everyone": {
                    "type": "boolean",
                    "description": "Its root is pinned for everyone "
                    "who reads it; `me.pinned` is whether it is pinned for the viewer",
                },
                "live_call": {
                    "type": ["object", "null"],
                    "description": "The thread's live call, or null",
                    "properties": {"id": {"type": "string"}, "live_count": {"type": "integer"}},
                    "required": ["id", "live_count"],
                },
            },
            required=["id", "title", "audience", "root_id", "last_seq", "last_unread_seq", "pinned_for_everyone"],
        ),
        "ThreadReactions": items(
            obj({"emoji": "s", "count": "i", "actors": "a"}, required=["emoji", "count", "actors"])
        ),
        "ThreadEarlier": obj({"home": "s", "messages": "i"}, required=["home", "messages"]),
        "ThreadUploaded": obj(
            {"thread": ref("ThreadView"), "message": ref("ThreadMessage")}, required=["thread", "message"]
        ),
        "ThreadSidebar": obj(
            {
                "attention": items({"type": "object"}),
                "threads": items(ref("ThreadView")),
                "dms": items({"type": "object"}),
                "next_cursor": "n",
                "bot_dms": ref("ThreadDmCounts"),
                "person_dms": ref("ThreadDmCounts"),
            },
            required=["attention", "threads", "dms"],
        ),
        "ThreadDmCounts": {
            "type": "object",
            "additionalProperties": {"type": "integer"},
            "description": "Unread in each open DM with one teammate, by that teammate's actor "
            "(first page only; {} after)",
        },
        "ThreadRail": obj(
            {
                "pinned": items(ref("ThreadView")),
                "threads": items(ref("ThreadView")),
                "catch_up": {
                    "type": ["integer", "null"],
                    "description": "Catch up's count, at most 99 (first page only; null after)",
                },
                "catch_up_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The root ids `catch_up` counts, at most 99 (first page only; "
                    "[] after): a client keeps the set from the live stream",
                },
                "unread": items(ref("ThreadRailUnread")),
                "dms": ref("ThreadDmCounts"),
                "next_cursor": "n",
                "seq": {
                    "type": "integer",
                    "description": "The live change number this first page is as of (first page only): a stream that "
                    "resumes from it (`GET /api/v2/events?after=`) carries every change since",
                },
            },
            ["pinned", "threads", "catch_up", "catch_up_ids", "dms", "next_cursor"],
        ),
        "ThreadRailUnread": {
            **obj(
                {
                    "id": "s",
                    "title": "s",
                    "name": "s",
                    "audience": "s",
                    "dm_with": items(obj({"actor": "s", "name": "s"})),
                    "dm_with_count": "i",
                    "home_actor": {"type": ["string", "null"], "description": "The bot or person whose Home it is"},
                    "new_count": "i",
                    "mention": "b",
                    "task_id": {"type": ["string", "null"]},
                    "last_message_at": {"type": ["string", "null"]},
                },
                required=["id", "audience", "new_count", "mention"],
            ),
            "description": "A Catch up item the rail does not hold (a 1:1 DM, a Home, a task's thread), in Catch up's "
            "order: mentions first, then newest activity (first page only; [] after)",
        },
        "ThreadBrowse": obj(
            {"threads": items({"allOf": [ref("ThreadView"), obj({"member_count": "i"})]}), "next_cursor": "n"}
        ),
        "ThreadMatch": obj(
            {
                "threads": items(ref("ThreadView")),
                "dm": {
                    "type": ["string", "null"],
                    "description": "The viewer's open DM with the one person or bot named, or null",
                },
            },
            required=["threads", "dm"],
        ),
        "ThreadCatchUp": obj(
            {
                "items": items(
                    obj(
                        {
                            "thread": ref("ThreadView"),
                            "new_count": "i",
                            "mention": "b",
                            "messages": items(ref("ThreadMessage")),
                            "more": "i",
                        }
                    )
                ),
                "count": {"type": "integer", "description": "Every item across pages, at most 99"},
                "next_cursor": "n",
            }
        ),
        "ThreadSearch": obj(
            {
                "hits": items(
                    obj(
                        {
                            "message_id": "s",
                            "thread_id": "s",
                            "title": "s",
                            "sender_id": "s",
                            "created_at": "s",
                            "excerpt": "s",
                        },
                        required=["message_id", "thread_id", "excerpt"],
                    )
                ),
                "next_cursor": "n",
            },
            required=["hits", "next_cursor"],
        ),
        "ThreadMessage": obj(
            {
                "id": "s",
                "conversation_id": "s",
                "thread_id": "s",
                "seq": "i",
                "sender_id": "s",
                "recipient_id": "n",
                "kind": "s",
                "body": "n",
                "created_at": "s",
                "edited_at": "n",
                "revision": "i",
                "refs": "o",
                "deleted": "b",
                "root_id": "n",
                "origin_id": "n",
                "parent_message_id": "n",
                "depth": "i",
                "hidden": "b",
                "clipped": "b",
                "sub_thread": {
                    "type": ["object", "null"],
                    "description": "The sub-thread branching from this message: {id, count, last_seq, unread, more}",
                },
                "in_reply_to": "n",
                "reply_to": {
                    "oneOf": [ref("ThreadReplyQuote"), {"type": "null"}],
                    "description": "The message this one answers (or a sub-thread message's "
                    "parent): a quote, `{id, hidden: true}` when the viewer does not "
                    "read it, or null",
                },
                "routed": {
                    "oneOf": [ref("ThreadRouted"), {"type": "null"}],
                    "description": "Who routing asked to answer this message (`to` empty: nobody); "
                    "null when routing never looked at it",
                },
            },
            required=["id", "seq", "thread_id"],
        ),
        "ThreadReplyQuote": obj(
            {
                "id": "s",
                "author": "s",
                "author_name": "s",
                "excerpt": {"type": "string", "description": "Plain text, at most 120 characters"},
                "hidden": "b",
            },
            required=["id"],
        ),
        "ThreadRouted": obj(
            {"to": items(obj({"actor": "s", "name": "s"}, required=["actor", "name"]))}, required=["to"]
        ),
        "ThreadBotStatusRow": obj(
            {
                "actor": "s",
                "name": "s",
                "state": {"type": "string", "enum": ["working", "up_next", "cant_reply"]},
                "detail": "s",
                "since": "s",
                "run_id": "n",
                "fix": {
                    "oneOf": [obj({"label": "s", "href": "s"}, required=["label", "href"]), {"type": "null"}],
                    "description": "Where to fix what stops it (cant_reply only)",
                },
            },
            required=["actor", "name", "state", "detail", "since", "run_id", "fix"],
        ),
        "ThreadBotStatus": obj({"bots": items(ref("ThreadBotStatusRow"))}, required=["bots"]),
        "ThreadReach": obj(
            {
                "thread_id": "s",
                "way": {"type": "string", "enum": ["steer", "queue", "defer"]},
                "reaches": items(
                    obj(
                        {
                            "actor": "s",
                            "reason": {
                                "type": "string",
                                "description": "mention, ask, dm, home, sub_thread, reply, follower or digest",
                            },
                            "mode": {"type": "string", "enum": ["steer", "queue", "defer", "digest"]},
                            "defer_for": "n",
                            "adds_reader": "b",
                        },
                        required=["actor", "reason", "mode"],
                    )
                ),
                "outside": items({"type": "string"}),
            },
            required=["thread_id", "reaches", "outside"],
        ),
        "ThreadCrumb": obj({"id": "s", "title": "s"}),
        "ThreadTimeline": obj(
            {
                "thread": ref("ThreadView"),
                "breadcrumb": items(ref("ThreadCrumb")),
                "messages": items({"type": "object"}),
                "next_cursor": "n",
                "bot_status": {
                    "type": "array",
                    "items": ref("ThreadBotStatusRow"),
                    "description": "First page only: the bots the root waits on, as `GET .../bot-status` gives them",
                },
                "read_marks": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "description": "`flat` only: the viewer's last_read_seq in the root and in "
                    "each thread a message on the page is in (0: read nothing)",
                },
            },
            required=["thread", "messages"],
        ),
        "ThreadMembers": obj(
            {
                "members": items(
                    obj({"actor": "s", "role": "s", "added_by": "n", "created_at": "s"}, required=["actor", "role"])
                ),
                "next_cursor": "n",
            },
            required=["members"],
        ),
        "ThreadMemberChange": obj(
            {"thread_id": "s", "actor": "s", "role": "s", "removed": "b"}, required=["thread_id", "actor"]
        ),
        "ThreadRead": obj({"thread_id": "s", "last_read_seq": "i"}, required=["thread_id", "last_read_seq"]),
        "ThreadReadAll": obj({"threads": "i"}, required=["threads"]),
        "ThreadFollow": obj({"thread_id": "s", "following": "b", "notify": "s"}, required=["thread_id", "following"]),
        "ThreadMessageDeleted": obj({"id": "s", "deleted": "b", "revision": "i"}, required=["id", "deleted"]),
        "ThreadDeleted": obj({"deleted": "s"}, required=["deleted"]),
    }
)
SCHEMAS["ThreadMessage"]["properties"]["source"] = ref("MessageSource")
SCHEMAS["ThreadSearch"]["properties"]["hits"]["items"]["properties"]["source"] = ref("MessageSource")
# Calls (docs/meet.md): a call belongs to a thread. Joining a Tico Meet call hands back a token for audio and screen share
# only; joining a Google Meet hands back its link.
SCHEMAS.update(
    {
        "CallParticipant": obj({"actor": "s", "joined_at": "s", "left_at": "n", "removed_at": "n", "in_call": "b"}),
        "CallProvider": obj(
            {
                "provider": {"enum": ["tico", "google_meet"]},
                "meet_uri": {"type": ["string", "null"], "description": "The Google Meet link Google gave"},
                "join_url": {
                    "type": ["string", "null"],
                    "description": "The Meet link with authuser=<your Google address> when known; null for Tico Meet",
                },
                "live_count": {"type": "integer", "description": "People in the call now, as its status source says"},
                "status_source": {"enum": ["google", "recorder", None], "description": "Where live status comes from"},
                "seen_at": {"type": ["string", "null"], "description": "When anyone was first seen in the Meet"},
                "duration_s": {"type": ["integer", "null"], "description": "An ended call's length"},
                "ring": {"type": "boolean", "description": "The call rings you: rung, not joined, still live"},
                "card_message_id": "n",
                "transcript_message_id": "n",
                "recorder": obj({"source": "n", "installed": "b"}),
            }
        ),
        "CallHold": obj(
            {"kind": {"enum": ["transcript", "action_items"]}, "reason": "s", "detail": "s", "created_at": "s"}
        ),
        "CallSummary": {
            "allOf": [
                obj(
                    {
                        "id": "s",
                        "thread_id": "s",
                        "title": "s",
                        "state": {"enum": ["live", "ended"]},
                        "connector": "s",
                        "started_at": "s",
                        "ended_at": "n",
                        "end_reason": "n",
                    }
                ),
                ref("CallProvider"),
            ]
        },
        "CallBase": obj(
            {
                "id": "s",
                "thread_id": "s",
                "title": "s",
                "state": {"enum": ["live", "ended"]},
                "connector": "s",
                "meeting_id": "s",
                "started_at": "s",
                "ended_at": "n",
                "end_reason": "n",
                "participants": items(ref("CallParticipant")),
                "bots": items({"type": "string"}),
            },
            holds={
                "type": "array",
                "items": ref("CallHold"),
                "description": "Posts the thread refused at the call's end. Only the connector sees them",
            },
            speak_replies={
                "type": "boolean",
                "description": "Whether bots were asked to speak their replies "
                "on this call; off unless the call asked. Spoken replies are not sent yet",
            },
        ),
        "Call": {"allOf": [ref("CallBase"), ref("CallProvider")]},
        "CallList": obj(
            {
                "calls": items(ref("CallSummary")),
                "next_before": "n",
                "available": "b",
                "providers": items({"enum": ["google_meet", "tico"]}),
            }
        ),
        "CallRinging": obj({"calls": items(ref("Call"))}),
        "CallRecorderSource": obj(
            {"source": "b", "meeting_id": "s", "title": "s", "source_actor": "n", "has_transcript": "b"}
        ),
        "CallJoin": obj(
            {"call": ref("Call"), "url": "n", "expires_in": "i", "token": "s"},
            required=["call", "url"],
        ),
        "CallToken": obj({"url": "s", "expires_in": "i", "token": "s"}),
    }
)


# Feedback on work (backend/feedback.py): 👍/👎 with a reason on a message, task or approval a bot or a person made.
TAGS["Feedback"] = (
    "A person's 👍 or 👎 on a message, task or approval a bot or another person made, with an optional reason: "
    "a 👎 on a bot's work can tell the bot (a note for its next run in that conversation) or become an org rule. "
    "Every answer leaves out feedback on what the caller cannot read."
)
SCHEMAS.update(
    {
        "Feedback": obj(
            {
                "id": "s",
                "person_id": "s",
                "subject_type": {"enum": ["message", "task", "approval", "ask"]},
                "subject_id": "s",
                "bot_id": "s",
                "rating": {"enum": ["up", "down"]},
                "reason": "s",
                "outcome": {"enum": ["none", "told_bot", "rule"]},
                "note_id": "n",
                "note_status": {"enum": ["waiting", "carried", "settled", "canceled", None]},
                "org_rule_id": "n",
                "rule_status": {"enum": ["suggested", "active", "retired", None]},
                "created_at": "s",
                "updated_at": "s",
            }
        ),
        "FeedbackMarkers": obj(
            {"items": items(ref("Feedback")), "counts": items(obj({"subject_id": "s", "up": "i", "down": "i"}))}
        ),
        "FeedbackPage": obj({"items": items(ref("Feedback")), "next_cursor": "n"}),
        "FeedbackReasons": obj({"reasons": items(obj({"text": "s", "count": "i"}))}),
        "FeedbackRemoved": obj({"ok": "b", "id": "s"}),
        "FeedbackRuleDraft": obj({"text": "s", "drafted": "b"}),
        "FeedbackRuleMade": obj({"feedback": ref("Feedback"), "rule": "o"}),
    }
)


SCHEMAS.update(
    {
        'MeetingTaskLink': obj(
            {
                'task_id': 's',
                'number': {'type': ['integer', 'null']},
                'title': 's',
                'status': 's',
                'kind': {'enum': ['linked', 'sent', 'proposal']},
                'created_by': 's',
            }
        ),
        'MeetingTaskLinks': obj({'tasks': items(ref('MeetingTaskLink')), 'next_cursor': 'n'}),
        'MeetingTaskLinkBatch': obj(
            {
                'tasks': items(ref('MeetingTaskLink')),
                'next_cursor': 'n',
                'results': items(obj({'status': {'enum': [200, 404]}})),
            }
        ),
        'TaskMeeting': obj(
            {
                'meeting_id': 's',
                'title': 's',
                'started_at': 's',
                'url': 's',
                'kind': {'enum': ['linked', 'sent', 'proposal']},
            }
        ),
        'TaskMeetings': obj({'meetings': items(ref('TaskMeeting')), 'next_cursor': 'n'}),
        'TaskContextPage': obj({'id': 's', 'title': 's', 'version': 'i', 'updated_at': 's'}),
        'TaskContextMeeting': obj({'id': 's', 'title': 's', 'date': 's'}),
        'TaskContext': obj(
            {
                'pages': {**items(ref('TaskContextPage')), 'maxItems': 10},
                'meetings': {**items(ref('TaskContextMeeting')), 'maxItems': 10},
            }
        ),
        'TaskMakePage': obj(
            {
                'make_page': {
                    'anyOf': [
                        obj(
                            {
                                'message_id': 's',
                                'conversation_id': 's',
                                'state': {'enum': ['making', 'made', 'failed', 'update_required']},
                            }
                        ),
                        {'type': 'null'},
                    ]
                }
            }
        ),
        'MeetingTaskRemoved': obj({'ok': 'b'}),
    }
)


# The Heartbeat (backend/heartbeat.py): one short beat of ordered steps, every 10 s by default.
INT_N = {"type": ["integer", "null"]}
SCHEMAS.update(
    {
        "HeartbeatStep": obj(
            {
                "key": "s",
                "name": "s",
                "kind": {"enum": ["due", "recovery", "poll"]},
                "kind_name": "s",
                "every_s": INT_N,
                "every": "s",
                "switch": "b",
                "on": "b",
                "can_change": "b",
                "last_run_at": "n",
                "last_ms": INT_N,
                "last_result": "n",
                "last_did_at": "n",
                "last_did": "n",
                "last_error": "n",
                "last_error_at": "n",
                "failing": "b",
                "runs_24h": "i",
                "did_24h": "i",
                "failed_24h": "i",
            }
        ),
        # A beat whose failures repeat the newest row's is folded into it: `repeats` beats, the last at `last_repeated_at`.
        "HeartbeatBeat": obj(
            {
                "id": "s",
                "started_at": "s",
                "total_ms": "i",
                "did": "i",
                "failed": "i",
                "repeats": "i",
                "last_repeated_at": "n",
                "steps": items(
                    obj({"key": "s", "name": "s", "text": "n", "ms": "i", "error": "n"}, required=["key", "ms"])
                ),
            }
        ),
        "HeartbeatView": obj(
            {
                "interval_s": "i",
                "interval_min": "i",
                "interval_max": "i",
                "last_beat_at": "n",
                "beats_24h": "i",
                "quiet_beats": "i",
                "quiet_since_at": "n",
                "kinds": items(obj({"key": "s", "name": "s"})),
                "steps": items(ref("HeartbeatStep")),
                "beats": items(ref("HeartbeatBeat")),
                "next": "n",
                "can_change": "b",
            }
        ),
        "SystemRoutineSaved": obj(
            {
                "job": {
                    "anyOf": [ref("HeartbeatStep"), obj({"key": "s", "name": "s"})],
                    "description": "The saved routine's row: a HeartbeatStep for ask_deadlines, "
                    "else its GET /system-routines row",
                }
            }
        ),
    }
)


MENTIONS_DESCRIPTION = (
    "Turns the file paths and names a bot wrote (in a message, a page, a report) into its files, for links. "
    "A value with a folder (`reports/pack.docx`, `/home/bot/checkout/reports/pack.docx`) matches the file published "
    "from that path in the bot's checkout, else the one file whose path it ends with or that ends with it. A bare name "
    "(`pack.docx`) matches only when exactly one of the bot's files has that name. Only the files the caller may see "
    "count, by each file's current version; archived files and links never match. A value that is not exactly one "
    "file is absent from `files`: none, two or more (never a guess), or one the caller may not read all look the same. "
    "Tico's web app also tries a bare name inside a folder named earlier in the same paragraph; this route does not, "
    "so send the joined path yourself. A caller who may see the bot but none of its files gets 200 with `files` "
    "empty, as does a request with no m. Errors: 401 not signed in, 404 no such bot or one the caller may not see, "
    "422 more than 60 m values."
)


def operations(app):
    """Each stable operation as (path, method, tag, operationId, summary, answer name in SCHEMAS): the routes whose
    decorator carries api_v2(...), in the order the app serves them."""
    found, seen = [], set()
    for route in app.routes:
        extra = getattr(route, "openapi_extra", None) or {}
        if MARK not in extra:
            continue
        for method in sorted(m.lower() for m in route.methods):
            for key in ((route.path_format, method), extra["operationId"]):
                if key in seen:
                    raise RuntimeError("%s %s: %r is declared twice" % (method.upper(), route.path_format, key))
                seen.add(key)
            if route.tags[0] not in TAGS:
                raise RuntimeError(
                    "%s %s has the tag %r, which is not in TAGS" % (method.upper(), route.path, route.tags[0])
                )
            found.append((route.path_format, method, route.tags[0], extra["operationId"], route.summary, extra[MARK]))
    return found


def stable_path(app):
    """The pattern of every stable path and its kept old spellings (backend/legacy_spellings.py), but the event
    stream: the answers that carry display names (app.py). Built once per app, on first use."""
    pattern = getattr(app.state, "stable_path", None)
    if pattern is None:
        pattern = app.state.stable_path = re.compile(
            "|".join(
                re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(spelling))
                for path, *_ in operations(app)
                if path != "/api/v2/events"
                for spelling in (path, *legacy_of(path))
            )
        )
    return pattern


def spec(app):
    """The v2 document for this app: the stable operations of `app.openapi()`, described."""
    full = copy.deepcopy(app.openapi())
    paths = {}
    for path, method, tag, op_id, summary, answer in operations(app):
        op = (full["paths"].get(path) or {}).get(method)
        if op is None:
            raise RuntimeError("the stable API lists %s %s, which the server does not serve" % (method.upper(), path))
        op.pop(MARK, None)
        op.pop("tags", None)  # FastAPI puts a route's own tags first; the document has them after its answers
        op["tags"], op["operationId"], op["summary"] = [tag], op_id, summary
        op.pop("description", None)
        responses = {code: r for code, r in op.get("responses", {}).items() if code != "422"}
        if answer:
            responses["200"] = {"description": "OK", "content": {"application/json": {"schema": ref(answer)}}}
        if path == "/api/v2/events":
            responses["200"] = {
                "description": EVENTS_DESCRIPTION,
                "x-task-event-schema": ref("TaskEvent"),
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        if path == "/api/v2/cloud-machines/support":
            op["description"] = (
                "HQ is the source of the switch and helper states; no local copy is kept. "
                "GET returns available false, on null and an empty machines list off Tico Cloud "
                "or when HQ answers 404 not_cloud. Demo and rehearsal never contact HQ. "
                "PUT accepts a boolean on, needs the owner and refuses support sessions. "
                "Core assigns an increasing revision and keeps it for retries of the same "
                "Idempotency-Key. A 409 stale_revision is final and carries HQ's current boolean "
                "on in the error. After an uncertain PUT, read HQ again before showing a saved state."
            )
            responses["403"] = {"description": "People only; changing the switch needs the owner", **ERROR}
            responses["502"] = {"description": "Didn't reach Tico HQ, or HQ's answer could not be used", **ERROR}
            if method == "put":
                responses["404"] = {"description": "not_cloud: cloud-machine support is not available", **ERROR}
        if path == "/api/v2/bots/{bot}/files/mentions":
            op["description"] = MENTIONS_DESCRIPTION
            for param in op.get("parameters", []):
                if param.get("name") == "m":
                    param["description"] = (
                        "A path or file name as the bot wrote it; repeat for each (m=a&m=b), at most 60. Values are "
                        "trimmed, a repeat counts once, and one over 300 characters is ignored. Before matching, "
                        "\\ becomes / and a leading ./ is dropped; the answer keeps the value as sent (trimmed)"
                    )
            responses["404"] = {**ERROR, "description": "not_found: no such bot, or one the caller may not see"}
        if method == "get" and path.startswith("/api/v2/files/") and path.endswith("/versions/{number}"):
            responses["200"] = {
                "description": "The file's bytes (application/octet-stream, sent as an attachment)",
                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            }
        if method == "get" and path.startswith("/api/v2/finance/tax/") and path.endswith(".csv"):
            responses["200"] = {
                "description": "The workpaper as CSV (text/csv, sent as an attachment)",
                "content": {"text/csv": {"schema": {"type": "string"}}},
            }
        # The logo travels as its raw bytes, not JSON, and the preview is a page of HTML (company_page/api.py).
        if path == "/api/v2/company-page/logo" and method == "post":
            op["requestBody"] = {
                "required": True,
                "description": "The image itself, up to 2 MB. SVG is refused.",
                "content": {mime: {"schema": {"type": "string", "format": "binary"}} for mime in COMPANY_LOGO_TYPES},
            }
        if path == "/api/v2/company-page/logo" and method == "get":
            responses["200"] = {
                "description": "The logo's bytes, sent as their image type",
                "content": {mime: {"schema": {"type": "string", "format": "binary"}} for mime in COMPANY_LOGO_TYPES},
            }
        if path == "/api/v2/company-page/preview" and method == "get":
            responses["200"] = {
                "description": "The page as HTML for one audience, never kept",
                "content": {"text/html": {"schema": {"type": "string"}}},
            }
        if path == "/auth/login":
            responses = {
                "302": {"description": "Redirect to the identity provider"},
                "400": {"description": "next is not an allowed origin, or code_challenge is missing"},
            }
        if path not in ("/healthz", "/auth/login", "/auth/token"):
            responses["401"] = {"description": "Not signed in", **ERROR}
            responses["422"] = {
                "description": "Invalid request or rule: error.code and field-specific error.detail",
                **ERROR,
            }
        if method == "post" and path in (
            "/api/v2/tasks",
            "/api/v2/tasks/{tid}",
            "/api/v2/tasks/{tid}/comments",
            "/api/v2/messages",
            "/api/v2/conversations/{cid}/messages",
        ):
            responses["422"] = {
                **ERROR,
                "description": "ask_refused: a bot ask fails the person's asking criteria; "
                "detail gives the reason and fix. Nothing is created or changed; no quarantine "
                "or org-rule hold. Progress notes should be posted as updates.",
            }
        if path == "/auth/token":
            op["requestBody"] = TOKEN_REQUEST
            op["security"] = []
            responses["400"] = {"description": "invalid_grant, or the request is malformed", **ERROR}
        if path == "/api/v2/mail/messages/organize":
            op["requestBody"] = {"required": True, "content": {"application/json": {"schema": ref("MailOrganizeBody")}}}
            responses["403"] = {"description": "Organize access or the company organize switch is off", **ERROR}
        connection = path == '/api/v2/inbound/company-connection'
        if connection:
            op['requestBody'] = {
                'required': True,
                'content': {
                    'application/json': {
                        'schema': {
                            'type': 'object',
                            'additionalProperties': False,
                            'required': ['action', 'code'],
                            'properties': {
                                'action': {'type': 'string', 'enum': ['preview', 'connect']},
                                'code': {
                                    'type': 'string',
                                    'minLength': 43,
                                    'maxLength': 43,
                                    'pattern': '^[A-Za-z0-9_-]{43}$',
                                    'writeOnly': True,
                                },
                            },
                        }
                    }
                },
            }
            op['description'] = (
                'Direct signed-in human owner/admin only; no personal tokens, support or delegated agents. '
                'Preview shows the target company before explicit consent to share Inbox in both directions. '
                'The server supplies its private key and existing reported installation identity; neither belongs in this body. '
                'Requests are limited to 2 KiB; code validation errors never echo supplied values. '
                'Disconnect in the portal stops future access, preserving merged history and existing local copies.'
            )
            for code, detail in [
                ('403', 'Direct human management or portal authorization required'),
                ('409', 'Missing saved identity, expired code or conflicting connection'),
                ('503', 'Inbox connection unavailable; reconcile the same code in its original lifetime'),
            ]:
                responses[code] = {**ERROR, 'description': detail}
        if path in ("/healthz", "/auth/login"):
            op["security"] = []
        op["responses"] = dict(sorted(responses.items()))
        inbound = path == "/api/v2/inbound/tasks"
        granola = path.startswith("/api/v2/meetings/granola")
        if inbound:
            op["security"] = [{"serviceKey": []}]
        if method in WRITE_METHODS and path.startswith("/api/v2/"):
            keyless = method == "put" and path in PUT_WITHOUT_KEY
            parameters = op.setdefault("parameters", [])
            parameters[:] = [p for p in parameters if (p.get("name"), p.get("in")) != ("Idempotency-Key", "header")]
            parameters.append(
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": not (inbound or granola or connection or keyless),
                    "schema": {"type": "string"},
                    "description": (
                        "Not needed: the pair (service key, `key`) makes a repeated call harmless, and the header "
                        "is ignored."
                        if inbound
                        else 'Optional. Retry the same action and code; HQ rechecks the current grant, role, session and original expiry without readoption.'
                        if connection
                        else "Optional. Sync uses a two-minute debounce; connecting starts a new device sign-in."
                        if granola
                        else "Optional: this write does not check the header."
                        if keyless
                        else "1-200 characters. Reusing a key with the same body replays the first answer."
                    ),
                }
            )
        if method == "post" and path.startswith("/api/v2/calls/") and path.endswith("/token"):
            # A refresh writes nothing, so the key is optional and ignored.
            for param in op["parameters"]:
                if param.get("name") == "Idempotency-Key":
                    param["required"] = False
                    param["description"] = "Not needed: a refresh writes nothing; the header is ignored."
        paths.setdefault(path, {})[method] = op
    used = set()
    _refs(paths, used)
    request_schemas = full.get("components", {}).get("schemas", {})
    collisions = SCHEMAS.keys() & request_schemas.keys()
    if collisions:
        raise RuntimeError("Request and response schema names collide: " + ", ".join(sorted(collisions)))
    components = {"Problem": PROBLEM, **SCHEMAS, **request_schemas}
    keep, queue = {}, sorted(used | {"Problem"})
    while queue:
        name = queue.pop()
        if name in keep or name not in components:
            continue
        keep[name] = components[name]
        more = set()
        _refs(components[name], more)
        queue.extend(sorted(more))
    return {
        "openapi": full["openapi"],
        "info": {"title": "Tico API (v2)", "version": VERSION, "description": DESCRIPTION},
        "tags": [{"name": name, "description": text} for name, text in TAGS.items()],
        "paths": dict(sorted(paths.items())),
        "components": {
            "schemas": dict(sorted(keep.items())),
            "securitySchemes": {
                "bearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "A bearer session from POST /auth/token (browser apps) or a personal API token (servers).",
                },
                "serviceKey": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "A service key, tico_sk_...: a tasks key reaches POST /api/v2/inbound/tasks and nothing else "
                    "(an update key reaches only /api/v2/system/update, docs/service-keys.md).",
                },
                "cookie": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "tico_session",
                    "description": "The browser session of Tico's own page (`__Host-tico_session` over https).",
                },
            },
        },
        "security": [{"bearer": []}, {"cookie": []}],
    }


# Cloud agents (backend/cloud_agents.py): a seat asks Tico's team to connect an agent it does not connect yet.
SCHEMAS.update(
    {
        "CloudAgent": obj(
            {"id": "s", "name": "s", "maker": "s", "site": "s", "line": "s", "supported": "b", "connect_id": "n"},
            required=["id", "name", "maker", "site", "line", "supported", "connect_id"],
        ),
        "CloudAgentList": obj({"agents": items(ref("CloudAgent"))}, required=["agents"]),
        "CloudAgentAdded": obj(
            {"bot": "s", "harness": "s", "model": "s", "seat": "s"}, required=["bot", "harness", "model", "seat"]
        ),
        "SeatAgent": obj(
            {
                "seat": ref("Seat"),
                "agent": ref("CloudAgent"),
                "steps": items({"enum": ["requested", "building", "ready"]}),
                "tasks": items(obj({"id": "s", "title": "s", "status": "s"}, required=["id", "title", "status"])),
            },
            required=["seat", "agent", "steps", "tasks"],
        ),
        "SeatCanceled": obj(
            {"id": "s", "closed": "b", "request_state": "s"}, required=["id", "closed", "request_state"]
        ),
    }
)
SCHEMAS["Seat"]["properties"].update({name: _t("s") for name in ("agent", "agent_name", "request_state")})
SCHEMAS["Seat"]["required"] = sorted(set(SCHEMAS["Seat"]["required"]) | {"agent", "agent_name", "request_state"})

# The Growth page's numbers (backend/growth_numbers.py): the one read it draws from.
NUM_N_INT = {"type": ["integer", "null"]}
SCHEMAS.update(
    {
        "GrowthMonth": obj(
            {
                "month": "s",
                "spend": NUM_N,
                "bot_cost": NUM_N,
                "new_customers": NUM_N_INT,
                "cac": NUM_N,
                "kept_d30": NUM_N,
                "kept_d90": NUM_N,
                "kept_d180": NUM_N,
                "expected_months": NUM_N,
                "ltv": NUM_N,
                "ltv_cac": NUM_N,
                "payback_months": NUM_N,
                "quality": "n",
                "revenue_month": NUM_N,
                "margin_pct": NUM_N,
            }
        ),
        "GrowthChannel": obj(
            {"slug": "s", "name": "s", "stage": "n", "owner_ref": "n", "series": items(ref("GrowthMonth"))}
        ),
        "GrowthKpi": obj(
            {
                "id": "s",
                "name": "s",
                "unit": "s",
                "latest": NUM_N,
                "latest_at": "n",
                "target": NUM_N,
                "goal_id": "n",
                "parent_kpi_id": "n",
                "growth_role": "n",
                "stage": "n",
                "loop_name": "n",
                "loop_step": NUM_N_INT,
                "series": items(obj({"at": "n", "value": {"type": "number"}})),
            }
        ),
        "GrowthMarker": obj(
            {
                "task_id": "s",
                "title": "s",
                "kind": {"enum": "started finished".split()},
                "outcome": {"enum": ["won", "lost", "inconclusive", "kept", "dropped", None]},
                "result": {"enum": ["failed", "worked-once", "watching", None]},
                "at": "s",
                "channels": items({"type": "string"}),
                "goal_id": "n",
            }
        ),
        "GrowthNumbers": obj(
            {
                "months": items({"type": "string"}),
                "series": items(ref("GrowthMonth")),
                "channels": items(ref("GrowthChannel")),
                "kpis": obj(
                    {
                        "north_star": {"oneOf": [ref("GrowthKpi"), {"type": "null"}]},
                        "inputs": items(ref("GrowthKpi")),
                        "guardrails": items(ref("GrowthKpi")),
                        "other": items(ref("GrowthKpi")),
                    }
                ),
                "markers": items(ref("GrowthMarker")),
                "may_edit": {"type": "boolean"},
            }
        ),
    }
)

# The Company page (backend/company_page): what an owner or an admin writes, and the page's own settings. Readers never
# reach these routes; the reader origin serves the page and its data without a key.
COMPANY_LOGO_TYPES = ("image/png", "image/jpeg", "image/webp")  # company_page/release.py LOGO_TYPES
TAGS["Company page"] = (
    "The public page for the company's updates: its audiences, the editions each one reads, the stats "
    "the page shows, the readers who sign in to it, and the logo (docs/company-page.md)."
)


# What the Assistant did for you, the Needs you summary and why an item is here (docs/assistant.md).
SCHEMAS.update(
    {
        "NeedsSummary": obj(
            {
                "actor": "s",
                "total": "i",
                "this_week": "i",
                "by_kind": "o",
                "by_bot": items({"type": "object"}),
                "bots_left": "i",
            }
        ),
        "Origin": obj(
            {
                "ref": "s",
                "kind": "s",
                "id": "s",
                "title": "s",
                "created_at": "s",
                "made_by": "o",
                "task": {"type": ["string", "null"]},
                "cause": "o",
                "siblings": "o",
                "stop": items({"type": "object"}),
            }
        ),
        "AssistantChangeList": obj({"changes": items({"type": "object"}), "next": {"type": ["string", "null"]}}),
        "AssistantChangeResult": obj({"id": "s", "restored": "b", "already": "b", "object": "s"}),
    }
)


# Tools (backend/mailboxes.py): which inboxes Tico uses and who may use each.
TAGS["Mailboxes"] = (
    "Which mailboxes Tico uses and who may use each: Read, or Read and write (organize, draft and send, as far as the company allows)."
)
_MAILBOX_LEVEL = {"enum": ["none", "read", "read_write"]}
_MAILBOX_REF = obj({"id": "s", "name": "s"})
SCHEMAS.update(
    {
        "Mailbox": obj(
            {
                "id": "s",
                "address": "s",
                "provider": "s",
                "kind": {"enum": ["personal", "shared"]},
                "person_id": "n",
                "person_name": "s",
                "connection": {"enum": ["workspace", "personal"]},
                "status": {"enum": ["active", "stopped"]},
                "state": {"enum": ["connected", "not_connected", "awaiting_person", "stopped"]},
                "level": _MAILBOX_LEVEL,
                "routines": items(_MAILBOX_REF),
                "used_by": obj({"people": "i", "bots": "i", "names": items({"type": "string"})}),
                "synced_at": "n",
                "newest_epoch": {"type": ["integer", "null"]},
                "sync": {"enum": ["", "signin", "failed"]},
                "sync_error": "s",
            }
        ),
        "MailboxMay": obj({"read": "b", "organize": "b", "send": "b", "change_calendar": "b"}),
        "MailboxList": obj(
            {
                "admin": "b",
                "domain": "s",
                "may": ref("MailboxMay"),
                "mine": {"anyOf": [ref("Mailbox"), {"type": "null"}]},
                "shared": items(ref("Mailbox")),
                "mailboxes": items(ref("Mailbox")),
            }
        ),
        "MailboxResult": obj({"mailbox": ref("Mailbox")}),
        "MailboxDetail": obj(
            {
                "mailbox": ref("Mailbox"),
                "can_manage": "b",
                "can_stop": "b",
                "accesses": items(
                    obj(
                        {
                            "id": "n",
                            "grantee_type": {"enum": ["person", "bot", "admins"]},
                            "grantee_id": "s",
                            "name": "s",
                            "level": {"enum": ["read", "read_write"]},
                            "implicit": "b",
                        }
                    )
                ),
                "routines": items(obj({"id": "s", "name": "s", "enabled": "b", "can_edit": "b"})),
                "may": ref("MailboxMay"),
                "candidates": items(obj({"type": {"enum": ["person", "bot"]}, "id": "s", "name": "s"})),
            }
        ),
    }
)

TAGS["Email"] = "Live Gmail organizing; Gmail holds the mail and Tico records only action details and ids."
MAIL_ORGANIZE_ACTION = {"enum": ["archive", "unarchive", "mark_read", "mark_unread", "label", "unlabel"]}
MAIL_ORGANIZE_IDS = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 128},
    "minItems": 1,
    "maxItems": 500,
}
MAIL_ORGANIZE_FIELDS = {
    "mailbox": {"type": "string", "maxLength": 254},
    "action": MAIL_ORGANIZE_ACTION,
    "ids": MAIL_ORGANIZE_IDS,
    "query": {"type": "string", "minLength": 1, "maxLength": 4096},
    "label": {"type": "string", "minLength": 1, "maxLength": 225},
    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
}
SCHEMAS["MailOrganizeBody"] = {
    "oneOf": [
        {**obj(MAIL_ORGANIZE_FIELDS, required=("mailbox", "action", "ids")), "not": {"required": ["query"]}},
        {**obj(MAIL_ORGANIZE_FIELDS, required=("mailbox", "action", "query")), "not": {"required": ["ids"]}},
    ]
}
SCHEMAS["MailOrganizeUndo"] = obj(
    {"action": MAIL_ORGANIZE_ACTION, "ids": items("s"), "label": "s"}, required=("action", "ids")
)
SCHEMAS["MailOrganizeResult"] = obj(
    {
        "mailbox": "s",
        "action": MAIL_ORGANIZE_ACTION,
        "label": "s",
        "count": "i",
        "ids": items("s"),
        "undo": ref("MailOrganizeUndo"),
    },
    required=("mailbox", "action", "count", "ids", "undo"),
)

TAGS["Letters out"] = "Physical letters sent through the Gateway; approvals bind the saved proof and price."
SCHEMAS["LetterOutContents"] = obj(
    {
        **{
            key: "s"
            for key in ("id", "requested_by", "mail_class", "currency", "status", "error", "created_at", "updated_at")
        },
        **{
            key: "n"
            for key in (
                "acting_bot_id",
                "automation_id",
                "task_id",
                "thread_id",
                "quote_id",
                "quote_expires_at",
                "approval_id",
                "approval_hash",
                "hq_letter_id",
                "proof",
            )
        },
        "recipient": "o",
        "return_address": "o",
        "tracking": items(obj({"barcode": "s", "status": "n"})),
        "page_count": {"type": ["integer", "null"]},
        "total_micros": "i",
        "color": "b",
        "duplex": "b",
        "cancel_possible": "b",
        "letter_id": "s",
        "approval_reason": "s",
        "accepted_at": "n",
        "billing": "s",
        "charged_micros": {"type": ["integer", "null"]},
        "redacted": {"enum": [False]},
        "can_cancel": "b",
    }
)
SCHEMAS["LetterOutMetadata"] = obj(
    {
        **{key: "s" for key in ("id", "mail_class", "currency", "status", "billing", "created_at", "updated_at")},
        "acting_bot_id": "n",
        "page_count": {"type": ["integer", "null"]},
        "total_micros": "i",
        "charged_micros": {"type": ["integer", "null"]},
        "redacted": {"enum": [True]},
        "can_cancel": "b",
    }
)
SCHEMAS["LetterOutMetadata"]["additionalProperties"] = False
SCHEMAS["LetterOut"] = {"oneOf": [ref("LetterOutContents"), ref("LetterOutMetadata")]}
SCHEMAS["LetterOutPage"] = obj({"items": items(ref("LetterOut")), "next_before": "n"})
SCHEMAS["LettersSettings"] = obj(
    {
        "id": "s",
        "mode": "s",
        "return_address": {"type": ["object", "null"]},
        "monthly_cap_micros": {"type": ["integer", "null"]},
        "availability": "s",
        "checked_at": "n",
        "created_at": "s",
        "updated_at": "s",
    }
)
SCHEMAS["LettersAccess"] = obj({"send": "b", "outbox_id": "s"})
SCHEMAS["LettersGrants"] = obj(
    {
        "outbox_id": "s",
        "next": "n",
        "grants": items(
            obj(
                {
                    "id": "s",
                    "outbox_id": "s",
                    "grantee_type": "s",
                    "grantee_id": "s",
                    "level": "s",
                    "granted_by_id": "s",
                    "created_at": "s",
                }
            )
        ),
    }
)
SCHEMAS["LettersGrant"] = obj({"grantee_type": "s", "grantee_id": "s", "level": "s"})
SCHEMAS["LettersRevoked"] = obj({"revoked": "b"})
SCHEMAS["LetterBotSettings"] = obj({"bot_id": "s", "mode": "n", "monthly_cap_micros": {"type": ["integer", "null"]}})
TAGS["Physical mail"] = "Company letters, explicit Read grants and legal mail routing (docs/physical-mail.md)."
SCHEMAS.update(
    {
        "PostalMailGrant": obj(
            {
                "id": "s",
                "address_id": "s",
                "grantee_type": {"enum": ["person", "bot"]},
                "grantee_id": "s",
                "level": {"enum": ["read"]},
                "granted_by_id": "s",
                "created_at": "s",
                "updated_at": "s",
            }
        ),
        "PostalMailGrants": obj({"address_id": "s", "grants": items(ref("PostalMailGrant")), "next": "n"}),
        "PostalMailGrantResult": obj({"grant": ref("PostalMailGrant")}),
        "PostalMailRevoked": obj({"ok": "b"}),
        "PostalMailBots": obj(
            {
                "bots": items(
                    obj(
                        {
                            "id": "s",
                            "slug": "s",
                            "display_name": "s",
                            "state": "s",
                            "read_error": "n",
                            "routing_error": "n",
                        }
                    )
                ),
                "next": "n",
            }
        ),
        "PostalMailSettings": obj(
            {
                "settings": obj(
                    {"id": "i", "legal_bot_id": "n", "updated_by_id": "n", "created_at": "s", "updated_at": "s"}
                )
            }
        ),
    }
)

TAGS["Email sending"] = (
    "One sending policy and durable undo queue for bots, automations and people acting through Tico."
)
ANSWERS.update(
    {
        "EmailSendingPolicy": obj({"company": "o", "inbox": "o", "bot": "o", "effective": "o"}),
        "EmailSend": obj(
            {
                "id": "s",
                "mailbox": "s",
                "actor": "s",
                "bot": "s",
                "via": "s",
                "automation_action_id": "n",
                "calendar_action_id": "n",
                "content_sha256": "s",
                "body_sha256": "s",
                "subject": "s",
                "approval_id": "s",
                "state": "s",
                "created_at": "s",
                "send_after": "s",
                "claimed_at": "n",
                "finished_at": "n",
                "provider_message_id": "n",
                "detail": "s",
                "recipients": items("s"),
                "attachments": items("o"),
                "decision": "o",
                "classification": "o",
            }
        ),
        "EmailSendResult": obj({"send": ref("EmailSend")}),
        "EmailSendDetail": obj({"send": ref("EmailSend"), "body": "s", "bodies": items("o"), "preview_complete": "b"}),
        "EmailSendList": obj({"sends": items(ref("EmailSend")), "next_cursor": "n"}),
        "EmailSuppressionList": obj({"recipients": items("o"), "next_cursor": "n"}),
    }
)


TAGS["Alerts"] = "Personal phone verification, consent and opt-in paging, paid by the company at $1 per accepted alert."
SCHEMAS.update(
    {
        "PersonalAlerts": obj(
            {
                "phone": "n",
                "status": "s",
                "verified_at": "n",
                "call_consent": "b",
                "preferences": obj(
                    {
                        "legal_mail": "b",
                        "urgent_calls": "b",
                        "timezone": "s",
                        "quiet_start": "s",
                        "quiet_end": "s",
                        "emergency_exception": "b",
                        "monthly_cap": "i",
                    }
                ),
                "monthly_spend": "i",
                "company_monthly_cap": "i",
                "company_monthly_spend": "i",
                "price_usd": "i",
                "paid_by": "s",
                "recent": items(obj({"id": "s", "kind": "s", "state": "s", "error": "s", "created_at": "s"})),
            }
        ),
        "AlertPhoneCode": obj({"status": "s", "expires_in": "i"}),
        "AlertTest": obj({"id": "s", "status": "s"}, required=["id", "status"], price_usd=_t("i"), paid_by=_t("s")),
        "AlertCompanyCap": obj({"monthly_cap": "i", "monthly_spend": "i", "price_usd": "i"}),
    }
)

# The company skills directory (backend/skills.py): answers are not declared yet, so the document lists the routes only.
TAGS["Skills"] = (
    "The company's approved skills: proposals and approval, screening state, and installs for bots and people (docs/skills.md)."
)


# Calendar (docs/dev/calendar-contract.md, HTTP): one way in for Google, Tico and derived calendars.
TAGS["Calendar"] = (
    "Calendars of people, bots, shared calendars and teams: Google read live, Tico events, runs, due dates and time "
    "away merged into one list of occurrences (docs/dev/calendar-contract.md)."
)
_CAL_OBJ = obj({"ref": "s", "type": "s", "id": "s", "name": "s"})
_CAL_ERRORS = items(obj({"ref": "s", "reason": "s"}))
SCHEMAS.update(
    {
        "CalendarOccurrence": obj(
            {
                "occurrence_id": "s",
                "event_id": "n",
                "calendar": _CAL_OBJ,
                "home": {"type": "string", "enum": ["google", "tico", "derived"]},
                "kind": {"type": "string", "enum": ["event", "run", "due", "away"]},
                "title": "s",
                "start": "s",
                "end": "s",
                "all_day": "b",
                "time_zone": "n",
                "attendees": "a",
                "response": "n",
                "busy_only": "b",
                "recurring": "b",
                "status": "s",
                "meeting_url": "n",
                "open_url": "n",
                "links": obj({"thread": "n", "meeting": "n", "task": "n", "automation": "n", "attempt": "n"}),
                "can": obj({"join": "b", "edit": "b"}),
            }
        ),
        "CalendarView": obj(
            {
                "ref": "s",
                "type": "s",
                "id": "s",
                "name": "s",
                "home": "n",
                "time_zone": "n",
                "work_starts": "n",
                "work_ends": "n",
                "work_days": "n",
                "can_write": "b",
            }
        ),
        "CalendarEvents": obj(
            {
                "from": "s",
                "to": "s",
                "calendars": items(ref("CalendarView")),
                "occurrences": items(ref("CalendarOccurrence")),
                "errors": _CAL_ERRORS,
            },
            month_counts=items(obj({"bot_id": "s", "date": "s", "count": "i"})),
        ),
        "CalendarList": obj({"calendars": items(ref("CalendarView")), "errors": _CAL_ERRORS}),
        "CalendarSearch": obj({"occurrences": items(ref("CalendarOccurrence")), "partial": "b", "errors": _CAL_ERRORS}),
        "CalendarFree": obj({"slots": items(obj({"start": "s", "end": "s"})), "errors": _CAL_ERRORS}),
        "CalendarSettingsView": obj(
            {
                "time_zone": "s",
                "time_zone_set": "b",
                "work_starts": "s",
                "work_ends": "s",
                "work_days": "s",
                "record_on_join": "b",
            }
        ),
    }
)


def generate():
    """The document for a default installation: what GET /api/v2/openapi.json serves to a signed-in caller."""
    import tempfile
    from .app import create_app
    from .config import Settings

    with tempfile.TemporaryDirectory() as tmp:
        return spec(create_app(Settings(db_path=Path(tmp) / "hub.db")))


def render(document):
    return json.dumps(document, indent=2, sort_keys=False) + "\n"


def determinism_problem():
    """None when two generations give the same bytes, else the reason they do not (the static PR job runs this)."""
    first = render(generate())
    if render(generate()) != first:
        return "the API spec differs between two generations of the same code: something in it is not deterministic"
    return None


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(determinism_problem() or 0)  # a message exits non-zero; None exits 0
    sys.stdout.write(render(generate()))
