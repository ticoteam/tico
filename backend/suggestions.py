"""Gateway suggestions, kept locally until a fresh condition check at Update delivery."""

import json
import math
import operator
import re

import httpx

from clients import gateway as G
from . import gateway_connection, postal_mail, usage_limits
from .store import H, Problem

KEY = "suggestions-settings"
LAST_FETCH = "suggestions-last-fetch"
DEFAULTS = {"share_stats_for_suggestions": False, "report_suggestion_outcomes": True}
TRANSPORT = None                         # Tests supply httpx.MockTransport; there is no retry or model call.
TIMEOUT = 15
MAX_BYTES = 1024 * 1024
MAX_ITEMS = 100
DELIVERY_MAX = 10
ID = re.compile(r"sg_[0-9a-f]{32}\Z")
FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}\Z")
ROLES = {"botops": ("botops",), "goal_manager": ("goal-manager",),
         "engineering_lead": ("engineering-lead",)}
OUTCOMES = {"delivered", "acted", "dismissed", "expired", "condition_false"}
# This is the whole outbound dictionary. New local facts cannot silently become outbound fields.
COUNTS = {"bots_total", "bots_botops", "bots_goal_manager", "bots_engineering_lead", "bots_finance_bot",
          "tasks_open", "tasks_stuck", "automations_count"}
NUMBERS = {"spend_month_usd", "spend_month_cap_usd"}
FLAGS = {"source_github_connected", "source_slack_connected", "source_email_connected", "source_finance_connected"}
FIELDS = COUNTS | NUMBERS | FLAGS | {"plan_tier", "snapshot_version"}
MAX_METRIC = 1_000_000_000
TYPES = {**dict.fromkeys(COUNTS, "integer"), **dict.fromkeys(NUMBERS, "number"),
         **dict.fromkeys(FLAGS, "boolean"), "plan_tier": "string", "snapshot_version": "integer"}
COMPARISONS = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt, "gte": operator.ge,
               "lt": operator.lt, "lte": operator.le}


def available(c, settings):
    """Hosted installs or installs connected to Tico Gateway; a plain open-source install cannot opt in."""
    return bool(settings.hosted or gateway_connection.connected(c, settings))


def privacy(c, settings):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    saved = json.loads(row[0]) if row else {}
    out = {name: saved.get(name, default) is True for name, default in DEFAULTS.items()}
    allowed = available(c, settings) and not (settings.demo or settings.rehearsal)
    out["share_stats_for_suggestions"] &= allowed
    return {**out, "available": allowed}


def save_privacy(c, settings, actor, changes):
    before = privacy(c, settings)
    if changes.get("share_stats_for_suggestions") and not before["available"]:
        raise Problem("validation", "Needs Tico Gateway", 422)
    after = {name: changes.get(name, before[name]) for name in DEFAULTS}
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, json.dumps(after)))
    H.event(c, actor, "suggestions.privacy", "", after)
    return privacy(c, settings)


def _bots(c):
    return c.execute("SELECT b.id,s.template FROM bots b LEFT JOIN bot_settings s ON s.bot_id=b.id "
                     "WHERE b.state='active' ORDER BY b.id").fetchall()


def snapshot(c, settings, local=False):
    """Counts sent to the Gateway. With local=True, also the company pool's monthly spend and cap, which only
    condition checks on this install read: spend never leaves it. Other facts stay unknown."""
    bots = _bots(c)
    out = {"snapshot_version": 1, "bots_total": len(bots)}
    for role, templates in {**ROLES, "finance_bot": postal_mail.FINANCE_TEMPLATES}.items():
        out["bots_" + role] = sum(row["template"] in templates for row in bots)
    out["tasks_open"] = c.execute("SELECT count(*) FROM tasks WHERE status IN "
                                   "('open','doing','waiting','review','ready')").fetchone()[0]
    out["automations_count"] = c.execute("SELECT count(*) FROM automations WHERE enabled=1 AND builtin=0 "
                                         "AND deleted_at IS NULL").fetchone()[0]
    money = usage_limits.pool_state(c, usage_limits.COMPANY) if local else {"monthly_usd": None}
    if money["monthly_usd"]:
        out["spend_month_usd"] = money["month_spent"]
        out["spend_month_cap_usd"] = money["monthly_usd"]
    return {field: value for field, value in out.items() if _metric(field, value)}


def _metric(field, value):
    """The HQ v1 dictionary's exact types, ranges and enums (gateway/suggestions.py)."""
    if field == "snapshot_version":
        return type(value) is int and value == 1
    if field in COUNTS:
        return type(value) is int and 0 <= value <= MAX_METRIC
    if field in NUMBERS:
        return type(value) in (int, float) and 0 <= value <= MAX_METRIC and math.isfinite(value)
    if field in FLAGS:
        return type(value) is bool
    return isinstance(value, str) and value in ("trial", "starter", "self_hosted", "unknown")


def _condition(condition):
    if not isinstance(condition, dict) or set(condition) != {"all"} or not isinstance(condition["all"], list):
        return False
    if not 1 <= len(condition["all"]) <= len(FIELDS):
        return False
    for test in condition["all"]:
        if not isinstance(test, dict) or set(test) not in ({"field", "op", "value"}, {"field", "op", "other_field"}):
            return False
        if not isinstance(test["field"], str) or test["field"] not in FIELDS:
            return False
        if not isinstance(test["op"], str) or test["op"] not in COMPARISONS:
            return False
        if test["op"] not in ("eq", "ne") and TYPES[test["field"]] not in ("integer", "number"):
            return False
        if "other_field" in test:
            if not isinstance(test["other_field"], str) or test["other_field"] not in FIELDS:
                return False
            types = {TYPES[test["field"]], TYPES[test["other_field"]]}
            if len(types) != 1 and not types <= {"integer", "number"}:
                return False
        elif not _metric(test["field"], test["value"]):
            return False
    return True


def matches(condition, facts):
    """HQ's evaluator on validated metrics, including its typed, bounded conditions and missing-operand rule."""
    if not _condition(condition):
        raise ValueError("Invalid suggestion condition")
    for test in condition["all"]:
        if test["field"] not in facts or "other_field" in test and test["other_field"] not in facts:
            return False
        right = test["value"] if "value" in test else facts[test["other_field"]]
        if not COMPARISONS[test["op"]](facts[test["field"]], right):
            return False
    return True


def target(store):
    """Prepare the vault outside the writer, then resolve the key and address together before HTTP."""
    from .credentials import SEALED, Vault
    if store.settings.demo or store.settings.rehearsal:
        return "", ""
    keys = Vault(store)
    with store.read() as c:
        sealed = gateway_connection._row(c).get("key", "")
    if not gateway_connection.env_key(store.settings) and sealed.startswith(SEALED):
        keys.cipher.prepare(store)
    with store.transaction() as c:
        if (not gateway_connection.env_key(store.settings) and keys.cipher.cached is None
                and gateway_connection._row(c).get("key", "").startswith(SEALED)):
            raise GatewayError("Gateway key is not ready")
        secret, base, _ = gateway_connection.target(c, keys, store.settings)
    base = G.safe_base(base) if secret else base
    # Suggest is a free service at /v1/suggestions, beside the model routes' /v1/gateway prefix.
    if base.endswith("/v1/gateway"):
        base = base[:-len("/v1/gateway")]
    return secret, base


class GatewayError(Exception):
    pass


def _post(secret, base, path, payload, *, decode=True):
    """A bounded response and fixed error messages keep remote text and bearer keys out of step details."""
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT, trust_env=False, follow_redirects=False) as client:
            with client.stream("POST", base + path, headers={"Authorization": "Bearer " + secret, "User-Agent": G.user_agent()}, json=payload) as reply:
                if not 200 <= reply.status_code < 300:
                    raise GatewayError(f"Gateway returned HTTP {reply.status_code}")
                data = bytearray()
                for part in reply.iter_bytes():
                    data.extend(part)
                    if len(data) > MAX_BYTES:
                        raise GatewayError("Gateway response is too large")
                return json.loads(data) if decode and data else None
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        raise GatewayError("Gateway request failed (" + type(exc).__name__ + ")") from None


def _items(reply):
    if (not isinstance(reply, dict) or type(reply.get("version")) is not int or reply["version"] != 1
            or not isinstance(reply.get("suggestions"), list)):
        raise GatewayError("Invalid suggestions response")
    items = reply["suggestions"]
    if len(items) > MAX_ITEMS:
        raise GatewayError("Too many suggestions")
    for item in items:
        if (not isinstance(item, dict) or not ID.fullmatch(str(item.get("id", "")))
                or not FINGERPRINT.fullmatch(str(item.get("fingerprint", "")))
                or item.get("owner_role") not in (*ROLES, "finance_bot", "person_money_cap")
                or not all(isinstance(item.get(k), str) and len(item[k]) <= limit
                           for k, limit in (("title", 500), ("body", 8000)))
                or type(item.get("cooldown_days")) is not int or not 1 <= item["cooldown_days"] <= 36500
                or not _condition(item.get("condition"))):
            raise GatewayError("Invalid suggestion")
    return items


def fetch(store, destination=None):
    with store.read() as c:
        if not privacy(c, store.settings)["share_stats_for_suggestions"]:
            return None
        facts = snapshot(c, store.settings)
    secret, base = destination or target(store)
    if not secret:
        raise GatewayError("Needs Tico Gateway")
    with store.read() as c:
        controls = privacy(c, store.settings)
        if not all(controls[name] for name in DEFAULTS):
            return None
    return _items(_post(secret, base, "/v1/suggestions", facts))


def store_new(c, items, now=None):
    """One row per fingerprint. A renewed fingerprint starts a new receipt only after its cooldown and reporting."""
    now = now or H.now()
    record = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    reports_on = json.loads(record[0]).get("report_suggestion_outcomes", True) if record else True
    count = 0
    for item in items:
        old = c.execute("SELECT * FROM suggestions WHERE fingerprint=?", (item["fingerprint"],)).fetchone()
        if old and (old["status"] == "new" or (old["cooldown_until"] or "") > now
                    or reports_on and old["outcome"] and not old["outcome_reported_at"]):
            continue
        if c.execute("SELECT 1 FROM suggestions WHERE id=? AND fingerprint<>?",
                     (item["id"], item["fingerprint"])).fetchone():
            continue
        if old:
            c.execute("DELETE FROM suggestions WHERE fingerprint=?", (item["fingerprint"],))
        c.execute("INSERT OR IGNORE INTO suggestions(id,fingerprint,owner_role,title,body,condition,cooldown_days,"
                  "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (item["id"], item["fingerprint"], item["owner_role"], item["title"], item["body"],
                   json.dumps(item["condition"]), item["cooldown_days"], now, now))
        count += c.execute("SELECT changes()").fetchone()[0]
    return count


def owner(c, role):
    if role == "finance_bot":
        return postal_mail.finance_bot(c)
    templates = ROLES.get(role, ())
    return next((row["id"] for row in _bots(c) if row["template"] in templates), None)


def _outcome(c, row, outcome, now):
    c.execute("UPDATE suggestions SET status=?,outcome=?,outcome_reported_at=NULL,updated_at=?,"
              "cooldown_until=coalesce(cooldown_until,?) WHERE id=?",
              ("sent" if outcome == "delivered" else outcome, outcome, now,
               H.shift(now, days=row["cooldown_days"]), row["id"]))


def pending(c, bot, batch_id=None):
    """Re-check conditions and role ownership, including the exact bound batch on a retry."""
    bound = batch_id and c.execute("SELECT 1 FROM suggestions WHERE owner_bot_id=? AND update_batch_id=? LIMIT 1",
                                   (bot, batch_id)).fetchone()
    rows = (c.execute("SELECT * FROM suggestions WHERE owner_bot_id=? AND update_batch_id=? "
                      "AND status='sent' AND outcome IS NULL ORDER BY created_at,id", (bot, batch_id)).fetchall()
            if bound else c.execute("SELECT * FROM suggestions WHERE status='new' ORDER BY created_at,id").fetchall())
    # Planning and sending both call this inside a write transaction.
    facts, now, out = snapshot(c, None, local=True), H.now(), []
    for row in rows:
        owns = owner(c, row["owner_role"]) == bot
        if not bound and not owns:
            continue
        if not matches(json.loads(row["condition"]), facts):
            _outcome(c, row, "condition_false", now)
            continue
        if not owns:
            c.execute("UPDATE suggestions SET status='new',owner_bot_id=NULL,update_batch_id=NULL,message_id=NULL,"
                      "delivered_at=NULL,cooldown_until=NULL,updated_at=? WHERE id=?", (now, row["id"]))
            continue
        out.append(dict(row))
        if not bound and len(out) == DELIVERY_MAX:
            break
    return out[:DELIVERY_MAX]


def mark_sent(c, bot, batch_id, message_id, items):
    now = H.now()
    for row in items[:DELIVERY_MAX]:
        c.execute("UPDATE suggestions SET status='sent',owner_bot_id=?,update_batch_id=?,message_id=?,delivered_at=?,"
                  "cooldown_until=?,updated_at=? WHERE id=? AND status='new'",
                  (bot, batch_id, message_id, now, H.shift(now, days=row["cooldown_days"]), now, row["id"]))


def feedback(c, bot, batch_id, acted=(), settled=False):
    """Only the receiving bot and bound request can acknowledge an id; absence is delivery, never action."""
    now = H.now()
    for row in c.execute("SELECT * FROM suggestions WHERE owner_bot_id=? AND update_batch_id=? AND status='sent'",
                          (bot, batch_id)).fetchall():
        if row["id"] in acted:
            _outcome(c, row, "acted", now)
        elif settled and row["outcome"] is None:
            _outcome(c, row, "delivered", now)


def settle_delivery(c, bot, batch_id):
    """Only completion of the original delivery message settles an unreported suggestion."""
    now = H.now()
    for row in c.execute("SELECT s.* FROM suggestions s JOIN jobs j "
                          "ON j.message_id=s.message_id AND j.bot_id=s.owner_bot_id "
                          "WHERE s.owner_bot_id=? AND s.update_batch_id=? AND s.status='sent' "
                          "AND s.outcome IS NULL AND j.state='completed'", (bot, batch_id)).fetchall():
        _outcome(c, row, "delivered", now)


def expire(c, now=None):
    now = now or H.now()
    for row in c.execute("SELECT * FROM suggestions WHERE status='sent' AND outcome IS NULL AND cooldown_until<=?",
                          (now,)).fetchall():
        _outcome(c, row, "expired", now)


def deliver_people(c, settings):
    """Use spend alerts' company-owner recipient and keeper notice path; never pick an arbitrary person."""
    person = usage_limits.recipients(c, None, settings.owner_email)
    if not person:
        return 0
    facts, now, sent = snapshot(c, settings, local=True), H.now(), 0
    if not all(field in facts for field in NUMBERS) or facts["spend_month_usd"] <= facts["spend_month_cap_usd"]:
        return 0
    for row in c.execute("SELECT * FROM suggestions WHERE status='new' AND owner_role='person_money_cap' "
                          "ORDER BY created_at,id LIMIT ?", (DELIVERY_MAX,)).fetchall():
        if not matches(json.loads(row["condition"]), facts):
            _outcome(c, row, "condition_false", now)
            continue
        H.say(c, H.KEEPER, H.human_actor(person["id"]), row["title"] + "\n\n" + row["body"], kind="notice")
        c.execute("UPDATE suggestions SET delivered_at=? WHERE id=?", (now, row["id"]))
        _outcome(c, row, "delivered", now)
        sent += 1
    return sent


def report(store):
    """Send at most one bounded batch. Stamp only the exact outcome accepted by HQ, after the network call."""
    with store.read() as c:
        controls = privacy(c, store.settings)
        if not controls["report_suggestion_outcomes"] or not controls["share_stats_for_suggestions"]:
            return 0
        rows = c.execute("SELECT id,outcome,updated_at FROM suggestions WHERE outcome IS NOT NULL "
                         "AND outcome_reported_at IS NULL ORDER BY updated_at,id LIMIT 100").fetchall()
    if not rows:
        return 0
    # Resolve the current destination again so a cleared or changed Gateway never uses the fetch's old key.
    secret, base = target(store)
    if not secret:
        raise GatewayError("Needs Tico Gateway")
    with store.read() as c:
        controls = privacy(c, store.settings)
        if not all(controls[name] for name in DEFAULTS):
            return 0
    _post(secret, base, "/v1/suggestions/outcomes",
          [{"suggestion_id": r["id"], "outcome": r["outcome"]} for r in rows], decode=False)
    with store.transaction() as c:
        for row in rows:
            c.execute("UPDATE suggestions SET outcome_reported_at=? WHERE id=? AND outcome=? AND updated_at=?",
                      (H.now(), row["id"], row["outcome"], row["updated_at"]))
    return len(rows)
