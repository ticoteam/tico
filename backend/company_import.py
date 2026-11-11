"""Apply a setup-page company file through the installed app's ordinary onboarding.

The file describes a team, never an identity or permission grant. The same adapter
serves the owner's file upload and the installer. Its caller owns the transaction.

A format 0.3 file's `strategy` block lands as the setup page drafted it, for the owner to confirm, never published:
the strategy draft (sections marked unconfirmed, bets `proposed`), the market by spend (definition, segments and
their sizes `proposed`, each size on its evidence), its players as market entities with their sites watched, the
listening requests as a "Watching" section and the posts filed as market insights for the Librarian's next pass.
Each goal names the bet it came from. A file without the block imports exactly as before.

A seat with a `team` goes in the group of that name: one group per name, made in file order, or the group that already
has it. A seat without one goes in its template's group. A built-in is the only bot a file makes, and it stays outside
groups whatever its team says (backend/groups.py).
"""

import hashlib
import json
import logging
from datetime import date, timedelta
from urllib.parse import urlsplit

from pydantic import ValidationError

from . import company_file as C
from . import goals as G
from . import groups as Groups
from . import lifecycle
from . import kpis as K
from . import market as MK
from . import market_spend as MS
from . import market_watch as MW
from . import models as M
from . import onboarding as O
from . import setup_live
from . import strategy as S
from . import strategy_setup as SS
from .company_file import draft as D
from .company_file import starter
from .company_file.yamlfile import tool_list
from .store import H, Problem, encode

PLAN_KEY = "onboarding.company_file"
FAILED_KEY = "onboarding.company_import_failed"
log = logging.getLogger(__name__)


# The built-ins the chart shows in a team rather than in the Built-in group: the Finance Owner, in
# Corporate. Every other built-in stays in the Built-in group whatever team a file names.
IN_TEAM_BUILT_INS = ("finance-owner",)
SOFTWARE_DEPARTMENTS = ("product", "engineering")  # the default chart's seats a company that makes no software skips
SOFTWARE_TEAMS = ("Product", "Engineering")  # and its teams


def plan(onboarding, c, who, text):
    """Validate and translate without changing the company or trusting file owner IDs.

    The chart is laid out exactly (docs/onboarding.md, "Setup picks the chart"): a built-in role (the Assistant, BotOps)
    is a bot; every other role is a Recruiting seat with the file's reporting line, a seat under a seat included; a role
    a person holds is that person's, kept for the owner to invite (`people`). A line to someone not in Tico yet (a
    person the owner hasn't invited) goes to the owner until they are invited."""
    onboarding._owner(who)
    try:
        saved = starter.load(text)
    except C.DraftError as exc:
        raise Problem("company_file", exc.plain, 422) from None
    draft = saved
    owner = onboarding.auth.owner_id(c)
    if not owner or not H.human(c, owner):
        raise Problem("company_file", "Sign in as this company's owner before importing its starter team", 409)
    cards = O.read_cards(onboarding.settings)
    by_name = {name: card for card in cards for name in (card["template"], card["slug"])}
    required = {onboarding._bot_slug(card["slug"]): card["template"] for card in cards if card["required"]}
    home = "human:" + owner
    owner_id = D.owner_id(draft)
    held = {bot["id"]: bot["person"] for bot in draft["bots"] if bot.get("person")}
    people_ids = {p["id"] for p in draft["people"]}
    ids, builtins, roles = {}, {}, {}
    for bot in draft["bots"]:
        if bot["id"] in held:
            continue
        card = by_name.get(bot["template"])
        if card is not None and card["required"]:
            slug = onboarding._bot_slug(card["slug"])
            ids[bot["id"]] = slug
            # There is one of each built-in. A second role on it (two roles an older release had that both fold into
            # the Finance Owner, say) is that built-in too: the first keeps its place, and a line to either ends there.
            builtins.setdefault(slug, (bot, card))
        elif onboarding._bot_slug(bot["id"]) in required or bot["id"] in required:
            raise Problem("company_file", "An employee ID conflicts with another employee or a built-in role", 422)
        else:
            if bot.get("live") and card is None:
                raise Problem(
                    "company_file", "A live seat needs a template Tico can build a bot from: %s" % bot["template"], 422
                )
            roles[bot["id"]] = (bot, card)

    def holder(at):
        """Who a line ends at when it reaches a person: the owner, or a person not in Tico until invited."""
        person = held.get(at, at)
        return None if person == owner_id else person

    def line(bot):
        """(reports_to, waiting on person) for a seat: another seat's key, a built-in's slug or the owner."""
        parent = D.manager_of(draft, bot)
        if parent in roles:
            return parent, None
        if parent in ids:
            return ids[parent], None
        if parent == owner_id or parent in held or parent in people_ids:
            return home, holder(parent)
        raise Problem("company_file", "An employee's reporting line cannot be applied", 422)

    selected = {}
    try:
        names = M.OnboardingNames(
            company_name=draft["company"]["name"], owner_name=(D.owner(draft) or {"name": ""})["name"]
        )
        display = O.display_names(onboarding.settings, {"names": names.model_dump()})
        for slug, (bot, card) in builtins.items():
            parent = D.manager_of(draft, bot)
            # A built-in is a bot, and a bot reports to a bot or a person: under a seat, it reports where that seat does.
            seen = set()
            while parent in roles and parent not in seen:
                seen.add(parent)
                parent = D.manager_of(draft, roles[parent][0])
            parent = ids.get(parent) or home
            if parent == slug:
                parent = home
            instructions = O.render(card, display, bot["title"])["instructions"]
            if bot["reason"]:
                instructions += "\n\nWhy this role is on the company chart:\n" + bot["reason"]
            selected[slug] = M.OnboardingSelection(
                template=card["template"], display_name=bot["title"], instructions=instructions, reports_to=parent
            )
        # The places are sent only when the file names them: a file without them keeps the places already saved.
        places = {"regions": draft["company"]["regions"]} if draft["company"].get("regions") else {}
        body = M.OnboardingDraft(
            names=names,
            answers=M.OnboardingAnswers(what_we_do=draft["company"]["about"], **places),
            selected=selected,
        )
        seats = {}
        for key, (bot, card) in roles.items():
            reports_to, waits = line(bot)
            seats[key] = {
                "title": M.SeatCreate(title=bot["title"]).title,
                "template": bot["template"],
                "description": O.job_description(card or {}, display, bot["reason"]),
                "reports_to": reports_to,
                "waits_on": waits,
                "team": bot.get("team", ""),
                "live": bool(bot.get("live")),
            }
        # A built-in that sits in a team (the Finance Owner is the Finance Owner) keeps a seat on its line, filled by the
        # built-in bot itself: a manager hired above it later takes it along (recruiting._follow_filled).
        for slug, (bot, card) in builtins.items():
            if slug in IN_TEAM_BUILT_INS:
                seats[slug] = {
                    "title": M.SeatCreate(title=bot["title"]).title,
                    "template": card["template"],
                    "description": O.job_description(card, display, bot["reason"]),
                    "reports_to": line(bot)[0],
                    "waits_on": None,
                    "team": bot.get("team", ""),
                    "live": False,
                    "fills": slug,
                }
    except ValidationError:
        raise Problem(
            "company_file", "A company name, title or instruction is too long for this Tico setup", 422
        ) from None
    titles = {}
    for bot in draft["bots"]:
        if bot["id"] in held:
            titles.setdefault(held[bot["id"]], []).append(bot["title"])
    people = [
        {"id": p["id"], "name": p["name"], "title": ", ".join(titles.get(p["id"]) or []) or p["role"]}
        for p in draft["people"]
        if p["id"] != owner_id
    ]
    fingerprint = hashlib.sha256(encode(saved).encode()).hexdigest()
    deferred = []
    if "models" in saved:
        deferred.append("model_choices")
    if tool_list(draft):
        deferred.append("tool_connections")
    if any(b.get("first_job") for b in draft["bots"]):
        deferred.append("first_jobs")
    if any(b.get("routine") for b in draft["bots"]):
        deferred.append("routines")
    summary = {
        "fingerprint": fingerprint,
        "company_name": names.company_name,
        "employees": [
            {
                "id": slug,
                "template": choice.template,
                "title": choice.display_name,
                "reports_to": choice.reports_to,
                "start": "now",
            }
            for slug, choice in selected.items()
        ],
        "seats": [
            {
                "id": key,
                "template": seat["template"],
                "title": seat["title"],
                "reports_to": seat["reports_to"],
                **({"waits_on": seat["waits_on"]} if seat["waits_on"] else {}),
                **({"live": True} if seat["live"] else {}),
            }
            for key, seat in seats.items()
            if not seat.get("fills")  # a built-in's seat: it works from day one, under `employees`
        ],
        "people": people,
        "goals": [
            {"id": goal["id"], "title": goal_title(goal), "owner": goal_owner(goal, owner_id, ids, required)}
            for goal in draft["goals"]
        ],
        "builtins": sorted(required),
        "deferred": deferred,
    }
    if D.has_strategy(saved):
        summary["strategy"] = strategy_preview(saved["strategy"])
    return body, saved, seats, summary


def goal_owner(goal, owner_id, ids, builtins):
    """Who owns an imported goal on Goals. The file names the company's owner for a company goal (setup's
    recruiting/goals.py gives every company goal to the owner) and keeps no level or parent, so the owner's goals are
    the company's. Every other role on the chart is a Recruiting seat, which holds no work (backend/lifecycle.py), so
    its goal is the company's until the seat is recruited; check() has already given a goal whose owner left the chart
    to the owner. The built-in bots own no goals (Goals keeps them out of its tree), so a goal the file gives one, the
    Assistant's in a starter plan, is the company's too."""
    slug = ids.get(goal["owner"])
    return G.COMPANY if goal["owner"] == owner_id or slug is None or slug in builtins else "bot:" + slug


def goal_title(goal):
    """The file may leave a goal's title blank; Tico names every goal, so it is called by its measure instead."""
    return goal["title"].strip() or goal["measure"] or "Untitled goal"


def target(goal):
    """The file's numbers as the KPI link's target: an improvement needs both a target and its deadline, a range
    a min or a max. Anything less is a measure with no target yet."""
    if goal["direction"] == "range":
        if goal["min"] is None and goal["max"] is None:
            return {"kind": "none"}
        return {"kind": "maintain", "min": goal["min"], "max": goal["max"]}
    if goal["target"] is None or not goal["deadline"]:
        return {"kind": "none"}
    return {"kind": "improve", "target": goal["target"], "baseline": goal["baseline"], "deadline": goal["deadline"]}


def import_goals(c, actor, goals, bets=None):
    """Each goal under its owner, with its measure as a KPI on it, and on the bet it came from (`bets`: the file's
    bet id to the strategy's). The file holds a baseline and a target but never readings, so no history is made up.
    A target Tico would refuse (one on the wrong side of its baseline) is left off for the owner to set; the goal and
    its measure still land."""
    for goal, owner in goals:
        made = G.create(c, actor, goal_title(goal), owner, bet_id=(bets or {}).get(goal.get("bet")))
        if not goal["measure"]:
            continue
        kpi = G.kpi_create(
            c, actor, owner, {"name": goal["measure"], "unit": goal["unit"], "direction": goal["direction"]}
        )
        aim = target(goal)
        G.kpi_link(c, actor, made["id"], kpi["id"], {"kind": "none"} if K.validate_target(kpi, aim)[1] else aim)


# ----------------------------------------------------------------------------- the strategy block (format 0.3)
SOURCE = "the company file"  # the unconfirmed line: "_Unconfirmed: drafted from the company file._"
WATCHING = "watching"  # the extra section the listening requests are kept in
WATCHING_TITLE = "Watching"


def strategy_preview(strategy):
    """What the strategy block will add, for the preview."""
    market, listening = strategy["market"], strategy["listening"]
    return {
        "summary": bool(strategy["summary"]),
        "vision": bool(strategy["vision"]),
        "sections": [k for k in S.FIXED_KEYS if strategy["sections"].get(k)]
        + (["watching"] if listening["requests"] else []),
        "bets": [{"id": b["id"], "statement": b["statement"]} for b in strategy["bets"] if b["statement"]],
        "market": {
            "root": market["definition"]["root"],
            "segments": len(market["segments"]),
            "sizes": sum(1 for s in market["segments"] if s["usd"] is not None),
            "players": [p["name"] for p in market["players"]],
        },
        "watches": [p["url"] for p in market["players"] if p["url"]],
        "insights": len(listening["posts"]),
    }


def _safe_url(url):
    """The address, or "" for one with a user name or password in it, or one that can't be read. The file's check
    refuses both already; this keeps a credential out of market evidence whatever wrote the plan."""
    try:
        parts = urlsplit(str(url or ""))
        return url if parts.scheme in ("http", "https") and parts.hostname and "@" not in parts.netloc else ""
    except ValueError:
        return ""


def _segment_id(c, key):
    """A free id for a file's segment: `segment/<key>`, or with -2, -3 ... when the market graph has it (the root
    set_definition made from the same words, say)."""
    base = "segment/" + key
    n, found = 1, base
    while MK.entity(c, found):
        n += 1
        found = "%s-%d" % (base, n)
    return found


def _best_effort(c, name, work, skipped):
    """One part of the block: a part the app refuses (a name the market graph already has, say) is left out and
    named in `skipped`, and the rest of the import goes on."""
    c.execute("SAVEPOINT " + name)
    try:
        out = work()
    except (H.Refused, Problem) as exc:
        c.execute("ROLLBACK TO " + name)
        out = None
        skipped.append({"part": name, "reason": str(getattr(exc, "detail", "") or exc)[:200]})
    c.execute("RELEASE " + name)
    return out


def _strategy_draft(c, actor, strategy):
    """Summary, vision and the company file's sections where the draft's are empty (How we sell is not in the file, so
    it stays as the draft has it), and the listening requests as the Watching section. Never published."""
    row = S.draft(c)
    sections = {
        k: SS.unconfirmed(strategy["sections"].get(k), SOURCE)
        for k in S.FIXED_KEYS
        if strategy["sections"].get(k) and not row["sections"][k].strip()
    }
    requests = strategy["listening"]["requests"]
    if requests and WATCHING not in row["sections"]["extra"]:
        sections["extra"] = {WATCHING: {"title": WATCHING_TITLE, "body": "\n".join("- " + r for r in requests)}}
    lines = {k: strategy[k] for k in ("summary", "vision") if strategy[k] and not row[k].strip()}
    if sections or lines:
        S.update_draft(c, actor, sections=sections or None, **lines)
    return sorted(sections) + sorted(lines)


def _strategy_bets(c, actor, bets, today, skipped):
    """Each bet `proposed`, its wrong-if a text test due on its check date (CHECK_DAYS out when it has none); a
    statement the draft already has is that bet. {file bet id: strategy bet id}."""
    live = {b["statement"].strip().lower(): b["id"] for b in S.bets(c) if b["status"] != "retired"}
    ids = {}
    for item in bets:
        if not item["statement"]:
            continue
        known = live.get(item["statement"].strip().lower())
        if known or len(live) >= S.LIVE_MAX:
            if known:
                ids[item["id"]] = known
            continue
        check_by = item["check_by"] or (today + timedelta(days=SS.CHECK_DAYS)).isoformat()
        wrong = {"kind": "text", "text": item["wrong_if"], "check_by": check_by} if item["wrong_if"] else None
        bet = _best_effort(
            c,
            "company_file_bet",
            lambda item=item, wrong=wrong: S.add_bet(
                c, actor, item["statement"], item["why"], item["rules_out"], wrong, None, "proposed"
            )[0],
            skipped,
        )
        if bet:
            ids[item["id"]] = live[item["statement"].strip().lower()] = bet["id"]
    return ids


def _own_company(c, actor, name, evidence):
    """`company/self`, the centre of the market graph, made when the graph has none yet."""
    if not MK.entity(c, MK.OWN_COMPANY_ID):
        MK.create_entity(
            c,
            actor,
            type="organization",
            name=name,
            summary=name + ".",
            evidence_ids=[evidence],
            force=True,
            entity_id=MK.OWN_COMPANY_ID,
        )
    return MK.OWN_COMPANY_ID


def _market(c, actor, market, company, skipped):
    """The definition and its segments, each size `proposed` on its own evidence, `ours` as the company's
    `operates_in`, and `next` on the segment's properties. Only on a market not defined yet: a team that defined its
    own keeps it. {file segment key: segment id}."""
    definition, segments = market["definition"], market["segments"]
    if (MS.definition(c) or {}).get("root") or not (definition["root"] or segments):
        return {}
    MS.set_definition(
        c,
        actor,
        {
            "root_name": definition["root"] or company + " market",
            "description": definition["in"],
            "includes": [definition["in"]] if definition["in"] else None,
            "excludes": [definition["out"]] if definition["out"] else None,
            "year": definition["year"],
        },
    )
    root = MS.definition(c)["root"]
    evidence = MS.definition_evidence(c, actor)
    by_key = {s["key"]: s for s in segments}

    def depth(segment):
        n, at = 1, segment
        while at["parent"] in by_key:
            n, at = n + 1, by_key[at["parent"]]
        return n

    ids = {}
    for segment in sorted(segments, key=depth):  # a parent before its sub-segments
        parent = ids.get(segment["parent"], root) if segment["parent"] else root
        if depth(segment) > MS.MAX_DEPTH or (segment["parent"] and segment["parent"] not in ids):
            skipped.append(
                {
                    "part": "company_file_segment",
                    "reason": "%s is deeper than %d levels or under a segment "
                    "left out" % (segment["name"] or segment["key"], MS.MAX_DEPTH),
                }
            )
            continue

        def made(segment=segment, parent=parent):
            # Forced past a name the tree already has: a segment named like the root is still the file's own. Its own
            # places (`regions`) are said in its summary, where a bot reads a segment.
            serves = segment.get("regions") or []
            properties: dict[str, object] = {"next": True} if segment["next"] else {}
            seg = MS._new_segment(
                c,
                actor,
                segment["name"] or segment["key"],
                summary=("Serves: " + "; ".join(serves) + ".") if serves else "",
                evidence_ids=[evidence],
                parent=parent,
                entity_id=_segment_id(c, segment["key"]),
                properties=properties or None,
                force=True,
            )
            urls = [u for u in map(_safe_url, segment["evidence"]) if u]
            if segment["usd"] is not None and urls:
                MS.create_size(
                    c,
                    actor,
                    {
                        "segment_id": seg["id"],
                        "method": segment["method"],
                        "spend_usd": segment["usd"],
                        "low_usd": segment["usd"],
                        "high_usd": segment["usd"],
                        "arithmetic": segment["arithmetic"],
                        "evidence": [{"source_url": u, "source_kind": "other"} for u in urls],
                        "year": definition["year"],
                        "confidence": "low",
                    },
                )
            if segment["ours"]:
                MK.create_edge(
                    c,
                    actor,
                    src=_own_company(c, actor, company, evidence),
                    rel="operates_in",
                    dst=seg["id"],
                    evidence_ids=[evidence],
                )
            return seg["id"]

        found = _best_effort(c, "company_file_segment", made, skipped)
        if found:
            ids[segment["key"]] = found
    return ids


def _players(c, actor, players, segments, company, skipped):
    """Each player a company on the market graph, in its segments and competing with the company, its site a
    watched page. [entity id]."""
    made = []
    for player in players:
        player = {**player, "url": _safe_url(player["url"])}

        def one(player=player):
            host = (urlsplit(player["url"]).hostname or "").removeprefix("www.") if player["url"] else ""
            evidence, _ = MK.create_evidence(
                c,
                actor,
                source_url=player["url"],
                source_kind="site" if host else "other",
                our_read="A player in the market, named in the company file from setup.",
            )
            # Forced past a shared domain: two players the page found on one site (a marketplace, say) are still two.
            entity, _ = MK.create_entity(
                c,
                actor,
                type="organization",
                name=player["name"],
                external_ids={"domain": host} if host else None,
                evidence_ids=[evidence["id"]],
                force=True,
            )
            for key in player["segments"]:
                if key in segments:
                    MK.create_edge(
                        c, actor, src=entity["id"], rel="operates_in", dst=segments[key], evidence_ids=[evidence["id"]]
                    )
            MK.create_edge(
                c,
                actor,
                src=entity["id"],
                rel="competes_with",
                dst=_own_company(c, actor, company, evidence["id"]),
                evidence_ids=[evidence["id"]],
            )
            return entity["id"]

        entity_id = _best_effort(c, "company_file_player", one, skipped)
        if entity_id:
            made.append(entity_id)
            if player["url"]:
                _best_effort(
                    c, "company_file_watch", lambda: MW.add(c, actor, entity_id, player["url"], "home"), skipped
                )
    return made


def _posts(c, actor, posts, skipped):
    """The posts the setup page found, filed as market insights (`new`) for the Librarian's daily curate."""
    filed = []
    for post in posts:
        if not _safe_url(post["url"]):
            skipped.append({"part": "company_file_post", "reason": "an address with a user name or password"})
            continue
        found = _best_effort(
            c,
            "company_file_post",
            lambda post=post: MK.report(
                c,
                actor,
                kind="other",
                about="Listening: " + post["tag"],
                claim=post["title"] or post["url"],
                source_url=post["url"],
                confidence="low",
                source_ref="company-file:" + post["url"],
            ),
            skipped,
        )
        if found:
            filed.append(found["id"])
    return filed


def import_strategy(c, actor, strategy, company, today=None):
    """The block, part by part (see the module's docstring). Answers ({file bet id: bet id}, what was written)."""
    skipped = []
    written = {
        "draft": _best_effort(c, "company_file_draft", lambda: _strategy_draft(c, actor, strategy), skipped) or []
    }
    bets = _strategy_bets(c, actor, strategy["bets"], today or date.today(), skipped)
    segments = (
        _best_effort(c, "company_file_market", lambda: _market(c, actor, strategy["market"], company, skipped), skipped)
        or {}
    )
    players = _players(c, actor, strategy["market"]["players"], segments, company, skipped)
    insights = _posts(c, actor, strategy["listening"]["posts"], skipped)
    written.update(
        bets=sorted(bets.values()),
        segments=sorted(segments.values()),
        players=players,
        insights=insights,
        skipped=skipped,
    )
    return bets, written


def preview(onboarding, c, who, text):
    return plan(onboarding, c, who, text)[3]


def apply(onboarding, c, who, text, fingerprint):
    """Apply a confirmed plan once; retries never rebuild or replace a configured team."""
    body, saved, seats, summary = plan(onboarding, c, who, text)
    if fingerprint != summary["fingerprint"]:
        raise Problem("company_file_changed", "The company file changed; preview it again before importing", 409)
    record = O.load(c)
    previous = record.get("company_import") or {}
    if previous.get("fingerprint") == fingerprint:
        clear_failure(c)  # this file is the one already applied, so nothing from setup is left unimported
        return onboarding.view(c, who)
    builtins = set(summary["builtins"])
    existing = [r[0] for r in c.execute("SELECT id FROM bots") if r[0] not in builtins]
    laid = c.execute("SELECT 1 FROM seats WHERE closed_at IS NULL LIMIT 1").fetchone()
    if record["completed"] or existing or laid:
        raise Problem("company_already_setup", "This company is already set up; import cannot replace its team", 409)
    # Create definitions under the owner before installing the desired graph.
    # Otherwise complete() can see a temporary cycle through the old seed while
    # it resolves a newly-created parent. Neither staging nor a partial chart is
    # visible outside this transaction; the confirmed choices are restored below.
    home = "human:" + onboarding.auth.owner_id(c)
    staging = body.model_copy(
        update={
            "selected": {slug: choice.model_copy(update={"reports_to": home}) for slug, choice in body.selected.items()}
        }
    )
    # The strategy goes in first, so complete() links each group it makes to the bet it serves.
    bets, strategy = (
        import_strategy(c, who.actor, saved["strategy"], body.names.company_name)
        if D.has_strategy(saved)
        else ({}, None)
    )
    onboarding.save(c, who, staging)
    onboarding.complete(c, who, finish=False)  # the file's own chart is laid out below, then finished

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
            onboarding.admin.update_bot(
                c, who, slug, M.BotDefinitionUpdate(reports_to=parent, expected_revision=row["revision"])
            )
    made, teams = lay_out_chart(onboarding, c, who, seats)
    finish_new_company(onboarding, c, who, seats, made, teams)
    owners = {goal["id"]: goal["owner"] for goal in summary["goals"]}
    import_goals(c, who.actor, [(goal, owners[goal["id"]]) for goal in saved["goals"]], bets)
    record = O.load(c)
    record["selected"] = {slug: choice.model_dump() for slug, choice in body.selected.items()}
    record["seats"] = made
    # The people who hold a role, to invite (Settings > People): each with the seats that report to them meanwhile.
    people = [
        {**person, "seats": sorted(made[key] for key, seat in seats.items() if seat["waits_on"] == person["id"])}
        for person in summary["people"]
    ]
    record["company_import"] = {
        "fingerprint": fingerprint,
        "imported_at": H.now(),
        "by": who.actor,
        "deferred": summary["deferred"],
        "seats": sum(1 for key in made if not seats[key].get("fills")),
        "people": people,
        **({"strategy": strategy} if strategy else {}),
    }
    onboarding._store(c, record, who.actor)
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (PLAN_KEY, encode(saved)),
    )
    clear_failure(c)
    H.event(
        c,
        who.actor,
        "onboarding.company_imported",
        "",
        {"fingerprint": fingerprint, "bots": sorted(body.selected), "seats": record["company_import"]["seats"]},
    )
    onboarding.complete_company(c)  # what a start would complete, now that the chart's live bots exist
    return onboarding.view(c, who)


def _depth(key, seats):
    """How many seats sit above this one in the file, so a manager is made before the seats under it."""
    depth, at = 0, seats[key]["reports_to"]
    while at in seats and depth <= len(seats):
        depth, at = depth + 1, seats[at]["reports_to"]
    return depth


def _go_live(onboarding, c, who, seats, made, teams):
    """Each live seat of the chart gets a bot that fills it, parents first. A bot reports to a bot or a person, never to a
    seat (lifecycle.bot_parent), so it is defined under the line its seat answers to. BotOps sets it up like any picked
    role, it goes in its team's group, and it fills its seat (lifecycle.fill_seat moves the seats under it onto the bot).
    `placed_under` is the line it was defined on: a bot that is later hired above it follows the seat (recruiting)."""
    record = O.load(c)
    cards = {card["template"]: card for card in O.read_cards(onboarding.settings)}
    names = O.display_names(onboarding.settings, record)
    home = "human:" + onboarding.auth.owner_id(c)
    reviewers = []  # the review seats' cards gone live so far: the second reviews on another model (D12)
    for key in sorted((key for key in seats if seats[key]["live"]), key=lambda key: _depth(key, seats)):
        seat, seat_id = seats[key], made[key]
        above = c.execute("SELECT reports_to FROM seats WHERE id=?", (seat_id,)).fetchone()["reports_to"] or ""
        under = lifecycle.bot_parent(c, above) or home
        card = O.render(cards[seat["template"]], names, seat["title"])
        if seat["template"] == REVIEWER_TEMPLATE:
            other = second_reviewer_model(onboarding, c, reviewers[0]) if reviewers else None
            reviewers.append(card)
            card = {**card, "model": other} if other else card
        instructions = card["instructions"].rstrip()
        if seat["description"]:
            instructions += "\n\nJob description:\n" + seat["description"]
        choice = {
            "template": seat["template"],
            "display_name": seat["title"],
            "instructions": instructions,
            "reports_to": under,
            "goes_live": True,
        }
        bot_id = onboarding._define(c, who, key, choice, card, reports_to=under)
        declared = onboarding._declared(c, bot_id)
        declared["placed_under"] = under
        # Its setup ends with it live and taking work, with no person step (backend/setup_live.py): BotOps takes it
        # live on its "Set up" task, and nothing parks it in Onboarding.
        declared[setup_live.GOES_LIVE] = True
        onboarding._write_config(c, bot_id, declared)
        onboarding._setup_task(c, who, bot_id, choice, card, record["answers"])
        if teams.get(key):
            Groups.move_bot(c, bot_id, teams[key])
        lifecycle.fill_seat(c, who, seat_id, bot_id)


REVIEWER_TEMPLATE = "reviewer"  # Reviewer 1 and Reviewer 2 (pipeline_seats.TEMPLATES)


def second_reviewer_model(onboarding, c, first_card):
    """The model Reviewer 2 runs on, so the Engineering pipeline's second review is a different model's (D12): Codex's
    recommended model when the company has OpenAI, else another current model of the provider Reviewer 1 runs on
    (another Claude model for a Claude company; a Codex company's Reviewer 1 already runs Codex). None when no provider
    is chosen yet: both then follow the company default until someone picks one."""
    from . import providers as P

    first = onboarding._runtime(c, first_card)
    if not first:
        return None
    codex = P.recommended("openai")
    if codex and codex["id"] != first[0] and "openai" in P.load(c, onboarding.settings)["enabled"]:
        return codex["id"]
    provider = P.MODEL_BY_ID[first[0]]["provider"]
    return next((row["id"] for row in P.provider_models(provider) if row["id"] != first[0]), None)


def lay_out_chart(onboarding, c, who, seats):
    """A chart's seats (plan()'s `seats` shape) laid out as Recruiting seats. A seat with a team goes in the group of that
    name, one per name, made if missing; one without goes in its template's group, as it always has. A built-in's seat
    (`fills`: the Finance Owner's is the Finance Owner's) is filled at once by that bot as it is, which goes in the seat's
    group: no second bot and no setup task. `placed_under` keeps the line it is on, so a manager hired above its seat
    later takes it along (recruiting._follow_filled). Returns ({key: seat id}, {key: group id})."""
    teams = Groups.seat_teams(
        c, onboarding.settings, sorted({seat["template"] for seat in seats.values() if not seat["team"]})
    )
    named = Groups.team_groups(c, [seat["team"] for seat in seats.values() if seat["team"]])
    chart = {}
    for key, seat in seats.items():
        team = named.get(seat["team"]) or teams.get(seat["template"], "")
        chart[key] = {**seat, "team": team, "description_by": "chart"}
    made = lifecycle.lay_out(c, who.actor, chart)
    for key in sorted((key for key in seats if seats[key].get("fills")), key=lambda key: _depth(key, seats)):
        found = H.resolve_bot(c, seats[key]["fills"])
        if not found:
            continue
        line = c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (found["id"],)).fetchone()
        declared = onboarding._declared(c, found["id"])
        declared["placed_under"] = (line["reports_to"] if line else "") or ""
        onboarding._write_config(c, found["id"], declared)
        if chart[key]["team"]:
            Groups.move_bot(c, found["id"], chart[key]["team"])
        lifecycle.fill_seat(c, who, made[key], found["id"])
    return made, {key: seat["team"] for key, seat in chart.items()}


FINISHED_KEY = "new_company_finished"  # on the onboarding record: when the finish ran, so it never runs twice


def new_company(c):
    """Whether this company's work is still only what every hub boots with: no pipeline beyond General and the presets
    nobody changed or put a task in (pipeline_settings.from_preset: Product and Engineering, made before setup ends)."""
    from . import pipeline_settings

    kept = {H.GENERAL_PIPELINE, *pipeline_settings.from_preset(c)}
    return all(row[0] in kept for row in c.execute("SELECT id FROM pipelines"))


def finish_new_company(onboarding, c, who, seats, made, teams):
    """The finish every way a new company is set up shares (a company file, the setup page's chart, or no chart at all):
    the chart's live seats get their bots (_go_live). Only for a new company (new_company), and once: a company already
    set up, or one finished before, is left as it is. True when it ran."""
    if O.load(c).get(FINISHED_KEY) or not new_company(c):
        return False
    _go_live(onboarding, c, who, seats, made, teams)
    record = O.load(c)
    record[FINISHED_KEY] = H.now()
    onboarding._store(c, record, who.actor)
    return True


def default_chart(onboarding, c, home, every_team=False):
    """The chart a new company starts with when setup picked none: the chart formula's own default
    (backend/company_file/chart_formula.py: no segment or bet picked, the default channels), which is the chart the
    site's company file gives a company that picked nothing, in plan()'s `seats` shape. A seat whose template this
    install lacks is left out, and a seat under it reports to the owner. A company that said software is not its
    product gets no Product or Engineering seats, as the chart builder does (company_file/actions.py `seed`), and a team
    setup's Your team step switched off (`answers.teams_off`) gets none, unless `every_team` (the step lists them all);
    a team of built-ins only (`locked_teams`) is never off. The Finance Owner's seat is filled by the Finance Owner, as
    on import."""
    from .company_file import chart_formula as F

    cards = {card["template"]: card for card in O.read_cards(onboarding.settings)}
    record = O.load(c)
    display = O.display_names(onboarding.settings, record)
    no_software = record["answers"].get("software_product") == "no"
    rows = [
        row
        for row in F.build({})["employees"]
        if row["template"] in cards
        and not (no_software and (row["department"] in SOFTWARE_DEPARTMENTS or row["team"] in SOFTWARE_TEAMS))
    ]
    off = set() if every_team else set(record["answers"].get("teams_off") or []) - locked_teams(rows, cards)
    rows = [row for row in rows if row["team"] not in off]
    keys = {row["id"] for row in rows}
    seats = {}
    for row in rows:
        card = cards[row["template"]]
        built_in = onboarding._bot_slug(card["slug"]) if card["required"] else ""
        if built_in and built_in not in IN_TEAM_BUILT_INS:
            continue  # every other built-in is a bot already, in the Built-in group
        seats[row["id"]] = {
            "title": M.SeatCreate(title=row["title"]).title,
            "template": row["template"],
            "description": O.job_description(card, display, row["reason"]),
            "reports_to": row["reports_to"] if row["reports_to"] in keys else home,
            "waits_on": None,
            "team": row["team"],
            "live": bool(row["live"]) and not built_in,
            **({"fills": built_in} if built_in else {}),
        }
    return seats


def locked_teams(rows, cards):
    """The default chart's teams made of built-ins only: every company has them, so setup cannot switch them off."""
    teams = {}
    for row in rows:
        teams.setdefault(row["team"], []).append(bool(cards[row["template"]]["required"]))
    return {team for team, required in teams.items() if all(required)}


def default_teams(onboarding, c, home):
    """The default chart by team, for setup's Your team step: each team's name, whether it is on, and its seats in
    chart order (live: a bot works from day one; else a Recruiting seat), each with the seat it reports to inside the
    team (None for the team's top seats), so the step draws the chart's own shape. Every team is listed, the
    switched-off ones as off; a team of built-ins only is `locked`, always on."""
    off = set(O.load(c)["answers"].get("teams_off") or [])
    chart = default_chart(onboarding, c, home, every_team=True)
    teams = {}
    for key, seat in chart.items():
        team = teams.setdefault(
            seat["team"], {"team": seat["team"], "on": seat["team"] not in off, "locked": True, "roles": []}
        )
        team["locked"] = team["locked"] and bool(seat.get("fills"))
        boss = chart.get(seat["reports_to"])
        team["roles"].append(
            {
                "id": key,
                "title": seat["title"],
                "live": seat["live"] or bool(seat.get("fills")),
                "reports_to": seat["reports_to"] if boss and boss["team"] == seat["team"] else None,
            }
        )
    for team in teams.values():
        team["on"] = team["on"] or team["locked"]
    return list(teams.values())


def add_invitee(onboarding, c, who, key, email):
    """One person the imported file names, now on the roster with the email the owner typed (and on the sign-in allow
    list); the seats that waited with the owner report to them from now on. Their roster row, the same one again when
    they were already added (Invite sends again)."""
    from . import access as Access
    from . import views

    if who.role != "owner":
        raise Problem("forbidden", "Only the owner invites the people from setup", 403)
    record = O.load(c)
    imported = record.get("company_import") or {}
    people = list(imported.get("people") or [])
    at = next((i for i, person in enumerate(people) if person.get("id") == key), None)
    if at is None:
        raise Problem("not_found", "That person isn't in the company file from setup", 404)
    entry = people[at]
    roster = views.roster(c)
    if entry.get("person"):
        row = next((p for p in roster["people"] if p["id"] == entry["person"] and not p.get("hidden")), None)
        if row:
            return row
    _, row = Access.add_person(c, who.actor, roster, name=entry["name"], email=email, title=entry.get("title") or "")
    home = "human:" + onboarding.auth.owner_id(c)
    for seat_id in entry.get("seats") or []:  # a seat someone has moved since stays where they put it
        seat = c.execute("SELECT reports_to FROM seats WHERE id=? AND closed_at IS NULL", (seat_id,)).fetchone()
        if seat and seat["reports_to"] == home:
            c.execute(
                "UPDATE seats SET reports_to=?,updated_at=?,updated_by=? WHERE id=?",
                ("human:" + row["id"], H.now(), who.actor, seat_id),
            )
            H.event(c, who.actor, "seat.updated", seat_id, {"fields": ["reports_to"]})
    people[at] = {**entry, "person": row["id"]}
    record["company_import"] = {**imported, "people": people}
    onboarding._store(c, record, who.actor)
    return row


def load_on_start(onboarding, c):
    """The installer-configured plan is applied before any generic sample company.

    A company whose setup is complete is left alone: no plan replaces its team, and nothing is kept against it. Until
    then, an error raises, and the caller (`start`) keeps it for the owner to see instead of stopping the server or
    installing a different team."""
    settings = onboarding.settings
    if O.load(c).get("completed"):
        # The team is in, and no file replaces it: a start never shows a company file failure to a set-up company (an
        # owner who uploaded a fixed file after a failed start would otherwise see the old file's failure again).
        clear_failure(c)
        return None
    if settings.company_file_error:
        raise Problem("company_file", settings.company_file_error, 422)
    if not settings.company_file_data.strip():
        raise Problem("company_file", "The company file setup gave this server is empty", 422)
    draft = starter.unpack(settings.company_file_data)
    if (O.load(c).get("company_import") or {}).get("fingerprint") == hashlib.sha256(encode(draft).encode()).hexdigest():
        clear_failure(c)  # a file fixed back to the one already applied ends an earlier start's failure
        return None
    who = onboarding.auth.owner_identity(c)
    summary = preview(onboarding, c, who, draft)
    return apply(onboarding, c, who, draft, summary["fingerprint"])


def start(onboarding, store, telemetry=None):
    """Apply the setup's company file on server start (spec test 2). A file that can't be applied leaves nothing of
    itself behind (its transaction rolls back), never puts a generic sample company in its place, and is kept as
    `onboarding.company_import_failed` for the owner to see on first run and on Health; the server still starts, with the
    built-ins only. A later import that works clears it."""
    try:
        with store.transaction() as c:
            load_on_start(onboarding, c)
        return None
    except Exception as exc:
        if isinstance(exc, Problem):
            reason = str(exc.detail)
            log.warning("Company file from setup not imported: %s", reason)
        elif isinstance(exc, C.DraftError):
            # The owner reads the plain words (a role by its title); the log keeps the field the check refused.
            reason = exc.plain
            log.warning("Company file from setup not imported: %s", exc)
            if telemetry:
                telemetry.capture("company_import", exc)
        else:
            reason = "Tico couldn't read or apply it"
            log.exception("Company file from setup not imported")
            if telemetry:
                telemetry.capture("company_import", exc)
        failure = {"at": H.now(), "reason": reason[:500]}
        with store.transaction() as c:
            c.execute(
                "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (FAILED_KEY, encode(failure)),
            )
            H.event(c, "system", "onboarding.company_import_failed", "", {"reason": failure["reason"]})
        return failure


def clear_failure(c, actor=""):
    """End a kept start failure: an import that works, the applied file again, the file setting removed, a team set up
    on the welcome page, or the owner dismissing it. An actor records who dismissed it."""
    gone = c.execute("DELETE FROM registry_metadata WHERE key=?", (FAILED_KEY,)).rowcount
    if gone and actor:
        H.event(c, actor, "onboarding.company_import_failure_dismissed", "", {})
    return bool(gone)


def forget(store):
    """No company file is configured any more, so a failure kept from one that was is no longer true (app startup)."""
    with store.transaction() as c:
        clear_failure(c)


def dismiss(onboarding, c, who):
    """The owner's way out of a kept failure they will not fix by import (they will set the team up by hand, say): Health
    and the welcome page stop showing it. A server start with the same file records it again while setup is not
    complete."""
    onboarding._owner(who)
    clear_failure(c, who.actor)
    return onboarding.view(c, who)


def failure(c):
    """Why the setup's company file wasn't imported on server start, or None (`start`)."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (FAILED_KEY,)).fetchone()
    try:
        value = json.loads(row[0]) if row else None
    except ValueError:
        value = None
    return value if isinstance(value, dict) else None
