"""The Tico AI Gateway connection in Settings > AI providers: one gateway key and the Gateway's URL.

It is the one key for everything the app asks the Gateway (clients/gateway.py): Decide's questions and the bots that run
on Tico Cheap, Mid and Power 1.0. The server's environment may set it (`TICO_GATEWAY_KEY`, `TICO_GATEWAY_URL`), which
wins and shows as connected; otherwise the owner saves it here. The saved key is sealed with the credential vault's
data key (backend/credentials.py `CredentialCipher.seal`), like the storage and Slack secrets: no read returns it, only
a preview of its first and last three characters, and no event or log line carries it.

While a key is set, the `tico` provider is on (`providers.load`), so Tico models can be picked like any other model;
clearing it turns them off. A bot on a Tico model gets the key and URL in its run's environment (`run_credentials`,
read by runner-rs/src/hosts/pi.rs), and no other bot does.

The Gateway's own word on the key (`<base>/key`: working, revoked, how much of the month's cap is used) is read on
save, on Check now, when the owner opens the page and hourly (backend/scheduler.py), never more than once a minute
unasked (`check_soon`: the Gateway answers it 6 times a minute per key), and kept with the connection for Settings and
Health.
"""

import hashlib
import json
import os
import threading
import time

from clients import gateway as G

from .store import H, Problem

KEY = "gateway-connection"
CONTEXT = "gateway-connection:key"
PROVIDER = "tico"
ENV_KEY, ENV_URL = "TICO_GATEWAY_KEY", "TICO_GATEWAY_URL"
# What a Tico-model bot's run gets them as. Not TICO_: the runner keeps those names for itself (runner-rs/src/env.rs).
RUN_KEY_ENV, RUN_URL_ENV = "TICOAI_GATEWAY_KEY", "TICOAI_GATEWAY_URL"
MODEL_PREFIX = "tico-"
# Unasked checks (page loads, the hourly sweep) are at least this far apart for one key.
CHECK_EVERY = 60
_tried, _tried_lock = {}, threading.Lock()


def is_tico(model):
    """Whether `model` is one of the Gateway's chat models, served with the gateway key."""
    return str(model or "").startswith(MODEL_PREFIX)


def env_key(settings=None):
    """The key the server's environment sets (Dev 178's `Settings.gateway_key` when it has one), or ''."""
    return str(getattr(settings, "gateway_key", "") or os.environ.get(ENV_KEY, "")).strip()


def env_url(settings=None):
    """The Gateway URL the environment sets, or ''. Dev 178's `Settings.gateway_url` always has one, Tico HQ's by
    default; that default is no choice, so it does not hide the URL saved in Settings."""
    configured = str(os.environ.get(ENV_URL, "") or getattr(settings, "gateway_url", "") or "").strip()
    return configured if configured and G.base_url(configured) != G.DEFAULT_BASE else ""


def _row(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    return stored if isinstance(stored, dict) else {}


def _write(c, record):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, json.dumps(record, sort_keys=True)))


def source(c, settings=None):
    """Where the key comes from: "environment", "settings" or "" when there is none."""
    return "environment" if env_key(settings) else "settings" if _row(c).get("key") else ""


def connected(c, settings=None):
    return bool(source(c, settings))


def key(c, vault, settings=None):
    """The gateway key, for a call to the Gateway or a run that makes one; '' when none is set. Never logged."""
    if env_key(settings):
        return env_key(settings)
    sealed = _row(c).get("key")
    return vault.cipher.unseal(c, CONTEXT, sealed) if sealed else ""


def url(c, settings=None):
    """The Gateway's base URL: the environment's, else the saved one, else Tico HQ's."""
    return G.base_url(env_url(settings) or _row(c).get("url") or "")


def view(c, settings=None, *, manage=False):
    """What Settings > AI providers shows. Never the key; its preview and the Gateway's last word only to the people
    who manage the connection (`manage`: the owner)."""
    record, where = _row(c), source(c, settings)
    out = {"label": G.LABEL, "connected": bool(where), "source": where, "url": url(c, settings),
           "default_url": G.DEFAULT_BASE, "editable": where != "environment"}
    if not manage:
        return out
    check = record.get("check") if isinstance(record.get("check"), dict) else {}
    # A check is about the key it was made with: one made before the key changed (or under the environment's) is none.
    fresh = check if check.get("for") == _fingerprint(c, settings) else {}
    state = fresh.get("state") or ("unchecked" if where else "none")
    return {**out, "preview": record.get("preview", "") if where == "settings" else "",
            "env": ENV_KEY if where == "environment" else "",
            "revision": int(record.get("revision") or 0),
            "updated": record.get("updated", ""), "updated_by": record.get("updated_by", ""),
            "state": state, "working": state not in G.REFUSED,
            "message": fresh.get("message") or ("Not checked yet." if where else "No gateway key saved."),
            "used": fresh.get("used"), "checked": fresh.get("at", "")}


def _fingerprint(c, settings=None):
    """Which key a check was made with, without keeping it: the source and the saved revision, or for the
    environment's key (which a restart can replace with no revision) a short one-way digest of it and its URL."""
    where = source(c, settings)
    if where == "environment":
        digest = hashlib.sha256(f"tico-gateway-check\n{env_key(settings)}\n{env_url(settings)}".encode()).hexdigest()
        return f"{where}:{digest[:16]}"
    return f"{where}:{_row(c).get('revision', 0)}" if where else ""


def owner_only(who):
    if who.role != "owner":
        raise Problem("forbidden", "Only the owner may change the Tico AI Gateway connection", 403)


def _clean_key(value):
    value = str(value or "").strip()
    if not value or len(value) > 4000 or any(ch in value for ch in "\r\n\0 "):
        raise Problem("key", "Paste the gateway key as one line", 422)
    return value


def _clean_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return G.safe_base(value)
    except ValueError:
        raise Problem("url", "The Gateway URL starts with https:// (plain http:// only for localhost), with no spaces, "
                      "query or password", 422) from None


def save(c, vault, who, body, now, settings=None):
    """Save a key (and URL), or only the URL with the key left as it is. Returns the record before and after, both
    without the key."""
    owner_only(who)
    if env_key(settings):
        raise Problem("environment", f"The server's environment sets the gateway key ({ENV_KEY}); change it there", 409)
    before = _row(c)
    if int(body.get("expected_revision") or 0) != int(before.get("revision") or 0):
        raise Problem("conflict", "The Gateway connection changed since you opened it; reload and try again", 409)
    secret = body.get("key")
    new_url = _clean_url(body.get("url"))
    after = {**before, "url": new_url if new_url != G.DEFAULT_BASE else "", "revision": int(before.get("revision") or 0) + 1,
             "updated": now, "updated_by": who.actor}
    if secret:
        secret = _clean_key(secret)
        if not vault.configured:
            raise Problem("vault_unavailable", "Credentials are not set up on this server, so a key cannot be stored", 503)
        after["key"] = vault.cipher.seal(c, CONTEXT, secret)
        after["preview"] = secret[:3] + "…" + secret[-3:] if len(secret) >= 12 else "••••••"
    elif not before.get("key"):
        raise Problem("key", "Paste the gateway key Tico gave you", 422)
    after.pop("check", None)
    _write(c, after)
    H.event(c, who.actor, "gateway.connection.saved", PROVIDER,
            {"key_changed": bool(secret), "url": after["url"] or G.DEFAULT_BASE, "revision": after["revision"]})
    return _public(before), _public(after)


def clear(c, who, now, settings=None):
    """Forget the saved key. Tico models turn off with it; bots on them stop until a key is saved again."""
    owner_only(who)
    if env_key(settings):
        raise Problem("environment", f"The server's environment sets the gateway key ({ENV_KEY}); remove it there", 409)
    before = _row(c)
    if not before.get("key"):
        return _public(before), _public(before)
    after = {"url": before.get("url", ""), "revision": int(before.get("revision") or 0) + 1, "updated": now,
             "updated_by": who.actor}
    _write(c, after)
    H.event(c, who.actor, "gateway.connection.cleared", PROVIDER, {"revision": after["revision"]})
    return _public(before), _public(after)


def _public(record):
    return {k: v for k, v in record.items() if k not in ("key", "check")}


def record_check(c, result, fingerprint, now, settings=None):
    """Keep the Gateway's last word on the key it was asked about (`fingerprint`); an answer about a key that has
    changed since is dropped, so it never replaces what the current key's check said."""
    if _fingerprint(c, settings) != fingerprint:
        return False
    record = _row(c)
    known = record.get("check") if isinstance(record.get("check"), dict) else {}
    if result["state"] in G.TRANSIENT and known.get("for") == fingerprint and known.get("state") not in G.TRANSIENT:
        return False                                  # a Gateway that was down or busy says nothing new about the key
    record["check"] = {"state": result["state"], "message": result["message"], "used": result.get("used"),
                       "at": now, "for": fingerprint}
    _write(c, record)
    return True


def check_now(store, vault, settings=None, opener=None):
    """Ask the Gateway about the key now (outside any transaction: it is a network call) and keep the answer."""
    with store.read() as c:
        secret, base, fingerprint = key(c, vault, settings), url(c, settings), _fingerprint(c, settings)
    if not secret:
        return None
    result = G.check(secret, base, opener=opener)
    with store.transaction() as c:
        record_check(c, result, fingerprint, H.now(), settings)
    return result


def check_soon(store, vault, settings=None, opener=None):
    """`check_now` on a background thread, unless this key was asked about less than CHECK_EVERY seconds ago. For the
    checks nobody pressed a button for: the page opening and the hourly sweep. Returns the thread, or None."""
    with store.read() as c:
        fingerprint = _fingerprint(c, settings)
    if not fingerprint:
        return None
    now = time.monotonic()
    with _tried_lock:
        if now - _tried.get(fingerprint, -CHECK_EVERY) < CHECK_EVERY:
            return None
        _tried[fingerprint] = now
    return check_later(store, vault, settings, opener)


def check_later(store, vault, settings=None, opener=None):
    """`check_now` on a background thread: the sweep and page loads never wait on the Gateway."""
    def run():
        try:
            check_now(store, vault, settings, opener)
        except Exception:
            pass                                          # the next check tries again; the last answer stays
    thread = threading.Thread(target=run, name="gateway-check", daemon=True)
    thread.start()
    return thread


def wanted(config):
    """Whether a run on `config` (a bot's config with its runtime and model filled in) may call the Gateway: its model,
    or the fallback it names itself, is a Tico model."""
    config = config if isinstance(config, dict) else {}
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    return is_tico(config.get("model")) or is_tico(fallback.get("model"))


HANDED = "gateway-attempt:"


def hand_out(c, attempt_id, config):
    """Remember the Tico models the claim handed this attempt out on (its model, and the fallback it names), when it has
    any. The key and the run's outcome follow what the run was given, never a setting changed while it runs."""
    from .repositories import save_metadata
    if not wanted(config):
        return
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    save_metadata(c, HANDED + attempt_id, {"model": str(config.get("model") or ""), "fallback": {
        "harness": str(fallback.get("harness") or ""), "model": str(fallback.get("model") or "")}})


def handed(c, attempt_id):
    """What `hand_out` remembered for the attempt, or {} (a run that was given no Tico model)."""
    from .repositories import metadata
    return metadata(c, HANDED + attempt_id) if attempt_id else {}


def forget(c, attempt_id):
    c.execute("DELETE FROM registry_metadata WHERE key=?", (HANDED + attempt_id,))


def ran_tico(config, body):
    """Whether the part of a run that ended it (`body`, its completion) ran on a Tico model: the model the runner
    counted last; else, when a fallback harness ran the turn, the fallback the bot names itself (an automatic one is
    never a Tico model, providers.default_fallbacks); else the run's own model."""
    usage = getattr(body, "usage", None)
    segments = (usage.segments or [usage]) if usage else []
    if segments and segments[-1].model:
        return is_tico(segments[-1].model)
    if getattr(body, "fallback", None):
        fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
        return fallback.get("harness") == body.fallback and is_tico(fallback.get("model"))
    return is_tico(config.get("model"))


def run_credentials_for(c, vault, settings, who, attempt_id):
    """`run_credentials` for attempt `attempt_id`, by what its claim handed it out on, to the runner `who` that holds it.
    Only a runner is handed the key: its turn reaches the Gateway through the runner's relay with a token for that turn
    alone (runner-rs/src/gateway_relay.rs), because the turn's own commands can read whatever the turn's processes hold."""
    row = c.execute("SELECT bot FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    config = handed(c, attempt_id)
    if not row or not wanted(config):
        return []
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    items = run_credentials(c, vault, settings, config.get("model") if is_tico(config.get("model"))
                            else fallback.get("model"))
    if items:
        H.event(c, who.actor, "gateway.key_sent", PROVIDER,
                {"bot": row["bot"], "attempt": attempt_id, "runner": who.runner_id})
    return items


def run_credentials(c, vault, settings, model):
    """What a run on `model` gets in its environment from the connection: the key and URL for a Tico model with a key
    set, else nothing. Shaped like `GET credential-runtime`'s items."""
    if not is_tico(model):
        return []
    secret = key(c, vault, settings)
    try:
        base = G.safe_base(url(c, settings))
    except ValueError:
        return []                                     # an environment URL the key may not go to (the page says so)
    if not secret:
        return []
    return [{"id": "gateway-key", "name": G.LABEL, "env": RUN_KEY_ENV, "kind": "api_key", "value": secret},
            {"id": "gateway-url", "name": G.LABEL + " URL", "env": RUN_URL_ENV, "kind": "text",
             "value": G.chat_base(base)}]


def health(c, settings=None):
    """The Health row for the people who manage the connection, or None when there is nothing to say."""
    shown = view(c, settings, manage=True)
    if not shown["connected"] or shown["state"] in ("connected", "unchecked", *G.TRANSIENT):
        return None
    status = "warn" if shown["state"] in ("warning", "not_ready") else "bad"
    return {"status": status, "summary": shown["message"]}
