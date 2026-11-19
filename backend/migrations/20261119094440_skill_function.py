"""A skill's job function (Library › Skills, docs/skills.md): `skills.function`, what its latest approved SKILL.md names
in its frontmatter as `metadata.function`, written at each approval (backend/skills.py). NULL when it names none.

The skills approved before this are filled from their latest version's SKILL.md here. The frontmatter is read by the
same rule as clients/skills.py `metadata` at the time of writing, copied so this file never changes with it: the indented
`key: value` lines under a top-level `metadata:` line, inside the leading `---` block. Running this again changes nothing.
"""

import json
import re

NESTED = re.compile(r"^\s+([A-Za-z_][\w-]*):\s*(.*)$")


def _function(text):
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    inside, found = False, None
    for line in lines[1:]:
        if line.strip() == "---":
            return found
        if not line.strip():
            continue
        if line[:1] in (" ", "\t"):
            match = NESTED.match(line) if inside else None
            if match and match.group(1) == "function":
                value = match.group(2).strip()
                if value.startswith("[") and value.endswith("]"):
                    value = (value[1:-1].split(",") or [""])[0].strip()
                if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
                    value = value[1:-1]
                found = value.strip()[:80] or None
            continue
        inside = line.rstrip() == "metadata:"
    return None


def up(conn):
    if "function" not in {row[1] for row in conn.execute("PRAGMA table_info(skills)")}:
        conn.execute("ALTER TABLE skills ADD COLUMN function TEXT")
    rows = conn.execute(
        "SELECT s.id, v.files FROM skills s JOIN skill_versions v ON v.id = s.latest_version_id WHERE s.function IS NULL"
    ).fetchall()
    for skill_id, files in rows:
        try:
            bundle = json.loads(files or "[]")
        except ValueError:
            continue
        text = next((f.get("content") for f in bundle if isinstance(f, dict) and f.get("path") == "SKILL.md"), None)
        function = _function(text) if isinstance(text, str) else None
        if function:
            conn.execute("UPDATE skills SET function=? WHERE id=?", (function, skill_id))
