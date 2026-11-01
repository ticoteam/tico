"""Named seeds for the real-hub browser server (ui/tests/support/real_hub_server.py, --seed). A seed takes the world the
server runs (harness.Org) and makes one piece of state a browser test starts from, through the API a teammate calls.
Seeds run in the order given. A seed returns False when it skipped itself because the route it needs is not built yet;
otherwise it returns None."""
import json

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


PAGE_HTML = ('<!doctype html><html><head><title>{title}</title></head><body style="margin:0;font:15px system-ui">'
             '<h1 style="margin:16px">{title}</h1><p style="margin:16px">{text}</p>'
             '<svg viewBox="0 0 300 80" width="300" height="80"><rect x="16" y="10" width="{bar}" height="40" fill="#4a7"/>'
             '</svg><script>steal()</script></body></html>')


@seed("pages")
def pages(world):
    """The Engineering Lead's pages and files, published during one turn on Dana's task "Q3 launch": an older weekly
    page filed on the task and a bot-wide weekly page that replaced it (data from Oct 27), a launch plan filed on the
    task, and a signup funnel CSV bot-wide, so Library › Files has pages, data and a search hit inside a CSV. A page
    filed on the task is pinned to its top; the replaced one is left out there. The bot is named "Engineering Lead",
    so the status lines name it, not its slug."""
    from .harness import DANA
    # Written as the harness writes the bots' other fields: the definition route refuses the reports_to it set by hand.
    with world.store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Engineering Lead' WHERE slug='engineering-lead'")
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='engineering-lead'").fetchone()[0]
                            or "{}")
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='engineering-lead'",
                  (json.dumps({**config, "display_name": "Engineering Lead"}),))
    made = _ok(world.api("post", "/api/v2/tasks", {"title": "Q3 launch", "body": "Plan the Q3 launch.",
                                                    "owner": "engineering-lead"}, who=DANA), "task")
    task = (made.get("task") or made)["id"]

    def act(token, attempt):
        def page(name, title, text, bar, **fields):
            body = {"name": name, "title": title, "text": PAGE_HTML.format(title=title, text=text, bar=bar), **fields}
            return _ok(world.api("post", "/api/v2/pages", body, who=token), name)["page"]["file_id"]
        old = page("weekly-old.html", "Engineering weekly (old)", "Last month's numbers.", 90, task=task,
                   cadence="weekly")
        page("weekly.html", "Engineering weekly", "Shipped 14 changes; review time fell to 6 hours.", 240,
             scope="bot", cadence="weekly", as_of="2026-10-27", replaces=old)
        page("launch.html", "Launch plan", "Beta on Nov 3, general release on Nov 17.", 160, task=task)
        _ok(world.api("post", "/api/v2/files/uploads", {
            "name": "funnel.csv", "title": "Signup funnel", "scope": "bot",
            "text": "stage,visitors\nlanding,1200\nsignup,310\nactivated,140\n"}, who=token), "funnel.csv")
    if world.turn("engineering-lead", act=act) is None:
        raise RuntimeError("the Engineering Lead's turn did not run")


COUNSEL = "counsel-test"
VENDOR_TERMS = ("Northwind Messaging Terms\n\n4. Messaging\n\nCustomer messages must include an opt-out line in every "
                "send. Keep a record of each consent for five years.\n\n5. Data\n\nDelete raw logs after 30 days.\n")


@seed("operations")
def operations(world):
    """Finance set up, one customer and Acme's shared records, written as Dana through POST /api/v2/finance/actions: two
    locations, a service, a project, two pieces of equipment and due dates (Operations, ui/tests/operations-real.cjs)."""
    from datetime import date, timedelta
    from backend.finance import actions as FA
    from backend.store import H
    with world.store.transaction() as c:
        FA.setup(c, "human:dana")

    def act(kind, **payload):
        reply = _ok(world.api("post", "/api/v2/finance/actions", {"kind": kind, "payload": payload}), kind)
        return reply.get("result") or {}
    soon = (date.fromisoformat(H.now()[:10]) + timedelta(days=40)).isoformat()
    customer = act("contact_create", name="Beta Foods", kind="customer")["contact_id"]
    act("location_create", name="Acme North Store", kind="store", in_books=False,
        address={"street": "1 Main St", "city": "Portland", "state": "OR", "postal_code": "97201"})
    site = act("location_create", name="Beta Foods Site", kind="customer_site", customer_id=customer,
               address={"city": "Bend", "state": "OR"})["location_id"]
    act("item_create", name="Filter change", kind="service", sku="SVC-10", unit="each", price=8900,
        description="Swap and dispose", taxable=True)
    act("item_create", name="Air filter 20x20", kind="non_inventory", sku="PRT-20")
    act("project_create", name="North Store refit", customer_id=customer, status="open")
    act("project_create", name="Old refit", status="closed")
    unit = act("equipment_create", name="Rooftop unit 3", kind="customer", customer_id=customer, location_id=site,
               make="Trane", model="XR-16", serial="SN-4471", warranty_ends_on=soon)["equipment_id"]
    act("equipment_create", name="Walk-in cooler", kind="owned", make="Hussmann", serial="HU-88")
    act("due_date_create", name="Filter change", subject_type="equipment", subject_id=unit, repeats="quarterly",
        starts_on="2020-01-01", lead_days=14)
    act("due_date_create", name="Fire inspection", subject_type="location", subject_id=site, repeats="yearly",
        starts_on="2099-01-01", lead_days=30)


@seed("counsel")
def counsel(world):
    """Counsel on, as onboarding turns it on (its bot row and config, and a test token, "tico-counsel"), and a vendor's
    terms in Files, uploaded by Dana under the Engineering Lead, for Rules › External › Add document. No model runs: a
    test claims Counsel's run on its read task with the hub's runner token (real_hub_server.py prints it) and writes
    its suggestions itself, through the API as that run."""
    from backend.auth import Identity
    from backend.blobs import register
    from backend.store import H
    from .harness import DANA, DANA_ID
    with world.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO bots(slug, display_name, state) VALUES(?, 'Counsel', 'active')", (H.COUNSEL,))
        c.execute("INSERT OR REPLACE INTO bot_config(bot, config_json, team, operator) VALUES(?, '{}', '', ?)",
                  (H.COUNSEL, DANA_ID))
        c.execute("INSERT OR REPLACE INTO agents(bot, harness, token_hash, created, created_by) VALUES(?,?,?,?,?)",
                  (H.COUNSEL, "test", "x-counsel", H.now(), "human:" + DANA_ID))
        data = VENDOR_TERMS.encode("utf-8")
        digest = world.app.state.blobs.put(data, "text/plain")
        item = register(c, Identity("human:" + DANA_ID, "owner"), digest, len(data), "northwind-terms.txt", "text/plain")
        world.app.state.files.add_version(
            c, bot="engineering-lead", actor="human:" + DANA_ID, scope="bot", task=None, conversation=None, attempt="",
            identity="upload:northwind-terms", title="Northwind Messaging Terms", name="northwind-terms.txt",
            mime="text/plain", blob_id=item["id"], digest=digest, size=len(data))
    world.app.state.auth.settings.test_identities[COUNSEL] = Identity("bot:" + H.COUNSEL, "bot", agent="test")
    # Counsel on the world's computer, ready, so a read task's run can be claimed and started.
    _ok(world.api("post", f"/api/v2/bots/{H.COUNSEL}/assignment",
                  {"runner_id": world.runner["runner_id"], "expected_generation": 0}, who=DANA), "Counsel assignment")
    _ok(world.api("post", "/api/v2/runners/heartbeat", {
        "version": "test", "platform": "test",
        "readiness": {slug: True for slug in [*world.bot_slugs, H.COUNSEL]}}, who=world.runner["token"]), "heartbeat")
