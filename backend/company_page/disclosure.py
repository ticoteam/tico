"""What each audience may see of each data class: Show, Rounded, Range, Delayed N days or Hidden. A class with no rule is
Hidden, and so is anything this module does not recognise.

A rule's `rounding` is a few space-separated tokens. `sig:2` or `nearest:1000` say how Rounded rounds, `bands` or
`step:1000000` say how Range groups values, and `quarterly` (with any mode but Hidden) discloses whole quarters only."""
import re
from collections import namedtuple

from .. import hubdb as H
from ..store import Problem
from . import audiences as A
from . import settings as S

# `customer_names` stands apart from `customers` so Investors can have the count without the names, and `mission` is a
# class of its own so Quiet can mean nothing at all.
FINANCE_CLASSES = ("revenue", "growth", "gross_margin", "burn", "cash_runway", "fundraising", "ai_spend", "pnl_line")
NUMBER_CLASSES = (*FINANCE_CLASSES, "headcount", "customers", "pipeline", "kpi")
DATA_CLASSES = (*NUMBER_CLASSES, "customer_names", "product", "people", "legal", "security", "mission")
MODES = ("show", "rounded", "range", "delayed", "hidden")
HIDDEN = {"mode": "hidden", "lag_days": 0, "rounding": None}
LAG_MAX = 3650
REF_MAX = 200
# How much a mode gives away, least first. Delayed is exact, only later.
RANK = {"hidden": 0, "range": 1, "rounded": 2, "delayed": 3, "show": 4}

Rounding = namedtuple("Rounding", "method n quarterly")
_METHOD = re.compile(r"(sig|nearest|step):(\d{1,15})")


def parse_rounding(text):
    """Rounding(method, n, quarterly). Raises ValueError on anything it does not know, which callers treat as refused
    or, when comparing rules, as loosening."""
    method, n, quarterly = None, 0, False
    for token in str(text or "").split():
        match = _METHOD.fullmatch(token)
        if token == "quarterly":
            quarterly = True
        elif token == "bands" and method is None:
            method = "bands"
        elif match and method is None and int(match[2]) > 0:
            method, n = match[1], int(match[2])
        else:
            raise ValueError(f"unknown rounding {token!r}")
    if method == "sig" and not 1 <= n <= 6:
        raise ValueError("sig is 1 to 6")
    return Rounding(method, n, quarterly)


def _clean_rounding(mode, text):
    """The stored form. A rule with no method gets the plain default, so the row says what it does."""
    try:
        r = parse_rounding(text)
    except ValueError as exc:
        raise Problem("rounding", str(exc), 422) from None
    allowed = {"rounded": ("sig", "nearest"), "range": ("bands", "step")}.get(mode, ())
    if r.method and r.method not in allowed:
        raise Problem("rounding", f"{r.method} does not apply to {mode}", 422)
    default = {"rounded": "sig:2", "range": "bands"}.get(mode)
    method = (r.method if not r.n else f"{r.method}:{r.n}") if r.method else default
    tokens = [t for t in (method, "quarterly" if r.quarterly else None) if t]
    return " ".join(tokens) or None


def _column(**rules):
    """A preset's rules for one audience: every class is written, so a preset replaces whatever came before."""
    return {cls: rules.get(cls, HIDDEN) for cls in DATA_CLASSES}


_SHOW = {"mode": "show", "lag_days": 0, "rounding": None}
_DELAYED_30 = {"mode": "delayed", "lag_days": 30, "rounding": None}
_QUARTERLY = {"mode": "rounded", "lag_days": 0, "rounding": "sig:2 quarterly"}
_QUARTERLY_COUNT = {"mode": "rounded", "lag_days": 0, "rounding": "sig:1 quarterly"}   # small counts: 12 stays 12 at two
_EVERYTHING = {cls: _SHOW for cls in DATA_CLASSES}
_NUMBERS = {cls: _SHOW for cls in NUMBER_CLASSES}
_PUBLIC_STANDARD = _column(product=_SHOW, headcount=_SHOW, mission=_SHOW)
# A custom audience could be anyone, so a preset gives it the Public column until its owner says otherwise.
PRESETS = {
    "radical": {"public": {**_EVERYTHING, **{cls: _DELAYED_30 for cls in FINANCE_CLASSES}},
                "investors": _EVERYTHING, "board": _EVERYTHING},
    "standard": {"public": _PUBLIC_STANDARD, "investors": _column(**_NUMBERS, product=_SHOW, mission=_SHOW),
                 "board": _EVERYTHING},
    "quiet": {"public": _column(),
              "investors": _column(**{**{cls: _QUARTERLY for cls in NUMBER_CLASSES}, "headcount": _QUARTERLY_COUNT,
                                      "customers": _QUARTERLY_COUNT, "mission": _SHOW}),
              "board": _EVERYTHING},
}


def _plain(row):
    return {"mode": row["mode"], "lag_days": int(row["lag_days"] or 0), "rounding": row["rounding"]}


def all_rules(c, audience_id):
    """{(data class, ref id): rule}, one query, for the reads that look up many cells."""
    return {(r["data_class"], r["ref_id"]): _plain(r) for r in c.execute(
        "SELECT data_class, ref_id, mode, lag_days, rounding FROM disclosure_rules WHERE audience_id=?", (audience_id,))}


def lookup(rules, data_class, ref_id=None):
    """The cell's own rule, else the class's, else Hidden. An unknown class has none, whatever a row says."""
    if data_class not in DATA_CLASSES:
        return dict(HIDDEN)
    return dict(rules.get((data_class, ref_id or "")) or rules.get((data_class, "")) or HIDDEN)


def rule_for(c, audience_id, data_class, ref_id=None):
    return lookup(all_rules(c, audience_id), data_class, ref_id)


def rules(c, audience_id):
    """Every rule of an audience, for the grid."""
    return [{"data_class": cls, "ref_id": ref, **rule} for (cls, ref), rule in sorted(all_rules(c, audience_id).items())]


def current_revision(c, audience_id):
    row = c.execute("SELECT id FROM disclosure_revisions WHERE audience_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (audience_id,)).fetchone()
    return row[0] if row else None


def _coarser(old, new):
    """Whether `new` is surely no finer than `old`, both of one mode. A nearest of 1000 is not provably coarser than a
    nearest of 700, so only exact multiples count."""
    if old.method == new.method:
        if old.method == "sig":
            return new.n <= old.n
        if old.method in ("nearest", "step"):
            return new.n % old.n == 0
        return True
    return False


def is_loosening(old_rule, new_rule):
    """Whether moving from `old_rule` to `new_rule` could show anyone more. A loosening needs a new stats approval; a
    tightening applies at once. When it can't be told, it counts as loosening: asking again costs one click."""
    try:
        old, new = dict(HIDDEN) if old_rule is None else old_rule, dict(HIDDEN) if new_rule is None else new_rule
        before, after = old["mode"], new["mode"]
        if before not in MODES or after not in MODES:
            return True
        if after == "hidden":
            return False
        if before == "hidden":
            return True
        was, now = parse_rounding(old.get("rounding")), parse_rounding(new.get("rounding"))
        if was.quarterly and not now.quarterly:
            return True
        if before == after:
            if before == "delayed":
                return int(new.get("lag_days") or 0) < int(old.get("lag_days") or 0)
            if before in ("rounded", "range"):
                # An unset method is the default the row would store.
                was = was._replace(method=was.method or ("sig" if before == "rounded" else "bands"),
                                   n=was.n or (2 if before == "rounded" else 0))
                now = now._replace(method=now.method or ("sig" if before == "rounded" else "bands"),
                                   n=now.n or (2 if before == "rounded" else 0))
                return not _coarser(was, now)
            return False
        if before == "show":
            return False
        # Sig-rounded to 1-2-5 bands is the one coarser pair across modes; a delayed value turning current is not.
        return not (before == "rounded" and after == "range" and was.method in (None, "sig")
                    and now.method in (None, "bands"))
    except (AttributeError, KeyError, TypeError, ValueError):
        return True


def _clean_rule(data_class, ref_id, mode, lag_days, rounding):
    if data_class not in DATA_CLASSES:
        raise Problem("data_class", "Unknown data class", 422)
    if mode not in MODES:
        raise Problem("mode", f"Mode is {', '.join(MODES)}", 422)
    ref = str(ref_id or "").strip()
    if len(ref) > REF_MAX:
        raise Problem("ref_id", "That reference is too long", 422)
    if mode == "delayed":
        if isinstance(lag_days, bool) or not isinstance(lag_days, int) or not 1 <= lag_days <= LAG_MAX:
            raise Problem("lag_days", f"Delayed takes 1 to {LAG_MAX} days", 422)
    else:
        lag_days = 0
    return {"data_class": data_class, "ref_id": ref, "mode": mode, "lag_days": lag_days,
            "rounding": None if mode == "hidden" else _clean_rounding(mode, rounding)}


def _write(c, audience_id, rule, ts):
    c.execute("INSERT INTO disclosure_rules (id, audience_id, data_class, ref_id, mode, lag_days, rounding, updated_at) "
              "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(audience_id, data_class, ref_id) DO UPDATE SET mode=excluded.mode, "
              "lag_days=excluded.lag_days, rounding=excluded.rounding, updated_at=excluded.updated_at",
              (H.new_id(), audience_id, rule["data_class"], rule["ref_id"], rule["mode"], rule["lag_days"],
               rule["rounding"], ts))


def _finish(c, actor, audience, loosened, approved, ts, action, detail):
    """One revision for the change. A pure tightening keeps an approved stats board approved; a loosening does not, so
    a person has to look again. The public page changes in this step either way. Returns what the change was, for the
    route to say: a tightening leaves published editions up, so the caller reports how many."""
    from . import stats
    revision = H.new_id()
    c.execute("INSERT INTO disclosure_revisions (id, audience_id, created_by, created_at) VALUES (?,?,?,?)",
              (revision, audience["id"], actor, ts))
    if approved and not loosened:
        stats.carry_board_approval(c, audience["id"])
    H.event(c, actor, action, audience["id"], {**detail, "revision": revision, "loosened": loosened})
    if audience["kind"] == "public":
        S.rebuild_if_live(c, "disclosure changed")
    return {"revision": revision, "loosened": loosened}


def set_rules(c, actor, audience_id, changes):
    """Several cells, one revision. Each is compared against what that cell resolved to before, so a new cell that only
    narrows its class is a tightening. Returns {"revision", "loosened", "tightened"}."""
    from . import stats
    S.require_person(c, actor)
    audience = A.need(c, audience_id)
    wanted = [_clean_rule(r["data_class"], r.get("ref_id"), r["mode"], r.get("lag_days", 0), r.get("rounding"))
              for r in changes]
    before = all_rules(c, audience_id)
    moved = [r for r in wanted if before.get((r["data_class"], r["ref_id"])) !=
             {"mode": r["mode"], "lag_days": r["lag_days"], "rounding": r["rounding"]}]
    if not moved:
        return {"revision": current_revision(c, audience_id), "loosened": False, "tightened": False}
    approved = stats.board_approved(c, audience_id)
    loosened = any(is_loosening(lookup(before, r["data_class"], r["ref_id"]), r) for r in moved)
    ts = H.now()
    for rule in moved:
        _write(c, audience_id, rule, ts)
    return _tightened(_finish(c, actor, audience, loosened, approved, ts, "company_page.disclosure",
                              {"cells": [[r["data_class"], r["ref_id"], r["mode"]] for r in moved][:50]}))


def set_rule(c, actor, audience_id, data_class, ref_id=None, mode="hidden", lag_days=0, rounding=None):
    return set_rules(c, actor, audience_id, [{"data_class": data_class, "ref_id": ref_id, "mode": mode,
                                              "lag_days": lag_days, "rounding": rounding}])


def preset_column(name, kind):
    column = PRESETS.get(name)
    if not column:
        raise Problem("preset", f"A preset is {', '.join(PRESETS)}", 422)
    return column.get(kind) or column["public"]


def apply_preset(c, actor, audience_id, name):
    """Replaces every rule, per-KPI and per-line ones included. Any cell it would loosen makes the whole change a
    loosening."""
    from . import stats
    S.require_person(c, actor)
    audience = A.need(c, audience_id)
    column = {(cls, ""): {"mode": r["mode"], "lag_days": r["lag_days"], "rounding": r["rounding"]}
              for cls, r in preset_column(name, audience["kind"]).items()}
    before = all_rules(c, audience_id)
    approved = stats.board_approved(c, audience_id)
    loosened = any(is_loosening(lookup(before, cls, ref), lookup(column, cls, ref)) for cls, ref in {*before, *column})
    ts = H.now()
    c.execute("DELETE FROM disclosure_rules WHERE audience_id=?", (audience_id,))
    for (cls, ref), rule in column.items():
        _write(c, audience_id, {"data_class": cls, "ref_id": ref, **rule}, ts)
    return _tightened(_finish(c, actor, audience, loosened, approved, ts, "company_page.preset", {"preset": name}))


def _tightened(change):
    return {**change, "tightened": not change["loosened"]}
