"""Replace the account email used as the default name for saved login plans."""

import re

EMAIL_NAME = re.compile(r'(?:[^\s@]+|"[^"]+")@[^\s@]+(?:\s+[0-9]+)?')


def up(conn):
    labels = {"claude_login": "Claude plan", "codex_login": "ChatGPT plan"}
    taken = {name.strip().lower() for (name,) in conn.execute("SELECT name FROM model_subscriptions")}
    # A reconnect can replace the preview while leaving the old account email in the public name.
    plans = conn.execute(
        "SELECT s.id,s.name,s.kind,h.name FROM model_subscriptions s "
        "LEFT JOIN humans h ON s.owner_id<>'' AND "
        "h.id=CASE WHEN s.owner_id LIKE 'human:%' THEN substr(s.owner_id,7) ELSE s.owner_id END "
        "WHERE s.kind IN ('claude_login','codex_login') AND instr(s.name,'@')>0 "
        "ORDER BY s.created_at,s.id"
    ).fetchall()
    # The API caps names at 80 and plans at 200, but key imports can put more rows in an older database.
    base_limit = 80 - len(f" {max(200, len(taken) + len(plans))}")
    next_number = {}
    for sid, name, kind, display_name in plans:
        if not EMAIL_NAME.fullmatch(name.strip()):
            continue
        display_name = (display_name or "").strip()
        base = labels[kind]
        if display_name and "@" not in display_name:
            ending = f"'s {base}"
            base = display_name[: base_limit - len(ending)].rstrip() + ending
        key = base.lower()
        n = next_number.get(key, 1)
        name = base if n == 1 else f"{base} {n}"
        while name.lower() in taken:
            n += 1
            name = f"{base} {n}"
        next_number[key] = n + 1
        taken.add(name.lower())
        conn.execute("UPDATE model_subscriptions SET name=? WHERE id=?", (name, sid))
