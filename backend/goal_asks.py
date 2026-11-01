"""What the Goals page needs from a person, as tasks the Strategy Bot asks (docs/goals-and-kpis.md, "Needs you").

Only above Passive (backend/strategy_bot.py `proactive`): at Passive, `sync` files nothing new and Needs you shows only
what was already asked. A task filed before stays until it is resolved, as below.

Three things wait on a person: a KPI off track (red) on a goal they own, a KPI they keep whose reading is out of date
(stale or missing), and a proposal to confirm or reject. Each is one task, requested by the Strategy Bot and owned by
the person who must act, so it shows in Tasks and Decide like any other ask. One per KPI or proposal, kind and person
(`goal_asks`), never two. `sync` files what is new and closes what is resolved: a fresh reading, a KPI back on track, a
proposal decided, or the thing now someone else's. A task the person closed or declined while the cause stands is left
alone, and asked again only once the cause has cleared and come back.

Two more wait on a person: the company's vision, while it is empty (one ask to the environment owner, first in their
list), and the Why lines the Strategy Bot proposed (one bundled ask per person who decides them, never one per goal).
Asks are filed most important first (`ORDER`). At level 2 and 3 a recovery the Strategy Bot proposes for a goal
(backend/strategy_suggest.py) is its own item, with its options, and takes the place of asking that goal's owner why a
KPI is off track.

A company goal or KPI is the environment owner's to act on. A bot's red or stale KPI is the bot's own work (the Strategy
Bot's pass asks it), so no task is filed for it; a proposal about a bot's goal or KPI goes to the nearest person above
the bot on the org chart, as only a person decides one."""

import logging

from .batch_work import isolated
from . import needs_shape, strategy_bot
from . import goals as G
from . import kpis as K
from . import strategy as S
from . import views
from .store import H

log = logging.getLogger("tico.goal_asks")

KINDS = ("vision", "kpi_red", "recovery", "why", "proposal", "kpi_stale")
ORDER = {kind: n for n, kind in enumerate(KINDS)}     # the vision first, then a goal off track, then the rest
WHY_REF = "goals"                                     # one bundled Why ask per person


def _org(conn):
    archived = {row["id"] for row in H.bots(conn) if row.get("state") == "archived"}
    return views.roster(conn), views.entries(conn), archived, True


def _person(conn, actor, owner_id, org):
    """The person who acts for an owner: a person themselves, the environment owner for `company`, and for a bot
    the nearest person above it (else the environment owner). None when nobody on the roster can."""
    if actor == G.COMPANY or not actor:
        return H.human_actor(owner_id) if owner_id else None
    if H.is_human(actor):
        return actor if H.human(conn, H.actor_id(actor)) else None
    for boss in G.above(actor, *org):
        if H.is_human(boss) and H.human(conn, H.actor_id(boss)):
            return boss
    return H.human_actor(owner_id) if owner_id else None


def _acts_for(actor, owner_id):
    """Who acts on a red or stale KPI: the owner, or the environment owner for a company one."""
    if actor == G.COMPANY:
        return H.human_actor(owner_id) if owner_id else None
    return actor


def readers(conn, auth):
    """`reads` for wanted and sync: whether a person may Read a bot's activity, its KPIs among it (backend/auth.py)."""
    return lambda person, owner: auth.person_can(conn, person, H.actor_id(owner), "read")


def _may_read(reads, person, owner):
    """A person's and the company's measures are the team's; a bot's only for a person who may Read the bot."""
    return not H.is_bot(owner) or bool(reads and reads(person, owner))


def wanted(conn, owner_id, at=None, reads=None):
    """Everything that waits on someone now: [{kind, ref, owner, ...the item the Goals page shows}]. `owner` is
    the actor who must act (a bot only for its own red or stale KPI). `owner_id` is the environment owner's
    roster id. `reads` (readers) says whose measures a person may read; without it no bot's measure is named."""
    org = None
    items = []
    strategy_proactive = strategy_bot.proactive(conn)      # above Passive: the vision ask and Why lines in Needs you
    # At Passive nothing new is asked, but an ask filed above it stays until its cause is resolved.
    filed = set() if strategy_proactive else {(r[0], r[1]) for r in conn.execute(
        "SELECT kind, owner FROM goal_asks WHERE kind IN ('vision','why')")}
    # The vision is the strategy's, and the owner is asked about their own draft (backend/strategy.py).
    if owner_id and not S.vision(conn, editor=True) and (
            strategy_proactive or ("vision", H.human_actor(owner_id)) in filed):
        items.append({"kind": "vision", "ref": "company", "owner": H.human_actor(owner_id)})
    live = G.goals(conn, live_only=True)
    for goal_id, rows in K.goal_views(conn, [g["id"] for g in live], at).items():
        goal = next(g for g in live if g["id"] == goal_id)
        who = _acts_for(goal["owner_id"], owner_id)
        for k in rows:
            if who and k["status"] == "red":
                items.append({"kind": "kpi_red", "ref": k["id"], "owner": who, "goal_id": goal_id,
                              "goal_title": goal["title"], "kpi_id": k["id"], "kpi_name": k["name"],
                              "reason": k["reason"]})
    owned = [dict(r) for r in conn.execute("SELECT * FROM kpis WHERE archived_at IS NULL ORDER BY created_at")]
    data = K.effective_many(conn, [r["id"] for r in owned])
    for record in owned:
        who = _acts_for(record["owner"], owner_id)
        if K.auto(record["id"]):
            continue
        state = K.assess(record, None, data.get(record["id"], []), at)
        if who and state["freshness"] != "fresh":
            items.append({"kind": "kpi_stale", "ref": record["id"], "owner": who, "kpi_id": record["id"],
                          "kpi_name": record["name"], "freshness": state["freshness"], "reason": state["reason"]})
    whys, recovering = {}, set()
    from . import strategy_suggest
    suggests = strategy_suggest.suggests(conn)
    for p in G.proposals(conn, "pending"):
        if p["kind"] == "recovery":
            # Only while it suggests (level 2 or 3); lowering the level withdraws them.
            goal = G.goal(conn, p["goal_id"]) if suggests and p["goal_id"] else None
            if goal:
                org = org or _org(conn)
                who = _person(conn, goal["owner_id"], owner_id, org)
                if who:
                    recovering.add((goal["id"], who))
                    items.append({"kind": "recovery", "ref": p["id"], "owner": who, "proposal": p,
                                  "goal_id": goal["id"], "goal_title": goal["title"],
                                  "options": strategy_suggest.options_view(conn, p),
                                  "ask": strategy_suggest.ask_text(conn, p, goal["title"])})
            continue
        if p["kind"] == "goal_why":
            # Never one task per goal: each person who decides Why lines is asked once for all of theirs.
            goal = G.goal(conn, p["goal_id"]) if (strategy_proactive or filed) and p["goal_id"] else None
            if goal and goal.get("bet_id"):
                continue            # a goal that serves a bet has its why: the bet
            if goal:
                org = org or _org(conn)
                who = _person(conn, goal["owner_id"], owner_id, org)
                if who and (strategy_proactive or ("why", who) in filed):
                    whys.setdefault(who, {})[goal["id"]] = {"goal_id": goal["id"], "goal_title": goal["title"],
                                                           "why": (p.get("payload") or {}).get("why") or ""}
            continue
        goal = G.goal(conn, p["goal_id"]) if p["goal_id"] else None
        record = K.kpi(conn, p["kpi_id"]) if p["kpi_id"] else None
        if p["kind"] == "kpi_definition":
            decides = record and record["owner"]
        else:
            decides = (goal and goal["owner_id"]) or (record and record["owner"])
        org = org or _org(conn)
        who = _person(conn, decides, owner_id, org)
        if who:
            # A private task still exposes its title and body to its owner. Do not file a proposal
            # naming a bot-owned goal or KPI for a person who cannot read that bot.
            if any(H.is_bot(r["owner"]) and not _may_read(reads, who, r["owner"])
                   for r in (goal, record) if r):
                continue
            item = {"kind": "proposal", "ref": p["id"], "owner": who, "proposal": p,
                    "goal_title": goal and goal["title"], "kpi_name": record and record["name"]}
            used = K.kpi(conn, (p.get("payload") or {}).get("kpi_id")) if p["kind"] == "goal_kpi" else None
            if used and not _may_read(reads, who, used["owner"]):
                continue            # they may not add a measure they may not read (goal-proposals decide)
            if used:
                item["measure_name"] = used["name"]     # an Add a KPI that reuses a measure names it
            # A bot's goal or measure named in the ask is its activity: the ask is the person's and the Strategy Bot's.
            if p["kind"] in GOAL_KINDS and any(H.is_bot(r["owner"]) for r in (goal, record, used) if r):
                item["private"] = True
            items.append(item)
    for who, goals in whys.items():
        items.append({"kind": "why", "ref": WHY_REF, "owner": who, "goals": list(goals.values())})
    # A goal with a recovery waiting is one item for its owner: the recovery, not also "check why it is off track".
    items = [i for i in items if not (i["kind"] == "kpi_red" and (i["goal_id"], i["owner"]) in recovering)]
    # A red KPI used by two of one person's goals is one ask.
    seen, out = set(), []
    for item in sorted(items, key=lambda i: ORDER[i["kind"]]):
        key = (item["kind"], item["ref"], item["owner"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _change(p, kpi_name):
    """The proposed change in a few words."""
    payload = p.get("payload") or {}
    if p["kind"] == "goal_wording":
        parts = [f'title "{payload["title"]}"' if payload.get("title") else "", "a new description" if payload.get("body") else ""]
        return "new " + " and ".join(x for x in parts if x)
    if p["kind"] == "kpi_definition":
        return "change " + ", ".join(f"{k.replace('_', ' ')} to {v!r}" for k, v in K.clean(payload).items())
    if p["kind"] == "kpi_target":
        return "target " + (K.target_label({"unit": ""}, payload) or payload.get("kind") or "none")
    if p["kind"] == "goal_kpi":
        target = K.target_label({"unit": (payload.get("kpi") or {}).get("unit") or ""}, payload.get("target"))
        return f"measure it by {kpi_name}" + (f", target {target}" if target else ", no target yet")
    if p["kind"] == "flag":
        return f"flagged as {payload.get('issue')}"
    if p["kind"] == "strategy_draft":
        parts = [k for k in ("summary", "vision", "sections") if payload.get(k)]
        if payload.get("bets"):
            parts.append(f"{len(payload['bets'])} bet{'s' if len(payload['bets']) != 1 else ''}")
        return "change the strategy draft: " + ", ".join(parts)
    if p["kind"] in ("brand_draft", "gtm_draft"):
        page = "brand" if p["kind"] == "brand_draft" else "go-to-market"
        return f"change the {page} draft: " + ", ".join(sorted((payload.get("sections") or {})))
    return p["kind"]


def urgent(item):
    """A card a tripped bet or a publish brought: filed now, at every level, outside the Strategy Bot's 5 a day. Trips
    are rare (once per bet per version) and a wrong bet should not wait a day."""
    p = item.get("proposal") or {}
    return item.get("kind") == "proposal" and (
        p.get("kind") == S.REALIGN or (p.get("kind") == "strategy_draft"
                                       and (p.get("payload") or {}).get("source") == S.TRIPPED))


def _lens_name(p):
    """The lens a strategy proposal came from (`source: lens`, backend/lenses.py), by name, or None."""
    from . import lenses
    payload = p.get("payload") or {}
    found = lenses.lens(payload.get("lens")) if payload.get("source") == S.LENS else None
    return found["name"] if found else None


def _ask(item, manager=strategy_bot.NAME):
    """The task's title and body: the ask first, in plain words. `manager` is the bot's name
    (backend/strategy_bot.py)."""
    name, goal = item.get("kpi_name") or "", item.get("goal_title") or ""
    closes = "This closes by itself once "
    if item["kind"] == "vision":
        title = f"Tell your {manager} what this team is for"
        body = (f"{title}: write the team's Vision (one or two sentences: where it is going) and what it sells (for "
                f"whom) on the Strategy page (#/strategy). The {manager} ties every goal to the vision. {closes}a "
                f"vision is saved.")
        return title, body
    if item["kind"] == "recovery":
        return item["ask"]
    if item["kind"] == "why":
        lines = "\n".join(f"- {g['goal_title']}: \"{g['why']}\"" for g in item.get("goals") or [])
        title = "These goals need a Why line"
        body = (f"{title}, one line tying each to the company's vision. The {manager} proposed these:\n{lines}\n"
                f"Accept or edit each on its goal on the Goals page, or write your own. {closes}every one is settled.")
        return title, body
    if item["kind"] == "kpi_stale":
        first = item["reason"].endswith("has no reading yet")
        title = f"Log the first {name} reading" if first else f"Log a new {name} reading"
        body = (f"{title}: {item['reason']}. Open {name} on the Goals page and add the latest number, or tell the "
                f"{manager} on this task where it comes from. {closes}a fresh reading is logged.")
        return title, body
    if item["kind"] == "kpi_red":
        title = f"Check why {name} is off track on {goal}"
        body = (f"{title}: {item['reason']}. Add a check-in on the goal saying what happens next, or propose a "
                f"different target on the Goals page. {closes}{name} is back on track.")
        return title, body
    p = item["proposal"]
    if p["kind"] in GOAL_KINDS:
        return _goal_ask(item, manager)
    title = {"strategy_draft": "Decide a suggested change to your strategy",
             "brand_draft": "Decide a suggested change to your brand",
             "gtm_draft": "Decide a suggested change to your go-to-market"}.get(p["kind"], "Confirm or reject a goal proposal")
    if p["kind"] == S.REALIGN:
        payload = p.get("payload") or {}
        said = {"retired": "retired", "rewritten": "reworded"}
        lines = "\n".join(f"- {g['title']} (its bet was {said.get(g['change'], g['change'])}: \"{g['statement']}\")"
                          for g in payload["goals"])
        count = len(payload["goals"])
        title = f"Choose what happens to {count} goal{'s' if count != 1 else ''} after strategy v{payload['n']}"
        body = (f"{title}. Publishing v{payload['n']} retired or reworded the bets these goals serve:\n{lines}\n"
                f"For each one choose Keep as it is, Retire goal, or Move to a bet, then press the one button under "
                f"Needs you on the Goals page. Nothing changes until you press it. {closes}it is decided.")
        return title, body
    if p["kind"] == "strategy_draft":
        lens = _lens_name(p)
        if lens:
            title += f" from the {lens} lens"
        body = (f"{title}. {H.actor_id(p['proposed_by'])} proposed it"
                + (" from this week's market briefing" if (p.get("payload") or {}).get("source") == "briefing" else "")
                + (f" because {p['reason'].rstrip('.')}" if p.get("reason") and not lens else "")
                + f": {_change(p, name)}. Add to draft writes it to your draft; publishing stays your click on the "
                  f"Strategy page (#/strategy). No, leave it changes nothing. Decide it under Needs you on the Goals "
                  f"page. {closes}it is decided.")
        return title, body
    said = f" because {p['reason'].rstrip('.')}" if p.get("reason") else ""
    body = (f"{title}. {H.actor_id(p['proposed_by'])} proposed it{said}: {_change(p, name)}. Confirm or reject it "
            f"under Needs you on the Goals page. {closes}it is decided.")
    return title, body


# The goal suggestions whose asks use their card's words (ui/goals-kpis.js goalPropHtml), never "Confirm or reject".
GOAL_KINDS = ("goal_kpi", "kpi_target", "kpi_definition", "goal_wording", "flag")
OLD_WORDS = "Confirm or reject "


def _goal_ask(item, manager):
    p, payload = item["proposal"], item["proposal"].get("payload") or {}
    name, goal = item.get("kpi_name") or "", item.get("goal_title") or ""
    measure = (payload.get("kpi") or {}).get("name") or item.get("measure_name")
    title = {"goal_kpi": f"Add the measure {measure} to {goal}?" if measure else f"Add a measure to {goal}?",
             "kpi_target": f"Use a new {name} target on {goal}?",
             "kpi_definition": f"Use a new definition for {name}?",
             "goal_wording": f"Use new wording for {goal}?",
             "flag": f"The {manager} noticed something on {goal}"}[p["kind"]]
    said = f" because {p['reason'].rstrip('.')}" if p.get("reason") else ""
    change = _change(p, measure or "a measure")
    body = (f"{title.rstrip('?')}. {H.actor_id(p['proposed_by'])} proposed it{said}: {change}. "
            f"Decide it under Needs you on the Goals page.")
    return title, body


def _reword(conn, task, item):
    """An ask filed in the old words ("Confirm or reject ...") takes its card's words, once: afterwards its title no
    longer starts with them."""
    if not str(task["title"]).startswith(OLD_WORDS):
        return
    title, body = _ask(item, strategy_bot.name(conn))
    if H.TITLE_LINT == "refuse" and H.lint_title(title):
        return
    with isolated(conn, "goal_ask_reword", task["id"]):
        H.task_update(conn, G.AUTO_ACTOR, task["id"], title=_free_title(conn, task["owner_id"], title), body=body,
                      private=True if item.get("private") else None, quiet=True)


def _relist(conn, task, item):
    """The bundled Why ask lists the goals waiting now: one joined or was settled since it was filed."""
    body = _ask(item, strategy_bot.name(conn))[1]
    if task["body"] != body:
        with isolated(conn, "goal_ask_relist", task["id"]):
            H.task_update(conn, G.AUTO_ACTOR, task["id"], body=body, quiet=True)


def _resolved(conn, kind, ref):
    if kind == "vision":
        return "Resolved: the team's vision is set."
    if kind == "why":
        return "Resolved: no Why line is waiting on you."
    if kind == "kpi_stale":
        return "Resolved: a fresh reading is logged (or the KPI moved on)."
    if kind == "kpi_red":
        return "Resolved: the KPI is no longer off track on your goal."
    row = G.proposal(conn, ref)
    if kind == "recovery" and row:
        from . import strategy_suggest
        done = strategy_suggest.result_text(conn, row)
        if done:
            return done
        if row["status"] == "rejected":
            return f"Resolved: {row['decision_note'] or 'rejected; nothing changed'}."
    if row and row["status"] != "pending":
        return f"Resolved: the proposal was {row['status']} by {H.actor_id(row['decided_by'] or '')}.".replace(" by .", ".")
    return "Resolved: nothing is waiting on you here any more."


def _free_title(conn, owner, title):
    """task_create refuses a second live task with the same requester, owner and title; two different proposals
    can word the same, so the second one is numbered instead of refused."""
    taken = {r[0] for r in conn.execute(
        f"SELECT title FROM tasks WHERE requester_id=? AND owner_id=? AND status IN ({','.join('?' * len(H.LIVE_STATUSES))})",
        (G.AUTO_ACTOR, owner, *H.LIVE_STATUSES))}
    out, n = title[:290], 2
    while out in taken:
        out, n = f"{title[:290]} ({n})", n + 1
    return out


def manager_ready(conn):
    row = H.bot(conn, G.GOAL_MANAGER)
    return bool(row) and row.get("state") not in ("archived", "quarantined")


def sync(conn, owner_id, at=None, reads=None):
    """File the asks that are new and close the ones that are resolved. Returns {created, closed}. Never raises
    for one row: a failure is recorded and the rest go on (backend/batch_work.py)."""
    if not manager_ready(conn):
        return {"created": [], "closed": []}
    # at Passive nothing new is filed; what was asked still closes when resolved
    asks = strategy_bot.proactive(conn)
    # Only someone on the roster: a refused ask counts against the Strategy Bot (rule 8), every hour.
    want = {(i["kind"], i["ref"], i["owner"]): i for i in wanted(conn, owner_id, at, reads)
            if H.is_human(i["owner"]) and H.human(conn, H.actor_id(i["owner"]))}
    have = {(r["kind"], r["ref"], r["owner"]): dict(r) for r in conn.execute("SELECT * FROM goal_asks")}
    created, closed = [], []
    moved = {(kind, ref): owner for kind, ref, owner in want}
    room = {}           # each person's room left today in the Strategy Bot's 5 a day

    def has_room(owner):
        if not asks:
            return False
        if owner not in room:
            room[owner] = strategy_bot.room_today(conn, owner)
        return room[owner] > 0

    for key, row in list(have.items()):
        task = H.task(conn, row["task_id"])
        # Marked done while its cause stands: accepted and asked again, so it does not sit done and hidden.
        again = key in want and task and task["status"] == "done"
        if key in want and not again:
            if key[0] == "why" and task and task["status"] in H.LIVE_STATUSES:
                _relist(conn, task, want[key])
            elif (key[0] == "proposal" and task and task["status"] in H.LIVE_STATUSES
                  and want[key]["proposal"]["kind"] in GOAL_KINDS):
                _reword(conn, task, want[key])
            continue
        if again and not has_room(key[2]):
            continue        # asked again on a day with room; until then it stays as the person left it
        with isolated(conn, "goal_ask_close", row["task_id"]):
            if task and task["status"] in H.LIVE_STATUSES:
                if again:
                    note = f"Still needed: {want[key].get('reason') or 'nothing has changed yet'}. Asked again in a new task."
                elif moved.get(key[:2]):
                    note = f"Now asked of {H.actor_id(moved[key[:2]])}, who owns it now."
                else:
                    note = _resolved(conn, row["kind"], row["ref"])
                H.task_close(conn, G.AUTO_ACTOR, task["id"], note=note, quiet=True)
                closed.append(task["id"])
            conn.execute("DELETE FROM goal_asks WHERE kind=? AND ref=? AND owner=?", key)
            if again:
                del have[key]
    for key, item in want.items():
        if key in have:
            continue
        rush = urgent(item)
        if not rush and not has_room(item["owner"]):
            continue        # the next day's room, most important first (ORDER)
        with isolated(conn, "goal_ask_file", ":".join(key)):
            title, body = _ask(item, strategy_bot.name(conn))
            if H.TITLE_LINT == "refuse" and H.lint_title(title):
                # A KPI name the title lint refuses would be refused, and counted against the Strategy Bot, every hour.
                log.warning("Not asking %s: the title lint refuses its KPI's name", item["owner"])
                continue
            made = H.task_create(conn, G.AUTO_ACTOR, _free_title(conn, item["owner"], title), body, item["owner"],
                                 lint=False, lane="company", top=item["kind"] == "vision",
                                 private=True if item.get("private") else None)
            conn.execute("INSERT INTO goal_asks (kind, ref, owner, task_id, created_at) VALUES (?,?,?,?,?)",
                         (*key, made["id"], H.now()))
            created.append(made["id"])
            if not rush:
                room[item["owner"]] -= 1
    return {"created": created, "closed": closed}


def for_person(conn, actor, items):
    """The Goals page's Needs you for one caller: each item carries the task that asks it (`task`), and an item
    the person already closed or declined while its cause stands is left out, as the task is."""
    rows = {(r["kind"], r["ref"]): r["task_id"] for r in conn.execute("SELECT * FROM goal_asks WHERE owner=?", (actor,))}
    # An ask not filed yet shows only while today's room holds it, most important first (`items` are in ORDER): the
    # rest wait for the next day, as their tasks do (`sync`). At Passive nothing new is asked, so only filed ones show.
    room = strategy_bot.room_today(conn, actor) if strategy_bot.proactive(conn) else 0
    out = []
    for item in items:
        task_id = rows.get((item["kind"], item["ref"]))
        task = H.task(conn, task_id) if task_id else None
        if task_id and not (task and task["status"] in H.ACTIVE_STATUSES):
            continue
        if not task_id and not urgent(item):
            if room <= 0:
                continue
            room -= 1
        view = {k: v for k, v in item.items() if k not in ("owner", "measure_name", "private")}
        if task:
            view["task"] = {"id": task["id"], "title": task["title"], "status": task["status"]}
        title, body = _ask(item, strategy_bot.name(conn))
        out.append(needs_shape.goal_item(view, title, body, task["created_at"] if task else None))
    return out
