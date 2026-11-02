"""A model API key stored for one computer: Add key, Replace key and Clear key in Settings > Computers.

The key is a credential in the vault (backend/credentials.py), sealed like any other and shown only by its preview.
It is this computer's alone: `computer_keys` names the computer and runtime it signs in, and whether it is in use
(`active`) or waiting for the computer to check it (`staged`). The login queue never carries it: a `use_key` job, or a
`to_key` one (Switch to API key) (backend/model_login.py), has no value, and the runner fetches the staged key from
`GET runner-model-credentials` with that job's id, checks it, and reports. `confirm` then puts it in use and deletes
the key it replaces; `discard` deletes a key that failed, so the old one stays (runner-rs/src/signin.rs).

`keys_for` is the one place that decides which key a computer gets: its own active key, else a key shared with it by
name, else the company's shared "Every computer" key, else none; and none while a person cleared its key or signed it
out (`model_login_cleared`), or switched it to a subscription (`model_login_key_off`).

A shared key is a credential with grants to computers: subject `computers` ("Every computer" in Credentials), or one
`computer:<runner id>` per computer it was shared with in Settings > Computers ("Shared with: [computers]").
`serving` is which computers each one actually serves, the one list the Computers page and the "this computer only /
all listed computers" ask both use.
"""

from . import providers
from .credentials import COMPUTERS, granted
from .store import H, Problem

SHARED_WITH = "computer:"

RUNTIMES = tuple(providers.MODEL_KEYS)
STAGED, ACTIVE = "staged", "active"


def names(runtime):
    """The variables a key for `runtime` arrives in, the first being what a new key is stored as."""
    found = providers.MODEL_KEYS.get(runtime)
    if not found:
        raise Problem("kind", "A key signs in only " + ", ".join(RUNTIMES) + " on a computer", 422)
    return found


def _remove(c, credential_id):
    c.execute("DELETE FROM computer_keys WHERE credential_id=?", (credential_id,))
    c.execute("DELETE FROM credential_grants WHERE credential_id=?", (credential_id,))
    c.execute("DELETE FROM credentials WHERE id=?", (credential_id,))


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
    cid, now = H.new_id(), H.now()
    ciphertext, nonce = vault.cipher.encrypt(c, cid, value)
    preview = value[:3] + "…" + value[-3:] if len(value) >= 12 else "••••••"
    c.execute("INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,updated_by,"
              "created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              (cid, f"{env} for {runner['label']}", "api_key", env, preview, ciphertext, nonce,
               "computer:" + runner["id"], now, now, who.actor, who.actor))
    c.execute("INSERT INTO computer_keys(credential_id,runner_id,runtime,state,login_id,created_at,created_by) "
              "VALUES(?,?,?,?,?,?,?)", (cid, runner["id"], runtime, STAGED, login_id, now, who.actor))
    H.event(c, who.actor, "computer_key.stored", runner["id"], {"runtime": runtime, "env": env, "credential": cid})
    return {"id": cid, "env": env, "preview": preview}


def confirm(c, runner_id, login_id):
    """The computer checked the key staged for `login_id`: it is now this computer's key, and the one it replaces is
    deleted from the vault."""
    row = c.execute("SELECT * FROM computer_keys WHERE login_id=? AND runner_id=? AND state=?",
                    (login_id, runner_id, STAGED)).fetchone()
    if not row:
        return None
    for old in c.execute("SELECT credential_id FROM computer_keys WHERE runner_id=? AND runtime=? AND state=?",
                         (runner_id, row["runtime"], ACTIVE)).fetchall():
        _remove(c, old["credential_id"])
    c.execute("UPDATE computer_keys SET state=?, login_id=NULL WHERE credential_id=?", (ACTIVE, row["credential_id"]))
    # In use again: a key added after a clear, or after a switch to a subscription (Switch to API key included), signs
    # it in (the shared key stays behind it).
    for table in ("model_login_cleared", "model_login_key_off"):
        c.execute(f"DELETE FROM {table} WHERE runner_id=? AND runtime=? AND profile=''", (runner_id, row["runtime"]))
    H.event(c, "runner:" + runner_id, "computer_key.active", runner_id,
            {"runtime": row["runtime"], "credential": row["credential_id"]})
    return row["credential_id"]


def discard(c, login_id):
    """A staged key the computer could not use, or whose check was cancelled or ran out: deleted, the old key kept."""
    for row in c.execute("SELECT credential_id FROM computer_keys WHERE login_id=? AND state=?",
                         (login_id, STAGED)).fetchall():
        _remove(c, row["credential_id"])


def delete(c, runner_id, runtime):
    """Clear key: this computer's own key for `runtime` is deleted from the vault (staged ones too)."""
    rows = c.execute("SELECT credential_id FROM computer_keys WHERE runner_id=? AND runtime=?",
                     (runner_id, runtime)).fetchall()
    for row in rows:
        _remove(c, row["credential_id"])
    return len(rows)


def before_delete(c, who, credential_id):
    """Credentials > Delete on a computer's own key. One in use goes like any credential: the computer falls back to
    the shared key at its next key fetch, or, with none, the key is cleared there as Clear key does. One a computer is
    still checking is refused, so a check never confirms a key that is gone; it is cancelled or ends first."""
    from .model_login import ACTIVE_SQL
    row = c.execute("SELECT k.*, r.label FROM computer_keys k JOIN runners r ON r.id=k.runner_id "
                    "WHERE k.credential_id=?", (credential_id,)).fetchone()
    if not row:
        return
    if row["state"] == STAGED and c.execute(f"SELECT 1 FROM model_logins WHERE id=? AND {ACTIVE_SQL}",
                                            (row["login_id"],)).fetchone():
        raise Problem("state", f"{row['label']} is still checking this key; cancel Replace key there, or wait for it "
                      "to finish", 409)
    c.execute("DELETE FROM computer_keys WHERE credential_id=?", (credential_id,))
    # With no shared key to sign in with next, deleting the key in use is Clear key: the computer is told to delete its
    # copies and log out the login the key made, or that login would keep working on a key that is gone.
    cleared = row["state"] == ACTIVE and not shared(c, row["runtime"], row["runner_id"])
    if cleared:
        from .model_login import mark_key_cleared
        mark_key_cleared(c, who, row["runner_id"], row["runtime"])
    H.event(c, who.actor, "computer_key.deleted", row["runner_id"],
            {"runtime": row["runtime"], "credential": credential_id, "cleared": cleared})


def active(c, runner_id, runtime):
    """This computer's own key in use for `runtime`: its credential row, or None."""
    return c.execute("SELECT cr.* FROM computer_keys k JOIN credentials cr ON cr.id=k.credential_id "
                     "WHERE k.runner_id=? AND k.runtime=? AND k.state=? AND cr.ciphertext IS NOT NULL "
                     "ORDER BY k.created_at DESC", (runner_id, runtime, ACTIVE)).fetchone()


def _sealed_credentials(c):
    """Sealed credential rows once per read snapshot. `shared` used to re-scan the table for every runtime × computer
    (Operations `serving` / `shared_listing`)."""
    from . import read_cache
    return read_cache.rows(c, "SELECT * FROM credentials WHERE ciphertext IS NOT NULL ORDER BY id")


def shared(c, runtime, runner_id=None):
    """The shared key for `runtime` on computer `runner_id`: {env: credential row}, the first stored per variable. A key
    shared with that computer by name comes before the company's "Every computer" key; with no computer, only the
    latter."""
    allowed = providers.MODEL_KEYS.get(runtime) or ()
    sealed = _sealed_credentials(c)
    for held in ([granted(c, SHARED_WITH + runner_id)] if runner_id else []) + [granted(c, COMPUTERS)]:
        found = {}
        for row in sealed:
            if row["env"] in allowed and row["env"] not in found and row["id"] in held:
                found[row["env"]] = row
        if found:
            return {env: found[env] for env in allowed if env in found}
    return {}


def shared_for(c, runtime, env=None, found=None, runner_id=None):
    """The shared credential row a computer reporting `env` runs on: that variable's, else the first. `found` is
    `shared(c, runtime, runner_id)` when the caller has it."""
    found = shared(c, runtime, runner_id) if found is None else found
    return found.get(env) or next(iter(found.values()), None)


def keys_for(c, vault, runner_id, runtime, login_id=""):
    """What `GET runner-model-credentials` hands this computer for `runtime`, in the one order: the key staged for a
    live `use_key` job (`login_id`), else its own active key, else the shared key; nothing while its key was cleared
    or its login signed out. `clear_key` tells a computer that missed its Clear key job (it was offline) to clear
    its copy now. Returns (reply, credential ids sent)."""
    from .model_login import ACTIVE_SQL, KEY_JOBS_SQL
    if login_id:
        row = c.execute("SELECT cr.* FROM computer_keys k JOIN credentials cr ON cr.id=k.credential_id "
                        "JOIN model_logins l ON l.id=k.login_id WHERE k.login_id=? AND k.runner_id=? AND k.runtime=? "
                        f"AND k.state=? AND l.{KEY_JOBS_SQL} AND l.{ACTIVE_SQL}",
                        (login_id, runner_id, runtime, STAGED)).fetchone()
        if not row:
            return {"credentials": []}, []
        return {"credentials": [{"env": row["env"], "value": vault.cipher.decrypt(c, row), "source": "computer"}]}, [row["id"]]
    # A Switch to API key is running: the computer puts the new key in place itself, and until it says it is done what
    # Tico holds is still the subscription's. A regular fetch meanwhile changes nothing there (runner-rs/src/signin.rs).
    if c.execute(f"SELECT 1 FROM model_logins WHERE runner_id=? AND runtime=? AND profile='' AND kind='to_key' "
                 f"AND {ACTIVE_SQL}", (runner_id, runtime)).fetchone():
        return {"credentials": [], "switching": True}, []
    mark = c.execute("SELECT clear_key FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=''",
                     (runner_id, runtime)).fetchone()
    if mark:
        return {"credentials": [], "signed_out": True, **({"clear_key": True} if mark["clear_key"] else {})}, []
    # Switched to a subscription: no key reaches that login until a person adds one or picks Use the shared key.
    if c.execute("SELECT 1 FROM model_login_key_off WHERE runner_id=? AND runtime=? AND profile=''",
                 (runner_id, runtime)).fetchone():
        return {"credentials": [], "key_off": True}, []
    own = active(c, runner_id, runtime)
    if own:
        return {"credentials": [{"env": own["env"], "value": vault.cipher.decrypt(c, own), "source": "computer"}]}, [own["id"]]
    found = shared(c, runtime, runner_id)
    return ({"credentials": [{"env": env, "value": vault.cipher.decrypt(c, row), "source": "shared"}
                             for env, row in found.items()]}, [row["id"] for row in found.values()])


def listing(c, runner_id):
    """{runtime: {env, preview, since}} of this computer's own keys in use: what its rows show, never a value."""
    out = {}
    for row in c.execute("SELECT k.runtime, k.created_at, cr.env, cr.preview FROM computer_keys k "
                         "JOIN credentials cr ON cr.id=k.credential_id WHERE k.runner_id=? AND k.state=? "
                         "ORDER BY k.created_at", (runner_id, ACTIVE)):
        out[row["runtime"]] = {"env": row["env"], "preview": row["preview"], "since": row["created_at"]}
    return out


def listing_by_runner(c):
    """{runner_id: listing(...)} for every computer in one pass (Operations listing)."""
    out = {}
    for row in c.execute("SELECT k.runner_id, k.runtime, k.created_at, cr.env, cr.preview FROM computer_keys k "
                         "JOIN credentials cr ON cr.id=k.credential_id WHERE k.state=? "
                         "ORDER BY k.created_at", (ACTIVE,)):
        out.setdefault(row["runner_id"], {})[row["runtime"]] = {
            "env": row["env"], "preview": row["preview"], "since": row["created_at"]}
    return out


def _readiness(runner, runtime):
    """What `runner` last reported for `runtime`: its readiness row, or {}."""
    import json
    try:
        row = (json.loads(runner["readiness"] or "{}").get("runtimes") or {}).get(runtime)
    except (ValueError, AttributeError):
        return {}
    return row if isinstance(row, dict) else {}


def _reported(runner, runtime):
    key = _readiness(runner, runtime).get("key")
    return key if isinstance(key, dict) else {}


def uses_shared(c, runner, runtime):
    """The shared credential row `runner` runs `runtime` on, or None: one is shared with it, nothing a person did there
    stops it (Clear key, Sign out, Switch to subscription, its own key), and the computer has not said it signs in
    another way (a key set in its terminal, or an account login). One whose tool is not signed in yet counts, so a
    computer shows the key it is about to use."""
    rid = runner["id"]
    found = shared(c, runtime, rid)
    if not found or active(c, rid, runtime):
        return None
    for table in ("model_login_cleared", "model_login_key_off"):
        if c.execute(f"SELECT 1 FROM {table} WHERE runner_id=? AND runtime=? AND profile=''", (rid, runtime)).fetchone():
            return None
    reported = _reported(runner, runtime)
    if reported.get("source") not in (None, "", "shared"):
        return None
    if not reported and _readiness(runner, runtime).get("authenticated") == "ready":
        return None
    return shared_for(c, runtime, reported.get("name"), found)


def serving(c):
    """{runner id: {runtime: {id, name, env, preview, every, computers: [{id, label}]}}} for every live computer: the
    shared key each runs each runtime on, and every computer that same key serves. Values never leave the vault."""
    runners = c.execute("SELECT * FROM runners WHERE revoked_at IS NULL ORDER BY lower(label), id").fetchall()
    every = granted(c, COMPUTERS)
    used, served = {}, {}
    for runner in runners:
        for runtime in RUNTIMES:
            row = uses_shared(c, runner, runtime)
            if row:
                used.setdefault(runner["id"], {})[runtime] = row
                served.setdefault(row["id"], []).append({"id": runner["id"], "label": runner["label"]})
    return {rid: {runtime: {"id": row["id"], "name": row["name"], "env": row["env"], "preview": row["preview"],
                            "every": row["id"] in every, "computers": served[row["id"]]}
                  for runtime, row in found.items()} for rid, found in used.items()}


def shared_listing(c, runner):
    """{runtime: {env, preview}} of the shared key `runner` falls back on (shared with it by name, else "Every
    computer"), per runtime the one whose name it reported, so a preview never names the other kind. Listed whether or
    not the computer runs on it now (`serving` says where it does)."""
    out = {}
    for runtime in RUNTIMES:
        row = shared_for(c, runtime, _reported(runner, runtime).get("name"), runner_id=runner["id"])
        if row:
            out[runtime] = {"env": row["env"], "preview": row["preview"]}
    return out


def users(c, runner, runtime):
    """The live computers that run `runtime` on the same shared key as `runner` (it included), or []."""
    row = uses_shared(c, runner, runtime)
    if not row:
        return []
    return [r for r in c.execute("SELECT * FROM runners WHERE revoked_at IS NULL ORDER BY lower(label), id")
            if (found := uses_shared(c, r, runtime)) is not None and found["id"] == row["id"]]
