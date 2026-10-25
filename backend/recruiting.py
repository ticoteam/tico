"""Recruiting a seat (docs/onboarding.md, "Recruiting a seat"; bot-lifecycle-spec.md section 2, acceptance 6 to 9).

A Recruiting seat (backend/lifecycle.py) is filled in three steps, the same from the org chart and from Team > Add bot,
which creates the seat first:

1. **Open it.** The owner opening a seat asks the CEO Bot to draft its job description (`open_seat`): one task for the
   CEO Bot, which answers with `hub seat draft` (`draft`). Until then, and for everyone else, the seat shows the text it
   has: the chart model's, or what a person wrote. The draft never replaces a person's words: once someone writes the
   description themselves, nothing is drafted over it. Anyone who may change the seat (lifecycle `_may_place`) edits it.
2. **Recruit.** `candidates` answers at once, on this server, from the template catalog: up to three Tico templates,
   each with a one-line reason and a suggested model, exactly one of them recommended, and the models one may choose
   instead. No trial task and no outside service (spec section 6 keeps those for later).
3. **Pick.** `hire` fills the seat: the bot is created in Onboarding, in the seat's place on the chart with its
   reporting line, under the same rules as any bot the person adds (backend/settings_admin.py `create_bot`). Its
   repository comes only after that: a starter's computer builds it from the bot's definition, any other template's
   BotOps does on the "Set up" task it gets now. The seat is then closed, filled by that bot.
"""
from . import ceo_bot
from . import hubdb as H
from . import lifecycle
from . import models as M
from . import releases
from . import recruit_rank as R
from .store import Problem

CEO = "bot:" + ceo_bot.SLUG
CANDIDATES = 3
DRAFT_MAX = lifecycle.DESCRIPTION_MAX
CHART = "chart"                         # `description_by` for a chart model's text: drafted over like no text at all


# ------------------------------------------------------------------ the seat as the recruiting page shows it
def _person_wrote(row):
    by = str(row["description_by"] or "")
    return by.startswith("human:") or by == lifecycle.EARLIER


def _live_task(c, task_id):
    row = H.task(c, task_id) if task_id else None
    return row if row and row["status"] in H.ACTIVE_STATUSES else None


def draft_state(c, row):
    """`drafted` once the CEO Bot's draft is the description, `drafting` while its task is open, else `none`."""
    if row["drafted_at"] and row["description_by"] == CEO:
        return "drafted"
    return "drafting" if _live_task(c, row["draft_task"]) else "none"


def _may_edit(auth, c, who, row):
    try:
        lifecycle._may_place(auth, c, who, row["reports_to"])
        return True
    except Problem:
        return False


def view(auth, c, who, row):
    seat = lifecycle._seat(row)
    by = str(row["description_by"] or "")
    seat.update(draft={"state": draft_state(c, row), "by": ceo_bot.name(c), "at": row["drafted_at"] or ""},
                description_by="ceo_bot" if by == CEO else "chart" if by in ("", CHART) else "person",
                can_edit=_may_edit(auth, c, who, row))
    return seat


def show(auth, c, who, seat_id):
    return view(auth, c, who, lifecycle.seat(c, seat_id))


# ------------------------------------------------------------------ 1. the CEO Bot's draft
def _ceo_ready(c):
    bot = H.bot(c, ceo_bot.SLUG)
    return bool(bot) and bot.get("state") not in ("archived", "paused")


def _draft_body(c, row):
    reports = str(row["reports_to"] or "")
    boss = ((H.human(c, reports[6:]) or {}).get("name") or reports[6:]) if reports.startswith("human:") else (
        (H.bot(c, reports) or {}).get("display_name") or reports)
    current = str(row["description"] or "").strip()
    return "\n".join([
        f'The owner opened the Recruiting seat "{row["title"]}" on the org chart. Draft its job description for them.',
        "",
        f"- seat: {row['id']}",
        f"- role: {row['title']}",
        f"- reports to: {boss}",
        "",
        "Write what this role owns, what good work looks like and who it works with, in plain words a candidate "
        "would read: 80 to 250 words, no headings beyond a short list. Use what you know about the company; don't "
        "name a template, a model or an outside service.",
        "",
        f"Save it with `hub seat draft {row['id']} --text \"...\"` (or `--file <path>`). That is the whole task: "
        "it marks this task done. If someone writes the description themselves first, Tico keeps theirs and tells you.",
        *(["", "The text the seat has now (from the org chart model):", "", current] if current else []),
    ])


def open_seat(auth, c, who, seat_id):
    """The seat as its page shows it. The owner opening it, in person, asks the CEO Bot for a draft when no person has
    written the description and none is drafted or being drafted; nobody else's open asks the CEO Bot anything (it
    works for the owner alone)."""
    row = lifecycle.seat(c, seat_id)
    if (who.role == "owner" and not who.via and not _person_wrote(row) and draft_state(c, row) == "none"
            and _ceo_ready(c)):
        task = H.task_create(c, CEO, "Draft the job description: " + row["title"][:200], _draft_body(c, row), CEO,
                             lane="company", lint=False, deduplicate=False)
        c.execute("UPDATE seats SET draft_task=? WHERE id=?", (task["id"], seat_id))
        H.event(c, who.actor, "seat.draft_requested", seat_id, {"task": task["id"]})
        row = lifecycle.seat(c, seat_id)
    return view(auth, c, who, row)


def draft(auth, c, who, seat_id, text):
    """The CEO Bot's draft (`hub seat draft`): the seat's job description, unless a person wrote it since the owner
    opened the seat. Only the CEO Bot, on its own credential, while its draft task is open."""
    if who.role != "bot" or who.actor != CEO or who.via:
        raise Problem("forbidden", f"Only the {ceo_bot.name(c)} drafts a seat's job description", 403)
    row = lifecycle.seat(c, seat_id)
    if _person_wrote(row):
        raise Problem("draft", "Someone wrote this seat's job description themselves, so theirs stays; nothing to do",
                      409)
    task = _live_task(c, row["draft_task"])
    if not task:
        raise Problem("draft", "Nobody asked for a draft of this seat's job description; it is drafted when the owner "
                      "opens the seat", 409)
    text = str(text or "").strip()[:DRAFT_MAX]
    if len(text) < 20:
        raise Problem("draft", "Write the job description: a few sentences on what the role owns", 422)
    now = H.now()
    c.execute("UPDATE seats SET description=?,description_by=?,drafted_at=?,updated=?,updated_by=? WHERE id=?",
              (text, CEO, now, now, CEO, seat_id))
    H.task_update(c, CEO, task["id"], status="done", note="Drafted the job description for " + row["title"][:200])
    H.event(c, CEO, "seat.drafted", seat_id, {"task": task["id"]})
    return view(auth, c, who, lifecycle.seat(c, seat_id))


def end_draft(c, row, why):
    """Close the CEO Bot's open draft task when there is nothing left to draft: the seat was filled or closed, or a
    person wrote the description."""
    task = _live_task(c, row["draft_task"])
    if task:
        H.task_update(c, CEO, task["id"], status="declined", note=why[:500])


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


def _reason(card, hits, title, departments):
    """One line on why this template fits: the job description's own words it matches, else the role title's, else
    the first sentence of what the template does."""
    if hits:
        return ("Matches “" + "”, “".join(hits[:2]) + "” in the job description")[:R.WHY_LIMIT]
    shared = [word for stem, word in R._words(title) if stem in R._stems(card["name"])]
    if shared:
        home = departments.get(card["department"], "")
        return (card["name"] + " fits the role “" + title + "”" + (" in " + home if home else ""))[:R.WHY_LIMIT]
    return R._first_sentence(card["summary"], 100) or "A general-purpose Tico template"


def candidates(auth, onboarding, c, who, seat_id):
    """Recruit: up to three Tico templates for the seat, the best fit to its title and job description first and
    recommended, each with a one-line reason and a suggested model. Deterministic and local: the same seat gives the same
    list, and nothing leaves this server."""
    row = lifecycle.seat(c, seat_id)
    _may_hire(auth, c, who, row)
    value, cards = _recruitable(onboarding)
    departments = {d["id"]: d["name"] for d in value["departments"]}
    title = [(stem, word) for stem, word in R._words(row["title"])]
    words = title + [("", "")] + R._words(R._text(row["description"], 4000))
    common = R._common(cards)
    scored = []
    for card in cards:
        score, hits = R._match(card, words, common)
        named, _ = R._match(card, title, common)
        score += 2 * named + 3 * len(R._stems(card["name"]) & R._stems(row["title"]))
        scored.append((-score, {"default": 0, "common": 1}.get(card["suggest"], 2), card["name"].lower(), card, hits))
    scored.sort(key=lambda item: item[:3])
    out = []
    for n, (_, _, _, card, hits) in enumerate(scored[:CANDIDATES]):
        out.append({"template": card["template"], "name": card["name"], "summary": R._first_sentence(card["summary"]),
                    "icon": card["icon"], "department": departments.get(card["department"], ""),
                    "reason": _reason(card, hits, row["title"], departments), "recommended": n == 0,
                    **_suggested(c, onboarding, card["raw"])})
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
    slug = onboarding.admin.pick_slug(c, row["title"])
    # The bot first, under the rules for any bot this person adds: who may create one, under whom, on which model.
    created = onboarding.admin.create_bot(c, who, M.BotDefinitionCreate(
        slug=slug, display_name=row["title"][:100], description=description[:2000], status="planned",
        repo="bot-" + slug, thread_mode="personal", reports_to=row["reports_to"] or None,
        shared=bool(card.get("shared")), template=card["template"], model=model, effort=effort,
        owners=[H.actor_id(who.actor)]))
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
        choice = {"template": card["template"], "display_name": row["title"][:100], "instructions": instructions,
                  "description": description}
        setup_task = onboarding._setup_task(c, who, slug, choice, rendered, record["answers"])
    return {"slug": slug, "seat": seat_id, "stage": lifecycle.ONBOARDING, "reports_to": row["reports_to"],
            "template": card["template"], "model": model, "setup_task_id": setup_task,
            "note": (created or {}).get("note") if isinstance(created, dict) else None}
