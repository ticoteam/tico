"""Notices from Tico Team, the install's side (docs/tico-hq.md).

Tico Team can send a short notice to an install that has a Gateway key: HQ queues it on its system relay for the
install, for the trigger `_tico`, and the pull loop (backend/hook_relay.py) feeds it to `triggers.receive` like any other
delivery. Three things come of one:

- the notice is kept here (`tico_notices`) and shows in the notices banner next to what `GET /v1/latest` carries,
  once per notice id (`banner`, used by hosted_updates.notice);
- rules see it as the event `tico.notice` with `type` = its kind (`security`, `billing`, `info`), through
  `rules.judge_event`; the built-in team rows below wake BotOps or ask the owner, and the owner edits or switches
  them off but cannot delete them;
- `webhook.received` is not judged for it: that event is for senders outside Tico and this one is judged once, as
  `tico.notice`.

The trigger `_tico` has no address a sender can use: its stored secret hash is random and was never shown, and
`triggers.receive` refuses anything but the relay. Only the relay HQ lists with `system: true` (`trigger_relays`,
recorded from that list, never from a delivery) may feed it, so another relay naming `_tico` is ignored.
"""
import json
import re
import secrets

from . import hubdb as H

ID = "_tico"                     # the built-in trigger, its event is `webhook._tico`; the underscore is not a valid
                                 # trigger id (triggers.ID), so nobody can make one that collides
NAME = "Tico Team"
EVENT = "tico.notice"
KINDS = ("info", "security", "billing")
RULE_PREFIX = "tico-notice-"     # the built-in rows; rules.delete keeps them
RULE_POSITION = 90_000           # after any owner rule: a new team rule goes after the last position below this
BOTOPS = "botops"
SHOWN_DAYS = 14                  # a relayed notice stays in the banner this long
SHOWN = 5
KEEP_DAYS = 90
LISTED = 50
RELAY = re.compile(r"hr_[0-9a-f]{16}")


def is_builtin(trigger_id):
    return trigger_id == ID


# ----------------------------------------------------------------------------- the trigger and its rules
def ensure(c, owner=None):
    """The built-in trigger and rules, made if missing; safe to call any number of times. `owner` is the owner's
    actor (`human:<id>`): the built-in rows ask that person, so they wait until there is one. Returns whether the
    trigger and all rules exist."""
    now = H.now()
    # The secret hash is random and never shown, so no address can reach this trigger; it is only fed by the relay.
    c.execute("INSERT OR IGNORE INTO triggers(id,name,kind,secret_hash,created_by,created_at,updated_at,preset,type_from,unheard) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (ID, NAME, "webhook", secrets.token_hex(32), H.KEEPER, now, now, "none", "/kind", "log"))
    c.execute("UPDATE triggers SET deleted_at=NULL WHERE id=? AND deleted_at IS NOT NULL", (ID,))
    if not owner:
        return False
    for row in RULES:
        c.execute("INSERT OR IGNORE INTO rules(id,scope_kind,scope_id,event,condition,then_outcome,params,enabled,"
                  "position,note,created_by,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,1)",
                  (row["id"], "team", "", EVENT, json.dumps(row["when"], sort_keys=True), row["then"],
                   json.dumps(row["params"], sort_keys=True), RULE_POSITION + row["order"], row["note"], owner, now, now))
    # The owner can change; their built-in rows keep asking whoever is the owner now.
    c.execute("UPDATE rules SET created_by=? WHERE id LIKE ? AND created_by<>?", (owner, RULE_PREFIX + "%", owner))
    return True


def complete(c, owner):
    """Whether `ensure` has nothing left to do: a cheap read for the pull loop."""
    if not c.execute("SELECT 1 FROM triggers WHERE id=? AND deleted_at IS NULL AND created_by=?", (ID, H.KEEPER)).fetchone():
        return False
    if not owner:
        return True
    return c.execute("SELECT count(*) FROM rules WHERE id LIKE ? AND created_by=?",
                     (RULE_PREFIX + "%", owner)).fetchone()[0] >= len(RULES)


def _row(name, when, then, params, note, order):
    return {"id": RULE_PREFIX + name, "when": when, "then": then, "params": params, "note": note, "order": order}


RULES = (
    _row("security-wake", {"type": "security"}, "action",
         {"action": "wake_bot", "bot": BOTOPS,
          "text": "Tico Team sent a security notice: {{payload.body}} Check whether this server is affected and what to do."},
         "A security notice from Tico Team wakes BotOps", 0),
    _row("security-ask", {"type": "security"}, "ask", {"text": "Tico Team sent a security notice: {{payload.body}}"},
         "A security notice from Tico Team asks the owner", 1),
    _row("info-note", {"type": "info"}, "note",
         {"bot": BOTOPS, "text": "Tico Team sent a notice: {{payload.body}}"},
         "An info notice from Tico Team goes to BotOps as a note", 2),
    _row("billing-ask", {"type": "billing"}, "ask", {"text": "Tico Team sent a billing notice: {{payload.body}}"},
         "A billing notice from Tico Team asks the owner", 3),
)
RULE_IDS = frozenset(r["id"] for r in RULES)


def is_builtin_rule(rule_id):
    return str(rule_id).startswith(RULE_PREFIX)


# ----------------------------------------------------------------------------- the relay HQ made for it
def relay(c):
    row = c.execute("SELECT relay_id FROM trigger_relays WHERE trigger_id=? AND deleted_at IS NULL", (ID,)).fetchone()
    return row["relay_id"] if row else None


def record_relay(c, relay_id):
    """Remember `relay_id` as this install's relay for `_tico`, replacing an older one. Returns False for an id that is
    not a relay id."""
    if not RELAY.fullmatch(str(relay_id or "")):
        return False
    if relay(c) == relay_id:
        return True
    c.execute("DELETE FROM trigger_relays WHERE trigger_id=? AND deleted_at IS NULL", (ID,))
    c.execute("INSERT OR REPLACE INTO trigger_relays(relay_id,trigger_id,address_prefix,created_by,created_at,deleted_at,hq_gone) "
              "VALUES(?,?,?,?,?,NULL,0)", (relay_id, ID, "", H.KEEPER, H.now()))
    return True


def accepts(c, relay_id):
    """Whether a delivery for `_tico` from `relay_id` may be fed: only the relay recorded from HQ's list (`system: true`).
    A delivery alone never records one."""
    return bool(relay_id) and relay(c) == relay_id


# ----------------------------------------------------------------------------- a notice arrives
def parse(body):
    """The notice in a delivery's body as a dict (`id`, `kind`, `body`, `url`, `sent_at`), or None."""
    try:
        got = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(got, dict) or not isinstance(got.get("body"), str) or not got["body"].strip():
        return None
    ident = got.get("id")
    ident = str(ident)[:80] if isinstance(ident, (str, int)) and not isinstance(ident, bool) else ""
    kind = str(got.get("kind") or "info").strip().lower()
    return {"id": ident, "kind": kind[:20], "body": got["body"].strip()[:500],
            "url": _https(got.get("url")), "sent_at": str(got.get("sent_at") or "")[:40]}


def _https(value):
    value = str(value or "")
    return value if re.fullmatch(r"https://[^\s<>\"']{1,490}", value) else ""


def arrived(c, found, delivery, type_, via, body, payload, content, judge):
    """Keep the notice and let the rules judge it as `tico.notice`; returns the rules' results. Runs in the delivery's
    write transaction. `judge(c, ctx, event)` is triggers' savepointed judge."""
    notice = parse(body)
    if not notice:
        return []
    notice_id = notice["id"] or delivery
    c.execute("INSERT OR IGNORE INTO tico_notices(notice_id,kind,body,url,sent_at,received_at,delivery) VALUES(?,?,?,?,?,?,?)",
              (notice_id, notice["kind"], notice["body"], notice["url"], notice["sent_at"], H.now(), delivery))
    c.execute("DELETE FROM tico_notices WHERE received_at<?", (H.shift(H.now(), days=-KEEP_DAYS),))
    return judge(c, {"trigger": found["id"], "trigger_name": found["name"], "type": type_, "delivery": delivery,
                     "via": via, "payload": payload, "content": content, "kind": notice["kind"],
                     "notice_id": notice_id}, EVENT)


# ----------------------------------------------------------------------------- what people see
def _view(row, source="relay"):
    return {"id": row["notice_id"], "kind": row["kind"], "body": row["body"], "url": row["url"],
            "sent_at": row["sent_at"], "received_at": row["received_at"], "source": source}


def listing(c):
    return [_view(r) for r in c.execute("SELECT * FROM tico_notices ORDER BY received_at DESC LIMIT ?", (LISTED,)).fetchall()]


def banner(c, message):
    """What the banner shows: recent relayed notices, then `message` (what `GET /v1/latest` has for this version).
    A relayed notice whose id `GET /v1/latest` also carries shows once, as the one from `/v1/latest`."""
    from . import releases
    extras = releases.CHECKER.extras
    latest = {str(n["id"]) for n in (extras or {}).get("notices", [])}
    if message:
        latest.add(str(message["id"]))
    since = H.shift(H.now(), days=-SHOWN_DAYS)
    out = []
    for row in c.execute("SELECT * FROM tico_notices WHERE received_at>? ORDER BY received_at DESC LIMIT ?",
                         (since, SHOWN + len(latest))).fetchall():
        if row["notice_id"] in latest or len(out) >= SHOWN:
            continue
        out.append({"id": row["notice_id"], "versions": "", "body": row["body"], "url": row["url"], "kind": row["kind"]})
    return out + ([message] if message else [])


def notices(c, message):
    """The list `GET /api/v2/notices` gives: relayed notices, and the one from `/v1/latest` unless it also came by relay."""
    out = listing(c)
    if message and str(message["id"]) not in {n["id"] for n in out}:
        out.append({"id": str(message["id"]), "kind": "", "body": message["body"], "url": message["url"], "sent_at": "",
                    "received_at": "", "source": "latest"})
    return out


def install(app, store):
    from fastapi import Request
    from .store import Problem

    @app.get("/api/v2/notices")
    def notice_list(request: Request):
        """Notices from Tico Team: the ones that came through the Gateway relay and the current one from the release
        check, newest first, each once."""
        if request.state.identity.role not in ("human", "owner", "bot"):
            raise Problem("forbidden", "Only people and bots read notices", 403)
        from . import releases
        with store.read() as c:
            return {"notices": notices(c, releases.notice().get("message"))}
