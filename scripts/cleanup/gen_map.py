"""Generate scripts/cleanup/renames.yaml and backend/migrations/renames_data.py from the pre-cleanup schema.

The schema is read from backend/tests/fixtures/schema_pre_cleanup.sql (a fresh database at the base commit, before
any rename). On a newer main, rebuild that fixture from main first (`--refresh-fixture`, run on a tree without the
renames), then run this again and re-run scripts/cleanup/rename.py.

The rules are cleanup-spec.md's; every judgment call lives in the tables below (OVERRIDES, M3, WAVE3), never in
hand edits of the YAML, so the map regenerates the same way every time.

    python3 scripts/cleanup/gen_map.py              # write renames.yaml and backend/migrations/renames_data.py
    python3 scripts/cleanup/gen_map.py --check      # exit 1 if either file is stale
"""

import argparse
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "backend/tests/fixtures/schema_pre_cleanup.sql"
# Every table the commit before the renames can hold, with those made only on first use (gen_upgrade_fixture.py).
EVERY_TABLE = ROOT / "backend/tests/fixtures/schema_old_main.sql"
YAML_OUT = ROOT / "scripts/cleanup/renames.yaml"
DATA_OUT = ROOT / "backend/migrations/renames_data.py"
BASE = "29fcaaf6e"

# Waves whose column renames the server applies at startup (backend/migrations/renames.py) and the codemod has applied:
# `--applied 1,2` sets them; without it they stay what renames.yaml says.
APPLIED_WAVES = (1,)

# Names SQLite refuses as bare identifiers in ordinary statements (tested: to, when, values, order, group, limit),
# plus keywords that parse but read as SQL (end, start, with): an M2 result here takes the override instead.
SQL_UNSAFE = {"to", "when", "values", "order", "group", "limit", "end", "start", "with", "from", "select", "where",
              "table", "index", "default", "check", "references", "primary", "unique", "null", "not", "and", "or",
              "in", "is", "as", "on", "by", "case", "then", "else", "join", "set", "into"}

# M2 results that need a different name: (table, old) -> (new, why). CHECK marks a name the owner should confirm.
OVERRIDES = {
    ("mail_messages", "to_json"): ("to_addresses", "`to` is an SQLite keyword"),
    ("fin_rules", "when_json"): ("condition", "`when` is an SQLite keyword"),
    ("rules", "when_json"): ("condition", "`when` is an SQLite keyword"),
    ("attempt_load_snapshots", "start_json"): ("at_start", "`start` reads as SQL"),
    ("attempt_load_snapshots", "end_json"): ("at_end", "`end` is an SQLite keyword"),
    ("org_rules", "with_json"): ("with_scope", "`with` is an SQLite keyword"),
}
# M2 results worth a note.
M2_NOTES = {
    ("automation_runs", "state_json"): "wave 3 renames it saved_state (FOLLOW_UPS)",
    ("task_trash", "rows_json"): "`rows` is a window-frame keyword (parses as a column)",
    ("webhooks", "filter_json"): "`filter` is a window keyword (parses as a column)",
}

# M3, uniform: every table with the column renames it the same way. `scope` "global" means the codemod renames the
# name in every SQL string and key context; "scoped" means only where the table is known (common English words).
M3_UNIFORM = {
    # name: (new, scope, note)
    "started": ("started_at", "global", ""),
    "finished": ("finished_at", "global", ""),
    "last_seen": ("last_seen_at", "global", ""),
    "first_seen": ("first_seen_at", "global", ""),
    "expires": ("expires_at", "global", ""),
    "code_expires": ("code_expires_at", "global", ""),
    "refresh_expires": ("refresh_expires_at", "global", ""),
    "definition_updated": ("definition_updated_at", "global", ""),
    "next_due": ("next_due_at", "scoped", "also a computed field of automation and schedule views"),
    "due": ("due_at", "scoped", "tasks, deferrals, task_reminders; also a trigger type and finance documents' field"),
    "first_due": ("first_due_on", "scoped", "a date, not a time; a due trigger's JSON key stays `first_due`"),
    "last_used": ("last_used_at", "global", ""),
    "last_fired": ("last_fired_at", "global", ""),
    "last_checked": ("last_checked_at", "global", ""),
    "last_failed": ("last_failed_at", "global", ""),
    "last_success": ("last_succeeded_at", "global", ""),
    "last_sync": ("last_synced_at", "global", "REAL epoch seconds"),
    "last_routed": ("last_routed_at", "global", ""),
    "last_run": ("last_run_at", "scoped", "also an automation answer's last run object (automations.py, ui)"),
    "last_verified": ("last_verified_at", "global", ""),
    "last_woke": ("last_woke_at", "global", ""),
    "next_check": ("next_check_at", "global", "REAL epoch seconds"),
    "next_try": ("next_try_at", "global", ""),
    "next_attempt": ("next_attempt_at", "global", ""),
    "first_started": ("first_started_at", "global", ""),
    "restart_requested": ("restart_requested_at", "global", ""),
    "stop_requested": ("stop_requested_at", "global", ""),
    "fetched": ("fetched_at", "scoped", ""),
    "published": ("published_at", "scoped", ""),
    "delivered": ("delivered_at", "scoped", ""),
    "processed": ("processed_at", "scoped", ""),
    "received": ("received_at", "scoped", ""),
    "deleted": ("deleted_at", "scoped", ""),
    "edited": ("edited_at", "scoped", ""),
    "committed": ("committed_at", "scoped", ""),
    "reported": ("reported_at", "scoped", ""),
    "imported": ("imported_at", "scoped", ""),
    "indexed": ("indexed_at", "scoped", ""),
    "decided": ("decided_at", "scoped", ""),
    "revoked": ("revoked_at", "scoped", ""),
    "rotated": ("rotated_at", "scoped", ""),
    "settled": ("settled_at", "scoped", ""),
    "saved": ("saved_at", "scoped", ""),
    "queued": ("queued_at", "scoped", ""),
    "marked": ("marked_at", "scoped", ""),
    "logged": ("logged_at", "scoped", ""),
    "retired": ("retired_at", "scoped", ""),
    "nudged": ("nudged_at", "scoped", ""),
    "ended": ("ended_at", "scoped", ""),
    "cleared": ("cleared_at", "scoped", ""),
    "changed": ("changed_at", "scoped", ""),
    "checked": ("checked_at", "scoped", ""),
}

# M3, per table (bare ts/at/since/until/seen/...): judgment per table, all CHECK until reviewed. Wave 2b.
M3_PER_TABLE = {
    # events, logs and samples: the row's own time -> created_at
    **{(t, "ts"): ("created_at", "row time") for t in (
        "events", "task_events", "goal_events", "market_events", "company_events", "deltas", "refusals",
        "goal_checkins", "support_access_log", "flight_requests", "flight_slow", "flight_process", "flight_sql",
        "flight_db", "flight_events", "runner_load_samples", "perf_points", "perf_usage")},
    ("kpi_readings", "ts"): ("measured_at", "the reading's time (cleanup-spec)"),
    ("kpi_definitions", "ts"): ("created_at", "when this definition version was written"),
    **{(t, "at"): ("created_at", "row time") for t in (
        "audit_log", "automation_effects", "automation_holds", "automation_turn_answers", "automation_versions",
        "changes", "google_signin_refusals", "market_turn_sources", "strategy_events", "webhook_attempts")},
    ("credential_uses", "at"): ("used_at", "newest use, not row creation"),
    ("mail_reads", "at"): ("read_at", ""),
    ("milestones", "at"): ("reached_at", ""),
    ("redesign_migrated", "at"): ("migrated_at", ""),
    ("redesign_migration_accepts", "at"): ("accepted_at", ""),
    ("rule_due_status", "at"): ("satisfied_at", ""),
    ("rule_task_runs", "at"): ("ran_at", ""),
    ("task_rules", "at"): ("daily_at", "a daily rule's time of day (HH:MM), not a timestamp"),
    ("bot_status", "since"): ("changed_at", "when the current state began"),
    ("bot_status_history", "since"): ("started_at", ""),
    ("bot_status_history", "until"): ("ended_at", ""),
    ("automation_guard_holds", "since"): ("held_at", ""),
    ("invariant_state", "since"): ("started_firing_at", ""),
    ("feed_cursors", "since"): ("cursor_at", ""),
    ("market_edges", "since"): ("valid_from_on", "a date (market._day keeps the first ten characters)"),
    ("market_edges", "until"): ("valid_until_on", "a date (market._day keeps the first ten characters)"),
    ("bot_memory_seen", "seen"): ("seen_at", ""),
    ("bookkeeper_seen", "seen"): ("seen_at", ""),
    ("task_manager_seen", "seen"): ("seen_at", ""),
    ("google_mail_holders", "seen"): ("seen_at", "REAL epoch seconds"),
    ("perf_overloads", "last"): ("last_seen_at", "INTEGER epoch seconds"),
    ("feed_walks", "next"): ("next_walk_at", ""),
}
# Left as they are (not bare, or not times): *_since, *_until (snoozed_until, lease_until, blocked_since, ...),
# dates (*_date, day, due_on), Slack message ids (slack_events.ts, thread_ts, reply_ts, slack_ts, last_ts), version
# counters, and booleans that are fine Rails names (enabled, archived, private, public).

# Wave 3 (proposed, not applied): actor columns in the stable SQL set, plus operator/grp/dept.
STABLE = ("tasks", "messages", "conversations", "bots", "humans", "principals", "jobs", "attempts", "automations",
          "automation_runs", "goals", "goal_kpis", "kpis", "kpi_readings", "bot_files", "pages", "meetings",
          "approvals", "cards", "docs", "task_types", "task_steps", "runners")
ACTOR_NAMES = {"owner": "owner_id", "requester": "requester_id", "bot": "bot_id", "actor": "actor_id"}
NOT_ACTOR_BY = {("tasks", "carried_by"): "the attempt id that carried the task, not an actor",
                ("meetings", "owner"): "an email, not an actor; Release 2 names it",
                ("meetings", "recorded_by"): "an email or a runner id, not an actor; Release 2 names it",
                ("meetings", "uploaded_by"): "an email or a runner id, not an actor; Release 2 names it"}
# Named like a time, holds something else: left alone. task_reconnects.ended is how the card ended (fixed, cancelled, retired).
M3_KEPT = {("task_reconnects", "ended")}

WAVE3_NOTES = {("pages", "replaced_by"): "holds the replacing page's file id, not an actor"}
# Later renames of a column an applied wave already renamed: (table, name now) -> (new, wave, why).
FOLLOW_UPS = {("automation_runs", "state"): ("saved_state", "3", "the run's saved engine state (JSON); `state` is kept for lifecycle")}
WAVE3_SPECIAL = {
    ("messages", "from_actor"): "sender_id",
    ("messages", "to_actor"): "recipient_id",
    ("conversations", "owner_actor"): "owner_id",
    ("automations", "grp"): "group_id",
    ("bot_config", "operator"): "owner_id",
    ("runners", "operator"): "owner_id",
    ("enrollments", "operator"): "owner_id",
}
API_FIELDS = {"attempt_id": "run_id", "attempt": "run", "from_actor_name": "sender_name",
              "to_actor_name": "recipient_name", "operator_name": "owner_name",
              "ts": "created_at", "at": "created_at", "since": "changed_at"}
# A renamed column's old name the API still says for something else (a hand-built key, a count, a table outside wave 3).
API_KEPT = ["actor", "bot", "last_run", "next", "queued", "state", "status_by", "updated_by"]
# Where one golden says a name differently from the rest (forms: e2e/renames.py).
GOLDEN_FIELDS = {
    "bot_files": {".bot": "bot_id"},
    "events": {"bot!status": "bot_id", "status.bot": "bot"},
    "goal_detail": {"latest.actor": "actor_id", "goal.status_by": "status_by_id"},
    "goals": {"status_by": "status_by_id", ".owner": "owner"},
    "kpi_readings": {"actor": "actor_id", "ts": "measured_at"},
    "kpis": {"actor": "actor_id"},
    "pages": {"bot": "bot_id"},
    "task_detail": {"execution.bot": "bot_id"},
}
# Keys an answer gained: a conversation's owner_id now gets a display name, as every owner_id does.
GOLDEN_ADDED = {"conversations": ["conversations.owner_name"], "messages": ["conversation.owner_name"]}


def load_schema(sql_path):
    """{table: [columns]} for real tables (FTS shadow tables and sqlite_* left out), plus the virtual tables. The tables
    made only on first use come from EVERY_TABLE: a dump of a fresh hub does not have them."""
    tables, virtual = _one_schema(sql_path)
    if EVERY_TABLE.exists():
        every, every_virtual = _one_schema(EVERY_TABLE)
        for name, cols in every.items():
            tables.setdefault(name, cols)
        virtual |= every_virtual
    return tables, virtual


def _one_schema(sql_path):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "schema.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(sql_path.read_text())
        conn.close()
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        virtual = {name for name, sql in rows if sql and sql.upper().startswith("CREATE VIRTUAL")}
        shadow = {f"{v}_{s}" for v in virtual for s in ("data", "idx", "content", "docsize", "config")}
        tables = {}
        for name, sql in rows:
            if name.startswith("sqlite_") or name in shadow:
                continue
            tables[name] = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
        conn.close()
    return tables, virtual


def build(tables, virtual):
    columns = {}          # (table, old) -> new
    notes = {}            # (table, old) -> comment
    waves = {"1": [], "2": [], "2s": [], "2b": [], "3": []}
    collisions = []

    def add(wave, table, old, new, note=""):
        if table in virtual:
            return
        existing = set(tables[table])
        taken = {n for (t, _), n in columns.items() if t == table}
        if new in existing or new in taken:
            collisions.append(f"{table}.{old} -> {new}: {table} already has {new}")
            return
        columns[(table, old)] = new
        if note:
            notes[(table, old)] = note
        waves[wave].append((table, old))

    # M2: drop _json.
    for table, cols in sorted(tables.items()):
        for col in cols:
            if col.endswith("_json"):
                new, note = OVERRIDES.get((table, col), (col[:-5], M2_NOTES.get((table, col), "")))
                if new in SQL_UNSAFE:
                    raise SystemExit(f"{table}.{col} -> {new} is an SQL keyword: add an OVERRIDES entry")
                add("1", table, col, new, note)
    # M1 + M3 uniform.
    for table, cols in sorted(tables.items()):
        for col in cols:
            if col == "created":
                add("2", table, col, "created_at")
            elif col == "updated":
                add("2", table, col, "updated_at")
            elif col in M3_UNIFORM and (table, col) not in M3_PER_TABLE and (table, col) not in M3_KEPT:
                new, scope, note = M3_UNIFORM[col]
                # A scoped name is a common word (queued, checked, deleted) that is also a counter, a status or a
                # flag all over the code, so table-aware renaming cannot settle most of its sites: wave 2s is
                # mapped and reported, applied later by hand per table.
                add("2" if scope == "global" else "2s", table, col, new, note)
    # M3 per table.
    for (table, col), (new, note) in sorted(M3_PER_TABLE.items()):
        if table in tables and col in tables[table]:
            add("2b", table, col, new, note)
    # Wave 3.
    for table in STABLE:
        for col in tables.get(table, []):
            if (table, col) in WAVE3_SPECIAL:
                add("3", table, col, WAVE3_SPECIAL[(table, col)])
            elif col in ACTOR_NAMES and (table, col) not in NOT_ACTOR_BY:
                add("3", table, col, ACTOR_NAMES[col])
            elif col.endswith("_by") and (table, col) not in NOT_ACTOR_BY:
                add("3", table, col, col + "_id", WAVE3_NOTES.get((table, col), ""))
    for (table, col), new in WAVE3_SPECIAL.items():
        if table not in STABLE and table in tables and col in tables[table]:
            add("3", table, col, new)
    for table, cols in sorted(tables.items()):
        if "dept" in cols:
            add("3", table, "dept", "department")
    for (table, old), (new, wave, note) in FOLLOW_UPS.items():
        columns[(table, old)] = new
        notes[(table, old)] = note
        waves[wave].append((table, old))
    return columns, notes, waves, collisions


def scopes(columns, waves, tables):
    """Per wave: names renamed the same way on every table that has them (global) vs per table (scoped)."""
    out = {}
    for wave, entries in waves.items():
        by_name = {}
        for table, old in entries:
            by_name.setdefault(old, {})[table] = columns[(table, old)]
        glob, scoped = {}, {}
        for old, per_table in sorted(by_name.items()):
            holders = {t for t, cols in tables.items() if old in cols}
            uniform = len(set(per_table.values())) == 1 and set(per_table) == holders
            forced = wave == "2" and M3_UNIFORM.get(old, ("", "global"))[1] == "scoped"
            if wave in ("1", "2") and uniform and not forced:
                glob[old] = next(iter(per_table.values()))
            else:
                for t, new in per_table.items():
                    scoped[f"{t}.{old}"] = new
        out[wave] = (glob, scoped)
    return out


def q(s):
    return f'"{s}"' if any(ch in s for ch in ":#{}[],&*?|-<>=!%@`'\" ") or s in ("yes", "no", "on", "off", "null") \
        else s


def render_yaml(columns, notes, waves, collisions, by_scope):
    rules = {"1": "M2: every *_json column drops the suffix (stored encoding unchanged)",
             "2": "M1 created/updated -> created_at/updated_at; M3 uniform bare time columns -> <verb>_at",
             "2s": "M3 per table, common words (queued, checked, deleted...) -> <verb>_at (needs review, not "
                   "applied: most sites cannot be told apart from counters, statuses and flags)",
             "2b": "M3 per table: bare ts/at/since/until/seen -> named times (needs review, not applied)",
             "3": "actor columns in the stable SQL set -> <role>_id; operator -> owner_id; grp -> group_id; "
                  "dept -> department (needs review, not applied)"}
    lines = [
        "# Generated by scripts/cleanup/gen_map.py from backend/tests/fixtures/schema_pre_cleanup.sql and the rules",
        "# in cleanup-spec.md. Do not edit by hand: change the tables in gen_map.py and regenerate.",
        "# `# CHECK` marks a name the integration owner should confirm before the map is frozen.",
        f"base: {BASE}",
        f"applied_waves: [{', '.join(str(w) for w in APPLIED_WAVES)}]",
        "tables: {}",
        "columns:",
    ]
    for (table, old), new in sorted(columns.items()):
        note = notes.get((table, old))
        lines.append(f"  {table}.{old}: {new}" + (f"  # {note}" if note else ""))
    lines += ["values: {}", "api_fields:"]
    lines += [f"  {k}: {v}" for k, v in API_FIELDS.items()]
    lines.append(f"api_kept: [{', '.join(API_KEPT)}]")
    lines.append("golden_fields:")
    lines += [f"  {name}: {{{', '.join(f'{q(k)}: {v}' for k, v in fields.items())}}}"
              for name, fields in GOLDEN_FIELDS.items()]
    lines.append("golden_added:")
    lines += [f"  {name}: [{', '.join(keys)}]" for name, keys in GOLDEN_ADDED.items()]
    lines.append("waves:")
    for wave in ("1", "2", "2s", "2b", "3"):
        glob, scoped = by_scope[wave]
        lines.append(f'  "{wave}":')
        lines.append(f"    rule: {q(rules[wave])}")
        lines.append(f"    count: {len(waves[wave])}")
        lines.append("    global:" + (" {}" if not glob else ""))
        for old, new in glob.items():
            n = sum(1 for t, o in waves[wave] if o == old)
            lines.append(f"      {old}: {new}  # {n} table{'s' if n != 1 else ''}")
        lines.append("    scoped:" + (" {}" if not scoped else ""))
        for key, new in sorted(scoped.items()):
            t, o = key.split(".", 1)
            note = notes.get((t, o))
            lines.append(f"      {key}: {new}" + (f"  # {note}" if note else ""))
    lines.append("collisions:" + (" []" if not collisions else ""))
    lines += [f"  - {q(c)}" for c in collisions]
    return "\n".join(lines) + "\n"


def render_data(columns, waves):
    applied = [(t, o, columns[(t, o)], w) for w in waves for t, o in waves[w]
               if w in {str(a) for a in APPLIED_WAVES}]
    lines = ['"""Generated by scripts/cleanup/gen_map.py from scripts/cleanup/renames.yaml: do not edit.',
             "",
             "The column and table renames backend/migrations/renames.py applies at startup, as (table, old, new, wave).",
             '"""',
             "",
             "TABLES = ()",
             "",
             "COLUMNS = ("]
    lines += [f"    ({t!r}, {o!r}, {n!r}, {w!r})," for t, o, n, w in sorted(applied)]
    lines += [")", ""]
    return "\n".join(lines)


def refresh_fixture():
    """Rebuild the fixture from the current tree (run it on main, before any rename)."""
    from backend.config import Settings
    from backend.store import Store
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "hub.sqlite"
        Store(Settings(db_path=db)).initialize()
        conn = sqlite3.connect(db)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        dump = "\n".join(conn.iterdump())
        conn.close()
    FIXTURE.write_text(
        "-- The schema and seed rows of a fresh database before the cleanup renames (backend/migrations/renames.py).\n"
        "-- backend/tests/test_schema_renames.py upgrades it; scripts/cleanup/gen_map.py reads the schema from it.\n"
        + dump + f"\nPRAGMA user_version={version};\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="exit 1 if the outputs are stale")
    parser.add_argument("--refresh-fixture", action="store_true", help="rebuild the fixture from this tree first")
    parser.add_argument("--applied", help="the waves the server and the code have taken, e.g. 1,2 (default: as now)")
    args = parser.parse_args()
    global APPLIED_WAVES
    if args.applied is not None:
        APPLIED_WAVES = tuple(w.strip() for w in args.applied.split(",") if w.strip())
    elif YAML_OUT.exists():
        found = re.search(r"^applied_waves: \[(.*)\]$", YAML_OUT.read_text(), re.M)
        APPLIED_WAVES = tuple(w.strip() for w in found.group(1).split(",") if w.strip()) if found else ()
    if args.refresh_fixture:
        sys.path.insert(0, str(ROOT))
        refresh_fixture()
    tables, virtual = load_schema(FIXTURE)
    columns, notes, waves, collisions = build(tables, virtual)
    by_scope = scopes(columns, waves, tables)
    outputs = {YAML_OUT: render_yaml(columns, notes, waves, collisions, by_scope),
               DATA_OUT: render_data(columns, waves)}
    stale = [p for p, text in outputs.items() if not p.exists() or p.read_text() != text]
    if args.check:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if stale else 0
    for p in stale:
        p.write_text(outputs[p])
    counts = {w: len(e) for w, e in waves.items()}
    print(f"{len(tables)} tables; renames per wave {counts}; {len(collisions)} collisions")
    for c in collisions:
        print("  collision:", c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
