"""Copy existing payers to the agents they serve, leaving legacy sign-ins in place.

Local sign-ins remain attached to their original computer.
"""

import hashlib
import json

from backend import hubdb as H

KINDS = {
    "claude_token": ("claude", "plan"),
    "claude_login": ("claude", "plan"),
    "codex_login": ("codex", "plan"),
    "anthropic_key": ("claude", "key"),
    "openai_key": ("codex", "key"),
    "cursor_key": ("cursor", "key"),
    "meta_key": ("musecode", "key"),
}
KEYS = {
    "CLAUDE_CODE_OAUTH_TOKEN": "claude_token",
    "ANTHROPIC_API_KEY": "anthropic_key",
    "OPENAI_API_KEY": "openai_key",
    "CURSOR_API_KEY": "cursor_key",
    "META_API_KEY": "meta_key",
}
PLAN_RUNTIMES = {runtime for runtime, kind in KINDS.values() if kind == "plan"}


def active_people(conn):
    row = conn.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    try:
        people = (json.loads(row[0]).get("people") or []) if row else []
    except (ValueError, AttributeError):
        people = []
    unavailable = {
        p.get("id") for p in people if isinstance(p, dict) and (p.get("hidden") or p.get("sign_in") is False)
    }
    return {pid for (pid,) in conn.execute("SELECT id FROM humans") if pid not in unavailable and pid != "tico-support"}


def active_owner(value, people):
    pid = (value or "").removeprefix("human:")
    return "human:" + pid if pid in people else ""


def record(conn, action, target, detail, actor="tico"):
    conn.execute(
        "INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,?,?,?,?)",
        (H.new_id(), H.now(), actor, action, target, json.dumps(detail)),
    )


def copy_bindings(conn, people):
    for bot, runtime, sid, person, actor, updated_at, kind, owner, agent_owner in conn.execute(
        "SELECT a.bot,g.runtime,g.subscription_id,g.person,g.set_by,g.updated_at,s.kind,s.owner_id,bc.owner_id "
        "FROM computer_subscriptions g JOIN runners r ON r.id=g.runner_id AND r.revoked_at IS NULL "
        "JOIN assignments a ON a.runner_id=r.id LEFT JOIN model_subscriptions s ON s.id=g.subscription_id "
        "JOIN bot_config bc ON bc.bot=a.bot LEFT JOIN credentials v ON v.id=s.credential_id "
        "WHERE a.bot<>'relationship-worker' AND (v.source IS NULL OR v.source NOT LIKE 'computer:%') AND "
        "(g.person<>'' OR s.credential_id IS NULL OR s.credential_id='model-subscription-'||s.id "
        "OR NOT EXISTS (SELECT 1 FROM model_login_cleared m "
        "WHERE m.runner_id=r.id AND m.runtime=g.runtime AND m.profile=''))"
    ).fetchall():
        if not active_owner(agent_owner, people):
            continue
        if person:
            if runtime not in PLAN_RUNTIMES:
                continue
            owner, pick, sid = active_owner(person, people), "pool", ""
        else:
            tool, pick = KINDS.get(kind, ("", ""))
            if tool != runtime:
                continue
        if pick in ("pool", "plan") and not active_owner(owner, people):
            record(conn, "agent_subscription.owner_left", bot, {"runtime": runtime, "owner": owner or person})
            continue
        conn.execute(
            "INSERT INTO agent_subscriptions(bot,runtime,kind,person,subscription_id,set_by,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (bot, runtime, pick, owner or "", sid, actor, updated_at),
        )


def copy_key_grants(conn, people):
    # The runner applied these env vars in credential id order. For one tool, the last effective grant wins.
    rows = conn.execute(
        "SELECT bc.bot,v.id,v.name,v.env,v.preview,v.created_by,v.created_at,g.granted_by,g.created_at,g.parent_id,"
        "bc.owner_id,p.revoked_at,p.subject,v.source "
        "FROM bot_config bc JOIN credential_grants g ON g.subject='bot:'||bc.bot "
        "JOIN credentials v ON v.id=g.credential_id LEFT JOIN credential_grants p ON p.id=g.parent_id "
        "WHERE bc.bot<>'relationship-worker' AND g.revoked_at IS NULL AND v.kind IN ('api_key','token') "
        "AND v.tool_id IS NULL AND v.ciphertext IS NOT NULL ORDER BY v.id,g.created_at,g.id"
    ).fetchall()
    chosen = {}
    lost = {}
    for (
        bot,
        cid,
        name,
        env,
        preview,
        creator,
        created_at,
        actor,
        updated_at,
        parent,
        agent_owner,
        revoked,
        subject,
        source,
    ) in rows:
        kind = KEYS.get(env or name)
        if not kind or not active_owner(agent_owner, people) or (source or "").startswith("computer:"):
            continue
        if parent is not None and (revoked is not None or subject != "human:" + agent_owner):
            continue
        runtime, pick = KINDS[kind]
        existing = conn.execute(
            "SELECT id,owner_id FROM model_subscriptions WHERE credential_id=? AND kind=? ORDER BY id LIMIT 1",
            (cid, kind),
        ).fetchone()
        sid, owner = existing or (
            hashlib.sha256(f"agent-subscription:{cid}".encode()).hexdigest()[:32],
            active_owner(creator, people),
        )
        if pick == "plan" and not active_owner(owner, people):
            lost[bot, runtime] = owner or creator
            continue
        if not existing:
            conn.execute(
                "INSERT INTO model_subscriptions"
                "(id,name,kind,sealed,preview,added_by,owner_id,created_at,updated_at,credential_at,credential_id) "
                "VALUES(?,?,?,'',?,?,?,?,?,?,?)",
                (sid, name, kind, preview, creator or "tico", owner, created_at, created_at, created_at, cid),
            )
        chosen[bot, runtime] = (pick, owner, sid, actor, updated_at, parent)
    for (bot, runtime), args in chosen.items():
        # A saved login or an explicitly saved subscription was applied after the runner's granted keys.
        other = conn.execute(
            "SELECT a.kind,s.credential_id,s.id FROM agent_subscriptions a "
            "LEFT JOIN model_subscriptions s ON s.id=a.subscription_id WHERE a.bot=? AND a.runtime=?",
            (bot, runtime),
        ).fetchone()
        if other and (other[0] != "key" or not other[1] or other[1] == "model-subscription-" + other[2]):
            continue
        conn.execute(
            "INSERT INTO agent_subscriptions(bot,runtime,kind,person,subscription_id,set_by,updated_at,parent_grant) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(bot,runtime) DO UPDATE SET "
            "kind=excluded.kind,person=excluded.person,subscription_id=excluded.subscription_id,"
            "set_by=excluded.set_by,updated_at=excluded.updated_at,parent_grant=excluded.parent_grant",
            (bot, runtime, *args),
        )
    for (bot, runtime), owner in lost.items():
        if not conn.execute("SELECT 1 FROM agent_subscriptions WHERE bot=? AND runtime=?", (bot, runtime)).fetchone():
            record(conn, "agent_subscription.owner_left", bot, {"runtime": runtime, "owner": owner})


def revoke_key_grants(conn):
    # Every effective grant has been considered for conversion before independent delivery is disabled.
    marks = ",".join("?" for _ in KEYS)
    conn.execute(
        "UPDATE credential_grants SET revoked_at=?,revoked_by='tico' WHERE revoked_at IS NULL "
        "AND subject LIKE 'bot:%' AND subject<>'bot:relationship-worker' AND credential_id IN (SELECT id FROM credentials WHERE tool_id IS NULL "
        f"AND kind IN ('api_key','token') AND coalesce(nullif(env,''),name) IN ({marks}))",
        (H.now(), *KEYS),
    )


def up(conn):
    conn.execute("""
        CREATE TABLE agent_subscriptions (
            bot TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            runtime TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('pool','plan','key')),
            person TEXT NOT NULL DEFAULT '',
            subscription_id TEXT NOT NULL DEFAULT '',
            set_by TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            parent_grant TEXT,
            PRIMARY KEY(bot,runtime),
            CHECK ((kind='pool' AND person<>'' AND subscription_id='') OR
                   (kind IN ('plan','key') AND subscription_id<>''))
        )
    """)
    conn.execute(
        "CREATE INDEX agent_subscriptions_exact ON agent_subscriptions(subscription_id) WHERE subscription_id<>''"
    )
    conn.execute("CREATE INDEX agent_subscriptions_person ON agent_subscriptions(person) WHERE person<>''")
    conn.execute(
        "CREATE INDEX model_subscriptions_pool ON model_subscriptions(owner_id,kind,id) "
        "WHERE kind IN ('claude_token','claude_login','codex_login')"
    )
    people = active_people(conn)
    for sid, creator in conn.execute(
        "SELECT s.id,s.added_by FROM model_subscriptions s LEFT JOIN credentials v ON v.id=s.credential_id "
        "WHERE s.owner_id='' AND (v.source IS NULL OR v.source NOT LIKE 'computer:%')"
    ).fetchall():
        owner = active_owner(creator, people)
        if owner:
            conn.execute("UPDATE model_subscriptions SET owner_id=? WHERE id=?", (owner, sid))
    copy_bindings(conn, people)
    copy_key_grants(conn, people)
    marks = ",".join("?" for _ in KEYS)
    for attempt, bot in conn.execute(
        "SELECT a.id,a.bot_id FROM attempts a WHERE a.state IN ('leased','running') AND a.stop_requested_at IS NULL "
        "AND (EXISTS (SELECT 1 FROM credential_grants g JOIN credentials v ON v.id=g.credential_id "
        "WHERE g.subject='bot:'||a.bot_id AND g.subject<>'bot:relationship-worker' AND g.revoked_at IS NULL "
        "AND v.tool_id IS NULL AND v.kind IN ('api_key','token') "
        f"AND coalesce(nullif(v.env,''),v.name) IN ({marks})) "
        "OR EXISTS (SELECT 1 FROM attempt_subscriptions s WHERE s.attempt_id=a.id) "
        "OR EXISTS (SELECT 1 FROM computer_subscriptions g LEFT JOIN model_subscriptions m ON m.id=g.subscription_id "
        "LEFT JOIN credentials v ON v.id=m.credential_id WHERE g.runner_id=a.runner_id "
        "AND (a.handed_runtime='' OR a.handed_runtime=g.runtime) "
        "AND (g.person<>'' OR v.source IS NULL OR v.source NOT LIKE 'computer:%')))",
        tuple(KEYS),
    ).fetchall():
        conn.execute("UPDATE attempts SET stop_requested_at=? WHERE id=?", (H.TICO_ACTOR, attempt))
        record(conn, "attempt.stop", attempt, {"bot": bot, "restart": "The agent now chooses what pays"}, H.TICO_ACTOR)
    revoke_key_grants(conn)
