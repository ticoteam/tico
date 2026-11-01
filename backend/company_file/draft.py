"""The setup draft: its shape, its limits, and the rules every draft must keep.

A draft is the object the tico.team/setup page keeps (setup.js `blankDraft()`): step, company, people, bots, goals,
rules, tools and seq, and `strategy` once the Strategy step has drafted one (optional: a draft without it is still
valid). The server adds four fields the page passes back untouched and the company file never holds:
`undo` (the draft before BotOps's last change), `chat` (the conversation so far), `removed` (bots taken off the chart,
so they are not added back unasked) and `pending` (teams BotOps has suggested removing). Anything else is refused.
"""
import copy
import datetime as dt
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent


def _load(name):
    return json.loads((HERE / name).read_text())


SPEC = _load("actions.json")
ROLES = _load("roles.json")["roles"]
TEMPLATES = {role["slug"] for role in ROLES}
# Seats the company models place before Tico has a template for them: "No bot yet", and a person can hold one.
SEATS = _load("seats.json")["seats"]
OPEN_SEATS = {seat["slug"] for seat in SEATS}
# Retired templates a saved draft may still name. The built-in that replaced each one comes with every install, so a seat
# for it that is not built yet leaves the draft quietly instead of refusing the whole draft (`check`).
RETIRED = {"bookkeeping"}
# The company models a chart starts from (model_<key>.json), in the order the setup chat tries them: the first whose
# `picks_when` signal the company has, else Everything. On-site work comes before services, so a cleaning company that
# also sells its hours is a local service business.
MODEL_ORDER = ("software", "local", "services", "store", "everything")
MODELS = {key: _load("model_%s.json" % key) for key in MODEL_ORDER}
LIMITS = SPEC["limits"]
VOCAB = SPEC["vocabulary"]
STEPS = tuple(VOCAB["steps"])
DEPARTMENTS = tuple(VOCAB["departments"])
TEAMS = tuple(VOCAB["teams"])
SIGNALS = tuple(VOCAB["signals"])
RULES = {row["id"]: row["label"] for row in VOCAB["rules"]}
REVIEW = {row["id"]: row["label"] for row in VOCAB["review"]}
TOOLS = {row["id"]: row["label"] for row in VOCAB["tools"]}
HARNESSES = {row["id"]: row["name"] for row in VOCAB["harnesses"]}
DIRECTIONS = tuple(VOCAB["directions"])
SECTIONS = tuple(VOCAB["sections"][2:])        # serve, sell, know, not: the strategy's four sections
METHODS = tuple(VOCAB["methods"])              # how a segment was sized (tico backend/market_spend.py METHODS)
POST_SOURCES = tuple(VOCAB["post_sources"])
POST_TAGS = tuple(VOCAB["post_tags"])

ID = re.compile(r"[a-z0-9][a-z0-9-]*")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SERVER_FIELDS = ("undo", "chat", "removed", "pending")
TOP = ("step", "company", "people", "bots", "goals", "rules", "tools", "seq")
# Parts of the plan a draft may leave out: an older page, and a plan made before the Strategy step, has none.
OPTIONAL = ("strategy",)
BET_ID = re.compile(r"b[1-9]")
URL = re.compile(r"https?://\S+", re.I)
YEARS = (1990, 2100)
BOT_FIELDS = ("id", "template", "title", "dept", "lead", "core", "reason", "start", "routine", "tools", "about", "runtime",
              "existing")
# `team` is the name of the group a bot or seat goes in when the file is imported (backend/company_import.py). Absent or
# blank, the group is the one its template belongs to, as it always was.
BOT_OPTIONAL = ("isNew", "reports_to", "first_job", "person", "team")
PERSON_FIELDS = ("id", "name", "role", "dept", "owner")
# A goal's measure, as the page keeps it (format 0.2): up or down toward a target by a deadline, or a range to stay in.
# `measure` "" is not set yet.
GOAL_FIELDS = ("id", "title", "owner", "measure", "unit", "direction")
AIM = {"up": ("target", "baseline", "deadline"), "down": ("target", "baseline", "deadline"), "range": ("min", "max")}
# Where a goal sits, as the page and `POST /goals` keep it: a company goal, a group's (with its `dept` and the company
# goal it serves as `parent`) or a role's (its `dept` and its group goal as `parent`). All three are optional, as in
# tico-services' copy of this check (recruiting/company_file/draft.py).
LEVELS = ("company", "group", "role")
GOAL_LAYER = ("level", "parent", "dept")
DEPT_WORDS = {
    "engineering": ["engineer", "engineering", "developer", "dev", "code", "cto", "tech"],
    "product": ["product", "pm", "design", "ux"],
    "marketing": ["marketing", "marketer", "growth", "brand", "content", "cmo", "social", "seo"],
    "sales": ["sales", "account", "bd", "business development", "revenue", "partnership"],
    "support": ["support", "customer", "success", "service", "cx"],
    "finance": ["finance", "cfo", "accounting", "accountant", "books", "bookkeeper", "controller"],
    "operations": ["ops", "operations", "coo", "office", "logistics"],
    "hr": ["hr", "people", "recruit", "talent", "hiring"],
    "legal": ["legal", "counsel", "lawyer", "compliance"],
    "leadership": ["ceo", "founder", "owner", "president", "chief of staff"],
}
HEAD = re.compile(r"\b(head|lead|director|vp|chief|manager|cto|cfo|cmo|coo|cpo)\b", re.I)


class DraftError(ValueError):
    """A draft that breaks its shape or limits. The message names the field, never the value sent."""


def blank():
    """setup.js `blankDraft()`."""
    return {"step": "company", "company": {"name": "", "about": "", "signals": []}, "people": [], "bots": [], "goals": [],
            "rules": {"mode": "defaults", "never": list(RULES), "review": "one"}, "tools": {"mode": "defaults", "connect": []},
            "seq": 1}


def dept_of(role):
    """setup.js `deptOf()`: the department a person's role names, else None."""
    text = " %s " % str(role or "").lower()
    for dept, words in DEPT_WORDS.items():
        if any(re.search(r"\b%s\b" % re.escape(word), text) for word in words):
            return dept
    return None


def slugify(text):
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", str(text).lower())) or "x"


# ---------------------------------------------------------------------------------------------------- the chart
def owner(draft):
    return next((p for p in draft["people"] if p["owner_id"]), draft["people"][0] if draft["people"] else None)


def owner_id(draft):
    found = owner(draft)
    return found["id"] if found else "you"


def chart_ids(draft):
    """Who a bot may report to and a goal may be owned by: people (or "you" when there are none) and Tico's bots."""
    ids = {p["id"] for p in draft["people"]} or {"you"}
    return ids | {b["id"] for b in draft["bots"] if not b["existing"]}


def manager_of(draft, bot):
    """setup.js `managerOf()`, with a reports-to the visitor or BotOps set taking precedence while it still exists."""
    chosen = bot.get("reports_to")
    if chosen and chosen != bot["id"] and chosen in chart_ids(draft):
        return chosen
    if bot["core"]:
        return owner_id(draft)
    human = next((p for p in draft["people"] if p["department"] == bot["department"] and not p["owner_id"] and HEAD.search(p["role"])), None)
    if bot["lead"]:
        return human["id"] if human else owner_id(draft)
    lead = next((x for x in draft["bots"] if x["department"] == bot["department"] and x["lead"] and x is not bot), None)
    if lead:
        return lead["id"]
    return human["id"] if human else owner_id(draft)


def loops(draft):
    """The first bot whose reporting line comes back to itself, else None."""
    bots = {b["id"]: b for b in draft["bots"]}
    for bot in draft["bots"]:
        seen, at = set(), bot
        while at is not None and at["id"] not in seen:
            seen.add(at["id"])
            at = bots.get(manager_of(draft, at))
        if at is not None:
            return at
    return None


def teams_in(draft):
    return {b["department"] for b in draft["bots"] if not b["core"] and not b["existing"]}


# ---------------------------------------------------------------------------------------------------- shape
def _text(value, field, limit, empty=True):
    if not isinstance(value, str):
        raise DraftError("%s must be text" % field)
    # A lone surrogate cannot be written as UTF-8, and NUL ends strings elsewhere.
    value = re.sub("[\ud800-\udfff\x00]", "", value)
    if len(value) > limit:
        raise DraftError("%s is longer than %d characters" % (field, limit))
    if not empty and not value.strip():
        raise DraftError("%s is empty" % field)
    return value


def _id(value, field):
    if not isinstance(value, str) or len(value) > LIMITS["id"] or not ID.fullmatch(value):
        raise DraftError("%s must be a short id (a-z, 0-9 and -)" % field)
    return value


def _bool(value, field):
    if not isinstance(value, bool):
        raise DraftError("%s must be true or false" % field)
    return value


def _list(value, field, limit):
    if not isinstance(value, list):
        raise DraftError("%s must be a list" % field)
    if len(value) > limit:
        raise DraftError("%s has more than %d entries" % (field, limit))
    return value


def _keys(value, field, required, optional=()):
    if not isinstance(value, dict):
        raise DraftError("%s must be an object" % field)
    unknown = set(value) - set(required) - set(optional)
    if unknown:
        raise DraftError("%s has a field it can't have: %s" % (field, sorted(unknown)[0][:40]))
    missing = [key for key in required if key not in value]
    if missing:
        raise DraftError("%s is missing %s" % (field, missing[0]))
    return value


def _choices(value, field, allowed, limit=None):
    _list(value, field, limit or len(allowed))
    if any(not isinstance(item, str) or item not in allowed for item in value) or len(set(value)) != len(value):
        raise DraftError("%s has an unknown or repeated entry" % field)
    return list(value)


def number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or abs(value) > LIMITS["number"]:
        raise DraftError("%s must be a number" % field)
    return int(value) if float(value).is_integer() else float(value)


def date(value, field):
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise DraftError("%s must be a date like 2027-03-01" % field)
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise DraftError("%s is not a real date" % field) from None
    return value


def _bot(raw, at):
    field = "bots[%d]" % at
    _keys(raw, field, BOT_FIELDS, BOT_OPTIONAL)
    bot = {"id": _id(raw["id"], field + ".id"),
           "template": None if raw["template"] is None else _id(raw["template"], field + ".template"),
           "title": _text(raw["title"], field + ".title", LIMITS["title"], empty=False),
           "dept": raw["department"], "lead": _bool(raw["lead"], field + ".lead"), "core": _bool(raw["core"], field + ".core"),
           "reason": _text(raw["reason"], field + ".reason", LIMITS["reason"]),
           "start": _bool(raw["start"], field + ".start"),
           "routine": _text(raw["routine"], field + ".routine", LIMITS["routine"]),
           "tools": [_id(t, field + ".tools") for t in _list(raw["tools"], field + ".tools", 12)],
           "about": _text(raw["about"], field + ".about", LIMITS["summary"]),
           "runtime": None if raw["runtime"] is None else _id(raw["runtime"], field + ".runtime"),
           "existing": _bool(raw["existing"], field + ".existing")}
    if bot["department"] not in DEPARTMENTS:
        raise DraftError(field + ".dept is not a department")
    if bot["existing"] and bot["runtime"] not in HARNESSES:
        raise DraftError(field + ".runtime is not an agent Tico knows")
    if not bot["existing"] and bot["template"] not in TEMPLATES | OPEN_SEATS:
        raise DraftError(field + ".template is not one of Tico's role templates")
    if raw.get("reports_to") is not None:
        bot["reports_to"] = _id(raw["reports_to"], field + ".reports_to")
    if raw.get("person") is not None and not bot["existing"]:
        bot["person"] = _id(raw["person"], field + ".person")
    if raw.get("first_job") and not bot["existing"]:
        bot["first_job"] = _text(raw["first_job"], field + ".first_job", LIMITS["first_job"])
    if raw.get("team") is not None:
        team = _text(raw["team"], field + ".team", LIMITS["team"]).strip()
        if team:
            bot["team"] = team
    if bot["existing"]:
        # An agent the visitor already runs keeps its own schedule and work: the file holds no start, routine, tools
        # or first job for it, so the draft doesn't either.
        bot.update(start=True, routine="", tools=[])
    if raw.get("isNew") is True:
        bot["isNew"] = True
    return bot


def _person(raw, at):
    field = "people[%d]" % at
    _keys(raw, field, PERSON_FIELDS, ("isNew",))
    person = {"id": _id(raw["id"], field + ".id"), "name": _text(raw["name"], field + ".name", LIMITS["name"]),
              "role": _text(raw["role"], field + ".role", LIMITS["role"]), "department": raw["department"],
              "owner_id": _bool(raw["owner_id"], field + ".owner")}
    if person["department"] is not None and person["department"] not in DEPARTMENTS:
        raise DraftError(field + ".dept is not a department")
    if raw.get("isNew") is True:
        person["isNew"] = True
    return person


def aim(raw, field):
    """A goal's measure fields, every one present: measure, unit, direction, then target, baseline and deadline (up or
    down) or min and max (range). A number or deadline not known yet is None."""
    direction = raw.get("direction", "up")
    if direction not in DIRECTIONS:
        raise DraftError(field + ".direction must be up, down or range")
    for key in set(raw) - set(AIM[direction]):
        if key in ("target", "baseline", "deadline", "min", "max") and raw[key] is not None:
            raise DraftError("%s.%s doesn't go with direction %s" % (field, key, direction))
    out = {"measure": _text(raw.get("measure") or "", field + ".measure", LIMITS["measure"]).strip(),
           "unit": _text(raw.get("unit") or "", field + ".unit", LIMITS["unit"]).strip(), "direction": direction}
    for key in AIM[direction]:
        value = raw.get(key)
        out[key] = None if value is None else date(value, field + "." + key) if key == "deadline" \
            else number(value, field + "." + key)
    if direction == "range" and None not in (out["min"], out["max"]) and out["min"] > out["max"]:
        raise DraftError(field + ".min is above max")
    return out


def _goal(raw, at):
    field = "goals[%d]" % at
    _keys(raw, field, ("id", "title", "owner", "measure"),
          GOAL_FIELDS + ("target", "baseline", "deadline", "min", "max", "isNew", "bet") + GOAL_LAYER)
    goal = {"id": _id(raw["id"], field + ".id"), "title": _text(raw["title"], field + ".title", LIMITS["goal_title"]),
            "owner_id": _id(raw["owner_id"], field + ".owner"), **aim(raw, field)}
    if raw.get("level") is not None:
        if raw["level"] not in LEVELS:
            raise DraftError(field + ".level must be company, group or role")
        goal["level"] = raw["level"]
    if "parent" in raw:
        # A parent that is no longer among the goals is let go in check().
        goal["parent"] = None if raw["parent"] is None else _id(raw["parent"], field + ".parent")
    if "department" in raw:
        if raw["department"] is not None and raw["department"] not in DEPARTMENTS:
            raise DraftError(field + ".dept is not a department")
        goal["department"] = raw["department"]
    if raw.get("bet") is not None:
        # A bet that is no longer among the strategy's is let go in check().
        if not isinstance(raw["bet"], str) or not BET_ID.fullmatch(raw["bet"]):
            raise DraftError(field + ".bet must be a bet id like b2")
        goal["bet"] = raw["bet"]
    if raw.get("isNew") is True:
        goal["isNew"] = True
    return goal


# ---------------------------------------------------------------------------------------------------- the strategy
def _url(value, field, empty=True):
    """An http(s) address with a host and no user name or password in it (a credential never lands in the plan)."""
    value = _text(value, field, LIMITS["url"]).strip()
    if not value and empty:
        return value
    try:
        parts = urlsplit(value) if URL.fullmatch(value) else None
        good = parts is not None and bool(parts.hostname) and "@" not in parts.netloc
        parts and parts.port                    # "https://host:abc" raises here
    except ValueError:                          # "https://[" and other addresses urlsplit can't read
        good = False
    if not good:
        raise DraftError(field + " must be an http or https address, with no user name or password")
    return value


def _day(value, field):
    """A date, or "" for none."""
    return "" if value in ("", None) else date(value, field)


def _bet_row(raw, at):
    field = "strategy.bets[%d]" % at
    _keys(raw, field, ("id",), ("statement", "why", "rules_out", "wrong_if", "check_by"))
    if not isinstance(raw["id"], str) or not BET_ID.fullmatch(raw["id"]) or int(raw["id"][1:]) > LIMITS["bets"]:
        raise DraftError(field + ".id must be b1 to b%d" % LIMITS["bets"])
    out = {"id": raw["id"], "statement": _text(raw.get("statement", ""), field + ".statement", LIMITS["statement"])}
    for key in ("why", "rules_out", "wrong_if"):
        out[key] = _text(raw.get(key, ""), "%s.%s" % (field, key), LIMITS["bet_text"])
    out["check_by"] = _day(raw.get("check_by", ""), field + ".check_by")
    return out


def _usd(value, field):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not float(value).is_integer() or not 0 <= value <= 1e15:
        raise DraftError(field + " must be a whole number of dollars or null")
    return int(value)


def _segment(raw, at):
    field = "strategy.market.segments[%d]" % at
    _keys(raw, field, ("key",), ("name", "parent", "usd", "method", "arithmetic", "evidence", "ours", "next"))
    method = raw.get("method") or "top_down"
    if method not in METHODS:
        raise DraftError(field + ".method is not a sizing method")
    out = {"key": _id(raw["key"], field + ".key"), "name": _text(raw.get("name", ""), field + ".name", LIMITS["segment_name"]),
           "parent": None if raw.get("parent") is None else _id(raw["parent"], field + ".parent"),
           "usd": _usd(raw.get("usd"), field + ".usd"), "method": method,
           "arithmetic": _text(raw.get("arithmetic", ""), field + ".arithmetic", LIMITS["arithmetic"]),
           "evidence": [_url(u, field + ".evidence", empty=False)
                        for u in _list(raw.get("evidence", []), field + ".evidence", LIMITS["evidence"])],
           "ours": _bool(raw.get("ours", False), field + ".ours"), "next": _bool(raw.get("next", False), field + ".next")}
    if not out["evidence"]:
        out["usd"] = None       # a size stands only on its evidence: never an invented number
    return out


def _player(raw, at):
    field = "strategy.market.players[%d]" % at
    _keys(raw, field, ("name",), ("url", "segments"))
    return {"name": _text(raw["name"], field + ".name", LIMITS["player_name"]), "url": _url(raw.get("url", ""), field + ".url"),
            "segments": [_id(s, field + ".segments") for s in _list(raw.get("segments", []), field + ".segments",
                                                                    LIMITS["segments"])]}


def _post(raw, at):
    field = "strategy.listening.posts[%d]" % at
    _keys(raw, field, ("url",), ("source", "title", "when", "tag"))
    out = {"source": raw.get("source") or "web", "url": _url(raw["url"], field + ".url", empty=False),
           "title": _text(raw.get("title", ""), field + ".title", LIMITS["post_title"]),
           "when": _day(raw.get("when", ""), field + ".when"), "tag": raw.get("tag") or "market"}
    if out["source"] not in POST_SOURCES or out["tag"] not in POST_TAGS:
        raise DraftError(field + " has an unknown source or tag")
    return out


def _strategy(raw):
    """The strategy block (the setup page's Strategy step), every part present: a part not drafted yet is empty. Two
    quiet repairs, like a goal's gone parent: a segment's parent that is not among the segments (or would make a loop)
    is let go, and so is a player's segment that is gone."""
    _keys(raw, "strategy", (), ("summary", "vision", "sections", "bets", "market", "listening"))
    sections = _keys(raw.get("sections") or {}, "strategy.sections", (), SECTIONS)
    market = _keys(raw.get("market") or {}, "strategy.market", (), ("definition", "segments", "players"))
    definition = _keys(market.get("definition") or {}, "strategy.market.definition", (), ("root", "in", "out", "year"))
    listening = _keys(raw.get("listening") or {}, "strategy.listening", (), ("requests", "posts"))
    year = definition.get("year")
    if year is not None and (isinstance(year, bool) or not isinstance(year, int) or not YEARS[0] <= year <= YEARS[1]):
        raise DraftError("strategy.market.definition.year must be a year")
    out = {"summary": _text(raw.get("summary", ""), "strategy.summary", LIMITS["strategy_line"]),
           "vision": _text(raw.get("vision", ""), "strategy.vision", LIMITS["strategy_line"]),
           "sections": {k: _text(sections.get(k, ""), "strategy.sections." + k, LIMITS["section"]) for k in SECTIONS},
           "bets": [_bet_row(b, i) for i, b in enumerate(_list(raw.get("bets", []), "strategy.bets", LIMITS["bets"]))],
           "market": {"definition": {"root": _text(definition.get("root", ""), "strategy.market.definition.root",
                                                   LIMITS["market_root"]),
                                     "in": _text(definition.get("in", ""), "strategy.market.definition.in",
                                                 LIMITS["market_scope"]),
                                     "out": _text(definition.get("out", ""), "strategy.market.definition.out",
                                                  LIMITS["market_scope"]),
                                     "year": year},
                      "segments": [_segment(s, i) for i, s in enumerate(_list(market.get("segments", []),
                                                                             "strategy.market.segments", LIMITS["segments"]))],
                      "players": [_player(p, i) for i, p in enumerate(_list(market.get("players", []),
                                                                           "strategy.market.players", LIMITS["players"]))]},
           "listening": {"requests": [_text(r, "strategy.listening.requests", LIMITS["request"]) for r in
                                      _list(listening.get("requests", []), "strategy.listening.requests", LIMITS["requests"])],
                         "posts": [_post(p, i) for i, p in enumerate(_list(listening.get("posts", []),
                                                                          "strategy.listening.posts", LIMITS["posts"]))]}}
    _unique(out["bets"], "strategy.bets")
    segments = out["market"]["segments"]
    keys = [s["key"] for s in segments]
    if len(set(keys)) != len(keys):
        raise DraftError("strategy.market.segments has two entries with the same key")
    for flag in ("ours", "next"):
        if sum(1 for s in segments if s[flag]) > 1:
            raise DraftError("only one segment is marked %s" % flag)
    parents = {s["key"]: s["parent"] for s in segments}
    for segment in segments:
        seen, at = {segment["key"]}, segment["parent"]
        while at is not None and at in parents and at not in seen:
            seen.add(at)
            at = parents[at]
        if segment["parent"] is not None and (segment["parent"] not in parents or at is not None and at in seen):
            segment["parent"] = parents[segment["key"]] = None
    for player in out["market"]["players"]:
        player["segments"] = [s for s in dict.fromkeys(player["segments"]) if s in parents]
    return out


def blank_strategy():
    """setup.js `blankStrategy()`: the block before anything is drafted."""
    return {"summary": "", "vision": "", "sections": {k: "" for k in SECTIONS}, "bets": [],
            "market": {"definition": {"root": "", "in": "", "out": "", "year": None}, "segments": [], "players": []},
            "listening": {"requests": [], "posts": []}}


def has_strategy(draft):
    """Whether a draft holds a strategy worth writing down: some text, a bet, a segment or a request in its block."""
    def filled(value):
        if isinstance(value, dict):
            return any(filled(v) for k, v in value.items() if k != "year")
        if isinstance(value, list):
            return bool(value)
        return value not in ("", None, False)
    return filled(draft.get("strategy") or {})


def plan_of(draft):
    """The plan a company file holds: the top-level parts, and the optional ones the draft has."""
    return {key: draft[key] for key in TOP + OPTIONAL if key in draft}


def _unique(rows, field):
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise DraftError("%s has two entries with the same id" % field)


def check(raw, snapshot=False):
    """A valid copy of a draft, or DraftError. `snapshot` is an undo copy, which holds none of the server's lists but
    `removed`. Two quiet repairs keep the file well formed whatever the page did: with people but no owner the first
    person owns the company (as setup.js `owner()` reads it), and a goal whose owner has gone falls to the owner."""
    allowed = ("removed",) if snapshot else SERVER_FIELDS
    _keys(raw, "draft", TOP, allowed + OPTIONAL)
    if raw["step"] not in ("company",) + STEPS:
        raise DraftError("draft.step is not a step")
    company = _keys(raw["company"], "company", ("name", "about", "signals"))
    seq = raw["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int) or not 1 <= seq <= LIMITS["seq"]:
        raise DraftError("draft.seq must be a counter")
    rules = _keys(raw["rules"], "rules", ("mode", "never", "review"))
    tools = _keys(raw["tools"], "tools", ("mode", "connect"))
    if rules["mode"] not in ("defaults", "custom") or tools["mode"] not in ("defaults", "custom"):
        raise DraftError("rules.mode and tools.mode are defaults or custom")
    if rules["review"] not in REVIEW:
        raise DraftError("rules.review is not a review level")
    draft = {
        "step": raw["step"],
        "company": {"name": _text(company["name"], "company.name", LIMITS["name"]),
                    "about": _text(company["about"], "company.about", LIMITS["about"]),
                    "signals": _choices(company["signals"], "company.signals", SIGNALS)},
        "people": [_person(p, i) for i, p in enumerate(_list(raw["people"], "people", LIMITS["people"]))],
        "bots": [_bot(b, i) for i, b in enumerate(b for b in _list(raw["bots"], "bots", LIMITS["bots"])
                                                   if not (isinstance(b, dict) and b.get("template") in RETIRED
                                                           and not b.get("existing")))],
        "goals": [_goal(g, i) for i, g in enumerate(_list(raw["goals"], "goals", LIMITS["goals"]))],
        "rules": {"mode": rules["mode"], "never": [r for r in RULES if r in _choices(rules["never"], "rules.never", RULES)],
                  "review": rules["review"]},
        "tools": {"mode": tools["mode"], "connect": [t for t in TOOLS if t in _choices(tools["connect"], "tools.connect", TOOLS)]},
        "seq": seq,
    }
    if raw.get("strategy") is not None:
        draft["strategy"] = _strategy(raw["strategy"])
    for field in ("people", "bots", "goals"):
        _unique(draft[field], field)
    if draft["people"] and not any(p["owner_id"] for p in draft["people"]):
        draft["people"][0]["owner_id"] = True
    for extra in [p for p in draft["people"] if p["owner_id"]][1:]:
        extra["owner_id"] = False
    if sum(1 for b in draft["bots"] if b.get("first_job")) > 1:
        raise DraftError("only one bot has the first job")
    # A reports-to whose target left the chart (the visitor removed that person or bot on the page) falls back to the
    # line the chart draws anyway, so the draft says what the file says.
    known = chart_ids(draft)
    for bot in draft["bots"]:
        if bot.get("reports_to") is not None and (bot["reports_to"] not in known or bot["reports_to"] == bot["id"]):
            del bot["reports_to"]
    # A seat a person holds keeps that person while they are on the team, and one person holds one seat.
    people, held = {p["id"] for p in draft["people"]}, set()
    for bot in draft["bots"]:
        if "person" in bot:
            if bot["person"] not in people or bot["person"] in held:
                del bot["person"]
            else:
                held.add(bot["person"])
    if loops(draft):
        raise DraftError("a bot's reporting line comes back to itself")
    goal_ids = {goal["id"] for goal in draft["goals"]}
    for goal in draft["goals"]:
        if goal["owner_id"] not in known:
            goal["owner_id"] = owner_id(draft)
        if goal.get("parent") is not None and (goal["parent"] not in goal_ids or goal["parent"] == goal["id"]):
            goal["parent"] = None
        if "bet" in goal and goal["bet"] not in {b["id"] for b in (draft.get("strategy") or {}).get("bets", [])}:
            del goal["bet"]
    if "removed" in raw:
        draft["removed"] = []
        for i, row in enumerate(_list(raw["removed"], "removed", LIMITS["removed"])):
            field = "removed[%d]" % i
            _keys(row, field, ("template", "title"))
            draft["removed"].append({"template": None if row["template"] is None else _id(row["template"], field + ".template"),
                                     "title": _text(row["title"], field + ".title", LIMITS["title"])})
    if not snapshot:
        if raw.get("undo") is not None:
            draft["undo"] = check(raw["undo"], snapshot=True)
        if "chat" in raw:
            draft["chat"] = []
            for i, message in enumerate(_list(raw["chat"], "chat", LIMITS["chat"])):
                field = "chat[%d]" % i
                _keys(message, field, ("who", "text"))
                if message["who"] not in ("me", "bot"):
                    raise DraftError(field + ".who is me or bot")
                draft["chat"].append({"who": message["who"], "text": _text(message["text"], field + ".text", LIMITS["chat_text"])})
        if "pending" in raw:
            pending = _list(raw["pending"], "pending", LIMITS["pending"])
            if len(set(map(str, pending))) != len(pending):
                raise DraftError("pending has a repeated entry")
            draft["pending"] = [_id(p, "pending") for p in pending]
            # A team that is no longer on the chart is no longer waiting on the visitor.
            draft["pending"] = [p for p in draft["pending"] if p in teams_in(draft)]
    if len(json.dumps(draft)) > LIMITS["draft_bytes"]:
        raise DraftError("the draft is too large")
    return draft


def content(draft):
    """What an undo snapshot holds: the draft without `undo`, `chat` and `pending`, and without the page's new-card
    flags."""
    out = copy.deepcopy({key: value for key, value in draft.items() if key in TOP + OPTIONAL or key == "removed"})
    for rows in (out["bots"], out["people"], out["goals"]):
        for row in rows:
            row.pop("isNew", None)
    return out


def same_plan(a, b):
    """Whether two drafts hold the same plan: moving between tabs is not a change to undo."""
    a, b = content(a), content(b)
    a.pop("step"), b.pop("step")
    return a == b
