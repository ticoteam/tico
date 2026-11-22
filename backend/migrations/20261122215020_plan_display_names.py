"""Replace the account email used as the default name for saved login plans."""

import re


def up(conn):
    labels = {"claude_login": "Claude plan", "codex_login": "ChatGPT plan"}
    taken = {name.strip().lower() for (name,) in conn.execute("SELECT name FROM model_subscriptions")}
    plans = conn.execute(
        "SELECT s.id,s.name,s.kind,s.preview,h.name FROM model_subscriptions s "
        "LEFT JOIN humans h ON h.id=CASE WHEN s.owner_id LIKE 'human:%' THEN substr(s.owner_id,7) ELSE s.owner_id END "
        "WHERE s.owner_id<>'' AND s.kind IN ('claude_login','codex_login') AND instr(s.preview,'@')>0 "
        "ORDER BY s.created_at,s.id"
    ).fetchall()
    for sid, name, kind, preview, display_name in plans:
        if not re.fullmatch(re.escape(preview) + r"(?: [0-9]+)?", name):
            continue
        display_name = (display_name or "").strip()
        base = labels[kind]
        if display_name and "@" not in display_name:
            base = f"{display_name}'s {base}"
        n, name = 1, base
        while name.lower() in taken:
            n += 1
            name = f"{base} {n}"
        taken.add(name.lower())
        conn.execute("UPDATE model_subscriptions SET name=? WHERE id=?", (name, sid))
