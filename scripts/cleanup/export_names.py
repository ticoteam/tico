"""Write clients/renames.json: the renames that have shipped, read by `hub bot check-names` (clients/bot_names.py).

Sources: the applied waves' column renames in scripts/cleanup/renames.yaml and renames_data.py, the schema from before the
renames (to know which tables keep an old name), the API fields the server renamed and the ones it kept, the Task fields
it stopped sending, and the hub CLI's RENAMED table in clients/hubcli.py. Run it after a wave is applied or a command is
renamed:

    python3 scripts/cleanup/export_names.py

clients/tests/test_bot_names.py fails when the file no longer matches the applied rename data and the CLI table.
"""
import ast
import json
import runpy
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "clients" / "renames.json"
PRE_CLEANUP_SCHEMA = ROOT / "backend" / "tests" / "fixtures" / "schema_pre_cleanup.sql"
RENAMES_DATA = ROOT / "backend" / "migrations" / "renames_data.py"

# Task fields the API no longer sends: a bot reads `holds` instead (backend/task_holds.py public).
API_FIELDS_REMOVED = {
    "snoozed_until": "holds", "snoozed_by": "holds", "snoozed_note": "holds",
    "blocked_since": "holds", "blocked_reason": "holds", "blocked_kind": "holds",
    "blocked_from_step": "holds", "blocked_from_status": "holds", "blocked_gate": "holds",
}
# A command the CLI no longer has at all, and its replacement (the flags differ: `hub automation create --file`).
COMMANDS_REMOVED = [{"old": ["routine"], "new": ["automation"]}]
# New commands that share an old command's first word: `hub notice list` is not the old `hub notice <text>`.
COMMANDS_EXEMPT = [["notice", "list"], ["org", "order"], ["org", "reports-to"]]


def applied_rows(data):
    """(table, old, new, wave) of the applied waves. new is where the name ends up: state_json became state in wave 1 and
    that state became saved_state in wave 3, so a bot on state_json moves to saved_state."""
    applied = {str(w) for w in data["applied_waves"]}
    rows = [row for row in runpy.run_path(str(RENAMES_DATA))["COLUMNS"] if str(row[3]) in applied]
    step = {(table, old): new for table, old, new, _wave in rows}
    out = []
    for table, old, new, wave in rows:
        while (table, new) in step:
            new = step[(table, new)]
        out.append((table, old, new, wave))
    return out


def applied_columns(data, rows):
    """The names renamed on every table that has them, which check-names can match anywhere in a `hub sql` body."""
    waves = {str(k): v for k, v in data["waves"].items()}
    finals = {}
    for _table, old, new, _wave in rows:
        finals.setdefault(old, set()).add(new)
    columns = {}
    for w in data["applied_waves"]:
        for old, new in (waves[str(w)].get("global") or {}).items():
            if new:
                if len(finals[old]) != 1:
                    raise SystemExit(f"{old} is global in wave {w} but ends up as {sorted(finals[old])}")
                columns[old] = next(iter(finals[old]))
    return dict(sorted(columns.items()))


def schema_columns():
    """{table: columns} of the schema before the renames: the old names, on every table that had them."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(PRE_CLEANUP_SCHEMA.read_text())
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    columns = {table: {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')} for table in tables}
    conn.close()
    return columns


def table_columns(rows, schema):
    """{old: {"renames": {new: [tables]}, "kept_in": [tables]}} for every renamed name: `owner` moved on tasks and stayed
    on meetings, so a bot's use of it means something only with its table."""
    out = {}
    for table, old, new, _wave in rows:
        out.setdefault(old, {"renames": {}, "kept_in": []})["renames"].setdefault(new, []).append(table)
    for old, entry in out.items():
        moved = {table for tables in entry["renames"].values() for table in tables}
        entry["kept_in"] = sorted(table for table, columns in schema.items() if old in columns and table not in moved)
        entry["renames"] = {new: sorted(tables) for new, tables in sorted(entry["renames"].items())}
    return dict(sorted(out.items()))


def renamed_commands():
    tree = ast.parse((ROOT / "clients" / "hubcli.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "RENAMED" for t in node.targets):
            rules = []
            for elt in node.value.elts:
                old, new, tail = (ast.literal_eval(e) for e in elt.elts[:3])
                rewrite = elt.elts[3]
                # Only a plain word-for-word rename can be rewritten for a bot; the rest need the bot's own edit.
                fixable = not tail and isinstance(rewrite, ast.Constant) and rewrite.value is None
                rules.append({"old": list(old), "new": list(new), "fixable": fixable})
            return rules
    raise SystemExit("RENAMED is not in clients/hubcli.py")


def build():
    import yaml
    data = yaml.safe_load((HERE / "renames.yaml").read_text())
    rows = applied_rows(data)
    return {
        "source": "scripts/cleanup/renames.yaml",
        "applied_waves": [str(w) for w in data["applied_waves"]],
        "columns": applied_columns(data, rows),
        "table_columns": table_columns(rows, schema_columns()),
        "api_fields": dict(sorted((data.get("api_fields") or {}).items())),
        "api_kept": sorted(data.get("api_kept") or []),
        "api_fields_removed": dict(sorted(API_FIELDS_REMOVED.items())),
        "commands": renamed_commands(),
        "commands_removed": COMMANDS_REMOVED,
        "commands_exempt": COMMANDS_EXEMPT,
    }


def main():
    OUT.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
