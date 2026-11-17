"""A model API key for computers: Add key, Replace key, Clear key and shared keys in Settings > Computers.

One store holds every model key: a key is a subscription (backend/model_subscriptions.py) whose value stays in the
credential vault (`model_subscriptions.credential_id`, sealed like any credential and shown only by its preview), and a
computer signs a runtime in with it when it is given it (`computer_subscriptions`, one per computer and runtime).
A computer's own key is the one stored from that computer (its credential's source is `computer:<runner id>`); any
other key given to computers is shared, and `serving` lists which computers each one serves.

The login queue never carries a key. A new key is staged first (`computer_keys`, state `staged`): a `use_key` job, or a
`to_key` one (Switch to API key) (backend/model_login.py), has no value, and the runner fetches the staged key from
`GET runner-model-credentials` with that job's id, checks it, and reports. `confirm` then makes it the computer's key
(the old own key's value is replaced in place); `discard` deletes a key that failed, so the old one stays
(runner-rs/src/signin.rs).

`keys_for` answers `GET runner-model-credentials`, which runners of v0.6.0-rc.1 on still poll and write to their key
files: the staged key for a live job, else nothing while a person cleared the key, signed the login out or switched it
to a subscription (`model_login_cleared`, `model_login_key_off`), else the key the computer is given, as `computer`
(its own) or `shared`. A subscription saved in Settings > Models is never sent there: it signs turns in only, as before.
"""

from . import providers
from .store import H, Problem

OWN = "computer:"  # an own key's credential source, with the runner id

RUNTIMES = tuple(providers.MODEL_KEYS)
STAGED = "staged"

# The key a computer is given for a runtime: its subscription and its vault credential (`id` is the credential's).
KEY_SQL = (
    "SELECT cr.*, s.id AS subscription_id, s.revision AS subscription_revision, g.updated_at AS given_at "
    "FROM computer_subscriptions g JOIN model_subscriptions s ON s.id=g.subscription_id "
    "JOIN credentials cr ON cr.id=s.credential_id WHERE g.person='' AND cr.ciphertext IS NOT NULL"
)


def names(runtime):
    """The variables a key for `runtime` arrives in, the first being what a new key is stored as."""
    found = providers.MODEL_KEYS.get(runtime)
    if not found:
        raise Problem("kind", "A key signs in only " + ", ".join(RUNTIMES) + " on a computer", 422)
    return found


def drop_credential(c, credential_id):
    """Delete a key's vault value once no subscription points at it and nobody else holds a grant to it."""
    if c.execute("SELECT 1 FROM model_subscriptions WHERE credential_id=?", (credential_id,)).fetchone():
        return
    if c.execute(
        "SELECT 1 FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL "
        "AND subject<>'computers' AND subject NOT LIKE 'computer:%'",
        (credential_id,),
    ).fetchone():
        return
    c.execute("DELETE FROM computer_keys WHERE credential_id=?", (credential_id,))
    c.execute("DELETE FROM credential_grants WHERE credential_id=?", (credential_id,))
    c.execute("DELETE FROM credentials WHERE id=?", (credential_id,))


def _drop(c, sid):
    """Delete a key's subscription, the computers given it and its vault value."""
    found = c.execute("SELECT credential_id FROM model_subscriptions WHERE id=?", (sid,)).fetchone()
    c.execute("DELETE FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sid,))
    c.execute("DELETE FROM model_subscriptions WHERE id=?", (sid,))
    if found and found["credential_id"]:
        drop_credential(c, found["credential_id"])


def _given(c, runner_id, runtime):
    """The key this computer is given for `runtime` (its credential row, with `subscription_id`), or None."""
    return c.execute(KEY_SQL + " AND g.runner_id=? AND g.runtime=?", (runner_id, runtime)).fetchone()


def _models_plan(c, runner_id, runtime):
    """The name of a subscription from Settings > Models (not a key) given to this computer for `runtime`, or None."""
    found = c.execute(
        "SELECT s.name FROM computer_subscriptions g JOIN model_subscriptions s ON s.id=g.subscription_id "
        "WHERE g.runner_id=? AND g.runtime=? AND (g.person<>'' OR s.credential_id IS NULL)",
        (runner_id, runtime),
    ).fetchone()
    if found:
        return found["name"]
    person = c.execute(
        "SELECT 1 FROM computer_subscriptions WHERE runner_id=? AND runtime=? AND person<>''", (runner_id, runtime)
    ).fetchone()
    return "a person's plans" if person else None


def _refuse_models_plan(c, runner_id, runtime):
    name = _models_plan(c, runner_id, runtime)
    if name:
        raise Problem("subscription", f"This computer signs {runtime} in with {name} from Settings > Models", 409)


def subscription_for(c, credential, actor, name=None):
    """The subscription that keeps `credential` (a vault row) as a key, made the first time."""
    from . import model_subscriptions as MS

    found = c.execute("SELECT id FROM model_subscriptions WHERE credential_id=?", (credential["id"],)).fetchone()
    if found:
        return found["id"]
    kind = MS.KEY_KINDS.get(credential["env"])
    if not kind:
        raise Problem("env", f"{credential['env'] or 'This key'} signs no model in on a computer", 422)
    sid, now = H.new_id(), H.now()
    c.execute(
        "INSERT INTO model_subscriptions(id,name,kind,sealed,preview,added_by,owner_id,created_at,updated_at,"
        "credential_at,credential_id) VALUES(?,?,?,'',?,?,'',?,?,?,?)",
        (sid, name or credential["name"], kind, credential["preview"], actor, now, now, now, credential["id"]),
    )
    H.event(c, actor, "subscription.created", sid, {"kind": kind, "credential": credential["id"]})
    return sid


def assign(c, runner_id, runtime, sid, actor, settings=None):
    """Give the computer the key `sid` for `runtime`. A Codex key signs in from a home of its own, so a change starts
    that computer's Codex sessions fresh (model_subscriptions._reset)."""
    from . import model_subscriptions as MS

    current = c.execute(
        "SELECT subscription_id FROM computer_subscriptions WHERE runner_id=? AND runtime=? AND person=''",
        (runner_id, runtime),
    ).fetchone()
    if current and current["subscription_id"] == sid:
        return
    MS._set(c, runner_id, runtime, sid, actor)
    kind = str((MS.row(c, sid) or {}).get("kind") or "")
    if MS.KINDS.get(kind, {}).get("file"):
        MS._reset(c, settings, runner_id, runtime)


def store(c, vault, who, runner, runtime, value, login_id, env=""):
    """Seal `value` in the vault as a key staged for this computer's `runtime`; it is used once `confirm` says the
    computer checked it. Returns the stored key's metadata, never its value."""
    allowed = names(runtime)
    env = env or allowed[0]
    if env not in allowed:
        raise Problem("env", f"A {runtime} key is stored as " + " or ".join(allowed), 422)
    value = str(value or "").strip()
    if not value or len(value) > 4000 or any(ch in value for ch in "\r\n\0"):
        raise Problem("credential", "Paste the key as one line", 422)
    if not vault.configured:
        raise Problem("vault_unavailable", "Credentials are not set up on this server, so a key cannot be stored", 503)
    _refuse_models_plan(c, runner["id"], runtime)
    cid, now = H.new_id(), H.now()
    ciphertext, nonce = vault.cipher.encrypt(c, cid, value)
    preview = value[:3] + "…" + value[-3:] if len(value) >= 12 else "••••••"
    c.execute(
        "INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,updated_by,"
        "created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            cid,
            f"{env} for {runner['label']}",
            "api_key",
            env,
            preview,
            ciphertext,
            nonce,
            OWN + runner["id"],
            now,
            now,
            who.actor,
            who.actor,
        ),
    )
    c.execute(
        "INSERT INTO computer_keys(credential_id,runner_id,runtime,state,login_id,created_at,created_by) "
        "VALUES(?,?,?,?,?,?,?)",
        (cid, runner["id"], runtime, STAGED, login_id, now, who.actor),
    )
    H.event(c, who.actor, "computer_key.stored", runner["id"], {"runtime": runtime, "env": env, "credential": cid})
    return {"id": cid, "env": env, "preview": preview}


def confirm(c, runner_id, login_id, settings=None):
    """The computer checked the key staged for `login_id`: it is now this computer's key. Its own key before takes the
    new value in place (the old value is deleted), unless that key is given to other computers too; else the key is a
    new subscription given to it. A computer given a subscription from Settings > Models meanwhile keeps it, and the
    key is kept but given to no computer."""
    from . import model_subscriptions as MS

    row = c.execute(
        "SELECT k.*, cr.env, cr.preview, cr.name FROM computer_keys k JOIN credentials cr ON cr.id=k.credential_id "
        "WHERE k.login_id=? AND k.runner_id=? AND k.state=?",
        (login_id, runner_id, STAGED),
    ).fetchone()
    if not row:
        return None
    runtime, cid, now = row["runtime"], row["credential_id"], H.now()
    c.execute("DELETE FROM computer_keys WHERE credential_id=?", (cid,))
    current = _given(c, runner_id, runtime)
    # Its own key before, unless it is given in Settings > Models to other computers too: that stays theirs as it is,
    # and the new value is this computer's alone, a subscription of its own.
    old = None
    if (
        current is not None
        and current["source"] == OWN + runner_id
        and not c.execute(
            "SELECT 1 FROM computer_subscriptions WHERE subscription_id=? AND NOT (runner_id=? AND runtime=? AND person='')",
            (current["subscription_id"], runner_id, runtime),
        ).fetchone()
    ):
        old = current
    if old is not None and MS.KEY_KINDS.get(row["env"]) == (MS.row(c, old["subscription_id"]) or {}).get("kind"):
        c.execute(
            "UPDATE model_subscriptions SET credential_id=?,preview=?,revision=revision+1,status='new',"
            "status_detail='',status_at=?,updated_at=?,credential_at=? WHERE id=?",
            (cid, row["preview"], now, now, now, old["subscription_id"]),
        )
        drop_credential(c, old["id"])
    else:
        label = (c.execute("SELECT label FROM runners WHERE id=?", (runner_id,)).fetchone() or {"label": ""})["label"]
        kind = MS.KEY_KINDS[row["env"]]
        credential = {"id": cid, "env": row["env"], "preview": row["preview"], "name": ""}
        sid = subscription_for(c, credential, row["created_by"], f"{MS.KINDS[kind]['name']} · {label}")
        if not _models_plan(c, runner_id, runtime):
            assign(c, runner_id, runtime, sid, row["created_by"], settings)
            if old is not None:
                _drop(c, old["subscription_id"])
    # In use again: a key added after a clear, or after a switch to a subscription (Switch to API key included), signs
    # it in.
    for table in ("model_login_cleared", "model_login_key_off"):
        c.execute(f"DELETE FROM {table} WHERE runner_id=? AND runtime=? AND profile=''", (runner_id, runtime))
    H.event(c, "runner:" + runner_id, "computer_key.active", runner_id, {"runtime": runtime, "credential": cid})
    return cid


def discard(c, login_id):
    """A staged key the computer could not use, or whose check was canceled or ran out: deleted, the old key kept."""
    for row in c.execute(
        "SELECT credential_id FROM computer_keys WHERE login_id=? AND state=?", (login_id, STAGED)
    ).fetchall():
        drop_credential(c, row["credential_id"])


def delete(c, runner_id, runtime):
    """Clear key (or a switch to a subscription): the computer's key for `runtime` is taken off it. Its own key is
    deleted from the vault (staged ones too), or only taken off it while other computers are given it; a shared key
    stays given to it, held back by the mark the caller sets, so Use the shared key brings back that key and no other."""
    n = 0
    for row in c.execute(
        "SELECT credential_id FROM computer_keys WHERE runner_id=? AND runtime=?", (runner_id, runtime)
    ).fetchall():
        drop_credential(c, row["credential_id"])
        n += 1
    current = _given(c, runner_id, runtime)
    if current:
        n += 1
        if current["source"] != OWN + runner_id:
            return n
        if c.execute(
            "SELECT 1 FROM computer_subscriptions WHERE subscription_id=? AND runner_id<>?",
            (current["subscription_id"], runner_id),
        ).fetchone():
            c.execute(
                "DELETE FROM computer_subscriptions WHERE runner_id=? AND runtime=? AND person=''", (runner_id, runtime)
            )
        else:
            _drop(c, current["subscription_id"])
    return n


def before_delete(c, who, credential_id):
    """Credentials > Delete on a computer's key. One a computer is still checking is refused, so a check never confirms
    a key that is gone. A key in use goes like any credential: its subscription goes with it, and each computer given
    it is cleared as Clear key does (told to delete its copies and log out the login the key made: a v0.6.0-rc.1
    runner keeps a Codex login a key made until told so)."""
    from .model_login import ACTIVE_SQL

    row = c.execute(
        "SELECT k.*, r.label FROM computer_keys k JOIN runners r ON r.id=k.runner_id WHERE k.credential_id=?",
        (credential_id,),
    ).fetchone()
    if (
        row
        and row["state"] == STAGED
        and c.execute(f"SELECT 1 FROM model_logins WHERE id=? AND {ACTIVE_SQL}", (row["login_id"],)).fetchone()
    ):
        raise Problem(
            "state",
            f"{row['label']} is still checking this key; cancel Replace key there, or wait for it to finish",
            409,
        )
    c.execute("DELETE FROM computer_keys WHERE credential_id=?", (credential_id,))
    sub = c.execute(
        "SELECT s.id, cr.source FROM model_subscriptions s JOIN credentials cr ON cr.id=s.credential_id "
        "WHERE s.credential_id=?",
        (credential_id,),
    ).fetchone()
    if not sub:
        return
    given = c.execute(
        "SELECT runner_id, runtime FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sub["id"],)
    ).fetchall()
    c.execute("DELETE FROM computer_subscriptions WHERE subscription_id=? AND person=''", (sub["id"],))
    c.execute("DELETE FROM model_subscriptions WHERE id=?", (sub["id"],))
    from .model_login import mark_key_cleared

    for g in given:
        mark_key_cleared(c, who, g["runner_id"], g["runtime"])
        H.event(
            c,
            who.actor,
            "computer_key.deleted",
            g["runner_id"],
            {"runtime": g["runtime"], "credential": credential_id, "cleared": True},
        )


def active(c, runner_id, runtime):
    """This computer's own key in use for `runtime`: its credential row, or None."""
    found = _given(c, runner_id, runtime)
    return found if found and found["source"] == OWN + runner_id else None


def shared(c, runtime, runner_id=None):
    """The shared key given to computer `runner_id` for `runtime`: {env: credential row}, or {}."""
    if not runner_id:
        return {}
    found = _given(c, runner_id, runtime)
    return {found["env"]: found} if found and found["source"] != OWN + runner_id else {}


def shared_for(c, runtime, env=None, found=None, runner_id=None):
    """The shared credential row a computer runs `runtime` on, or None. `found` is `shared(c, runtime, runner_id)` when
    the caller has it."""
    found = shared(c, runtime, runner_id) if found is None else found
    return found.get(env) or next(iter(found.values()), None)


def _marked(c, runner_id, runtime):
    return any(
        c.execute(f"SELECT 1 FROM {t} WHERE runner_id=? AND runtime=? AND profile=''", (runner_id, runtime)).fetchone()
        for t in ("model_login_cleared", "model_login_key_off")
    )


def keys_for(c, vault, runner_id, runtime, login_id=""):
    """What `GET runner-model-credentials` hands this computer for `runtime`, in the one order: the key staged for a
    live `use_key` job (`login_id`), else the key it is given (its own as `computer`, else `shared`); nothing while its
    key was cleared or its login signed out. `clear_key` tells a computer that missed its Clear key job (it was offline)
    to clear its copy now. The reply keeps the shape runners of v0.6.0-rc.1 read. Returns (reply, credential ids sent)."""
    from .model_login import ACTIVE_SQL, KEY_JOBS_SQL

    if login_id:
        row = c.execute(
            "SELECT cr.* FROM computer_keys k JOIN credentials cr ON cr.id=k.credential_id "
            "JOIN model_logins l ON l.id=k.login_id WHERE k.login_id=? AND k.runner_id=? AND k.runtime=? "
            f"AND k.state=? AND l.{KEY_JOBS_SQL} AND l.{ACTIVE_SQL}",
            (login_id, runner_id, runtime, STAGED),
        ).fetchone()
        if not row:
            return {"credentials": []}, []
        return {"credentials": [{"env": row["env"], "value": vault.cipher.decrypt(c, row), "source": "computer"}]}, [
            row["id"]
        ]
    # A Switch to API key is running: the computer puts the new key in place itself, and until it says it is done what
    # Tico holds is still the subscription's. A regular fetch meanwhile changes nothing there (runner-rs/src/signin.rs).
    if c.execute(
        f"SELECT 1 FROM model_logins WHERE runner_id=? AND runtime=? AND profile='' AND kind='to_key' AND {ACTIVE_SQL}",
        (runner_id, runtime),
    ).fetchone():
        return {"credentials": [], "switching": True}, []
    mark = c.execute(
        "SELECT clear_key FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=''", (runner_id, runtime)
    ).fetchone()
    if mark:
        return {"credentials": [], "signed_out": True, **({"clear_key": True} if mark["clear_key"] else {})}, []
    # Switched to a subscription: no key reaches that login until a person adds one or picks Use the shared key.
    if c.execute(
        "SELECT 1 FROM model_login_key_off WHERE runner_id=? AND runtime=? AND profile=''", (runner_id, runtime)
    ).fetchone():
        return {"credentials": [], "key_off": True}, []
    found = _given(c, runner_id, runtime)
    if not found or found["env"] not in providers.MODEL_KEYS.get(runtime, ()):
        return {"credentials": []}, []
    source = "computer" if found["source"] == OWN + runner_id else "shared"
    return {"credentials": [{"env": found["env"], "value": vault.cipher.decrypt(c, found), "source": source}]}, [
        found["id"]
    ]


def listing_by_runner(c):
    """{runner_id: {runtime: {env, preview, since}}} of every computer's own keys in use: what its rows show, never a
    value (Operations listing)."""
    out = {}
    for row in c.execute(
        "SELECT g.runner_id, g.runtime, g.updated_at AS given_at, cr.env, cr.preview, cr.source "
        "FROM computer_subscriptions g JOIN model_subscriptions s ON s.id=g.subscription_id "
        "JOIN credentials cr ON cr.id=s.credential_id WHERE g.person='' AND cr.ciphertext IS NOT NULL "
        "ORDER BY g.updated_at"
    ):
        if row["source"] == OWN + row["runner_id"]:
            out.setdefault(row["runner_id"], {})[row["runtime"]] = {
                "env": row["env"],
                "preview": row["preview"],
                "since": row["given_at"],
            }
    return out


def uses_shared(c, runner, runtime):
    """The shared credential row `runner` runs `runtime` on, or None: one is given to it, and nothing a person did there
    stops it (Clear key, Sign out, Switch to subscription)."""
    found = shared_for(c, runtime, runner_id=runner["id"])
    if not found or _marked(c, runner["id"], runtime):
        return None
    return found


def _live_runners(c):
    return c.execute("SELECT * FROM runners_with_state WHERE revoked_at IS NULL ORDER BY lower(label), id").fetchall()


def serving(c):
    """{runner id: {runtime: {id, name, env, preview, every, computers: [{id, label}]}}} for every live computer: the
    shared key each runs each runtime on, and every computer that same key serves. Values never leave the vault."""
    runners = _live_runners(c)
    used, served = {}, {}
    for runner in runners:
        for runtime in RUNTIMES:
            row = uses_shared(c, runner, runtime)
            if row:
                used.setdefault(runner["id"], {})[runtime] = row
                served.setdefault(row["id"], []).append({"id": runner["id"], "label": runner["label"]})
    return {
        rid: {
            runtime: {
                "id": row["id"],
                "name": row["name"],
                "env": row["env"],
                "preview": row["preview"],
                "every": False,  # no key covers every computer, later ones too, any more (D11-5 drops the field)
                "computers": served[row["id"]],
            }
            for runtime, row in found.items()
        }
        for rid, found in used.items()
    }


def shared_listing(c, runner):
    """{runtime: {env, preview}} of the shared key given to `runner`, whether or not it runs on it now (`serving` says
    where it does)."""
    out = {}
    for runtime in RUNTIMES:
        row = shared_for(c, runtime, runner_id=runner["id"])
        if row:
            out[runtime] = {"env": row["env"], "preview": row["preview"]}
    return out


def users(c, runner, runtime):
    """The live computers that run `runtime` on the same shared key as `runner` (it included), or []."""
    row = uses_shared(c, runner, runtime)
    if not row:
        return []
    return [
        r for r in _live_runners(c) if (found := uses_shared(c, r, runtime)) is not None and found["id"] == row["id"]
    ]


def shared_keys(c, runtime):
    """Every shared key for `runtime` (credential rows), the one the most computers are given first."""
    allowed = names(runtime)
    rows = c.execute(
        "SELECT cr.*, (SELECT count(*) FROM computer_subscriptions g WHERE g.subscription_id=s.id AND g.person='') "
        "AS computers FROM model_subscriptions s JOIN credentials cr ON cr.id=s.credential_id "
        "WHERE cr.ciphertext IS NOT NULL AND cr.source NOT LIKE 'computer:%' "
        "ORDER BY computers DESC, s.created_at DESC, cr.id"
    ).fetchall()
    return [r for r in rows if r["env"] in allowed]
