"""Recruiting a seat (docs/onboarding.md, "Recruiting a seat"; bot-lifecycle-spec.md section 2, acceptance 6 to 9).

A Recruiting seat (backend/lifecycle.py) is filled in three steps, the same from the org chart and from the Team plus
menu's Post a job, which creates the seat first:

1. **Post it.** `draft` writes a job description from the title at once, on this server: the catalog card whose name
     and tags best fit the title, as one line from its summary, what it owns and the tools it works in. With
     `redraft` and a Gemini key, one model call rewrites that text in under five seconds; without either, the composed
     text is the answer. Nothing is stored until the person posts the job (`POST /v2/seats`), and anyone who may change
     the seat edits it later (lifecycle `_may_place`).
2. **Recruit.** `candidates` answers at once, on this server, from the template catalog: up to eight Tico templates,
     the best fit to the seat's title and job description first, each with one line, what it does on day one, what it
     needs and how well its name fits the title (`match`). No trial task and no outside service.
3. **Pick.** `hire` fills the seat: the bot is created in Onboarding, in the seat's place on the chart with its
     reporting line, under the same rules as any bot the person adds (backend/settings_admin.py `create_bot`). Its
     repository comes only after that: a starter's computer builds it from the bot's definition, any other template's
     BotOps does on the "Set up" task it gets now. The seat is then closed, filled by that bot."""
import json
from dataclasses import replace
from urllib.request import Request, urlopen

from . import hubdb as H
from . import lifecycle
from . import models as M
from . import releases
from . import recruit_rank as R
from .store import Problem

CANDIDATES = 8
CHART = "chart"                         # `description_by` for a chart model's text
DRAFT_OWNS = 5                          # bullets under "What you'll do"
REWRITE_SECONDS = 5
REWRITE_MODEL = "gemini-3.7-flash"
# The tools a card's `prerequisites` name, as a person reads them. Tico's own tasks, docs and the open web need nothing
# set up, so a candidate needing them has them; any other tool is set up on hire (required) or later (optional).
TOOL_LABELS = {"hub": "Tico tasks", "docs": "Docs", "web": "Web", "mail": "Email", "calendar": "Calendar",
               "meetings": "Meetings", "github": "GitHub", "crm": "CRM", "chat": "Chat"}
BUILT_IN_TOOLS = frozenset({"hub", "docs", "web"})
# Words of rank, not of the work: "Support lead" and "Engineering lead" share no job.
RANKS = frozenset(stem for word in ("lead", "head", "manager", "senior", "junior", "chief", "director", "specialist",
                                    "associate", "officer") for stem, _ in R._words(word))


# ------------------------------------------------------------------ the seat as the recruiting page shows it
def _may_edit(auth, c, who, row):
    try:
        lifecycle._may_place(auth, c, who, row["reports_to"])
        return True
    except Problem:
        return False


def view(auth, c, who, row):
    seat = lifecycle._seat(row)
    by = str(row["description_by"] or "")
    seat.update(description_by="person" if by.startswith("human:") or by == lifecycle.EARLIER else "chart",
                can_edit=_may_edit(auth, c, who, row))
    return seat


def show(auth, c, who, seat_id):
    return view(auth, c, who, lifecycle.seat(c, seat_id))


def end_draft(c, row, why):
    """Close a draft task an earlier release asked the Strategy Bot for, once the seat is filled, closed or written by a
    person: nothing drafts a seat's description in the background any more."""
    task = H.task(c, row["draft_task"]) if row["draft_task"] else None
    if task and task["status"] in H.ACTIVE_STATUSES:
        H.task_update(c, task["owner"], task["id"], status="declined", note=why[:500])


# ------------------------------------------------------------------ 1. the job description, drafted from the title
def _tool(tool):
    return TOOL_LABELS.get(tool, tool.replace("-", " ").replace("_", " ").capitalize())


def _best_card(cards, title, team=""):
    """The catalog card whose name and tags best fit the title, or None when nothing does. A card in the department
    the seat's group is named for wins a tie."""
    words = R._words(title)
    if not words:
        return None
    common = R._common(cards)
    group = str(team or "").strip().lower()
    best = None
    for card in cards:
        score, _ = R._match(card, words, common)
        score += 3 * len(R._stems(card["name"]) & R._stems(title) - RANKS)
        if not score:
            continue
        key = (score, card["department"] == group, -len(card["name"]), card["template"])
        if best is None or key > best[0]:
            best = (key, card)
    return best[1] if best else None


def compose(card):
    """One line from the card's summary, what it owns as "What you'll do", and the tools it works in."""
    raw = card["raw"]
    lines = [R._first_sentence(raw["summary"] or card["summary"], 200)]
    owns = [R._text(item, 160) for item in raw["owns"][:DRAFT_OWNS] if R._text(item, 160)]
    if owns:
        lines += ["", "What you'll do", *("- " + item for item in owns)]
    tools = list(dict.fromkeys(_tool(row["tool"]) for row in raw["prerequisites"]))
    if tools:
        lines += ["", "Works in: " + ", ".join(tools)]
    return "\n".join(lines).strip()[:lifecycle.DESCRIPTION_MAX]


def boss_name(c, reports_to):
    """Who a seat reports to, by name: a person's, a bot's or another seat's title."""
    reports = str(reports_to or "")
    if reports.startswith("human:"):
        return (H.human(c, reports[6:]) or {}).get("name") or reports[6:]
    if reports.startswith(lifecycle.SEAT_PREFIX):
        row = c.execute("SELECT title FROM seats WHERE id=?", (reports[len(lifecycle.SEAT_PREFIX):],)).fetchone()
        return row["title"] if row else ""
    return (H.bot(c, reports) or {}).get("display_name") or reports


def _rewrite(text, title, boss):
    """One Gemini call that rewrites the composed description for this role, in at most REWRITE_SECONDS. Tests patch
    this; it raises when there is no key or the call fails, and the caller keeps the composed text."""
    from clients.docs_qa import key
    prompt = (f"Rewrite this job description for the role \"{title}\"" + (f", reporting to {boss}" if boss else "")
              + ". Keep its shape: one opening line, then \"What you'll do\" with three to five short bullets "
              "starting with \"- \", then one \"Works in:\" line. Plain words, under 150 words, no other headings, and "
              "never name a template, a model or a vendor. Answer with the text only.\n\n" + text)
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    request = Request(f"https://generativelanguage.googleapis.com/v1beta/models/{REWRITE_MODEL}:generateContent",
                      data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": key()})
    with urlopen(request, timeout=REWRITE_SECONDS) as response:
        payload = json.load(response)
    parts = payload["candidates"][0]["content"]["parts"]
    return "".join(part.get("text", "") for part in parts).strip()


def draft(onboarding, title, team="", boss="", redraft=False):
    """A job description for a seat not posted yet, in well under a second: composed from the catalog card that best
    fits the title (`source` "catalog"), or "" when none does (`source` "none"). `redraft` asks the model to rewrite
    the composed text once (`source` "model"); with no key, or a slow or failed call, the composed text stands."""
    _, cards = _recruitable(onboarding)
    card = _best_card(cards, str(title or "")[:lifecycle.TITLE_MAX], team)
    if not card:
        return {"description": "", "source": "none"}
    text = compose(card)
    if redraft:
        try:
            rewritten = _rewrite(text, str(title).strip()[:lifecycle.TITLE_MAX], boss)
        except (OSError, ValueError, KeyError, IndexError, TypeError):   # no key, slow, failed or odd: composed text stands
            rewritten = ""
        if len(rewritten) >= 40:
            return {"description": rewritten[:lifecycle.DESCRIPTION_MAX], "source": "model"}
    return {"description": text, "source": "catalog"}


# ------------------------------------------------------------------ 2. candidates
def _recruitable(onboarding):
    """The catalog cards a seat may be filled from: the org builder's own (no built-in, no helper; recruit_rank.build),
    with each card's model, effort and whether it is a starter from the card itself."""
    from .recruit import catalog
    from .onboarding import read_cards
    full = {card["template"]: card for card in read_cards(onboarding.settings)}
    value = catalog(onboarding.settings)
    out = []
    for card in value["cards"]:
        raw = full.get(card["template"])
        if raw and not raw["bootstrap"] and not raw["required"] and raw["kind"] != "helper":
            out.append({**card, "raw": raw})
    return value, out


def _models(c, onboarding):
    """The models one may choose instead: current models of the company's enabled providers."""
    from . import providers
    enabled = providers.load(c, onboarding.settings)["enabled"]
    return [{"id": m["id"], "name": m.get("label") or m["id"], "provider": m.get("provider") or ""}
            for m in onboarding.models.values() if m.get("provider") in enabled and not m.get("deprecated")]


def _suggested(c, onboarding, raw):
    picked = onboarding._runtime(c, raw)
    if not picked:
        return {"model": "", "model_name": "Your team's default model, once one is chosen", "effort": ""}
    model, effort = picked
    return {"model": model, "model_name": onboarding.models[model].get("label") or model, "effort": effort}


def _needs(raw):
    """What the card needs, each with its state: a built-in tool is connected, any other is set up on hire when the
    card requires it and not set when it is optional."""
    out = []
    for row in raw["prerequisites"]:
        tool = row["tool"]
        state = "connected" if tool in BUILT_IN_TOOLS else "on_hire" if row["required"] else "not_set"
        out.append({"tool": tool, "label": _tool(tool), "required": row["required"], "state": state})
    return out


def candidates(auth, onboarding, c, who, seat_id):
    """Recruit: up to eight Tico templates for the seat, strong matches first, then good ones, each tier best first.
    `match` is "strong" when the template's name shares a word of the work with the title (not one of rank), "good"
    when its tags, name or summary fit the title or job description at all, else "". Deterministic and local: the same
    seat gives the same list, and nothing leaves this server."""
    row = lifecycle.seat(c, seat_id)
    # Listing candidates changes nothing: BotOps lists them with the rights of the person it acts for, and the pick is
    # their own tap on a card (botops_act.CONFIRM).
    _may_hire(auth, c, replace(who, confirmed=True) if who.via == "botops" else who, row)
    value, cards = _recruitable(onboarding)
    departments = {d["id"]: d["name"] for d in value["departments"]}
    title = [(stem, word) for stem, word in R._words(row["title"])]
    words = title + [("", "")] + R._words(R._text(row["description"], 4000))
    common = R._common(cards)
    scored = []
    for card in cards:
        score, _ = R._match(card, words, common)
        named, _ = R._match(card, title, common)
        shared = len(R._stems(card["name"]) & R._stems(row["title"]) - RANKS)
        score += 2 * named + 3 * shared
        match = "strong" if shared else "good" if score > 0 else ""
        scored.append((("strong", "good", "").index(match), -score, {"default": 0, "common": 1}.get(card["suggest"], 2),
                       card["name"].lower(), card, match))
    scored.sort(key=lambda item: item[:4])
    out = []
    for *_, card, match in scored[:CANDIDATES]:
        raw = card["raw"]
        out.append({"template": card["template"], "name": card["name"], "icon": card["icon"],
                    "department": departments.get(card["department"], ""), "department_id": card["department"],
                    "line": R._first_sentence(card["summary"], 90), "summary": R._text(raw["summary"], 1000),
                    "tags": card["tags"][:6], "owns": raw["owns"][:6], "never": raw["never"][:6],
                    "onboarding": [item["ask"] for item in raw["onboarding"] if item["ask"]][:6],
                    "prerequisites": _needs(raw), "match": match, "source": "template", "starter": bool(raw["starter"]),
                    **_suggested(c, onboarding, raw)})
    return {"seat": view(auth, c, who, row), "candidates": out, "models": _models(c, onboarding)}


# ------------------------------------------------------------------ 3. the pick
def _may_hire(auth, c, who, row):
    """Whoever may change the seat picks for it: the owner, or a person it reports up to, in person or on a request
    they confirmed. A bot never picks, and BotOps acting for someone does so only on their confirmed request."""
    if who.role not in ("owner", "human") or (who.via and not who.confirmed):
        raise Problem("forbidden", "Only a person picks a candidate for a seat: the owner, or someone the seat reports "
                      "up to", 403)
    lifecycle._may_place(auth, c, who, row["reports_to"])


def hire(auth, onboarding, c, who, seat_id, body):
    """Fill the seat with the picked candidate: the bot in Onboarding, in the seat's place, and only then its repo."""
    from . import message_bots
    from .onboarding import BOTOPS, display_names, load, render
    row = c.execute("SELECT * FROM seats WHERE id=?", (seat_id,)).fetchone()
    if row and row["filled_by"]:
        name = (H.bot(c, row["filled_by"]) or {}).get("display_name") or row["filled_by"]
        raise Problem("filled", f"This seat is already filled by {name}", 409)
    row = lifecycle.seat(c, seat_id)
    _may_hire(auth, c, who, row)
    _, cards = _recruitable(onboarding)
    card = next((card["raw"] for card in cards if card["template"] == body.template), None)
    if not card:
        raise Problem("template", "Pick one of the candidates Recruit offered", 422)
    if not card["starter"] and not H.bot(c, BOTOPS):
        raise Problem("botops", "BotOps builds this bot, and this team has no BotOps; add it from Templates first", 409)
    if body.model:
        if body.model not in {m["id"] for m in _models(c, onboarding)}:
            raise Problem("model", "Choose one of your team's current models", 422)
        model, effort = body.model, ""
    else:
        suggested = _suggested(c, onboarding, card)
        model, effort = suggested["model"], suggested["effort"]
    stand_in = not model
    if stand_in:
        # No provider yet: a stand-in satisfies the definition, then the bot follows the company default (as setup does).
        model, effort = next((m["id"], m.get("default_effort") or "") for m in onboarding.models.values()
                             if m.get("provider") and not m.get("deprecated"))
    record = load(c)
    rendered = render(card, display_names(onboarding.settings, record), row["title"])
    description = str(row["description"] or "").strip()
    instructions = rendered["instructions"].rstrip() + ("\n\n## Job description\n" + description + "\n" if description else "")
    name = (body.name or row["title"]).strip()[:100] or row["title"][:100]
    slug = onboarding.admin.pick_slug(c, name)
    # The bot first, under the rules for any bot this person adds: who may create one, under whom, on which model and
    # computer.
    created = onboarding.admin.create_bot(c, who, M.BotDefinitionCreate(
        slug=slug, display_name=name, description=description[:2000], status="planned",
        repo="bot-" + slug, thread_mode="personal", reports_to=row["reports_to"] or None,
        shared=bool(card.get("shared")), template=card["template"], model=model, effort=effort,
        owners=[H.actor_id(who.actor)], runner_id=body.runner_id or None))
    if stand_in:
        onboarding._follow_default(c, slug)
        model = ""
    if card.get("session") == "task":
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.session','task') WHERE bot=?", (slug,))
    if row["team"]:
        c.execute("UPDATE bot_config SET team=? WHERE bot=?", (row["team"], slug))
    declared = onboarding._declared(c, slug)
    declared.update(template=card["template"], instructions=instructions, template_version=releases.version(), recruited_from=seat_id)
    onboarding._write_config(c, slug, declared)
    # Onboarding from the start: it learns, takes no work, and a person above it marks it complete (lifecycle.py).
    c.execute("UPDATE bot_config SET onboarding_state=? WHERE bot=?", (lifecycle.statuses.NEEDS_SETUP, slug))
    message_bots.link(c, who.actor, slug, who.actor)
    now = H.now()
    c.execute("UPDATE seats SET closed_at=?,closed_by=?,filled_by=?,updated=?,updated_by=? WHERE id=?",
              (now, who.actor, slug, now, who.actor, seat_id))
    # Seats that reported to this one move under the bot that fills it, rather than under a closed seat.
    c.execute("UPDATE seats SET reports_to=?,updated=?,updated_by=? WHERE reports_to=? AND closed_at IS NULL",
              (slug, now, who.actor, "seat:" + seat_id))
    end_draft(c, row, "The seat was filled")
    H.event(c, who.actor, "seat.filled", seat_id, {"bot": slug, "template": card["template"], "model": model})
    # Only now its repository: a starter's computer builds it from this definition (`materialize`), any other
    # template's BotOps on its "Set up" task.
    setup_task = None
    if card["starter"]:
        onboarding._make_starter(c, who, slug, card["template"])
    else:
        choice = {"template": card["template"], "display_name": name, "instructions": instructions,
                  "description": description}
        setup_task = onboarding._setup_task(c, who, slug, choice, rendered, record["answers"])
    return {"slug": slug, "name": name, "seat": seat_id, "stage": lifecycle.ONBOARDING, "reports_to": row["reports_to"],
            "template": card["template"], "model": model, "setup_task_id": setup_task,
            "note": (created or {}).get("note") if isinstance(created, dict) else None}
