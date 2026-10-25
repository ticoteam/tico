"""The Tico AI Gateway's lineup: departments, levels, versions, decisions and services.

A chat tier is (department, level, fast): `tico-marketing-medium-fast` always means that tier's current version, and
`tico-marketing-medium-fast-1.0` is one version of it. A version points at one priced model and its price never
changes; a new price is a new version. Decisions (`tico-decide-low|medium|high`, `tico-classify-low`) have no department
and no fast. The ids before the tiers (`tico-cheap-1.1`, `tico-mid-1.0`, `tico-power-1.0`, `tico-cheap-1.0`,
`tico-decide-1.0`, `tico-classify-1.0`) keep working forever at their old price; they are the "old lineup", never offered.

The data is Tico HQ's catalog (`GET <gateway base>/catalog`, schema 1). `SNAPSHOT` is this release's copy of it, used
before the first refresh, offline and when the Gateway is not connected. `refresh` keeps what HQ answers for an hour
and ignores anything malformed, so a new version appears without an app release. backend/providers.py builds its model
rows, prices and long-prompt rates from here, and looks up any other id (a pinned older version, an auto id, a version
that arrived by refresh) through `row_for`, `price_for` and `long_prompt_for`.
"""
import json
import math
import re
import threading
import time

from clients import gateway as G

SCHEMA = 1
DEPARTMENTS = (("general", "General"), ("product", "Product"), ("engineering", "Engineering"),
               ("marketing", "Marketing"), ("sales", "Sales"), ("finance", "Finance"), ("legal", "Legal"),
               ("science", "Science"), ("operations", "Operations"), ("data", "Data"))
DEPARTMENT_IDS = tuple(row[0] for row in DEPARTMENTS)
LEVELS = (("low", "Low"), ("medium", "Medium"), ("high", "High"))
LEVEL_IDS = tuple(row[0] for row in LEVELS)
DEFAULT_DEPARTMENT, DEFAULT_LEVEL = "general", "medium"
FIRST_VERSION = "1.0"
PREFIX = "tico-"

# A bot's department from its org-chart group (templates/groups.yaml); anything else is General.
GROUP_DEPARTMENT = {"sales": "sales", "marketing": "marketing", "support": "operations", "finance": "finance",
                    "operations": "operations", "legal": "legal", "hr": "operations", "product": "product",
                    "engineering": "engineering", "leadership": "general"}

# What a model reads besides text: the catalog's `inputs` ("text", "image", "pdf") on every chat version. Claude reads
# all three; DeepSeek V4.1 Flash (Low Fast) reads text only.
CLAUDE_INPUTS = ("text", "image", "pdf")
INPUT_KINDS = ("text", "image", "pdf")

# What one level costs and runs on at version 1.0, in USD per 1M tokens: (input, cached input, output). Every department
# maps to the same rows at 1.0: there is no department benchmark yet that justifies another pick. Haiku's rates go up
# for a prompt past `long`'s limit.
ROWS = {
    "low": {"model": "Claude Haiku 5.5", "price": (0.15, 0.015, 0.75), "long": (100_000, (0.75, 0.075, 3.75)),
            "inputs": CLAUDE_INPUTS},
    "medium": {"model": "Claude Sonnet 5.5", "price": (2.50, 0.25, 12.50), "long": None, "inputs": CLAUDE_INPUTS},
    "high": {"model": "Claude Opus 5.5", "price": (5.0, 0.50, 25.0), "long": None, "inputs": CLAUDE_INPUTS},
}
# The row a fast tier points at (`tico-fast-low-1.0`, `tico-fast-high-1.0` at Tico HQ). Fast is measurably faster and may
# cost more. Low Fast is DeepSeek V4.1 Flash; Medium Fast and High Fast are both Claude Opus 5.5 in Anthropic's fast mode,
# the only fast route at or above Medium's quality. Cached input is 10% of input for both (HQ prices DeepSeek cache reads at a tenth).
FAST_ROWS = {
    "low": {"model": "DeepSeek V4.1 Flash", "price": (0.45, 0.045, 1.80), "long": None, "inputs": ("text",)},
    "medium": {"model": "Claude Opus 5.5 (fast mode)", "price": (10.0, 1.0, 50.0), "long": None, "inputs": CLAUDE_INPUTS},
    "high": {"model": "Claude Opus 5.5 (fast mode)", "price": (10.0, 1.0, 50.0), "long": None, "inputs": CLAUDE_INPUTS},
}

DECISIONS = (
    {"id": "tico-decide-low", "level": "low", "name": "Decide · Low", "model": "Jev 1.13", "price": (0.06, 0.006, 0.0),
     "types": ["noul", "choice", "score"],
     "when": "Yes/no, which-one and how-much questions about a state; fastest and cheapest. The default."},
    {"id": "tico-decide-medium", "level": "medium", "name": "Decide · Medium", "model": "Claude Sonnet 5.5", "price": (2.50, 0.25, 12.50),
     "types": ["noul", "choice", "score"], "when": "The same questions when Low gets close calls wrong; slower."},
    {"id": "tico-decide-high", "level": "high", "name": "Decide · High", "model": "Claude Opus 5.5", "price": (5.0, 0.50, 25.0),
     "types": ["noul", "choice", "score"], "when": "The same questions that need careful judgment; slowest."},
    {"id": "tico-classify-low", "level": "low", "name": "Classify · Low", "model": "Jev 1.13", "price": (0.06, 0.006, 0.0),
     "types": ["choice"], "when": "Sorting one text into one of your labels."},
)
# Medium and High serve the same typed contract from a language model, at that model's price.

SERVICES = (
    {"id": "decide", "name": "Decide · Classify", "for": "Calibrated answers to typed questions about a state",
     "price": "From $0.06 per 1M tokens", "status": "live", "tools": ["hub_decision_ask", "hub_classify"]},
    {"id": "inbox.email", "name": "Inbox · Email", "for": "Receive sign-in codes and notices",
     "price": "Free addresses · $0.14 per 1,000 received", "status": "live",
     "tools": ["hub_inbound_create_email", "hub_inbound_wait"]},
    {"id": "inbox.numbers", "name": "Inbox · Numbers", "for": "Receive texts and codes on a number",
     "price": "From $14 per 30 days", "status": "approval", "tools": []},
    {"id": "search", "name": "Search", "for": "Web search with sources and a short answer",
     "price": "Tokens like Low · $0.00875 per search", "status": "live",
     "tools": ["hub_gateway_search"]},
    {"id": "fetch", "name": "Fetch", "for": "Read a web page or PDF", "price": "$1.25 per 1,000 pages + tokens",
     "status": "live", "tools": ["hub_gateway_fetch"]},
    {"id": "transcribe", "name": "Transcribe", "for": "Speech to text", "price": "", "status": "next", "tools": []},
    {"id": "send-email", "name": "Send email", "for": "Send from a company address", "price": "", "status": "later",
     "tools": []},
    {"id": "enrich", "name": "Enrich", "for": "Facts about a company or person", "price": "", "status": "later",
     "tools": []},
    {"id": "voice", "name": "Voice", "for": "Calls and spoken replies", "price": "", "status": "later", "tools": []},
    {"id": "mail", "name": "Physical mail and registered agent", "for": "A street address that takes post",
     "price": "", "status": "planned", "tools": []},
)
STATUSES = ("live", "approval", "next", "later", "planned")

# What each level is for, in a line a bot or a person reads.
LEVEL_WHEN = {"low": "Fast and cheap: triage, sorting, short replies, routine checks.",
              "medium": "Everyday work: writing, analysis, most bot turns.",
              "high": "Hard problems: long plans, tricky code, careful reasoning."}

# The ids from before the tiers, with the tier row each one is (their price and behaviour never change).
OLD = {"tico-cheap-1.1": ("Cheap 1.1", "low"), "tico-cheap-1.0": ("Cheap 1.0", "low"),
       "tico-mid-1.0": ("Mid 1.0", "medium"), "tico-power-1.0": ("Power 1.0", "high")}
OLD_DECISIONS = {"tico-decide-1.0": "tico-decide-low-1.0", "tico-classify-1.0": "tico-classify-low-1.0"}
OLD_SUFFIX = " (old lineup)"

_VERSION = re.compile(r"\d{1,3}\.\d{1,3}")
_WORD = re.compile(r"[a-z][a-z0-9]*")
_NAME = re.compile(r"[^\x00-\x1f]{1,120}")


def name_of(department, level, fast=False):
    """`Marketing · Medium · Fast`, from ids."""
    departments, levels = dict(DEPARTMENTS), dict(LEVELS)
    return " · ".join([departments.get(department, department.title()), levels.get(level, level.title())]
                      + (["Fast"] if fast else []))


def tier_id(department, level, fast=False):
    """The auto id of a tier: `tico-marketing-medium-fast`."""
    return f"{PREFIX}{department}-{level}" + ("-fast" if fast else "")


def _price(row):
    low, high, out = row["price"]
    return {"input": low, "output": out, "cached_input": high}


def _long(row):
    if not row["long"]:
        return None
    limit, (low, high, out) = row["long"]
    return {"from": limit, "input": low, "output": out, "cached_input": high}


def _version(row, ident, version, released=""):
    return {"version": version, "id": ident, "model": row["model"], "price": _price(row), "long_prompt": _long(row),
            "inputs": list(row["inputs"]), **({"released": released} if released else {})}


def _snapshot():
    models = []
    for department, _ in DEPARTMENTS:
        for level, _ in LEVELS:
            for fast in (False, True):
                ident = tier_id(department, level, fast)
                row = (FAST_ROWS if fast else ROWS)[level]
                models.append({"id": ident, "department": department, "level": level, "fast": fast,
                               "name": name_of(department, level, fast), "current": FIRST_VERSION,
                               "versions": [_version(row, f"{ident}-{FIRST_VERSION}", FIRST_VERSION)]})
    decisions = [{"id": row["id"], "level": row["level"], "name": row["name"], "current": FIRST_VERSION,
                  "types": row["types"], "when": row["when"],
                  "versions": [{"version": FIRST_VERSION, "id": f"{row['id']}-{FIRST_VERSION}", "model": row["model"],
                                "price": {"input": row["price"][0], "output": row["price"][2],
                                          "cached_input": row["price"][1]}}] if row["price"] else []}
                 for row in DECISIONS]
    return {"schema": SCHEMA, "as_of": "2026-10-25",
            "departments": [{"id": ident, "name": name} for ident, name in DEPARTMENTS],
            "levels": [{"id": ident, "name": name} for ident, name in LEVELS],
            "models": models, "decisions": decisions, "services": [dict(row) for row in SERVICES],
            "changes": [{"date": "2026-10-25", "text": "Department tiers with Low, Medium, High and Fast."}]}


SNAPSHOT = _snapshot()


# ----------------------------------------------------------------------------- validation
def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _text(value, default=""):
    return value if isinstance(value, str) and _NAME.fullmatch(value) else default


def _clean_price(price):
    if not isinstance(price, dict) or not _number(price.get("input")) or not _number(price.get("output")):
        return None
    cached = price.get("cached_input")
    return {"input": price["input"], "output": price["output"],
            "cached_input": cached if _number(cached) else round(price["input"] * 0.1, 6)}


def _clean_long(value):
    if not isinstance(value, dict) or not all(_number(value.get(k)) for k in ("from", "input", "output")):
        return None
    cached = value.get("cached_input")
    return {"from": int(value["from"]), "input": value["input"], "output": value["output"],
            "cached_input": cached if _number(cached) else round(value["input"] * 0.1, 6)}


def _clean_inputs(value):
    """The kinds a version reads (`text`, `image`, `pdf`) from HQ's `inputs`, in HQ's order and without repeats, or
    None when HQ names none (an older catalog): the version is then never claimed to read anything but text."""
    if not isinstance(value, list):
        return None
    kinds = list(dict.fromkeys(k for k in value if k in INPUT_KINDS))
    return kinds or None


def _clean_versions(ident, rows):
    """The well-formed versions of the tier `ident`, each with the id its version makes; the rest are ignored."""
    out, seen = [], set()
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        version, price = row.get("version"), _clean_price(row.get("price"))
        if (not isinstance(version, str) or not _VERSION.fullmatch(version) or version in seen
                or row.get("id") != f"{ident}-{version}" or price is None):
            continue
        seen.add(version)
        item = {"version": version, "id": row["id"], "model": _text(row.get("model")), "price": price,
                "long_prompt": _clean_long(row.get("long_prompt"))}
        if inputs := _clean_inputs(row.get("inputs")):
            item["inputs"] = inputs
        for key in ("context", "max_output"):
            if isinstance(row.get(key), int) and not isinstance(row.get(key), bool) and row[key] > 0:
                item[key] = row[key]
        out.append(item)
    return out


def _version_key(version):
    major, _, minor = version.partition(".")
    return int(major), int(minor)


def _current(row, versions):
    """The tier's current version: the one HQ names when it is among the good ones, else the newest."""
    named = row.get("current")
    if isinstance(named, str) and any(v["version"] == named for v in versions):
        return named
    return max(versions, key=lambda v: _version_key(v["version"]))["version"]


def validate(doc):
    """The catalog in `doc` (HQ's answer, parsed) with every malformed entry left out, or None when `doc` is not a
    schema 1 catalog or holds no usable tier. A tier needs a department and a level, an id that is its department,
    level and fast flag, and at least one version whose id follows from it and whose price is numbers."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None
    departments = [{"id": row["id"], "name": _text(row.get("name"), row["id"].title()),
}
                   for row in doc.get("departments") or [] if isinstance(row, dict)
                   and isinstance(row.get("id"), str) and _WORD.fullmatch(row["id"])]
    levels = [{"id": row["id"], "name": _text(row.get("name"), row["id"].title())}
              for row in doc.get("levels") or [] if isinstance(row, dict) and row.get("id") in LEVEL_IDS]
    known = {row["id"] for row in departments}
    models = []
    for row in doc.get("models") or []:
        if not isinstance(row, dict) or row.get("department") not in known or row.get("level") not in LEVEL_IDS \
                or not isinstance(row.get("fast"), bool) \
                or row.get("id") != tier_id(row["department"], row["level"], row["fast"]):
            continue
        versions = _clean_versions(row["id"], row.get("versions"))
        if versions:
            models.append({"id": row["id"], "department": row["department"], "level": row["level"],
                           "fast": row["fast"], "name": _text(row.get("name"),
                                                              name_of(row["department"], row["level"], row["fast"])),
                           "current": _current(row, versions),
                           "versions": sorted(versions, key=lambda v: _version_key(v["version"]))})
    if not models:
        return None
    decisions = []
    for row in doc.get("decisions") or []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) \
                or not re.fullmatch(r"tico-(decide|classify)-(low|medium|high)", row["id"]):
            continue
        versions = _clean_versions(row["id"], row.get("versions"))
        if versions:
            decisions.append({"id": row["id"], "level": row["id"].rsplit("-", 1)[1],
                              "name": _text(row.get("name"), row["id"]), "current": _current(row, versions),
                              "types": [t for t in row.get("types") or [] if t in ("noul", "choice", "score")],
                              "when": _text(row.get("when")), "versions": versions})
    services = []
    for row in doc.get("services") or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str) and _text(row.get("name")) \
                and row.get("status") in STATUSES:
            services.append({"id": row["id"][:60], "name": row["name"], "for": _text(row.get("for")),
                             "price": _text(row.get("price")), "status": row["status"],
                             "tools": [t for t in row.get("tools") or [] if isinstance(t, str) and len(t) < 60][:12]})
    changes = [{"date": _text(row.get("date")), "text": _text(row.get("text"))} for row in doc.get("changes") or []
               if isinstance(row, dict) and _text(row.get("text"))][:20]
    return {"schema": SCHEMA, "as_of": _text(doc.get("as_of")),
            "departments": departments or SNAPSHOT["departments"], "levels": levels or SNAPSHOT["levels"],
            "models": models, "decisions": decisions or SNAPSHOT["decisions"], "services": services or SNAPSHOT["services"],
            "changes": changes}


# ----------------------------------------------------------------------------- the catalog in use
REFRESH_SECONDS = 3600
RETRY_SECONDS = 300
MAX_BYTES = 2_000_000
TIMEOUT = 5
_lock = threading.Lock()
_state = {"catalog": None, "at": 0.0, "tried": 0.0, "running": False}
_indexes = {}


def catalog():
    """The catalog in use: the last good refresh, else this release's snapshot."""
    return _state["catalog"] or SNAPSHOT


def reset():
    """Forget any refresh (tests)."""
    with _lock:
        _state.update(catalog=None, at=0.0, tried=0.0, running=False)


def fetch(base, opener=None, timeout=TIMEOUT):
    """HQ's catalog from `<base>/catalog` (public, no key) validated, or None when it cannot be read or is malformed."""
    try:
        import urllib.request
        request = urllib.request.Request(G.safe_base(base) + "/catalog", headers={"Accept": "application/json"})
        with (opener or G._send)(request, timeout) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            return None
        return validate(json.loads(body))
    except (ValueError, urllib.error.URLError, OSError, TimeoutError):
        return None


def refresh(base, opener=None, now=None, force=False):
    """Fetch HQ's catalog unless the last good one is less than an hour old (`force` asks anyway); keeps it when it
    validates. Returns the catalog in use. A failed fetch is not asked again for five minutes."""
    now = time.monotonic() if now is None else now
    with _lock:
        if not force and ((_state["catalog"] and now - _state["at"] < REFRESH_SECONDS)
                          or now - _state["tried"] < RETRY_SECONDS):
            return catalog()
        _state["tried"] = now
    found = fetch(base, opener)
    if found:
        with _lock:
            _state.update(catalog=found, at=now)
    return catalog()


def refresh_soon(base):
    """The catalog in use now, asking HQ for a fresh one on a background thread when the cached one is stale. For
    screens and tools: nobody waits on the network. `base` is None while the Gateway is not connected."""
    now = time.monotonic()
    if base and not _state["running"] and not (_state["catalog"] and now - _state["at"] < REFRESH_SECONDS) \
            and now - _state["tried"] >= RETRY_SECONDS:
        def run():
            try:
                refresh(base)
            finally:
                _state["running"] = False
        _state["running"] = True
        threading.Thread(target=run, name="gateway-catalog", daemon=True).start()
    return catalog()


# ----------------------------------------------------------------------------- resolving an id
def _index(cat):
    """{id: entry} for every auto and pinned id of `cat`'s tiers and decisions, built once per catalog."""
    found = _indexes.get(id(cat))
    if found and found[0] is cat:
        return found[1]
    table = {}
    for kind, rows in (("chat", cat["models"]), ("decide", cat["decisions"])):
        for row in rows:
            current = next((v for v in row["versions"] if v["version"] == row["current"]), None)
            if current is None:             # a decision tier this release has no price for yet
                continue
            table[row["id"]] = {"tier": row, "version": current, "auto": True, "kind": kind}
            for version in row["versions"]:
                table[version["id"]] = {"tier": row, "version": version, "auto": False, "kind": kind}
    _indexes.clear()
    _indexes[id(cat)] = (cat, table)
    return table


def _price_tuple(price):
    return (price["input"], price["cached_input"], price["output"])


def _long_tuple(long):
    return (long["from"], (long["input"], long["cached_input"], long["output"])) if long else None


def resolve(model, cat=None):
    """What an id is, or None when it is not a Tico id: {id, tier (the auto id), kind (chat|decide), name, model (what
    runs underneath), label (the name without a version), version, auto, price {input, cached_input, output}, long_prompt, inputs (what it reads: text, image, pdf; text alone when the catalog names none), department, level, fast, old}.
    An auto id answers with the tier's current version; an old-lineup id with its own price, marked `old`."""
    model = str(model or "").strip().lower()
    if not model.startswith(PREFIX):
        return None
    if model in OLD:
        label, level = OLD[model]
        version = model.rsplit("-", 1)[1]
        row = ROWS[level]
        return {"id": model, "tier": model, "kind": "chat", "name": label + OLD_SUFFIX, "label": label, "model": row["model"],
                "version": version, "auto": False, "price": _price(row), "long_prompt": _long(row)
                if model == "tico-cheap-1.1" else None, "department": "", "level": level, "fast": False, "old": True,
                "inputs": list(row["inputs"])}
    if model in OLD_DECISIONS:
        row = next(r for r in DECISIONS if r["id"] == OLD_DECISIONS[model].rsplit("-", 1)[0])
        return {"id": model, "tier": model, "kind": "decide", "name": row["name"] + " 1.0" + OLD_SUFFIX, "label": row["name"],
                "model": row["model"], "version": FIRST_VERSION, "auto": False,
                "price": {"input": row["price"][0], "output": row["price"][2], "cached_input": row["price"][1]},
                "long_prompt": None, "department": "", "level": row["level"], "fast": False, "old": True,
                "inputs": ["text"]}
    entry = _index(cat or catalog()).get(model)
    if not entry:
        return None
    tier, version = entry["tier"], entry["version"]
    suffix = " (auto)" if entry["auto"] else " " + version["version"]
    return {"id": model, "tier": tier["id"], "kind": entry["kind"], "name": tier["name"] + suffix, "label": tier["name"],
            "model": version["model"], "version": version["version"], "auto": entry["auto"],
            "price": version["price"], "long_prompt": version.get("long_prompt"),
            "inputs": list(version.get("inputs") or ["text"]),
            "department": tier.get("department", ""), "level": tier["level"], "fast": bool(tier.get("fast")),
            "old": False, "current": tier["current"]}


def price_for(model):
    """(input, cached input, output) USD per 1M tokens for any Tico id, or None."""
    found = resolve(model)
    return _price_tuple(found["price"]) if found and found["price"] else None


def long_prompt_for(model):
    """(prompt tokens, (input, cached input, output)) above which a Tico id's rates go up, or None."""
    found = resolve(model)
    return _long_tuple(found["long_prompt"]) if found else None


def efforts_of(level):
    return ("low", "high", "max"), ("low" if level == "low" else "high")


def row_for(model):
    """A providers.MODEL_CATALOG-style row for any Tico id this release or the catalog knows, or None."""
    found = resolve(model)
    if not found:
        return None
    efforts, default = efforts_of(found["level"])
    row = {"id": found["id"], "label": found["name"], "runtime": "pi", "provider": "tico",
           "description": (found["model"] + " underneath") if found["model"] else "",
           "efforts": efforts, "default_effort": default}
    if found["old"]:
        row["deprecated"] = True
        row["lineup"] = "old"
    elif found["kind"] == "decide":
        row["deprecated"] = True        # a decision tier is asked for by the judge, never picked as a bot's model
    else:
        row["tier"] = {"department": found["department"], "level": found["level"], "fast": found["fast"],
                       "version": found["version"], "auto": found["auto"]}
    return row


def listed_rows():
    """The rows MODEL_CATALOG lists for the Gateway: each snapshot tier at its current pinned version, with General
    Medium the one a bot gets when nobody chose, and the old lineup after them (retired, not offered)."""
    rows = []
    for tier in SNAPSHOT["models"]:
        row = row_for(f"{tier['id']}-{tier['current']}")
        if tier["id"] == tier_id(DEFAULT_DEPARTMENT, DEFAULT_LEVEL):
            row["recommended"] = True
        rows.append(row)
    rows += [row_for(old) for old in OLD]
    return rows


# ----------------------------------------------------------------------------- a bot's pick
def department_of(group):
    """The Gateway department of an org-chart department id (`sales`, `support`...); General for any other."""
    return GROUP_DEPARTMENT.get(str(group or ""), DEFAULT_DEPARTMENT)


def current_version(department, level, fast=False, cat=None):
    cat = cat or catalog()
    tier = _index(cat).get(tier_id(department, level, fast))
    return tier["tier"]["current"] if tier else FIRST_VERSION


def default_model(department=DEFAULT_DEPARTMENT, level=DEFAULT_LEVEL, fast=False):
    """What a bot on the Gateway runs on when nobody chose: its department's tier at the current version, pinned. A
    department the catalog does not have is General."""
    cat = catalog()
    if tier_id(department, level, fast) not in _index(cat):
        department = DEFAULT_DEPARTMENT
    if tier_id(department, level, fast) not in _index(cat):
        return f"{tier_id(DEFAULT_DEPARTMENT, DEFAULT_LEVEL)}-{FIRST_VERSION}"
    return f"{tier_id(department, level, fast)}-{current_version(department, level, fast, cat)}"


def clean_block(value):
    """A bot.yaml `gateway:` block (`level: low|medium|high`, `fast: true`) as the two settings it holds, or None when it
    says nothing usable."""
    if not isinstance(value, dict):
        return None
    out = {}
    if value.get("level") in LEVEL_IDS:
        out["level"] = value["level"]
    if value.get("fast") is True:
        out["fast"] = True
    return out or None


def bot_model(department, gateway=None):
    """The Gateway model for a bot in `department`, from its `gateway:` block in bot.yaml (`level`, `fast`)."""
    gateway = gateway if isinstance(gateway, dict) else {}
    level = gateway.get("level") if gateway.get("level") in LEVEL_IDS else DEFAULT_LEVEL
    return default_model(department, level, gateway.get("fast") is True)


# ----------------------------------------------------------------------------- views
def view(cat=None):
    """What the model picker needs, small: the departments and levels, and for each tier its current version and the
    versions (id, version, model, price) to pick from."""
    cat = cat or catalog()
    return {"as_of": cat.get("as_of", ""), "departments": [{"id": r["id"], "name": r["name"]} for r in cat["departments"]],
            "levels": cat["levels"],
            "tiers": {row["id"]: {"name": row["name"], "department": row.get("department", ""), "level": row["level"],
                                  "fast": bool(row.get("fast")), "current": row["current"], "versions": [
                {"version": v["version"], "id": v["id"], "model": v["model"], "price": v["price"]}
                for v in reversed(row["versions"])]} for row in cat["models"]}}


def lineup(query="", cat=None):
    """The catalog as an agent reads it: tiers with a line on when to use each, decisions and services; `query` keeps
    the entries whose words contain it. Prices are USD per 1M tokens."""
    cat = cat or catalog()
    words = [w for w in re.split(r"\W+", str(query or "").lower()) if w]
    names = {r["id"]: r["name"] for r in cat["departments"]}

    def keep(*texts):
        hay = " ".join(texts).lower()
        return all(w in hay for w in words)
    models = []
    for row in cat["models"]:
        now = next(v for v in row["versions"] if v["version"] == row["current"])
        when = LEVEL_WHEN.get(row["level"], "") + (" Faster, and may cost more." if row["fast"] else "")
        if keep(row["name"], now["model"], row["department"], names.get(row["department"], ""), when):
            models.append({"id": row["id"], "pinned": now["id"], "name": row["name"], "model": now["model"],
                           "version": now["version"], "price": now["price"], "when": when})
    decisions = [{"id": row["id"], "name": row["name"], "when": row["when"], "types": row["types"],
                  "model": row["versions"][0]["model"] if row["versions"] else "",
                  "pinned": next((v["id"] for v in row["versions"] if v["version"] == row["current"]), ""),
                  "price": row["versions"][0]["price"] if row["versions"] else None}
                 for row in cat["decisions"] if keep(row["name"], row["when"], row["id"])]
    services = [{"id": row["id"], "name": row["name"], "when": row["for"], "price": row["price"],
                 "status": row["status"], "tools": row["tools"]}
                for row in cat["services"] if keep(row["name"], row["for"], row["id"], " ".join(row["tools"]))]
    return {"as_of": cat.get("as_of", ""), "models": models, "decisions": decisions, "services": services,
            "note": "A model id without a version always means the tier's current version; add -<version> to pin it."}


def money(price):
    """`$2.50 / $12.50 per 1M` from a price dict."""
    if not price:
        return ""
    return f"${price['input']:.2f} / ${price['output']:.2f} per 1M"
