"""Goals agreed in words: the CEO Bot's goal read-back (docs/goals-and-kpis.md; spec chat-first-goals.md).

In the owner's chat the CEO Bot asks Tico to read back up to 20 changes to goals, their measures and the CEO Bot's
suggestions (`POST /api/v2/goals/read-back`). Tico writes the message itself, from the changes and what is stored
now, posts it as the CEO Bot's reply and stores the exact saves it stands for (event `goal.read_back` on the
message): each change is one or more steps, a route, a body. A change the owner could not make by their own click
here (a colour, done or dropped, archiving, a reading, the company Vision) is refused before anything is posted.

The goal, goal-KPI and suggestion routes then accept the owner's yes to it (`agreement`): their next message in the
chat, with nothing from anyone in between and nothing after it, a plain yes ("yes", "ok", "go ahead", "save it") or a
yes to some by number ("yes to 1 and 3", "yes except 2"), within 30 minutes of the read-back. Tico reads the numbers,
not the bot. A save must be one stored step exactly, of a change the yes covers, saved once, and the goal must not
have been changed by anyone since the read-back but these saves. Anything else is `403 not_agreed` and saves nothing.
Each save is recorded as the owner, via the CEO Bot, citing their yes and the read-back (`H.DELEGATION`).
"""

import json
import re

from pydantic import ValidationError

from . import ceo_bot
from . import goals as G
from . import kpis as K
from .store import H, Problem

TTL_S = 30 * 60
MOST = 20
EVENT = "goal.read_back"
SAVED = "goal.read_back_save"
NEW = "{new}"                 # a step's goal is the one the change's first step created
CLOSING_ONE = "Reply yes to save exactly this; any other reply saves nothing."
CLOSING_MANY = ("Reply yes to save exactly these, or yes to some by number (like “yes to 1 and 3”); any other reply "
                "saves nothing.")

# The routes a yes to a read-back saves through. Everything else on Goals stays a click or a card.
_SAVING = [re.compile(p) for p in (
    r"/api/v2/goals", r"/api/v2/goals/(?!refresh$|read-back$)[^/]+", r"/api/v2/goals/[^/]+/why",
    r"/api/v2/goals/[^/]+/kpis", r"/api/v2/goals/[^/]+/kpis/[^/]+", r"/api/v2/goals/[^/]+/kpis/[^/]+/unlink",
    r"/api/v2/goal-proposals/[^/]+/decide")]

# What stays the owner's click, in the one sentence the CEO Bot passes on (criterion 12).
CLICK = {
    "colour": "A goal's colour stays your click on the Goals page, so I left it out.",
    "status": "Marking a goal done or dropped stays your click on the Goals page, so I left it out.",
    "archive": "Archiving stays your click on the Goals page, so I left it out.",
    "reading": "A KPI reading is logged on the KPI itself, not agreed in chat, so I left it out.",
    "vision": "The company Vision stays your click at the top of the Goals page, so I left it out.",
    "strategy": "Strategy drafts and bets stay your click on the Strategy page, so I left it out.",
    "recovery": "A recovery suggestion stays your click on its card, so I left it out.",
}
_CLICK_KINDS = {"colour": "colour", "color": "colour", "status": "status", "done": "status", "dropped": "status",
                "drop": "status", "reopen": "status", "archive": "archive", "kpi_archive": "archive",
                "delete": "archive", "restore": "archive", "reading": "reading", "readings": "reading",
                "kpi_reading": "reading", "log": "reading", "vision": "vision", "company": "vision",
                "description": "vision", "strategy": "strategy", "bet": "strategy"}
_CLICK_FIELDS = {"colour": "colour", "color": "colour", "status": "status", "done": "status", "dropped": "status",
                 "archived": "archive", "archive": "archive", "value": "reading", "reading": "reading",
                 "readings": "reading", "vision": "vision"}
KINDS = ("goal_create", "goal_update", "measure_add", "target_set", "measure_remove", "suggestion")
GOAL_FIELDS = ("title", "owner", "parent_id", "bet_id", "why")
MEASURE_FIELDS = ("kpi_id", "name", "definition", "unit", "direction", "cadence", "source_note", "owner")
TARGET_FIELDS = ("kind", "baseline", "baseline_at", "target", "deadline", "min", "max")
SUGGESTIONS = ("goal_wording", "goal_kpi", "kpi_definition", "kpi_target", "flag", "goal_why")


def saving(method, path):
    """Whether this write is one a yes to a goal read-back may make."""
    return method == "POST" and any(p.fullmatch(str(path or "")) for p in _SAVING)


def ceo_turn(who):
    """The CEO Bot's turn in the owner's own chat: the owner's rights, via the CEO Bot, not a click."""
    return bool(who.via == "assistant" and not who.confirmed and who.attempt_id and who.role == "owner"
                and who.task_actor == "bot:" + ceo_bot.SLUG)


def active():
    """The read-back save the current write was let through for (backend/app.py), or None."""
    record = H.DELEGATION.get() if H.VIA.get() == "assistant" else None
    return (record or {}).get("goal_read_back") and record


# ------------------------------------------------------------------ the owner's yes
_YES = (r"(?:yes|yep|yeah|y|ok|okay|sure|go ahead|go|do it|please do|save it|save them|save|confirm(?:ed)?|"
        r"correct|that'?s right)")
_TAIL = r"[\s,.!]*(?:please|thanks|thank you)?[\s.!]*"
_N = r"#?\d{1,2}"
_NUMS = rf"{_N}(?:\s*(?:,\s*and|,|&|\+|and)\s*{_N})*"
PLAIN = re.compile(rf"{_YES}(?:\s+(?:to\s+)?(?:all|both|everything|all of them|them all))?{_TAIL}", re.I)
SOME = re.compile(rf"{_YES}[\s,:]*(?:to\s+)?(?:numbers?\s+)?(?P<yes>{_NUMS})"
                  rf"(?:\s*[,;.]?\s*(?:and\s+|but\s+)?(?:no|not)\s+(?:to\s+)?(?P<no>{_NUMS}))?{_TAIL}", re.I)
EXCEPT = re.compile(rf"{_YES}(?:\s+to\s+(?:all|everything)\s+but|(?:\s+to\s+(?:all|everything))?[\s,]*"
                    rf"(?:except(?:\s+for)?|but\s+not|apart\s+from|other\s+than))\s+(?:numbers?\s+)?(?P<no>{_NUMS})"
                    rf"{_TAIL}", re.I)


def agreed(words, count):
    """The change numbers the owner's reply says yes to, or None when it is not a yes to the read-back as it stands:
    "yes but make it 50", "wait", a number it does not have, or the same number both ways."""
    text = " ".join(str(words or "").split())
    if not text or len(text) > 120:
        return None
    numbers = lambda part: {int(n) for n in re.findall(r"\d+", part or "")}
    if PLAIN.fullmatch(text):
        return set(range(1, count + 1))
    if m := SOME.fullmatch(text):
        yes, no = numbers(m["yes"]), numbers(m["no"])
    elif m := EXCEPT.fullmatch(text):
        no = numbers(m["no"])
        yes = set(range(1, count + 1)) - no
    else:
        return None
    if not yes or yes & no or any(n < 1 or n > count for n in yes | no):
        return None
    return yes


def not_agreed(detail):
    return Problem("not_agreed", detail + "; nothing was saved. Read it back again (`hub goal read-back`) and save on "
                   "the owner's yes", 403)


def stored(c, message_id):
    """The read-back stored with this message: {id, created, changes}, or None."""
    row = c.execute("SELECT m.id, m.created, m.from_actor, m.conversation_id, e.detail_json FROM messages m "
                    "JOIN events e ON e.target=m.id AND e.action=? WHERE m.id=? AND m.deleted_at IS NULL",
                    (EVENT, message_id)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "created": row["created"], "conversation_id": row["conversation_id"],
            "changes": (H._json(row["detail_json"], {}) or {}).get("changes") or []}


def saves(c, back_id):
    """{(change, step): what that save recorded} for one read-back."""
    out = {}
    for r in c.execute("SELECT detail_json FROM events WHERE target=? AND action=?", (back_id, SAVED)):
        d = H._json(r["detail_json"], {}) or {}
        out[(d.get("change"), d.get("step"))] = d
    return out


def answered(c, who, cited):
    """The read-back the owner's cited message says yes to, with the numbers it covers. Their message must be the
    very next one in the chat after the read-back (nothing from anyone in between, nothing from them since), and the
    read-back no more than 30 minutes old."""
    msg = H.message(c, cited) or {}
    room, at = msg.get("conversation_id"), str(msg.get("created") or "")
    before = c.execute("SELECT id FROM messages WHERE conversation_id=? AND id<>? AND created<=? AND deleted_at IS NULL "
                       "ORDER BY created DESC, rowid DESC LIMIT 1", (room, cited, at)).fetchone()
    back = stored(c, before["id"]) if before else None
    if not back or H.message(c, back["id"])["from_actor"] != "bot:" + ceo_bot.SLUG:
        raise not_agreed("The owner's message is not a reply to a goal read-back (anything said after the read-back "
                         "takes the yes back)")
    later = c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND from_actor=? AND created>? AND id<>? "
                      "AND deleted_at IS NULL LIMIT 1", (room, who.actor, at, cited)).fetchone()
    if later:
        raise not_agreed("The owner wrote again after their yes")
    if str(back["created"]) < H.shift(H.now(), seconds=-TTL_S):
        raise not_agreed(f"The read-back is more than {TTL_S // 60} minutes old")
    yes = agreed(msg.get("body"), len(back["changes"]))
    if yes is None:
        raise not_agreed("The owner's reply is not a plain yes to the read-back")
    return back, yes, " ".join(str(msg.get("body") or "").split())[:120]


def owner_actor(c, value, actor):
    value = str(value or "").strip()
    if value in ("me", "self"):
        return actor
    if value == G.COMPANY:
        return G.COMPANY
    return H.resolve_actor(c, value) or value


def _model(path):
    from . import models as M
    if path == "/api/v2/goals":
        return M.GoalCreate
    for end, model in (("/why", M.GoalWhy), ("/unlink", M.Empty), ("/decide", M.GoalProposalDecision)):
        if path.endswith(end):
            return model
    if re.fullmatch(r"/api/v2/goals/[^/]+/kpis", path):
        return M.GoalKpiLink
    if re.fullmatch(r"/api/v2/goals/[^/]+/kpis/[^/]+", path):
        return M.KpiTarget
    return M.GoalUpdate


def canonical(c, path, body, actor):
    """A save's body as the route reads it, owners resolved: two bodies that save the same thing compare equal."""
    if isinstance(body, (bytes, str)):
        try:
            body = json.loads(body or b"{}")
        except ValueError:
            return None
    if not isinstance(body, dict):
        return None
    try:
        data = _model(path)(**body).model_dump()
    except (ValidationError, TypeError):
        return None
    if data.get("owner"):
        data["owner"] = owner_actor(c, data["owner"], actor)
    return data


def step_path(step, change, done):
    created = (done.get((change["n"], 0)) or {}).get("goal_id")
    return step["path"].replace(NEW, created) if NEW in step["path"] and created else step["path"]


def changed_since(c, back, change, done):
    """What someone changed on this change's goals since the read-back, other than its own saves, in words."""
    mine = {m for d in done.values() for m in d.get("marks") or []}
    goals = [g for g in change.get("watch") or []] + [d["goal_id"] for (n, _), d in done.items()
                                                       if n == change["n"] and d.get("goal_id")]
    said = []
    for gid in dict.fromkeys(goals):
        for r in c.execute("SELECT id, actor, field, old, new, status_source FROM goal_events WHERE goal_id=? AND ts>? "
                           "ORDER BY ts", (gid, back["created"])):
            if r["id"] in mine or r["field"] in ("rank", "checkin", "status_source") or (
                    r["field"] == "status" and r["status_source"] == "auto") or r["field"] == "proposal":
                continue
            who = H.actor_id(r["actor"])
            if r["field"] in ("title", "why", "owner", "status", "kpi_target"):
                said.append(f"{who} changed the {r['field'].replace('kpi_', '')} from “{r['old'] or 'none'}” to "
                            f"“{r['new'] or 'none'}”")
            else:
                said.append(f"{who} changed its {r['field'].replace('_id', '').replace('_', ' ')}")
    return said[:3]


def agreement(c, who, cited, method, path, body):
    """What this save is, once checked against the read-back the owner said yes to: the delegation record's
    `goal_read_back` part. Raises `not_agreed` (403), or 409 when the goal changed since or it was saved already."""
    back, yes, words = answered(c, who, cited)
    done = saves(c, back["id"])
    sent = canonical(c, path, body, who.actor)
    found = None
    for change in back["changes"]:
        for i, step in enumerate(change["steps"]):
            target = step_path(step, change, done)
            if (method == step["method"] and path == target and NEW not in target and sent is not None
                    and sent == canonical(c, target, step["body"], who.actor)):
                found = (change, i)
                break
        if found:
            break
    if not found:
        raise not_agreed("That is not what was read back: save exactly a change from the read-back, no other field, "
                         "value or goal")
    change, i = found
    if change["n"] not in yes:
        raise not_agreed(f"The owner did not say yes to change {change['n']}")
    if (change["n"], i) in done:
        raise Problem("conflict", f"Change {change['n']} is saved already", 409)
    if said := changed_since(c, back, change, done):
        raise Problem("goal_changed", f"Change {change['n']} was not saved: since the read-back " + "; ".join(said)
                      + ". Tell the owner what changed and read it back again", 409)
    return {"goal_read_back": back["id"], "change": change["n"], "step": i, "steps": len(change["steps"]),
            "words": words, "kind": change["kind"]}


def record(c, actor, claim, mark, out):
    """Record the save in the write's own transaction: which step of which change, the goal it made or changed and
    the goal history rows it wrote (so they are not read as someone else's change), and add the link to the reply."""
    gid = ((out or {}).get("goal") or {}).get("id") if isinstance(out, dict) else None
    marks = [r[0] for r in c.execute("SELECT id FROM goal_events WHERE rowid>?", (mark,))]
    H.event(c, actor, SAVED, claim["goal_read_back"], {"change": claim["change"], "step": claim["step"],
                                                       "goal_id": gid, "marks": marks})
    return gid


def text(changes):
    one = len(changes) == 1
    if one:
        head = "I'll save this goal:" if changes[0]["kind"] == "goal_create" else "I'll save this change:"
        return head + "\n" + changes[0]["text"] + "\n" + CLOSING_ONE
    lines = [f"{c['n']}. {c['text']}" for c in changes]
    return f"I'll save these {len(changes)} changes:\n" + "\n".join(lines) + "\n" + CLOSING_MANY


def click_reason(raw):
    """The sentence for a change that stays a click, or None."""
    kind = str(raw.get("kind") or "").strip().lower()
    if kind in _CLICK_KINDS:
        return CLICK[_CLICK_KINDS[kind]]
    for key in raw:
        if str(key).lower() in _CLICK_FIELDS:
            return CLICK[_CLICK_FIELDS[str(key).lower()]]
    return None


def target_words(spec, target):
    label = K.target_label(spec or {}, target) if target else ""
    return label.removeprefix("→ ") if label else "no target"
