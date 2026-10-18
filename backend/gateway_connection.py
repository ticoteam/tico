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
save, on Check now and hourly (backend/scheduler.py), and kept with the connection for Settings and Health.
"""

import json
import os
import threading

from clients import gateway as G

from .store import H, Problem

KEY = "gateway-connection"
CONTEXT = "gateway-connection:key"
PROVIDER = "tico"
ENV_KEY, ENV_URL = "TICO_GATEWAY_KEY", "TICO_GATEWAY_URL"
# What a Tico-model bot's run gets them as. Not TICO_: the runner keeps those names for itself (runner-rs/src/env.rs).
RUN_KEY_ENV, RUN_URL_ENV = "TICOAI_GATEWAY_KEY", "TICOAI_GATEWAY_URL"
MODEL_PREFIX = "tico-"


def is_tico(model):
    """Whether `model` is one of the Gateway's chat models, served with the gateway key."""
    return str(model or "").startswith(MODEL_PREFIX)


def env_key(settings=None):
    """The key the server's environment sets (Dev 178's `Settings.gateway_key` when it has one), or ''."""
    return str(getattr(settings, "gateway_key", "") or os.environ.get(ENV_KEY, "")).strip()


def env_url(settings=None):
    return str(getattr(settings, "gateway_url", "") or os.environ.get(ENV_URL, "")).strip()


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
            "updated": record.get("updated", ""), "updated_by": record.get("updated_by", ""),
            "state": state, "working": state not in ("revoked", "invalid"),
            "message": fresh.get("message") or ("Not checked yet." if where else "No gateway key saved."),
            "used": fresh.get("used"), "checked": fresh.get("at", "")}


def _fingerprint(c, settings=None):
    """Which key a check was made with, without keeping it: the source and the saved revision."""
    where = source(c, settings)
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
    if not (value.startswith("https://") or value.startswith("http://localhost") or value.startswith("http://127.0.0.1")):
        raise Problem("url", "The Gateway URL starts with https://", 422)
    if any(ch in value for ch in " \r\n\0?#@"):
        raise Problem("url", "Enter the Gateway URL alone, with no spaces, query or password", 422)
    return G.base_url(value)


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


def check_later(store, vault, settings=None):
    """`check_now` on a background thread: the hourly sweep never waits on the Gateway."""
    def run():
        try:
            check_now(store, vault, settings)
        except Exception:
            pass                                          # the next hour tries again; the last answer stays
    threading.Thread(target=run, name="gateway-check", daemon=True).start()


def wanted(config):
    """Whether a run on `config` (a bot's config with its runtime and model filled in) may call the Gateway: its model,
    or the fallback it names itself, is a Tico model."""
    config = config if isinstance(config, dict) else {}
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    return is_tico(config.get("model")) or is_tico(fallback.get("model"))


def run_config(c, settings, bot, attempt_id):
    """The config a bot's run started on, worked out as the claim did (backend/execution.py): the bot's own or shared
    config, a check subtask's model on its task, the company default filled in."""
    from . import check_models, providers
    from .shared_bots import follow
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    config = follow(c, bot, json.loads(row[0]) if row and row[0] else {})
    turn = c.execute("SELECT task_id FROM turns WHERE id=?", (attempt_id,)).fetchone() if attempt_id else None
    if turn and turn["task_id"]:
        config = check_models.apply(config, check_models.of(c, turn["task_id"], bot))
    return providers.fill(providers.load(c, settings), config)


def run_credentials_for(c, vault, settings, who):
    """`run_credentials` for the bot run `who` (a bot identity with its attempt)."""
    bot = H.actor_id(who.actor)
    config = run_config(c, settings, bot, who.attempt_id)
    if not wanted(config):
        return []
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    items = run_credentials(c, vault, settings, config.get("model") if is_tico(config.get("model"))
                            else fallback.get("model"))
    if items:
        H.event(c, who.actor, "gateway.key_sent", PROVIDER, {"bot": bot, "attempt": who.attempt_id or None})
    return items


def run_credentials(c, vault, settings, model):
    """What a run on `model` gets in its environment from the connection: the key and URL for a Tico model with a key
    set, else nothing. Shaped like `GET credential-runtime`'s items."""
    if not is_tico(model):
        return []
    secret = key(c, vault, settings)
    if not secret:
        return []
    return [{"id": "gateway-key", "name": G.LABEL, "env": RUN_KEY_ENV, "kind": "api_key", "value": secret},
            {"id": "gateway-url", "name": G.LABEL + " URL", "env": RUN_URL_ENV, "kind": "text",
             "value": G.chat_base(url(c, settings))}]


def health(c, settings=None):
    """The Health row for the people who manage the connection, or None when there is nothing to say."""
    shown = view(c, settings, manage=True)
    if not shown["connected"] or shown["state"] in ("connected", "unchecked", "unreachable"):
        return None
    status = "warn" if shown["state"] == "warning" else "bad"
    return {"status": status, "summary": shown["message"]}
