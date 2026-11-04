"""Tico's sample company: a made-up five-bot team a new company can start as (docs/sample-company.md).

The team is backend/company_file/sample_company.yaml, a company file (format 0.2) with two fields only this loader
reads: each employee's `runs_on`, the subscription its main work uses, and its `starter_tasks`. Loading it is the
first run done for the owner: the sample's names and answers are saved, its four role bots are picked and created
the way the first-run wizard creates them (Onboarding.complete), each with its own model so the work is spread over
the Claude and ChatGPT subscriptions, and then, unlike a wizard pick, each is marked set up: the sample's answers
stand in for the setup chat, so a starter task is claimed as soon as a computer runs the bot. A bot's first routine
stays off until a person turns it on. Each bot gets its goal, and the owner files its starter tasks.

It is not demo mode (backend/demo.py): these are real bots on the person's own subscriptions. Nothing a starter task
asks for leaves the company. Each ends with one Yes/No question to the owner (`hub task question`), which is the card
in Needs you; Tico's approvals are for a send, spending, publishing or a merge, and a starter task does none of them.

Two doors: the first-run welcome's "Start with a sample company" (POST /api/v2/setup/sample, the owner), and
TICO_SAMPLE_COMPANY=on, which loads it on the first start whose owner is on the roster. Both leave a company that is
already set up alone, and both keep a team name already set (the recommended install sets TICO_TEAM_NAME); the
sample's own name, Sample Co, is used only when there is none, and its tasks and goals say whichever it is.
"""
from pathlib import Path

import yaml

from . import goals as G
from . import onboarding as O
from . import providers
from .company_file import from_yaml
from .config import DEFAULT_COMPANY_NAME
from .store import H, Problem

FILE = Path(__file__).resolve().parent / "company_file" / "sample_company.yaml"
# The subscription a bot's main work runs on, and the provider that serves it. Both are turned on when the company
# has chosen no provider yet; a company that has keeps its choice, and a bot whose provider it left off follows the
# company's default model.
RUNS_ON = {"claude": "anthropic", "codex": "openai"}
ASK = """

## Ask for the approval
When {what} is on this task, ask the owner once, from this task:

    hub task question --on {owner} "Approve {what}?"

Their answer reaches you on this task. Yes: mark this task done. No: change it as they say and ask once more.
Nothing in this task is sent, posted or published outside Sample Co, whatever they answer.
"""


def team():
    """The sample company file: its draft (company_file.from_yaml, so it stays a file Tico can import) and, by bot
    id, what only the sample uses: `runs_on`, and the starter tasks."""
    text = FILE.read_text()
    extra = {row["id"]: {"runs_on": row.get("runs_on") or "", "tasks": row.get("starter_tasks") or []}
             for row in yaml.safe_load(text)["employees"]}
    return from_yaml(text), extra


def fresh(c, record):
    """A company that has not been set up: the first run is not finished and no bot is active (onboarding.needed)."""
    return not record["completed"] and not c.execute("SELECT 1 FROM bots WHERE state='active' LIMIT 1").fetchone()


def load_on_start(onboarding, c):
    """TICO_SAMPLE_COMPANY=on on a server's start: a new company becomes the sample company, once. Nothing happens to a
    company that is set up, or while the owner is not on the roster yet (a later start loads it)."""
    if not onboarding.settings.sample_company or not fresh(c, O.load(c)):
        return None
    try:
        who = onboarding.auth.owner_identity(c)
    except Problem:
        return None
    return load(onboarding, c, who)


def load(onboarding, c, who):
    """Make this company the sample company: names, answers, bots, goals and starter tasks, in the caller's
    transaction. Refused once the company is set up. Returns what the welcome page reads (Onboarding.view)."""
    settings = onboarding.settings
    record = O.load(c)
    if not fresh(c, record):
        raise Problem("sample_company", "This company is already set up, so it can't start as the sample company", 409)
    draft, extra = team()
    _providers(c, settings, who)
    cards = {card["slug"]: card for card in O.read_cards(settings)}
    assistant = next(b for b in draft["bots"] if cards[b["template"]]["required"])
    slugs = {b["id"]: settings.assistant_bot if b is assistant else b["id"] for b in draft["bots"]}
    company = draft["company"]
    name = team_name(settings, record) or company["name"]
    ours = lambda text: text.replace(company["name"], name)
    record["names"] = {**record["names"], "company_name": name}
    record["answers"] = {**record["answers"], "what_we_do": ours(company["about"]), "customers": "businesses",
                         "team_size": "1 (just me)", "software_product": "yes",
                         "departments": [b["dept"] for b in draft["bots"] if b is not assistant]}
    names = O.display_names(settings, record)
    # The assistant is a required card: complete() makes it (or keeps the one there is) under the company's own name.
    record["selected"] = {
        slugs[b["id"]]: {"template": cards[b["template"]]["template"], "display_name": b["title"],
                         "instructions": O.render(cards[b["template"]], names, b["title"])["instructions"],
                         "reports_to": slugs.get(b.get("reports_to"), "")}
        for b in draft["bots"] if b is not assistant}
    onboarding._store(c, record, who.actor)
    chosen = providers.load(c, settings)["enabled"]
    models = {slugs[bot_id]: providers.recommended(RUNS_ON[row["runs_on"]])["id"]
              for bot_id, row in extra.items() if RUNS_ON.get(row["runs_on"]) in chosen}
    onboarding.complete(c, who, models=models, seats=False)   # the sample is a working team, not a chart to recruit into
    for slug in record["selected"]:
        onboarding.onboarded(c, who, slug, arm=False)
    owner = H.actor_id(who.actor)
    # The bots exist now: their ids (new bots get their own, not their slug) are the actors that own goals and tasks.
    ids = {slug: (H.resolve_bot(c, slug) or {}).get("id", slug) for slug in set(slugs.values())}
    goals = {}
    for goal in draft["goals"]:
        made = G.create(c, who.actor, ours(goal["title"]), "bot:" + ids[slugs[goal["owner"]]], body=name + "'s goal.")
        goals.setdefault(goal["owner"], made["id"])
    tasks = []
    for bot_id, row in extra.items():
        for task in row["tasks"]:
            body = ours(task["body"].rstrip() + ASK.format(what=task["approve"], owner=owner))
            made = H.task_create(c, who.actor, ours(task["title"]), body, "bot:" + ids[slugs[bot_id]], allow_planned=True,
                                 goal_id=goals.get(bot_id))
            tasks.append(made["id"])
    record = O.load(c)
    setup = {slugs[b["id"]]: state(name, b, H.now()[:10]) for b in draft["bots"] if b is not assistant}
    record["sample"] = {"loaded": H.now(), "by": who.actor, "bots": sorted(slugs.values()), "tasks": tasks,
                        "setup": setup}
    onboarding._store(c, record, who.actor)
    H.event(c, who.actor, "onboarding.sample_loaded", "", {"bots": sorted(slugs.values()), "tasks": len(tasks)})
    return onboarding.view(c, who)


def state(name, bot, today):
    """The state.md sections a role bot's repository is made with (clients/catalog.materialize): its setup done from
    the sample's answers, so it works its starter task instead of asking the setup questions, and its routine off."""
    return {
        "Setup": (f"Done {today}. You are the {bot['title']} at {name}, Tico's sample company: a made-up team for "
                  "trying Tico. The sample's own answers stood in for the setup questions, so do not ask them."),
        "Answers": f"- {today}: {bot['reason']}\n- {today}: The company is in `knowledge/company.md`.",
        "Routine": ("Your first routine is off. The sample company keeps it off until a person turns it on in Tico; "
                    "do not turn it on yourself."),
        "Next": "Do your starter task, then ask the owner the one Yes/No question it ends with. Then work your open tasks.",
    }


def team_name(settings, record):
    """The team name already set, which the sample keeps: one saved in the welcome, else TICO_TEAM_NAME (config's
    "Company" is the name of none)."""
    saved = str(record["names"].get("company_name") or "").strip()
    configured = (settings.company_name or "").strip()
    return saved or (configured if configured != DEFAULT_COMPANY_NAME else "")


def _providers(c, settings, who):
    """A company with no AI provider chosen gets the two the sample runs on, Claude first (its default)."""
    before = providers.load(c, settings)
    if providers.configured(before):
        return
    before, after = providers.save(c, who.actor, {"enabled": ["anthropic", "openai"],
                                                  "expected_revision": before["revision"]}, H.now())
    keys = ("enabled", "runtime", "model", "revision", "auto_fallback", "check_models")
    H.event(c, who.actor, "providers.updated", "", {"before": {k: before[k] for k in keys},
                                                     "after": {k: after[k] for k in keys}, "sample": True})
