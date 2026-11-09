"""The Tico AI Gateway's lineup: levels, versions, decisions and services.

A chat tier is (level, fast): five levels, X-Low to X-High, each with a Fast switch. `tico-medium-fast` always means
that tier's current version, and `tico-medium-fast-1.0` is one version of it. A version points at one priced model and
its price never changes; a new price is a new version. Decisions (`tico-decide-low|medium|high`) have no fast. The
meeting tier (`tico-meeting`) is the live-meeting bot's reply model, not a bot's pick.

The ids from before this lineup (department tiers, and the first lineup's
`tico-mid-1.0` and friends) are rewritten once to the new ids (migration 20261106*_gateway_lineup3_ids) and mapped on
read by `new_id`, so a value an older peer writes still resolves.

The data is Tico HQ's catalog (`GET <gateway base>/catalog`, schema 2). `SNAPSHOT` (backend/gateway_catalog.json) is
this release's copy of it, used before the first refresh, offline and when the Gateway is not connected; a catalog of
any other schema is ignored. `refresh` keeps what HQ answers for an hour and ignores anything malformed, so a new
version appears without an app release. backend/providers.py builds its model rows, prices and long-prompt rates from
here, and looks up any other id (a pinned older version, an auto id, a version that arrived by refresh) through
`row_for`, `price_for` and `long_prompt_for`.
"""

import json
import math
import pathlib
import re
import threading
import time

from clients import gateway as G

SCHEMA = 2
LEVELS = (("xlow", "X-Low"), ("low", "Low"), ("medium", "Medium"), ("high", "High"), ("xhigh", "X-High"))
LEVEL_IDS = tuple(row[0] for row in LEVELS)
DEFAULT_LEVEL = "medium"
FIRST_VERSION = "1.0"
PREFIX = "tico-"

# What a model reads besides text: the catalog's `inputs` ("text", "image", "pdf") on every chat version.
INPUT_KINDS = ("text", "image", "pdf")

# The meeting tier (HQ's `tico-meeting`, pinned `tico-meeting-1.0`): a chat tier with no level or fast. It is the reply
# model of the live-meeting bot (Inception Mercury 3, text only, 1M context), chosen in Settings, not a bot's.
MEETING_TIER = "tico-meeting"
MEETING_NAME = "Meeting"

STATUSES = ("live", "approval", "next", "later", "planned")

# What each level is for, in a line a bot or a person reads.
LEVEL_WHEN = {
    "xlow": "Cheapest: bulk sorting, tagging, short checks.",
    "low": "Fast and cheap: triage, sorting, short replies, routine checks.",
    "medium": "Everyday work: writing, analysis, most bot turns.",
    "high": "Hard problems: long plans, tricky code, careful reasoning.",
    "xhigh": "The hardest problems, when High is not enough; the dearest.",
}
# What each Decide level is for (HQ's catalog names none).
DECISION_WHEN = {
    "tico-decide-low": "Yes/no, which-one and how-much questions about a state; fastest and cheapest. The default.",
    "tico-decide-medium": "The same questions when Low gets close calls wrong; slower.",
    "tico-decide-high": "The same questions that need careful judgment; slowest.",
}

# ----------------------------------------------------------------------------- ids from before this lineup
# The ten department ids of the lineup before this one: `tico-{department}-{level}[-fast][-{version}]`.
OLD_DEPARTMENTS = (
    "general",
    "product",
    "engineering",
    "marketing",
    "sales",
    "finance",
    "legal",
    "science",
    "operations",
    "data",
)
_OLD_TIER = re.compile(r"tico-(?:" + "|".join(OLD_DEPARTMENTS) + r")-(low|medium|high)(-fast)?(-\d{1,3}\.\d{1,3})?")
# The first lineup's ids and the retired Classify, each with the id it is now. Remove with the department ids once every
# install runs this lineup's app (HQ keeps them as hidden aliases for one release window).
ALIASES = {
    "tico-cheap-1.1": "tico-low-1.0",
    "tico-cheap-1.0": "tico-low-1.0",
    "tico-mid-1.0": "tico-medium-1.0",
    "tico-power-1.0": "tico-high-1.0",
    "tico-decide-1.0": "tico-decide-low-1.0",
    "tico-classify-1.0": "tico-decide-low",
    "tico-classify-low": "tico-decide-low",
    "tico-classify-low-1.0": "tico-decide-low",
}


def new_id(model):
    """The id `model` is in this lineup: a department tier's id without its department (`tico-sales-medium-fast-1.0` ->
    `tico-medium-fast-1.0`), a first-lineup or Classify id by `ALIASES`, and any other value as it is (so a new id maps
    to itself and the mapping is idempotent). The meeting tier is unchanged."""
    if not isinstance(model, str):
        return model
    key = model.strip().lower()
    if key in ALIASES:
        return ALIASES[key]
    found = _OLD_TIER.fullmatch(key)
    if found:
        return PREFIX + found.group(1) + (found.group(2) or "") + (found.group(3) or "")
    return model


_VERSION = re.compile(r"\d{1,3}\.\d{1,3}")
_NAME = re.compile(r"[^\x00-\x1f]{1,120}")


def name_of(level, fast=False):
    """`Medium · Fast`, from ids."""
    return " · ".join([dict(LEVELS).get(level, level.title())] + (["Fast"] if fast else []))


def tier_id(level, fast=False):
    """The auto id of a tier: `tico-medium-fast`."""
    return f"{PREFIX}{level}" + ("-fast" if fast else "")


DEFAULT_TIER = tier_id(DEFAULT_LEVEL)


# This release's copy of HQ's catalog: HQ's `build()` (ticoteam/tico-services gateway/catalog.py) written out as it
# answers, so the copy and a refresh never differ. Prices, models and sources are HQ's (gateway/tiers.py there).
SNAPSHOT = json.loads((pathlib.Path(__file__).with_name("gateway_catalog.json")).read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- validation
def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _text(value, default=""):
    return value if isinstance(value, str) and _NAME.fullmatch(value) else default


def _clean_details(value):
    """A service's `details` (`[{label, value}]`, short text each), up to 12; [] when HQ sent none or none are usable."""
    rows = value if isinstance(value, list) else []
    return [
        {"label": row["label"], "value": row["value"]}
        for row in rows
        if isinstance(row, dict) and _text(row.get("label")) and _text(row.get("value"))
    ][:12]


def _clean_price(price):
    if not isinstance(price, dict) or not _number(price.get("input")) or not _number(price.get("output")):
        return None
    cached = price.get("cached_input")
    return {
        "input": price["input"],
        "output": price["output"],
        "cached_input": cached if _number(cached) else round(price["input"] * 0.1, 6),
    }


def _clean_long(value):
    if not isinstance(value, dict) or not all(_number(value.get(k)) for k in ("from", "input", "output")):
        return None
    cached = value.get("cached_input")
    return {
        "from": int(value["from"]),
        "input": value["input"],
        "output": value["output"],
        "cached_input": cached if _number(cached) else round(value["input"] * 0.1, 6),
    }


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
        if (
            not isinstance(version, str)
            or not _VERSION.fullmatch(version)
            or version in seen
            or row.get("id") != f"{ident}-{version}"
            or price is None
        ):
            continue
        seen.add(version)
        item = {
            "version": version,
            "id": row["id"],
            "model": _text(row.get("model")),
            "price": price,
            "long_prompt": _clean_long(row.get("long_prompt")),
        }
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
    schema 2 catalog or holds no usable tier. A tier needs a level, an id that is its level and fast flag, and at least
    one version whose id follows from it and whose price is numbers. The meeting tier (`tico-meeting`) has no level and
    no fast; it is kept beside the level tiers, else the release's copy."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None
    levels = [
        {"id": row["id"], "name": _text(row.get("name"), dict(LEVELS)[row["id"]])}
        for row in doc.get("levels") or []
        if isinstance(row, dict) and row.get("id") in LEVEL_IDS
    ]
    models, meeting = [], None
    for row in doc.get("models") or []:
        if not isinstance(row, dict):
            continue
        if row.get("id") == MEETING_TIER:
            # The meeting tier's own shape: no level, not fast. Anything else is a malformed row.
            if row.get("level") is not None or row.get("fast") is not False:
                continue
            level, fast = None, False
            name = _text(row.get("name"), MEETING_NAME)
        elif (
            row.get("level") in LEVEL_IDS
            and isinstance(row.get("fast"), bool)
            and row.get("id") == tier_id(row["level"], row["fast"])
        ):
            level, fast = row["level"], row["fast"]
            name = _text(row.get("name"), name_of(level, fast))
        else:
            continue
        versions = _clean_versions(row["id"], row.get("versions"))
        if versions:
            item = {
                "id": row["id"],
                "level": level,
                "fast": fast,
                "name": name,
                "current": _current(row, versions),
                "versions": sorted(versions, key=lambda v: _version_key(v["version"])),
            }
            if level is None:
                meeting = item
            else:
                models.append(item)
    if not models:
        return None
    if meeting is None:
        # HQ's catalog without the meeting tier: the release's copy stands.
        meeting = next(row for row in SNAPSHOT["models"] if row["id"] == MEETING_TIER)
    models.append(meeting)
    decisions = []
    for row in doc.get("decisions") or []:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not re.fullmatch(r"tico-decide-(low|medium|high)", row["id"])
        ):
            continue
        versions = _clean_versions(row["id"], row.get("versions"))
        if versions:
            decisions.append(
                {
                    "id": row["id"],
                    "level": row["id"].rsplit("-", 1)[1],
                    "name": _text(row.get("name"), row["id"]),
                    "current": _current(row, versions),
                    "types": [t for t in row.get("types") or [] if t in ("noul", "choice", "score")],
                    "when": _text(row.get("when")) or DECISION_WHEN.get(row["id"], ""),
                    "versions": versions,
                }
            )
    services = []
    for row in doc.get("services") or []:
        if (
            isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and _text(row.get("name"))
            and row.get("status") in STATUSES
        ):
            services.append(
                {
                    "id": row["id"][:60],
                    "name": row["name"],
                    "for": _text(row.get("for")),
                    "price": _text(row.get("price")),
                    "status": row["status"],
                    "tools": [t for t in row.get("tools") or [] if isinstance(t, str) and len(t) < 60][:12],
                    "details": _clean_details(row.get("details")),
                }
            )
    changes = [
        {"date": _text(row.get("date")), "text": _text(row.get("text"))}
        for row in doc.get("changes") or []
        if isinstance(row, dict) and _text(row.get("text"))
    ][:20]
    return {
        "schema": SCHEMA,
        "as_of": _text(doc.get("as_of")),
        "levels": levels or SNAPSHOT["levels"],
        "models": models,
        "decisions": decisions or SNAPSHOT["decisions"],
        "services": services or SNAPSHOT["services"],
        "changes": changes,
    }


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
    except urllib.error.HTTPError as error:
        with error:
            return None
    except (ValueError, urllib.error.URLError, OSError, TimeoutError):
        return None


def refresh(base, opener=None, now=None, force=False):
    """Fetch HQ's catalog unless the last good one is less than an hour old (`force` asks anyway); keeps it when it
    validates. Returns the catalog in use. A failed fetch is not asked again for five minutes."""
    now = time.monotonic() if now is None else now
    with _lock:
        if not force and (
            (_state["catalog"] and now - _state["at"] < REFRESH_SECONDS) or now - _state["tried"] < RETRY_SECONDS
        ):
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
    if (
        base
        and not _state["running"]
        and not (_state["catalog"] and now - _state["at"] < REFRESH_SECONDS)
        and now - _state["tried"] >= RETRY_SECONDS
    ):

        def run():
            try:
                refresh(base)
            finally:
                _state["running"] = False

        _state["running"] = True
        threading.Thread(target=run, name="gateway-catalog", daemon=True).start()
    return catalog()


# ----------------------------------------------------------------------------- resolving an id
def _model_key(text):
    """A model's name with case and punctuation dropped, so `gpt-6.1-terra` and `GPT-6.1 Terra` are the same model."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


# The models a Tico tier may not run here: the held rows in providers.MODEL_CATALOG (Dev 693). A tier whose current
# version runs one is left out whole, and a pinned version that runs one is left out, so the Gateway's index, picker,
# lineup and resolver never offer the id, whatever HQ publishes.
HELD_MODELS = frozenset(_model_key(m) for m in ("gpt-6.1-terra",))


def _held(version):
    return _model_key(version.get("model")) in HELD_MODELS


def live_row(row):
    """The tier as a customer may see it, or None: its current version must run no held model, and no held version is kept."""
    current = next((v for v in row["versions"] if v["version"] == row["current"]), None)
    if current is None or _held(current):
        return None
    return {**row, "versions": [v for v in row["versions"] if not _held(v)]}


def _index(cat):
    """{id: entry} for every auto and pinned id of `cat`'s tiers and decisions, built once per catalog."""
    found = _indexes.get(id(cat))
    if found and found[0] is cat:
        return found[1]
    table = {}
    for kind, rows in (("chat", cat["models"]), ("decide", cat["decisions"])):
        for raw in rows:
            row = live_row(raw)
            if row is None:  # no current version, or one that runs a held model: not offered
                continue
            current = next(v for v in row["versions"] if v["version"] == row["current"])
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
    """What an id is, or None when it is not a Tico id: {id (this lineup's: `new_id`), tier (the auto id), kind
    (chat|decide), name, model (what runs underneath), label (the name without a version), version, auto, price {input,
    cached_input, output}, long_prompt, inputs (what it reads: text, image, pdf; text alone when the catalog names none),
    context (tokens, or None), level, fast}. An auto id answers with the tier's current version; an id from before this
    lineup answers as the id it is now."""
    model = new_id(str(model or "").strip().lower())
    if not model.startswith(PREFIX):
        return None
    entry = _index(cat or catalog()).get(model)
    if not entry:
        return None
    tier, version = entry["tier"], entry["version"]
    suffix = " (auto)" if entry["auto"] else " " + version["version"]
    return {
        "id": model,
        "tier": tier["id"],
        "kind": entry["kind"],
        "name": tier["name"] + suffix,
        "label": tier["name"],
        "model": version["model"],
        "version": version["version"],
        "auto": entry["auto"],
        "price": version["price"],
        "long_prompt": version.get("long_prompt"),
        "inputs": list(version.get("inputs") or ["text"]),
        "level": tier["level"],
        "fast": bool(tier.get("fast")),
        "current": tier["current"],
        "context": version.get("context"),
    }


def price_for(model):
    """(input, cached input, output) USD per 1M tokens for any Tico id, or None."""
    found = resolve(model)
    return _price_tuple(found["price"]) if found and found["price"] else None


def long_prompt_for(model):
    """(prompt tokens, (input, cached input, output)) above which a Tico id's rates go up, or None."""
    found = resolve(model)
    return _long_tuple(found["long_prompt"]) if found else None


def efforts_of(level):
    return ("low", "high", "max"), ("low" if level in ("xlow", "low") else "high")


def row_for(model):
    """A providers.MODEL_CATALOG-style row for any Tico id this release or the catalog knows, or None. An id from before
    this lineup gets the row of the id it is now."""
    found = resolve(model)
    if not found:
        return None
    efforts, default = efforts_of(found["level"])
    if found["tier"] == MEETING_TIER:
        efforts, default = ("low", "high", "max"), "low"  # the meeting tier's catalog effort (HQ: default low)
    row = {
        "id": found["id"],
        "label": found["name"],
        "runtime": "pi",
        "provider": "tico",
        "description": (found["model"] + " underneath") if found["model"] else "",
        "efforts": efforts,
        "default_effort": default,
    }
    if found.get("context"):
        row["context_tokens"] = found["context"]
    if found["kind"] == "decide":
        row["deprecated"] = True  # a decision tier is asked for by the judge, never picked as a bot's model
    else:
        row["tier"] = {
            "level": found["level"],
            "fast": found["fast"],
            "version": found["version"],
            "auto": found["auto"],
        }
    return row


def listed_rows():
    """The rows MODEL_CATALOG lists for the Gateway: each snapshot tier at its current pinned version, with Medium the
    one a bot gets when nobody chose."""
    rows = []
    for tier in SNAPSHOT["models"]:
        if tier["id"] == MEETING_TIER:
            continue  # the meeting tier is not a bot's pick: meeting_rows
        row = row_for(f"{tier['id']}-{tier['current']}")
        if row is None:  # its current version runs a held model
            continue
        if tier["id"] == DEFAULT_TIER:
            row["recommended"] = True
        rows.append(row)
    return rows


def meeting_rows():
    """The meeting model rows (`tico-meeting-1.0`) for Settings' Meeting model choice: flagged `meeting`, never a bot's pick."""
    tier = next(row for row in SNAPSHOT["models"] if row["id"] == MEETING_TIER)
    row = row_for(f"{MEETING_TIER}-{tier['current']}")
    row["meeting"] = True
    return [row]


# ----------------------------------------------------------------------------- a bot's pick
def current_version(level, fast=False, cat=None):
    cat = cat or catalog()
    tier = _index(cat).get(tier_id(level, fast))
    return tier["tier"]["current"] if tier else FIRST_VERSION


def default_model(level=DEFAULT_LEVEL, fast=False):
    """What a bot on the Gateway runs on when nobody chose: the level's tier at the current version, pinned. A tier the
    catalog does not offer (it runs a held model) is Medium, and a held Medium the first level tier still offered."""
    cat = catalog()
    index = _index(cat)
    if tier_id(level, fast) in index:
        return f"{tier_id(level, fast)}-{current_version(level, fast, cat)}"
    if DEFAULT_TIER in index:
        return f"{DEFAULT_TIER}-{current_version(DEFAULT_LEVEL, False, cat)}"
    for row in cat["models"]:
        live = live_row(row)
        if live is not None and live.get("level"):
            return f"{live['id']}-{live['current']}"
    return f"{DEFAULT_TIER}-{FIRST_VERSION}"


def clean_block(value):
    """A bot.yaml `gateway:` block (`level: xlow|low|medium|high|xhigh`, `fast: true`) as the two settings it holds, or
    None when it says nothing usable."""
    if not isinstance(value, dict):
        return None
    out = {}
    if value.get("level") in LEVEL_IDS:
        out["level"] = value["level"]
    if value.get("fast") is True:
        out["fast"] = True
    return out or None


def bot_model(gateway=None):
    """The Gateway model for a bot from its `gateway:` block in bot.yaml (`level`, `fast`): Medium when it names none."""
    gateway = gateway if isinstance(gateway, dict) else {}
    level = gateway.get("level") if gateway.get("level") in LEVEL_IDS else DEFAULT_LEVEL
    return default_model(level, gateway.get("fast") is True)


# ----------------------------------------------------------------------------- views
def view(cat=None):
    """What the model picker needs, small: the levels, and for each tier its current version and the versions (id,
    version, model, price) to pick from."""
    cat = cat or catalog()
    return {
        "as_of": cat.get("as_of", ""),
        "levels": cat["levels"],
        "tiers": {
            row["id"]: {
                "name": row["name"],
                "level": row["level"],
                "fast": bool(row.get("fast")),
                "current": row["current"],
                "versions": [
                    {
                        "version": v["version"],
                        "id": v["id"],
                        "model": v["model"],
                        "price": v["price"],
                        **({"context": v["context"]} if v.get("context") else {}),
                    }
                    for v in reversed(row["versions"])
                ],
            }
            for row in (live_row(r) for r in cat["models"] if r.get("level"))
            if row is not None
        },
    }


def lineup(query="", cat=None):
    """The catalog as an agent reads it: tiers with a line on when to use each, decisions and services; `query` keeps
    the entries whose words contain it. Prices are USD per 1M tokens."""
    cat = cat or catalog()
    words = [w for w in re.split(r"\W+", str(query or "").lower()) if w]

    def keep(*texts):
        hay = " ".join(texts).lower()
        return all(w in hay for w in words)

    models = []
    for raw in cat["models"]:
        row = live_row(raw)
        if row is None:
            continue
        now = next(v for v in row["versions"] if v["version"] == row["current"])
        when = LEVEL_WHEN.get(row["level"], "") + (" Faster, and may cost more." if row["fast"] else "")
        if keep(row["name"], now["model"], row["level"] or "", when):
            models.append(
                {
                    "id": row["id"],
                    "pinned": now["id"],
                    "name": row["name"],
                    "model": now["model"],
                    "version": now["version"],
                    "price": now["price"],
                    "when": when,
                }
            )
    decisions = [
        {
            "id": row["id"],
            "name": row["name"],
            "when": row.get("when") or DECISION_WHEN.get(row["id"], ""),
            "types": row["types"],
            "model": now["model"] if now else "",
            "pinned": now["id"] if now else "",
            "price": now["price"] if now else None,
        }
        for row in (live_row(r) for r in cat["decisions"])
        if row is not None and keep(row["name"], row.get("when") or DECISION_WHEN.get(row["id"], ""), row["id"])
        for now in [next((v for v in row["versions"] if v["version"] == row["current"]), None)]
    ]
    services = [
        {
            "id": row["id"],
            "name": row["name"],
            "when": row["for"],
            "price": row["price"],
            "status": row["status"],
            "tools": row["tools"],
            "details": row.get("details") or [],
        }
        for row in cat["services"]
        if keep(row["name"], row["for"], row["id"], " ".join(row["tools"]))
    ]
    return {
        "as_of": cat.get("as_of", ""),
        "models": models,
        "decisions": decisions,
        "services": services,
        "note": "A model id without a version always means the tier's current version; add -<version> to pin it.",
    }


def money(price):
    """`$2.50 / $12.50 per 1M` from a price dict."""
    if not price:
        return ""
    return f"${price['input']:.2f} / ${price['output']:.2f} per 1M"
