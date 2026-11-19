# pyright: strict
"""The answers of the Bots routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj, scalar

SCHEMAS.update(
    {
        "TaskFlow": obj({"going": "i", "waiting": "i"}, required=["going", "waiting"]),
        # What a bot's tasks run on (backend/task_models.py stored): an empty model is the bot's own model.
        "TaskModelSetting": obj(
            {
                "harness": {"type": "string", "description": "Empty without a model, or for the model's own"},
                "model": {"type": "string", "description": "Empty: the bot's own model runs its tasks"},
                "reasoning_effort": "s",
                "runtime": {"type": "string", "description": "Empty without a model"},
                "compact_at": {
                    "type": ["integer", "null"],
                    "description": "Where a task's conversation compacts, in tokens; null is the model's default",
                },
            }
        ),
        "BotTaskModelSaved": obj(
            {
                "bot": {"type": "string", "description": "The bot's id"},
                "task_model": {"oneOf": [ref("TaskModelSetting"), {"type": "null"}]}
                | {"description": "What its tasks run on now; null: the same as the bot"},
                "revision": {"type": "integer", "description": "The bot's configuration revision after the save"},
                "readiness": {
                    "type": ["object", "null"],
                    "description": "With a task model: whether the bot's computer can run it now (its tasks run on the "
                    "bot's own model until it can); else null",
                },
                "warning": {
                    "type": "string",
                    "description": "Plain words for a cheaper task model on a bot that sends, spends, publishes or "
                    "writes; it saved all the same. Empty otherwise",
                },
            }
        ),
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
                "task_model": {"oneOf": [ref("TaskModelSetting"), {"type": "null"}]}
                | {
                    "description": "On a bot whose configuration the caller may read: what its tasks run on; null is "
                    "the same as the bot"
                },
                "compact_at": {
                    "type": ["integer", "null"],
                    "description": "On a bot whose configuration the caller may read: where its conversation "
                    "compacts, in tokens (its saved size, else its model's default)",
                },
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
                                # ok: an OK (an approval); answer: a ❓ on the task's chat (org_routes.making).
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
        "BotList": {
            **items(ref("Bot")),
            "description": "Each bot as Bot; with ?fields=, each row holds only the named fields it has, none required",
        },
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
        "BotBrowserExitView": obj(
            {
                "browser_exit": {
                    "type": ["object", "null"],
                    "properties": {"runner_id": {"type": "string"}, "name": {"type": "string"}},
                }
            },
            choices=items(obj({"runner_id": "s", "label": "s", "owner_id": "s"})),
            may_change=scalar("b"),
            required=["browser_exit"],
        ),
        # Failed-run back-off and run budgets (backend/run_failures.py, backend/turn_budgets.py).
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
        # Bot profiles (backend/profiles.py).
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
