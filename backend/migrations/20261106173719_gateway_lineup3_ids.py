"""Gateway lineup 3: every stored Tico model id becomes this lineup's id, once.

A department tier's id loses its department (`tico-sales-medium-fast-1.0` -> `tico-medium-fast-1.0`), the first
lineup's ids become the level they ran at (`tico-mid-1.0` -> `tico-medium-1.0`), `tico-decide-1.0` is pinned Decide Low
1.0 and the retired Classify ids are Decide Low. The meeting tier and every other value are kept as they are, so running
it again changes nothing.

What is rewritten is configuration: a bot's model, fallback and other settings (`bot_settings`), the company's model
choice and any other saved setting (`registry_metadata`), a task's run and check models, automations' flows, routines'
definitions and task rules' actions. Records of what already ran (attempts, usage, feedback) keep the id they ran on;
backend/gateway_tiers.py `new_id` maps those on read. The table here is a frozen copy of that mapping: a migration never
calls app code, and the app's copy goes once every install runs this lineup.
"""

import json
import re

DEPARTMENTS = (
    "general",
    "product",
    "engineering",
    "marketing",
    "sales",
    "finance",
    "legal",
    "science",
    "operations",
    "data",
)
TIER = re.compile(r"tico-(?:" + "|".join(DEPARTMENTS) + r")-(low|medium|high)(-fast)?(-\d{1,3}\.\d{1,3})?")
ALIASES = {
    "tico-cheap-1.1": "tico-low-1.0",
    "tico-cheap-1.0": "tico-low-1.0",
    "tico-mid-1.0": "tico-medium-1.0",
    "tico-power-1.0": "tico-high-1.0",
    "tico-decide-1.0": "tico-decide-low-1.0",
    "tico-classify-1.0": "tico-decide-low",
    "tico-classify-low": "tico-decide-low",
    "tico-classify-low-1.0": "tico-decide-low",
}
# (table, column, json): where a model id is kept as configuration. A table or column a file does not have is skipped.
PLACES = (
    ("bot_settings", "model", False),
    ("bot_settings", "fallback", True),
    ("bot_settings", "extra", True),
    ("registry_metadata", "value", True),
    ("task_run_models", "model", False),
    ("task_check_models", "model", False),
    ("automations", "flow_json", True),
    ("schedules", "definition_json", True),
    ("task_rules", "actions_json", True),
)


def new_id(value):
    """`value` as this lineup's id; any other text as it is. `runtime:model` (a check model) maps its model part."""
    key = value.strip().lower()
    if key in ALIASES:
        return ALIASES[key]
    found = TIER.fullmatch(key)
    if found:
        return "tico-" + found.group(1) + (found.group(2) or "") + (found.group(3) or "")
    runtime, colon, model = value.partition(":")
    if colon and model.strip().lower().startswith("tico-"):
        mapped = new_id(model)
        return runtime + ":" + mapped if mapped != model else value
    return value


def walk(value):
    """`value` (parsed JSON) with every string that is an old id replaced; the same object when nothing changed."""
    if isinstance(value, str):
        return new_id(value)
    if isinstance(value, list):
        out = [walk(item) for item in value]
        return value if all(a is b for a, b in zip(out, value, strict=True)) else out
    if isinstance(value, dict):
        out = {key: walk(item) for key, item in value.items()}
        return value if all(out[key] is value[key] for key in value) else out
    return value


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, column, is_json in PLACES:
        if table not in tables or column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            continue
        rows = conn.execute(f"SELECT rowid, {column} FROM {table} WHERE {column} LIKE '%tico-%'").fetchall()
        for rowid, text in rows:
            if not isinstance(text, str):
                continue
            if not is_json:
                fixed = new_id(text)
            else:
                try:
                    parsed = json.loads(text)
                except ValueError:
                    fixed = new_id(text)  # a plain text setting
                else:
                    mapped = walk(parsed)
                    fixed = text if mapped is parsed else json.dumps(mapped)
            if fixed != text:
                conn.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (fixed, rowid))
