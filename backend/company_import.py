"""Apply a setup-page company file through the installed app's ordinary onboarding.

The file describes a team, never an identity or permission grant. The same adapter
serves the owner's file upload and the installer. Its caller owns the transaction.
"""
import hashlib
from pydantic import ValidationError

from . import company_file as C
from . import models as M
from . import onboarding as O
from .company_file import draft as D
from .company_file import starter
from .company_file.yamlfile import tool_list
from .store import H, Problem, encode

PLAN_KEY = "onboarding.company_file"


def plan(onboarding, c, who, text):
    """Validate and translate without changing the company or trusting file owner IDs."""
    onboarding._owner(who)
    try:
        saved = starter.load(text)
    except C.DraftError as exc:
        raise Problem("company_file", str(exc), 422) from None
    draft = saved
    owner = onboarding.auth.owner_id(c)
    if not owner or not H.human(c, owner):
        raise Problem("company_file", "Sign in as this company's owner before importing its starter team", 409)
    cards = O.read_cards(onboarding.settings)
    by_name = {name: card for card in cards for name in (card["template"], card["slug"])}
    required = {onboarding._bot_slug(card["slug"]): card["template"] for card in cards if card["required"]}
    ids, picked = {}, {}
    # A seat with no template yet, or one a person holds, makes no bot: the chart keeps it as a seat.
    seats = [bot for bot in draft["bots"] if bot["template"] in D.OPEN_SEATS or bot.get("person")]
    for bot in draft["bots"]:
        if bot in seats:
            continue
        card = by_name.get(bot["template"])
        if card is None:
            raise Problem("company_file", "An employee's template is not installed in this Tico catalog", 422)
        slug = onboarding._bot_slug(card["slug"] if card["required"] else bot["id"])
        if slug in picked or slug in required and required[slug] != card["template"]:
            raise Problem("company_file", "An employee ID conflicts with another employee or a built-in role", 422)
        ids[bot["id"]] = slug
        picked[slug] = (bot, card)
    home = "human:" + owner
    owner_id = D.owner_id(draft)
    selected, later = {}, []
    try:
        names = M.OnboardingNames(company_name=draft["company"]["name"],
                                  owner_name=(draft["people"][0]["name"] if draft["people"] else ""))
        display = O.display_names(onboarding.settings, {"names": names.model_dump()})
        for slug, (bot, card) in picked.items():
            parent = D.manager_of(draft, bot)
            # Under a seat that makes no bot (the Controller, until its template exists), the bot reports one step up.
            skipped = {seat["id"]: seat for seat in seats}
            while parent in skipped:
                parent = D.manager_of(draft, skipped.pop(parent))
            parent = home if parent == owner_id else ids.get(parent)
            if parent is None or parent == slug:
                raise Problem("company_file", "An employee's reporting line cannot be applied", 422)
            instructions = O.render(card, display, bot["title"])["instructions"]
            if bot["reason"]:
                instructions += "\n\nWhy this role is on the company chart:\n" + bot["reason"]
            selected[slug] = M.OnboardingSelection(template=card["template"], display_name=bot["title"],
                                                   instructions=instructions, reports_to=parent)
            if not bot["start"] and not card["required"]:
                later.append(slug)
        body = M.OnboardingDraft(names=names, answers=M.OnboardingAnswers(what_we_do=draft["company"]["about"]),
                                 selected=selected)
    except ValidationError:
        raise Problem("company_file", "A company name, title or instruction is too long for this Tico setup", 422) from None
    fingerprint = hashlib.sha256(encode(saved).encode()).hexdigest()
    deferred = []
    if "models" in saved:
        deferred.append("model_choices")
    if seats:
        deferred.append("open_seats")
    if draft["goals"]:
        deferred.append("goals")
    if tool_list(draft):
        deferred.append("tool_connections")
    if any(b.get("first_job") for b in draft["bots"]):
        deferred.append("first_jobs")
    if any(b.get("routine") for b in draft["bots"]):
        deferred.append("routines")
    summary = {"fingerprint": fingerprint, "company_name": names.company_name,
               "employees": [{"id": slug, "template": choice.template, "title": choice.display_name,
                              "reports_to": choice.reports_to, "start": "later" if slug in later else "now"}
                             for slug, choice in selected.items()],
               "builtins": sorted(required), "deferred": deferred}
    return body, saved, later, summary


def preview(onboarding, c, who, text):
    return plan(onboarding, c, who, text)[3]


def apply(onboarding, c, who, text, fingerprint):
    """Apply a confirmed plan once; retries never rebuild or replace a configured team."""
    body, saved, later, summary = plan(onboarding, c, who, text)
    if fingerprint != summary["fingerprint"]:
        raise Problem("company_file_changed", "The company file changed; preview it again before importing", 409)
    record = O.load(c)
    previous = record.get("company_import") or {}
    if previous.get("fingerprint") == fingerprint:
        return onboarding.view(c, who)
    builtins = set(summary["builtins"])
    existing = [r[0] for r in c.execute("SELECT slug FROM bots") if r[0] not in builtins]
    if record["completed"] or existing:
        raise Problem("company_already_setup", "This company is already set up; import cannot replace its team", 409)
    # Create definitions under the owner before installing the desired graph.
    # Otherwise complete() can see a temporary cycle through the old seed while
    # it resolves a newly-created parent. Neither staging nor a partial chart is
    # visible outside this transaction; the confirmed choices are restored below.
    home = "human:" + onboarding.auth.owner_id(c)
    staging = body.model_copy(update={"selected": {
        slug: choice.model_copy(update={"reports_to": home}) for slug, choice in body.selected.items()}})
    onboarding.save(c, who, staging)
    onboarding.complete(c, who)
    # complete() preserves existing definitions, including the seeded built-ins'
    # managers. Apply the confirmed chart after all targets exist, parents first
    # so replacing the old seed hierarchy cannot create a temporary cycle.
    def parent_rank(slug):
        parent = body.selected[slug].reports_to
        return 1 + parent_rank(parent) if parent in body.selected else 0

    for slug in sorted(body.selected, key=parent_rank):
        row = c.execute("SELECT reports_to,revision FROM bot_config WHERE bot=?", (slug,)).fetchone()
        parent = body.selected[slug].reports_to
        if row["reports_to"] != parent:
            onboarding.admin.update_bot(c, who, slug, M.BotDefinitionUpdate(reports_to=parent, expected_revision=row["revision"]))
    for slug in later:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot=?", (slug,)).fetchone()[0]
        onboarding.admin.update_bot(c, who, slug, M.BotDefinitionUpdate(status="paused", expected_revision=revision))
    record = O.load(c)
    record["selected"] = {slug: choice.model_dump() for slug, choice in body.selected.items()}
    record["company_import"] = {"fingerprint": fingerprint, "imported_at": H.now(), "by": who.actor,
                                "deferred": summary["deferred"], "later": later}
    onboarding._store(c, record, who.actor)
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (PLAN_KEY, encode(saved)))
    H.event(c, who.actor, "onboarding.company_imported", "", {"fingerprint": fingerprint,
                                                              "bots": sorted(body.selected), "later": later})
    return onboarding.view(c, who)


def load_on_start(onboarding, c):
    """The installer-configured plan is applied before any generic sample company.

    A changed plan never replaces a configured company. Errors stop startup so a
    failed import cannot look like a successful installation of a different team.
    """
    draft = starter.unpack(onboarding.settings.company_file_data)
    if (O.load(c).get("company_import") or {}).get("fingerprint") == hashlib.sha256(encode(draft).encode()).hexdigest():
        return None
    who = onboarding.auth.owner_identity(c)
    summary = preview(onboarding, c, who, draft)
    return apply(onboarding, c, who, draft, summary["fingerprint"])
