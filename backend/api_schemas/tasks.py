# pyright: strict
"""The answers of the Tasks routes (backend/openapi_v2.py gathers them)."""

from ..task_documents import NAMES as DOCUMENT_NAMES
from ..api_contract import ref
from ._base import ACTORS, SCHEMAS, items, obj

SCHEMAS.update(
    {
        "Task": obj(
            {
                "private": "b",
                "id": "s",
                "title": "s",
                # A task is a title and a chat: its request is the first message on its chat, on a full read only.
                "request": {"oneOf": [obj({"id": "s", "from": "s", "text": "s", "at": "s"}), {"type": "null"}]},
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
            pushed_past={
                "type": "boolean",
                "description": "On a list row: it entered its stage pushed past a requirement",
            },
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
                "answer": {
                    "oneOf": [obj({"id": "s", "value": "s", "note": "n", "by": "s", "at": "s"}), {"type": "null"}]
                },
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
                "moved_unseen": "i",
                "skipped": items(obj({"id": "s", "code": "s", "reason": "s"})),
            }
        ),
        "TaskManyResult": obj(
            {
                "batch_id": "s",
                "changed": items("s"),
                "changed_unseen": "i",
                "unchanged": "i",
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
                "fallback": {
                    "type": ["string", "null"],
                    "description": "The fallback harness that ran the turn, if any",
                },
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
        "TaskReviewAskView": obj({"questions": "a", "who": "n", "by": "s"}),
        "ReviewAnswer": {
            "oneOf": [
                obj({"target": "o", "answers": "o", "other": "n", "dismiss": "b", "by": "s", "at": "s"}),
                obj({"by": "s", "text": "s", "at": "s"}),
            ]
        },
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
        "InboundTaskResult": obj(
            {"task": {"oneOf": [obj({"id": "s"}), {"type": "null"}]}, "created": "b", "changed": "b"}
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

SCHEMAS["TaskDetail"]["properties"]["events"] = items(obj({"source": ref("MessageSource")}, required=["source"]))

SCHEMAS.update(
    {
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

SCHEMAS.update(
    {
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
    }
)
