"""tico.company.yaml, formats 0.2 and 0.3: written from a draft as the setup page's `toYAML()` writes it, and read back.

The file holds the plan only: never `undo`, `chat`, `removed` or `pending`, and never a credential. Format 0.2 differs
from the page's 0.1 in its goals (a measure, unit, direction and numbers in place of free-text target and due date),
a bot's `reports_to` set by the visitor or BotOps, the one `first_job`, and an existing agent's department and reason.
Format 0.3 adds the `strategy` block (after `goals:`) and a goal's `bet`. A plan without a strategy is still written as
0.2, byte for byte, so its file imports on an app from before 0.3; the reader takes both.
An employee's or existing agent's optional `team` (the name of its group) is read and written in either format.
"""
import datetime as dt
import json
import re

from . import draft as D
from .keys import scrub_all

FORMAT = "0.2"
STRATEGY_FORMAT = "0.3"
FORMATS = (FORMAT, STRATEGY_FORMAT)
HEADER = ("# tico.company.yaml", "# Made with Tico setup at https://tico.team/setup",
          "# Format %s. Import in Tico's first-run setup or with tico setup --company-file.",
          "# No passwords, keys or credentials are stored here; tools are connected on your own install.")
NOT_SET = "# not set yet: which number, and by when?"
# YAML 1.1 reads these bare words as booleans or null, and a leading digit can read as a number or a date.
PLAIN = re.compile(r"[a-z][a-z0-9_-]*")
WORDS = {"y", "n", "yes", "no", "on", "off", "true", "false", "null"}
# Characters a YAML reader refuses or reads as a line break, written as escapes.
UNSAFE = re.compile("[\x7f-\x9f  ﻿￾￿]")


def q(text):
    """setup.js `q()`: a double-quoted string, "" when empty."""
    return UNSAFE.sub(lambda m: "\\u%04x" % ord(m.group(0)), json.dumps(str(text or ""), ensure_ascii=False))


def word(value):
    """An id or a vocabulary word: bare when YAML reads it back as the same text."""
    return value if PLAIN.fullmatch(value) and value not in WORDS else q(value)


def num(value):
    if value is None:
        return "null"
    text = repr(value)
    # YAML 1.1 reads 1e-05 as text: its floats need a dot and a signed exponent.
    mantissa, _, exponent = text.partition("e")
    if exponent:
        text = "%s%se%s%s" % (mantissa, "" if "." in mantissa else ".0", "" if exponent[0] in "+-" else "+", exponent)
    return text


def tool_list(draft):
    """setup.js `toolList()`: the tools chosen, or by default every tool a bot on the chart uses."""
    if draft["tools"]["mode"] == "custom":
        return draft["tools"]["connect"]
    return [t for t in D.TOOLS if any(t in b["tools"] for b in draft["bots"])]


def to_yaml(draft, created=None):
    """The company file for a draft, with key-shaped strings removed whatever path the draft took. `created_at` defaults
    to now."""
    draft = D.check(scrub_all(draft)[0])
    created = created or dt.datetime.now(dt.timezone.utc)
    head = D.owner(draft)
    head_id = head["id"] if head else "you"
    strategy = D.has_strategy(draft) or any("bet" in g for g in draft["goals"])
    version = STRATEGY_FORMAT if strategy else FORMAT
    L = [line % version if "%s" in line else line for line in HEADER]
    L += ["version: %s" % version, "created: %s" % created.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
          + "%03dZ" % (created.microsecond // 1000)]
    company = draft["company"]
    L += ["company:", "  name: %s" % q(company["name"]), "  about: %s" % q(company["about"]),
          "  signals: [%s]" % ", ".join(company["signals"])]
    L.append("people:")
    if not draft["people"]:
        L += ["  - id: you", '    name: "You"', '    role: "Owner"', "    owner: true"]
    for p in draft["people"]:
        L += ["  - id: %s" % word(p["id"]), "    name: %s" % q(p["name"]), "    role: %s" % q(p["role"]),
              "    owner: true" if p["owner_id"] else "    reports_to: %s" % word(head_id)]
    L.append("employees:" + ("" if any(not b["existing"] for b in draft["bots"]) else " []"))
    for b in (b for b in draft["bots"] if not b["existing"]):
        L += ["  - id: %s" % word(b["id"]), "    template: %s" % word(b["template"]), "    title: %s" % q(b["title"]),
              "    department: %s" % b["department"], "    reports_to: %s" % word(D.manager_of(draft, b)),
              "    start: %s" % ("now" if b["start"] else "later"), "    reason: %s" % q(b["reason"])]
        if b.get("team"):
            L.append("    team: %s" % q(b["team"]))
        if b["routine"]:
            L.append("    first_routine: %s" % q(b["routine"]))
        if b["tools"]:
            L.append("    tools: [%s]" % ", ".join(word(t) for t in b["tools"]))
        if b.get("first_job"):
            L.append("    first_job: %s" % q(b["first_job"]))
        if b["template"] in D.OPEN_SEATS:
            L.append("    no_bot_yet: true  # Tico has no template for this seat yet")
        if b.get("person"):
            L.append("    held_by: %s" % word(b["person"]))
    L.append("existing_agents:" + ("" if any(b["existing"] for b in draft["bots"]) else " []"))
    for b in (b for b in draft["bots"] if b["existing"]):
        L += ["  - id: %s" % word(b["id"]), "    runtime: %s" % b["runtime"], "    title: %s" % q(b["title"]),
              "    job: %s" % q(b["about"]), "    department: %s" % b["department"], "    reason: %s" % q(b["reason"]),
              "    reports_to: %s" % word(D.manager_of(draft, b))]
        if b.get("team"):
            L.append("    team: %s" % q(b["team"]))
    L.append("goals:" + ("" if draft["goals"] else " []"))
    for g in draft["goals"]:
        L += ["  - id: %s" % word(g["id"]), "    title: %s" % q(g["title"]),
              "    owner: %s" % word(head_id if g["owner_id"] == "you" else g["owner_id"]),
              "    measure: %s" % (q(g["measure"]) if g["measure"] else "null  " + NOT_SET),
              "    unit: %s" % q(g["unit"]), "    direction: %s" % g["direction"]]
        for key in D.AIM[g["direction"]]:
            L.append("    %s: %s" % (key, g[key] or "null" if key == "deadline" else num(g[key])))
        if "bet" in g:
            L.append("    bet: %s" % word(g["bet"]))
    if strategy:
        L += strategy_lines(draft.get("strategy") or D.blank_strategy())
    L += ["rules:", "  mode: %s" % draft["rules"]["mode"], "  never_without_a_person:" + ("" if draft["rules"]["never"] else " []")]
    L += ["    - %s  # %s" % (r, D.RULES[r]) for r in draft["rules"]["never"]]
    L.append("  review: %s  # %s" % (draft["rules"]["review"], D.REVIEW[draft["rules"]["review"]]))
    L += ["tools:", "  mode: %s" % draft["tools"]["mode"],
          "  connect: [%s]  # connected after setup, never during it" % ", ".join(tool_list(draft))]
    return "\n".join(L) + "\n"


def _items(key, rows, indent, lines):
    """A list of entries under `key`: `[]` on the key's line when empty, else one `- ` entry per row."""
    pad = " " * indent
    out = ["%s%s:%s" % (pad, key, "" if rows else " []")]
    for row in rows:
        first, *rest = lines(row)
        out += ["%s  - %s" % (pad, first)] + ["%s    %s" % (pad, line) for line in rest]
    return out


def strategy_lines(s):
    """The `strategy:` block, format 0.3: every part written, an empty one as "" or []."""
    market, listening = s["market"], s["listening"]
    L = ["strategy:", "  summary: %s" % q(s["summary"]), "  vision: %s" % q(s["vision"]), "  sections:"]
    L += ["    %s: %s" % (key, q(s["sections"][key])) for key in D.SECTIONS]
    L += _items("bets", s["bets"], 2, lambda b: [
        "id: %s" % word(b["id"]), "statement: %s" % q(b["statement"]), "why: %s" % q(b["why"]),
        "rules_out: %s" % q(b["rules_out"]), "wrong_if: %s" % q(b["wrong_if"]),
        "check_by: %s" % (b["check_by"] or q(""))])
    definition = market["definition"]
    L += ["  market:", "    definition:", "      root: %s" % q(definition["root"]), "      in: %s" % q(definition["in"]),
          "      out: %s" % q(definition["out"]), "      year: %s" % num(definition["year"])]
    L += _items("segments", market["segments"], 4, lambda g: [
        "key: %s" % word(g["key"]), "name: %s" % q(g["name"]),
        "parent: %s" % ("null" if g["parent"] is None else word(g["parent"])), "usd: %s" % num(g["usd"]),
        "method: %s" % word(g["method"]), "arithmetic: %s" % q(g["arithmetic"]),
        "evidence: [%s]" % ", ".join(q(u) for u in g["evidence"]), "ours: %s" % str(g["ours"]).lower(),
        "next: %s" % str(g["next"]).lower()])
    L += _items("players", market["players"], 4, lambda p: [
        "name: %s" % q(p["name"]), "url: %s" % q(p["url"]),
        "segments: [%s]" % ", ".join(word(key) for key in p["segments"])])
    L += ["  listening:", "    requests:" + ("" if listening["requests"] else " []")]
    L += ["      - %s" % q(text) for text in listening["requests"]]
    L += _items("posts", listening["posts"], 4, lambda p: [
        "source: %s" % word(p["source"]), "url: %s" % q(p["url"]), "title: %s" % q(p["title"]),
        "when: %s" % (p["when"] or q("")), "tag: %s" % word(p["tag"])])
    return L


# ---------------------------------------------------------------------------------------------------- reading
def _rows(doc, key):
    rows = doc.get(key) or []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise D.DraftError("%s must be a list of entries" % key)
    return rows


def _str(value):
    return "" if value is None else value.isoformat() if isinstance(value, dt.date) else str(value) \
        if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def _part(value):
    return value if isinstance(value, dict) else {}


def _team(raw):
    """A bot's `team` as the file names it: trimmed and clipped to LIMITS["team"], "" when absent or blank. A value that
    is not text is returned as it is, for draft.check to refuse."""
    team = _str(raw.get("team"))
    return team.strip()[:D.LIMITS["team"]].strip() if isinstance(team, str) else team


def _strategy(raw):
    """The strategy block as the draft keeps it: dates as text, a missing text as "", the rest for D.check to judge."""
    raw = _part(raw)
    market, listening = _part(raw.get("market")), _part(raw.get("listening"))
    definition, sections = _part(market.get("definition")), _part(raw.get("sections"))
    return {"summary": _str(raw.get("summary")), "vision": _str(raw.get("vision")),
            "sections": {key: _str(sections.get(key)) for key in D.SECTIONS},
            "bets": [{"id": _str(b.get("id")), **{key: _str(b.get(key)) for key in
                                                  ("statement", "why", "rules_out", "wrong_if", "check_by")}}
                     for b in _rows(raw, "bets")],
            "market": {"definition": {"root": _str(definition.get("root")), "in": _str(definition.get("in")),
                                      "out": _str(definition.get("out")), "year": definition.get("year")},
                       "segments": [{"key": _str(g.get("key")), "name": _str(g.get("name")),
                                     "parent": None if g.get("parent") is None else _str(g.get("parent")),
                                     "usd": g.get("usd"), "method": _str(g.get("method")) or "top_down",
                                     "arithmetic": _str(g.get("arithmetic")),
                                     "evidence": [_str(u) for u in g.get("evidence") or []],
                                     "ours": g.get("ours") is True, "next": g.get("next") is True}
                                    for g in _rows(market, "segments")],
                       "players": [{"name": _str(p.get("name")), "url": _str(p.get("url")),
                                    "segments": [_str(key) for key in p.get("segments") or []]}
                                   for p in _rows(market, "players")]},
            "listening": {"requests": [_str(r) for r in listening.get("requests") or []],
                          "posts": [{"source": _str(p.get("source")), "url": _str(p.get("url")),
                                     "title": _str(p.get("title")), "when": _str(p.get("when")),
                                     "tag": _str(p.get("tag"))} for p in _rows(listening, "posts")]}}


def from_yaml(text):
    """The draft a company file holds (D.DraftError when it isn't a format 0.2 or 0.3 file Tico can read). Key-shaped
    strings in it are removed. The draft starts on the chart tab with no undo, chat, removed or pending."""
    import yaml  # PyYAML: only reading a file needs it.

    if not isinstance(text, str) or len(text) > D.LIMITS["draft_bytes"]:
        raise D.DraftError("the file is too large")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        raise D.DraftError("the file isn't valid YAML") from None
    if not isinstance(doc, dict) or str(doc.get("version")) not in FORMATS:
        raise D.DraftError("the file isn't a format %s company file" % " or ".join(FORMATS))
    doc, _ = scrub_all(doc)
    company = doc.get("company") if isinstance(doc.get("company"), dict) else {}
    draft = D.blank()
    draft["step"] = "chart"
    draft["company"] = {"name": _str(company.get("name")), "about": _str(company.get("about")),
                        "signals": company.get("signals") or []}
    people = _rows(doc, "people")
    if len(people) == 1 and people[0] == {"id": "you", "name": "You", "role": "Owner", "owner": True}:
        people = []
    draft["people"] = [{"id": _str(p.get("id")), "name": _str(p.get("name")), "role": _str(p.get("role")),
                        "department": D.dept_of(_str(p.get("role"))), "owner_id": p.get("owner_id") is True} for p in people]
    chosen = {}
    for b in _rows(doc, "employees"):
        role = next((r for r in D.ROLES if r["slug"] == b.get("template")), None) \
            or next((dict(r, lead=False, core=False) for r in D.SEATS if r["slug"] == b.get("template")), None)
        if not role:
            raise D.DraftError("employees has a template Tico doesn't have: %s" % str(b.get("template"))[:40])
        bot = {"id": _str(b.get("id")), "template": role["slug"], "title": _str(b.get("title")), "dept": b.get("department"),
               "lead": role["lead"], "core": role["core"], "reason": _str(b.get("reason")),
               "start": b.get("start") == "now" or b.get("start") is True, "routine": _str(b.get("first_routine")),
               "tools": b.get("tools") or [], "about": role["summary"][:D.LIMITS["summary"]], "runtime": None,
               "existing": False}
        if b.get("first_job"):
            bot["first_job"] = _str(b["first_job"])
        if _team(b):
            bot["team"] = _team(b)
        if b.get("held_by"):
            bot["person"] = _str(b["held_by"])
        chosen[bot["id"]] = _str(b.get("reports_to"))
        draft["bots"].append(bot)
    for b in _rows(doc, "existing_agents"):
        bot = {"id": _str(b.get("id")), "template": None, "title": _str(b.get("title")), "dept": b.get("department"),
               "lead": False, "core": False, "reason": _str(b.get("reason")), "start": True, "routine": "", "tools": [],
               "about": _str(b.get("job")), "runtime": b.get("runtime"), "existing": True}
        if _team(b):
            bot["team"] = _team(b)
        chosen[bot["id"]] = _str(b.get("reports_to"))
        draft["bots"].append(bot)
    for g in _rows(doc, "goals"):
        goal = {"id": _str(g.get("id")), "title": _str(g.get("title")), "owner_id": _str(g.get("owner_id")),
                "measure": _str(g.get("measure")), "unit": _str(g.get("unit")), "direction": g.get("direction") or "up"}
        for key in D.AIM.get(goal["direction"], ()):
            goal[key] = _str(g.get(key)) or None if key == "deadline" else g.get(key)
        if g.get("bet") is not None:
            goal["bet"] = _str(g["bet"])
        draft["goals"].append(goal)
    rules = doc.get("rules") if isinstance(doc.get("rules"), dict) else {}
    tools = doc.get("tools") if isinstance(doc.get("tools"), dict) else {}
    draft["rules"] = {"mode": rules.get("mode"), "never": rules.get("never_without_a_person") or [],
                      "review": rules.get("review")}
    draft["tools"] = {"mode": tools.get("mode"), "connect": (tools.get("connect") or []) if tools.get("mode") == "custom" else []}
    draft["seq"] = len(draft["people"]) + len(draft["bots"]) + len(draft["goals"]) + 1
    if isinstance(doc.get("strategy"), dict):
        draft["strategy"] = _strategy(doc["strategy"])
    draft = D.check(draft)
    # A reports-to the file names is kept only where it differs from the line the chart draws anyway.
    for bot in draft["bots"]:
        if chosen.get(bot["id"]) and chosen[bot["id"]] != D.manager_of(draft, bot):
            bot["reports_to"] = chosen[bot["id"]]
    return D.check(draft)
