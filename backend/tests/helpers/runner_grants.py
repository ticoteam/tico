"""A fictional company's tools, keys, settings and grants for GET /api/v2/runner-credential-grants (backend/credentials.py
`runner_grants`), shared by its test and bench/perf-runner-grants.py, and the answer as the route gave it before it read
in one pass: each bot's `granted` and `tool_settings.names` on their own, outside a snapshot."""

import json

from backend import tool_settings
from backend.credentials import granted
from backend.store import H
from backend.tests.helpers import engine as X

PEOPLE = ("ana", "ben", "cara")


def seed(c, bots, rng, tools=60, runners=8):
    """`bots` (slugs) spread over `runners` computers; `tools` tools of every kind with a few hundred keys, settings
    and grants: everyone-reads and switched-off tools, Only listed (audience) tools, website tools, tools on the proxy
    and tools whose address the proxy cannot call, multi-secret templates, one variable several tools deliver, held
    and login keys, keys with no value, grants revoked, grants passed on from a person (their parent live or revoked,
    the person the bot's owner or not), grants to people and to the computers, archived bots."""
    now = H.now()
    for n, slug in enumerate(bots):
        X.bot(c, slug, computer=False)
        runner = f"r{n % runners}"
        c.execute(
            "INSERT OR IGNORE INTO runners(id,label,owner_id,token_hash,created_at) VALUES(?,?,'ana',?,?)",
            (runner, "box" + runner, "h" + runner, now),
        )
        c.execute("INSERT OR REPLACE INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, runner, now))
        c.execute("UPDATE bot_config SET owner_id=? WHERE bot=?", (PEOPLE[n % 3], slug))
        if n % 23 == 22:
            c.execute("UPDATE bots SET state='archived' WHERE id=?", (slug,))
    keys = []
    for t in range(tools):
        tid = f"tool{t:02d}"
        kind = "website" if t % 17 == 16 else "api"
        audience = json.dumps(["human:ana"]) if t % 11 == 10 else None
        delivery = "proxy" if t % 3 == 0 else "computer"
        base_url = "" if t % 5 == 4 else f"https://api{t}.example.com"
        template = json.dumps({"secrets": ["A", "B"]}) if t % 13 == 12 else ""
        c.execute(
            "INSERT INTO tools(id,name,service,base_url,delivery,created_by,created_at,updated_at,audience,kind,"
            "everyone_reads,auth_template) VALUES(?,?,?,?,?,'human:ana',?,?,?,?,?,?)",
            (
                tid,
                f"Tool {t}",
                f"svc{t}",
                base_url,
                delivery,
                now,
                now,
                audience,
                kind,
                0 if t % 7 == 6 else 1,
                template,
            ),
        )
        for k in range(rng.randint(1, 8)):
            cid = f"key{t:02d}{k}"
            # Several tools deliver SHARED_TOKEN; template tools a variable per secret; some keys have no variable.
            env = (
                "SHARED_TOKEN"
                if t % 9 == 1 and k == 0
                else f"TOOL{t}_{'AB'[k % 2]}"
                if template
                else ""
                if k % 6 == 5
                else f"TOOL{t}_KEY{k}"
            )
            ckind = "login" if k % 7 == 6 else rng.choice(("token", "api_key"))
            c.execute(
                "INSERT INTO credentials(id,name,kind,env,ciphertext,nonce,tool_id,access_level,holder,created_at,"
                "updated_at,updated_by,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,'ana','ana')",
                (
                    cid,
                    f"Key {t}.{k}",
                    ckind,
                    env,
                    None if k % 9 == 8 else b"sealed",
                    b"n",
                    tid,
                    rng.choice(("read", "read_write", "read_write")),
                    "bot:" + bots[t % len(bots)] if k % 10 == 9 else None,
                    H.shift(now, minutes=k),
                    now,
                ),
            )
            keys.append(cid)
        for s in range(rng.randint(0, 3)):
            # Names repeat across tools, and one is a key's variable.
            name = rng.choice((f"TOOL{t}_HOST", "AWS_REGION", "POSTHOG_HOST", f"TOOL{t}_KEY0", f"TOOL{t}_PROJECT_ID"))
            if not c.execute("SELECT 1 FROM tool_settings WHERE tool_id=? AND name=?", (tid, name)).fetchone():
                c.execute(
                    "INSERT INTO tool_settings(id,tool_id,name,value,created_by,created_at,updated_by,updated_at) "
                    "VALUES(?,?,?,?,'human:ana',?,'human:ana',?)",
                    (f"set{t:02d}{s}", tid, name, f"v{s}", now, now),
                )
    for k in range(40):  # keys of no tool
        c.execute(
            "INSERT INTO credentials(id,name,kind,env,ciphertext,nonce,created_at,updated_at,updated_by,created_by) "
            "VALUES(?,?,'token',?,?,'n',?,?,'ana','ana')",
            (f"loose{k:02d}", f"Loose {k}", f"LOOSE_{k}" if k % 4 else "", b"sealed", now, now),
        )
        keys.append(f"loose{k:02d}")
    grants = []
    subjects = ["human:" + p for p in PEOPLE] + ["computers"]
    for n in range(len(bots) * 4):
        bot = bots[n % len(bots)]
        cid = rng.choice(keys)
        parent = None
        if n % 5 == 0:  # passed on from a person: the bot's owner or another, their grant live or revoked
            parent = f"pg{n}"
            person = PEOPLE[bots.index(bot) % 3] if n % 3 else rng.choice(PEOPLE)
            c.execute(
                "INSERT OR IGNORE INTO credential_grants(id,credential_id,subject,granted_by,created_at,revoked_at) "
                "VALUES(?,?,?,'human:ana',?,?)",
                (parent, cid, "human:" + person, now, now if n % 4 == 0 else None),
            )
            parent = c.execute(  # the person's grant already there, when they held one
                "SELECT id FROM credential_grants WHERE credential_id=? AND subject=?", (cid, "human:" + person)
            ).fetchone()[0]
        grants.append((f"g{n}", cid, "bot:" + bot, parent, now if n % 8 == 7 else None))
    grants += [(f"o{n}", rng.choice(keys), rng.choice(subjects), None, None) for n in range(60)]
    c.executemany(
        "INSERT OR IGNORE INTO credential_grants(id,credential_id,subject,granted_by,parent_id,created_at,revoked_at) "
        "VALUES(?,?,?,'human:ana',?,?,?)",
        [(g, cid, s, p, now, r) for g, cid, s, p, r in grants],
    )


def reference(store, runner_id):
    """The answer as the route gave it before: each bot read on its own, outside a snapshot."""
    with store.read() as c:
        bots = {r[0]: [] for r in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (runner_id,))}
        stored = c.execute(
            "SELECT id,env FROM credentials WHERE ciphertext IS NOT NULL AND env!='' AND kind!='login'"
        ).fetchall()
        for bot, names in bots.items():
            held = granted(c, "bot:" + bot)
            names.extend(r["env"] for r in stored if r["id"] in held)
            names.extend(n for n in tool_settings.names(c, held) if n not in names)
        return {"bots": bots}
