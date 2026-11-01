"""A synced bot answered through its platform's desktop app on one of its person's computers.

A Grok Bot lives in Grok and reaches Tico only through a daily sync (backend/external_sync.py). Its person may
choose one of their own computers whose runner drives the Grok Mac app: `config_json.app_delivery =
{"computer": <runner id>, "app": "grok"}`, set through the bot's definition (settings_admin.update_bot). While
that computer is registered:

- a message to the bot in its chat queues a job for that computer like any bot's (store.EXTERNAL_BOT_SQL), and
  the claim carries `app_delivery` and the bot's identity in the app (`app_bot`) in its config; the sync's inbox
  never hands the same message over as well (external_sync.take_inbox);
- the computer reports what the app's conversation shows (`POST /api/v2/runners/me/app-delivery/{slug}`): the
  bot's new words become its messages here, what the person typed in the app itself is copied in as theirs and
  read, and a question the bot needs its person for is an ask in their Needs you;
- what goes wrong with the app is a Health finding on that computer (`health_problems`), until a clean read clears
  it (the listing says which bots' apps have one, so the computer posts an empty clean report for them).

A message is matched to one Tico already holds by sender, words and the time it was said (the app's own `at`, kept as
`refs.app.at`, or when Tico wrote it), within ECHO_HOURS either way: so the daily sync and the app's history meet in the
middle in either order. The 4.2 read contract has no needs-a-person marker on a message, so the watch reports the
bot's words as ordinary messages; a question the app needs its person for reaches Needs you through a send's
`needs_human` error, which becomes one ask whichever of the turn's reply and the error report arrives first.

Unset, cleared, or with the computer revoked, the bot is sync-only exactly as before.
"""

import json
from datetime import timezone

from fastapi import Request

from . import models as M
from . import rooms
from .harnesses import resolve_harness
from .store import H, Problem, encode, readiness_document

# The desktop apps a runner drives, and the synced harness each one answers for.
APPS = {"grok": {"label": "Grok", "harness": "grokbot", "provider": "grok"}}
HARNESS_APP = {meta["harness"]: app for app, meta in APPS.items()}
SEEN_KEYS = 1000            # message keys remembered per bot: far more than one conversation page
ECHO_HOURS = 24             # a reported message that matches what Tico already holds within this window is that one
SEEN = "app-delivery-seen:"         # registry_metadata key per bot: {"after": key, "keys": [...]}
ERRORS = "app-delivery-errors:"     # registry_metadata key per computer: {app: {code, detail, bot, at}}
# What a report's error says about the app itself; the computer's next readiness report saying it is ready clears it.
APP_STATE_CODES = {"no_permission", "not_signed_in", "needs_foreground", "ui_changed", "not_installed", "not_running"}
WORDS = {"no_permission": "Tico is not allowed to control it (System Settings > Privacy & Security)",
         "not_signed_in": "not signed in", "needs_foreground": "it needs to be in front",
         "ui_changed": "its screens changed, so Tico cannot read them", "not_installed": "not installed",
         "not_running": "not running", "no_driver": "the app driver is not installed",
         "bot_not_found": "the bot was not found in the app", "timeout": "it did not answer in time",
         "send_failed": "a message could not be sent", "busy": "busy", "error": "not working"}


def setting(config):
    """The bot's {"computer", "app"}, or None."""
    value = (config or {}).get("app_delivery")
    if isinstance(value, dict) and value.get("computer") and value.get("app") in APPS:
        return {"computer": str(value["computer"]), "app": value["app"]}
    return None


def _config(c, bot):
    row = c.execute("SELECT config,operator FROM bot_config WHERE bot=?", (bot,)).fetchone()
    return (H._json(row["config"], {}) or {}, row["operator"]) if row else ({}, None)


def active(c, config):
    """The registered computer the bot is delivered through, or None: the same rule the queue trigger applies."""
    found = setting(config)
    if not found:
        return None
    return c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (found["computer"],)).fetchone()


def view(c, config):
    """What the bot's pages show: {"computer", "app", "app_label", "computer_label", "active"}, or None."""
    found = setting(config)
    if not found:
        return None
    row = c.execute("SELECT label,revoked_at FROM runners WHERE id=?", (found["computer"],)).fetchone()
    return {**found, "app_label": APPS[found["app"]]["label"], "computer_label": row["label"] if row else "",
            "active": bool(row and not row["revoked_at"])}


def app_report(runner, app):
    """The computer's latest readiness for `app` (models.AppDriverApp as stored), or None when it reports none."""
    driver = readiness_document(runner["readiness"]).get("appdriver")
    apps = driver.get("apps") if isinstance(driver, dict) else None
    report = apps.get(app) if isinstance(apps, dict) else None
    return report if isinstance(report, dict) else None


def _not_ready(report):
    """Why the app is not ready by its readiness report, or '' when it is."""
    if report is None:
        return "this computer does not report its desktop apps; update Tico there"
    if report.get("ready") is True:
        return ""
    return str(report.get("detail") or "")[:300] or WORDS.get(report.get("state"), WORDS["error"])


def change(c, who, auth, bot, value, declared, operator):
    """Apply a definition's `app_delivery` to `declared` (the bot's config, saved by the caller). Only the person who
    synced the bot chooses a computer, and only one of their own whose app is ready; they or an admin turn it off."""
    harness = resolve_harness(declared)
    current = setting(declared)
    person = "human:" + str(operator or "")
    if value is None:
        if current:
            if who.actor != person and not (who.role == "owner" or auth.bot_admin(who)):
                raise Problem("app_delivery", "Only the person who synced this bot, or an admin, can turn off its "
                              "delivery through the app", 422)
            turn_off(c, who.actor, bot, declared, "turned off")
        return
    meta = APPS[value.app]
    if harness != meta["harness"]:
        raise Problem("app_delivery", f"Only a {meta['label']} Bot is delivered through the {meta['label']} app", 422)
    if who.actor != person:
        raise Problem("app_delivery", f"Only the person who synced this {meta['label']} Bot can choose where it is "
                      "delivered", 422)
    runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (value.computer,)).fetchone()
    if not runner or runner["operator"] != operator:
        raise Problem("app_delivery", f"Choose one of your own computers to deliver this {meta['label']} Bot", 422)
    why = _not_ready(app_report(runner, value.app))
    if why:
        raise Problem("app_delivery", f"{meta['label']} isn't ready on {runner['label']}: {why}", 422)
    declared["app_delivery"] = {"computer": runner["id"], "app": value.app}
    old = c.execute("SELECT * FROM assignments WHERE bot=?", (bot,)).fetchone()
    if not old or old["runner_id"] != runner["id"]:
        # The computer's runner takes the bot's jobs from its assignment, as any bot's (Execution.candidate).
        generation = (old["generation"] if old else 0) + 1
        c.execute("INSERT INTO assignments VALUES(?,?,?,?,?) ON CONFLICT(bot) DO UPDATE SET runner_id=excluded.runner_id,"
                  "generation=excluded.generation,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                  (bot, runner["id"], generation, H.now(), who.actor))
        H.event(c, who.actor, "bot.assigned", bot, {"runner": runner["id"], "generation": generation})
    H.event(c, who.actor, "bot.app_delivery_on", bot, {"runner": runner["id"], "app": value.app})


def turn_off(c, actor, bot, declared, reason):
    """Back to the daily sync: off the computer, and its waiting messages handed to the sync's inbox again."""
    found = setting(declared)
    declared.pop("app_delivery", None)
    if found:
        c.execute("DELETE FROM assignments WHERE bot=? AND runner_id=?", (bot, found["computer"]))
    # A message no runner took goes to the sync instead (external_sync.take_inbox); one a runner took stays its.
    c.execute("UPDATE jobs SET state='cancelled' WHERE bot=? AND state='queued'", (bot,))
    H.event(c, actor, "bot.app_delivery_off", bot, {"runner": (found or {}).get("computer"), "reason": reason})


def runner_revoked(c, actor, runner_id):
    """A removed computer delivers nothing: each bot delivered through it goes back to the daily sync."""
    for row in c.execute("SELECT bot,config FROM bot_config WHERE json_extract(config,'$.app_delivery.computer')=?",
                         (runner_id,)).fetchall():
        declared = H._json(row["config"], {}) or {}
        turn_off(c, actor, row["bot"], declared, "computer removed")
        c.execute("UPDATE bot_config SET config=?,revision=revision+1 WHERE bot=?", (encode(declared), row["bot"]))
    c.execute("DELETE FROM registry_metadata WHERE key=?", (ERRORS + runner_id,))


def app_bot(config):
    """The bot as the app knows it, {"id", "name"}: the id in grok.com/bot/<id> and its name there, as synced."""
    found = setting(config)
    if not found or resolve_harness(config) != APPS[found["app"]]["harness"]:
        return None
    record = config.get(APPS[found["app"]]["provider"]) or {}
    return {"id": str(record.get("id") or ""), "name": str(record.get("name") or config.get("display_name") or "")}


def job_config(config):
    """A claimed or assigned bot's config with `app_bot` beside its `app_delivery`, for the runner."""
    bot = app_bot(config)
    return {**config, "app_bot": bot} if bot else config


# ----------------------------------------------------------------------------- what the app shows
def _norm(text):
    return " ".join(str(text or "").split())


def said_at(value, fallback):
    """The app's `at` for a message in Tico's own timestamp format (UTC), or `fallback` when it has none or it can't
    be read."""
    at = H.parse_ts(value) if value else None
    if at is None:
        return fallback
    at = at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# When a message was said: the app's own time for one the app reported, else when Tico wrote it.
SAID_SQL = "COALESCE(json_extract(refs,'$.app.at'),created_at)"


def echoes(c, room_id, sender, text, any_source=True, at=None):
    """True when the room already holds `text` from `sender` said within ECHO_HOURS of `at`: the reply a runner turn
    posted, or the person's message Tico delivered. A message the app itself reported counts only with `any_source`
    false (and by the app's own time for it), so the app saying the same words twice is two messages."""
    at = at or H.now()
    # A message the app reported has its key; a `needs_human` question has `app` without one, so it is both what Tico
    # holds (the watch reading the same words doesn't post them again) and what the app brought (the turn's reply
    # and the sync don't either).
    which = "json_extract(refs,'$.app.key') IS NULL" if any_source else "json_extract(refs,'$.app') IS NOT NULL"
    sql = (f"SELECT body FROM messages WHERE conversation_id=? AND from_actor=? AND {SAID_SQL} BETWEEN ? AND ? "
           f"AND deleted_at IS NULL AND {which}")
    wanted = _norm(text)
    return any(_norm(row[0]) == wanted for row in c.execute(
        sql, (room_id, sender, H.shift(at, hours=-ECHO_HOURS), H.shift(at, hours=ECHO_HOURS))))


def _seen(c, bot):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (SEEN + bot,)).fetchone()
    value = H._json(row[0], {}) if row else {}
    value = value if isinstance(value, dict) else {}
    return {"after": value.get("after"), "keys": [k for k in value.get("keys") or [] if isinstance(k, str)]}


def _save(c, key, value):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, json.dumps(value)))


def _errors(c, runner_id):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (ERRORS + runner_id,)).fetchone()
    value = H._json(row[0], {}) if row else {}
    return value if isinstance(value, dict) else {}


def _ask(c, actor, person, room, text, refs):
    """The bot's question to its person: an ask in its chat, which their Needs you lists (H.loose_asks_to). The same
    open question is not asked twice, and the same words the turn already posted as its reply (a send's `needs_human`
    completes the turn with them) become that question rather than a second message."""
    for row in c.execute(f"SELECT m.body FROM messages m WHERE m.conversation_id=? AND m.from_actor=? AND m.to_actor=? "
                         f"AND m.kind='ask' AND m.deleted_at IS NULL AND {H.OPEN_ASK_SQL}", (room["id"], actor, person)):
        if _norm(row[0]) == _norm(text):
            return False
    reply = next((row for row in c.execute(
        "SELECT id,body,refs FROM messages WHERE conversation_id=? AND from_actor=? AND to_actor=? AND kind='say' "
        "AND deleted_at IS NULL AND json_extract(refs,'$.app') IS NULL AND created_at>=? ORDER BY created_at DESC",
        (room["id"], actor, person, H.shift(H.now(), hours=-ECHO_HOURS))) if _norm(row["body"]) == _norm(text)), None)
    if reply:
        c.execute("UPDATE messages SET kind='ask',refs=? WHERE id=?",
                  (encode({**(H._json(reply["refs"], {}) or {}), **refs, "depth": 1}), reply["id"]))
        H.event(c, actor, "app_delivery.asked", reply["id"], {"to": person, "conversation": room["id"]})
        return False
    H._write_message(c, actor, person, text, room, "ask", {**refs, "depth": 1}, None, None)
    return True


def delivered(c, runner_id):
    """The bots delivered through an app on this computer, for GET /api/v2/runners/me/app-delivery. `error`: this
    computer's Health holds an error for the bot's app, which the next clean read clears."""
    out, errors = [], _errors(c, runner_id)
    for row in c.execute("SELECT bc.bot,bc.config FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                         "WHERE b.state<>'archived' AND json_extract(bc.config,'$.app_delivery.computer')=? "
                         "ORDER BY bc.bot", (runner_id,)):
        config = H._json(row["config"], {}) or {}
        bot = app_bot(config)
        if bot:
            app = setting(config)["app"]
            out.append({"slug": row["bot"], "app": app, "bot": bot, "after": _seen(c, row["bot"])["after"],
                        "error": app in errors})
    return out


def report(c, runner_id, slug, body):
    """Record what the app shows for `slug`, a bot delivered through this computer. {"after", "posted"}."""
    config, operator = _config(c, slug)
    found = setting(config)
    if not found or found["computer"] != runner_id or not app_bot(config):
        raise Problem("not_found", "That bot is not delivered through an app on this computer", 404)
    if body.app != found["app"]:
        raise Problem("app", f"This bot is delivered through {APPS[found['app']]['label']}, not {body.app}", 422)
    label = APPS[found["app"]]["label"]
    actor, person = "bot:" + slug, "human:" + operator
    room = rooms.personal_room(c, person, slug, subject=label + " Bot")
    seen = _seen(c, slug)
    known, keys, after, posted, now = set(seen["keys"]), list(seen["keys"]), seen["after"], 0, H.now()
    for msg in body.messages:
        after = msg.key
        # The newest SEEN_KEYS keys answer most reports; an older key the computer sends again (a lost cursor) is
        # found on the message it made.
        if msg.key in known or c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND "
                                         "json_extract(refs,'$.app.key')=?", (room["id"], msg.key)).fetchone():
            continue
        known.add(msg.key)
        keys.append(msg.key)
        at = said_at(msg.at, now)
        refs = {"app": {"app": found["app"], "key": msg.key, "at": at}}
        if msg.role == "bot":
            if msg.needs_human:
                posted += _ask(c, actor, person, room, msg.text, refs)
            elif not echoes(c, room["id"], actor, msg.text, at=at):
                # The bot's own words, unread for its person like any bot's reply.
                H._write_message(c, actor, person, msg.text, room, "say", refs, None, None)
                posted += 1
        elif not echoes(c, room["id"], person, msg.text, at=at):
            # Typed in the app itself: the person's own words, already read, and no job (`quiet`).
            H._write_message(c, person, actor, msg.text, room, "say", {**refs, "quiet": True}, None, None,
                             delivered_at=now, read_at=now)
            posted += 1
    errors = _errors(c, runner_id)
    if body.error and body.error.code == "needs_human":
        text = body.error.text or body.error.detail or f"{label} needs you to do something in the app on this computer"
        posted += _ask(c, actor, person, room, text, {"app": {"app": found["app"], "error": "needs_human"}})
    elif body.error:
        errors[found["app"]] = {"code": body.error.code, "detail": body.error.detail, "bot": slug, "at": now}
    else:
        errors.pop(found["app"], None)
    _save(c, ERRORS + runner_id, errors)
    _save(c, SEEN + slug, {"after": after, "keys": keys[-SEEN_KEYS:]})
    if posted:
        H.event(c, actor, "app_delivery.reported", slug, {"runner": runner_id, "posted": posted})
    return {"after": after, "posted": posted}


def health_problems(c, who, auth, online_ids):
    """[(check id, summary)] for each online computer whose app does not work for the bots delivered through it: its
    latest report's error until a clean report (or, for the app's own state, a ready readiness report after it), else
    its readiness for the app. An administrator sees every computer; anyone else their own."""
    full = who.role == "owner" or auth.bot_admin(who)
    used = {}
    for row in c.execute("SELECT bot,config FROM bot_config WHERE json_extract(config,'$.app_delivery.computer') "
                         "IS NOT NULL"):
        found = setting(H._json(row["config"], {}) or {})
        if found and found["computer"] in online_ids:
            used.setdefault((found["computer"], found["app"]), []).append(row["bot"])
    out = []
    for (runner_id, app), bots in sorted(used.items()):
        runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (runner_id,)).fetchone()
        if not runner or not (full or who.actor == "human:" + runner["operator"]):
            continue
        state = app_report(runner, app)
        error = _errors(c, runner_id).get(app)
        cleared = (error and error.get("code") in APP_STATE_CODES and (state or {}).get("ready") is True
                   and (runner["last_seen_at"] or "") > (error.get("at") or ""))
        if error and not cleared:
            why = WORDS.get(error.get("code"), WORDS["error"]) + (f" ({error['detail']})" if error.get("detail") else "")
        else:
            why = _not_ready(state)
        if why:
            label = APPS[app]["label"]
            out.append((f"appdriver:{runner_id}:{app}",
                        f"{label} on {runner['label']}: {why}. Messages to {', '.join(bots[:3])} wait until it works."))
    return out


def install(app, store, execution, mutate):
    @app.get("/api/v2/runners/me/app-delivery")
    def listing(request: Request):
        """The bots this computer delivers through a desktop app, each with the newest message key it reported."""
        who = request.state.identity
        with store.read() as c:
            if not execution.runner(c, who):
                raise Problem("identity", "A registered runner is required", 403)
            return {"bots": delivered(c, who.runner_id)}

    @app.post("/api/v2/runners/me/app-delivery/{slug}")
    def reported(request: Request, slug: str, body: M.AppDeliveryReport):
        """Record what the app shows in `slug`'s conversation; each message key is taken once."""
        who = request.state.identity

        def work(c):
            if not execution.runner(c, who):
                raise Problem("identity", "A registered runner is required", 403)
            return report(c, who.runner_id, slug, body)
        return mutate(request, body, work)
