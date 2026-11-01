"""Lenses (docs/lenses.md): thought exercises run against the team's strategy or its market.

A lens is one YAML file in `backend/lenses/`: a few steps asked one at a time (text, a list, scores, a choice), what the
result shows (sections and one chart), and what data would sharpen it. A run is one row of `lens_runs`: the answers,
then one model call with no runner (backend/updates.py `writer_for`: the Tico AI Gateway, else the team's own key; the
job and its stale rule are backend/strategy_setup.py's), then a validated result: a summary, sections, a chart, findings
tied to bets, the assumptions the model made for skipped steps, proposed changes to the strategy draft, and what would
sharpen it. The model's JSON is never trusted: every part is clipped and checked, and a part that fails is dropped, not
kept.

Who: the owner starts a run on the published version or, on their own click, on the draft. The Strategy Bot may start
one; the Librarian may start a market one. A run on the draft is the owner's alone (like the draft); every other run is
readable by every signed-in reader, bots included, since it only discusses what is published. Changes go to the draft
through the owner's click (`apply`, the code path of accepting a `strategy_draft` proposal); when the Strategy Bot ran
the lens, the changes are filed as one `strategy_draft` proposal (source `lens`) and the owner decides it on the usual
card. A lens never publishes. Unversioned and idempotent (`ensure`), like the strategy tables."""
import functools
import json
import logging
import re
import sqlite3
import threading
from datetime import date
from pathlib import Path

import yaml

from . import hubdb as H
from . import models as M
from . import strategy as S
from .store import Problem

log = logging.getLogger("tico.lenses")

LENSES_DIR = Path(__file__).resolve().parent / "lenses"
STRATEGY_BOT = "bot:goal-manager"
LIBRARIAN = "bot:librarian"
STALE_S = 600               # a run going this long was cut off by a restart
PROMPT_CHARS = 24_000       # the whole model input
SPEND_SEGMENTS = 25         # sized segments a market lens reads
INPUTS = ("text", "list", "scores", "choice")
CHARTS = ("value_curve", "matrix_2x2", "power_table", "cascade", "table", "none")
APPLIES = ("strategy", "market")
STATES = ("running", "done", "failed", "no_model")
POWERS = ("scale", "network", "counter-positioning", "switching", "brand", "cornered", "process", "none")
FINDINGS = ("supports", "challenges", "gap")
# The order the list shows, strategy first; a lens not named here follows by id.
ORDER = ("playing-to-win", "blue-ocean", "scenarios", "seven-powers", "good-strategy", "pre-mortem", "positioning",
         "where-to-play", "five-forces")
TEXT_MAX = 2_000            # an answer in words
LIST_MAX = 12               # items in a list answer
ITEM_MAX = 200
COLUMNS_MAX = 6             # "Us" and up to five rivals
ROWS_MAX = 10               # factors in a scores answer
LIMIT = 50                  # runs listed
LIMIT_MAX = 200
SUMMARY_MAX = 600
BODY_MAX = 4_000            # one section
TITLE_MAX = 120
SECTIONS_MAX = 8
FINDINGS_MAX = 12
FINDING_MAX = 500
ASSUMPTIONS_MAX = 10
ASSUMPTION_MAX = 300
CHANGES_MAX = 8
LABEL_MAX = 200
PAYLOAD_MAX = 12_000        # one change's payload, as JSON
SHARPEN_MAX = 6
SHARPEN_TEXT_MAX = 200
CELL_MAX = 400
INTERRUPTED = {"code": "interrupted", "message": "The run was cut off; run it again"}
SHARPEN_TARGET = "market/open-questions"

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS lens_runs (id TEXT PRIMARY KEY, lens TEXT NOT NULL, applies_to TEXT NOT NULL, "
    "on_draft INTEGER NOT NULL DEFAULT 0, strategy_n INTEGER, status TEXT NOT NULL, "
    "answers TEXT NOT NULL DEFAULT '{}', result TEXT, applied TEXT NOT NULL DEFAULT '[]', "
    "sharpen_tasks TEXT NOT NULL DEFAULT '{}', proposal_id TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, "
    "finished_at TEXT, error TEXT, claimed INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS lens_runs_created ON lens_runs(created_at)",
)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


# Test seam: how the job is started.
def spawn(work):
    threading.Thread(target=work, name="lens-run", daemon=True).start()


def writer(c, settings, store):
    """The model a run is made with, or None: the daily-update drafts' writer (backend/updates.py)."""
    from . import updates
    return updates.writer_for(c, settings, store)


# ----------------------------------------------------------------------------- the lens files
def _bad(path, why):
    raise ValueError(f"lens {path.name}: {why}")


def _load(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    lens_id = str(data.get("id") or "")
    if lens_id != path.stem:
        _bad(path, "id is the file name")
    if data.get("applies_to") not in APPLIES:
        _bad(path, f"applies_to is {'|'.join(APPLIES)}")
    steps, seen = [], {}
    for raw in data.get("steps") or []:
        step = {"key": str(raw.get("key") or ""), "ask": str(raw.get("ask") or ""), "input": raw.get("input")}
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", step["key"]) or step["key"] in seen or not step["ask"]:
            _bad(path, f"step {step['key']!r} needs its own key and an ask")
        if step["input"] not in INPUTS:
            _bad(path, f"step {step['key']} input is {'|'.join(INPUTS)}")
        if raw.get("hint"):
            step["hint"] = str(raw["hint"])
        if step["input"] == "choice":
            step["options"] = [str(o) for o in raw.get("options") or []]
            if len(step["options"]) < 2:
                _bad(path, f"step {step['key']} is a choice with at least two options")
        if step["input"] == "scores":
            for field in ("rows_from", "columns_from"):
                source = seen.get(raw.get(field))
                if not source or source["input"] != "list":
                    _bad(path, f"step {step['key']} {field} names an earlier list step")
                step[field] = raw[field]
        seen[step["key"]] = step
        steps.append(step)
    output = data.get("output") or {}
    chart = output.get("chart") or "none"
    if chart not in CHARTS:
        _bad(path, f"chart is {'|'.join(CHARTS)}")
    if not steps or not data.get("name") or not isinstance(data.get("minutes"), int):
        _bad(path, "a name, minutes and steps")
    return {"id": lens_id, "name": str(data["name"]), "applies_to": data["applies_to"],
            "description": str(data.get("description") or ""), "minutes": data["minutes"],
            "listed": bool(data.get("listed")), "steps": steps,
            "output": {"chart": chart, "sections": [str(s) for s in output.get("sections") or []]},
            "sharpen": [str(s) for s in data.get("sharpen") or []]}


@functools.lru_cache(maxsize=1)
def lenses():
    """Every lens, as its YAML says. Loaded once: the files ship with the release."""
    found = [_load(path) for path in sorted(LENSES_DIR.glob("*.yaml"))]
    rank = lambda lens: (lens["applies_to"] != "strategy",
                         ORDER.index(lens["id"]) if lens["id"] in ORDER else len(ORDER), lens["id"])
    return sorted(found, key=rank)


def lens(lens_id):
    return next((x for x in lenses() if x["id"] == lens_id), None)


def listed(applies_to=None):
    """What GET /lenses offers: the listed lenses, strategy first, then market."""
    return [{k: x[k] for k in ("id", "name", "applies_to", "description", "minutes", "steps", "output", "sharpen")}
            for x in lenses() if x["listed"] and (not applies_to or x["applies_to"] == applies_to)]


# ----------------------------------------------------------------------------- answers
def _invalid(key, why):
    raise Problem("invalid", f"{key}: {why}", 422, extra={"reason": key})


def _text(key, value, limit):
    if not isinstance(value, str):
        _invalid(key, "words are a string")
    value = value.strip()
    if len(value) > limit:
        _invalid(key, f"keep it under {limit} characters")
    return value


def clean_answers(the, answers):
    """The answers as stored: {step key: value}, a skipped step simply absent. Anything that is not a step of this lens
    or not the shape its input takes is a 422 `invalid`, so a stored answer is always safe to show and to prompt with."""
    if not isinstance(answers, dict):
        _invalid("answers", "an object of {step key: answer}")
    steps = {s["key"]: s for s in the["steps"]}
    out = {}
    for key, value in answers.items():
        step = steps.get(key)
        if not step:
            _invalid(key, f"not a step of {the['id']}")
        if value is None:
            continue
        kind = step["input"]
        if kind == "text":
            value = _text(key, value, TEXT_MAX)
        elif kind == "list":
            if not isinstance(value, list) or len(value) > LIST_MAX:
                _invalid(key, f"a list of at most {LIST_MAX} items")
            value = [x for x in (_text(key, item, ITEM_MAX) for item in value) if x]
        elif kind == "choice":
            if value not in step["options"]:
                _invalid(key, f"one of {', '.join(step['options'])}")
        else:
            value = _scores(key, value)
        if value not in ("", [], None) and (kind != "scores" or value["rows"]):
            out[key] = value
    return out


def _scores(key, value):
    if not isinstance(value, dict) or set(value) != {"columns", "rows"}:
        _invalid(key, "scores are {columns: [...], rows: [{factor, scores}]}")
    columns, rows = value["columns"], value["rows"]
    if not isinstance(columns, list) or not 1 <= len(columns) <= COLUMNS_MAX:
        _invalid(key, f"one to {COLUMNS_MAX} columns")
    columns = [_text(key, x, ITEM_MAX) for x in columns]
    if not all(columns) or not isinstance(rows, list) or len(rows) > ROWS_MAX:
        _invalid(key, f"named columns and at most {ROWS_MAX} rows")
    out = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"factor", "scores"}:
            _invalid(key, "each row is {factor, scores}")
        factor, scores = _text(key, row["factor"], ITEM_MAX), row["scores"]
        if not factor or not isinstance(scores, list) or len(scores) != len(columns) or not all(
                isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 5 for n in scores):
            _invalid(key, "each row has a factor and one whole score from 0 to 5 per column")
        out.append({"factor": factor, "scores": scores})
    return {"columns": columns, "rows": out}


# ----------------------------------------------------------------------------- reading runs
def _row(c, run_id):
    return H._one(c, "SELECT * FROM lens_runs WHERE id=?", (run_id,))


def _stale(row):
    return row["status"] == "running" and H.shift(row["created_at"], seconds=STALE_S) < H.now()


def view(row, short=False):
    """What the API answers. A run cut off (a restart, a hung call) reads as `failed`. The list (`short`) carries the
    result's summary only and no answers: a page of runs stays small."""
    the = lens(row["lens"])
    out = {"id": row["id"], "lens": row["lens"], "name": the["name"] if the else row["lens"],
           "applies_to": row["applies_to"], "on_draft": bool(row["on_draft"]), "strategy_n": row["strategy_n"],
           "status": row["status"], "answers": {} if short else S.loads(row["answers"], {}),
           "result": S.loads(row["result"], {}) or None, "applied": S.loads(row["applied"], []),
           "sharpen_tasks": S.loads(row["sharpen_tasks"], {}), "proposal_id": row["proposal_id"],
           "created_by": row["created_by"], "created_at": row["created_at"], "finished_at": row["finished_at"],
           "error": S.loads(row["error"], {}) or None}
    if short and out["result"]:
        out["result"] = {"summary": out["result"].get("summary") or ""}
    if _stale(row):
        out.update(status="failed", error=INTERRUPTED)
    return out


def runs(c, who, lens_id=None, limit=LIMIT):
    """Newest first, only the runs this caller may see: draft runs are the owner's."""
    from . import goal_routes
    where, args = [], []
    if not goal_routes.company_editor(who):
        where.append("on_draft=0")
    if lens_id:
        where.append("lens=?")
        args.append(lens_id)
    sql = "SELECT * FROM lens_runs" + (" WHERE " + " AND ".join(where) if where else "")
    rows = H._rows(c.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?",
                             (*args, max(1, min(int(limit), LIMIT_MAX)))))
    return [view(r, short=True) for r in rows]


def read(c, who, run_id):
    """One run, or 404: a draft run is not found by anyone but the owner."""
    from . import goal_routes
    row = _row(c, run_id)
    if not row or (row["on_draft"] and not goal_routes.company_editor(who)):
        raise Problem("not_found", "No such lens run", 404)
    return row


# ----------------------------------------------------------------------------- starting a run
def start(c, who, settings, store, lens_id, answers, on="published"):
    """A new run, `running` (the caller spawns `run` once this commits) or `no_model` at once, with the answers saved.
    The owner starts any; the Strategy Bot any strategy or market lens on the published version; the Librarian a market
    one."""
    from . import goal_routes
    the = lens(lens_id)
    if not the:
        raise Problem("not_found", f"There is no lens {lens_id}", 404)
    owner = goal_routes.company_editor(who)
    market = the["applies_to"] == "market"
    if not (owner or who.actor == STRATEGY_BOT or (market and who.actor == LIBRARIAN)):
        raise Problem("forbidden", "Only the owner, the Strategy Bot and, "
                                   "for a market lens, the Librarian run a lens", 403)
    on_draft = on == "draft" and not market
    if on_draft and not owner:
        raise Problem("forbidden", "Only the owner runs a lens on the draft", 403)
    answers = clean_answers(the, answers)
    number = None
    if not market and not on_draft:
        published = S.current(c)
        if not published:
            raise Problem("no_published", "Nothing is published yet; run it on the draft, or publish first", 422)
        number = published["n"]
    model = writer(c, settings, store) is not None
    now = H.now()
    row = {"id": H.new_id(), "lens": lens_id, "applies_to": the["applies_to"], "on_draft": int(on_draft),
           "strategy_n": number, "status": "running" if model else "no_model", "answers": S.dumps(answers),
           "created_by": who.actor, "created_at": now, "finished_at": None if model else now}
    c.execute("INSERT INTO lens_runs (id, lens, applies_to, on_draft, strategy_n, status, answers, created_by, created_at, "
              "finished_at) VALUES (:id, :lens, :applies_to, :on_draft, :strategy_n, :status, :answers, :created_by, "
              ":created_at, :finished_at)", row)
    S._event(c, who.actor, "lens_run", {"run_id": row["id"], "lens": lens_id, "on_draft": on_draft})
    if not model:
        S._event(c, who.actor, "lens_done", {"run_id": row["id"], "lens": lens_id, "status": "no_model"})
    return view(_row(c, row["id"]))


def stop_running(c, error=INTERRUPTED):
    """Every run still `running` becomes `failed`: at start-up (backend/app.py lifespan; no thread of this process runs
    them)."""
    for row in H._rows(c.execute("SELECT id, lens FROM lens_runs WHERE status='running'")):
        c.execute("UPDATE lens_runs SET status='failed', finished_at=?, error=? WHERE id=?",
                  (H.now(), S.dumps(error), row["id"]))
        S._event(c, H.KEEPER, "lens_done", {"run_id": row["id"], "lens": row["lens"], "status": "failed"})


# ----------------------------------------------------------------------------- the model
CHART_SHAPES = {
    "value_curve": '{"kind": "value_curve", "factors": ["Price", ...], "series": [{"name": "Us", "scores": [0-5, one '
                   'per factor]}, {"name": "a rival", "scores": [...]}]}; "Us" is the first series',
    "matrix_2x2": '{"kind": "matrix_2x2", "x": {"name": "...", "low": "...", "high": "..."}, "y": {...}, "cells": [{"q": '
                  '"ll|lh|hl|hh", "title": "...", "text": "..."}, four of them]}; q is x then y, so hl is x high and y low',
    "power_table": '{"kind": "power_table", "rows": [{"bet_id": "a bet id or null", "bet": "its statement", "power": '
                   '"scale|network|counter-positioning|switching|brand|cornered|process|none", "how": "...", "when": "..."}]}'
                   ', one row per active bet',
    "cascade": '{"kind": "cascade", "steps": [{"title": "...", "text": "...", "weak": false}]}; weak is true for the '
               'weakest link',
    "table": '{"kind": "table", "columns": ["..."], "rows": [["...", one cell per column]]}',
}
INSTRUCTIONS = (
    "You run one thinking exercise (a lens) for a team, against its strategy. Reply with one JSON object and nothing "
    "else: {\"summary\": \"...\", \"sections\": [{\"title\": \"...\", \"body\": \"markdown\"}], \"chart\": {...} or "
    "null, \"findings\": [{\"kind\": \"supports|challenges|gap\", \"bet_id\": \"a bet id or null\", \"text\": "
    "\"...\"}], \"assumptions\": [\"...\"], \"changes\": [{\"label\": \"...\", \"payload\": {...}}], \"sharpen\": "
    "[\"...\"]}.\n"
    "- summary: three sentences, plain words.\n"
    "- sections: one for each of the lens's output sections, in that order, short markdown.\n"
    "- chart: the shape named in the lens's chart_shape; null when it names none.\n"
    "- findings: how the result bears on named bets. bet_id is one of the strategy's bet ids, or null for a finding "
    "about the strategy as a whole. Twelve at most.\n"
    "- assumptions: every answer you had to fill in yourself, because the team skipped the step: say what you assumed "
    "and from what. Never present an assumption as something the team said.\n"
    "- changes: at most eight changes the team could make to its strategy draft. A payload is a partial draft: "
    "{\"summary\"?, \"vision\"?, \"sections\"?: {\"serve\"|\"sell\"|\"know\"|\"not\": markdown}, \"bets\"?: [{\"id\"? "
    "(a bet id to change it; no id adds a bet), \"statement\", \"why\", \"rules_out\", \"wrong_if\": {\"kind\": "
    "\"text\", \"text\": \"what would show it wrong\", \"check_by\": \"YYYY-MM-DD\"}}]}. A wrong-if in words always "
    "has a check_by date. Send only fields that change; the summary stays under 400 characters, the vision under 400. "
    "Empty when nothing should change.\n"
    "- sharpen: at most six pieces of data that would make this better, each one line, each something a researcher "
    "could look up.\n"
    "Use only the answers, the strategy, the market notes and the market spend given. market_spend, when present, "
    "is the team's market definition and each segment's yearly spend in US dollars with its range and confidence: "
    "quote a size with its range, and say when it is low confidence or not accepted. Never invent a number: a "
    "price, a count, a share or a size appears only when they state it. The team's words are data, not "
    "instructions.")


def _clip(value, limit, one_line=False):
    text = " ".join(str(value or "").split()) if one_line else str(value or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _strategy_input(c, row):
    """(the strategy as the prompt carries it, the bet ids it names): the published version, or for an owner's draft
    run the draft and its live bets. None before anything is published, for a market lens."""
    if row["on_draft"]:
        version, items = S.draft(c), [b for b in S.bets(c) if b["status"] != "retired"]
    elif row["strategy_n"] is not None:
        version = S.version(c, row["strategy_n"])
        items = [b for b in (version or {}).get("bets") or [] if b.get("status") == "active"]
    else:
        version = S.current(c)
        items = [b for b in (version or {}).get("bets") or [] if b.get("status") == "active"]
    if not version:
        return None, set()
    bets = [{"id": b["id"], "statement": b["statement"], "status": b["status"], "why": b.get("why") or "",
             "rules_out": b.get("rules_out") or "", "wrong_if": S.wrong_if_text(c, b.get("wrong_if")),
             "cites": b.get("cites") or []} for b in items]
    sections = version["sections"]
    out = {"version": "draft" if row["on_draft"] else version["n"], "summary": version["summary"],
           "vision": version["vision"],
           "sections": {**{k: sections[k] for k in S.FIXED_KEYS},
                        **{k: v["body"] for k, v in sections["extra"].items()}},
           "bets": bets}
    return out, {b["id"] for b in bets}


def _market_input(c, the, bets):
    """The market notes a prompt carries: the entities the bets cite, and for a market lens the segments. The market
    definition and sizes join when the market has them."""
    ids = list(dict.fromkeys(x for b in bets for x in b["cites"]))[:10]
    out = []
    try:
        if ids:
            marks = ",".join("?" * len(ids))
            out += H._rows(c.execute(f"SELECT id, type, name, summary FROM market_entities WHERE id IN ({marks})", ids))
        if the["applies_to"] == "market":
            out += H._rows(c.execute("SELECT id, type, name, summary FROM market_entities WHERE type='segment' AND "
                                     "status NOT IN ('retired','merged') ORDER BY name LIMIT 15"))
    except sqlite3.OperationalError:
        return []
    seen, notes = set(), []
    for e in out:
        if e["id"] not in seen:
            seen.add(e["id"])
            notes.append({"id": e["id"], "type": e["type"], "name": e["name"], "summary": _clip(e["summary"], 300)})
    return notes


def _spend_input(c, the):
    """For a market lens, the market's definition and its segments' sizes (backend/market_spend.py), compact: what the
    market is and what each part is worth, how sure, and which one is ours. None when nothing is defined."""
    from . import market_spend as MS
    if the["applies_to"] != "market":
        return None
    try:
        found = MS.spend(c)
    except sqlite3.OperationalError:
        return None
    defn = found["definition"]
    if not defn:
        return None
    segments = []
    for seg in found["segments"][:SPEND_SEGMENTS]:
        size = seg["size"] if seg["size"] and seg["size"].get("spend_usd") is not None else {}
        segments.append({"id": seg["id"], "name": seg["name"], "depth": seg["depth"], "ours": seg["self"],
                         "spend_usd": size.get("spend_usd"), "low_usd": size.get("low_usd"),
                         "high_usd": size.get("high_usd"), "growth_pct": size.get("growth_pct"),
                         "confidence": size.get("confidence"), "accepted": bool(size.get("accepted")),
                         "disagree": seg["disagree"]})
    return {"definition": {k: defn.get(k) for k in ("description", "includes", "excludes", "year", "geography")},
            "year": found["year"], "total": found["total"], "segments": segments}


def prompt_input(c, row):
    """(the user message, the bet ids it names). The whole input is at most PROMPT_CHARS: when it is longer the market
    notes go first, then the sections and the bets' reasoning shrink."""
    the = lens(row["lens"])
    answers = S.loads(row["answers"], {})
    steps = [{"key": s["key"], "ask": s["ask"], "input": s["input"],
              "answer": answers.get(s["key"], "not answered: assume it from the strategy and say so in assumptions")}
             for s in the["steps"]]
    strategy, ids = _strategy_input(c, row)
    notes = _market_input(c, the, (strategy or {}).get("bets") or [])
    base = {"today": date.today().isoformat(),
            "lens": {"name": the["name"], "description": the["description"], "steps": steps,
                     "output_sections": the["output"]["sections"], "chart": the["output"]["chart"],
                     "chart_shape": CHART_SHAPES.get(the["output"]["chart"], "none"),
                     "sharpen_examples": the["sharpen"]},
            "strategy": strategy, "market_notes": notes, "market_spend": _spend_input(c, the)}
    return _fit(base), ids


def _fit(base):
    """The input as JSON, at most PROMPT_CHARS: the market notes go first, then the strategy's sections and the bets'
    reasoning shrink in steps, and last of all the strategy keeps its summary and its bets' statements."""
    dump = lambda: json.dumps(base, ensure_ascii=False)
    text, strategy = dump(), base["strategy"]
    if len(text) > PROMPT_CHARS:
        base["market_notes"] = []
        text = dump()
    if len(text) > PROMPT_CHARS and base.get("market_spend"):
        base["market_spend"]["segments"] = [x for x in base["market_spend"]["segments"] if x["depth"] == 1]
        text = dump()
    for limit in (1500, 600, 250):
        if len(text) <= PROMPT_CHARS or not strategy:
            break
        strategy["sections"] = {k: _clip(v, limit) for k, v in strategy["sections"].items()}
        for b in strategy["bets"]:
            b["why"], b["rules_out"] = _clip(b["why"], limit), _clip(b["rules_out"], limit)
        text = dump()
    if len(text) > PROMPT_CHARS and strategy:
        base["strategy"] = {**strategy, "sections": {}, "vision": "", "summary": _clip(strategy["summary"], 400),
                            "bets": [{"id": b["id"], "statement": b["statement"]} for b in strategy["bets"]]}
        text = dump()
    return text[:PROMPT_CHARS]


# ----------------------------------------------------------------------------- checking what the model said
def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _strings(value, limit, count):
    return [_clip(x, limit, True) for x in value[:count] if isinstance(x, str) and x.strip()] \
        if isinstance(value, list) else None


def chart_error(kind, chart, bet_ids):
    """The chart cleaned to its shape, or None when the model's is not that shape (the result is kept without it)."""
    if not isinstance(chart, dict) or chart.get("kind") != kind:
        return None
    if kind == "value_curve":
        raw, series = chart.get("factors"), chart.get("series")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 10 or not all(isinstance(x, str) and x.strip() for x in raw) \
                or not isinstance(series, list) or not 1 <= len(series) <= 7:
            return None
        factors, out = [_clip(x, 60, True) for x in raw], []
        for item in series:
            scores = item.get("scores") if isinstance(item, dict) else None
            if not isinstance(scores, list) or len(scores) != len(factors) or not all(
                    _is_number(n) and 0 <= n <= 5 for n in scores) or not _clip(item.get("name"), 40, True):
                return None
            out.append({"name": _clip(item["name"], 40, True), "scores": scores})
        return {"kind": kind, "factors": factors, "series": out}
    if kind == "matrix_2x2":
        axes = {}
        for axis in ("x", "y"):
            raw = chart.get(axis)
            if not isinstance(raw, dict) or not all(_clip(raw.get(k), 80, True) for k in ("name", "low", "high")):
                return None
            axes[axis] = {k: _clip(raw[k], 80, True) for k in ("name", "low", "high")}
        cells = chart.get("cells")
        if not isinstance(cells, list) or len(cells) != 4 or {c.get("q") for c in cells if isinstance(c, dict)} != \
                {"ll", "lh", "hl", "hh"}:
            return None
        return {"kind": kind, **axes, "cells": [{"q": c["q"], "title": _clip(c.get("title"), 120, True),
                                                  "text": _clip(c.get("text"), 600)} for c in cells]}
    if kind == "power_table":
        rows = chart.get("rows")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 12 or not all(
                isinstance(r, dict) and r.get("power") in POWERS and _clip(r.get("bet"), 300, True) for r in rows):
            return None
        return {"kind": kind, "rows": [{"bet_id": r["bet_id"] if r.get("bet_id") in bet_ids else None,
                                        "bet": _clip(r["bet"], 300, True), "power": r["power"],
                                        "how": _clip(r.get("how"), 400), "when": _clip(r.get("when"), 200, True)}
                                       for r in rows]}
    if kind == "cascade":
        steps = chart.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 8 or not all(
                isinstance(s, dict) and _clip(s.get("title"), 120, True) for s in steps):
            return None
        return {"kind": kind, "steps": [{"title": _clip(s["title"], 120, True), "text": _clip(s.get("text"), 600),
                                         "weak": s.get("weak") is True} for s in steps]}
    if kind == "table":
        columns, rows = chart.get("columns"), chart.get("rows")
        if not isinstance(columns, list) or not 1 <= len(columns) <= 8 or not isinstance(rows, list) \
                or not 1 <= len(rows) <= 20 or not all(isinstance(r, list) and len(r) == len(columns) for r in rows):
            return None
        cell = lambda x: _clip(x if isinstance(x, (str, int, float)) and not isinstance(x, bool) else "", CELL_MAX)
        return {"kind": kind, "columns": [_clip(x, 80, True) for x in columns],
                "rows": [[cell(x) for x in r] for r in rows]}
    return None


def _wrong_if_ok(c, wrong):
    """Whether a wrong-if would be taken on apply (backend/strategy.py `_wrong_if`), without writing a refusal."""
    if not isinstance(wrong, dict) or wrong.get("kind") not in S.WRONG_KINDS:
        return False
    if wrong.get("check_by"):
        try:
            date.fromisoformat(str(wrong["check_by"])[:10])
        except ValueError:
            return False
    if wrong["kind"] == "text":
        return bool(_clip(wrong.get("text"), S.WRONG_TEXT_MAX, True)) and bool(wrong.get("check_by"))
    if wrong["kind"] == "thesis":
        return bool(str(wrong.get("market_id") or "").strip())
    from . import kpis as K
    return bool(K.kpi(c, str(wrong.get("kpi_id") or ""))) and wrong.get("direction") in S.DIRECTIONS \
        and _is_number(wrong.get("threshold"))


def change_error(c, payload):
    """Why a change's payload would not be taken, or None: the proposal rules (backend/strategy.py `proposal_error`)
    and what writing it would refuse (a new bet's lengths, a wrong-if that is not whole)."""
    error = S.proposal_error(c, payload)
    if error:
        return error
    for item in payload.get("bets") or []:
        if len(" ".join(str(item.get("statement") or "").split())) > S.STATEMENT_MAX:
            return "a bet statement is one sentence"
        if item.get("wrong_if") and not _wrong_if_ok(c, item["wrong_if"]):
            return "a wrong-if is whole"
    return None


def clean_result(c, the, raw, bet_ids):
    """The model's JSON as a result, or None when nothing usable came back. Each part is clipped and checked on its
    own; a part that fails is dropped and the rest kept."""
    raw = raw if isinstance(raw, dict) else {}
    sections = []
    for item in raw.get("sections") if isinstance(raw.get("sections"), list) else []:
        if isinstance(item, dict) and str(item.get("body") or "").strip() and len(sections) < SECTIONS_MAX:
            sections.append({"title": _clip(item.get("title"), TITLE_MAX, True), "body": _clip(item["body"], BODY_MAX)})
    findings = []
    for item in raw.get("findings") if isinstance(raw.get("findings"), list) else []:
        if not isinstance(item, dict) or item.get("kind") not in FINDINGS or not _clip(item.get("text"), 1):
            continue
        bet_id = item.get("bet_id") or None
        if bet_id is not None and bet_id not in bet_ids:
            continue
        findings.append({"kind": item["kind"], "bet_id": bet_id, "text": _clip(item["text"], FINDING_MAX)})
        if len(findings) == FINDINGS_MAX:
            break
    changes = []
    for item in raw.get("changes") if isinstance(raw.get("changes"), list) else []:
        payload = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(payload, dict) or not _clip(item.get("label"), 1):
            continue
        payload = {k: v for k, v in payload.items() if k not in ("source", "lens")}
        try:
            size = len(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError):
            continue
        if size > PAYLOAD_MAX or change_error(c, payload):
            continue
        changes.append({"label": _clip(item["label"], LABEL_MAX, True), "payload": payload})
        if len(changes) == CHANGES_MAX:
            break
    kind = the["output"]["chart"]
    result = {"summary": _clip(raw.get("summary"), SUMMARY_MAX), "sections": sections,
              "chart": chart_error(kind, raw.get("chart"), bet_ids) if kind != "none" else None,
              "findings": findings, "assumptions": _strings(raw.get("assumptions"), ASSUMPTION_MAX, ASSUMPTIONS_MAX) or [],
              "changes": changes, "sharpen": _strings(raw.get("sharpen"), SHARPEN_TEXT_MAX, SHARPEN_MAX) or []}
    if not (result["summary"] or sections or result["chart"] or findings or changes):
        return None
    return result


# ----------------------------------------------------------------------------- the job
def _finish(c, row, status, result=None, error=None, proposal_id=None):
    c.execute("UPDATE lens_runs SET status=?, result=?, error=?, proposal_id=?, finished_at=? WHERE id=?",
              (status, S.dumps(result) if result else None, S.dumps(error) if error else None, proposal_id, H.now(),
               row["id"]))
    S._event(c, row["created_by"], "lens_done", {"run_id": row["id"], "lens": row["lens"], "status": status})


def _fail(store, run_id, code, message):
    with store.transaction() as c:
        row = _row(c, run_id)
        if row and row["status"] == "running":
            _finish(c, row, "failed", error={"code": code, "message": message})


def run(store, run_id):
    """The job itself, outside any transaction: read the strategy, ask the model, save what it said. Runs once per run,
    whatever retries the POST had."""
    with store.transaction() as c:
        row = _row(c, run_id)
        if not row or row["status"] != "running" or row["claimed"]:
            return None
        c.execute("UPDATE lens_runs SET claimed=1 WHERE id=?", (run_id,))
        write = writer(c, store.settings, store)
        if write is None:
            _finish(c, row, "no_model")
            return None
        user, _ = prompt_input(c, row)
    try:
        raw = write(INSTRUCTIONS, user)
    except Exception as exc:      # a model's answer can be anything: the run says so and the answers remain
        from clients import judge as J
        code = exc.code if isinstance(exc, J.JudgeError) else "failed"
        log.info("Lens run failed (%s)", type(exc).__name__)
        return _fail(store, run_id, code, "The model did not answer usably; run it again")
    with store.transaction() as c:
        return finish(c, run_id, raw)


def finish(c, run_id, raw):
    """Check what the model said and save it. A run cut off before it answered writes nothing. A Strategy Bot run with
    at least one change files one `strategy_draft` proposal; an owner's never does."""
    row = _row(c, run_id)
    if not row or row["status"] != "running":
        return None
    if _stale(row):
        return _finish(c, row, "failed", error=INTERRUPTED)
    _, ids = _strategy_input(c, row)
    result = clean_result(c, lens(row["lens"]), raw, ids)
    if result is None:
        return _finish(c, row, "failed", error={"code": "empty", "message": "Nothing usable came back; run it again"})
    proposal_id = None
    if row["created_by"] == STRATEGY_BOT and result["changes"]:
        proposal_id = _propose(c, row, result)
    _finish(c, row, "done", result=result, proposal_id=proposal_id)


def merged(c, changes):
    """The changes' payloads as one partial draft: the first summary and vision, sections together, bets together (a
    later change to the same bet id adds its fields). A change that would make the whole refused is left out."""
    out = {}
    for change in changes:
        payload = change["payload"]
        trial = {k: v for k, v in out.items()}
        for key in ("summary", "vision"):
            if payload.get(key) and not trial.get(key):
                trial[key] = payload[key]
        if payload.get("sections"):
            sections = {**trial.get("sections", {})}
            for key, value in payload["sections"].items():
                if key == "extra" and isinstance(value, dict):
                    sections["extra"] = {**sections.get("extra", {}), **value}
                elif key != "extra" and key not in sections:
                    sections[key] = value
            trial["sections"] = sections
        if payload.get("bets"):
            bets = [dict(b) for b in trial.get("bets", [])]
            for item in payload["bets"]:
                same = next((b for b in bets if item.get("id") and b.get("id") == item["id"]), None)
                if same:
                    same.update(item)
                else:
                    bets.append(dict(item))
            trial["bets"] = bets
        if trial != out and not S.proposal_error(c, trial):
            out = trial
    return out


def _propose(c, row, result):
    """The Strategy Bot's run: its changes, merged, as one `strategy_draft` proposal for the owner to decide. None when
    the proposal is refused (the run is kept without one)."""
    from . import goals as G
    payload = merged(c, result["changes"])
    if not payload:
        return None
    lens(row["lens"])
    payload = {**payload, "source": "lens", "lens": row["lens"]}
    c.execute("SAVEPOINT lens_proposal")
    try:
        found = G.propose(c, row["created_by"], "strategy_draft", payload=payload,
                          reason=result["summary"])
    except H.Refused:
        c.execute("ROLLBACK TO lens_proposal")
        found = None
    c.execute("RELEASE lens_proposal")
    return found["id"] if found else None


# ----------------------------------------------------------------------------- the owner's follow-ups
def owner_only(who):
    from . import goal_routes
    if not goal_routes.company_editor(who):
        raise Problem("forbidden", "Only the owner adds a lens's changes to the draft or asks the Librarian to sharpen "
                      "it, on their own click", 403)


def apply(c, auth, who, run_id, index, revision):
    """The owner adds one change to the draft: the code path of confirming a `strategy_draft` proposal, against the
    draft revision the page showed. Each change is added once."""
    owner_only(who)
    row = read(c, who, run_id)
    if row["proposal_id"]:
        raise Problem("proposed", "These changes were sent to you as a proposal; decide it on its card", 409)
    changes = (S.loads(row["result"], {}) or {}).get("changes") or []
    if not 0 <= index < len(changes):
        raise Problem("invalid", f"This run has {len(changes)} change{'s' if len(changes) != 1 else ''}", 422)
    applied = S.loads(row["applied"], [])
    if index in applied:
        raise Problem("applied", "That change was already added to the draft", 409)
    S._check_revision(c, S.draft(c), revision)
    payload = changes[index]["payload"]
    error = S.proposal_error(c, payload)         # a bet may have been retired since the run
    if error:
        raise Problem("invalid", error, 422)
    S.apply_proposal(c, who.actor, payload)
    c.execute("UPDATE lens_runs SET applied=? WHERE id=?", (S.dumps(sorted([*applied, index])), run_id))
    S._event(c, who.actor, "lens_apply", {"run_id": run_id, "lens": row["lens"], "index": index})
    return {"run": view(_row(c, run_id)), "strategy": S.view(c, auth, who)}


def sharpen(c, auth, who, run_id, i):
    """The owner asks the Librarian for the data that would sharpen a run: one research task on the market's open
    questions, filed once. A second ask answers the task it already filed."""
    from . import librarian
    owner_only(who)
    row = read(c, who, run_id)
    asks = (S.loads(row["result"], {}) or {}).get("sharpen") or []
    if not 0 <= i < len(asks):
        raise Problem("invalid", f"This run has {len(asks)} thing{'s' if len(asks) != 1 else ''} to sharpen", 422)
    filed = S.loads(row["sharpen_tasks"], {})
    if str(i) not in filed:
        the = lens(row["lens"])
        body = M.LibrarianResearch(
            target=SHARPEN_TARGET, instructions=f"{asks[i]}\n\nFrom the {the['name'] if the else row['lens']} lens, "
                                                f"run {run_id}.")
        task = librarian.research(c, auth, who, body)
        filed[str(i)] = task["task_id"]
        c.execute("UPDATE lens_runs SET sharpen_tasks=? WHERE id=?", (S.dumps(filed), run_id))
    return {"run": view(_row(c, run_id)), "task_id": filed[str(i)]}


# ----------------------------------------------------------------------------- HTTP
def install(app, store, auth, mutate):
    from fastapi import Request

    def reader(who):
        auth.domain(who)

    @app.get("/api/v2/lenses")
    def lenses_list(request: Request, applies_to: str = ""):
        """The listed lenses, strategy first then market: id, name, description, minutes, steps, output and what would
        sharpen each. `?applies_to=strategy|market` narrows them."""
        reader(request.state.identity)
        if applies_to and applies_to not in APPLIES:
            raise Problem("validation", f"applies_to is {'|'.join(APPLIES)}", 422)
        return {"lenses": listed(applies_to or None)}

    @app.get("/api/v2/lenses/runs")
    def lens_runs(request: Request, lens: str = "", limit: int = LIMIT):
        """Runs, newest first: only the ones the caller may see (a draft run is the owner's). Each carries the
        summary only; GET /lenses/runs/{id} has the whole run."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return {"runs": runs(c, who, lens or None, limit)}

    @app.get("/api/v2/lenses/runs/{run_id}")
    def lens_run_show(request: Request, run_id: str):
        """One run: its answers, state and result. A run on the draft is the owner's: 404 for anyone else."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return view(read(c, who, run_id))

    @app.post("/api/v2/lenses/{lens_id}/runs", status_code=202)
    def lens_run_start(request: Request, lens_id: str, body: M.LensRun):
        """The owner, the Strategy Bot and, for a market lens, the Librarian start a run. `on: draft` is the owner's.
        Answers at once with the run (`running`; `no_model` when no model provider is set up, the answers saved)."""
        who = request.state.identity
        reader(who)
        found = mutate(request, body, lambda c: start(c, who, store.settings, store, lens_id, body.answers, body.on))
        if found.get("status") == "running":
            spawn(lambda: run(store, found["id"]))
        return found

    @app.post("/api/v2/lenses/runs/{run_id}/apply")
    def lens_run_apply(request: Request, run_id: str, body: M.LensApply):
        """Owner only: add one of a run's changes to the draft. `revision` is the draft's as read: another answers 409.
        A change already added answers 409 `applied`."""
        who = request.state.identity
        reader(who)
        return mutate(request, body, lambda c: apply(c, auth, who, run_id, body.index, body.revision))

    @app.post("/api/v2/lenses/runs/{run_id}/sharpen/{i}")
    def lens_run_sharpen(request: Request, run_id: str, i: int, body: M.Empty):
        """Owner only: file the Librarian research task for one thing that would sharpen the run. Filed once."""
        who = request.state.identity
        reader(who)
        return mutate(request, body, lambda c: sharpen(c, auth, who, run_id, i))
