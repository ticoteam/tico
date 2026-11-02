"""The market by spend (docs/librarian.md#market-spend): the market defined, cut into segments, and each segment sized.

The market graph (backend/market.py) has no numbers on purpose. Spend keeps its rule that every figure is cited in
tables of its own:

- the **definition**, in `registry_metadata` (`market.definition`): the root segment, what is in and out, the counting
  rules, the year, and shared named assumptions (`params`) that formulas use as `$name`;
- **segments**: `segment` entities joined by `member_of` edges (sub-segment to segment to root);
- **sizes** (`market_sizes`): several estimates per segment, one per method, each with its arithmetic, parameters and
  evidence. One is accepted. A formula is evaluated by a small parser (below), never `eval`;
- **figures** (`market_figures`): a player's revenue, ARR, customers or seats, dated and labelled reported, estimated
  or claimed;
- **sources**: the shipped catalog (`market_sources/catalog.yaml`) and a team's own additions (`market_sources`).

Only the owner and the Librarian write (`market.require_writer`). Each write that changes what a segment is worth is a
`market_events` row on the segment, so a bet that cites the segment trips (backend/strategy.py). A proposed size is
logged but does not trip a bet: only an accepted size is something a bet rests on.
"""

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import yaml
from fastapi import Query, Request
from pydantic import Field

from . import hubdb as H
from . import market as MK
from . import models as M
from .store import Problem

DEFINITION_KEY = "market.definition"
DIMENSIONS = ("offering", "buyer")
BASES = ("product", "platform")
METHODS = ("top_down", "bottom_up", "player_sum", "owner")
STATUSES = ("proposed", "accepted", "rejected")
CONFIDENCES = ("high", "medium", "low")
METRICS = ("revenue", "arr", "customers", "seats")
FIGURE_KINDS = ("reported", "estimated", "claimed")
COVERS = ("sizing", "players", "buyers", "pricing", "signals", "value_chain")
ACCESS = ("free", "team_key", "gateway")
RELIABILITY = ("primary", "secondary", "estimate", "signal")
CATALOG = Path(__file__).parent / "market_sources" / "catalog.yaml"

SIZE_TASK = "Size the market"
PLAYBOOK = "playbooks/size-the-market.md"
DEFINITION_EVIDENCE = "market.definition"
MAX_DEPTH = 4                 # root's children are depth 1
MAX_SEGMENTS = 200
MAX_PLAYERS = 25
MAX_FIGURES = 6               # per player, newest first
LAYER_MAX = 40
LAYER_EDGES_MAX = 400
QUOTE_CLIP = 300
MAX_USD = 1e15                # a size above this is a typo
DISAGREE_RATIO = 2.0
FORMULA_MAX = 500
TOKENS_MAX = 200
PAREN_MAX = 20
PARAMS_MAX = 30
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?")
LAYERS = (("suppliers", "Suppliers"), ("platforms", "Platforms"), ("rivals", "Us and rivals"),
          ("channels", "Channels"), ("buyers", "Buyers"))
CHAIN_RELATIONS = ("competes_with", "integrates_with", "partners_with", "distributes_through", "sells_to")


# ----------------------------------------------------------------------------- formulas
class FormulaError(ValueError):
    """A formula that is not arithmetic over known names. The text says what to fix."""


_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d*)?|\.\d+)|(\$?[A-Za-z_][A-Za-z0-9_]*)|(\S))")


def _tokens(formula):
    text = str(formula or "")
    if len(text) > FORMULA_MAX:
        raise FormulaError(f"A formula is at most {FORMULA_MAX} characters")
    out, at = [], 0
    while at < len(text):
        if not text[at:].strip():
            break
        found = _TOKEN.match(text, at)
        number, name, other = found.groups()
        if number is not None:
            out.append(("num", float(number)))
        elif name is not None:
            if name.startswith("$") and not NAME.fullmatch(name[1:]):
                raise FormulaError(f"{name} is not a name")
            out.append(("name", name))
        elif other in "+-*/()":
            out.append(("op", other))
        else:
            raise FormulaError(f"'{other}' is not allowed: a formula uses numbers, names, + - * / and parentheses")
        at = found.end()
        if len(out) > TOKENS_MAX:
            raise FormulaError(f"A formula has at most {TOKENS_MAX} parts")
    if not out:
        raise FormulaError("The formula is empty")
    return out


def names_in(formula):
    """The names a formula reads: `smb_share` for a local parameter, `$smb_share` for a shared one."""
    return {value for kind, value in _tokens(formula) if kind == "name"}


def evaluate(formula, local, shared):
    """Arithmetic over numbers and known names: `+ - * /`, unary signs and parentheses, nothing else. There is no
    power, no call and no attribute, so the work is bounded by the length of the text. `local` and `shared` map a name
    to a number; `$name` reads `shared`. Raises FormulaError."""
    tokens, at, depth = _tokens(formula), 0, 0

    def finite(value):
        if not math.isfinite(value) or abs(value) > MAX_USD * 1e6:
            raise FormulaError("The formula gives a number that is too large")
        return value

    def peek():
        return tokens[at] if at < len(tokens) else (None, None)

    def atom():
        nonlocal at, depth
        kind, value = peek()
        if kind is None:
            raise FormulaError("The formula ends where a number or a name should be")
        at += 1
        if kind == "num":
            return value
        if kind == "name":
            source = shared if value.startswith("$") else local
            key = value.lstrip("$")
            if key not in source:
                raise FormulaError(f"{value} is not a known " + ("shared parameter" if value.startswith("$") else "parameter"))
            return float(source[key])
        if value != "(":
            raise FormulaError(f"'{value}' is not where a number or a name should be")
        depth += 1
        if depth > PAREN_MAX:
            raise FormulaError(f"Parentheses nest at most {PAREN_MAX} deep")
        inner = expression()
        if peek() != ("op", ")"):
            raise FormulaError("A parenthesis is not closed")
        at += 1
        depth -= 1
        return inner

    def unary():
        nonlocal at
        sign = 1.0
        while peek() in (("op", "-"), ("op", "+")):
            sign = -sign if tokens[at][1] == "-" else sign
            at += 1
        return sign * atom()

    def term():
        nonlocal at
        total = unary()
        while peek() in (("op", "*"), ("op", "/")):
            operator = tokens[at][1]
            at += 1
            right = unary()
            if operator == "/":
                if right == 0:
                    raise FormulaError("The formula divides by zero")
                total = finite(total / right)
            else:
                total = finite(total * right)
        return total

    def expression():
        nonlocal at
        total = term()
        while peek() in (("op", "+"), ("op", "-")):
            operator = tokens[at][1]
            at += 1
            right = term()
            total = finite(total + right if operator == "+" else total - right)
        return total

    result = expression()
    if at != len(tokens):
        raise FormulaError(f"'{tokens[at][1]}' is not where an operator should be")
    return finite(result)         # a lone literal or name never passed through an operator's check


# ----------------------------------------------------------------------------- the definition and its parameters
def _loads(value, default):
    return MK.loads(value, default)


def definition(conn):
    row = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (DEFINITION_KEY,)).fetchone()
    return (_loads(row[0], {}) or None) if row else None


def _save_definition(conn, value):
    conn.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (DEFINITION_KEY, MK.dumps(value)))


def shared_values(defn):
    """The shared parameters as {name: number}."""
    out = {}
    for name, item in ((defn or {}).get("params") or {}).items():
        value = item.get("value") if isinstance(item, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out[name] = float(value)
    return out


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Problem("validation", f"{label} must be a number", 422)
    return float(value)


def _param_entry(name, item, previous=None):
    """One parameter as {value, source?, judgement?}. A bare number is a value; a partial entry keeps what the
    parameter already said about its source."""
    if not NAME.fullmatch(str(name)):
        raise Problem("validation", f"'{name}' is not a parameter name: letters, digits and underscores, starting with a letter", 422)
    entry = dict(previous or {})
    if isinstance(item, dict):
        unknown = set(item) - {"value", "source", "judgement"}
        if unknown:
            raise Problem("validation", f"Parameter {name} has unknown fields: {', '.join(sorted(unknown))}", 422)
        if "value" in item:
            entry["value"] = _number(item["value"], f"Parameter {name}")
        if "source" in item:
            entry["source"] = str(item["source"] or "")[:500]
        if "judgement" in item:
            entry["judgement"] = bool(item["judgement"])
    else:
        entry["value"] = _number(item, f"Parameter {name}")
    if "value" not in entry:
        raise Problem("validation", f"Parameter {name} needs a value", 422)
    return {k: v for k, v in entry.items() if v not in ("", None) or k == "value"}


def _params(raw):
    """A size's own parameters, checked. Names starting with `$` are the snapshots the server keeps of shared ones."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict) or len(raw) > PARAMS_MAX:
        raise Problem("validation", f"params is an object with at most {PARAMS_MAX} parameters", 422)
    return {name: _param_entry(name, item) for name, item in raw.items()}


def _lines(label, value):
    if value is None:
        return None
    out = [str(v).strip() for v in value if str(v).strip()]
    if len(out) > 40 or any(len(v) > 1000 for v in out):
        raise Problem("validation", f"{label} has at most 40 lines of 1000 characters", 422)
    return out


def _usd(value):
    if value is None:
        return "none"
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            return f"${value / limit:.2f}{suffix}"
    return f"${value:.0f}"


def set_definition(conn, actor, patch):
    """Change the market's definition. Fields not named are kept; a list replaces the list; `params` merges by name
    (`null` removes one). `root_name` creates the root segment when there is none. A change to a shared parameter
    moves every size that uses it, so each moved segment gets an event."""
    current = definition(conn) or {}
    new = dict(current)
    for key in ("description", "geography", "currency"):
        if patch.get(key) is not None:
            new[key] = str(patch[key]).strip()
    if patch.get("year") is not None:
        new["year"] = int(patch["year"])
    for key in ("includes", "excludes", "counting_rules"):
        lines = _lines(key, patch.get(key))
        if lines is not None:
            new[key] = lines
    old_shared = shared_values(current)
    if patch.get("params") is not None:
        if not isinstance(patch["params"], dict) or len(patch["params"]) > PARAMS_MAX:
            raise Problem("validation", f"params is an object with at most {PARAMS_MAX} parameters", 422)
        merged = dict(current.get("params") or {})
        for name, item in patch["params"].items():
            if item is None:
                merged.pop(name, None)
            else:
                merged[name] = _param_entry(name, item, merged.get(name))
        new["params"] = merged
    root = (patch.get("root") or "").strip()
    root_name = (patch.get("root_name") or "").strip()
    if root:
        row = MK.entity(conn, root)
        if not row or row["type"] != "segment" or row["status"] != "active":
            raise Problem("not_found", f"{root} is not a segment of the market graph", 404)
        new["root"] = root
    if root_name:
        if new.get("root"):
            existing = MK.entity(conn, new["root"])
            if existing and existing["name"].strip().lower() != root_name.lower() and not root:
                raise Problem("exists", f"The market's root is already {existing['name']} ({new['root']})", 409)
        else:
            made = _new_segment(conn, actor, root_name, summary=new.get("description") or "")
            new["root"] = made["id"]
    new.setdefault("params", {})
    _save_definition(conn, new)
    changed = {n for n in set(old_shared) | set(shared_values(new)) if old_shared.get(n) != shared_values(new).get(n)}
    if changed:
        _log_shared_change(conn, actor, old_shared, shared_values(new), changed)
    return new


def _log_shared_change(conn, actor, old_shared, new_shared, changed):
    """A shared parameter moved: each segment whose accepted size uses it has a new size, whatever was written."""
    rows = H._rows(conn.execute("SELECT * FROM market_sizes WHERE status='accepted' AND formula IS NOT NULL"))
    for row in rows:
        row = _size_row(row)
        if not {k[1:] for k in row["params"] if k.startswith("$")} & changed:
            continue
        before, after = effective(row, old_shared)["spend_usd"], effective(row, new_shared)["spend_usd"]
        if before != after:
            _log(conn, actor, row["segment_id"], "size", _usd(before), _usd(after),
                 "shared parameter " + ", ".join(sorted(changed)) + " changed")


# ----------------------------------------------------------------------------- sizes
def _size_row(row):
    if not row:
        return None
    row = dict(row)
    row["params"] = _loads(row.get("params"), {})
    row["evidence_ids"] = _loads(row.get("evidence_ids"), [])
    return row


def size(conn, size_id):
    return _size_row(H._one(conn, "SELECT * FROM market_sizes WHERE id=?", (size_id,)))


def effective(row, shared):
    """The row as it reads now. A formula over a shared parameter that moved since the row was written is worked out
    again, and its low and high move by the same ratio. The stored row is never changed by a read."""
    out = {**row, "recomputed": False}
    if not row.get("formula"):
        return out
    snapshots = {k[1:]: v.get("value") for k, v in row["params"].items() if k.startswith("$")}
    current = {k: float(shared.get(k, v)) for k, v in snapshots.items() if v is not None}
    if not snapshots or all(math.isclose(current[k], float(v), rel_tol=1e-12, abs_tol=1e-12) for k, v in snapshots.items() if k in current):
        return out
    local = {k: v["value"] for k, v in row["params"].items() if not k.startswith("$")}
    try:
        spend = round(evaluate(row["formula"], local, current), 2)
    except FormulaError:
        return out
    params = {**row["params"], **{"$" + k: {**row["params"].get("$" + k, {}), "value": v} for k, v in current.items()}}
    if not math.isfinite(spend) or spend < 0 or spend > MAX_USD:
        out.update(spend_usd=None, low_usd=None, high_usd=None, params=params,
                   error="A shared parameter puts this estimate out of range")
        return out
    old = float(row["spend_usd"])
    if old > 0:
        low, high = row["low_usd"] * spend / old, row["high_usd"] * spend / old
    else:
        low, high = min(row["low_usd"], spend), max(row["high_usd"], spend)
    out.update(spend_usd=spend, low_usd=min(low, spend), high_usd=max(high, spend), recomputed=True, params=params)
    return out


def _log(conn, actor, segment_id, field, old, new, note="", trip=True):
    MK._event(conn, "entity", segment_id, actor, field, old, new, None, note, trip=trip)


def _segment(conn, segment_id):
    row = MK.entity(conn, str(segment_id or ""))
    if not row or row["type"] != "segment" or row["status"] != "active":
        raise Problem("not_found", f"{segment_id} is not a segment of the market graph", 404)
    return row


def _evidence(conn, actor, ids, inline, need):
    """The evidence ids a size or figure rests on: existing rows, and rows written now from inline evidence."""
    out = []
    for eid in ids or []:
        eid = str(eid).strip()
        if eid and eid not in out:
            if not MK.evidence(conn, eid):
                raise Problem("not_found", f"There is no evidence {eid}", 404)
            out.append(eid)
    for item in inline or []:
        item = item if isinstance(item, dict) else item.model_dump()
        if not (str(item.get("source_url") or "").strip() or str(item.get("quote") or "").strip()):
            raise Problem("evidence", "Evidence needs a source_url or a quote", 422)
        kind = item.get("source_kind") or "other"
        if kind not in MK.SOURCE_KINDS:
            raise Problem("validation", f"A source kind is {'|'.join(MK.SOURCE_KINDS)}, not {kind}", 422)
        row, _events = MK.create_evidence(conn, actor, source_url=item.get("source_url") or "", source_kind=kind,
                                          quote=item.get("quote") or "", our_read=item.get("our_read") or "")
        if row["id"] not in out:
            out.append(row["id"])
    if need and not out:
        raise Problem("evidence", "Every method except owner needs at least one evidence row: evidence_ids or evidence", 422)
    if len(out) > 20:
        raise Problem("validation", "A row cites at most 20 evidence rows", 422)
    return out


def create_size(conn, actor, f):
    """File one estimate for a segment. `f` is a dict of the request's fields. It is `proposed` unless `accept`."""
    defn = definition(conn)
    shared = shared_values(defn)
    seg = _segment(conn, f["segment_id"])
    method = f["method"]
    year = f.get("year") or (defn or {}).get("year") or datetime.now(timezone.utc).year
    params = _params(f.get("params"))
    if any(k.startswith("$") for k in params):
        raise Problem("validation", "A parameter name cannot start with $; set shared parameters on the definition", 422)
    formula = (f.get("formula") or "").strip() or None
    spend = f.get("spend_usd")
    if formula:
        try:
            names = names_in(formula)
            used = {n[1:] for n in names if n.startswith("$")}
            spend = round(evaluate(formula, {k: v["value"] for k, v in params.items()}, shared), 2)
        except FormulaError as exc:
            raise Problem("formula", str(exc), 422) from None
        for name in sorted(used):
            params["$" + name] = {"value": shared[name]}
    if spend is None:
        raise Problem("validation", "Give spend_usd, or a formula that works it out", 422)
    low, high = f.get("low_usd"), f.get("high_usd")
    if low is None or high is None:
        raise Problem("validation", "A size needs low_usd and high_usd, the range the evidence supports", 422)
    spend, low, high = _number(spend, "spend_usd"), _number(low, "low_usd"), _number(high, "high_usd")
    if not 0 <= low <= spend <= high <= MAX_USD:
        raise Problem("range", "Sizes are in US dollars with low_usd <= spend_usd <= high_usd, none negative "
                      f"(spend {spend:g}, low {low:g}, high {high:g})", 422)
    growth = f.get("growth_pct")
    if growth is not None and not -100 <= _number(growth, "growth_pct") <= 100_000:
        raise Problem("validation", "growth_pct is a percentage per year, at least -100", 422)
    ids = _evidence(conn, actor, f.get("evidence_ids"), f.get("evidence"), need=method != "owner")
    now = H.now()
    row = {"id": H.new_id(), "segment_id": seg["id"], "dimension": f.get("dimension") or "offering",
           "basis": f.get("basis") or "product", "year": int(year), "spend_usd": spend, "low_usd": low, "high_usd": high,
           "growth_pct": growth, "method": method, "arithmetic": str(f.get("arithmetic") or ""), "formula": formula,
           "params": MK.dumps(params), "evidence_ids": json.dumps(ids), "confidence": f.get("confidence") or "medium",
           "overlaps": str(f.get("overlaps") or ""), "spread": str(f.get("spread") or ""), "status": "proposed",
           "created_by": actor, "created_at": now, "updated_at": now}
    conn.execute("INSERT INTO market_sizes (id, segment_id, dimension, basis, year, spend_usd, low_usd, high_usd, "
                 "growth_pct, method, arithmetic, formula, params, evidence_ids, confidence, overlaps, spread, status, "
                 "created_by, created_at, updated_at) VALUES (:id,:segment_id,:dimension,:basis,:year,:spend_usd,:low_usd,"
                 ":high_usd,:growth_pct,:method,:arithmetic,:formula,:params,:evidence_ids,:confidence,:overlaps,"
                 ":spread,:status,:created_by,:created_at,:updated_at)", row)
    _log(conn, actor, seg["id"], "size.proposed", None, f"{_usd(spend)} ({method}, {row['dimension']}/{row['basis']} {row['year']})",
         trip=False)
    if f.get("accept"):
        return accept_size(conn, actor, row["id"])
    return effective(size(conn, row["id"]), shared)


def _same_key(conn, row):
    return H._rows(conn.execute(
        "SELECT * FROM market_sizes WHERE segment_id=? AND dimension=? AND basis=? AND year=? AND status='accepted' AND id<>?",
        (row["segment_id"], row["dimension"], row["basis"], row["year"], row["id"])))


def accept_size(conn, actor, size_id):
    """The estimate becomes the segment's size; any other accepted row for the same segment, dimension, basis and
    year goes back to proposed. This is the change a bet that cites the segment can trip on."""
    row = size(conn, size_id)
    if not row:
        raise Problem("not_found", f"There is no size {size_id}", 404)
    shared = shared_values(definition(conn))
    if row["status"] == "accepted":
        return effective(row, shared)
    before = None
    for other in _same_key(conn, row):
        before = effective(_size_row(other), shared)["spend_usd"]
        conn.execute("UPDATE market_sizes SET status='proposed', updated_at=? WHERE id=?", (H.now(), other["id"]))
    conn.execute("UPDATE market_sizes SET status='accepted', updated_at=? WHERE id=?", (H.now(), size_id))
    now_row = effective(size(conn, size_id), shared)
    _log(conn, actor, row["segment_id"], "size", None if before is None else _usd(before), _usd(now_row["spend_usd"]),
         f"{row['method']} accepted ({row['dimension']}/{row['basis']} {row['year']})")
    return now_row


def reject_size(conn, actor, size_id):
    row = size(conn, size_id)
    if not row:
        raise Problem("not_found", f"There is no size {size_id}", 404)
    shared = shared_values(definition(conn))
    if row["status"] == "rejected":
        return effective(row, shared)
    was = row["status"] == "accepted"
    conn.execute("UPDATE market_sizes SET status='rejected', updated_at=? WHERE id=?", (H.now(), size_id))
    shown = effective(size(conn, size_id), shared)
    _log(conn, actor, row["segment_id"], "size" if was else "size.rejected", _usd(shown["spend_usd"]) if was else None,
         None if was else _usd(shown["spend_usd"]), f"{row['method']} rejected ({row['dimension']}/{row['basis']} {row['year']})",
         trip=was)
    return shown


def change_params(conn, actor, size_id, changes):
    """The owner moves a parameter of an estimate: a new `owner` row copies the formula and the parameters with the
    change, is worked out again with its range scaled by the same ratio, and is accepted."""
    row = size(conn, size_id)
    if not row:
        raise Problem("not_found", f"There is no size {size_id}", 404)
    if not row.get("formula"):
        raise Problem("formula", "This estimate has no formula, so it has no parameters to change; add an owner estimate", 422)
    if not isinstance(changes, dict) or not changes:
        raise Problem("validation", "Say which parameter to change: params {name: value}", 422)
    shared = shared_values(definition(conn))
    now_row = effective(row, shared)
    params = {k: dict(v) for k, v in now_row["params"].items()}
    for name, value in changes.items():
        if str(name).startswith("$") or name not in params:
            raise Problem("validation", f"{name} is not a parameter of this estimate; change a shared parameter on the "
                          "market definition (PUT /api/v2/market/definition)", 422)
        params[name] = _param_entry(name, {"value": value, "source": "owner"}, params[name])
    local = {k: v["value"] for k, v in params.items() if not k.startswith("$")}
    used = {n[1:] for n in names_in(row["formula"]) if n.startswith("$")}
    try:
        spend = round(evaluate(row["formula"], local, {k: params["$" + k]["value"] for k in used}), 2)
    except FormulaError as exc:
        raise Problem("formula", str(exc), 422) from None
    if not 0 <= spend <= MAX_USD:
        raise Problem("range", f"That makes the estimate {spend:g} dollars, outside 0 to {MAX_USD:g}", 422)
    base = now_row if now_row["spend_usd"] is not None else row         # out of range now: scale the stored range
    old = float(base["spend_usd"])
    if old > 0:
        low, high = base["low_usd"] * spend / old, base["high_usd"] * spend / old
    else:
        low, high = min(base["low_usd"], spend), max(base["high_usd"], spend)
    said = ", ".join(f"{k} = {params[k]['value']:g}" for k in changes)
    created = {"segment_id": row["segment_id"], "dimension": row["dimension"], "basis": row["basis"], "year": row["year"],
               "spend_usd": spend, "low_usd": min(low, spend), "high_usd": max(high, spend),
               "growth_pct": row["growth_pct"], "method": "owner",
               "arithmetic": (row["arithmetic"] + f"\nOwner set {said}.").strip()[:4000], "formula": row["formula"],
               "params": {k: v for k, v in params.items() if not k.startswith("$")}, "confidence": row["confidence"],
               "overlaps": row["overlaps"], "spread": row["spread"], "evidence_ids": row["evidence_ids"], "accept": True}
    return create_size(conn, actor, created)


# ----------------------------------------------------------------------------- segments and the tree
def definition_evidence(conn, actor):
    """The one evidence row every segment the team defined stands on: the definition itself."""
    row, _events = MK.create_evidence(
        conn, actor, evidence_key=MK.evidence_id(DEFINITION_EVIDENCE), source_kind="other",
        our_read="A segment of the market as the team defined it (the market definition on the Spend view).")
    return row["id"]


def _new_segment(conn, actor, name, summary="", evidence_ids=None, parent=None, entity_id=None, properties=None,
                 force=False):
    ev = evidence_ids or [definition_evidence(conn, actor)]
    made, _events = MK.create_entity(conn, actor, type="segment", name=name, summary=summary, evidence_ids=ev,
                                     entity_id=entity_id, properties=properties, force=force)
    if parent:
        MK.create_edge(conn, actor, src=made["id"], rel="member_of", dst=parent, evidence_ids=ev)
    return made


def add_segment(conn, actor, name, parent_id=None, summary=""):
    defn = definition(conn) or {}
    root = defn.get("root")
    if not root:
        raise Problem("definition", "Define the market and its root first: PUT /api/v2/market/definition with root_name", 422)
    parent = _segment(conn, parent_id or root)
    seg_id = "segment/" + MK.slug(name)
    old = MK.entity(conn, seg_id)
    if old and (old["type"] != "segment" or old["status"] != "retired"):
        raise Problem("exists", f"{seg_id} already exists", 409, extra={"id": seg_id})
    if parent["id"] != root:
        found = _tree(conn, root)
        if parent["id"] not in found["depth"]:
            raise Problem("tree", f"{parent['id']} is not under the market's root", 422)
        if found["depth"][parent["id"]] >= MAX_DEPTH:
            raise Problem("tree", f"The tree is at most {MAX_DEPTH} levels deep", 422)
    if old:
        # A segment removed earlier comes back under the parent named now, keeping its id and its history.
        MK.update_entity(conn, actor, seg_id, status="active")
        day = _today()
        held = conn.execute("SELECT 1 FROM market_edges WHERE src=? AND rel='member_of' AND (valid_from_on IS NULL OR "
                            "substr(valid_from_on,1,10)<=?) AND (valid_until_on IS NULL OR substr(valid_until_on,1,10)>=?)", (seg_id, day, day)).fetchone()
        if not held:
            MK.create_edge(conn, actor, src=seg_id, rel="member_of", dst=parent["id"], since=day,
                           evidence_ids=[definition_evidence(conn, actor)])
        return MK.entity(conn, seg_id)
    made = _new_segment(conn, actor, name, summary=str(summary or ""), parent=parent["id"])
    return made


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _tree(conn, root):
    """The segments under the root by `member_of`, depth first: {order, depth, parent, rows}."""
    day = _today()
    rows = {r["id"]: r for r in H._rows(conn.execute(
        "SELECT id, name, summary, properties FROM market_entities WHERE type='segment' AND status='active'"))}
    children = {}
    for edge in conn.execute("SELECT src, dst FROM market_edges WHERE rel='member_of' AND (valid_from_on IS NULL OR substr(valid_from_on,1,10)<=?) "
                             "AND (valid_until_on IS NULL OR substr(valid_until_on,1,10)>=?)", (day, day)):
        if edge["src"] in rows and edge["dst"] in rows and edge["src"] != edge["dst"]:
            children.setdefault(edge["dst"], []).append(edge["src"])
    order, depth, parent = [], {}, {}

    def walk(node, level):
        for child in sorted(children.get(node, []), key=lambda c: (rows[c]["name"].lower(), c)):
            if child in depth or child == root or level > MAX_DEPTH or len(order) >= MAX_SEGMENTS:
                continue
            depth[child], parent[child] = level, node
            order.append(child)
            walk(child, level + 1)

    if root in rows:
        walk(root, 1)
    return {"order": order, "depth": depth, "parent": {k: (None if v == root else v) for k, v in parent.items()}, "rows": rows}


# ----------------------------------------------------------------------------- the spend view
def _best(rows):
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(rows, key=lambda r: (r["spend_usd"] is None, rank.get(r["confidence"], 3), r["method"] == "owner",
                                       r["updated_at"]), reverse=False)[0]


def _disagree(rows):
    spends = [r["spend_usd"] for r in rows if r["spend_usd"] is not None]
    if len(spends) < 2:
        return False
    low, high = min(spends), max(spends)
    return high > 0 and (low <= 0 or high / low > DISAGREE_RATIO)


def _rolled(ids, parent, chosen_of):
    """Each segment's spend as the total counts it: its own chosen size when that has a spend, else the sum of its
    children's, at any depth, else None."""
    kids = {}
    for seg_id in ids:
        kids.setdefault(parent.get(seg_id), []).append(seg_id)
    rolled = {}
    for seg_id in reversed(ids):                    # children come after their parent in `ids`
        own = chosen_of[seg_id]
        if own and own["spend_usd"] is not None:
            rolled[seg_id] = {k: own[k] for k in ("spend_usd", "low_usd", "high_usd")}
            continue
        parts = [rolled[k] for k in kids.get(seg_id, []) if rolled.get(k)]
        rolled[seg_id] = {k: sum(p[k] for p in parts) for k in ("spend_usd", "low_usd", "high_usd")} if parts else None
    return rolled


def spend(conn, dimension="offering", basis="product", year=None):
    defn = definition(conn)
    shared = shared_values(defn)
    year = int(year or (defn or {}).get("year") or datetime.now(timezone.utc).year)
    shown = None if defn is None else {**defn, "params": {k: dict(v) for k, v in (defn.get("params") or {}).items()}}
    root = (defn or {}).get("root")
    result = {"definition": shown, "year": year, "dimension": dimension, "basis": basis,
              "total": {"spend_usd": 0, "low_usd": 0, "high_usd": 0}, "segments": []}
    tree = _tree(conn, root) if root else {"order": [], "depth": {}, "parent": {}, "rows": {}}
    ids = tree["order"]
    if not ids:
        return result
    marks = json.dumps(ids)
    sizes = {}
    for row in conn.execute("SELECT * FROM market_sizes WHERE dimension=? AND year=? AND status<>'rejected' "
                            "AND segment_id IN (SELECT value FROM json_each(?)) ORDER BY created_at, id", (dimension, year, marks)):
        sizes.setdefault(row["segment_id"], []).append(effective(_size_row(row), shared))
    _attach_evidence(conn, [r for rows in sizes.values() for r in rows])
    figures = {}
    for row in conn.execute("SELECT f.*, e.source_url FROM market_figures f LEFT JOIN market_evidence e ON e.id=f.evidence_id "
                            "WHERE f.segment_id IN (SELECT value FROM json_each(?)) ORDER BY f.as_of DESC, f.created_at DESC, f.id", (marks,)):
        figures.setdefault((row["segment_id"], row["entity_id"]), []).append(
            {"metric": row["metric"], "value": row["value"], "text": row["text"], "kind": row["kind"],
             "as_of": row["as_of"], "source_url": row["source_url"] or ""})
    members, own = _players(conn, ids, figures)
    names = {r["id"]: r["name"] for r in H._rows(conn.execute(
        "SELECT id, name FROM market_entities WHERE id IN (SELECT value FROM json_each(?)) AND status='active'",
        (json.dumps(sorted({e for seg in members.values() for e in seg})),)))}
    chosen_of = {}
    for seg_id in ids:
        mine = [r for r in sizes.get(seg_id, []) if r["basis"] == basis]
        accepted = [r for r in mine if r["status"] == "accepted"]
        chosen_of[seg_id] = accepted[0] if accepted else (_best(mine) if mine else None)
    rolled = _rolled(ids, tree["parent"], chosen_of)
    total = {"spend_usd": 0.0, "low_usd": 0.0, "high_usd": 0.0}
    for seg_id in ids:
        if tree["depth"][seg_id] == 1 and rolled[seg_id]:
            for key in total:
                total[key] += rolled[seg_id][key]
    for seg_id in ids:
        rows = sizes.get(seg_id, [])
        mine = [r for r in rows if r["basis"] == basis]
        chosen = chosen_of[seg_id]
        level = tree["depth"][seg_id]
        players = []
        for entity_id in sorted(members.get(seg_id, ()), key=lambda e: (not figures.get((seg_id, e)), names.get(e, e).lower(), e)):
            if entity_id in names and len(players) < MAX_PLAYERS:
                players.append({"entity_id": entity_id, "name": names[entity_id],
                                "figures": figures.get((seg_id, entity_id), [])[:MAX_FIGURES]})
        result["segments"].append({
            "id": seg_id, "name": tree["rows"][seg_id]["name"], "parent": tree["parent"].get(seg_id), "depth": level,
            "summary": tree["rows"][seg_id]["summary"],
            "size": {**chosen, "accepted": chosen["status"] == "accepted"} if chosen else None,
            "rolled_usd": rolled[seg_id],
            "estimates": mine, "disagree": _disagree(mine),
            "bases": sorted({r["basis"] for r in rows}), "players": players, "self": seg_id in own,
            "next": bool(MK.loads(tree["rows"][seg_id]["properties"], {}).get("next"))})
    result["total"] = {k: round(v, 2) for k, v in total.items()}
    return result


def _attach_evidence(conn, rows):
    """Each estimate carries the evidence rows it cites, quotes clipped, so a page can show the sources."""
    wanted = sorted({e for r in rows for e in r["evidence_ids"]})
    found = {r["id"]: r for r in H._rows(conn.execute(
        "SELECT id, source_url, source_kind, quote FROM market_evidence WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(wanted),)))} if wanted else {}
    for r in rows:
        r["evidence"] = [{"id": e, "source_url": found[e]["source_url"], "source_kind": found[e]["source_kind"],
                          "quote": found[e]["quote"][:QUOTE_CLIP]} for e in r["evidence_ids"] if e in found]


def _players(conn, ids, figures):
    """Who is in each segment: companies and products with an `operates_in` edge into it or a `competes_with` edge
    that names it, and any with a figure in it. And which segments `company/self` is in."""
    marks = json.dumps(ids)
    members = {s: set() for s in ids}
    own = set()
    day = _today()
    current = "(valid_from_on IS NULL OR substr(valid_from_on,1,10)<=?) AND (valid_until_on IS NULL OR substr(valid_until_on,1,10)>=?)"
    for edge in conn.execute(
            f"SELECT src, dst, rel, CASE WHEN json_valid(properties) THEN json_extract(properties,'$.segment') END AS segment "
            f"FROM market_edges WHERE {current} AND ((rel='operates_in' AND dst IN (SELECT value FROM json_each(?))) "
            "OR (rel='competes_with' AND CASE WHEN json_valid(properties) THEN json_extract(properties,'$.segment') END "
            "IN (SELECT value FROM json_each(?))))", (day, day, marks, marks)):
        seg = edge["dst"] if edge["rel"] == "operates_in" else edge["segment"]
        ends = (edge["src"],) if edge["rel"] == "operates_in" else (edge["src"], edge["dst"])
        members[seg].update(ends)
        if MK.OWN_COMPANY_ID in ends:
            own.add(seg)
    for seg_id, entity_id in figures:
        members[seg_id].add(entity_id)
    return members, own


# ----------------------------------------------------------------------------- figures
def create_figure(conn, actor, f):
    ent = MK.entity(conn, f["entity_id"])
    if not ent or ent["type"] not in ("company", "product") or ent["status"] != "active":
        raise Problem("not_found", f"{f['entity_id']} is not a company or product in the market graph", 404)
    seg = _segment(conn, f["segment_id"])
    as_of = str(f.get("as_of") or "").strip()
    if not MONTH.fullmatch(as_of):
        raise Problem("validation", "as_of is a month or a day: YYYY-MM or YYYY-MM-DD", 422)
    value = f.get("value")
    text = str(f.get("text") or "").strip()
    if value is None and not text:
        raise Problem("validation", "A figure needs a value, or text that says what it is", 422)
    if value is not None and not 0 <= _number(value, "value") <= MAX_USD:
        raise Problem("range", "A figure is not negative", 422)
    ids = _evidence(conn, actor, [f["evidence_id"]] if f.get("evidence_id") else [], f.get("evidence"), need=True)
    row = {"id": H.new_id(), "entity_id": ent["id"], "segment_id": seg["id"], "metric": f["metric"], "value": value,
           "text": text, "kind": f["kind"], "as_of": as_of, "evidence_id": ids[0], "created_by": actor, "created_at": H.now()}
    conn.execute("INSERT INTO market_figures (id, entity_id, segment_id, metric, value, text, kind, as_of, evidence_id, "
                 "created_by, created_at) VALUES (:id,:entity_id,:segment_id,:metric,:value,:text,:kind,:as_of,:evidence_id,"
                 ":created_by,:created_at)", row)
    shown = f"{f['metric']} {value:g}" if value is not None else f"{f['metric']}: {text}"
    MK._event(conn, "entity", ent["id"], actor, "figure", None, f"{shown} ({f['kind']}, {as_of}) in {seg['id']}")
    return row


# ----------------------------------------------------------------------------- sources
def shipped_sources():
    """The catalog this release ships, checked."""
    items = yaml.safe_load(CATALOG.read_text()) or []
    return [_source(item, "shipped") for item in items]


def _source(item, origin, by=None, created=None):
    if not isinstance(item, dict):
        raise Problem("validation", "A source is an object", 422)
    unknown = set(item) - {"id", "name", "kind", "covers", "access", "reliability", "how", "url"}
    if unknown:
        raise Problem("validation", f"A source has no field {', '.join(sorted(unknown))}", 422)
    name, kind = str(item.get("name") or "").strip(), str(item.get("kind") or "").strip()
    if not name or not kind:
        raise Problem("validation", "A source needs a name and a kind", 422)
    ident = str(item.get("id") or "").strip() or MK.slug(name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", ident):
        raise Problem("validation", f"'{ident}' is not a source id: lower-case letters, digits and hyphens", 422)
    covers = [str(c) for c in item.get("covers") or []]
    if not covers or any(c not in COVERS for c in covers):
        raise Problem("validation", f"covers is a list from {', '.join(COVERS)}", 422)
    if item.get("access") not in ACCESS:
        raise Problem("validation", f"access is {'|'.join(ACCESS)}", 422)
    if item.get("reliability") not in RELIABILITY:
        raise Problem("validation", f"reliability is {'|'.join(RELIABILITY)}", 422)
    url = str(item.get("url") or "").strip()
    if url and not re.match(r"https?://", url):
        raise Problem("validation", "A source url starts with https://", 422)
    return {"id": ident, "name": name[:200], "kind": kind[:80], "covers": list(dict.fromkeys(covers)), "access": item["access"],
            "reliability": item["reliability"], "how": str(item.get("how") or "")[:1000], "url": url or None,
            "origin": origin, **({"created_by": by, "created_at": created} if origin == "team" else {})}


def sources(conn):
    """The shipped catalog with the team's additions; a team row with a shipped id replaces it."""
    merged = {s["id"]: s for s in shipped_sources()}
    for row in H._rows(conn.execute("SELECT * FROM market_sources ORDER BY created_at, id")):
        row["covers"] = _loads(row["covers"], [])
        merged[row["id"]] = {**row, "origin": "team"}
    return list(merged.values())


def add_source(conn, actor, item):
    row = _source(item, "team", actor, H.now())
    conn.execute("INSERT INTO market_sources (id, name, kind, covers, access, reliability, how, url, created_by, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
                 "covers=excluded.covers, access=excluded.access, reliability=excluded.reliability, how=excluded.how, "
                 "url=excluded.url", (row["id"], row["name"], row["kind"], json.dumps(row["covers"]), row["access"],
                                      row["reliability"], row["how"], row["url"], actor, row["created_at"]))
    H.event(conn, actor, "market.source", row["id"], {"name": row["name"]})
    return row


# ----------------------------------------------------------------------------- the value chain
def value_chain(conn):
    """Five layers, from the graph: us and our rivals; what we and they integrate with or partner with (platforms, or
    suppliers when the edge says so); where they are sold (channels); and who they sell to (buyers)."""
    day = _today()
    me = MK.OWN_COMPANY_ID
    empty = {"layers": [{"key": k, "title": t, "nodes": []} for k, t in LAYERS], "edges": []}
    if not conn.execute("SELECT 1 FROM market_entities WHERE id=? AND status='active'", (me,)).fetchone():
        return empty
    current = "(valid_from_on IS NULL OR substr(valid_from_on,1,10)<=?) AND (valid_until_on IS NULL OR substr(valid_until_on,1,10)>=?)"
    rivals = {e["dst"] if e["src"] == me else e["src"] for e in H._rows(conn.execute(
        f"SELECT src, dst FROM market_edges WHERE rel='competes_with' AND (src=? OR dst=?) AND {current}", (me, me, day, day)))}
    rivals.discard(me)
    front = [me, *sorted(rivals)]
    marks = json.dumps(front)
    layer_of = {"integrates_with": "platforms", "partners_with": "platforms", "distributes_through": "channels",
                "sells_to": "buyers"}
    found = {k: set() for k, _ in LAYERS}
    found["rivals"] = set(front)
    kept = []
    for edge in H._rows(conn.execute(
            f"SELECT src, rel, dst, CASE WHEN json_valid(properties) THEN properties END AS properties FROM market_edges "
            f"WHERE rel IN ('integrates_with','partners_with','distributes_through','sells_to') AND {current} "
            "AND (src IN (SELECT value FROM json_each(?)) OR (rel IN ('integrates_with','partners_with') AND dst IN "
            "(SELECT value FROM json_each(?))))", (day, day, marks, marks))):
        ours = edge["src"] if edge["src"] in front else edge["dst"] if edge["rel"] in ("integrates_with", "partners_with") and edge["dst"] in front else None
        target = edge["dst"] if ours == edge["src"] else edge["src"]
        if ours is None or target in front:
            continue
        layer = layer_of[edge["rel"]]
        props = _loads(edge["properties"], {})
        if layer == "platforms" and (props.get("role") == "supplier" or props.get("supplier") is True or props.get("layer") == "supplier"):
            layer = "suppliers"
        found[layer].add(target)
        kept.append((ours, edge["rel"], target, layer))
    every = sorted(set().union(*found.values()))
    info = {r["id"]: r for r in H._rows(conn.execute(
        "SELECT id, name, type FROM market_entities WHERE status='active' AND id IN (SELECT value FROM json_each(?))", (json.dumps(every),)))}
    degree = {}
    for edge in conn.execute(f"SELECT src, dst FROM market_edges WHERE {current} AND (src IN (SELECT value FROM json_each(?)) "
                             "OR dst IN (SELECT value FROM json_each(?)))", (day, day, json.dumps(every), json.dumps(every))):
        degree[edge["src"]] = degree.get(edge["src"], 0) + 1
        degree[edge["dst"]] = degree.get(edge["dst"], 0) + 1
    layers, shown = [], set()
    for key, title in LAYERS:
        ranked = sorted((i for i in found[key] if i in info), key=lambda i: (i != me, -degree.get(i, 0), info[i]["name"].lower(), i))
        nodes = [{"id": i, "name": info[i]["name"], "type": info[i]["type"], "self": i == me} for i in ranked[:LAYER_MAX]]
        shown.update(n["id"] for n in nodes)
        layers.append({"key": key, "title": title, "nodes": nodes})
    seen, edges = set(), []
    for ours, rel, target, _layer in kept:
        if ours in shown and target in shown and (ours, rel, target) not in seen:
            seen.add((ours, rel, target))
            edges.append({"src": ours, "rel": rel, "dst": target})
    for rival in sorted(rivals & shown):
        edges.append({"src": rival, "rel": "competes_with", "dst": me})
    return {"layers": layers, "edges": edges[:LAYER_EDGES_MAX]}


# ----------------------------------------------------------------------------- the research task
def _open_task(conn):
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    row = conn.execute(f"SELECT id FROM tasks WHERE owner_id=? AND (title=? OR title LIKE ?) AND status IN ({marks}) "
                       "ORDER BY created_at DESC LIMIT 1", ("bot:librarian", SIZE_TASK, SIZE_TASK + " (%)", *H.ACTIVE_STATUSES)).fetchone()
    return row["id"] if row else None


def _free_title(conn, actor):
    marks = ",".join("?" * len(H.LIVE_STATUSES))
    for n in range(1, 40):
        title = SIZE_TASK if n == 1 else f"{SIZE_TASK} ({n})"
        if not conn.execute(f"SELECT 1 FROM tasks WHERE requester_id=? AND owner_id=? AND title=? AND status IN ({marks})",
                            (actor, "bot:librarian", title, *H.LIVE_STATUSES)).fetchone():
            return title
    return f"{SIZE_TASK} (40)"


def research(conn, auth, who, description):
    """The owner asks the Librarian to define and size the market. One task at a time: asking again while it is open
    returns the same task."""
    from . import getting_started as GS
    from . import rooms
    if who.role != "owner":
        raise Problem("forbidden", "Only the owner asks for the market to be sized", 403)
    found = _open_task(conn)
    if found:
        return {"task_id": found}
    if not GS._active(conn, GS.LIBRARIAN):
        raise Problem("librarian", "The Librarian is not running yet. It starts once a computer hosts it.", 409)
    description = str(description or "").strip()
    if not description:
        raise Problem("validation", "Describe the market in a sentence", 422)
    defn = definition(conn)
    if not (defn or {}).get("description"):
        set_definition(conn, who.actor, {"description": description})
    safe = MK._for_task(description)
    text = "\n".join([f"Define and size our market: {safe}", "", f"Follow {PLAYBOOK}."])
    task = H.task_create(conn, who.actor, _free_title(conn, who.actor), text, "bot:librarian", allow_planned=True,
                         conversation_id=rooms.task_conversation_id(conn, auth, "bot:librarian", who.actor))
    H.event(conn, who.actor, "market.sizing_requested", task["id"], {})
    return {"task_id": task["id"]}


# ----------------------------------------------------------------------------- HTTP
Line = Annotated[str, Field(max_length=1000)]


class MarketEvidenceIn(M.Contract):
    source_url: str = Field(default="", max_length=2000)
    source_kind: str = Field(default="other", max_length=40)
    quote: str = Field(default="", max_length=20_000)
    our_read: str = Field(default="", max_length=20_000)


class MarketDefinitionPatch(M.Contract):
    root: str | None = Field(default=None, max_length=200)
    root_name: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    includes: list[Line] | None = Field(default=None, max_length=40)
    excludes: list[Line] | None = Field(default=None, max_length=40)
    counting_rules: list[Line] | None = Field(default=None, max_length=40)
    year: int | None = Field(default=None, ge=1990, le=2100)
    geography: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    params: dict | None = None


class MarketSegmentCreate(M.Contract):
    name: str = Field(min_length=1, max_length=300)
    parent_id: str | None = Field(default=None, max_length=200)
    summary: str = Field(default="", max_length=2000)


class MarketSizeCreate(M.Contract):
    segment_id: str = Field(min_length=1, max_length=200)
    dimension: Literal["offering", "buyer"] = "offering"
    basis: Literal["product", "platform"] = "product"
    year: int | None = Field(default=None, ge=1990, le=2100)
    spend_usd: float | None = None
    low_usd: float | None = None
    high_usd: float | None = None
    growth_pct: float | None = None
    method: Literal["top_down", "bottom_up", "player_sum", "owner"]
    arithmetic: str = Field(default="", max_length=4000)
    formula: str | None = Field(default=None, max_length=FORMULA_MAX)
    params: dict | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[MarketEvidenceIn] = Field(default_factory=list, max_length=10)
    confidence: Literal["high", "medium", "low"] = "medium"
    overlaps: str = Field(default="", max_length=4000)
    spread: str = Field(default="", max_length=4000)
    accept: bool = False


class MarketSizeParams(M.Contract):
    params: dict


class MarketFigureCreate(M.Contract):
    entity_id: str = Field(min_length=1, max_length=200)
    segment_id: str = Field(min_length=1, max_length=200)
    metric: Literal["revenue", "arr", "customers", "seats"]
    value: float | None = None
    text: str = Field(default="", max_length=1000)
    kind: Literal["reported", "estimated", "claimed"]
    as_of: str = Field(max_length=10)
    evidence_id: str | None = Field(default=None, max_length=80)
    evidence: list[MarketEvidenceIn] = Field(default_factory=list, max_length=5)


class MarketSourceCreate(M.Contract):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=80)
    covers: list[str] = Field(min_length=1, max_length=len(COVERS))
    access: Literal["free", "team_key", "gateway"]
    reliability: Literal["primary", "secondary", "estimate", "signal"]
    how: str = Field(default="", max_length=1000)
    url: str | None = Field(default=None, max_length=2000)


class MarketSpendResearch(M.Contract):
    description: str = Field(min_length=1, max_length=500)


def install(app, store, auth, mutate):
    def reader(who):
        auth.domain(who)

    @app.get("/api/v2/market/spend")
    def spend_view(request: Request, dimension: Literal["offering", "buyer"] = "offering",
                   basis: Literal["product", "platform"] = "product", year: int | None = Query(None, ge=1990, le=2100)):
        reader(request.state.identity)
        with store.read() as c:
            return spend(c, dimension, basis, year)

    @app.put("/api/v2/market/definition")
    def definition_put(request: Request, body: MarketDefinitionPatch):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            MK.require_writer(who)
            return set_definition(c, who.actor, body.model_dump(exclude_unset=True))

        return mutate(request, body, work)

    @app.post("/api/v2/market/segments")
    def segment_create(request: Request, body: MarketSegmentCreate):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            MK.require_writer(who)
            return {"segment": add_segment(c, who.actor, body.name, body.parent_id, body.summary)}

        return mutate(request, body, work)

    @app.post("/api/v2/market/sizes")
    def size_create(request: Request, body: MarketSizeCreate):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            MK.require_writer(who)
            return {"size": create_size(c, who.actor, body.model_dump())}

        return mutate(request, body, work)

    @app.post("/api/v2/market/sizes/{size_id}/accept")
    def size_accept(request: Request, size_id: str, body: M.Empty):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            MK.require_writer(who)
            return {"size": accept_size(c, who.actor, size_id)}

        return mutate(request, body, work)

    @app.post("/api/v2/market/sizes/{size_id}/reject")
    def size_reject(request: Request, size_id: str, body: M.Empty):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            MK.require_writer(who)
            return {"size": reject_size(c, who.actor, size_id)}

        return mutate(request, body, work)

    @app.post("/api/v2/market/sizes/{size_id}/params")
    def size_params(request: Request, size_id: str, body: MarketSizeParams):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            if who.role != "owner":
                raise Problem("forbidden", "Only the owner changes the parameters of an estimate", 403)
            return {"size": change_params(c, who.actor, size_id, body.params)}

        return mutate(request, body, work)

    @app.post("/api/v2/market/figures")
    def figure_create(request: Request, body: MarketFigureCreate):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            MK.require_writer(who)
            return {"figure": create_figure(c, who.actor, body.model_dump())}

        return mutate(request, body, work)

    @app.get("/api/v2/market/sources")
    def sources_list(request: Request):
        reader(request.state.identity)
        with store.read() as c:
            return {"sources": sources(c)}

    @app.post("/api/v2/market/sources")
    def source_create(request: Request, body: MarketSourceCreate):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            MK.require_writer(who)
            return {"source": add_source(c, who.actor, body.model_dump(exclude_none=True))}

        return mutate(request, body, work)

    @app.post("/api/v2/market/spend/research")
    def spend_research(request: Request, body: MarketSpendResearch):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            return research(c, auth, who, body.description)

        return mutate(request, body, work)

    @app.get("/api/v2/market/value-chain")
    def value_chain_view(request: Request):
        reader(request.state.identity)
        with store.read() as c:
            return value_chain(c)
