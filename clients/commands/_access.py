"""Who sees which tool: AUDIENCE, the Assistant's and the Rules Owner's lists, `offered_to` and `listing`.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from clients.commands import computer_upkeep, connections, pipelines
from clients.commands import org as hubtools_org

from clients.commands._registry import TOOLS, tool
from clients.commands._requester import (
    BOTOPS,
    BOTS,
    COUNSEL,
    HUMANS_AND_ASSISTANT,
    KINDS,
    NOT_ASSISTANT,
    PEOPLE,
    REQUESTER,
    REQUESTER_READ,
)
from clients.commands.mail import EMAIL_ORGANIZE


# `hub` commands with no tool: they write the Mac's own workspace (`clients/catalog.py`), which
# the hub cannot reach, so BotOps runs them in a shell. Everything else is in both doors.
# `tico_db` runs where the database credential is, on the runner; the server's MCP endpoint
# has neither the credential nor any business connecting to a team database. A person makes and
# revokes service keys in their own shell: a new key is shown to them, never to an agent's context.
# `tico_team_icon` streams a local image file; the JSON tool transport cannot open that path.
# `tico_bot_check_names` scans a bot's checkout on this computer (`clients/bot_names.py`): no hub, no credential.
SHELL_ONLY = {
    "tico_task_worktree_setup",
    "tico_bot_check",
    "tico_bot_check_names",
    "tico_db",
    "tico_service_key_create",
    "tico_service_key_list",
    "tico_service_key_revoke",
    "tico_team_icon",
    # This shell's own profiles (clients/profiles.py) and a live stream no MCP call can hold (clients/events_tail.py).
    "tico_auth_add",
    "tico_auth_list",
    "tico_auth_use",
    "tico_auth_remove",
    "tico_events_tail",
}


# ----------------------------------------------------------------------------- calls (Tico Meet: docs/meet.md)
# A call belongs to a thread. Starting, joining, ending and attaching bots are people's acts; BotOps acts as the person it
# works for, and the server refuses a bot on each of them. Every read checks the caller's access to the thread.
CALL_TOOLS = (
    "tico_call_list",
    "tico_call_show",
    "tico_call_start",
    "tico_call_join",
    "tico_call_token",
    "tico_call_leave",
    "tico_call_end",
    "tico_call_retry",
    "tico_call_bot_add",
    "tico_call_bot_remove",
)


for _spec in hubtools_org.TOOLS:
    tool(_spec["name"], _spec["description"], _spec["properties"], _spec["required"], writes=_spec["writes"])(
        _spec["fn"]
    )


# A write the Assistant may make on its own (backend/assistant.py write_allowed): anything else it proposes.
ASSISTANT_WRITES = {
    "tico_email_cancel",
    "tico_goal_read_back",
    "tico_goal_save",
    "tico_goal_create",
    "tico_goal_update",
    "tico_kpi_create",
    "tico_kpi_link",
    "tico_kpi_target",
    "tico_kpi_unlink",
    "tico_proposal_decide",
    "tico_inbound_create_email",
    "tico_task_child_create",
    "tico_task_create",
    "tico_task_update",
    "tico_task_done",
    "tico_task_move",
    "tico_task_reassign",
    "tico_task_comment",
    "tico_task_label",
    "tico_task_close",
    "tico_task_link",
    "tico_task_unlink",
    "tico_task_queue",
    "tico_task_release",
    "tico_task_answer",
    "tico_task_give",
    "tico_update_mark_read",
    "tico_changelog_mark_read",
    "tico_assistant_propose",
    "tico_assistant_learn",
    "tico_assistant_withdraw",
    "tico_assistant_change_restore",
    "tico_settings_set",
    "tico_rules_read",
    "tico_rules_ask",
    # People view; the Assistant books for them (decision 13).
    "tico_calendar_add",
    "tico_calendar_move",
    "tico_calendar_cancel",
}


ASSISTANT_WRITES.update('tico_email_' + action for action in EMAIL_ORGANIZE)


AUDIENCE = {
    **{
        name: HUMANS_AND_ASSISTANT
        for name in (
            "tico_email_mailboxes",
            "tico_email_inbox",
            "tico_email_search",
            "tico_email_thread",
            "tico_email_message",
        )
    },
    **{
        name: PEOPLE
        for name in (
            "tico_alerts_show",
            "tico_alerts_set",
            "tico_alerts_verify",
            "tico_alerts_confirm",
            "tico_alerts_remove",
            "tico_alerts_test",
            "tico_alerts_company",
        )
    },
    # Task caps (CONTRACT §1): people read and set them; a bot's own change is a proposal a person decides.
    "tico_meeting_link": REQUESTER + ("assistant",),
    "tico_meeting_unlink": REQUESTER + ("assistant",),
    "tico_task_meetings": REQUESTER + ("assistant",),
    "tico_task_caps": REQUESTER_READ,
    "tico_task_cap_set": PEOPLE + BOTS,
    **{name: REQUESTER for name in ("tico_repo_list", "tico_repo_update", "tico_bot_repos_get", "tico_bot_repos_set")},
    # Humans use their own rights; BotOps acts through `on_behalf_of`, which the server allows for no other bot.
    "tico_bot_branch": REQUESTER,
    # A bot or agent that manages the bot changes its name and description, its model and its task model
    # (report_managers); the server refuses any other bot.
    "tico_bot_update": REQUESTER + ("bot", "agent"),
    "tico_bot_model": REQUESTER + ("bot", "agent"),
    "tico_bot_task_model": REQUESTER + ("bot", "agent"),
    # A bot asks for its own credential in the chat or task its turn is on (backend/credential_cards.py); BotOps for any.
    "tico_api": REQUESTER,
    "tico_credential_request": ("bot", "botops"),
    # Org changes: the Strategy Owner's owner chat makes one the person asked for, citing their words (`on_behalf_of`).
    **{name: REQUESTER + ("assistant",) for name in ("tico_group_update", "tico_group_delete", "tico_org_reports_to")},
    # Maintenance: the owner or an admin changes it; the team reads it (backend/maintenance.py may_change).
    "tico_maintenance": REQUESTER_READ,
    "tico_maintenance_runs": REQUESTER_READ,
    "tico_maintenance_set": REQUESTER,
    "tico_maintenance_run": REQUESTER,
    "tico_credential_set": BOTOPS,
    "tico_message_redact": BOTOPS,
    "tico_support_file": BOTOPS,
    "tico_bot_repo_create": ("owner", "botops"),
    # Finish setup (backend/onboarding.py): the owner finishes it, and an admin reads it. A person's own agent, handed over
    # from setup (docs/connect-an-agent.md), uses these with their personal token.
    **{name: PEOPLE for name in ("tico_token_list", "tico_token_create", "tico_token_revoke")},  # people's own
    "tico_setup_show": ("owner", "admin"),
    "tico_setup_complete": ("owner",),
    "tico_setup_invite": ("owner",),
    "tico_skill_submit": PEOPLE + BOTS,  # any person or bot proposes; an admin approves (backend/skills.py)
    "tico_repo_review": ("bot", "botops"),
    "tico_repo_product_create": ("owner", "bot", "botops"),  # a bot needs Create product repositories (server)
    **{
        name: REQUESTER
        for name in (
            "tico_credential_grant",
            "tico_credential_revoke",
            "tico_credential_import",
            "tico_credential_delete",
        )
    },
    **{
        name: REQUESTER
        for name in (
            "tico_bot_create",
            "tico_bot_restore",
            "tico_bot_turns_at_once",
            "tico_computer_turns_at_once",
            "tico_agent_pair_show",
            "tico_agent_pair_approve",
            "tico_agent_pair_decline",
            "tico_bot_place",
            "tico_bot_go_live",
            "tico_task_model",
            "tico_bot_budget",
            "tico_bot_profile",
            "tico_bot_release",
            "tico_bot_pause",
            "tico_bot_scope",
            "tico_bot_resume",
            "tico_bot_access",
            "tico_human_access",
            "tico_group_access",
            "tico_bot_owners",
            "tico_human_add",
            "tico_tool_add",
            "tico_tool_update",
            "tico_tool_remove",
            "tico_bot_copy",
            "tico_bot_update_from_original",
            "tico_bot_suggest_to_original",
            "tico_skill_copy",
        )
    },
    **{name: REQUESTER_READ for name in ("tico_credential_list", "tico_health_check")},
    "tico_computer_list": KINDS,
    "tico_health_metrics": REQUESTER,
    **{
        name: REQUESTER
        for name in (
            "tico_bot_archive",
            "tico_doc_archive",
            "tico_file_archive",
            "tico_meeting_delete",
            "tico_meeting_granola_status",
            "tico_meeting_granola_sync",
            "tico_meeting_pending",
            "tico_meeting_approve",
            "tico_meeting_dismiss",
            "tico_meeting_restore",
        )
    },
    **{name: REQUESTER for name in CALL_TOOLS},  # a person's act; BotOps acts as the person it works for
    # The Assistant only.
    # The Assistant bot's own background run (a routine, kind `bot`) proposes a card for its person too; the server admits
    # only the Assistant bot, and only with `for` naming an active person (backend/app.py propose).
    "tico_assistant_propose": ("assistant", "bot"),
    # The morning message: the server admits only the Assistant bot's background run, once a company day per person
    # (backend/assistant.py morning). The Assistant's routine calls it, so its listing carries it too.
    "tico_assistant_morning": ("assistant", "bot"),
    # Any teammate may withdraw a waiting card or find one (backend/assistant.py teammate); confirming stays the owner's.
    "tico_assistant_withdraw": KINDS,
    "tico_assistant_proposals": KINDS,
    # A bot's add is a suggestion; the Assistant's is an org rule the person asked for in their own chat, citing their words.
    "tico_rules_add": KINDS,
    # A bot checks its own drafts and appeals its own holds; a person's writes are never checked.
    "tico_rules_check": BOTS,
    "tico_rules_appeal": BOTS,
    "tico_rules_withdraw": PEOPLE + BOTS,
    # A bot does not unsnooze (docs/dev/pipelines-v3-cutover.md, Bot commands): a person brings a task back.
    "tico_task_unsnooze": HUMANS_AND_ASSISTANT,
    "tico_assistant_read": PEOPLE,
    "tico_assistant_send": PEOPLE,
    "tico_needs_you_show": HUMANS_AND_ASSISTANT,  # views.human_only: a person's own list
    "tico_assistant_change_list": HUMANS_AND_ASSISTANT,  # what the person's Assistant did for them
    "tico_assistant_change_restore": HUMANS_AND_ASSISTANT,  # taking one back, as the person
    # A person's own layers (backend/learned.py): the person, or their own chat's turn; never a bot of its own.
    "tico_assistant_learn": HUMANS_AND_ASSISTANT,
    "tico_assistant_learned": HUMANS_AND_ASSISTANT,
    # A chat a person shared with a bot they chose (backend/assistant_share.py): only that bot, in its own run.
    "tico_assistant_shared": BOTS,
    "tico_assistant_shared_read": BOTS,
    # Humans only (views.human_only and the batch routes refuse a bot); a bot's own posts are the other way round.
    "tico_brief": HUMANS_AND_ASSISTANT,
    "tico_bot_recent": HUMANS_AND_ASSISTANT,
    "tico_mcp_stats": PEOPLE + BOTS,  # a bot the owner granted outcome stats; the server refuses the rest
    "tico_update_mark_read": HUMANS_AND_ASSISTANT,
    "tico_update_reply": PEOPLE,
    "tico_grokbot_sync": PEOPLE,
    "tico_external_sync": PEOPLE,
    "tico_changelog_list": HUMANS_AND_ASSISTANT,
    "tico_changelog_mark_read": HUMANS_AND_ASSISTANT,
    "tico_proposal_decide": HUMANS_AND_ASSISTANT,
    "tico_bot_mail": PEOPLE,  # backend/mail_settings.py: a person only
    # Feedback is a person's own (backend/feedback.py refuses a bot); a rule draft spends on a model, so no bot asks.
    **{
        name: PEOPLE
        for name in ("tico_feedback_give", "tico_feedback_remove", "tico_feedback_rule_draft", "tico_feedback_rule")
    },
    **{f"tico_needs_you_{step}": PEOPLE for step in ("start", "next", "respond", "commit", "abandon")},
    "tico_tool_report": ("agent",),
    "tico_update_create": BOTS,  # only a bot posts an update
    "tico_bot_ready": BOTS,  # an onboarding bot's own "I'm ready" (backend/lifecycle.py)
    # A seat is placed by a person under themselves (views and rights in backend/lifecycle.py).
    **{name: PEOPLE for name in ("tico_seat_create", "tico_seat_update", "tico_seat_close")},
    "tico_org_models": KINDS,
    # Files are a bot's own: only a bot, or the computer running it, publishes them.
    **{name: BOTS for name in ("tico_file_publish", "tico_file_link", "tico_file_touch", "tico_file_import")},
    "tico_page_publish": BOTS + ("assistant",),  # the route authenticates the initiating Make page request
    # A hard delete is the owner's alone; BotOps asks the owner who asked for it to confirm (backend/botops_act.py).
    "tico_file_delete": ("owner", "botops"),
    "tico_page_archive": PEOPLE + BOTS,
    "tico_page_access": PEOPLE + BOTS,
    # A bot's read-only view of the app (backend/app_view.py): an owner or admin turns it on, as themselves; the bot
    # takes its own screenshots with it.
    "tico_bot_app_view": PEOPLE,
    "tico_bot_app_shot": BOTS,
    # Reads that go over POST, which the Assistant may not make on its own.
    **{
        name: NOT_ASSISTANT
        for name in ("tico_sql", "tico_classify", "tico_decision_ask", "tico_decision_outcome", "tico_market_ask")
    },
    # Website tools (clients/commands/browser.py, logins.py): a bot's own turn drives the browser as its account; a
    # person opens the browser on the tool's page instead, and the Assistant never signs in anywhere.
    **{t["name"]: BOTS for t in TOOLS if t["name"].startswith(("tico_browser_", "tico_login_"))},
}


# Modules that keep their own AUDIENCE (and SHELL_ONLY) lines next to their tools.
for _module in (hubtools_org, connections, computer_upkeep, pipelines):
    AUDIENCE.update(_module.AUDIENCE)
SHELL_ONLY |= pipelines.SHELL_ONLY


COUNSEL_TOOLS = frozenset(
    {
        "tico_rules_list",
        "tico_rules_get",
        "tico_rules_add",
        "tico_file_read",
        "tico_task_comment",
        "tico_task_close",
        "tico_mail_list",
        "tico_mail_read",
    }
)


# The tools whose route the Assistant's own turn may never make (backend/assistant.py `NEVER`): a credential, a key, a
# sign-in, a support report, SQL, a message's redaction. The server refuses them on a turn whatever is offered; they are
# not offered to the Assistant either, so the live Assistant is shown what its gate admits and nothing else.
ASSISTANT_HIDDEN = frozenset(
    {
        # Contacts schema and field history: a person's Assistant reads entities (get, find, rules, self).
        "tico_contacts_types",
        "tico_contacts_describe",
        "tico_contacts_history",
        "tico_credential_grant",
        "tico_credential_revoke",
        "tico_credential_delete",
        "tico_credential_set",
        "tico_credential_import",
        "tico_agent_pair_approve",
        "tico_agent_pair_decline",
        "tico_call_token",
        "tico_sql",
        "tico_support_file",
        "tico_support_report",
        "tico_support_send",
        "tico_trigger_sign",
        "tico_message_redact",
        "tico_task_followers",  # task show carries followers
        "tico_tool_call",
        "tico_task_readers_list",  # tico_task_show has the readers; the Assistant's listing is near its size cliff
        "tico_tool_check",  # tico_tool_index has each tool's status; Check now is on the tool's page. Same cliff.
    }
)


# The tools an Assistant turn is offered beyond the ones its audience already names: the ones the Assistant template
# (templates/catalog/assistant) tells it to run, found by walking clients/hubcli's own command tree for each `hub ...` span
# (the same tool each command runs, `remotecli.tool_name`), plus the few it needs to change automations, rules and its
# proposals. The gate is unchanged: the CLI still reaches every route the gate admits; this only sizes the MCP listing.
ASSISTANT_TOOLS = frozenset(
    {
        "tico_assistant_learn",
        "tico_assistant_proposals",
        "tico_assistant_propose",
        "tico_assistant_withdraw",
        "tico_automation_get",
        "tico_automation_update",
        "tico_bot_status_history",
        "tico_bot_status_list",
        "tico_bot_status_set",
        "tico_brief",
        "tico_conversation_show",
        "tico_doc_read",
        "tico_doc_search",
        "tico_file_import",
        "tico_file_link",
        "tico_file_publish",
        "tico_file_touch",
        "tico_goal_list",
        "tico_goal_read_back",
        "tico_goal_save",
        "tico_goal_show",
        "tico_health_check",
        "tico_kpi_list",
        "tico_meeting_read",
        "tico_meeting_search",
        "tico_message_list",
        "tico_message_send",
        "tico_needs_you_commit",
        "tico_needs_you_next",
        "tico_proposal_list",
        "tico_replay_show",
        "tico_rules_add",
        "tico_settings_find",
        "tico_settings_set",
        "tico_settings_show",
        "tico_strategy_show",
        "tico_task_answer",
        "tico_task_ask",
        "tico_task_close",
        "tico_task_comment",
        "tico_task_create",
        "tico_task_done",
        "tico_task_list",
        "tico_task_move",
        "tico_task_show",
        "tico_task_update",
        "tico_team_show",
        "tico_automation_restore",
        "tico_automation_versions",
        "tico_bot_model",
        "tico_bot_pause",
        "tico_bot_resume",
        "tico_rules_retire",
        # Admin jobs 42-49: groups, bot owners and access, routines, budgets, inbox audience, Pending meetings.
        "tico_automation_create",
        "tico_bot_access",
        "tico_bot_owners",
        "tico_finance_act",
        "tico_inbound_audience",
        "tico_automation_guide",
        "tico_automation_templates",
        "tico_automation_template",
        "tico_inbox_send",
        "tico_meeting_approve",
        "tico_meeting_pending",
    }
)


def offered_to(tool_):
    """The kinds a tool is offered to. The Assistant's turn is offered the tools it is told to run (ASSISTANT_TOOLS) and
    what its audience already names, minus ASSISTANT_HIDDEN; the gate and the route decide what it may do."""
    name = tool_["name"]
    if name in AUDIENCE:
        kinds = tuple(AUDIENCE[name])
    elif tool_["writes"] and name not in ASSISTANT_WRITES:
        kinds = NOT_ASSISTANT
    else:
        kinds = KINDS
    if name in ASSISTANT_HIDDEN:
        kinds = tuple(k for k in kinds if k != "assistant")
    elif name in ASSISTANT_TOOLS and "assistant" not in kinds:
        kinds = (*kinds, "assistant")
    return (*kinds, COUNSEL) if name in COUNSEL_TOOLS else kinds


# Tools no list shows, to anyone: a bot is never offered them, and a call by name still runs (internal code calls them
# through BY_NAME). `tico_finance_card_fill` is a placeholder that only answers "not available yet".
UNLISTED = frozenset({"tico_finance_card_fill"})


def listing(local=False, kind=None):
    """`tools/list` payload: the public fields of every tool this server may offer this kind of caller (all of them
    when the kind is not known), never an UNLISTED one. Sorted by name, so the order never depends on where a tool is
    defined (and `tico_<noun>_<verb>` names keep a noun's tools together)."""
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in sorted(TOOLS, key=lambda t: t["name"])
        if t["name"] not in UNLISTED and (local or not t.get("local")) and (kind is None or kind in offered_to(t))
    ]
