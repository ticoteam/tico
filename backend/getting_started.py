"""After the wizard: the Getting started checklist, the tour and the market research request.

The checklist is computed from what the database says right now (a runner's last heartbeat, a
bot's row, a finished task), so a step can never be ticked by hand and can never stay ticked
after the thing it names goes away. What a person may do by hand is limited to their own
choices: skipping an optional step, closing the tour, hiding the checklist.
Those live in the person's `preferences` row, so each person has their own.
"""

import json

from . import model_login, providers
from .statuses import PARKED_SQL
from .store import H, Problem, encode, readiness_document

PREFERENCE = "onboarding.progress"
BOTOPS = "botops"
LIBRARIAN = "librarian"
MARKET_TASK = "Set up the market map"
ONLINE_SECONDS = 120
# The checklist items a person may skip.
OPTIONAL = ("github",)


def _person(who):
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "Getting started is for people, not machines or bots", 403)


def _owner(who, what):
    if who.role != "owner":
        raise Problem("forbidden", "Only the owner " + what, 403)


def load_state(c, who):
    row = c.execute("SELECT value_json FROM preferences WHERE actor=? AND key=?",
                    (who.actor, PREFERENCE)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    return {"tour": bool(stored.get("tour")), "checklist": bool(stored.get("checklist")),
            "skipped": [k for k in stored.get("skipped") or [] if k in OPTIONAL]}


def save_state(c, who, state):
    c.execute("INSERT INTO preferences(actor,key,value_json,updated) VALUES(?,?,?,?) "
              "ON CONFLICT(actor,key) DO UPDATE SET value_json=excluded.value_json, updated=excluded.updated",
              (who.actor, PREFERENCE, encode(state), H.now()))


def change_state(c, who, body):
    _person(who)
    state = load_state(c, who)
    if body.tour is not None:
        state["tour"] = body.tour
    if body.checklist is not None:
        state["checklist"] = body.checklist
    if body.skip:
        if body.skip not in OPTIONAL:
            raise Problem("kind", "Only optional steps can be skipped: " + ", ".join(OPTIONAL), 422)
        state["skipped"] = sorted({*state["skipped"], body.skip})
    save_state(c, who, state)
    return state


# ------------------------------------------------------------------ live checks
def _online_runners(c):
    since = H.shift(H.now(), seconds=-ONLINE_SECONDS)
    return c.execute("SELECT id,label,readiness_json FROM runners WHERE revoked_at IS NULL "
                     "AND last_seen>? ORDER BY last_seen DESC", (since,)).fetchall()


_wanted_runtimes = providers.wanted_runtimes


def _signed_in_runtime(runners, wanted):
    for runner in runners:
        for name, row in (readiness_document(runner["readiness_json"]).get("runtimes") or {}).items():
            if name in wanted and row.get("installed") and row.get("authenticated") == "ready":
                return name
    return ""


def _login_target(runners, wanted):
    """A computer that has a wanted runtime installed but not signed in, and that the browser
    can sign in (backend/model_login.py): where the checklist's Sign in button goes."""
    for runner in runners:
        for name, row in sorted((readiness_document(runner["readiness_json"]).get("runtimes") or {}).items()):
            if name in wanted and name in model_login.RUNTIMES and row.get("installed") \
                    and row.get("authenticated") != "ready":
                return {"runner_id": runner["id"], "runtime": name, "machine": runner["label"]}
    return None


def own_bots(c, settings):
    """Bots the company added, as opposed to the four built in."""
    bootstrap = {settings.assistant_bot, BOTOPS, "librarian", "goal-manager"}
    return [row["slug"] for row in c.execute("SELECT slug FROM bots WHERE state!='archived' ORDER BY slug")
            if row["slug"] not in bootstrap]


def _next_bot(c, settings):
    """The starter bot to set up next: parked (`needs_setup`), in the order first run put them, which
    puts the team's first bot first. Returns (slug, name, why), or None when nothing is waiting."""
    rows = c.execute("SELECT bc.bot,b.display_name,bc.config_json FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                     "WHERE bc.onboarding_state IN " + PARKED_SQL + " AND b.state!='archived'").fetchall()
    if not rows:
        return None
    ranked = sorted(((json.loads(row["config_json"] or "{}"), row) for row in rows),
                    key=lambda pair: (pair[0].get("setup_rank", 999), pair[1]["bot"]))
    row = ranked[0][1]
    why = "Press Start setup on its page and answer its questions; it drafts a first result for you to approve."
    return row["bot"], row["display_name"], why


def _first_output(c):
    """A starter bot has been onboarded: its setup is done and a person has seen its first
    result. The bot says so itself (`hub bot setup-done`), so this is what the database records."""
    return c.execute("SELECT bc.bot FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                     "WHERE bc.onboarding_state='onboarded' AND b.state!='archived' LIMIT 1").fetchone()


def _item(key, label, done, why, href="", tab="", optional=False, action="", login=None):
    return {"id": key, "label": label, "done": bool(done), "optional": optional,
            "why": "" if done else why, "href": href, "tab": tab, "action": action,
            **({"login": login} if login and not done else {})}


def checklist(c, who, settings, github):
    record = providers.load(c, settings)
    runners = _online_runners(c)
    wanted = _wanted_runtimes(record)
    signed = _signed_in_runtime(runners, wanted)
    login = (providers.PROVIDER_BY_RUNTIME.get(next(iter(sorted(wanted)), ""), {}) or {}).get("login", "")
    botops = H.bot(c, BOTOPS)
    mine = own_bots(c, settings)
    building = c.execute("SELECT id FROM tasks WHERE owner=? AND title LIKE 'Build a bot:%' "
                         "AND status NOT IN ('done','closed','declined') ORDER BY created DESC LIMIT 1",
                         ("bot:" + BOTOPS,)).fetchone()
    installed = bool(github and github["installation_id"])
    waiting = _next_bot(c, settings)
    items = [
        _item("signed_in", "Signed in", True, ""),
        _item("computer", "A computer is online", runners,
              "Bots run on a computer you set up. Add one and keep it awake.", "#/settings", "devices"),
        _item("model", "A model is signed in on it", signed,
              ("Sign in to a model on the computer" + (" (run `" + login + "`)" if login else "") + "."
               if wanted else "Choose your AI providers first."),
              "#/settings", "devices" if wanted else "providers", login=_login_target(runners, wanted)),
        _item("github", "GitHub is connected", installed,
              "Lets bots keep their work in your GitHub.", "#/settings", "cloud", optional=True),
        _item("botops", "BotOps is active", botops and botops["state"] == "active",
              "BotOps builds your other bots. It starts once a computer hosts it.", "#/bot/" + BOTOPS),
        _item("first_bot", "Create your first bot", mine,
              "BotOps is building it." if building else "Say what it should do and BotOps builds it.",
              "#/task/" + building["id"] if building else "", action="" if building else "create-bot"),
        _item("next_bot", "Set up " + waiting[1] if waiting else "No bot is waiting for setup", not waiting,
              waiting[2] if waiting else "", "#/bot/" + waiting[0] if waiting else ""),
        _item("first_output", "First approved output", _first_output(c),
              "Set up a starter bot and approve the first thing it drafts. That is the point of the team.",
              "#/bot/" + waiting[0] if waiting else "#/tasks"),
        _item("first_update", "Your first update arrived",
              c.execute("SELECT 1 FROM updates LIMIT 1").fetchone(),
              "Each active bot posts a short update every day.", "#/updates"),
    ]
    return items


# What each kind of person needs to see: the owner all of it, bot administrators what involves
# bots, everyone else only what they can act on or read.
AUDIENCE = {"owner": None,
            "admin": {"signed_in", "botops", "first_bot", "next_bot", "first_output", "first_update"},
            "human": {"signed_in", "first_output", "first_update"}}


def view(c, who, settings, auth, github=None):
    _person(who)
    state = load_state(c, who)
    kind = "owner" if who.role == "owner" else "admin" if auth.bot_admin(who) else "human"
    allowed = AUDIENCE[kind]
    items = [i for i in checklist(c, who, settings, github) if allowed is None or i["id"] in allowed]
    for item in items:
        item["skipped"] = item["optional"] and not item["done"] and item["id"] in state["skipped"]
    settled = [i for i in items if i["done"] or i["skipped"]]
    return {"items": items, "done": len(settled), "total": len(items),
            "complete": len(settled) == len(items), "dismissed": state["checklist"],
            "tour_seen": state["tour"],
            "can_build": auth.bot_admin(who), "owner": who.role == "owner"}


# ------------------------------------------------------------------ actions
def _task(c, auth, who, owner, title, body):
    from . import rooms
    if owner == BOTOPS:
        from .botops_act import request_task
        return request_task(c, auth, who, title, body)
    return H.task_create(c, who.actor, title, body, "bot:" + owner, allow_planned=True,
                         conversation_id=rooms.task_conversation_id(c, auth, "bot:" + owner, who.actor))


def _active(c, slug):
    bot = H.bot(c, slug)
    return bot if bot and bot["state"] == "active" else None


def _botops(c):
    if not _active(c, BOTOPS):
        raise Problem("botops", "BotOps is not active yet. It starts once a computer hosts it.", 409)


def _market_title(c, owner, actor):
    """A title this request can still open: the hub refuses a second live task with the same one."""
    marks = ",".join("?" * len(H.LIVE_STATUSES))
    for n in range(1, 20):
        title = MARKET_TASK if n == 1 else f"{MARKET_TASK} ({n})"
        if not c.execute(f"SELECT 1 FROM tasks WHERE requester=? AND owner=? AND title=? AND status IN ({marks})",
                         (actor, owner, title, *H.LIVE_STATUSES)).fetchone():
            return title
    return f"{MARKET_TASK} ({n})"


def market_context(c, auth, who, body):
    """What the owner gave us about their market (a website, a description, links) goes to the
    Librarian as one task. Its playbook (templates/catalog/librarian/playbooks/market-setup.md)
    researches it and writes the market pages and graph; the page polls for them."""
    _owner(who, "sets up the market")
    if not _active(c, LIBRARIAN):
        raise Problem("librarian", "The Librarian is not running yet. It starts once a computer hosts it.", 409)
    text = "\n".join([
        "Build our market map from what the owner gave us. Follow playbooks/market-setup.md.", "",
        "The owner's words: a website, a description, links, or all three.", "", body.text.strip()])
    task = _task(c, auth, who, LIBRARIAN, _market_title(c, "bot:" + LIBRARIAN, who.actor), text)
    H.event(c, who.actor, "getting_started.market_requested", LIBRARIAN, {"task": task["id"]})
    return {"task_id": task["id"], "bot": LIBRARIAN}


def install(app, store, auth, mutate, settings):
    from fastapi import Request

    from . import models as M

    @app.get("/api/v2/getting-started")
    def read(request: Request):
        who = request.state.identity
        _person(who)
        github = getattr(request.app.state, "github_app", None)
        with store.read() as c:
            return view(c, who, settings, auth, github.row(c) if github else None)

    @app.post("/api/v2/getting-started/state")
    def state(request: Request, body: M.GettingStartedState):
        who = request.state.identity
        return mutate(request, body, lambda c: change_state(c, who, body))

    @app.post("/api/v2/getting-started/market")
    def market(request: Request, body: M.GettingStartedMarket):
        who = request.state.identity
        _person(who)
        return mutate(request, body, lambda c: market_context(c, auth, who, body))
