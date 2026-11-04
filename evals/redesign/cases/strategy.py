"""Strategy scenarios 36-38 (PLAN section 7, wave 6 · strategy content).

36: a listening match reaches the market graph only through the Librarian's turn, never by a direct write.
37: the daily briefing fires at 07:00 on the fake clock and posts to Market and Updates.
38: a reorg is no proposal: a bot's org approval is refused, and the owner moves the bot herself, audited as hers."""
import json
from datetime import datetime

from ..harness import (CLAUDE_ID, DANA, LEAD, OPENAI_ID, OPENAI_REF, RELEASE_ID, W2, W3, W6, Check,
                       Scenario, Unmet, accepted, audit_of, auto_id, automation, body, got, make_group, rows, setup)


def canon(value):
    """A stable text form of a JSON value, for "changed" and "names" checks."""
    return json.dumps(value, sort_keys=True, default=str)


# ---------------------------------------------------------------------------------------------- steps
def put_destination(org):
    """The Market listening destination whose matches go to the Librarian (CONTRACT §2: receiver stays Librarian)."""
    return org.api("put", "/api/v2/listening/destinations/ai-tools",
                   {"category": "lead", "threshold": 0.5, "receiver": "bot:librarian", "readers": [],
                    "what": "Market watch for AI dev tools"}, who=DANA, wave=W6)


def listen(org):
    """One listening run with one match, saved the way the listening runner saves it (the owner may save runs)."""
    from backend.store import H
    return org.api("post", "/api/v2/listening/runs", {
        "source": "news", "query": "AI dev tools", "status": "ok", "started_at": H.now(), "pages_read": 1,
        "items_seen": 1, "note": "",
        "items": [{"native_id": "n-1", "url": "https://news.acme.example/dev-tools", "author": "Acme News",
                   "content": "A new dev tool for mapping markets."}]}, who=DANA, wave=W6)


def judge_match(org, facts):
    """The match's judgment, saved as the listening judge saves it (POST /api/v2/listening/judgments): a lead score
    over the destination's threshold routes the item, and the routing is what starts a listening trigger."""
    items = (body(facts, "match") or {}).get("items") or []
    if not items:
        raise Unmet(W6, "the listening run saved no item to judge")
    return org.api("post", "/api/v2/listening/judgments", {"judgments": [
        {"item_id": items[0]["id"], "question_set": "listening-item@1", "scores": {"lead": 0.9}}]}, who=DANA, wave=W6)


def graph(org):
    return org.api("get", "/api/v2/market/graph", who=DANA, wave=W6)


def runs_of(org, facts, key):
    return org.api("get", f"/api/v2/automations/{auto_id(facts, key)}/runs", who=DANA, wave=W3)


def read_auto(org, facts, key):
    return org.api("get", f"/api/v2/automations/{auto_id(facts, key)}", who=DANA, wave=W3)


def advance_to_next_run(org, facts):
    """Move the fake clock to the automation's next_run (UTC ISO) and let the scheduler run it."""
    data = body(facts, "read")
    if not isinstance(data, dict) or not data.get("next_run"):
        raise Unmet(W3, "GET /{id} carries no next_run for a schedule")
    due = datetime.fromisoformat(data["next_run"].replace("Z", "+00:00"))
    now = org.now()   # the fake clock, not the wall clock
    if due > now:
        org.pass_time(due - now)
    return due


def propose_move(org, facts):
    """The Engineering Lead tries to file moving the OpenAI builder from Builders to Release as an approval: org changes
    are no proposal any more (the Strategy Bot suggests them in words), so the hub refuses it."""
    return org.api("post", "/api/v2/approvals", {
        "kind": "org", "payload": {"bot": OPENAI_REF, "from": got(facts, "builders"), "to": got(facts, "release")}},
        who=LEAD, wave=W2)


def move_by_hand(org, facts):
    """Dana, the owner, moves it herself on the Org tab: one group change, no approval."""
    return org.api("patch", f"/api/v2/groups/{got(facts, 'release')}", {"add": {"bots": [OPENAI_ID]}}, who=DANA, wave=W2)


def audit_group(org, facts):
    return audit_of(org, obj=f"group:{got(facts, 'builders')}", who=DANA)


# ---------------------------------------------------------------------------------------------- 36
def s36_steps(org, facts):
    org.step(facts, "dest", put_destination, org)
    org.step(facts, "auto", automation, org, "Market · AI dev tools", "bot:librarian",
             {"type": "listening", "destination": "ai-tools"},
             [{"id": "update", "type": "bot_turn", "enabled": True, "bot": "librarian",
               "prompt": "Update the market graph from this match."}])
    org.step(facts, "graph0", graph, org)
    org.step(facts, "match", listen, org)
    org.step(facts, "judged", judge_match, org, facts)
    org.step(facts, "graph1", graph, org)
    org.step(facts, "runs", runs_of, org, facts, "auto")
    org.step(facts, "turn", org.turn, "librarian", act=lambda token, attempt: curate(org, token))
    org.step(facts, "graph2", graph, org)


def curate(org, token):
    """The Librarian's turn on the match, through the market routes as the Librarian: the post as evidence, then the
    product it names, citing it. The url stays on the evidence; the graph names the match only if the hub says so."""
    seen = org.api("post", "/api/v2/market/evidence", {
        "source_url": "https://news.acme.example/dev-tools", "source_kind": "news",
        "quote": "A new dev tool for mapping markets."}, who=token, wave=W6)
    evidence = ((seen.body or {}).get("evidence") or {}).get("id") if isinstance(seen.body, dict) else None
    if not evidence:
        raise Unmet(W6, f"the Librarian could not file the evidence: {seen.status} {seen.excerpt()}")
    return org.api("post", "/api/v2/market/entities", {
        "type": "product", "name": "Acme Mapper", "summary": "A dev tool for mapping markets.",
        "evidence_ids": [evidence]}, who=token, wave=W6)


def match_started_run(facts):
    setup(facts, "auto", "runs", "match")
    runs = rows(body(facts, "runs"))
    hit = [run for run in runs if "listening" in canon(run.get("trigger_ref", ""))]
    return bool(hit), f"{len(runs)} runs; none from the listening trigger" if not hit else "run from the listening match"


def librarian_turn_ran(facts):
    turn = got(facts, "turn")
    return turn is not None, "the Librarian's turn ran" if turn else "the Librarian's turn was held or not claimed"


def no_direct_write(facts):
    ok, text = match_started_run(facts)   # a graph that stays put proves nothing unless the match did run
    if not ok:
        raise Unmet(W3, text)
    before, after = got(facts, "graph0"), got(facts, "graph1")
    setup(facts, "graph0", "graph1")
    same = canon(before.body) == canon(after.body)
    return same, "the match alone changed the graph" if not same else "graph unchanged until the turn"


def graph_updated_by_turn(facts):
    ok, text = librarian_turn_ran(facts)
    if not ok:
        raise Unmet(W3, text)
    setup(facts, "graph1", "graph2")
    before, after = facts["graph1"].body, facts["graph2"].body
    return canon(before) != canon(after), "the Librarian's turn left the graph unchanged"


def graph_names_turn(facts):
    ok, text = librarian_turn_ran(facts)
    if not ok:
        raise Unmet(W3, text)
    setup(facts, "graph2")
    text = canon(facts["graph2"].body)
    return "librarian" in text and "news.acme.example" in text, \
        "the graph does not name the Librarian's turn and the match"


# ---------------------------------------------------------------------------------------------- 37
BRIEFING = "# Acme market, Tuesday\n\n- Acme Mapper launched a dev tool for mapping markets.\n- No bet is touched."


def s37_steps(org, facts):
    org.step(facts, "auto", automation, org, "Daily briefing", "bot:librarian",
             {"type": "schedule", "cron": "0 7 * * *", "tz": "UTC"},
             [{"id": "brief", "type": "bot_turn", "enabled": True, "bot": "librarian",
               "prompt": "Write today's market briefing from the graph."},
              {"id": "post", "type": "action", "enabled": True, "do": "post_update",
               "args": {"to": ["market", "updates"]}}])
    org.step(facts, "read", read_auto, org, facts, "auto")
    org.step(facts, "fired", advance_to_next_run, org, facts)
    org.step(facts, "brief", org.turn, "librarian", text=BRIEFING)
    org.step(facts, "runs", runs_of, org, facts, "auto")
    org.step(facts, "market", org.api, "get", "/api/v2/market/briefings/latest", who=DANA, wave=W6)
    org.step(facts, "updates", org.api, "get", "/api/v2/updates", who=DANA, wave=W6)


def fires_at_seven(facts):
    due = got(facts, "fired")
    return due.hour == 7 and due.minute == 0, f"next run at {due.isoformat()}"


def run_on_schedule(facts):
    setup(facts, "auto", "runs")
    return bool(rows(body(facts, "runs"))), "no run logged for the 07:00 schedule"


def briefing_ran(facts):
    turn = got(facts, "brief")
    return turn is not None, "the briefing turn was held or not claimed"


def posted_to_market(facts):
    setup(facts, "market")
    data = body(facts, "market")
    return bool(data) and auto_id(facts, "auto") in canon(data), \
        "Market has no briefing from this automation"


def posted_to_updates(facts):
    setup(facts, "updates")
    return auto_id(facts, "auto") in canon(body(facts, "updates")), \
        "Updates has no post from this automation"


# ---------------------------------------------------------------------------------------------- 38
def s38_steps(org, facts):
    org.step(facts, "builders", make_group, org, "Builders", [CLAUDE_ID, OPENAI_ID])
    org.step(facts, "release", make_group, org, "Release", [RELEASE_ID])
    org.step(facts, "proposal", propose_move, org, facts)
    org.step(facts, "moved", move_by_hand, org, facts)
    org.step(facts, "groups", org.api, "get", "/api/v2/groups", who=DANA, wave=W2)
    org.step(facts, "audit", audit_group, org, facts)


def proposal_refused(facts):
    got(facts, "builders")
    got(facts, "release")
    reply = got(facts, "proposal")
    return reply.status == 422, f"an org approval got {reply.status}, not 422"


def moved_by_hand(facts):
    setup(facts, "moved", "groups")
    groups = rows(body(facts, "groups"))
    release = got(facts, "release")
    hit = [g for g in groups if g.get("id") == release and OPENAI_ID in canon(g)]
    return bool(hit), f"{OPENAI_ID} is not in Release after Dana's move"


def audited_as_dana(facts):
    setup(facts, "audit", "moved")
    hit = [row for row in rows(body(facts, "audit")) if not row.get("via") and "dana" in canon(row.get("actor"))]
    return bool(hit), "no audit row by Dana, with no approval, for the group change"


# ---------------------------------------------------------------------------------------------- scenarios
SCENARIOS = [
    Scenario(36, "strategy-listening-librarian",
             "A listening match reaches the market graph only through the Librarian's turn", s36_steps, [
                 Check("listening destination saved, receiver Librarian", W6,
                       lambda facts: accepted(facts, "dest")),
                 Check("the match started the Librarian's automation run", W3, match_started_run),
                 Check("the Librarian's turn ran on the match", W3, librarian_turn_ran),
                 Check("no direct graph write from the match", W6, no_direct_write),
                 Check("the Librarian's turn updated the market graph", W6, graph_updated_by_turn),
                 Check("the graph names the Librarian's turn and the match", W6, graph_names_turn),
             ], bots={"librarian": ("librarian", "claude")}),
    Scenario(37, "strategy-daily-briefing",
             "The daily briefing fires at 07:00 on the fake clock and posts to Market and Updates", s37_steps, [
                 Check("the schedule's next run is 07:00", W3, fires_at_seven),
                 Check("the 07:00 schedule logged a run", W3, run_on_schedule),
                 Check("the Librarian wrote the briefing", W6, briefing_ran),
                 Check("the briefing is posted to Market", W6, posted_to_market),
                 Check("the briefing is posted to Updates", W6, posted_to_updates),
             ], bots={"librarian": ("librarian", "claude")}),
    Scenario(38, "strategy-reorg-by-hand",
             "A reorg is no proposal: a bot's org approval is refused and the owner moves the bot herself", s38_steps, [
                 Check("a bot's org move is refused as a proposal", W2, proposal_refused),
                 Check("Dana's move is accepted", W2, lambda facts: accepted(facts, "moved")),
                 Check("the bot is in Release after Dana's move", W2, moved_by_hand),
                 Check("the group change is audited as Dana's, with no approval", W2, audited_as_dana),
             ]),
]
