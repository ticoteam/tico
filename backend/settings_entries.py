"""The settings catalogue's entries (backend/settings_catalog.py): one per control in Settings that writes.

Classes follow the settings owner's table (Appendix A of the BotOps-changes-settings spec): `direct` for what is
reversible, inside the team and spends nothing new; `confirm` for spending more, people and access, sending or
publishing outside, anything destructive, team rules and ownership (BotOps acting for a person puts them to the person
as a Confirm card, which only their own tap runs); `person` for a sign-in, a secret value, an upload, or the person's
own approvals and sending (only the person, in Settings). backend/tests/test_settings_catalog.py reads every write the files in
SETTINGS_FILES make and fails when no entry, ALIASES row or NOT_SETTINGS row covers it.
"""
from dataclasses import replace

from .settings_catalog import Setting as S

SETTINGS_FILES = sorted(
    ["settings.js", "settings-access.js", "settings-bot-editor.js", "settings-bots.js", "settings-strategy-bot.js",
     "settings-company.js", "settings-devices.js", "settings-history.js", "settings-hosted.js", "settings-listening.js",
     "settings-mail.js", "settings-people.js", "settings-providers.js", "settings-recurring.js", "settings-release.js",
     "settings-repositories.js", "settings-storage.js", "settings-webhooks.js", "subscriptions.js",
     "bot-budget.js", "bot-profile.js", "usage.js", "task-pipelines.js", "tags.js", "roadmaps.js", "inbound.js",
     "cloud-machines.js"])


def _card(tab, card=""):
    return "#/settings/" + tab + ("/" + card if card else "")


GENERAL, PEOPLE, COMPUTERS, BOTS = "Settings > General", "Settings > Humans", "Settings > Computers", "Settings > Bots"
RULES, HOOKS, TASKS, REPOS = "Settings > System", "Settings > Webhooks", "Settings > Tasks", "Settings > Repositories"
INBOUND, STRATEGY = "Settings > Email & Phone", "Settings > System > Strategy bot"
MAIL = "Settings > Mail"
MODELS = "Settings > Models"

_RULE_NAMES = {
    "assistant_direct": ("Assistant acts without asking", "confirm", ""),
    "assistant_approvals": ("People may let their Assistant decide their approvals", "confirm", ""),
    "botops_direct": ("BotOps changes providers and limits without asking", "confirm", ""),
    "botops_manages_bots": ("BotOps manages every bot", "confirm", ""),
    "managers_manage_reports": ("Managers change their reports' model, tasks, name and description", "confirm", ""),
    "admin_credentials": ("Admins store credentials", "confirm", ""),
    "members_store_credentials": ("Members store credentials for their bots", "confirm", ""),
    "admin_sql": ("Admins see SQL", "confirm", ""),
    "member_tokens": ("Members make personal tokens", "confirm", ""),
    "bots_go_ahead": ("Bots go ahead on their recommendation when nobody answers", "confirm", ""),
    "support_access": ("Owners and admins may let Tico support in", "confirm", ""),
    "health_monitor_reports": ("Health monitor reports", "direct", ""),
    "stale_review": ("Review stale tasks", "direct", ""),
    "topic_fresh": ("Keep topics fresh", "direct", ""),
    "stall_triage": ("Stalled task check", "direct", ""),
    "org_rule_checks": ("Check bot outputs against rules", "direct", ""),
}
_RULE_WHERE = {"health_monitor_reports": ("Settings > Health > Health monitor", _card("health")),
               "stale_review": (RULES + " > What Tico runs on its own", _card("system", "set-system-card")),
               "topic_fresh": (RULES + " > What Tico runs on its own", _card("system", "set-system-card")),
               "stall_triage": (RULES + " > What Tico runs on its own", _card("system", "set-system-card"))}
_ADMIN_RULES = ("stale_review", "topic_fresh", "stall_triage", "org_rule_checks")

TEAM_RULES = [
    S("team.rules." + key, name, _RULE_WHERE.get(key, (PEOPLE + " > Team rules",))[0],
      "admin" if key in _ADMIN_RULES else "owner", kind, "PUT", "access/rules", key,
      card=_RULE_WHERE.get(key, ("", _card("people")))[1], read="access#rules." + key, words="team rule", why=why)
    for key, (name, kind, why) in _RULE_NAMES.items()
] + [S("team.rules.go_ahead_days", "Days a bot waits before it goes ahead", PEOPLE + " > Team rules", "owner",
       "confirm", "PUT", "access/rules", "go_ahead_days", "int", _card("people"), "access#rules.go_ahead_days",
       words="wait days go ahead"),
    S("team.rules.report_creates_per_week", "Reports a manager may create a week", PEOPLE + " > Team rules", "owner",
      "confirm", "PUT", "access/rules", "report_creates_per_week", "int", _card("people"), "access#rules.report_creates_per_week",
      words="report create limit week"),
    S("team.rules.report_archives_per_week", "Reports a manager may archive or restore a week", PEOPLE + " > Team rules", "owner",
      "confirm", "PUT", "access/rules", "report_archives_per_week", "int", _card("people"), "access#rules.report_archives_per_week",
      words="report archive limit week")]

_USD = "hourly_usd, daily_usd, weekly_usd, monthly_usd"

ENTRIES = TEAM_RULES + [
    # General
    S("team.icon", "Team icon", GENERAL + " > Team", "owner", "person", "POST", "team/icon", value="upload",
      card=_card("general", "settings-team"), why="The team icon is an upload the owner makes in Settings"),
    S("me.assistant.decides_approvals", "My Assistant decides my approvals", GENERAL + " > Assistant", "self",
      "person", "POST", "assistant/settings", "decides_approvals", card=_card("general", "settings-assistant-pane"),
      why="Who decides your approvals is yours to choose, in Settings"),
    S("assistant.on", "Turn the Assistant on", GENERAL + " > Assistant", "owner", "confirm", "POST",
      "assistant/turn-on", value="json", card=_card("general", "settings-assistant-pane")),
    S("privacy.usage_count", "Count usage", GENERAL + " > Privacy", "owner", "direct", "PUT", "system/usage-count",
      "enabled", card=_card("general", "settings-privacy"), read="system/usage-count#enabled"),
    S("privacy.usage_count.reset", "Reset the usage count", GENERAL + " > Privacy", "owner", "confirm", "POST",
      "system/usage-count/reset", value="json", card=_card("general", "settings-privacy")),
    S("company.server_connection", "Connect to the Tico company server", GENERAL + " > Tico company", "admin",
      "person", "POST", "company/server-connection", value="json", card=_card("general", "settings-company"),
      why="Only a person signed in themselves connects the company server"),
    S("company.server_connection.remove", "Disconnect the Tico company server", GENERAL + " > Tico company", "admin",
      "confirm", "DELETE", "company/server-connection", value="json", card=_card("general", "settings-company")),
    S("storage.s3", "Company storage (S3)", GENERAL + " > Storage", "owner", "person", "PUT", "system/storage",
      value="json", card=_card("general", "settings-storage"), words="bucket s3",
      why="Storage keys are typed in Settings, never passed through a bot"),
    S("storage.s3.remove", "Remove company storage", GENERAL + " > Storage", "owner", "confirm", "DELETE",
      "system/storage", value="json", card=_card("general", "settings-storage")),
    S("storage.move", "Move files to company storage", GENERAL + " > Storage", "owner", "confirm", "POST",
      "system/storage/move", value="json", card=_card("general", "settings-storage")),
    # Humans
    S("people.allowed", "Who can sign in", PEOPLE + " > Who can sign in", "owner", "confirm", "PUT", "access/allow",
      value="json", card=_card("people"), read="access#allow", words="domain sign in"),
    S("people.directory", "Directory sync", PEOPLE + " > Directory sync", "owner", "confirm", "PUT", "directory",
      value="json", card=_card("people"), read="directory", words="scim google directory"),
    S("people.directory.scim_token", "Directory SCIM token", PEOPLE + " > Directory sync", "owner", "person", "POST",
      "directory/scim-token", value="json", card=_card("people"), why="A new token is shown once, to the owner"),
    S("people.add", "Add a human", PEOPLE + " > Add a human", "anyone", "confirm", "POST", "access/humans",
      value="json", card=_card("people"), words="invite person"),
    S("people.member_bot_limit", "Member bot limit", PEOPLE + " > Team rules", "admin", "confirm", "PUT",
      "access/limits", "member_bot_limit", "int", _card("people"), "access#limits.member_bot_limit"),
    S("people.person.role", "A person's role", PEOPLE, "admin", "confirm", "POST", "access/humans/{person}", "role",
      "choice:member|admin", _card("people"), words="admin member role"),
    S("people.person.sign_in", "A person may sign in", PEOPLE, "admin", "confirm", "POST", "access/humans/{person}",
      "sign_in", card=_card("people")),
    S("people.person.create_bots", "A person may create bots", PEOPLE, "admin", "confirm", "POST",
      "access/humans/{person}", "create_bots", card=_card("people")),
    S("people.person.add_people", "A person may add people", PEOPLE, "admin", "confirm", "POST",
      "access/humans/{person}", "add_people", card=_card("people")),
    S("people.person.left", "A person has left", PEOPLE, "owner", "confirm", "POST", "humans/{person}", "left",
      card=_card("people")),
    S("people.person.delete", "Delete a person", PEOPLE, "owner", "confirm", "DELETE", "humans/{person}",
      value="json", card=_card("people")),
    S("owner.email", "Owner email", PEOPLE + " > Owner", "owner", "person", "POST", "access/owner/email",
      value="json", card=_card("people"), why="The owner's sign-in is changed only by the owner, in Settings"),
    S("owner.transfer", "Transfer ownership", PEOPLE + " > Owner", "owner", "confirm", "POST", "access/owner",
      value="json", card=_card("people")),
    # Computers
    S("team.turn_levels", "Company turn levels", MODELS + " > Limits", "admin", "confirm", "POST",
      "settings/turn-levels", value="json", card=_card("models", "settings-limits"), words="warning stop per person"),
    S("bot.agent_credential", "Agent credential", COMPUTERS + " > Connect an agent", "bot", "person", "POST",
      "bots/{bot}/agent-credential", value="json", card=_card("devices"),
      why="A new agent credential is shown once, to the person, in Settings"),
    S("bot.agent_credential.revoke", "Revoke the agent credential", COMPUTERS + " > Connect an agent", "bot",
      "confirm", "POST", "bots/{bot}/agent-credential/revoke", value="json", card=_card("devices")),
    S("agents.pairing.approve", "Approve an agent pairing", COMPUTERS + " > Connect an agent", "bot", "person",
      "POST", "agents/pairings/approve", value="json", card=_card("devices"),
      why="The pairing code is on the person's screen: they approve it in Settings"),
    S("me.tokens", "API tokens", COMPUTERS + " > API tokens", "self", "person", "POST", "me/tokens", value="json",
      card=_card("devices", "settings-tokens"), why="A personal token is shown once, to the person, in Settings"),
    S("me.tokens.revoke", "Revoke an API token", COMPUTERS + " > API tokens", "self", "confirm", "POST",
      "me/tokens/{id}/revoke", value="json", card=_card("devices", "settings-tokens")),
    S("computers.add", "Add a computer", COMPUTERS + " > Add a computer", "self", "person", "POST", "enrollments",
      value="json", card=_card("devices"), why="Enrolling a computer happens on it, from Settings"),
    S("computer.harness", "Harness on a computer", COMPUTERS, "owner", "direct", "POST",
      "computers/{computer}/harness-choices", value="json", card=_card("devices")),
    S("computer.harness.action", "Harness action", COMPUTERS, "owner", "direct", "POST",
      "computers/{computer}/harness-actions", value="json", card=_card("devices")),
    S("computer.performance", "Computer performance", COMPUTERS, "operator", "direct", "POST",
      "computers/{computer}/performance", "mode", "text", _card("devices")),
    S("computer.inbox_only", "Inbox only", COMPUTERS, "operator", "direct", "POST", "computers/{computer}/inbox-only",
      "enabled", card=_card("devices")),
    S("computer.member_bots", "Takes member bots", COMPUTERS, "admin", "confirm", "POST",
      "computers/{computer}/member-bots", "accepts", card=_card("devices")),
    S("computer.turns_at_once", "Turns at once", COMPUTERS, "admin", "direct", "POST",
      "computers/{computer}/turns-at-once", value="json", card=_card("devices")),
    S("computer.browser_exit", "Share internet with bots", COMPUTERS, "operator", "person", "POST",
      "computers/{computer}/browser-exit", value="json", card=_card("devices"),
      read="computers/{computer}/browser-exit", words="residential proxy exit browse through",
      why="Only the person whose computer it is shares its internet"),
    S("computer.revoke", "Revoke a computer", COMPUTERS, "operator", "confirm", "POST", "computers/{computer}/revoke",
      value="json", card=_card("devices")),
    S("sleep.hours", "Sleep when idle", COMPUTERS + " > Sleep when idle", "admin", "direct", "POST", "sleep", "hours",
      "number", _card("devices", "settings-sleep"), "sleep#hours"),
    S("models.providers", "Models", MODELS + " > Defaults", "owner", "confirm", "PUT", "providers", value="json",
      card=_card("models", "settings-defaults"), read="providers", words="model provider fallback"),
    S("models.gateway", "Tico AI Gateway", MODELS + " > Tico Gateway", "owner", "person", "PUT", "providers/gateway",
      value="json", card=_card("models", "settings-gateway-card"), why="A gateway key is typed in Settings, never "
      "passed through a bot"),
    S("models.gateway.automatic", "Tico AI Gateway, automatic", MODELS + " > Tico Gateway", "owner", "confirm", "POST",
      "providers/gateway/automatic", value="json", card=_card("models", "settings-gateway-card")),
    S("models.gateway.remove", "Remove the Tico AI Gateway", MODELS + " > Tico Gateway", "owner", "confirm", "DELETE",
      "providers/gateway", value="json", card=_card("models", "settings-gateway-card")),
    # Company subscriptions (backend/model_subscriptions.py): a value is pasted only in Settings; giving one to a
    # computer, or removing one in use, restarts that computer's runs on that runtime.
    S("models.subscription.add", "Add a subscription", MODELS + " > Subscriptions", "admin", "person",
      "POST", "model-subscriptions", value="json", card=_card("models", "settings-subs"),
      words="claude max chatgpt plan setup token api key login", why="A subscription's token, key or login is "
      "pasted in Settings, never passed through a bot"),
    S("models.subscription.change", "Rename or reconnect a subscription", MODELS + " > Subscriptions",
      "admin", "person", "POST", "model-subscriptions/{id}", value="json", card=_card("models", "settings-subs"),
      words="rename reconnect", why="A subscription's token, key or login is pasted in Settings, never passed "
      "through a bot"),
    S("models.subscription.remove", "Remove a subscription", MODELS + " > Subscriptions", "admin",
      "confirm", "DELETE", "model-subscriptions/{id}", value="json", card=_card("models", "settings-subs")),
    S("models.computer_subscription", "A computer's subscription", MODELS + " > Subscriptions", "admin",
      "confirm", "PUT", "computer-subscriptions", value="json", card=_card("models", "settings-subs"),
      read="computer-subscriptions", words="give assign subscription computer claude codex"),
    # Bots
    S("bot.fallback", "Fallback model", BOTS, "bot", "direct", "POST", "bots/{bot}/fallback", "fallback", "text",
      _card("bots")),
    S("bot.task_model", "Task model", BOTS, "bot", "direct", "POST", "bots/{bot}/task-model", "task_model", "text",
      _card("bots")),
    S("bot.change", "Change a bot's model or computer", BOTS, "bot", "direct", "POST", "bots/{bot}/transitions",
      value="json", card=_card("bots"), words="model effort harness computer move"),
    S("bot.browser_exit", "Browse through", BOTS + " > Bot editor", "bot", "direct", "POST", "bots/{bot}/browser-exit",
      value="json", card=_card("bots"), read="bots/{bot}/browser-exit", words="residential proxy exit internet"),
    S("bot.restore", "Restore an archived bot", BOTS, "bot", "direct", "POST", "bots/{bot}/restore", value="json",
      card=_card("bots")),
    S("bot.limit.usd", "Bot spend limit", BOTS + " > Spend limit", "bot", "confirm", "PUT", "usage/limits/{bot}",
      value="json", card=_card("bots"), read="usage/limits#bots.{bot}", words="usd dollars spend budget " + _USD),
    S("team.budget", "Run budget", BOTS + " > Run budget", "admin", "confirm", "POST", "settings/turn-budgets",
      value="json", card=_card("bots", "settings-turn-budgets"), words="turns per hour day"),
    # Hiding is display only (backend/sidebar.py), so an admin's change runs at once.
    S("company.sidebar", "Sidebar", GENERAL + " > Sidebar", "admin", "direct", "PUT", "company/sidebar", value="json",
      card=_card("general", "settings-sidebar"), read="company/sidebar#hidden_sections",
      words="sidebar sections tabs hide hidden business type operations finance growth meetings strategy"),
    S("bots.create", "New bot", BOTS + " > New bot", "anyone", "direct", "POST", "bots", value="json",
      card=_card("bots"), words="create hire"),
    S("bot.compact_at", "Compact at", BOTS + " > Bot editor", "bot", "direct", "POST", "bots/{bot}/compact-at",
      "compact_at", "int", _card("bots")),
    S("bot.manages_reports", "Manages its reports", BOTS + " > Bot editor", "bot", "direct", "POST",
      "bots/{bot}/manages-reports", "manages_reports", card=_card("bots"),
      words="manager report model task name description"),
    S("bot.keep_tasks_going", "Keep tasks going", BOTS + " > Bot editor", "bot", "direct", "POST",
      "bots/{bot}/keep-tasks-going", "keep_tasks_going", card=_card("bots")),
    S("bot.turns_at_once", "Turns at once", BOTS + " > Bot editor", "bot", "direct", "POST",
      "bots/{bot}/turns-at-once", "turns_at_once", card=_card("bots")),
    S("bot.definition", "Bot definition", BOTS + " > Bot editor", "bot", "direct", "POST", "bots/{bot}/definition",
      value="json", card=_card("bots"), words="name description reports to status repo shared"),
    S("bot.archive", "Archive a bot", BOTS + " > Bot editor", "bot", "confirm", "POST", "bots/{bot}/archive",
      value="json", card=_card("bots")),
    S("bot.owners", "Bot owners", BOTS + " > Bot editor", "bot", "confirm", "POST", "bots/{bot}/owners", "owners",
      "list", _card("bots")),
    S("bot.co_owners", "Bot co-owners", BOTS + " > Bot editor", "bot", "confirm", "POST", "bots/{bot}/co-owners",
      value="json", card=_card("bots")),
    S("bot.change.apply", "Apply a pending change without a checkpoint", BOTS + " > Bot editor", "bot", "confirm",
      "POST", "settings/transitions/{id}/apply-without-checkpoint", value="json", card=_card("bots"),
      extra={"change_without_checkpoint": True}),
    S("bot.change.cancel", "Cancel a pending change", BOTS + " > Bot editor", "bot", "direct", "POST",
      "settings/transitions/{id}/cancel", value="json", card=_card("bots")),
    S("bot.mail", "Bot mail", BOTS + " > Bot editor", "bot", "person", "POST", "bots/{bot}/mail-settings",
      value="json", card=_card("bots"), why="Where a bot's mail goes is set by a person, in Settings"),
    S("bot.access", "Who sees a bot's work", BOTS + " > Bot editor", "bot", "confirm", "PUT", "bots/{bot}/access",
      value="json", card=_card("bots"), words="see read write"),
    S("bot.budget", "Bot run budget", BOTS + " > Bot editor", "bot", "confirm", "POST", "bots/{bot}/budget",
      value="json", card=_card("bots"), words="turns per hour day"),
    S("bot.profile", "Bot profile", BOTS + " > Bot editor", "bot", "direct", "POST", "bots/{bot}/profile",
      value="json", card=_card("bots")),
    # Tico Cloud: a cloud machine, shared or its own, its size and Always on all spend credit
    # (backend/cloud_machines.py). The shared machine's size and Always on are the company's, for owners and admins.
    S("bot.cloud_machine", "Cloud machine", BOTS + " > Bot editor", "bot", "confirm", "POST",
      "bots/{bot}/cloud-machine", value="json", card=_card("bots"),
      words="cloud machine shared own size always on computer"),
    S("cloud.shared_machine", "Shared cloud machine", BOTS + " > Bot editor", "admin", "confirm", "POST",
      "cloud-machines/shared", value="json", card=_card("bots"), words="shared cloud machine size always on"),
    # Letting a bot run workflows or edit workflow files, or turning Create product repositories back on (the Owner
    # only), is the person's own click: from BotOps, a Confirm card (backend/repositories.py bot_set).
    S("bot.repositories", "Bot repositories", REPOS, "admin", "direct", "PUT", "bots/{bot}/repositories",
      value="json", card=_card("repos", "set-repos"), words="run edit workflows actions ci product",
      confirm_when=(("chosen.run_workflows", (True,)), ("chosen.edit_workflows", (True,)),
                    ("all_repos.run_workflows", (True,)), ("all_repos.edit_workflows", (True,)),
                    ("create_product_repositories", (True,))),
      why="A Run workflows or Edit workflow files tick (a kept one coming back too), turning Create product "
          "repositories back on (the owner only), or taking access away needs a Confirm card: running or editing "
          "workflows starts CI, which uses the repository's secrets"),
    # Strategy bot
    S("strategy.checks", "Strategy bot regular checks", STRATEGY, "owner", "confirm", "POST",
      "strategy-bot/checks", "on", card=_card("system", "set-strategy-bot-card")),
    S("strategy.level", "How much the Strategy bot does", STRATEGY, "owner", "confirm", "POST",
      "strategy-bot/level", "level", "int", _card("system", "set-strategy-bot-card")),
    # Repositories
    S("repo.enabled", "Repository on", REPOS, "admin", "direct", "PUT", "repositories/{id}", "enabled",
      card=_card("repos", "set-repos")),
    S("repo.setup_command", "Repository setup command", REPOS, "admin", "direct", "PUT", "repositories/{id}",
      "setup_command", "text", _card("repos", "set-repos")),
    S("repos.new_bot_default", "New bots get repositories", REPOS, "admin", "direct", "PUT", "repositories/settings",
      "new_bot_default", "text", _card("repos", "set-repos")),
    S("repos.product.create", "New product repository", REPOS, "owner", "confirm", "POST", "github/product-repos",
      value="json", card=_card("repos", "set-repos")),
    # Rules
    S("rules.system", "What Tico runs on its own", RULES + " > What Tico runs on its own", "admin", "direct", "PUT",
      "system-routines/{id}", value="json", card=_card("system", "set-system-card"), words="schedule routine"),
    S("rules.task_rule", "Task rule", RULES + " > What Tico runs on its own", "anyone", "direct", "PUT", "task-rules/{id}",
      value="json", card=_card("system", "set-system-card")),
    S("rules.task_rule.create", "New task rule", RULES + " > What Tico runs on its own", "anyone", "direct", "POST",
      "task-rules", value="json", card=_card("system", "set-system-card")),
    S("release.on", "Daily release", RULES + " > Releases", "admin", "direct", "PATCH", "release-schedule", "on",
      card=_card("system", "set-release-card"), read="release-schedule#on"),
    S("release.times", "Release times", RULES + " > Releases", "admin", "direct", "PATCH", "release-schedule",
      "times", "list", _card("system", "set-release-card"), "release-schedule#times", words="release schedule"),
    S("release.bot", "Release bot", RULES + " > Releases", "admin", "direct", "PATCH", "release-schedule", "bot",
      "text", _card("system", "set-release-card"), "release-schedule#bot"),
    S("release.writer", "Release notes writer", RULES + " > Releases", "admin", "direct", "PATCH",
      "release-schedule", "writer", "text", _card("system", "set-release-card"), "release-schedule#writer"),
    *[S("screening." + source, "Screening: " + name, RULES + " > Screening", "admin", "direct", "PUT", "screening",
        source, "choice:off|warn" + ("" if source == "mail" else "|hold"), _card("system", "set-screening-card"),
        "screening#modes." + source, words="prompt injection screening")
      for source, name in (("webhook", "Webhooks"), ("mail", "Mail"), ("idea", "Ideas"), ("watcher", "Watchers"), ("document", "Documents"))],
    *[S("screening." + layer, "Screening: " + name, RULES + " > Screening", "admin", "direct", "PUT", "screening",
        layer, card=_card("system", "set-screening-card"), read="screening#layers." + layer,
        words="prompt injection screening detector")
      for layer, name in (("patterns", "Patterns"), ("model", "Decision model"))],
    S("task_manager.on", "Task Manager", RULES + " > Task Manager", "owner", "direct", "POST", "task-manager/turn-on",
      value="json", card=_card("system", "set-task-manager-card")),
    S("task_manager.off", "Task Manager off", RULES + " > Task Manager", "owner", "direct", "POST",
      "task-manager/turn-off", value="json", card=_card("system", "set-task-manager-card")),
    # Webhooks: `incoming`, `outgoing` and `gateway` are the Webhooks tab's own parts, which ui/app/router.js opens from
    # `#/settings/webhooks/<part>` before the catalogue's generic `#/settings/<tab>/<card>` branch: rename them
    # together.
    S("webhooks.incoming.create", "New incoming trigger", HOOKS + " > Incoming", "anyone", "direct", "POST", "triggers",
      value="json", card=_card("webhooks", "incoming"), words="webhook incoming"),
    S("webhooks.incoming", "Incoming trigger", HOOKS + " > Incoming", "creator", "direct", "POST", "triggers/{id}",
      value="json", card=_card("webhooks", "incoming"), words="webhook incoming keep days hourly"),
    S("webhooks.incoming.signing", "Incoming trigger signing", HOOKS + " > Incoming", "creator", "person", "POST",
      "triggers/{id}/signing", value="json", card=_card("webhooks", "incoming"),
      why="A signing secret is typed in Settings, never passed through a bot"),
    S("webhooks.incoming.rotate", "Rotate an incoming trigger's address", HOOKS + " > Incoming", "creator", "confirm",
      "POST",
      "triggers/{id}/rotate", value="json", card=_card("webhooks", "incoming")),
    S("webhooks.incoming.delete", "Delete an incoming trigger", HOOKS + " > Incoming", "creator", "confirm", "POST",
      "triggers/{id}/delete", value="json", card=_card("webhooks", "incoming")),
    S("webhooks.gateway.wake", "Gateway wake", HOOKS + " > Gateway", "creator", "confirm", "POST",
      "triggers/{id}/relay/wake", "wake", card=_card("webhooks", "gateway")),
    S("hooks.wake_url", "Gateway wake URL", HOOKS + " > Gateway", "owner", "confirm", "PUT", "hooks/wake-url", "url",
      "text", _card("webhooks", "gateway")),
    S("webhooks.outgoing.create", "New outgoing webhook", HOOKS + " > Outgoing", "anyone", "confirm", "POST", "webhooks",
      value="json", card=_card("webhooks", "outgoing")),
    S("webhooks.outgoing", "Outgoing webhook", HOOKS + " > Outgoing", "creator", "confirm", "POST", "webhooks/{id}/update",
      value="json", card=_card("webhooks", "outgoing")),
    # Listening
    S("listening.destination", "Listening destination", "Settings > Listening", "owner", "confirm", "PUT",
      "listening/destinations/{id}", value="json", card=_card("listening")),
    S("listening.destination.remove", "Remove a listening destination", "Settings > Listening", "owner", "confirm",
      "POST", "listening/destinations/{id}/remove", value="json", card=_card("listening")),
    # Tasks
    S("tasks.type", "Task type", TASKS + " > Task types", "mover", "direct", "POST", "task-types/{id}", value="json",
      card=_card("tasks", "settings-types"), words="steps pipeline"),
    S("tasks.type.create", "New task type", TASKS + " > Task types", "mover", "direct", "POST", "task-types",
      value="json", card=_card("tasks", "settings-types")),
    S("tasks.type.delete", "Delete a task type", TASKS + " > Task types", "mover", "confirm", "POST",
      "task-types/{id}/delete", value="json", card=_card("tasks", "settings-types")),
    S("roadmap.public", "Public roadmap", TASKS + " > Public roadmap", "admin", "confirm", "POST", "roadmaps/settings",
      value="json", card=_card("tasks", "settings-roadmaps"), words="publish public"),
    S("tasks.tag.create", "New task tag", TASKS + " > Task tags", "anyone", "direct", "POST", "tags", value="json",
      card=_card("tasks")),
    S("tasks.tag", "Task tag", TASKS + " > Task tags", "anyone", "direct", "POST", "tags/{id}", value="json",
      card=_card("tasks")),
    # Email & Phone
    S("inbound.company_connection", "Email & phone company connection", INBOUND, "admin", "person", "POST",
      "inbound/company-connection", value="json", card=_card("inbound", "set-inbound"),
      why="Only a person signed in themselves connects company email and phone"),
    S("inbound.inbox.create", "New inbox", INBOUND, "anyone", "direct", "POST", "inbound/inboxes", value="json",
      card=_card("inbound", "set-inbound")),
    S("inbound.inbox", "Inbox", INBOUND, "anyone", "direct", "PATCH", "inbound/inboxes/{id}", value="json",
      card=_card("inbound", "set-inbound")),
    S("inbound.inbox.release", "Release an inbox", INBOUND, "admin", "confirm", "POST", "inbound/inboxes/{id}/release",
      value="json", card=_card("inbound", "set-inbound")),
    S("inbound.phone_request", "Ask for a phone number", INBOUND, "admin", "confirm", "POST",
      "inbound/phone-requests", value="json", card=_card("inbound", "set-inbound")),
    S("inbound.phone_request.accept", "Accept a phone number's price", INBOUND, "admin", "confirm", "POST",
      "inbound/phone-requests/{id}/accept", value="json", card=_card("inbound", "set-inbound")),
    S("inbound.phone_request.cancel", "Cancel a phone request", INBOUND, "admin", "direct", "POST",
      "inbound/phone-requests/{id}/cancel", value="json", card=_card("inbound", "set-inbound")),
    # Usage
    S("mail.address.order", "Get an address", MAIL + " > Addresses", "admin", "confirm", "POST", "postal/orders",
      value="json", card=_card("mail", "settings-mail-addresses"),
      words="physical mail mailbox postal address registered agent form 1583"),
    S("mail.intake", "Mail-intake inbox", MAIL + " > Bring your own", "admin", "direct", "PUT", "postal/intake",
      value="json", card=_card("mail", "settings-mail-intake"), read="postal/intake#intake",
      words="physical mail scans email in postal provider"),
    S("team.limit.usd", "Company spend limit", "Usage > Company", "admin", "confirm", "PUT", "usage/limits",
      value="json", card="#/usage", read="usage/limits#default", words="usd dollars spend " + _USD),
    S("pool.limit.usd", "Pool spend limit", "Usage > Pools", "admin", "confirm", "PUT", "usage/pools/{id}",
      value="json", card="#/usage", words="usd dollars spend group " + _USD),
]

# Confirm entries whose route BotOps can't yet put on a Confirm card (backend/assistant.py valid_operation): a person
# changes them in Settings until it can, rather than BotOps changing them at once.
NO_CARD_YET = ("assistant.on", "privacy.usage_count.reset", "company.server_connection.remove", "storage.s3.remove",
               "storage.move", "people.person.delete", "me.tokens.revoke", "team.turn_levels",
               "bot.budget", "strategy.checks", "strategy.level",
               "repos.product.create", "hooks.wake_url", "webhooks.outgoing.create",
               "listening.destination", "listening.destination.remove", "roadmap.public",
               "inbound.inbox.release", "inbound.phone_request", "inbound.phone_request.accept", "pool.limit.usd",
               "bot.cloud_machine", "cloud.shared_machine", "mail.address.order")
_NO_CARD_WHY = " needs a person's yes, and BotOps can't ask for it on a Confirm card yet"
ENTRIES = [(replace(e, when_held="person", why=e.name + " that sends outside" + _NO_CARD_WHY) if e.confirm_when else
            replace(e, kind="person", why=e.name + _NO_CARD_WHY)) if e.key in NO_CARD_YET else e for e in ENTRIES]

# A write whose path the page builds in code, so the test reads it with a placeholder where a whole segment or more
# goes: the entry that covers it.
ALIASES = {
    ("POST", "{x}/invite"): "people.add",
    ("POST", "access/humans/{x}/invite"): "people.add",
    ("POST", "{x}/password"): "people.add",
    ("POST", "{x}/{x}"): "webhooks.outgoing",
    ("POST", "runners/{x}/inbox-only"): "computer.inbox_only",
    ("POST", "inbound/phone-requests/{x}"): "inbound.phone_request.accept",
    ("POST", "system-routines/{x}"): "rules.system",
    ("POST", "tags/{x}"): "tasks.tag",
}

# Writes on a Settings page that change no setting: one-off actions, and why each needs no entry.
NOT_SETTINGS = {
    ("POST", "approvals/{x}/undo"): "undoes one approval; nothing to set",
    ("POST", "jobs/{x}/reconcile"): "settles one stopped run; nothing to set",
    ("POST", "system/storage/tico/check"): "checks the storage connection",
    ("POST", "setup/company/people/{x}/invite"): "sends one invitation from setup",
    ("POST", "providers/gateway/check"): "checks the gateway connection",
    ("POST", "hosted-servers/{x}/start"): "starts one hosted server",
    ("POST", "settings/history/{x}/undo"): "undoes one past change; the owner does it from History",
    ("POST", "strategy-bot/log/{x}/undo"): "undoes one Strategy bot action",
    ("POST", "repositories/refresh"): "re-reads the repositories from GitHub",
    ("POST", "release/now"): "starts one release (`hub release now`)",
    ("POST", "screening/test"): "runs screening's built-in samples; nothing is kept",
    ("POST", "{x}/deliveries/{x}/replay"): "replays one delivery",
    ("POST", "{x}/sends/{x}/retry"): "retries one send",
    ("POST", "tasks/{x}/comments/{x}/moderate"): "moderates one comment on a public roadmap task",
    ("POST", "tasks/{x}/requesters/{x}/delete"): "removes one requester from a public roadmap task",
    ("POST", "tasks/{x}/requesters{x}"): "adds one requester to a public roadmap task",
    ("POST", "postal/items/{x}/retry"): "tries one failed letter again",
}
