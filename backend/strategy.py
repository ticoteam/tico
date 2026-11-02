"""The team's strategy (docs/strategy.md): the few bets it is making, written down, versioned, read by every bot, and
watched against the numbers and the market.

- The page is one row at a time in `strategy_versions`: a summary (400 characters, what every bot reads on every run),
  the vision (400 characters, kept but not shown on the Strategy page; the Goals page reads it from here and falls
  back to the summary), four fixed sections (`serve`, `sell`, `know`, `not`)
  plus any extra ones the owner adds (`extra: {key: {title, body}}`), and a snapshot of the bets. Exactly one row is
  the `draft` (`n` NULL); publishing stamps it `n` (1, 2, 3...), snapshots every bet that is not retired, and opens a
  fresh draft copied from it. The draft carries a `revision`: a write against an older one answers 409, so two
  editors never overwrite each other silently.
- Bets (`strategy_bets`) are the spine: a statement that could turn out wrong, why, what it rules out, what would show
  it is wrong (`wrong_if`: a KPI crossing a threshold, a market thesis changing, or a sentence with a check date), the
  market ids it rests on (`cites`), and a status (proposed, active, retired). `rules_out` and `wrong_if` are optional
  (the fit check watches a bet only when it has a `wrong_if`), and at most 7 are active. Goals and groups link to a bet (`goals.bet_id`, a group's `bet_id`); a retired
  bet keeps its goals.
- The default guide (`strategy_guides/default.yaml`) is the questions the Strategy Bot drafts a first strategy with. The
  exercises run against a strategy are lenses (backend/lenses.py).
- Every change is a `strategy_events` row with the old and new values, like `market_events`.
- Publishing emits `strategy.published`, puts `/strategy/summary` in every run's context from the next run
  (backend/learned.py `for_turn`, runner-rs/src/prompt.rs), and files one Decide card for the owner when a bet with
  goals was retired or rewritten (`strategy_realign`, decided through the goal proposals).
- `watch` is the fit check: each active bet of the published version is checked against its wrong-if from the KPI
  status pass, from each KPI reading and from each market write. A trip files one task for the Strategy Bot per bet per
  version, at every level.

Who may: everyone signed in reads the published version; only the owner, on their own click, sees and writes the
draft and the bets and publishes (backend/goal_routes.py `company_editor`). A bot, the Strategy Bot included, proposes
(`goal_proposals` kind `strategy_draft`) and the owner confirms; a confirmed proposal writes the draft, never publishes.

Unversioned and idempotent (`ensure`), like the company profile: an older release never reads the tables or the
columns. The first `ensure` on an install makes the draft from the company profile's vision and description.
"""
import functools
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import yaml
from fastapi import Request
from fastapi.responses import PlainTextResponse

from . import growth as G
from . import hubdb as H
from . import models as M
from .store import Problem

SUMMARY_MAX = 400          # one paragraph: what every bot reads on every run
VISION_MAX = 400           # one or two sentences, the limit the Goals page has had
SECTION_MAX = 4_000        # one markdown section
TITLE_MAX = 80             # an extra section's heading
EXTRA_MAX = 12             # extra sections on one page
NOTE_MAX = 500             # what a version was for
STATEMENT_MAX = 300        # a bet, in one sentence
BET_TEXT_MAX = 2_000       # a bet's why and what it rules out
WRONG_TEXT_MAX = 500       # a wrong-if said in words
CITES_MAX = 20
ACTIVE_MAX = 7             # 3 to 5 recommended; more than 7 active is no longer a strategy
LIVE_MAX = 30              # proposed and active together: a backlog, not a list of bets
RETIRED_SHOWN = 20         # the owner's view also lists the latest retired bets
GOALS_SHOWN = 20           # goals listed under one bet
VERSIONS_PAGE = 100
DEFAULT_GUIDE = "default"
BET_STATUSES = ("proposed", "active", "retired")
IDEAS_MAX = 20             # growth ideas one bet may list in a proposal
WRONG_KINDS = ("kpi", "thesis", "text")
DIRECTIONS = ("above", "below")
FIXED = (("serve", "Who we serve"), ("sell", "What we sell, and for how much"),
         ("know", "What we know that others don't"), ("not", "What we won't do"))
FIXED_KEYS = tuple(k for k, _ in FIXED)
TITLES = dict(FIXED)
EXTRA_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
FIELDS = ("guide", "summary", "vision", "sections")
SNAPSHOT = ("id", "statement", "why", "rules_out", "wrong_if", "cites", "status", "rank")
EVENT = "strategy.published"
REALIGN = "strategy_realign"
TRIPPED = "bet_tripped"
LENS = "lens"
BRIEFING = "briefing"
SOURCES = (TRIPPED, LENS, BRIEFING)       # what may prompt a strategy_draft proposal
PLAYBOOK = "playbooks/bet-tripped.md"
GUIDES_DIR = Path(__file__).resolve().parent / "strategy_guides"

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS strategy_versions (id TEXT PRIMARY KEY, n INTEGER UNIQUE, "
    "status TEXT NOT NULL CHECK (status IN ('draft','published')), guide TEXT NOT NULL DEFAULT 'default', "
    "summary TEXT NOT NULL DEFAULT '', vision TEXT NOT NULL DEFAULT '', sections TEXT NOT NULL DEFAULT '{}', "
    "bets TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0, "
    "updated_at TEXT, updated_by TEXT, published_at TEXT, published_by TEXT)",
    # The one-draft rule.
    "CREATE UNIQUE INDEX IF NOT EXISTS strategy_versions_draft ON strategy_versions(status) WHERE status='draft'",
    "CREATE TABLE IF NOT EXISTS strategy_bets (id TEXT PRIMARY KEY, statement TEXT NOT NULL, "
    "why TEXT NOT NULL DEFAULT '', rules_out TEXT NOT NULL DEFAULT '', wrong_if TEXT, cites TEXT NOT NULL DEFAULT '[]', "
    "status TEXT NOT NULL CHECK (status IN ('proposed','active','retired')), since_n INTEGER, until_n INTEGER, "
    "rank INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS strategy_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, actor TEXT NOT NULL, "
    "created_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')",
    "CREATE INDEX IF NOT EXISTS strategy_events_at ON strategy_events(created_at)",
    "CREATE INDEX IF NOT EXISTS strategy_events_kind ON strategy_events(kind, created_at)",
)


def ensure(c):
    """The tables, the goals column, and once the draft: from the company profile's vision (the vision) and
    description (what we sell) where an install has them, empty otherwise. Every start brings
    company_profile.vision in line with the team's vision (sync_vision), whatever an older release left there."""
    for statement in SCHEMA:
        c.execute(statement)
    H.add_column(c, "goals", "bet_id", "TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS goals_bet ON goals(bet_id) WHERE bet_id IS NOT NULL")
    if c.execute("SELECT 1 FROM strategy_versions LIMIT 1").fetchone():
        try:
            sync_vision(c)
        except sqlite3.OperationalError:      # an install from before the company profile
            pass
        return False
    try:
        profile = H._one(c, "SELECT vision, description FROM company_profile WHERE id='company'") or {}
    except sqlite3.OperationalError:          # an install from before the company profile
        profile = {}
    vision = " ".join(str(profile.get("vision") or "").split())[:VISION_MAX]
    description = str(profile.get("description") or "").strip()[:SECTION_MAX]
    sections = _sections({"sell": description})
    now = H.now()
    c.execute("INSERT INTO strategy_versions (id, n, status, guide, summary, vision, sections, bets, note, revision, "
              "updated_at, updated_by) VALUES (?,NULL,'draft',?,'',?,?,'[]','',0,?,?)",
              (H.new_id(), DEFAULT_GUIDE, vision, dumps(sections), now, H.KEEPER))
    if vision or description:
        _event(c, H.KEEPER, "migrated", {"new": {"vision": vision, "sell": description}})
    return True


# ----------------------------------------------------------------------------- small helpers
def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def loads(value, default):
    try:
        found = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return found if isinstance(found, type(default)) else default


def _event(c, actor, kind, payload):
    c.execute("INSERT INTO strategy_events (id, kind, actor, created_at, payload) VALUES (?,?,?,?,?)",
              (H.new_id(), kind, actor, H.now(), dumps(payload or {})))


def _clean(c, actor, value, limit, what, one_line=False):
    text = " ".join(str(value or "").split()) if one_line else str(value or "").strip()
    if len(text) > limit:
        H.refuse(c, actor, "lint", f"keep the {what} under {limit} characters")
    return text


def _sections(raw):
    """The stored shape, whatever was stored: every fixed key (a string) and `extra` ({key: {title, body}})."""
    raw = raw if isinstance(raw, dict) else {}
    out = {k: str(raw.get(k) or "") for k in FIXED_KEYS}
    extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
    out["extra"] = {k: {"title": str((v or {}).get("title") or k), "body": str((v or {}).get("body") or "")}
                    for k, v in extra.items() if isinstance(v, dict)}
    return out


def _one_line(text, limit=200):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# ----------------------------------------------------------------------------- guides
@functools.lru_cache(maxsize=1)
def guides():
    """Every guide, as its YAML says, the default first. Loaded once: the files ship with the release."""
    found = {}
    for path in GUIDES_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        found[data["id"]] = {"id": data["id"], "name": data["name"], "description": data.get("description") or "",
                             "listed": bool(data.get("listed")), "bets_label": data["bets_label"],
                             "bet_label": data["bet_label"],
                             "questions": [dict(q) for q in data.get("questions") or []],
                             "suggested_sections": [dict(s) for s in data.get("suggested_sections") or []]}
    return [found[g] for g in sorted(found, key=lambda g: (g != DEFAULT_GUIDE, g))]


def guide(gid):
    return next((g for g in guides() if g["id"] == gid), None) or guide(DEFAULT_GUIDE)


def listed_guides():
    """What GET /strategy/guides offers: only the default until the page can switch guides."""
    return [g for g in guides() if g["listed"]]


# ----------------------------------------------------------------------------- reads
def _version(row):
    if not row:
        return None
    row = dict(row)
    row["sections"] = _sections(loads(row.get("sections"), {}))
    row["bets"] = loads(row.get("bets"), [])
    return row


def draft(c):
    return _version(H._one(c, "SELECT * FROM strategy_versions WHERE status='draft'"))


def current(c):
    """The latest published version, or None before the first publish."""
    return _version(H._one(c, "SELECT * FROM strategy_versions WHERE status='published' ORDER BY n DESC LIMIT 1"))


def version(c, n):
    return _version(H._one(c, "SELECT * FROM strategy_versions WHERE status='published' AND n=?", (n,)))


def versions(c, limit=VERSIONS_PAGE):
    """The published versions, newest first: what the history list shows, without the sections and the bets."""
    return H._rows(c.execute("SELECT n, note, published_at, published_by, guide, summary FROM strategy_versions "
                             "WHERE status='published' ORDER BY n DESC LIMIT ?", (max(1, min(limit, VERSIONS_PAGE)),)))


def _bet(row):
    if not row:
        return None
    row = dict(row)
    row["wrong_if"] = loads(row.get("wrong_if"), {}) or None
    row["cites"] = loads(row.get("cites"), [])
    return row


def bet(c, bet_id):
    return _bet(H._one(c, "SELECT * FROM strategy_bets WHERE id=?", (bet_id,)))


def bets(c, status=None):
    """Bets in rank order; `status` narrows them to one status."""
    if status:
        rows = c.execute("SELECT * FROM strategy_bets WHERE status=? ORDER BY rank, created_at", (status,))
    else:
        rows = c.execute("SELECT * FROM strategy_bets ORDER BY rank, created_at")
    return [_bet(r) for r in rows]


def vision(c, editor=False):
    """The one vision: the draft's for the owner, the published one for everyone else. Before the first publish the
    draft's is the team's vision for everyone, as it was before Strategy: a team that never publishes keeps it. The
    Strategy page shows only the summary, so an empty vision is the summary."""
    row = None if editor else \
        H._one(c, "SELECT vision, summary FROM strategy_versions WHERE status='published' ORDER BY n DESC LIMIT 1")
    row = row or H._one(c, "SELECT vision, summary FROM strategy_versions WHERE status='draft'") or {}
    return row.get("vision") or " ".join(str(row.get("summary") or "").split())


def website(c):
    """The team's website as setup recorded it (backend/onboarding.py `names.website`), or None."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key='onboarding'").fetchone()
    names = (loads(row[0], {}) if row else {}).get("names") or {}
    return str(names.get("website") or "").strip() or None


def goals_by_bet(c, auth, who, bet_ids):
    """{bet id: [{id, title, owner, status}]}, only the goals the caller may read (goal_routes.readable_goals)."""
    from . import goal_routes
    if not bet_ids:
        return {}
    marks = ",".join("?" * len(bet_ids))
    rows = H._rows(c.execute(f"SELECT * FROM goals WHERE bet_id IN ({marks}) ORDER BY owner_id, rank, created_at",
                             tuple(bet_ids)))
    out = {}
    for row in goal_routes.readable_goals(c, auth, who, rows):
        out.setdefault(row["bet_id"], []).append(row)
    return out


def health(c, goals):
    """A bet's colour: the worst KPI-judged colour of its live goals, `gray` when none is judged by its KPIs. Never
    a goal's lifecycle (done, dropped), never a check-in or a task count."""
    from . import goals as G
    worst = None
    for row in goals:
        if row.get("status") in ("done", "dropped"):
            continue
        score = G.score(c, row)
        if score["basis"] == "kpis" and score["status"] in G.COLOURS:
            if worst is None or G.COLOURS.index(score["status"]) < G.COLOURS.index(worst):
                worst = score["status"]
    return worst or "gray"


def view(c, auth, who):
    """GET /strategy. The owner gets the draft, every live bet and the latest retired ones; everyone else the
    published version's bets only. No guide prompts: they are GET /strategy/guides."""
    from . import goal_routes
    editor = goal_routes.company_editor(who)
    published = current(c)
    mine = draft(c) if editor else None
    if editor:
        listed = [b for b in bets(c) if b["status"] != "retired"]
        listed += [_bet(r) for r in c.execute("SELECT * FROM strategy_bets WHERE status='retired' "
                                              "ORDER BY updated_at DESC LIMIT ?", (RETIRED_SHOWN,))]
    else:
        listed = list((published or {}).get("bets") or [])
    gid = (mine or published or {}).get("guide") or DEFAULT_GUIDE
    linked = goals_by_bet(c, auth, who, [b["id"] for b in listed])
    # The draft is the owner's: a bot never reads an unpublished "What we won't do".
    return {**({"draft": mine} if editor else {}), "current": published, "bets": listed,
            "label": guide(gid)["bets_label"], "label_one": guide(gid)["bet_label"], "may_edit": editor,
            "website": website(c),
            "goals_by_bet": {bid: [{k: g[k] for k in ("id", "title", "owner_id", "status")} for g in rows[:GOALS_SHOWN]]
                             for bid, rows in linked.items()},
            # goals_by_bet is a sample; a retire's "N goals serve it" needs every live goal the caller may read.
            "live_by_bet": {bid: sum((g.get("status") or "") not in ("done", "dropped") for g in rows)
                            for bid, rows in linked.items()},
            "health_by_bet": {b["id"]: health(c, linked.get(b["id"], [])) for b in listed}}


# ----------------------------------------------------------------------------- the words
def wrong_if_text(c, wrong):
    """A wrong-if in one line: "churn goes above 4 by 2027-03-31"."""
    if not wrong:
        return ""
    by = f" by {wrong['check_by']}" if wrong.get("check_by") else ""
    if wrong["kind"] == "kpi":
        from . import kpis as K
        record = K.kpi(c, wrong["kpi_id"]) or {}
        value = wrong["threshold"]
        value = int(value) if float(value).is_integer() else value
        return f"{record.get('name') or wrong['kpi_id']} goes {wrong['direction']} {value}{by}"
    if wrong["kind"] == "thesis":
        return f"the market changes on {wrong['market_id']}{by}"
    return _one_line(wrong.get("text")) + by


def summary_text(c, published=None):
    """What every run's context carries: the summary, one line per active bet with its wrong-if, the won't-do line,
    then the version. Empty before the first publish."""
    published = published or current(c)
    if not published:
        return ""
    lines = [published["summary"]] if published["summary"] else []
    active = [b for b in published["bets"] if b.get("status") == "active"]
    if active:
        label = guide(published["guide"])["bets_label"]
        lines.append(label + ":\n" + "\n".join(
            f"{i}. {b['statement']}" + (f" Wrong if {wrong_if_text(c, b.get('wrong_if'))}." if b.get("wrong_if") else "")
            for i, b in enumerate(active, 1)))
    if published["sections"]["not"]:
        lines.append("We won't: " + _one_line(published["sections"]["not"], 300))
    lines.append(f"version {published['n']}")
    return "\n\n".join(lines)


def diff(c, a, b):
    """Two published versions: the fields that changed, and the bets added, retired or changed, by bet id and status.
    A snapshot holds every bet that was not retired, so a bet missing from `to` was retired by then; a bet moved
    back to proposed is a change, not a retirement."""
    one, two = version(c, a), version(c, b)
    if not one or not two:
        raise Problem("not_found", f"There is no strategy version {a if not one else b}", 404)
    fields = [{"key": k, "from": one[k], "to": two[k]} for k in ("summary", "vision", "guide") if one[k] != two[k]]
    for key in FIXED_KEYS:
        if one["sections"][key] != two["sections"][key]:
            fields.append({"key": "sections." + key, "from": one["sections"][key], "to": two["sections"][key]})
    extra_a, extra_b = one["sections"]["extra"], two["sections"]["extra"]
    for key in sorted(set(extra_a) | set(extra_b)):
        if extra_a.get(key) != extra_b.get(key):
            fields.append({"key": "sections.extra." + key, "from": extra_a.get(key), "to": extra_b.get(key)})
    before = {x["id"]: x for x in one["bets"]}
    after = {x["id"]: x for x in two["bets"]}
    retired = [{**before[i], "status": "retired"} for i in before
               if i not in after or after[i].get("status") == "retired"]
    changed = [{"id": i, "from": before[i], "to": after[i]} for i in after
               if i in before and after[i].get("status") != "retired"
               and any(before[i].get(k) != after[i].get(k) for k in SNAPSHOT if k != "rank")]
    return {"from": a, "to": b, "fields": fields,
            "bets": {"added": [after[i] for i in after if i not in before], "retired": retired, "changed": changed}}


# ----------------------------------------------------------------------------- the draft
def _check_revision(c, row, revision):
    if revision is not None and int(revision) != int(row["revision"]):
        raise Problem("conflict", f"The strategy draft changed since you opened it (revision {row['revision']}, not "
                      f"{revision}); reload it and make your change again", 409, extra={"revision": row["revision"]})


def _bump(c, actor, row, changes=None):
    """Save `changes` on the draft (column: value, already clean) and move its revision on."""
    changes = dict(changes or {})
    if "sections" in changes:
        changes["sections"] = dumps(changes["sections"])
    sets = "".join(f"{k}=:{k}, " for k in changes)
    c.execute(f"UPDATE strategy_versions SET {sets}revision=revision+1, updated_at=:updated_at, updated_by=:updated_by "
              "WHERE id=:id", {**changes, "updated_at": H.now(), "updated_by": actor, "id": row["id"]})
    return row["revision"] + 1


def _merge_sections(c, actor, stored, sent):
    """`sent` merged onto `stored`: a fixed key sent is set and "" clears it; an extra section sent as {title, body}
    is set, sent as "" (or with an empty body) is removed; anything not sent is kept, whatever the guide."""
    if not isinstance(sent, dict):
        H.refuse(c, actor, "kind", "sections is an object: {serve, sell, know, not, extra: {key: {title, body}}}")
    out = _sections(stored)
    for key, value in sent.items():
        if key == "extra":
            if not isinstance(value, dict):
                H.refuse(c, actor, "kind", "extra is an object of {key: {title, body}}")
            for name, item in value.items():
                if not EXTRA_KEY.fullmatch(str(name)) or name in FIXED_KEYS:
                    H.refuse(c, actor, "kind", f"{name!r} is not a section key: lowercase letters, digits and dashes")
                if item in ("", None) or (isinstance(item, dict) and not str(item.get("body") or "").strip()):
                    out["extra"].pop(name, None)
                    continue
                if not isinstance(item, dict):
                    H.refuse(c, actor, "kind", f"extra section {name} is {{title, body}}")
                title = _clean(c, actor, item.get("title") or out["extra"].get(name, {}).get("title") or name,
                               TITLE_MAX, f"{name} title", one_line=True)
                out["extra"][name] = {"title": title, "body": _clean(c, actor, item.get("body"), SECTION_MAX,
                                                                     f"{title} section")}
            if len(out["extra"]) > EXTRA_MAX:
                H.refuse(c, actor, "lint", f"a strategy has at most {EXTRA_MAX} extra sections")
        elif key in FIXED_KEYS:
            out[key] = _clean(c, actor, value, SECTION_MAX, f'"{TITLES[key]}" section')
        else:
            H.refuse(c, actor, "kind", f"{key} is not a section; one of {', '.join(FIXED_KEYS)}, or extra")
    return out


def update_draft(c, actor, revision=None, guide_id=None, summary=None, vision=None, sections=None):
    """Save what changed on the draft, only the fields sent ("" clears one). `revision` is the draft's revision the
    writer read; another one answers 409. None skips the check: a confirmed proposal and the Goals page's vision
    write the draft as it is now."""
    H._writer(c, actor)
    row = draft(c)
    _check_revision(c, row, revision)
    changes = {}
    if guide_id is not None and guide_id != row["guide"]:
        if guide_id not in [g["id"] for g in guides()]:
            H.refuse(c, actor, "kind", f"a guide is {'|'.join(g['id'] for g in guides())}, not {guide_id}")
        changes["guide"] = guide_id
    if summary is not None:
        changes["summary"] = _clean(c, actor, summary, SUMMARY_MAX, "summary")
    if vision is not None:
        changes["vision"] = _clean(c, actor, vision, VISION_MAX, "vision", one_line=True)
    if sections is not None:
        changes["sections"] = _merge_sections(c, actor, row["sections"], sections)
    changes = {k: v for k, v in changes.items() if v != row[k]}
    if not changes:
        return row
    _bump(c, actor, row, changes)
    _event(c, actor, "draft", {"old": {k: row[k] for k in changes}, "new": changes})
    if ("vision" in changes or "summary" in changes) and not current(c):
        # Before the first publish the draft's vision is the team's (vision()), so its history goes beside the
        # description's (GET /company/history); after it, an unpublished vision stays the owner's until publish.
        # The summary stands in for an empty vision, so a summary change moves it too.
        if "vision" in changes:
            _vision_event(c, actor, row["vision"], changes["vision"])
        sync_vision(c)
    H.event(c, actor, "strategy.draft", "", {"fields": sorted(changes)})
    return draft(c)


def _vision_event(c, actor, old, new):
    """The vision everyone sees changed: its history on the Goals page (GET /company/history)."""
    c.execute("INSERT INTO company_events (id, created_at, actor, field, old, new) VALUES (?,?,?,?,?,?)",
              (H.new_id(), H.now(), actor, "vision", old, new))


def sync_vision(c):
    """company_profile.vision, which hub SQL readers see, set to the vision everyone sees (vision()). The text
    lives in the versions; the column only follows them."""
    public = vision(c)
    row = c.execute("SELECT vision FROM company_profile WHERE id='company'").fetchone()
    if row and row[0] == public:
        return
    c.execute("INSERT INTO company_profile (id, vision, description) VALUES ('company',?,'') "
              "ON CONFLICT(id) DO UPDATE SET vision=excluded.vision", (public,))


# ----------------------------------------------------------------------------- bets
def _wrong_if(c, actor, value):
    """A wrong-if, checked and in its stored shape, or None for none. {kind: kpi, kpi_id, direction above|below,
    threshold}, {kind: thesis, market_id} or {kind: text, text, check_by}; `check_by` (YYYY-MM-DD) on any kind."""
    if not value:
        return None
    if not isinstance(value, dict):
        H.refuse(c, actor, "kind", "wrong_if is {kind: kpi|thesis|text, ...}")
    kind = value.get("kind")
    if kind not in WRONG_KINDS:
        H.refuse(c, actor, "kind", f"a wrong-if is kind {'|'.join(WRONG_KINDS)}, not {kind}")
    out = {"kind": kind}
    check_by = str(value.get("check_by") or "").strip()
    if check_by:
        try:
            out["check_by"] = date.fromisoformat(check_by[:10]).isoformat()
        except ValueError:
            H.refuse(c, actor, "date", "check_by is a date (YYYY-MM-DD)")
    if kind == "kpi":
        from . import kpis as K
        kpi_id = str(value.get("kpi_id") or "")
        if not kpi_id or not K.kpi(c, kpi_id):
            H.refuse(c, actor, "not-found", f"no KPI {kpi_id or '(name one with kpi_id)'}")
        if value.get("direction") not in DIRECTIONS:
            H.refuse(c, actor, "kind", "a KPI wrong-if says which way trips it: direction above|below")
        try:
            threshold = float(value.get("threshold"))
        except (TypeError, ValueError):
            H.refuse(c, actor, "lint", "a KPI wrong-if has a threshold, a number")
        out.update(kpi_id=kpi_id, direction=value["direction"], threshold=threshold)
    elif kind == "thesis":
        market_id = " ".join(str(value.get("market_id") or "").split())
        if not market_id or len(market_id) > 200:
            H.refuse(c, actor, "lint", "a thesis wrong-if names the market entity or page it watches: market_id")
        out["market_id"] = market_id
    else:
        text = _clean(c, actor, value.get("text"), WRONG_TEXT_MAX, "wrong-if", one_line=True)
        if not text:
            H.refuse(c, actor, "lint", "say what would show the bet is wrong: text")
        if not out.get("check_by"):
            H.refuse(c, actor, "lint", "a wrong-if in words needs a check_by date: the day to look again")
        out["text"] = text
    return out


def _cites(c, actor, value):
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        H.refuse(c, actor, "kind", "cites is a list of market entity or page ids")
    out = []
    for item in value:
        item = " ".join(item.split())[:200]
        if item and item not in out:
            out.append(item)
    if len(out) > CITES_MAX:
        H.refuse(c, actor, "lint", f"a bet cites at most {CITES_MAX} market ids")
    return out


def _may_be(c, actor, row, bet_id=None):
    """The rules a bet's new state must keep: at most ACTIVE_MAX active and LIVE_MAX not retired. What it rules out
    and what would show it wrong are optional; the fit check watches only a bet that has a wrong-if."""
    if row["status"] == "active":
        active = c.execute("SELECT count(*) FROM strategy_bets WHERE status='active' AND id<>?",
                           (bet_id or "",)).fetchone()[0]
        if active >= ACTIVE_MAX:
            H.refuse(c, actor, "lint", f"at most {ACTIVE_MAX} bets are active; retire one or keep this one proposed")
    if row["status"] != "retired":
        live = c.execute("SELECT count(*) FROM strategy_bets WHERE status<>'retired' AND id<>?",
                         (bet_id or "",)).fetchone()[0]
        if live >= LIVE_MAX:
            H.refuse(c, actor, "lint", f"a strategy holds at most {LIVE_MAX} bets that are not retired; retire one first")


def _bet_revision(c, actor, revision):
    """A bet write moves the draft's revision on (the bets are what a publish snapshots); `revision`, when sent, is
    checked like the draft's."""
    row = draft(c)
    _check_revision(c, row, revision)
    return _bump(c, actor, row)


def add_bet(c, actor, statement, why="", rules_out="", wrong_if=None, cites=None, status="proposed", rank=None,
            revision=None):
    H._writer(c, actor)
    statement = _clean(c, actor, statement, STATEMENT_MAX, "statement", one_line=True)
    if not statement:
        H.refuse(c, actor, "lint", "say the bet in one sentence")
    if status not in BET_STATUSES:
        H.refuse(c, actor, "kind", f"a status is {'|'.join(BET_STATUSES)}, not {status}")
    now = H.now()
    if rank is None:
        rank = c.execute("SELECT coalesce(max(rank), -1) + 1 FROM strategy_bets").fetchone()[0]
    row = {"id": H.new_id(), "statement": statement, "why": _clean(c, actor, why, BET_TEXT_MAX, "why"),
           "rules_out": _clean(c, actor, rules_out, BET_TEXT_MAX, "rules_out"),
           "wrong_if": _wrong_if(c, actor, wrong_if), "cites": _cites(c, actor, cites) or [], "status": status,
           "rank": int(rank), "created_at": now, "updated_at": now, "updated_by": actor}
    _may_be(c, actor, row)
    c.execute("INSERT INTO strategy_bets (id, statement, why, rules_out, wrong_if, cites, status, since_n, until_n, "
              "rank, created_at, updated_at, updated_by) VALUES (:id, :statement, :why, :rules_out, :wrong_if, :cites, "
              ":status, NULL, NULL, :rank, :created_at, :updated_at, :updated_by)",
              {**row, "wrong_if": dumps(row["wrong_if"]) if row["wrong_if"] else None, "cites": dumps(row["cites"])})
    _event(c, actor, "bet.add", {"id": row["id"], "new": {k: row[k] for k in SNAPSHOT if k != "id"}})
    H.event(c, actor, "strategy.bet", row["id"], {"status": status})
    return bet(c, row["id"]), _bet_revision(c, actor, revision)


def update_bet(c, actor, bet_id, statement=None, why=None, rules_out=None, wrong_if=None, cites=None, status=None,
               rank=None, revision=None):
    """Only the fields sent. `wrong_if` sent empty ({}) clears it; so does `cites` sent as []."""
    H._writer(c, actor)
    row = bet(c, bet_id)
    if not row:
        H.refuse(c, actor, "not-found", f"no bet {bet_id}")
    changes = {}
    if statement is not None:
        changes["statement"] = _clean(c, actor, statement, STATEMENT_MAX, "statement", one_line=True)
        if not changes["statement"]:
            H.refuse(c, actor, "lint", "say the bet in one sentence")
    if why is not None:
        changes["why"] = _clean(c, actor, why, BET_TEXT_MAX, "why")
    if rules_out is not None:
        changes["rules_out"] = _clean(c, actor, rules_out, BET_TEXT_MAX, "rules_out")
    if wrong_if is not None:
        changes["wrong_if"] = _wrong_if(c, actor, wrong_if)
    if cites is not None:
        changes["cites"] = _cites(c, actor, cites)
    if status is not None:
        if status not in BET_STATUSES:
            H.refuse(c, actor, "kind", f"a status is {'|'.join(BET_STATUSES)}, not {status}")
        changes["status"] = status
    if rank is not None:
        changes["rank"] = int(rank)
    changes = {k: v for k, v in changes.items() if v != row[k]}
    if not changes:
        return row, draft(c)["revision"]
    after = {**row, **changes}
    _may_be(c, actor, after, bet_id)
    stored = {**changes}
    if "wrong_if" in stored:
        stored["wrong_if"] = dumps(stored["wrong_if"]) if stored["wrong_if"] else None
    if "cites" in stored:
        stored["cites"] = dumps(stored["cites"])
    c.execute(f"UPDATE strategy_bets SET {', '.join(f'{k}=:{k}' for k in stored)}, updated_at=:updated_at, "
              "updated_by=:updated_by WHERE id=:id", {**stored, "updated_at": H.now(), "updated_by": actor, "id": bet_id})
    _event(c, actor, "bet.update", {"id": bet_id, "old": {k: row[k] for k in changes}, "new": changes})
    H.event(c, actor, "strategy.bet", bet_id, {"fields": sorted(changes)})
    return bet(c, bet_id), _bet_revision(c, actor, revision)


# ----------------------------------------------------------------------------- the Strategy Bot's proposal
def proposal_error(c, payload):
    """What is wrong with a `strategy_draft` goal proposal (backend/goals.py), in a sentence, or None. The payload is
    partial: {summary?, vision?, sections?, bets?: [{id?, statement, why?, rules_out?, wrong_if?, cites?, status?,
    ideas?}], source?, lens?}. A bet with an id is a partial change to it, so a retire is {id, status: retired}; a bet
    without one is new and needs its statement. `ideas` lists growth idea task ids that serve the bet; confirming links
    them to it. `source` says what prompted it (bet_tripped: a wrong-if tripped; lens: a lens run,
    named by `lens`; briefing: the week's market briefing touched the bet)."""
    if not isinstance(payload, dict) or not any(payload.get(k) not in (None, "", [], {}) for k in
                                                ("summary", "vision", "sections", "bets")):
        return "propose a summary, a vision, sections or bets"
    unknown = set(payload) - {"summary", "vision", "sections", "bets", "source", "lens"}
    if unknown:
        return f"a strategy proposal has summary, vision, sections, bets, source and lens, not {', '.join(sorted(unknown))}"
    if payload.get("source") is not None and payload["source"] not in SOURCES:
        return f"a source is {', '.join(SOURCES)}"
    if payload.get("lens") and (payload.get("source") != LENS or not isinstance(payload["lens"], str)
                                or len(payload["lens"]) > 60):
        return "lens is the id of the lens a proposal came from (source lens)"
    if len(" ".join(str(payload.get("summary") or "").split())) > SUMMARY_MAX:
        return f"keep the summary under {SUMMARY_MAX} characters"
    if len(" ".join(str(payload.get("vision") or "").split())) > VISION_MAX:
        return f"keep the vision under {VISION_MAX} characters"
    sections = payload.get("sections")
    if sections is not None:
        if not isinstance(sections, dict):
            return "sections is an object: {serve, sell, know, not, extra: {key: {title, body}}}"
        for key, value in sections.items():
            if key == "extra":
                if not isinstance(value, dict):
                    return "extra is an object of {key: {title, body}}"
            elif key not in FIXED_KEYS:
                return f"{key} is not a section; one of {', '.join(FIXED_KEYS)}, or extra"
            elif len(str(value or "")) > SECTION_MAX:
                return f'keep the "{TITLES[key]}" section under {SECTION_MAX} characters'
    items = payload.get("bets") or []
    if not isinstance(items, list):
        return "bets is a list"
    for item in items:
        if not isinstance(item, dict):
            return "each bet is an object"
        if item.get("id") and not bet(c, item["id"]):
            return f"no bet {item['id']}"
        if not item.get("id") and not " ".join(str(item.get("statement") or "").split()):
            return "say each new bet in one sentence (statement)"
        if item.get("status") and item["status"] not in BET_STATUSES:
            return f"a status is {'|'.join(BET_STATUSES)}, not {item['status']}"
        if item.get("wrong_if") and (not isinstance(item["wrong_if"], dict)
                                     or item["wrong_if"].get("kind") not in WRONG_KINDS):
            return f"a wrong-if is kind {'|'.join(WRONG_KINDS)}"
        ideas = item.get("ideas")
        if ideas is not None and (not isinstance(ideas, list) or len(ideas) > IDEAS_MAX
                                  or not all(isinstance(i, str) for i in ideas)):
            return f"ideas is a list of at most {IDEAS_MAX} growth idea task ids"
        for task_id in ideas or []:
            if not H._one(c, "SELECT task_id FROM growth_ideas WHERE task_id=?", (task_id,)):
                return f"no growth idea {task_id}"
    return None


def apply_proposal(c, actor, payload):
    """A confirmed `strategy_draft` proposal, written as the owner who confirmed it: the draft (merged onto it as it
    is now), then each bet, new or changed. Nothing is published."""
    update_draft(c, actor, None, None, payload.get("summary"), payload.get("vision"), payload.get("sections"))
    ids, linked = [], []
    for item in payload.get("bets") or []:
        fields = {k: item.get(k) for k in ("statement", "why", "rules_out", "wrong_if", "cites", "status")}
        if item.get("id"):
            row, _ = update_bet(c, actor, item["id"], **fields)
        else:
            row, _ = add_bet(c, actor, fields["statement"], fields["why"] or "", fields["rules_out"] or "",
                             fields["wrong_if"], fields["cites"], fields["status"] or "proposed")
        ids.append(row["id"])
        for task_id in item.get("ideas") or []:
            if not H._one(c, "SELECT task_id FROM growth_ideas WHERE task_id=?", (task_id,)):
                continue                 # deleted since the proposal was filed: nothing to link
            G._set_idea(c, actor, task_id, {"bet_id": row["id"]}, "growth.idea.update")
            linked.append(task_id)
    return {"draft": True, "bets": ids, "ideas": linked, "revision": draft(c)["revision"]}


# ----------------------------------------------------------------------------- publishing
def _snapshot(row):
    return {k: row[k] for k in SNAPSHOT}


def publish(c, actor, note="", revision=None):
    """Stamp the draft as version n with every bet that is not retired, open a fresh draft, emit `strategy.published`,
    and file the realignment card when bets with goals were retired or rewritten. The owner's own call (the route
    checks). Refused without an active bet and a "What we won't do", or when nothing changed since the last
    version."""
    H._writer(c, actor)
    row = draft(c)
    _check_revision(c, row, revision)
    note = _clean(c, actor, note, NOTE_MAX, "note", one_line=True)
    snapshot = [_snapshot(b) for b in bets(c) if b["status"] != "retired"]
    if not any(b["status"] == "active" for b in snapshot):
        H.refuse(c, actor, "lint", "publish with at least one active bet")
    if not row["sections"]["not"].strip():
        H.refuse(c, actor, "lint", "write \"What we won't do\" before publishing: a strategy with no trade-offs is a "
                                   "description")
    last = current(c)
    if last and all(last[k] == row[k] for k in FIELDS) and last["bets"] == snapshot:
        H.refuse(c, actor, "lint", f"nothing has changed since version {last['n']}; edit the draft or a bet first")
    n = (last["n"] if last else 0) + 1
    now = H.now()
    c.execute("UPDATE strategy_versions SET status='published', n=?, bets=?, note=?, published_at=?, published_by=? "
              "WHERE id=?", (n, dumps(snapshot), note, now, actor, row["id"]))
    # The fresh draft's revision follows on, so a page still holding the published one gets a 409, not a new draft.
    c.execute("INSERT INTO strategy_versions (id, n, status, guide, summary, vision, sections, bets, note, revision, "
              "updated_at, updated_by) VALUES (?,NULL,'draft',?,?,?,?,'[]','',?,?,?)",
              (H.new_id(), row["guide"], row["summary"], row["vision"], dumps(row["sections"]), row["revision"] + 1,
               now, actor))
    # A bet's span in versions: first published active here, or retired as of here.
    c.execute("UPDATE strategy_bets SET since_n=? WHERE status='active' AND since_n IS NULL", (n,))
    c.execute("UPDATE strategy_bets SET until_n=? WHERE status='retired' AND until_n IS NULL", (n,))
    if last and last["vision"] != row["vision"]:
        _vision_event(c, actor, last["vision"], row["vision"])
    sync_vision(c)
    _event(c, actor, "published", {"n": n, "note": note})
    H.event(c, actor, "strategy.publish", f"v{n}", {"n": n})
    published = version(c, n)
    H.hook(c, "strategy.published", f"v{n}", lambda: {"version": n, "note": note, "published_by": actor}, actor, revision=0)
    if last:
        realign(c, actor, last, published)
    return published


def _realign_goals(c, before, after):
    """The live goals of every bet `before` had that `after` retired or rewrote: [{goal_id, title, owner, bet_id,
    change, statement, was?}]; `was` is a rewritten bet's wording before (a card filed before it has none)."""
    now = {b["id"]: b for b in after["bets"]}
    moved = {}
    for old in before["bets"]:
        new = now.get(old["id"])
        if not new or new["status"] == "retired":
            moved[old["id"]] = ("retired", old["statement"], None)
        elif new["statement"] != old["statement"]:
            moved[old["id"]] = ("rewritten", new["statement"], old["statement"])
    if not moved:
        return []
    marks = ",".join("?" * len(moved))
    rows = c.execute(f"SELECT id, title, owner_id, bet_id FROM goals WHERE bet_id IN ({marks}) AND "
                     "coalesce(status,'') NOT IN ('done','dropped') ORDER BY owner_id, rank, created_at", tuple(moved))
    out = []
    for r in rows:
        change, statement, was = moved[r["bet_id"]]
        out.append({"goal_id": r["id"], "title": r["title"], "owner_id": r["owner_id"], "bet_id": r["bet_id"],
                    "change": change, "statement": statement, **({"was": was} if was else {})})
    return out


def realign(c, owner, before, after):
    """One Decide card for the owner after a publish that retired or rewrote bets with live goals: a
    `strategy_realign` proposal listing each goal with retire goal / move to another bet / keep, filed as a task in
    the owner's queue at every level and outside the Strategy Bot's daily room. Returns the proposal or None."""
    from . import goals as G
    goals = _realign_goals(c, before, after)
    if not goals:
        return None
    active = [{"id": b["id"], "number": i, "statement": b["statement"]}
              for i, b in enumerate((b for b in after["bets"] if b["status"] == "active"), 1)]
    payload = {"n": after["n"], "goals": goals, "bets": active}
    proposal = G.propose(c, H.KEEPER, REALIGN, payload=payload,
                         reason=f"strategy v{after['n']} retired or rewrote the bets these goals serve")
    file_card(c, owner, proposal)
    return proposal


def file_card(c, owner, proposal):
    """The task that asks the owner to decide `proposal`, tracked like the Strategy Bot's asks (backend/goal_asks.py) so
    it closes once decided. Filed now, whatever the Strategy Bot's level and its room today."""
    from . import goal_asks as GA
    from . import goals as G
    if c.execute("SELECT 1 FROM goal_asks WHERE kind='proposal' AND ref=?", (proposal["id"],)).fetchone():
        return None
    requester = G.AUTO_ACTOR if GA.manager_ready(c) else H.KEEPER
    title, body = GA._ask({"kind": "proposal", "proposal": proposal})
    # A realign card is urgent (goal_asks.urgent): past the daily cap, so publishing never rolls back on it.
    made = H.task_create(c, requester, title, body, owner, lint=False, lane="company")
    c.execute("INSERT INTO goal_asks (kind, ref, owner, task_id, created_at) VALUES ('proposal',?,?,?,?)",
              (proposal["id"], owner, made["id"], H.now()))
    return made


def settle_card(c, actor, proposal_id, note):
    """A decided strategy card closes its task now, whether or not the Strategy Bot's pass runs on this install."""
    row = H._one(c, "SELECT * FROM goal_asks WHERE kind='proposal' AND ref=?", (proposal_id,))
    if not row:
        return
    task = H.task(c, row["task_id"])
    if task and task["status"] in H.LIVE_STATUSES:
        H.task_close(c, actor, task["id"], note=note, quiet=True)
    c.execute("DELETE FROM goal_asks WHERE kind='proposal' AND ref=?", (proposal_id,))


def card_view(c, proposal):
    """What a strategy card shows beyond its payload: a lens proposal's lens by name (`lens_name`), and a realign
    card's goals finished, dropped or deleted since it was filed (`closed_goals`), which it lists with no choice."""
    payload = proposal.get("payload") or {}
    if proposal.get("kind") == "strategy_draft" and payload.get("source") == LENS and payload.get("lens"):
        from . import lenses
        found = lenses.lens(payload["lens"])
        proposal["lens_name"] = found["name"] if found else None
    elif proposal.get("kind") == REALIGN:
        ids = [g.get("goal_id") for g in payload.get("goals") or [] if isinstance(g, dict) and g.get("goal_id")]
        live = {r[0] for r in c.execute(f"SELECT id FROM goals WHERE id IN ({','.join('?' * len(ids))}) AND "
                                        "coalesce(status,'') NOT IN ('done','dropped')", ids)} if ids else set()
        proposal["closed_goals"] = [i for i in ids if i not in live]
    return proposal


def realign_error(c, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("goals"), list) or not payload["goals"]:
        return "a realignment lists the goals to decide"
    return None


def apply_realign(c, actor, payload, moves):
    """The owner's choice per goal: `keep` (the default for a goal not named), `retire` (the goal is dropped) or a
    bet id (the goal moves to it). A goal finished or dropped since the card was filed is left as it is (`closed`)."""
    from . import goals as G
    listed = {g["goal_id"] for g in payload["goals"]}
    moves = dict(moves or {})
    stray = set(moves) - listed
    if stray:
        H.refuse(c, actor, "kind", f"{', '.join(sorted(stray))} is not a goal on this card")
    done = {}
    for goal_id in listed:
        choice = moves.get(goal_id) or "keep"
        row = G.goal(c, goal_id)
        if not row:
            done[goal_id] = "gone"
        elif row.get("status") in ("done", "dropped"):
            done[goal_id] = "closed"
        elif choice == "keep":
            done[goal_id] = "kept"
        elif choice == "retire":
            G.set_status(c, actor, goal_id, "dropped", "Retired with its bet")
            done[goal_id] = "retired"
        else:
            target = bet(c, choice)
            if not target or target["status"] == "retired":
                H.refuse(c, actor, "not-found", f"no live bet {choice} to move {goal_id} to")
            G.update(c, actor, goal_id, bet_id=choice)
            done[goal_id] = "moved"
    return {"goals": done}


# ----------------------------------------------------------------------------- the fit check
def _published_light(c):
    try:
        return H._one(c, "SELECT id, n, bets, published_at FROM strategy_versions WHERE status='published' "
                         "ORDER BY n DESC LIMIT 1")
    except sqlite3.OperationalError:        # a database from before the strategy tables
        return None


def watched(c, scope):
    """Whether the published version names this KPI or market id at all: most writes name nothing a bet cites, and
    this is one indexed read."""
    row = _published_light(c)
    return bool(row) and f'"{scope}"' in row["bets"]


def _evidence(c, item, published, today):
    """Why this bet's wrong-if trips now, in a sentence or two, or None."""
    wrong = item.get("wrong_if") or {}
    if wrong.get("kind") == "kpi":
        from . import kpis as K
        reading = K.latest(K.readings(c, wrong["kpi_id"], effective=True), usable=True)
        if not reading or reading.get("value") is None:
            return None
        value, threshold = float(reading["value"]), float(wrong["threshold"])
        if (value > threshold) if wrong["direction"] == "above" else (value < threshold):
            name = (K.kpi(c, wrong["kpi_id"]) or {}).get("name") or wrong["kpi_id"]
            return (f"{name} is {value:g} (period ending {str(reading.get('period_end') or '')[:10]}), "
                    f"{wrong['direction']} the {threshold:g} the bet said would show it wrong.")
        return None
    if wrong.get("kind") == "thesis":
        ids = list(dict.fromkeys([wrong["market_id"], *(item.get("cites") or [])]))
        marks = ",".join("?" * len(ids))
        rows = H._rows(c.execute(
            f"SELECT subject_kind, subject_id, field, old, new, created_at, actor FROM market_events WHERE subject_id IN "
            f"({marks}) AND created_at > ? AND actor <> 'seed' ORDER BY created_at DESC LIMIT 3", (*ids, published or "")))
        if not rows:
            return None
        return "The market changed since it was published: " + "; ".join(
            f"{r['subject_id']} {r['field']}: {_one_line(r['old'], 80) or '(none)'} -> {_one_line(r['new'], 80)}"
            f" ({r['created_at'][:10]})" for r in rows) + "."
    if wrong.get("kind") == "text" and wrong.get("check_by") and wrong["check_by"] < today:
        return f"Its check date {wrong['check_by']} has passed: look again at \"{wrong.get('text')}\"."
    return None


def watch(c, kpi_id=None, market_id=None, today=None):
    """The fit check: each active bet of the published version against its wrong-if. `kpi_id` or `market_id` limits
    it to the bets that name that id (a reading, a market write); neither checks every bet (the KPI status pass).
    A trip is one `bet_tripped` event and one task for the Strategy Bot per bet per version, whatever its level; nothing
    while the owner has its regular checks off or there is no Strategy Bot. Returns the tripped bet ids."""
    from . import strategy_bot
    from . import goal_asks as GA
    from . import goals as G
    from .batch_work import isolated
    row = _published_light(c)
    if not row:
        return []
    scope = kpi_id or market_id
    if scope and f'"{scope}"' not in row["bets"]:      # most writes name nothing a bet cites: skip the parse
        return []
    active = [b for b in loads(row["bets"], []) if b.get("status") == "active" and b.get("wrong_if")]
    if not active or not strategy_bot.checks(c) or not GA.manager_ready(c):
        return []
    done = {r[0] for r in c.execute("SELECT json_extract(payload,'$.bet_id') FROM strategy_events WHERE kind=? AND "
                                    "json_extract(payload,'$.n')=?", (TRIPPED, row["n"]))}
    today = today or date.today().isoformat()
    tripped = []
    for number, item in enumerate(active, 1):
        wrong = item["wrong_if"]
        if item["id"] in done:
            continue
        if kpi_id and wrong.get("kpi_id") != kpi_id:
            continue
        if market_id and market_id not in [wrong.get("market_id"), *(item.get("cites") or [])]:
            continue
        with isolated(c, "strategy_watch", item["id"]):
            evidence = _evidence(c, item, row["published_at"], today)
            if not evidence:
                continue
            body = (f"Bet {number} of strategy v{row['n']} (bet id {item['id']}): {item['statement']}\n"
                    f"Wrong if: {wrong_if_text(c, wrong)}\nEvidence: {evidence}\n\n"
                    f"Follow {PLAYBOOK}: propose to the owner retiring it, rewriting it, or keeping it with a new "
                    f"wrong-if (a strategy_draft proposal with source bet_tripped).")
            try:
                task = H.task_create(c, H.KEEPER, f"Bet {number}: may be wrong", body, G.AUTO_ACTOR, lint=False)
            except H.Refused as exc:
                if exc.rule != "duplicate":
                    raise
                # An open "Bet N" task (an earlier version's, or this bet's) holds the title: not recorded, so the
                # next pass files it once that task is closed.
                continue
            _event(c, H.KEEPER, TRIPPED, {"bet_id": item["id"], "n": row["n"], "evidence": evidence,
                                          "task_id": task["id"]})
            H.event(c, H.KEEPER, "strategy.bet_tripped", item["id"], {"n": row["n"]})
            tripped.append(item["id"])
    return tripped


# ----------------------------------------------------------------------------- HTTP
def install(app, store, auth, mutate):
    from . import goal_routes

    def reader(who):
        auth.domain(who)

    def editor(c, who):
        """The owner on their own click; a bot, the Strategy Bot included, proposes and the owner confirms."""
        auth.domain(who)
        if goal_routes.company_editor(who):
            return
        if who.role == "bot":
            raise Problem("forbidden", "A bot proposes a change to the strategy and the owner confirms it "
                          "(POST /api/v2/goal-proposals, kind strategy_draft)", 403)
        raise Problem("forbidden", "Only the owner writes the strategy and its bets, on their own click", 403)

    @app.get("/api/v2/strategy")
    def strategy_show(request: Request):
        """The published version and its bets, each bet's goals and colour; for the owner the draft and every bet."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return view(c, auth, who)

    @app.put("/api/v2/strategy/draft")
    def strategy_draft(request: Request, body: M.StrategyDraft):
        """Owner only. Only the fields sent change; "" clears one. `revision` is the draft's as read: another answers
        409 with the current one."""
        who = request.state.identity

        def work(c):
            editor(c, who)
            return {"draft": update_draft(c, who.actor, body.revision, body.guide, body.summary, body.vision,
                                          body.sections)}
        return mutate(request, body, work)

    @app.post("/api/v2/strategy/publish")
    def strategy_publish(request: Request, body: M.StrategyPublish):
        """Owner only: the draft becomes the next numbered version."""
        who = request.state.identity

        def work(c):
            editor(c, who)
            return publish(c, who.actor, body.note, body.revision)
        return mutate(request, body, work)

    @app.get("/api/v2/strategy/versions")
    def strategy_versions(request: Request, limit: int = VERSIONS_PAGE):
        """The published versions, newest first: n, note, published, published_by, guide, summary."""
        reader(request.state.identity)
        with store.read() as c:
            return versions(c, limit)

    @app.get("/api/v2/strategy/versions/{n}")
    def strategy_version(request: Request, n: int):
        """One published version, whole: its sections and its bets as they were."""
        reader(request.state.identity)
        with store.read() as c:
            row = version(c, n)
        if not row:
            raise Problem("not_found", f"There is no strategy version {n}", 404)
        return row

    @app.get("/api/v2/strategy/diff")
    def strategy_diff(request: Request):
        """`?from=&to=`: what changed between two published versions."""
        reader(request.state.identity)
        try:
            a, b = int(request.query_params.get("from", "")), int(request.query_params.get("to", ""))
        except ValueError:
            raise Problem("validation", "Say which versions to compare: ?from=1&to=2", 422) from None
        with store.read() as c:
            return diff(c, a, b)

    @app.post("/api/v2/strategy/bets")
    def strategy_bet_add(request: Request, body: M.StrategyBetCreate):
        """Owner only: a new bet, proposed unless said otherwise."""
        who = request.state.identity

        def work(c):
            editor(c, who)
            row, revision = add_bet(c, who.actor, body.statement, body.why, body.rules_out, body.wrong_if,
                                    body.cites, body.status, body.rank, body.revision)
            return {"bet": row, "revision": revision}
        return mutate(request, body, work)

    @app.post("/api/v2/strategy/bets/{bet_id}")
    def strategy_bet_update(request: Request, bet_id: str, body: M.StrategyBetUpdate):
        """Owner only: change a bet, only the fields sent, its rank and its status included."""
        who = request.state.identity

        def work(c):
            editor(c, who)
            row, revision = update_bet(c, who.actor, bet_id, body.statement, body.why, body.rules_out, body.wrong_if,
                                       body.cites, body.status, body.rank, body.revision)
            return {"bet": row, "revision": revision}
        return mutate(request, body, work)

    @app.get("/api/v2/strategy/summary")
    def strategy_summary(request: Request):
        """Plain text: what every run's context carries. A caller that asks for JSON (the hub client) gets
        {text, n}."""
        reader(request.state.identity)
        with store.read() as c:
            published = current(c)
            text = summary_text(c, published)
        if not published:
            raise Problem("not_found", "No strategy version has been published yet", 404)
        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/plain" not in accept:
            return {"text": text, "n": published["n"]}
        return PlainTextResponse(text)

    @app.get("/api/v2/strategy/guides")
    def strategy_guides(request: Request):
        """Deprecated: the Strategy Bot's drafting questions, the default guide only. The exercises run against a
        strategy are lenses (GET /lenses)."""
        reader(request.state.identity)
        return listed_guides()
