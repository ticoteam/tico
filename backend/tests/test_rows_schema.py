"""backend/rows.py mirrors the hub database: each TypedDict has exactly its table's columns, and a column is optional
there only when SQLite may leave it NULL. A migration that adds or changes a column fails here until rows.py follows."""

import sqlite3
import typing
from contextlib import closing

import pytest

from backend import rows
from backend.config import Settings
from backend.store import Store

TABLES = {
    rows.TaskRow: "tasks",
    rows.AttemptRow: "attempts",
    rows.RunnerRow: "runners",
    rows.ApprovalRow: "approvals",
    rows.AttemptEventRow: "attempt_events",
    rows.PrincipalRow: "principals",
}


# Tables with no TypedDict in backend/rows.py yet. Shrink-only: a table that gains a TypedDict must leave this list
# in the same change, and adding a name here is a visible decision in review, not a way around the row shapes.
UNTYPED = frozenset({
    "agent_pairings", "agents", "archives", "ask_ingest_items", "ask_ingest_settings", "assignments",
    "assistant_actions", "attempt_capture_drops", "attempt_captures", "attempt_conversations", "attempt_diagnostics",
    "attempt_inputs", "attempt_load_snapshots", "attempt_subscriptions", "audit_log", "automation_daily",
    "automation_effects", "automation_guard_holds", "automation_holds", "automation_inboxes", "automation_mail_actions",
    "automation_runs", "automation_turn_answers", "automation_versions", "automations", "backup_verified_blobs",
    "batches", "bills_inbound", "blob_locations", "blob_media", "blob_media_retries", "blob_purges", "blobs",
    "bookkeeper_seen", "bookkeeper_watch_listed", "bot_agent_instructions", "bot_browser_exits", "bot_budget",
    "bot_config", "bot_control", "bot_file_activity", "bot_file_versions", "bot_files", "bot_mail_settings",
    "bot_memory_documents", "bot_memory_oversized", "bot_memory_seen", "bot_memory_updates", "bot_repo_access",
    "bot_session_epochs", "bot_sessions", "bot_status", "bot_status_history", "bot_task_tabs", "bot_tool_requests",
    "bot_transition_checkpoints", "bot_transitions", "bots", "brand_drafts", "brand_versions", "browser_exit_marks",
    "browser_exit_usage", "browser_exits", "calendar_actions", "cards", "changes", "chat_goals", "check_waits",
    "cloud_machine_codes", "cloud_machines", "cloud_migrations", "compact_first", "company_events", "company_profile",
    "company_storage", "computer_keys", "computer_profiles", "computer_subscriptions", "connector_snapshots",
    "conversations", "credential_grants", "credential_imports", "credential_keys", "credential_requests",
    "credential_uses", "credentials", "deferrals", "deltas", "doc_source_jobs", "doc_sources", "doc_versions", "docs",
    "enrollments", "events", "external_documents", "external_sections", "external_snapshot_docs", "external_snapshots",
    "external_versions", "feed_cursors", "feed_deliveries", "feed_sources", "feed_updates", "feed_walks",
    "file_deletions", "fin_accounts", "fin_actions", "fin_allocations", "fin_attachments", "fin_bill_lines",
    "fin_bills", "fin_books", "fin_budget_versions", "fin_budgets", "fin_card_auths", "fin_card_saves", "fin_cards",
    "fin_cash_rollups", "fin_close_snapshots", "fin_connections", "fin_contacts", "fin_credit_refunds",
    "fin_dimensions", "fin_entries", "fin_events", "fin_external_refs", "fin_feed_txns", "fin_holds", "fin_inbox",
    "fin_invoice_lines", "fin_invoices", "fin_line_tags", "fin_lines", "fin_money_accounts", "fin_outbox",
    "fin_payments", "fin_periods", "fin_private_lines", "fin_projections", "fin_provider_users", "fin_raw_imports",
    "fin_recipients", "fin_reconciliations", "fin_restatements", "fin_rights", "fin_rollups", "fin_rule_counters",
    "fin_rule_runs", "fin_rules", "fin_settings", "fin_settlements", "fin_statements", "fin_tax_jurisdictions",
    "fin_tax_seeds", "fin_transfers", "fin_usage_snapshots", "fin_vendor_aliases", "fin_vendor_labels",
    "fin_view_defaults", "fin_view_grants", "fin_view_versions", "flight_db", "flight_events", "flight_process",
    "flight_requests", "flight_slow", "flight_sql", "goal_asks", "goal_checkins", "goal_events", "goal_kpis",
    "goal_proposals", "goals", "google_connects", "google_mail_holders", "google_signin_refusals",
    "granola_connections", "growth_channel_segments", "growth_channels", "growth_cohorts", "growth_idea_channels",
    "growth_idea_segments", "growth_ideas", "growth_segments", "growth_settings", "gtm_drafts", "gtm_sources", "gtm_versions", "health_findings", "health_reports",
    "hook_relay_state", "hosting_enrollments", "human_tokens", "humans", "idempotency", "idempotency_messages",
    "import_refs", "inbound_inboxes", "inbound_messages", "inbound_sync", "intake_items", "invariant_state",
    "invite_sends", "job_recovery", "jobs", "kpi_definitions", "kpi_readings", "kpis", "learned_entries",
    "learning_cursors", "learning_deliveries", "learning_items", "learning_runs", "learnings", "lens_runs",
    "linked_docs", "listen_items", "listen_judgments", "listen_runs", "listening_destinations", "live_meeting_access",
    "live_meeting_bots", "live_meeting_chat", "live_meeting_chat_routes", "live_meeting_chunk_versions",
    "live_meeting_chunks", "live_meeting_events", "live_meeting_humans", "live_meeting_turns", "live_meeting_windows",
    "live_meetings", "mail_agent_instructions", "mail_intake", "mail_items", "mail_mailboxes", "mail_messages",
    "mail_private_backfill", "mail_reads", "main_pushes", "market_briefings", "market_citations", "market_edges",
    "market_entities", "market_entity_keys", "market_events", "market_evidence", "market_figures", "market_insights",
    "market_pages", "market_sizes", "market_sources", "market_turn_sources", "market_watches", "media_assets",
    "memory_tidy_cards",
    "media_control", "meeting_brain", "meeting_comments", "meeting_deliveries", "meeting_importers", "meeting_items",
    "meeting_versions", "meetings", "message_assets", "message_links", "message_links_dirty", "message_links_seen",
    "messages", "milestones", "model_login_cleared", "model_login_key_off", "model_logins", "model_subscriptions",
    "notes", "oauth_clients", "oauth_grants", "oidc_codes", "oidc_sessions", "org_rule_holds", "org_rule_misses",
    "org_rule_verdicts", "org_rules", "org_turn_waits", "owner_links", "pages", "perf_overloads", "perf_points",
    "perf_usage", "pin_places", "pins", "preferences", "provenance_clock", "rate_limits", "read_deps", "read_dirty",
    "read_index", "recording_source_refs", "recording_transcripts", "redesign_migrated", "refusals",
    "registry_metadata", "repositories", "roadmap_actions", "roster_clock", "row_clock", "rule_due_dates",
    "rule_due_status", "rule_events_seen", "rule_reads", "rule_sources", "rule_suggestions", "rule_task_runs",
    "rule_task_state", "rules", "run_backoff", "runner_harness_actions", "runner_harness_choices",
    "runner_load_samples", "runner_versions", "schedule_config", "schedule_occurrences", "schedules",
    "schema_migrations", "screening_pending", "screening_verdicts", "seat_requests", "seats", "service_health",
    "service_jobs", "service_key_tasks", "service_keys", "session_epochs", "settings_changes", "slack_channels",
    "slack_digests", "slack_events", "slack_posts", "slack_reads", "slack_threads", "sleep_idle_state", "stale_judged",
    "stale_suggestions", "stall_triage", "start_places", "storage_moves", "strategy_actions", "strategy_bets",
    "strategy_events", "strategy_merges", "strategy_versions", "subscription_assignments", "support_access",
    "support_access_log", "support_tickets", "system_runs", "tags", "task_assets", "task_cap_log", "task_caps",
    "task_check_models", "task_delegations", "task_events", "task_file_reviews", "task_links", "task_manager_reports",
    "task_manager_seen", "task_milestone_waits", "task_reconnects", "task_relations", "task_reminders",
    "task_requesters", "task_roles", "task_rules", "task_run_models", "task_steps", "task_tags", "task_trash",
    "task_trash_assets", "task_types", "tico_notices", "tico_storage", "trigger_bodies", "trigger_deliveries",
    "trigger_relays", "trigger_seen", "triggers", "turn_usage_segments", "turns", "update_archive_overrides",
    "update_queue", "update_reads", "update_settings", "updates", "usage_alerts", "usage_limits", "usage_pools",
    "watcher_items", "watcher_pending", "watcher_refs", "watcher_runs", "watcher_triage", "webhook_attempts",
    "webhook_events", "webhook_sends", "webhooks", "worktree_waits"
})


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    """A freshly initialized hub database: its path, and each typed table's columns (name, nullable, primary key)."""
    tmp = tmp_path_factory.mktemp("rows-schema")
    store = Store(Settings(db_path=tmp / "hub.db", registry_dir=tmp))
    store.initialize(seed_market=False)
    with closing(store.connect()) as c:
        columns = {table: {name: (notnull == 0 and pk == 0) for _, name, _, notnull, _, pk in
                           c.execute(f"PRAGMA table_info({table})")}
                   for table in TABLES.values()}
    return tmp / "hub.db", columns


@pytest.fixture(scope="module")
def columns(fresh):
    return fresh[1]


def table_names(path):
    """Every ordinary table a fresh database holds. FTS5 virtual tables and their shadow tables hold an index
    derived from other rows, so they have no row shape of their own."""
    with closing(sqlite3.connect(path)) as c:
        rows = c.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    virtual = {name for name, sql in rows if (sql or "").upper().startswith("CREATE VIRTUAL")}
    return {name for name, _ in rows if name not in virtual and not any(name.startswith(v + "_") for v in virtual)}


@pytest.mark.parametrize("shape,table", TABLES.items(), ids=TABLES.values())
def test_row_keys_match_table_columns(shape, table, columns):
    assert set(shape.__annotations__) == set(columns[table])


@pytest.mark.parametrize("shape,table", TABLES.items(), ids=TABLES.values())
def test_optional_fields_are_the_nullable_columns(shape, table, columns):
    hints = typing.get_type_hints(shape)
    optional = {name for name, hint in hints.items() if type(None) in typing.get_args(hint)}
    assert optional == {name for name, nullable in columns[table].items() if nullable}


def test_every_table_has_a_row_shape_or_is_on_the_allowlist(fresh):
    """A new table without a TypedDict fails here, and so does an allowlist entry that no longer names an untyped table."""
    path, _ = fresh
    tables = table_names(path)
    typed = set(TABLES.values())
    assert typed <= tables, typed - tables
    assert not (UNTYPED & typed), "a table with a TypedDict leaves UNTYPED: " + ", ".join(sorted(UNTYPED & typed))
    assert tables - typed - UNTYPED == set(), "new table(s) need a TypedDict in backend/rows.py: " + ", ".join(sorted(tables - typed - UNTYPED))
    assert UNTYPED - tables == set(), "stale UNTYPED entries (remove them): " + ", ".join(sorted(UNTYPED - tables))
