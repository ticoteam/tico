"""Named seeds for the real-hub browser server (ui/tests/support/real_hub_server.py, --seed). A seed takes the world the
server runs (harness.Org) and makes one piece of state a browser test starts from, through the API a teammate calls.
Seeds run in the order given. A seed returns False when it skipped itself because the route it needs is not built yet;
otherwise it returns None."""
SEEDS = {}


def seed(name):
    def register(fn):
        SEEDS[name] = fn
        return fn
    return register


@seed("org")
def org(world):
    """The scenario org: its people, bots and reports_to are built when the world starts, so nothing is added here."""


# ---------------------------------------------------------------------------------------------- the e2e journeys' world
# The seeds below build what the redesign's browser journeys (ui/tests/redesign-e2e-*.cjs) start from, through the
# routes a teammate calls, in the scenario harness's helpers.
def _ok(reply, what):
    if not 200 <= reply.status < 300:
        raise RuntimeError(f"{what}: {reply.status} {reply.excerpt()}")
    return reply.body


class SteadyJudge:
    """app.state.judge for a browser test: every question answered at once and the same way (a choice's first option at
    82%, a yes/no 70%, a score in the middle), so a run's Decide outputs are real answers whatever questions a journey
    wrote. Same call shape as the scenarios' ReplayJudge."""

    def __init__(self):
        self.calls = []

    def __call__(self, state, questions, label=None, level=None):
        self.calls.append({"state": state, "questions": questions, "label": label, "level": level})
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "choice":
                first = "reply" if "reply" in q["criteria"] else next(iter(q["criteria"]))
                rest = [k for k in q["criteria"] if k != first]
                answers[qid] = {"type": "choice", "choice": first, "confidence": 0.82,
                                "probabilities": {first: 0.82, **{k: round(0.18 / len(rest), 2) for k in rest}}}
            elif q["type"] == "score":
                answers[qid] = {"type": "score", "score": (len(q["criteria"]) - 1) // 2, "confidence": 0.8}
            else:
                answers[qid] = {"type": "noul", "noul": 0.7}
        return {"model": "steady", "usage": {"input_tokens": 0, "output_tokens": 0}, "ms": 0, "answers": answers}


BUCKET = {"bucket": {"type": "choice", "instructions": "Reply, or archive as noise?",
                     "criteria": {"reply": "needs an answer", "archive": "noise"}}}


def _node(sid, kind, **fields):
    return {"id": sid, "type": kind, "enabled": True, **fields}


def _cond(field, op, value, **extra):
    return {"field": field, "op": op, "value": value, **extra}


@seed("automations")
def automations(world):
    """Dana's and Priya's automations, and a judge that answers every Decide (SteadyJudge):
    - Inbox triage (Dana's inbox): Filter (promo mail archived), Decide on the bucket, a note in the run log.
    - Route mail (Dana's inbox): Decide, then Paths "Reply" and "Archive" on the bucket, and Otherwise.
    - Weekly digest (Dana, a schedule): no steps yet, the journeys build it.
    - Standup notes (Priya's own, a schedule), made by Priya."""
    from .harness import DANA, DANA_REF, PRIYA, PRIYA_REF, automation
    world.app.state.judge = SteadyJudge()
    inbox = {"type": "message", "inbox": "dana@acme.example"}
    _ok(automation(world, "Inbox triage", DANA_REF, trigger=inbox, steps=[
        _node("sort", "filter", rules=[{"when": [_cond("message.from", "contains", "promo")], "match": "all",
                                        "then": [{"do": "archive", "args": {}}], "stop": True}]),
        _node("rank", "decide", questions=BUCKET, level="low", out="d1"),
        _node("tell", "action", do="note", args={"text": "Triaged"})]), "Inbox triage")
    _ok(automation(world, "Route mail", DANA_REF, trigger=inbox, steps=[
        _node("rank", "decide", questions=BUCKET, level="low", out="d1"),
        _node("fork", "paths", paths=[
            {"name": "Reply", "when": [_cond("d1.bucket", "is", "reply", min=0.7)],
             "steps": [_node("r", "action", do="note", args={"text": "Reply"})]},
            {"name": "Archive", "when": [_cond("d1.bucket", "is", "archive", min=0.7)],
             "steps": [_node("a", "action", do="note", args={"text": "Archive"})]}],
            otherwise={"steps": [_node("o", "action", do="note", args={"text": "Unsure"})]})]), "Route mail")
    # Not through automation(): it puts an empty Filter in a flow given no steps, and this one starts empty.
    _ok(world.api("post", "/api/v2/automations", {
        "name": "Weekly digest", "owner": DANA_REF, "enabled": True, "builtin": False,
        "trigger": {"type": "schedule", "cron": "0 8 * * 1", "tz": "UTC"}, "steps": []}, who=DANA), "Weekly digest")
    _ok(automation(world, "Standup notes", PRIYA_REF, who=PRIYA,
                   trigger={"type": "schedule", "cron": "0 9 * * 1-5", "tz": "UTC"},
                   steps=[_node("tell", "action", do="note", args={"text": "Standup"})]), "Standup notes")


@seed("bot_proposal")
def bot_proposal(world):
    """A bot's proposal on Inbox triage (after `automations`): a pending version that archives receipts first, written
    as BotOps writes it when Builder Claude asks (Builder Claude's rights, which do not reach Dana's automation, so the
    PATCH is a proposal). No model runs."""
    from .harness import CLAUDE, DANA, rows
    listed = _ok(world.api("get", "/api/v2/automations", who=DANA), "list")
    auto = next(a for a in rows(listed) if a.get("name") == "Inbox triage")
    full = _ok(world.api("get", f"/api/v2/automations/{auto['id']}", who=DANA), "read")
    steps = [dict(s) for s in full["steps"]]
    for step in steps:
        if step["type"] == "decide" and step.get("set"):
            step.pop("questions", None)
    receipts = {"when": [_cond("message.subject", "contains", "receipt")], "match": "all",
                "then": [{"do": "archive", "args": {}}], "stop": True}
    steps[0] = {**steps[0], "rules": [receipts, *steps[0]["rules"]]}
    _ok(world.api("patch", f"/api/v2/automations/{auto['id']}", {"steps": steps, "summary": "Archive receipts first"},
                  who=CLAUDE), "proposal")


@seed("groups")
def groups(world):
    """Two groups on the org chart, as Dana makes them: Engineering (Omar leads it: the Engineering Lead bot, the two
    builders and Omar) and Release. Omar moves teammates inside Engineering; a move to Release is Dana's to approve."""
    from .harness import DANA, OMAR_ID, OMAR_REF, make_group
    _ok(world.api("post", "/api/v2/groups", {
        "name": "Engineering", "lead": OMAR_REF,
        "add": {"bots": ["engineering-lead", "builder-claude", "builder-openai"], "people": [OMAR_ID]}}, who=DANA), "Engineering")
    make_group(world, "Release", bots=["release-manager"])


@seed("connection")
def connection(world):
    """A tool the Engineering Lead's computer reports (the runner's heartbeat): Acme Billing, read and refund, so
    Library › Tools lists it in use, with writes."""
    row = {"ready": True, "runtime": "claude", "model": "claude-opus-5-5", "repository_present": True,
           "configuration_valid": True, "problems": []}
    bots = {slug: {**row, **({"tools": [{"service": "acme-billing", "identity": "Acme billing account",
                                         "can": ["read", "refund"]}]} if slug == "engineering-lead" else {})}
            for slug in world.bot_slugs}
    _ok(world.api("post", "/api/v2/runners/heartbeat", {"version": "test", "platform": "test",
                                                        "readiness": {"schema_version": 1, "bots": bots}},
                  who=world.runner["token"]), "heartbeat")


@seed("toured")
def toured(world):
    """Dana, Omar and Priya have seen the first-visit tour and Dana dismissed the usage-count notice, so a journey's
    page opens on the app, with nothing over it."""
    from .harness import DANA, OMAR, PRIYA
    for who in (DANA, OMAR, PRIYA):
        _ok(world.api("post", "/api/v2/setup/getting-started/state", {"tour": True}, who=who), "tour seen")
    _ok(world.api("post", "/api/v2/system/usage-count/notice", {"state": "dismissed"}, who=DANA), "usage notice")
