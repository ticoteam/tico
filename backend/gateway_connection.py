"""The Tico AI Gateway connection in Settings > Computers: one gateway key and the Gateway's URL.

It is the one key for everything the app asks the Gateway (clients/gateway.py): Decide's questions and the bots that run
on Tico's models (Tico Cheap, Mid and Power). The server's environment may set it (`TICO_GATEWAY_KEY`, `TICO_GATEWAY_URL`), which
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

An install can also get its own key with no one copying it (`auto_tick`): when the install command sets an invite
(`TICO_GATEWAY_INVITE`) and no key is set, the server enrolls at start and hourly, keeps the key sealed like a pasted
one with `origin: "automatic"`, and swaps it for a new one when the Gateway says (`rotate_after`). The install's id and
secret (sealed) prove it is the same install if it ever loses the key. A pasted or environment key always wins. When Tico
turns the install off, enrolling stops and Health says so in plain words.

A self-run server linked to its Tico company (backend/company_server.py) gets its key the same way with no invite: it
asks HQ with its server key (`company_server.gateway_key`), which enrolls the install for the company, so Tico models
spend the company's credit (`via: "company"`). It asks right after linking and hourly while it has no key, before an
install command's invite. When the link ends (either side) HQ revokes that key and the server forgets it
(`company_ended`); the install keeps its id and secret and asks again once it is linked again.
"""

import datetime
import hashlib
import json
import os
import secrets
import threading
import time

from clients import gateway as G

from . import destinations, read_cache
from .store import H, Problem

KEY = "gateway-connection"
CONTEXT = "gateway-connection:key"
PROVIDER = "tico"
ENV_KEY, ENV_URL = "TICO_GATEWAY_KEY", "TICO_GATEWAY_URL"
# What a Tico-model bot's run gets them as. Not TICO_: the runner keeps those names for itself (runner-rs/src/env.rs).
RUN_KEY_ENV, RUN_URL_ENV = "TICOAI_GATEWAY_KEY", "TICOAI_GATEWAY_URL"
# The bot's opaque id for the Gateway's ledger, to the runner's relay only (runner-rs/src/gateway_relay.rs).
RUN_BOT_ENV = "TICOAI_GATEWAY_BOT"
MODEL_PREFIX = "tico-"
INVITE_ENV = "TICO_GATEWAY_INVITE"
INSTALL_CONTEXT = "gateway-connection:install"
AUTOMATIC = "automatic"
COMPANY = "company"                                       # `via`: the key and identity came from the company link
SYSTEM = "system:gateway"
# What Health says once Tico has turned this install off: it then never asks again under the same invite.
STOPPED = "Tico AI Gateway was turned off for this install by Tico. Contact support."
# Refusals that mean "this key is gone, but the install may still be welcome": enroll again with the install's secret.
ENROLL_AGAIN = (G.REVOKED_KEY, G.UNKNOWN_KEY, "already_rotated", "not_enrolled")
# Why an enroll did not work, for the owner on Health; anything else is a plain "try again later".
AUTO_MESSAGES = {
    "invalid_invite": "Tico AI Gateway could not connect automatically: the invite code in the install command is not "
                      "one Tico made. Ask Tico Team for a new one, or paste a gateway key here.",
    "invite_expired": "Tico AI Gateway could not connect automatically: the invite code has expired. Ask Tico Team for a "
                      "new one, or paste a gateway key here.",
    "invite_used_up": "Tico AI Gateway could not connect automatically: the invite code has been used by all the "
                      "installs it allows. Ask Tico Team for a new one, or paste a gateway key here.",
    "invite_revoked": "Tico AI Gateway could not connect automatically: Tico cancelled the invite code. Ask Tico Team "
                      "for a new one, or paste a gateway key here.",
}
AUTO_LATER = "Tico AI Gateway could not connect automatically yet. Tico tries again every hour."
_auto_lock = threading.Lock()
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
    row = read_cache.metadata(c, KEY)
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    return stored if isinstance(stored, dict) else {}


def _write(c, record):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, json.dumps(record, sort_keys=True)))


def revision(c):
    """The saved connection's revision: every save, clear and automatic key adds one."""
    return int(_row(c).get("revision") or 0)


def source(c, settings=None, record=None):
    """Where the key comes from: "environment", "settings" or "" when there is none. `record`: the saved connection
    already read (`target`)."""
    return "environment" if env_key(settings) else "settings" if (_row(c) if record is None else record).get("key") else ""


def connected(c, settings=None):
    return bool(source(c, settings))


def key(c, vault, settings=None):
    """The gateway key; '' when none is set. Never logged. A call to the Gateway takes it with its address from
    `target`."""
    if env_key(settings):
        return env_key(settings)
    sealed = _row(c).get("key")
    return vault.cipher.unseal(c, CONTEXT, sealed) if sealed else ""


def url(c, settings=None, record=None):
    """The Gateway's base URL: the environment's, else the saved one, else Tico HQ's. Not for a send: `target`."""
    return G.base_url(env_url(settings) or (_row(c) if record is None else record).get("url") or "")


def target(c, vault, settings=None):
    """(key, base URL, `_fingerprint`) for a call to the Gateway, all from one read of the saved connection; key '' when
    none is set. Every send takes its key and address from here, never from `key` and `url` one by one: outside a
    transaction each read sees the newest save, so a save in between (a new address with Clear, or with a newly typed
    key) would pair the key it took away with an address it was never kept for."""
    record = _row(c)
    if env_key(settings):
        secret = env_key(settings)
    else:
        secret = vault.cipher.unseal(c, CONTEXT, record["key"]) if record.get("key") else ""
    return secret, url(c, settings, record), _fingerprint(c, settings, record)


def view(c, settings=None, *, manage=False):
    """What Settings > Computers shows. Never the key; its preview and the Gateway's last word only to the people
    who manage the connection (`manage`: the owner)."""
    record, where = _row(c), source(c, settings)
    out = {"label": G.LABEL, "connected": bool(where), "source": where, "url": url(c, settings),
           "default_url": G.DEFAULT_BASE, "editable": where != "environment",
           "automatic": where == "settings" and record.get("origin") == AUTOMATIC}
    if not manage:
        return out
    auto = record.get("auto") if isinstance(record.get("auto"), dict) else {}
    # Cleared by the owner, or turned off by Tico (and since turned back on there): the owner may try again.
    out["auto_off"] = bool(auto.get("off") or auto.get("stopped")) and not where and (bool(invite(settings))
                                                                         or _identity(record))
    check = record.get("check") if isinstance(record.get("check"), dict) else {}
    # A check is about the key it was made with: one made before the key changed (or under the environment's) is none.
    fresh = check if check.get("for") == _fingerprint(c, settings) else {}
    state = fresh.get("state") or ("unchecked" if where else "none")
    return {**out, "preview": record.get("preview", "") if where == "settings" else "",
            "env": ENV_KEY if where == "environment" else "",
            "revision": int(record.get("revision") or 0),
            "updated_at": record.get("updated_at", ""), "updated_by": record.get("updated_by", ""),
            "state": state, "working": state not in G.REFUSED,
            "message": fresh.get("message") or ("Not checked yet." if where else "No gateway key saved."),
            "used": fresh.get("used"), "spent_usd": fresh.get("spent_usd"), "cap_usd": fresh.get("cap_usd"),
            "checked": fresh.get("at", "")}


def _fingerprint(c, settings=None, record=None):
    """Which key a check was made with, without keeping it: the source and the saved revision, or for the
    environment's key (which a restart can replace with no revision) a short one-way digest of it and its URL."""
    record = _row(c) if record is None else record
    where = source(c, settings, record)
    if where == "environment":
        digest = hashlib.sha256(f"tico-gateway-check\n{env_key(settings)}\n{env_url(settings)}".encode()).hexdigest()
        return f"{where}:{digest[:16]}"
    return f"{where}:{record.get('revision', 0)}" if where else ""


def key_id(c, settings=None, record=None):
    """A non-secret id of the gateway key in use, for usage records: the Gateway's own id of an automatic key (`gk_`),
    else `_fingerprint`. Never the key; '' with none."""
    record = _row(c) if record is None else record
    if source(c, settings, record) == "settings" and record.get("origin") == AUTOMATIC and record.get("key_id"):
        return str(record["key_id"])
    return _fingerprint(c, settings, record)


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


LABEL_KEY = "Gateway key"


def destination_change(c, body, settings=None):
    """What saving `body` would do to where the saved key goes (backend/destinations.py): None when it stays with the
    same service (a path on the same origin is the same service) or no key is saved here, else the safe old and new
    destination. An empty URL is Tico's address, so going back to it from another is a change too."""
    before = _row(c)
    if env_key(settings) or not before.get("key"):
        return None
    return destinations.change(G.base_url(before.get("url") or ""), G.base_url(_clean_url(body.get("url"))))


def save(c, vault, who, body, now, settings=None):
    """Save a key (and URL), or only the URL with the key left as it is. Returns the record before and after, both
    without the key.

    A new address for another service while a key is saved is held (`destinations.held`) until the body says what
    happens to the key: `credentials: "keep"` sends it to the new address, `"clear"` drops it in the same write (Tico
    models turn off, connecting automatically stays off, and no check is sent). A key typed in the same save replaces
    the saved one, so nothing saved moves. Either way the change leaves one `integration.destination_changed` event."""
    owner_only(who)
    if env_key(settings):
        raise Problem("environment", f"The server's environment sets the gateway key ({ENV_KEY}); change it there", 409)
    before = _row(c)
    if int(body.get("expected_revision") or 0) != int(before.get("revision") or 0):
        raise Problem("conflict", "The Gateway connection changed since you opened it; reload and try again", 409)
    secret = body.get("key")
    new_url = _clean_url(body.get("url"))
    option = destinations.choice(body.get("credentials"))
    moved = destination_change(c, body, settings)
    if moved and not secret and not option:
        raise destinations.held("gateway", moved, [LABEL_KEY], before.get("revision"), G.LABEL)
    destinations.require_tap(who, moved)
    after = {**before, "url": new_url if new_url != G.DEFAULT_BASE else "", "revision": int(before.get("revision") or 0) + 1,
             "updated_at": now, "updated_by": who.actor}
    if secret:
        secret = _clean_key(secret)
        if not vault.configured:
            raise Problem("vault_unavailable", "Credentials are not set up on this server, so a key cannot be stored", 503)
        after["key"] = vault.cipher.seal(c, CONTEXT, secret)
        after["preview"] = secret[:3] + "…" + secret[-3:] if len(secret) >= 12 else "••••••"
        for field in ("origin", "key_id", "rotate_after"):
            after.pop(field, None)                        # a pasted key is the owner's: never rotated or replaced
    elif moved and option == destinations.CLEAR:
        for field in ("key", "preview", "origin", "key_id", "rotate_after"):
            after.pop(field, None)
        after["auto"] = {"off": True, "at": now}          # never enrolled again at the new address behind the owner's back
    elif not before.get("key"):
        raise Problem("key", "Paste the gateway key Tico gave you", 422)
    after.pop("check", None)
    _write(c, after)
    H.event(c, who.actor, "gateway.connection.saved", PROVIDER,
            {"key_changed": bool(secret), "destination": destinations.safe(G.base_url(after["url"])),
             "revision": after["revision"]})
    if moved:
        destinations.record(c, who, "gateway", PROVIDER, moved, [LABEL_KEY],
                            destinations.REPLACED if secret else option, after["revision"])
    return _public(before), _public(after)


def clear(c, who, now, settings=None):
    """Forget the saved key. Tico models turn off with it; bots on them stop until a key is saved again."""
    owner_only(who)
    if env_key(settings):
        raise Problem("environment", f"The server's environment sets the gateway key ({ENV_KEY}); remove it there", 409)
    before = _row(c)
    if not before.get("key"):
        return _public(before), _public(before)
    after = {"url": before.get("url", ""), "revision": int(before.get("revision") or 0) + 1, "updated_at": now,
             "updated_by": who.actor}
    for field in ("install", "install_secret", "via"):
        if before.get(field):
            after[field] = before[field]                  # the install stays itself if it connects again
    if before.get("origin") == AUTOMATIC:
        after["auto"] = {"off": True, "at": now}          # cleared on purpose: not enrolled again behind the owner's back
    _write(c, after)
    H.event(c, who.actor, "gateway.connection.cleared", PROVIDER, {"revision": after["revision"]})
    return _public(before), _public(after)


def _public(record):
    return {k: v for k, v in record.items() if k not in ("key", "check", "install_secret")}


def automatic_on(c, who, now):
    """Let the install connect automatically again after the owner cleared its automatic key, or after Tico turned the
    install back on: the next `auto_tick` enrolls with the install's own secret."""
    owner_only(who)
    record = _row(c)
    auto = record.get("auto") if isinstance(record.get("auto"), dict) else {}
    if not auto.get("off") and not auto.get("stopped"):
        return False
    record.pop("auto", None)
    _write(c, record)
    H.event(c, who.actor, "gateway.connection.automatic_on", PROVIDER, {})
    return True


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
                       "spent_usd": result.get("spent_usd"), "cap_usd": result.get("cap_usd"),
                       "at": now, "for": fingerprint}
    _write(c, record)
    return True


def check_now(store, vault, settings=None, opener=None):
    """Ask the Gateway about the key now (outside any transaction: it is a network call) and keep the answer."""
    with store.read() as c:
        secret, base, fingerprint = target(c, vault, settings)
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
        record = _row(c)
        fingerprint = _fingerprint(c, settings) or ("invite:" + _invite_mark(invite(settings)) if invite(settings) else
                                                    COMPANY if _linked(c, settings) else
                                                    "install:" + record["install"] if _identity(record) else "")
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
        try:
            with store.read() as c:
                due = maintained(c, settings)
            if due:
                auto_tick(store, vault, settings, opener)  # a refused automatic key is replaced, or the stop recorded
        except Exception:
            pass
    thread = threading.Thread(target=run, name="gateway-check", daemon=True)
    thread.start()
    return thread


def wanted(config):
    """Whether a run on `config` (a bot's config with its runtime and model filled in) may call the Gateway: its model,
    or the fallback it names itself, is a Tico model, or the company's choice gave it the Gateway as its last fallback
    (`gateway_fallback`, backend/models_preset.py)."""
    config = config if isinstance(config, dict) else {}
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    return is_tico(config.get("model")) or is_tico(fallback.get("model")) or is_tico(config.get("gateway_fallback"))


HANDED = "gateway-attempt:"


def hand_out(c, attempt_id, config):
    """Remember the Tico models the claim handed this attempt out on (its model, and the fallback it names), when it has
    any. The key and the run's outcome follow what the run was given, never a setting changed while it runs."""
    from .repositories import save_metadata
    if not wanted(config):
        return
    fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
    save_metadata(c, HANDED + attempt_id, {"model": str(config.get("model") or ""), "fallback": {
        "harness": str(fallback.get("harness") or ""), "model": str(fallback.get("model") or "")},
        "gateway_fallback": str(config.get("gateway_fallback") or "")})


def handed(c, attempt_id):
    """What `hand_out` remembered for the attempt, or {} (a run that was given no Tico model)."""
    from .repositories import metadata
    return metadata(c, HANDED + attempt_id) if attempt_id else {}


def forget(c, attempt_id):
    c.execute("DELETE FROM registry_metadata WHERE key=?", (HANDED + attempt_id,))


def ran_tico(config, body):
    """Whether the part of a run that ended it (`body`, its completion) ran on a Tico model: the model the runner
    counted last; else, when a fallback harness ran the turn, the fallback the bot names itself, or the Gateway the
    company's choice put last (`gateway_fallback`; any other automatic one is never a Tico model,
    providers.default_fallbacks); else the run's own model."""
    usage = getattr(body, "usage", None)
    segments = (usage.segments or [usage]) if usage else []
    if segments and segments[-1].model:
        return is_tico(segments[-1].model)
    if getattr(body, "fallback", None):
        fallback = config.get("fallback") if isinstance(config.get("fallback"), dict) else {}
        if fallback.get("harness"):
            return fallback.get("harness") == body.fallback and is_tico(fallback.get("model"))
        # The harness the Gateway's own provider row runs on (providers.PROVIDERS), not a CLI name.
        from .providers import PROVIDER_BY_ID
        return body.fallback == PROVIDER_BY_ID[PROVIDER]["harness"] and is_tico(config.get("gateway_fallback"))
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
                            else fallback.get("model") if is_tico(fallback.get("model"))
                            else config.get("gateway_fallback"))
    if items and (bot := bot_header(c, vault, row["bot"])):
        # The relay sends it on every model call of the turn, in place of anything the turn sends by that name.
        items.append({"id": "gateway-bot", "name": G.LABEL + " bot", "env": RUN_BOT_ENV, "kind": "text",
                      "value": bot[G.BOT_HEADER]})
    if items:
        # Which key paid is fixed now: the relay holds it for the turn (execution.run_usage). The runner's shared
        # primary/fallback deadline stays inside HQ's 24-hour rotation grace (runner-rs/src/gateway_relay.rs).
        c.execute("UPDATE attempts SET gateway_key_id=? WHERE id=?", (key_id(c, settings) or None, attempt_id))
        H.event(c, who.actor, "gateway.key_sent", PROVIDER,
                {"bot": row["bot"], "attempt": attempt_id, "runner": who.runner_id})
    return items


def run_credentials(c, vault, settings, model):
    """What a run on `model` gets in its environment from the connection: the key and URL for a Tico model with a key
    set, else nothing. Shaped like `GET credential-runtime`'s items."""
    if not is_tico(model):
        return []
    secret, base, _ = target(c, vault, settings)
    try:
        base = G.safe_base(base)
    except ValueError:
        return []                                     # an environment URL the key may not go to (the page says so)
    if not secret:
        return []
    return [{"id": "gateway-key", "name": G.LABEL, "env": RUN_KEY_ENV, "kind": "api_key", "value": secret},
            {"id": "gateway-url", "name": G.LABEL + " URL", "env": RUN_URL_ENV, "kind": "text",
             "value": G.chat_base(base)}]


def health(c, settings=None):
    """The Health row for the people who manage the connection, or None when there is nothing to say."""
    record = _row(c)
    auto = record.get("auto") if isinstance(record.get("auto"), dict) else {}
    if not env_key(settings) and not record.get("key") and maintained(c, settings) and not auto.get("off"):
        if _stopped(auto, settings):
            return {"status": "bad", "summary": STOPPED}
        if auto.get("code"):
            return {"status": "warn", "summary": AUTO_MESSAGES.get(auto["code"], AUTO_LATER)}
    shown = view(c, settings, manage=True)
    if not shown["connected"] or shown["state"] in ("connected", "unchecked", *G.TRANSIENT):
        return None
    status = "warn" if shown["state"] in ("warning", "not_ready") else "bad"
    return {"status": status, "summary": shown["message"]}


# ----------------------------------------------------------------------------- automatic key per install
def invite(settings=None):
    """The invite code the install command set (`TICO_GATEWAY_INVITE`), or ''. Never typed, shown or logged."""
    return str(getattr(settings, "gateway_invite", "") or os.environ.get(INVITE_ENV, "")).strip()


def _invite_mark(code):
    """Which invite a stop was made under, without keeping it: a new invite in the install command tries again."""
    return hashlib.sha256(f"tico-gateway-invite\n{code}".encode()).hexdigest()[:16]


def _identity(record):
    """Whether the install has its own identity (id and sealed secret) from a first enroll."""
    return bool(record.get("install") and record.get("install_secret"))


def maintained(c, settings=None):
    """Whether the install looks after its own Gateway key: it has an invite to sign up with, a Tico company link that
    gives it one, or the identity a first sign-up gave it (which keeps rotating and enrolling again by its secret with
    no invite; one its company gave it only while linked)."""
    record = _row(c)
    return bool(invite(settings)) or _linked(c, settings) or (_identity(record) and record.get("via") != COMPANY)


def _linked(c, settings=None):
    """Whether this self-run server is linked to its Tico company (backend/company_server.py), which gives it its key."""
    from . import company_server
    return company_server.linked(c, settings)


def company_ended(c):
    """The Tico company link ended (backend/company_server.py `_forget`): HQ revoked the key it gave, so it goes here
    too. The install keeps its id and secret, so linked again it is the same install. A pasted key stays."""
    record = _row(c)
    if record.get("via") != COMPANY or record.get("origin") != AUTOMATIC or not record.get("key"):
        return False
    after = {k: v for k, v in record.items()
             if k not in ("key", "preview", "origin", "key_id", "rotate_after", "check", "auto")}
    after.update(revision=int(record.get("revision") or 0) + 1, updated_at=H.now(), updated_by=SYSTEM)
    _write(c, after)
    H.event(c, SYSTEM, "gateway.connection.company_ended", PROVIDER,
            {"install": record.get("install", ""), "revision": after["revision"]})
    return True


def _stopped(auto, settings):
    """Whether Tico's stop holds: with no invite always; with one, only the stop made under that same invite."""
    mark = auto.get("stopped")
    return bool(mark) and (not invite(settings) or mark == _invite_mark(invite(settings)))


def _utc(value):
    try:
        return datetime.datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def bot_header(c, vault, slug):
    """{X-Tico-Bot: gb_...} for a Gateway call made for bot `slug` with an automatic key, else {}. The id is opaque
    (an HMAC under the install's secret), so the Gateway's ledger can tell bots apart without learning their names."""
    record = _row(c)
    if not slug or not record.get("install_secret") or not getattr(vault, "configured", False):
        return {}
    try:
        secret = vault.cipher.unseal(c, INSTALL_CONTEXT, record["install_secret"])
    except Exception:
        return {}
    return {G.BOT_HEADER: G.bot_id(secret, slug)}


def _due(record, settings, now, linked=False):
    """What `auto_tick` should do with the connection as it is: "enroll", "rotate" or None. `linked`: the server is
    linked to its Tico company (`_linked`)."""
    if env_key(settings) or getattr(settings, "rehearsal", False):
        return None
    if record.get("via") == COMPANY and not linked:
        return None                                       # its company's identity: HQ holds it until it links again
    if not invite(settings) and not _identity(record) and not linked:
        return None                                       # the invite is only needed to make the identity
    auto = record.get("auto") if isinstance(record.get("auto"), dict) else {}
    if auto.get("off") or _stopped(auto, settings):
        return None
    tried = _utc(auto.get("at")) if auto.get("code") else None
    if tried and now - tried < datetime.timedelta(hours=1):
        return None                                       # a failed try waits an hour, however often the page is
                                                          # opened: HQ allows an address 5 enrolls an hour
    if not record.get("key"):
        return "enroll"
    if record.get("origin") != AUTOMATIC:
        return None                                       # the owner's pasted key wins
    check = record.get("check") if isinstance(record.get("check"), dict) else {}
    if check.get("state") in ("revoked", "unknown") and check.get("for") == f"settings:{record.get('revision', 0)}":
        return "enroll"                                   # the Gateway refused this key: enroll again by the secret
    after = _utc(record.get("rotate_after"))
    return "rotate" if after and now >= after else None


def auto_tick(store, vault, settings=None, opener=None, now=None):
    """Enroll or rotate this install's own key when it is due (`_due`); a network call, so outside any transaction.
    Returns what it did ("enrolled", "rotated", "stopped", a refusal code) or None when nothing was due."""
    if not getattr(vault, "configured", False) or not _auto_lock.acquire(blocking=False):
        return None
    try:
        return _auto_tick(store, vault, settings, opener, now or datetime.datetime.now(datetime.timezone.utc))
    finally:
        _auto_lock.release()


def _auto_tick(store, vault, settings, opener, now):
    with store.read() as c:
        record = _row(c)
        linked = _linked(c, settings)
        action = _due(record, settings, now, linked)
        if not action:
            return None
        base = url(c, settings, record)                   # the address this record's key was kept for (`target`)
        current = vault.cipher.unseal(c, CONTEXT, record["key"]) if action == "rotate" else ""
        install = str(record.get("install") or "")
        secret = vault.cipher.unseal(c, INSTALL_CONTEXT, record["install_secret"]) if install and record.get(
            "install_secret") else ""
    try:
        G.safe_base(base)
    except ValueError:
        return None                                       # Settings already says the URL is wrong
    if action == "rotate":
        status, answer, code = G.rotate(base, current, opener=opener)
        if status == 201 and answer.get("key"):
            return _keep(store, vault, record, answer, now, install, secret, "rotated")
        if code not in ENROLL_AGAIN:
            return _note(store, record, code or "unreachable", now)
    if not install or not secret:
        install, secret = "gi_" + secrets.token_hex(12), "gis_" + secrets.token_urlsafe(32)
        # Kept before HQ is asked: if HQ enrolls it but the answer is lost, the next try is the same install.
        with store.transaction() as c:
            if not _unchanged(c, record):
                return None
            record = {**_row(c), "install": install, "install_secret": vault.cipher.seal(c, INSTALL_CONTEXT, secret),
                      **({"via": COMPANY} if linked else {})}
            _write(c, record)
    if linked:
        # The company's key, billed to its credit: HQ enrolls this install for it (backend/company_server.py).
        from . import company_server
        status, answer, code = company_server.gateway_key(store, vault, install, secret)
    else:
        status, answer, code = G.enroll(base, install, secret, invite(settings), opener=opener)
    if status == 201 and answer.get("key"):
        return _keep(store, vault, record, answer, now, install, secret, "enrolled", COMPANY if linked else "")
    if code == G.INSTALL_REVOKED:
        return _note(store, record, code, now, stopped=_invite_mark(invite(settings)))
    if code == "already_enrolled":
        record = {**record, "install": "", "install_secret": ""}   # another install has this id: the next try makes a new one
    return _note(store, record, code or "unreachable", now)


def _stamp(now):
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _unchanged(c, record):
    """Whether the connection is as `record` was when the network call started: an owner who pasted or cleared a key
    meanwhile wins."""
    return int(_row(c).get("revision") or 0) == int(record.get("revision") or 0)


def _keep(store, vault, record, answer, now, install, secret, what, via=""):
    key_value = _clean_key(answer["key"])
    with store.transaction() as c:
        if not _unchanged(c, record):
            return None
        after = {**{k: v for k, v in record.items() if k not in ("check", "auto")},
                 "key": vault.cipher.seal(c, CONTEXT, key_value),
                 "preview": key_value[:3] + "…" + key_value[-3:],
                 "install": install, "install_secret": vault.cipher.seal(c, INSTALL_CONTEXT, secret),
                 "origin": AUTOMATIC, "key_id": str(answer.get("key_id") or ""),
                 "rotate_after": str(answer.get("rotate_after") or ""),
                 "revision": int(record.get("revision") or 0) + 1, "updated_at": _stamp(now), "updated_by": SYSTEM,
                 **({"via": via} if via else {})}
        _write(c, after)
        H.event(c, SYSTEM, f"gateway.connection.{what}", PROVIDER,
                {"install": install, "key_id": after["key_id"], "revision": after["revision"]})
    check_later(store, vault, getattr(store, "settings", None))
    # A Tico Cloud cell's new key may be what HQ needs to answer about its access (backend/cloud_access.py): ask now.
    from . import cloud_access
    if cloud_access.cell():
        cloud_access.sync_later(store, vault, getattr(store, "settings", None))
    return what


def _note(store, record, code, now, stopped=""):
    """Keep why the last try did not work, for Health and the hour's wait. `stopped`: Tico turned the install off; its
    key is dropped and nothing is asked again under this invite (or, with none, at all)."""
    with store.transaction() as c:
        if not _unchanged(c, record):
            return None
        after = dict(record)
        if stopped:
            for field in ("key", "preview", "origin", "key_id", "rotate_after", "check"):
                after.pop(field, None)
            after["revision"] = int(record.get("revision") or 0) + 1
            after["auto"] = {"stopped": stopped, "code": code, "at": _stamp(now)}
            H.event(c, SYSTEM, "gateway.connection.stopped", PROVIDER, {"install": record.get("install", "")})
        else:
            after["auto"] = {"code": code, "at": _stamp(now)}
        _write(c, after)
    return "stopped" if stopped else code
