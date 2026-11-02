"""Write clients/renames.json: the renames that have shipped, read by `hub bot check-names` (clients/bot_names.py).

Sources: the applied waves' global column names in scripts/cleanup/renames.yaml, the API fields the server renamed,
the Task fields it stopped sending, and the hub CLI's RENAMED table in clients/hubcli.py. Run it after a wave is applied
or a command is renamed:

    python3 scripts/cleanup/export_names.py

clients/tests/test_bot_names.py fails when the file no longer matches the applied rename data and the CLI table.
"""
import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "clients" / "renames.json"

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


def applied_columns(data):
    waves = {str(k): v for k, v in data["waves"].items()}
    columns = {}
    for w in data["applied_waves"]:
        for old, new in (waves[str(w)].get("global") or {}).items():
            if new:
                columns[old] = new
    return dict(sorted(columns.items()))


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
    return {
        "source": "scripts/cleanup/renames.yaml",
        "applied_waves": [int(w) for w in data["applied_waves"]],
        "columns": applied_columns(data),
        "api_fields": dict(sorted((data.get("api_fields") or {}).items())),
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
