"""The edit actions: each takes a draft and its arguments and changes the draft, or refuses in plain words.

`Turn` is one visitor turn. It takes the draft as the page sent it (the visitor's own edits stand), removes key-shaped
strings, starts an empty chart from the company model closest to the company's signals, runs the actions one at a time (a refused action leaves the draft
as it was) and, when the plan changed, keeps the draft from before the turn in `undo`. It records the parts each action
changed (`changed`), so the page can show which part the chat updated.
"""
import calendar
import copy
import datetime as dt
import difflib
import re

from . import draft as D
from .keys import scrub, scrub_all

SPEC = {row["name"]: row for row in D.SPEC["actions"]}
# A seat with no template yet reads like a role with nothing to set up: no routine, no tools, never a lead.
SEAT_ROLES = [{"slug": seat["slug"], "template": seat["slug"], "name": seat["name"], "department": seat["department"],
               "lead": False, "core": False, "summary": seat["summary"], "first_routine": "", "tools": [],
               "recommend_when": [], "open": True} for seat in D.SEATS]
BY_SLUG = {role["slug"]: role for role in D.ROLES + SEAT_ROLES}
BY_NAME = {role["name"].lower(): role for role in D.ROLES + SEAT_ROLES}
NEVER_REMOVED = ("botops",)


class Refusal(Exception):
    """An action that cannot apply. Its message goes back to the model and may be shown to the visitor."""


# ---------------------------------------------------------------------------------------------------- schemas
def schema_error(schema, value, path="input"):
    """The first way `value` breaks a strict JSON schema (type, properties, required, additionalProperties, enum,
    items), or None."""
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds] if kinds else []
    tests = {"string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
             "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
             "integer": lambda v: isinstance(v, int) and not isinstance(v, bool), "null": lambda v: v is None,
             "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict)}
    if kinds and not any(tests[kind](value) for kind in kinds):
        return "%s must be %s" % (path, " or ".join(kinds))
    if value is None:
        return None
    if "enum" in schema and value not in schema["enum"]:
        return "%s must be one of: %s" % (path, ", ".join(map(str, schema["enum"])))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                return "%s has no field %s" % (path, extra[0][:40])
        for key in schema.get("required", []):
            if key not in value:
                return "%s needs %s" % (path, key)
        for key, item in value.items():
            if key in properties:
                error = schema_error(properties[key], item, "%s.%s" % (path, key))
                if error:
                    return error
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            error = schema_error(schema["items"], item, "%s[%d]" % (path, i))
            if error:
                return error
    return None


# ---------------------------------------------------------------------------------------------------- templates
STOP = set("a an the and or of to for in on with our we us my i me is are be it that this they them help need want more "
           "some any get have has do does work your from at by as".split())
# setup.js ALIASES: everyday words for a role, mapped to the template that does it.
ALIASES = {
    "seo": "seo-visibility", "social": "listening",
    "social media": "listening", "blog": "content", "writer": "content", "copywriter": "content", "sdr": "sdr-research",
    "recruiter": "recruiting", "qa": "issue-triage", "tester": "issue-triage", "reviewer": "pr-reviewer",
    "code reviewer": "pr-reviewer", "engineer": "pr-reviewer", "support": "support", "customer support": "support",
    "invoices": "billing", "invoicing": "billing", "payroll": "payroll", "legal": "general-counsel",
    "lawyer": "general-counsel", "contracts": "legal-review", "hr": "people-hr", "chief of staff": "chief-of-staff",
    "pr": "pr", "press": "pr", "ads": "paid-media", "release manager": "release-notes", "docs": "docs-writer",
    "technical writer": "docs-writer", "note taker": "meeting-notes", "meeting notes": "meeting-notes",
    "project manager": "meeting-notes", "goal manager": "goal-manager", "ceo bot": "goal-manager",
    "librarian": "librarian", "assistant": "coo",
    "botops": "botops", "cfo": "finance-lead", "cmo": "marketing-lead", "cto": "engineering-lead",
    "cap table": "equity-manager", "equity": "equity-manager", "fundraising": "fundraising-manager",
    "month-end close": "controller", "bookings": "scheduling-bookings", "scheduling": "scheduling-bookings",
    "inspections": "quality-inspections", "quality": "quality-inspections",
}


def _words(text):
    return [w for w in re.findall(r"[a-z0-9&+]+", str(text).lower()) if w not in STOP and len(w) > 1]


def _stem(word):
    return re.sub(r"(ing|ers|er|es|s)$", "", re.sub(r"ies$", "y", word))


def score(role, text):
    """setup.js `scoreTemplate()`: how well a role's name, tags, pains and summary fit the words."""
    said = {_stem(w) for w in _words(text)}
    if not said:
        return 0.0
    name = [_stem(w) for w in _words(role["name"])]
    total = 6.0 if name and all(w in said for w in name) else 0.0
    total += sum(2 for w in name if w in said)
    total += sum(1.5 for tag in role["tags"] if _stem(tag) in said)
    for pain in role["pains"]:
        hits = sum(1 for w in _words(pain) if _stem(w) in said)
        total += hits if hits >= 2 else 0
    total += sum(0.4 for w in _words(role["summary"]) if _stem(w) in said and len(_stem(w)) > 4)
    return total


def find_role(text):
    """The template a slug, name or everyday word names exactly, else None. A name is tried whole before its "bot" or
    "agent" ending is dropped, so the built-in's old name ("ceo bot") is an alias and "Strategy Bot" finds its
    template."""
    key = re.sub(r"^(an?|the|some|another)\s+", "", str(text).strip().lower()).strip()
    bare = re.sub(r"\s+(bot|agent)$", "", key)
    if key in ALIASES:
        return BY_SLUG.get(ALIASES[key])
    if bare in ALIASES:
        return BY_SLUG.get(ALIASES[bare])
    return BY_SLUG.get(bare) or BY_NAME.get(key) or BY_NAME.get(bare)


def closest_role(text):
    """The real template nearest to a name that isn't one: the best word fit, else the closest spelling."""
    best = max(D.ROLES, key=lambda role: score(role, text))
    if score(best, text) > 0:
        return best
    names = {role["name"].lower(): role for role in D.ROLES} | {role["slug"]: role for role in D.ROLES}
    match = difflib.get_close_matches(str(text).lower(), list(names), n=1, cutoff=0)
    return names[match[0]] if match else None


# ---------------------------------------------------------------------------------------------------- dates
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9


def _next_day(today, month, day):
    """The first `month`/`day` after today (Feb 29 waits for a leap year)."""
    for year in range(today.year, today.year + 9):
        try:
            when = dt.date(year, month, day)
        except ValueError:
            continue
        if when > today:
            return when
    raise Refusal("That isn't a real date.")


def _month_end(year, month):
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _add_months(today, months):
    month = today.month - 1 + months
    year, month = today.year + month // 12, month % 12 + 1
    return dt.date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def deadline(text, today):
    """A deadline in words or as a date, as YYYY-MM-DD. "by March 1" is the next March 1 after today; "by June" or
    "the end of June" is June's last day; also "in 90 days", "in 3 months", "end of year", "end of quarter"."""
    words = re.sub(r"\s+", " ", str(text).strip().lower().replace(",", " "))
    words = re.sub(r"^(?:by|on|before|due|until)\s+", "", words)
    words = re.sub(r"^the\s+", "", words).strip(" .")
    when = None
    if D.DATE.fullmatch(words):
        try:
            when = dt.date.fromisoformat(words)
        except ValueError:
            raise Refusal("%s isn't a real date." % words) from None
    month_name = "(%s)" % "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.fullmatch(r"%s\.? (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?" % month_name, words) \
        or re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? (?:of )?%s\.?(?: (\d{4}))?" % month_name, words)
    if when is None and m:
        groups = m.groups()
        name, day = (groups[0], groups[1]) if not groups[0].isdigit() else (groups[1], groups[0])
        month, day, year = MONTHS[name], int(day), groups[2]
        if year:
            try:
                when = dt.date(int(year), month, day)
            except ValueError:
                raise Refusal("That isn't a real date.") from None
        else:
            when = _next_day(today, month, day)
    m = re.fullmatch(r"(?:end of )?%s(?: (\d{4}))?" % month_name, words)
    if when is None and m:
        month = MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else today.year + (1 if month < today.month else 0)
        when = _month_end(year, month)
    m = re.fullmatch(r"(?:in|within) (\d{1,4}) (day|week|month|year)s?", words)
    if when is None and m:
        n, unit = int(m.group(1)), m.group(2)
        # Checked before the arithmetic: "in 9999 years" is past the calendar's last year.
        if n * {"day": 1, "week": 7, "month": 31, "year": 366}[unit] > 3660:
            raise Refusal("That deadline is more than ten years away; pick a nearer one.")
        when = today + dt.timedelta(days=n if unit == "day" else 7 * n) if unit in ("day", "week") \
            else _add_months(today, n * (12 if unit == "year" else 1))
    if when is None and re.fullmatch(r"(?:end of (?:the |this )?year|this year|eoy)", words):
        when = dt.date(today.year, 12, 31)
    if when is None and re.fullmatch(r"(?:end of (?:the |this )?quarter|this quarter|eoq)", words):
        when = _month_end(today.year, (today.month - 1) // 3 * 3 + 3)
    if when is None:
        raise Refusal("I couldn't read that deadline as a date. Use a date like %s or words like \"March 1\"."
                      % _next_day(today, 3, 1).isoformat())
    if when <= today:
        raise Refusal("That deadline (%s) has already passed; pick a later one." % when.isoformat())
    if when > today + dt.timedelta(days=3660):
        raise Refusal("That deadline is more than ten years away; pick a nearer one.")
    return when.isoformat()


# ---------------------------------------------------------------------------------------------------- helpers
def next_id(draft, prefix):
    taken = {row["id"] for rows in (draft["bots"], draft["people"], draft["goals"]) for row in rows}
    while True:
        candidate = "%s-%d" % (prefix[:D.LIMITS["id"] - 8], draft["seq"])
        draft["seq"] += 1
        if candidate not in taken:
            return candidate


def _text(args, key, limit, needed=True):
    value = str(args.get(key) or "").strip()
    if needed and not value:
        raise Refusal("%s can't be empty." % key.capitalize().replace("_", " "))
    if len(value) > limit:
        raise Refusal("%s is too long: keep it under %d characters." % (key.capitalize().replace("_", " "), limit))
    return value


def find_bot(draft, ref):
    ref = str(ref or "").strip()
    found = next((b for b in draft["bots"] if b["id"] == ref), None) \
        or next(iter([b for b in draft["bots"] if b["title"].lower() == ref.lower()][:1]), None)
    if not found:
        raise Refusal("There's no bot \"%s\" on the chart." % ref[:80])
    return found


def _goal(draft, ref):
    found = next((g for g in draft["goals"] if g["id"] == str(ref)), None)
    if not found:
        raise Refusal("There's no goal \"%s\"." % str(ref)[:80])
    return found


def _title_taken(draft, title, but=None):
    return any(b["title"].lower() == title.lower() and b is not but for b in draft["bots"])


def _person_named(draft, ref):
    ref = str(ref).strip()
    return next((p for p in draft["people"] if p["id"] == ref or p["name"].lower() == ref.lower()), None)


def _target(draft, ref, notes, add_people=False):
    """The id on the chart a reports-to or a goal owner names: a person, one of Tico's bots, or "you". With
    `add_people`, a name nobody has yet joins the team as a person."""
    ref = str(ref or "").strip()
    if not ref:
        raise Refusal("Say who: a person, a bot, or \"you\".")
    if ref.lower() in ("you", "me", "myself", "i", "owner", "the owner"):
        return D.owner_id(draft)
    person = _person_named(draft, ref)
    if person:
        return person["id"]
    bot = next((b for b in draft["bots"] if not b["existing"] and (b["id"] == ref or b["title"].lower() == ref.lower())), None)
    if bot:
        return bot["id"]
    role = find_role(ref)
    if role:
        on_chart = next((b for b in draft["bots"] if b["template"] == role["slug"] and not b["existing"]), None)
        if on_chart:
            return on_chart["id"]
        raise Refusal("There's no %s on the chart yet. Add one first, or name someone else." % role["name"])
    if add_people and re.fullmatch(r"[A-Za-z][A-Za-z .'-]{0,%d}" % (D.LIMITS["name"] - 1), ref) and len(ref.split()) <= 4:
        if not draft["people"]:
            # The visitor owns the company, not the first name a goal mentions.
            add_person(draft, "me", "Owner", owner=True)
        person = add_person(draft, ref, "")
        notes.append("Added %s to the team as a person." % person["name"])
        return person["id"]
    raise Refusal("There's nobody called \"%s\" on the chart." % ref[:80])


def add_person(draft, name, role, owner=False):
    """setup.js `addPerson()`: the first person, or one marked owner, owns the company."""
    me = name.strip().lower() in ("me", "myself", "i")
    name = "You" if me else re.sub(r"\b\w", lambda m: m.group(0).upper(), name.strip())
    if len(draft["people"]) >= D.LIMITS["people"]:
        raise Refusal("A chart can have at most %d people." % D.LIMITS["people"])
    if _person_named(draft, name):
        raise Refusal("%s is already on the team." % name)
    base = D.slugify(name)[:D.LIMITS["id"] - 8]
    taken = {row["id"] for rows in (draft["bots"], draft["people"], draft["goals"]) for row in rows}
    person_id = next_id(draft, base) if base in taken or (base == "you" and not me) else base
    person = {"id": person_id, "name": name, "role": role, "department": D.dept_of(role), "owner": False, "isNew": True}
    founder = re.search(r"\b(ceo|founder|owner|president)\b", role, re.I) or me
    if owner or not draft["people"] or (founder and not any(p["owner_id"] and re.search(r"\b(ceo|founder|owner)\b", p["role"], re.I)
                                                              for p in draft["people"])):
        for other in draft["people"]:
            other["owner_id"] = False
        person["owner_id"] = True
    draft["people"].append(person)
    return person


def _make_bot(draft, role, title, reason, start):
    taken = {row["id"] for rows in (draft["bots"], draft["people"], draft["goals"]) for row in rows}
    ids = (role["slug"], D.slugify(title)[:D.LIMITS["id"]])
    bot_id = next((c for c in (ids if title == role["name"] else ids[::-1]) if c not in taken), None) \
        or next_id(draft, role["slug"])
    return {"id": bot_id, "template": role["slug"], "title": title, "dept": role["department"], "lead": role["lead"],
            "core": role["core"], "reason": reason, "start": start, "routine": role["first_routine"][:D.LIMITS["routine"]],
            "tools": list(role["tools"]), "about": role["summary"][:D.LIMITS["summary"]], "runtime": None,
            "existing": False, "isNew": True}


def _take_off(draft, gone):
    """Take bots off the chart: their goals fall to the owner, reports-to them fall back to the default line, and
    each is remembered in `removed` so it isn't added back unasked."""
    ids = {b["id"] for b in gone}
    draft["bots"] = [b for b in draft["bots"] if b["id"] not in ids]
    for bot in draft["bots"]:
        if bot.get("reports_to") in ids:
            del bot["reports_to"]
    for goal in draft["goals"]:
        if goal["owner_id"] in ids:
            goal["owner_id"] = D.owner_id(draft)
    removed = [r for r in draft.get("removed", []) if r["title"].lower() not in {b["title"].lower() for b in gone}]
    removed += [{"template": b["template"], "title": b["title"]} for b in gone]
    draft["removed"] = removed[-D.LIMITS["removed"]:]


# What a model's name reads as in BotOps's line about it.
MODEL_WORDS = {"everything": "everything Tico runs", "software": "a software company", "services": "a services firm",
               "local": "a local service business", "store": "an online store"}
# Signals that pick the model or say who the company sells to and how big it is: the model already answers them, so
# they add no roles of their own. The rest ("has a support inbox", "uses GitHub") each add the roles that do that work.
BROAD = {"sells_to_businesses", "sells_to_consumers", "small_team"} | {s for m in D.MODELS.values() for s in m["picks_when"]}


def pick_model(signals):
    """The model a company's signals point to: the first in D.MODEL_ORDER whose `picks_when` they hold, else Everything."""
    return next((key for key in D.MODEL_ORDER if set(D.MODELS[key]["picks_when"]) & set(signals)), "everything")


def _lay_out(draft, model, reason):
    """A model's seats on the chart, each in its model team, head first; a seat `under` another reports to it by
    title. A seat already there by title is left as it is."""
    for row in model["seats"]:
        role = BY_SLUG[row["template"]]
        title = row.get("title") or role["name"]
        if _title_taken(draft, title):
            continue
        bot = _make_bot(draft, role, title, reason, False)
        bot["department"] = row["team"]  # a model may seat a role in another team: the store's Pricing Analyst is in Finance
        draft["bots"].append(bot)
    by_title = {b["title"].lower(): b for b in draft["bots"]}  # the same case-blind match as `_title_taken`
    for row in model["seats"]:
        boss = by_title.get(str(row.get("under", "")).lower())
        bot = by_title.get((row.get("title") or BY_SLUG[row["template"]]["name"]).lower())
        if boss and bot and bot is not boss:
            bot.setdefault("reports_to", boss["id"])


def _core(draft):
    for row in D.MODELS["everything"]["core"]:
        role = BY_SLUG[row["template"]]
        if not any(b["template"] == role["slug"] for b in draft["bots"]):
            draft["bots"].append(_make_bot(draft, role, row.get("title") or role["name"],
                                           role["summary"][:D.LIMITS["reason"]], True))


def seed(draft):
    """Start an empty chart from the model closest to the company's signals, then add more ("close to their answers,
    but err on the side of showing more"): the head of every department Everything has and the model lacks (Product
    and Engineering only for a company that sells software), and every role whose `recommend_when` names one of the
    company's own signals. Nothing the model has is dropped for a missing signal. Returns BotOps's one line about it."""
    signals = set(draft["company"]["signals"])
    key = pick_model(signals)
    model = D.MODELS[key]
    _core(draft)
    _lay_out(draft, model, model["reason"])
    software = "sells_software" in signals
    gone = {r["title"].lower() for r in draft.get("removed", [])}
    heads = []
    for row in D.MODELS["everything"]["seats"]:
        role = BY_SLUG[row["template"]]
        if not role["lead"] or (role["department"] in ("product", "engineering") and not software):
            continue
        if any(b["department"] == role["department"] and b["lead"] for b in draft["bots"]) or role["name"].lower() in gone:
            continue
        draft["bots"].append(_make_bot(draft, role, role["name"], "Every chart has a head for each department.", False))
        heads.append(role["name"])
    own = signals - BROAD
    matched = []
    for role in D.ROLES:
        if role["core"] or not own & set(role["recommend_when"]) or _title_taken(draft, role["name"]):
            continue
        if (role["department"] in ("product", "engineering") and not software) or role["name"].lower() in gone:
            continue
        if any(b["template"] == role["slug"] for b in draft["bots"]):
            continue
        draft["bots"].append(_make_bot(draft, role, role["name"], "Fits what you said about how the company works.", False))
        matched.append(role["name"])
    if key == "everything":
        line = "Nothing you said points to one kind of company, so we started from the Everything model"
    else:
        line = "This looks like %s, so we started from the %s model" % (MODEL_WORDS[key], model["name"])
    added = (["the %s" % _join(heads)] if heads else []) + (["%s" % _join(matched)] if matched else [])
    return line + (" and added %s." % " and ".join(added) if added else ".")


def _join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------------------------------------------- the actions
def company_set(draft, args, today, notes):
    company = draft["company"]
    if not any(key in args for key in ("name", "about", "signals")):
        raise Refusal("Say what to change: the name, what the company does, or its signals.")
    if "name" in args:
        company["name"] = _text(args, "name", D.LIMITS["name"], needed=False)
    if "about" in args:
        company["about"] = _text(args, "about", D.LIMITS["about"], needed=False)
    if "signals" in args:
        company["signals"] = [s for s in D.SIGNALS if s in args["signals"]]
    return "Updated the company."


def bot_add(draft, args, today, notes):
    role = find_role(args["template"])
    if not role:
        near = closest_role(args["template"])
        raise Refusal("Tico has no \"%s\" template.%s" % (str(args["template"])[:80],
                      " The closest is %s (%s)." % (near["name"], near["slug"]) if near else ""))
    if role["slug"] in ("botops", "coo") and any(b["template"] == role["slug"] for b in draft["bots"]):
        raise Refusal("%s is already on the chart; there is only one." % role["name"])
    title = _text(args, "title", D.LIMITS["title"], needed=False) or role["name"]
    if _title_taken(draft, title):
        raise Refusal("There's already a %s on the chart. Give the new one another title, or change that one." % title)
    if len(draft["bots"]) >= D.LIMITS["bots"]:
        raise Refusal("A chart can have at most %d bots." % D.LIMITS["bots"])
    reason = _text(args, "reason", D.LIMITS["reason"])
    bot = _make_bot(draft, role, title, reason, args.get("start") == "now" or role["core"])
    draft["bots"].append(bot)
    draft["removed"] = [r for r in draft.get("removed", []) if r["title"].lower() != title.lower()]
    return "Added %s (%s) to %s." % (title, role["slug"], role["department"])


def bot_remove(draft, args, today, notes):
    bot = find_bot(draft, args["bot"])
    if bot["template"] in NEVER_REMOVED:
        raise Refusal("BotOps stays: it sets up and fixes the other bots.")
    _take_off(draft, [bot])
    return "Removed %s." % bot["title"]


def team_remove(draft, args, today, notes):
    gone = [b for b in draft["bots"] if b["department"] == args["team"] and not b["core"] and not b["existing"]]
    if not gone:
        raise Refusal("There's no %s team on the chart." % args["team"])
    _take_off(draft, gone)
    kept = [b["title"] for b in draft["bots"] if b["department"] == args["team"] and b["existing"]]
    return "Removed the %s team (%d bots).%s" % (args["team"], len(gone),
                                                 " Kept your own %s." % ", ".join(kept) if kept else "")


def bot_rename(draft, args, today, notes):
    bot = find_bot(draft, args["bot"])
    title = _text(args, "title", D.LIMITS["title"])
    if _title_taken(draft, title, but=bot):
        raise Refusal("Another bot is already called %s." % title)
    old, bot["title"], bot["isNew"] = bot["title"], title, True
    return "Renamed %s to %s." % (old, title)


def bot_set_reports_to(draft, args, today, notes):
    bot = find_bot(draft, args["bot"])
    target = _target(draft, args["to"], notes)
    if target == bot["id"]:
        raise Refusal("A bot can't report to itself.")
    before = bot.get("reports_to")
    bot["reports_to"] = target
    if D.loops(draft):
        if before is None:
            del bot["reports_to"]
        else:
            bot["reports_to"] = before
        raise Refusal("That would make a loop: %s already manages the one you named, directly or through others."
                      % bot["title"])
    return "%s now reports to %s." % (bot["title"], _name(draft, target))


def _tico_bot(draft, ref):
    """One of Tico's bots: the visitor's own agents keep their own schedule and work, and the file has no start,
    routine or first job for them."""
    bot = find_bot(draft, ref)
    if bot["existing"]:
        raise Refusal("%s is an agent you already run; its start, routine and first job stay as they are." % bot["title"])
    return bot


def bot_set_start(draft, args, today, notes):
    bot = _tico_bot(draft, args["bot"])
    if bot["core"]:
        raise Refusal("%s comes with every install and is always on." % bot["title"])
    bot["start"] = args["start"] == "now"
    return "%s starts %s." % (bot["title"], args["start"])


def bot_set_routine(draft, args, today, notes):
    bot = _tico_bot(draft, args["bot"])
    bot["routine"] = _text(args, "routine", D.LIMITS["routine"])
    return "Set %s's first routine." % bot["title"]


def bot_set_first_job(draft, args, today, notes):
    bot = _tico_bot(draft, args["bot"])
    if bot["core"]:
        raise Refusal("Pick one of the team's bots for the first job, not %s." % bot["title"])
    job = _text(args, "job", D.LIMITS["first_job"])
    for other in draft["bots"]:
        other.pop("first_job", None)
    bot["first_job"], bot["start"] = job, True
    return "%s has the first job and starts now." % bot["title"]


MEASURE_KEYS = ("measure", "unit", "direction", "target", "baseline", "deadline", "min", "max")
NOT_SET = {"measure": "", "unit": "", "direction": "up", "target": None, "baseline": None, "deadline": None}


def _aim(args, today, old=None):
    """A goal's measure from an action's fields, over the goal's old measure when there is one: a complete measure,
    or not set yet. A measure needs its unit, and a target and deadline (up or down) or a min and max (range)."""
    given = {key: args[key] for key in MEASURE_KEYS if key in args}
    if old and "direction" in given and given["direction"] != old["direction"]:
        old = {key: old[key] for key in ("measure", "unit")}
    raw = {**{key: value for key, value in (old or {}).items() if key in MEASURE_KEYS}, **given}
    if not str(raw.get("measure") or "").strip():
        if any(raw.get(key) not in (None, "", "up") for key in MEASURE_KEYS if key in given):
            raise Refusal("Say what is counted (the measure) along with its numbers.")
        return dict(NOT_SET)
    if isinstance(raw.get("deadline"), str) and raw["deadline"].strip():
        raw["deadline"] = deadline(raw["deadline"], today)
    else:
        raw["deadline"] = None
    if raw.get("direction", "up") == "range":
        raw.pop("target", None), raw.pop("baseline", None), raw.pop("deadline", None)
    try:
        out = D.aim(raw, "goal")
    except D.DraftError as error:
        raise Refusal("The measure isn't right: %s." % str(error).replace("goal.", "")) from None
    needed = ["unit"] + (["min", "max"] if out["direction"] == "range" else ["target", "deadline"])
    missing = [key for key in needed if out[key] in (None, "")]
    if missing:
        raise Refusal("A measure needs its %s too. Ask the visitor, or leave the measure out until they say."
                      % " and ".join(missing))
    return out


def goal_add(draft, args, today, notes):
    if len(draft["goals"]) >= D.LIMITS["goals"]:
        raise Refusal("The plan has the most goals it can hold (%d)." % D.LIMITS["goals"])
    title = _text(args, "title", D.LIMITS["goal_title"])
    aim = _aim(args, today)
    owner = _target(draft, args["owner_id"], notes, add_people=True) if args.get("owner_id") else D.owner_id(draft)
    draft["goals"].append({"id": next_id(draft, "goal"), "title": title, "owner_id": owner, **aim, "isNew": True})
    return "Added the goal \"%s\", owned by %s%s." % (title, _name(draft, owner),
                                                      "" if aim["measure"] else "; its measure is not set yet")


def goal_edit(draft, args, today, notes):
    goal = _goal(draft, args["goal"])
    if not any(key in args for key in ("title", "owner") + MEASURE_KEYS):
        raise Refusal("Say what to change: the title, the owner or the measure.")
    if "title" in args:
        goal["title"] = _text(args, "title", D.LIMITS["goal_title"])
    if any(key in args for key in MEASURE_KEYS):
        old = {key: goal.pop(key) for key in MEASURE_KEYS if key in goal}
        goal.update(_aim(args, today, old if old.get("measure") else None))
    if "owner_id" in args:
        goal["owner_id"] = _target(draft, args["owner"], notes, add_people=True)
    goal["isNew"] = True
    return "Updated the goal \"%s\"." % goal["title"]


def goal_remove(draft, args, today, notes):
    goal = _goal(draft, args["goal"])
    draft["goals"].remove(goal)
    return "Removed the goal \"%s\"." % goal["title"]


def person_set(draft, args, today, notes):
    name = _text(args, "name", D.LIMITS["name"])
    role = _text(args, "role", D.LIMITS["role"], needed=False)
    if not args.get("person"):
        person = add_person(draft, name, role, owner=bool(args.get("owner")))
        return "Added %s%s." % (person["name"], " (%s)" % role if role else "")
    person = next((p for p in draft["people"] if p["id"] == args["person"]), None)
    if not person:
        raise Refusal("There's nobody with the id \"%s\" on the team." % str(args["person"])[:80])
    other = _person_named(draft, name)
    if other and other is not person:
        raise Refusal("%s is already on the team." % name)
    if args.get("owner") is False and person["owner_id"]:
        raise Refusal("Someone has to own the company: mark another person as the owner instead.")
    person["name"], person["isNew"] = name, True
    if "role" in args:
        person["role"], person["department"] = role, D.dept_of(role)
    if args.get("owner"):
        for each in draft["people"]:
            each["owner_id"] = each is person
    return "Updated %s." % name


def agent_set(draft, args, today, notes):
    job = _text(args, "job", D.LIMITS["summary"])
    runtime = args["runtime"]
    dept = D.dept_of(job) or ("engineering" if re.search(r"code|engineer|\bpr\b|bug|github", job, re.I)
                              or runtime in ("claude-code", "codex", "cursor", "devin", "copilot") else "operations")
    fields = {"title": D.HARNESSES[runtime], "runtime": runtime, "about": job, "dept": dept,
              "reason": ("Already yours: %s." % job.rstrip("."))[:D.LIMITS["reason"]]}
    if args.get("agent"):
        agent = next((b for b in draft["bots"] if b["id"] == args["agent"] and b["existing"]), None)
        if not agent:
            raise Refusal("There's no agent of yours with the id \"%s\" on the chart." % str(args["agent"])[:80])
        agent.update(fields, isNew=True)
        return "Updated your %s." % agent["title"]
    if len(draft["bots"]) >= D.LIMITS["bots"]:
        raise Refusal("A chart can have at most %d bots." % D.LIMITS["bots"])
    draft["bots"].append({"id": next_id(draft, runtime), "template": None, "lead": False, "core": False, "start": True,
                          "routine": "", "tools": [], "existing": True, "isNew": True, **fields})
    return "Added your %s, now on the chart with a manager." % fields["title"]


def rules_set_never(draft, args, today, notes):
    draft["rules"].update(mode="custom", never=[r for r in D.RULES if r in args["never"]])
    return "Set what always needs a person."


def rules_set_review(draft, args, today, notes):
    draft["rules"].update(mode="custom", review=args["review"])
    return "Set the review level: %s." % D.REVIEW[args["review"]]


def rules_use_defaults(draft, args, today, notes):
    blank = D.blank()
    draft["rules"], draft["tools"] = blank["rules"], blank["tools"]
    return "Back to Tico's default rules and tools."


def tools_set(draft, args, today, notes):
    draft["tools"] = {"mode": "custom", "connect": [t for t in D.TOOLS if t in args["connect"]]}
    return "Set the tools to connect after setup."


# ---------------------------------------------------------------------------------------------------- the strategy
STRATEGY_PARTS = {"summary": "the summary", "vision": "the vision", "serve": "Who we serve", "sell": "What we sell",
                  "know": "What we know", "not": "What we won't do"}
CHECK_DAYS = 90             # a new bet is looked at again this many days out


def _strategy(draft):
    if draft.get("strategy") is None:
        draft["strategy"] = D.blank_strategy()
    return draft["strategy"]


def _bet(draft, ref):
    found = next((b for b in _strategy(draft)["bets"] if b["id"] == str(ref).strip().lower()), None)
    if not found:
        raise Refusal("There's no bet \"%s\"." % str(ref)[:80])
    return found


def set_strategy_text(draft, args, today, notes):
    field = args["field"]
    limit = D.LIMITS["strategy_line"] if field in ("summary", "vision") else D.LIMITS["section"]
    text = _text(args, "text", limit, needed=False)
    strategy = _strategy(draft)
    if field in ("summary", "vision"):
        strategy[field] = text
    else:
        strategy["sections"][field] = text
    return ("Cleared %s." if not text else "Updated %s.") % STRATEGY_PARTS[field]


def _check_by(args, today):
    return deadline(args["check_by"], today) if str(args.get("check_by") or "").strip() else ""


def set_bet(draft, args, today, notes):
    bet = _bet(draft, args["id"])
    fields = ("statement", "why", "rules_out", "wrong_if", "check_by")
    if not any(key in args for key in fields):
        raise Refusal("Say what to change: the statement, why, what it rules out, what would show it wrong, or when "
                      "to check.")
    if "statement" in args:
        bet["statement"] = _text(args, "statement", D.LIMITS["statement"])
    for key in ("why", "rules_out", "wrong_if"):
        if key in args:
            bet[key] = _text(args, key, D.LIMITS["bet_text"], needed=False)
    if "check_by" in args:
        bet["check_by"] = _check_by(args, today)
    return "Updated bet %s." % bet["id"][1:]


def add_bet(draft, args, today, notes):
    bets = _strategy(draft)["bets"]
    if len(bets) >= D.LIMITS["bets"]:
        raise Refusal("The strategy has %d bets, the most it holds. Drop one first." % D.LIMITS["bets"])
    taken = {b["id"] for b in bets}
    bet = {"id": next("b%d" % n for n in range(1, D.LIMITS["bets"] + 1) if "b%d" % n not in taken),
           "statement": _text(args, "statement", D.LIMITS["statement"])}
    for key in ("why", "rules_out", "wrong_if"):
        bet[key] = _text(args, key, D.LIMITS["bet_text"], needed=False)
    bet["check_by"] = (today + dt.timedelta(days=CHECK_DAYS)).isoformat()
    bets.append(bet)
    return "Added bet %s: %s" % (bet["id"][1:], bet["statement"])


def drop_bet(draft, args, today, notes):
    bet = _bet(draft, args["id"])
    _strategy(draft)["bets"].remove(bet)
    linked = [g for g in draft["goals"] if g.get("bet") == bet["id"]]
    for goal in linked:
        del goal["bet"]
    return "Removed bet %s.%s" % (bet["id"][1:], " %d goal%s no longer tied to a bet." % (
        len(linked), " is" if len(linked) == 1 else "s are") if linked else "")


def add_listening_request(draft, args, today, notes):
    requests = _strategy(draft)["listening"]["requests"]
    text = _text(args, "text", D.LIMITS["request"])
    if text.lower() in {r.strip().lower() for r in requests}:
        raise Refusal("That is already being watched.")
    if len(requests) >= D.LIMITS["requests"]:
        raise Refusal("At most %d things are watched. Drop one first." % D.LIMITS["requests"])
    requests.append(text)
    return "Watching for: %s" % text


def drop_listening_request(draft, args, today, notes):
    listening = _strategy(draft)["listening"]
    text = str(args.get("text") or "").strip().lower()
    found = next((r for r in listening["requests"] if r.strip().lower() == text), None)
    if found is None:
        raise Refusal("Nothing called \"%s\" is being watched." % str(args.get("text"))[:80])
    listening["requests"].remove(found)
    return "Stopped watching for: %s" % found


def resize_market(draft, args, today, notes):
    """Changes nothing: the turn keeps the note (`Turn.market_note`) and whatever serves the turn sizes the market again."""
    _text(args, "note", D.LIMITS["market_scope"])
    return "Sizing the market again with that."


def step_go(draft, args, today, notes):
    draft["step"] = args["step"]
    return "Moved to %s." % args["step"]


def _name(draft, ident):
    if ident == "you":
        return "you"
    found = next((p["name"] for p in draft["people"] if p["id"] == ident), None) \
        or next((b["title"] for b in draft["bots"] if b["id"] == ident), None)
    return found or ident


def model_use(draft, args, today, notes):
    """Switch the chart to one company model as it stands (the model chips; "show me everything" is Everything). The
    people, the visitor's own agents and the seats people hold stay; every other bot gives way to the model's."""
    model = D.MODELS[args["model"]]
    kept = [b for b in draft["bots"] if b["core"] or b["existing"] or "person" in b]
    _take_off(draft, [b for b in draft["bots"] if b not in kept])
    _core(draft)
    _lay_out(draft, model, model["reason"])
    taken = {b["title"].lower() for b in draft["bots"]}
    draft["removed"] = [r for r in draft.get("removed", []) if r["title"].lower() not in taken]
    return "The chart is now the %s model (%d seats)." % (model["name"], len(draft["bots"]))


def seat_fill(draft, args, today, notes):
    """A person holds a seat ("Ana runs our cap table"): added to the team when new, with the seat's title as role."""
    bot = find_bot(draft, args["seat"])
    if bot["core"] or bot["existing"]:
        raise Refusal("%s isn't a seat a person can hold." % bot["title"])
    person = _person_named(draft, args["name"])
    if not person:
        if not draft["people"] and str(args["name"]).strip().lower() not in ("me", "myself", "i"):
            # The visitor owns the company, not the first coworker who holds a seat.
            add_person(draft, "me", "Owner", owner=True)
        person = add_person(draft, args["name"], bot["title"])
    if person["department"] is None:
        person["department"] = bot["department"]
    for other in draft["bots"]:
        if other.get("person") == person["id"] and other is not bot:
            del other["person"]
    bot["person"], bot["isNew"] = person["id"], True
    return "%s holds the %s seat." % (person["name"], bot["title"])


ACTIONS = {fn.__name__: fn for fn in (
    company_set, model_use, seat_fill, bot_add, bot_remove, bot_rename, bot_set_reports_to, bot_set_start, bot_set_routine, bot_set_first_job,
    team_remove, goal_add, goal_edit, goal_remove, person_set, agent_set, rules_set_never, rules_set_review,
    rules_use_defaults, tools_set, set_strategy_text, set_bet, add_bet, drop_bet, add_listening_request,
    drop_listening_request, resize_market, step_go)}


def touched(before, after):
    """The parts of the plan that differ: a top-level part by name, and the strategy by its own parts (summary,
    vision, sections.know, bets.b1, market, listening.requests), as the page's "Updated from chat" flash names them."""
    out = [key for key in ("company", "people", "bots", "goals", "rules", "tools") if before.get(key) != after.get(key)]
    one, two = before.get("strategy") or D.blank_strategy(), after.get("strategy") or D.blank_strategy()
    out += [key for key in ("summary", "vision") if one[key] != two[key]]
    out += ["sections." + key for key in D.SECTIONS if one["sections"][key] != two["sections"][key]]
    bets_one, bets_two = {b["id"]: b for b in one["bets"]}, {b["id"]: b for b in two["bets"]}
    out += ["bets." + key for key in sorted(set(bets_one) | set(bets_two)) if bets_one.get(key) != bets_two.get(key)]
    out += ["market"] if one["market"] != two["market"] else []
    out += ["listening." + key for key in ("requests", "posts") if one["listening"][key] != two["listening"][key]]
    return out


# ---------------------------------------------------------------------------------------------------- a turn
class Turn:
    """One visitor turn over a draft.

        turn = Turn(draft_from_page, today=dt.date.today())    # D.DraftError when the draft breaks its shape
        turn.apply("bot_add", {"template": "billing", "reason": "Invoices eat your Fridays."})
        turn.finish()                                          # the draft to send back

    `keys_removed` says a key-shaped string was cut from the draft or an action's input this turn; `seeded` says the
    empty chart was started from a company model, and `started_at` is BotOps's one line on which model and what it added.
    `changed` lists the parts the turn's actions changed (`touched`), in order; `market_note` is what the visitor said
    when they asked for the market to be sized again (`resize_market`), else None.
    """

    def __init__(self, raw, today=None):
        self.today = today or dt.date.today()
        raw, self.keys_removed = scrub_all(raw)
        self.draft = D.check(raw)
        self.seeded = not any(b["template"] for b in self.draft["bots"])
        self.started = ""
        if self.seeded:
            self.started = seed(self.draft)
            self.draft = D.check(self.draft)
        # The model is where the chart begins, not a change to undo.
        self.before = D.content(self.draft)
        self.changed, self.market_note = [], None

    def apply(self, name, args):
        """{"ok": true, "said": what changed} or {"ok": false, "refused": why}. A refused action changes nothing."""
        if name == "undo":
            return self._undo(args)
        if name not in ACTIONS:
            name, hit = scrub(str(name))
            self.keys_removed = self.keys_removed or hit
            return {"ok": False, "refused": "There's no action called %s." % name[:40]}
        args, hit = scrub_all(args)
        self.keys_removed = self.keys_removed or hit
        error = schema_error(SPEC[name]["input_schema"], args)
        if error:
            return {"ok": False, "refused": "That %s isn't right: %s." % (name, error)}
        work, notes = copy.deepcopy(self.draft), []
        try:
            said = ACTIONS[name](work, args, self.today, notes)
            work = D.check(work)
        except Refusal as refusal:
            return {"ok": False, "refused": str(refusal)}
        except D.DraftError as error:
            return {"ok": False, "refused": "That change would break the plan: %s." % error}
        self._touch(self.draft, work)
        self.draft = work
        if name == "resize_market":
            self.market_note = args["note"].strip()
        return {"ok": True, "said": " ".join([said] + notes)}

    def _touch(self, before, after):
        self.changed += [path for path in touched(before, after) if path not in self.changed]

    def _undo(self, args):
        if args not in ({}, None):
            return {"ok": False, "refused": "undo takes no input."}
        snapshot = self.draft.get("undo")
        if snapshot is None:
            return {"ok": False, "refused": "There's nothing to undo yet."}
        keep = {key: self.draft[key] for key in ("step", "chat", "pending") if key in self.draft}
        undone = D.check({**copy.deepcopy(snapshot), **keep})
        self._touch(self.draft, undone)
        self.draft = undone
        # Undone is the new starting point: one level, so a second undo has nothing to go back to.
        self.before = D.content(self.draft)
        return {"ok": True, "said": "Undid the last change."}

    def finish(self):
        """The draft after this turn. When the plan changed, `undo` holds the draft from before it."""
        out = D.check(self.draft)
        if not D.same_plan(out, self.before):
            out["undo"] = copy.deepcopy(self.before)
        return out


def apply_action(draft, name, args, today=None):
    """One action as its own turn: (the draft after it, {"ok", "said" or "refused", "keys_removed"})."""
    turn = Turn(draft, today)
    result = turn.apply(name, args)
    return turn.finish(), {**result, "keys_removed": turn.keys_removed}


def undo(draft, today=None):
    """The draft as it was before BotOps's last change (unchanged, with a refusal, when there is none)."""
    return apply_action(draft, "undo", {}, today)
