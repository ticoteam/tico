"""Computer keys become subscriptions: one store for the model key (backend/model_subscriptions.py).

A model key kept for computers in Settings > Computers (`computer_keys`, and vault grants to `computers` or
`computer:<id>`) becomes a `model_subscriptions` row that points at its existing vault credential (`credential_id`,
added here; `sealed` stays ''), and the computers it signs in become `computer_subscriptions` rows. No value is
decrypted: the vault credential keeps it.

1. A computer's own key in use (`computer_keys`, state active) becomes a subscription named "<kind> · <computer>",
   given to that computer. A computer already given a subscription for that runtime keeps it: the key is moved in
   but given to no computer.
2. A shared key (a credential granted to every computer, or to computers by name) becomes one subscription under its
   credential's name, given only to the computers that run on it now, by the rule the Computers page used
   (`computer_keys.uses_shared` before this): the key shared with it by name before the "Every computer" one, none on a
   computer with its own key, a cleared key or a switch to a subscription. And only where the computer reported
   signing in with the shared key: one on a terminal key or a login of its own, or one that has not said (no report,
   a failed or timed-out check), is not given it, so nobody is billed differently. Its computer grants are revoked: the
   subscription is the record now.
3. A Codex key now signs turns in from a home of its own, so the sessions of Codex on each computer given one start
   fresh, as a change of subscription does (model_subscriptions._reset).

It also indexes a subscription's key (`model_subscriptions_credential`) and a grant's subject (`credential_grants_subject`),
which the claim now reads for each bot's own key grants.

Staged keys (a check still running) stay in `computer_keys`; the server moves each when its check ends. Running this
again changes nothing: a credential already pointed at is skipped, and the moved rows and grants are gone.
"""

import json
import uuid
from datetime import datetime, timezone

# Variable -> (kind, runtime, name), as backend/model_subscriptions.py KINDS has them now.
KINDS = {
    "CLAUDE_CODE_OAUTH_TOKEN": ("claude_token", "claude", "Claude"),
    "ANTHROPIC_API_KEY": ("anthropic_key", "claude", "Anthropic API"),
    "OPENAI_API_KEY": ("openai_key", "codex", "OpenAI API"),
    "CURSOR_API_KEY": ("cursor_key", "cursor", "Cursor API"),
    "META_API_KEY": ("meta_key", "musecode", "Meta API"),
}
# Runtime -> the variables a key for it arrives in, in the order a computer took them (providers.MODEL_KEYS).
NAMES = {
    "claude": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    "codex": ("OPENAI_API_KEY",),
    "cursor": ("CURSOR_API_KEY",),
    "musecode": ("META_API_KEY",),
}
FILE_RUNTIMES = ("codex",)  # a subscription signs these in from a home of its own
SHARED_SUBJECTS = "(g.subject='computers' OR g.subject LIKE 'computer:%')"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _subscription(conn, credential_id, env, name, preview, added_by, created_at, now):
    """The subscription for `credential_id`, made once."""
    found = conn.execute("SELECT id FROM model_subscriptions WHERE credential_id=?", (credential_id,)).fetchone()
    if found:
        return found[0]
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO model_subscriptions(id,name,kind,sealed,preview,added_by,owner_id,created_at,updated_at,"
        "credential_at,credential_id) VALUES(?,?,?,'',?,?,'',?,?,?,?)",
        (sid, name, KINDS[env][0], preview or "", added_by or "tico", created_at or now, now, now, credential_id),
    )
    return sid


def _give(conn, runner_id, runtime, sid, actor, now):
    """Give the computer `sid` for `runtime` unless it is given something for it already. Whether it was given."""
    if conn.execute(
        "SELECT 1 FROM computer_subscriptions WHERE runner_id=? AND runtime=?", (runner_id, runtime)
    ).fetchone():
        return False
    conn.execute(
        "INSERT INTO computer_subscriptions(runner_id,runtime,subscription_id,set_by,updated_at,person) "
        "VALUES(?,?,?,?,?,'')",
        (runner_id, runtime, sid, actor or "tico", now),
    )
    return True


def _fence(conn, runner_id, runtime, now):
    """The sessions of `runtime` of each bot on this computer start fresh (execution.reset_bot_sessions)."""
    for (bot,) in conn.execute("SELECT bot FROM assignments WHERE runner_id=?", (runner_id,)).fetchall():
        conn.execute(
            "INSERT INTO bot_session_epochs VALUES(?,1,?) ON CONFLICT(bot) DO UPDATE SET "
            "epoch=epoch+1,updated_at=excluded.updated_at",
            (bot, now),
        )
        for cid, participants in conn.execute(
            "SELECT id,participants FROM conversations WHERE participants LIKE ?", (f'%"bot:{bot}"%',)
        ).fetchall():
            try:
                found = "bot:" + bot in json.loads(participants or "[]")
            except ValueError:
                found = False
            if found:
                conn.execute(
                    "INSERT INTO session_epochs VALUES(?,1,?) ON CONFLICT(conversation_id) DO UPDATE SET "
                    "epoch=epoch+1,updated_at=excluded.updated_at",
                    (cid, now),
                )
        conn.execute("DELETE FROM bot_sessions WHERE bot=? AND runtime=?", (bot, runtime))


def _reported(readiness, runtime):
    """The key the computer last reported signing `runtime` in with ({name, source}), or {}."""
    try:
        row = (json.loads(readiness or "{}").get("runtimes") or {}).get(runtime)
    except (ValueError, AttributeError):
        return {}
    key = row.get("key") if isinstance(row, dict) else None
    return key if isinstance(key, dict) else {}


def _marked(conn, runner_id, runtime):
    return any(
        conn.execute(
            f"SELECT 1 FROM {table} WHERE runner_id=? AND runtime=? AND profile=''", (runner_id, runtime)
        ).fetchone()
        for table in ("model_login_cleared", "model_login_key_off")
    )


def _shared_on(conn, shared, runner_id, readiness, runtime, own):
    """The shared credential this computer runs `runtime` on, or None (the old `computer_keys.uses_shared`)."""
    allowed = NAMES[runtime]
    found = {}
    for subjects in (("computer:" + runner_id,), ("computers",)):
        found = {}
        for cid, env, subject in shared:
            if env in allowed and env not in found and subject in subjects:
                found[env] = cid
        if found:
            break
    if not found or (runner_id, runtime) in own or _marked(conn, runner_id, runtime):
        return None
    # Only where it reported signing in with the shared key: a computer that has not said (no report yet, a failed or
    # timed-out check) may be on a login of its own, and giving it the key would change who is billed.
    key = _reported(readiness, runtime)
    if key.get("source") != "shared":
        return None
    return found.get(key.get("name")) or next(found[env] for env in allowed if env in found)


def up(conn, now=None):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "model_subscriptions" not in tables:
        return
    if "credential_id" not in [r[1] for r in conn.execute("PRAGMA table_info(model_subscriptions)")]:
        conn.execute("ALTER TABLE model_subscriptions ADD COLUMN credential_id TEXT REFERENCES credentials(id)")
    # Lookups by key (a key's subscription, Replace key for all) and by grantee (a bot's own key grants, read on each
    # claim: model_subscriptions.for_claim) seek rather than scan (backend/tests/test_model_key_plan.py).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS model_subscriptions_credential ON model_subscriptions(credential_id) "
        "WHERE credential_id IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS credential_grants_subject ON credential_grants(subject) WHERE revoked_at IS NULL"
    )
    if "computer_keys" not in tables:
        return
    now = now or _now()
    live = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT id,label,readiness FROM runners_with_state WHERE revoked_at IS NULL")
    }
    own_rows = conn.execute(
        "SELECT k.credential_id,k.runner_id,k.runtime,k.created_at,k.created_by,cr.env,cr.preview,r.label "
        "FROM computer_keys k JOIN credentials cr ON cr.id=k.credential_id JOIN runners r ON r.id=k.runner_id "
        "WHERE k.state='active' AND cr.ciphertext IS NOT NULL ORDER BY k.created_at DESC, k.credential_id"
    ).fetchall()
    own = {(r[1], r[2]) for r in own_rows}
    shared = conn.execute(
        "SELECT g.credential_id,cr.env,g.subject FROM credential_grants g JOIN credentials cr ON cr.id=g.credential_id "
        f"WHERE g.revoked_at IS NULL AND {SHARED_SUBJECTS} AND cr.ciphertext IS NOT NULL ORDER BY cr.id"
    ).fetchall()
    shared = [(r[0], r[1], r[2]) for r in shared if r[1] in KINDS]
    # Decided on the rows as they were, before anything moves.
    runs_on = {}
    for runner_id, (_, readiness) in live.items():
        for runtime in NAMES:
            cid = _shared_on(conn, shared, runner_id, readiness, runtime, own)
            if cid:
                runs_on[(runner_id, runtime)] = cid
    given = []
    # 1. Each computer's own key, the newest first when there were two.
    for cid, runner_id, runtime, created_at, created_by, env, preview, label in own_rows:
        kind = KINDS.get(env)
        if not kind or kind[1] != runtime:
            continue  # a variable that signs nothing in on that runtime: left as it was
        sid = _subscription(conn, cid, env, f"{kind[2]} · {label}", preview, created_by, created_at, now)
        if runner_id in live and _give(conn, runner_id, runtime, sid, created_by, now):
            given.append((runner_id, runtime))
        conn.execute("DELETE FROM computer_keys WHERE credential_id=?", (cid,))
    # 2. Each shared key, on the computers that run on it.
    for cid in dict.fromkeys(r[0] for r in shared):
        name, env, preview, created_by, created_at = conn.execute(
            "SELECT name,env,preview,created_by,created_at FROM credentials WHERE id=?", (cid,)
        ).fetchone()
        sid = _subscription(conn, cid, env, name, preview, created_by, created_at, now)
        for (runner_id, runtime), on in sorted(runs_on.items()):
            if on == cid and KINDS[env][1] == runtime and _give(conn, runner_id, runtime, sid, created_by, now):
                given.append((runner_id, runtime))
        conn.execute(
            "UPDATE credential_grants SET revoked_at=?, revoked_by='tico' WHERE credential_id=? AND revoked_at IS NULL "
            "AND (subject='computers' OR subject LIKE 'computer:%')",
            (now, cid),
        )
    # 3. A home of its own for Codex: its sessions there start fresh.
    for runner_id, runtime in given:
        if runtime in FILE_RUNTIMES:
            _fence(conn, runner_id, runtime, now)
