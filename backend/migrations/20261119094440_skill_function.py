"""A skill's job function (Library › Skills, docs/skills.md): `skills.function`, what its latest approved SKILL.md names
in its frontmatter as `metadata.function`, written at each approval (backend/skills.py). NULL when it names none.

The skills approved before this are filled from their latest version's SKILL.md here. The frontmatter is read by the
same rule as clients/skills.py `metadata` at the time of writing, copied so this file never changes with it: the
`key: value` lines directly under a top-level `metadata:` line (indented as its first key is; deeper lines belong to a
nested map), inside the leading `---` block. Only SKILL.md's text is read, BATCH skills at a time. Running this again
changes nothing.
"""

import re

BATCH = 200

NESTED = re.compile(r"^\s+([A-Za-z_][\w-]*):\s*(.*)$")


def _function(text):
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    inside, depth, found = False, None, None
    for line in lines[1:]:
        if line.strip() == "---":
            return found
        if not line.strip():
            continue
        if line[:1] in (" ", "\t"):
            indent = len(line) - len(line.lstrip())
            depth = indent if inside and depth is None else depth
            match = NESTED.match(line) if inside and indent == depth else None
            if match and match.group(1) == "function":
                value = match.group(2).strip()
                if value.startswith("[") and value.endswith("]"):
                    value = (value[1:-1].split(",") or [""])[0].strip()
                if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
                    value = value[1:-1]
                found = value.strip()[:80] or None
            continue
        inside, depth = line.rstrip() == "metadata:", None
    return None


def up(conn):
    if "function" not in {row[1] for row in conn.execute("PRAGMA table_info(skills)")}:
        conn.execute("ALTER TABLE skills ADD COLUMN function TEXT")
    after = ""
    while True:
        rows = conn.execute(
            "SELECT s.id, (SELECT json_extract(f.value, '$.content') FROM json_each(v.files) f "
            "WHERE json_extract(f.value, '$.path') = 'SKILL.md' LIMIT 1) AS text "
            "FROM skills s JOIN skill_versions v ON v.id = s.latest_version_id "
            "WHERE s.function IS NULL AND s.id > ? AND json_valid(v.files) ORDER BY s.id LIMIT ?",
            (after, BATCH),
        ).fetchall()
        if not rows:
            return
        for skill_id, text in rows:
            function = _function(text) if isinstance(text, str) else None
            if function:
                conn.execute("UPDATE skills SET function=? WHERE id=?", (function, skill_id))
        after = rows[-1][0]
