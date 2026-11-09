"""Product hands approved specs to Engineering as subtasks (decision D64: spawn as subtask, not a `move` link).

The world has two pipelines: the sim's board is Engineering, and a Product pipeline is built beside it (Scenario
`pipelines`). Everything is set up through the API, as a pipeline's owner would: Product's stages, its three
requirements ("Spec approved" on Review, ana's; "Engineering shipped" on In development, met when its Engineering
subtasks ship; "Validated" on Validate, ana's), and a spawn link from Product's Review to Engineering.

Five Product cards wait at Review. ana works her review queue every tick: she approves each spec, and posts the first
few Approves a second time at once (a double click), which the server refuses. Leaving Review files one Engineering
ticket as a subtask of the card, in Engineering's first stage, and the card stays at In development. The first card to
get there is sent back to Review by ana and moved on again: its handoff is live, so leaving Review a second time files
nothing. The builders take the Engineering tickets through the board as any other; the Release Manager ships every hour,
and once a card's subtasks are done the card moves itself to Validate, where ana approves it to Done.

The checks: one Engineering subtask and one handoff per approved spec, the second Approve refused, the bounced card
filing nothing new, no card past In development before its subtasks ship (watched every tick), a card whose subtasks
shipped moving on, at least one card Done, and no call with a server error."""

from evals.factory.scenarios import BASE, Scenario

GATE = "pipeline links"
CARDS = 5
DOUBLE_CLICKS = 3  # her first few Approves she posts twice
PRODUCT = {
    "name": "Product",
    # Public, so the spawned ticket carries the card's title and spec (a private source's subtask says "New request").
    "default_visibility": "public",
    "steps": [
        {"name": "Ideas", "status": "open"},
        {"name": "Review", "status": "review"},
        {"name": "In development", "status": "doing"},
        {"name": "Validate", "status": "review"},
        {"name": "Done", "status": "done"},
        {"name": "Declined", "status": "closed"},
    ],
}
REQUIREMENTS = (
    ("Review", {"name": "Spec approved", "kind": "approval", "reviewed_by": "person", "reviewer_id": "human:ana"}),
    (
        "In development",
        # needs.pipeline is filled in setup: the subtasks that count are the Engineering pipeline's (the sim's board).
        {"name": "Engineering shipped", "kind": "run", "needs": {"source": "subtasks"}, "reviewed_by": "nobody"},
    ),
    ("Validate", {"name": "Validated", "kind": "approval", "reviewed_by": "person", "reviewer_id": "human:ana"}),
)
PAST_DEVELOPMENT = ("Validate", "Done")
APPROVED_AT = {"Spec approved": "Review", "Validated": "Validate"}  # the stage ana approves each at


def setup(run, w, team):
    product = w.pipelines["Product"]
    run.product = product["id"]
    run.requirements = {}
    for stage, rule in REQUIREMENTS:
        if rule["kind"] == "run":
            rule = {**rule, "needs": {**rule["needs"], "pipeline": w.dev["id"]}}
        made = w.post(f"pipeline-stages/{w.stage_id('Product', stage)}/requirements", rule)
        run.requirements[rule["name"]] = made["id"]
    run.link = w.post(
        "pipeline-links",
        {"from_stage_id": w.stage_id("Product", "Review"), "to_pipeline_id": w.dev["id"], "mode": "spawn"},
    )["link"]["id"]
    run.cards = []
    for n in range(CARDS):
        made = w.post(
            "tasks",
            {
                "owner": "ana",
                "title": f"Spec {n + 1}",
                "body": "The spec: what to build and how we'll know it worked.",
                "type": product["id"],
                "step": "Review",
            },
        )
        run.cards.append(made.get("task", made)["id"])
    run.handoff = {
        "approved": set(),  # cards whose spec ana approved
        "again": [],  # (card, status, code) of each second Approve she posted at once
        "bounced": None,  # the card ana sent back to Review after its spawn, and moved on again
        "bounce": [],  # (step, status, code) of her moves on it
        "early": [],  # (when, card, stage, unshipped) seen at a tick's start: a card past In development too soon
        "late": [],  # (when, card) seen at In development a tick after every subtask shipped
        "shipped_at": {},  # card -> when all its subtasks were first seen shipped
    }


def path(task, requirement, verb):
    return f"pipelines/tasks/{task}/requirements/{requirement}/{verb}"


def body(item):
    """What the queue item tells the reviewer: the work, the attachment and the requirement version they saw."""
    return {
        "work_version": item["work_version"],
        "attachment_digest": item["attachment_digest"],
        "requirement_version": item["requirement_version"],
    }


def stage_of(w, task):
    found = w.sql("SELECT s.name FROM tasks t JOIN task_steps s ON s.id=t.step_id WHERE t.id=?", (task,))
    return found[0]["name"] if found else None


def subtasks(w, card):
    """The card's Engineering subtasks, with their status."""
    return w.sql(
        "SELECT t.id, t.status FROM tasks t JOIN task_relations r ON r.from_task=t.id AND r.kind='parent' "
        "WHERE r.to_task=? AND t.type_id=? ORDER BY t.created_at, t.id",
        (card, w.dev["id"]),
    )


def handoffs(w, card):
    return w.sql("SELECT target_task_id FROM pipeline_handoffs WHERE source_task_id=?", (card,))


def code_of(reply):
    return (reply.get("error") or {}).get("code") if isinstance(reply, dict) else None


def watch(run, w):
    """The cards as they are at the tick's start: none is past In development while a subtask is unshipped, and none
    still sits at In development a tick after its subtasks all shipped."""
    now, seen = w.now(), run.handoff
    for card in run.cards:
        stage, children = stage_of(w, card), subtasks(w, card)
        unshipped = [c["id"] for c in children if c["status"] != "done"]
        if stage in PAST_DEVELOPMENT and (unshipped or not children):
            seen["early"].append((now, card, stage, len(unshipped)))
        if children and not unshipped:
            seen["shipped_at"].setdefault(card, now)
            if stage == "In development" and seen["shipped_at"][card] < now:
                seen["late"].append((now, card))


def during(run, w, team, i):
    watch(run, w)
    review(run, w)
    bounce(run, w)


def review(run, w):
    """ana's pass over her review queue: she approves each Product item at the card's current stage."""
    seen, names = run.handoff, {v: k for k, v in run.requirements.items()}
    for item in w.get("pipelines/review/queue")["items"]:
        card, name = item["task_id"], names.get(item["requirement_id"])
        if card not in run.cards or name not in APPROVED_AT or item.get("form", "review") != "review":
            continue
        if stage_of(w, card) != APPROVED_AT[name]:
            continue  # a stale item of a stage the card has left is approved in place and moves nothing: she leaves it
        status, _ = w.request("post", path(card, item["requirement_id"], "approve"), body(item))
        if status >= 400 or name != "Spec approved":
            continue
        seen["approved"].add(card)
        if len(seen["again"]) < DOUBLE_CLICKS:
            again, answer = w.request("post", path(card, item["requirement_id"], "approve"), body(item))
            seen["again"].append((card, again, code_of(answer)))


def bounce(run, w):
    """ana sends the first card that reached In development back to Review, then moves it on again. Its handoff is live,
    so leaving Review again files nothing. Should the move on be refused (Review's approval asked again), her queue has
    it and her next pass approves it."""
    seen = run.handoff
    if seen["bounced"]:
        return
    card = next((c for c in run.cards if stage_of(w, c) == "In development" and subtasks(w, c)), None)
    if card is None:
        return
    seen["bounced"] = card
    for step in ("Review", "In development"):
        status, reply = w.request("post", "tasks/" + card, {"version": w.task(card)["version"], "step": step})
        seen["bounce"].append((step, status, code_of(reply)))


def one_subtask_per_spec(run):
    """Every approved spec has exactly one Engineering subtask and one handoff row, the bounced card's included, and
    every spec was approved."""
    w, bad = run.w, []
    approved = run.handoff["approved"]
    for card in run.cards:
        made, rows = subtasks(w, card), handoffs(w, card)
        want = 1 if card in approved else 0
        if len(made) != want or len(rows) != want:
            bad.append(f"{w.title(card)}: {len(made)} subtasks, {len(rows)} handoffs")
    if len(approved) != len(run.cards):
        bad.append(f"ana approved {len(approved)} of {len(run.cards)} specs")
    return not bad, ", ".join(bad[:6]) or f"{len(approved)} specs approved, one subtask each"


def second_approve_refused(run):
    """The Approve posted again at once is refused with a 4xx (no server error); it filed nothing (one_subtask_per_spec)."""
    again = run.handoff["again"]
    ok = bool(again) and all(400 <= status < 500 for _, status, _ in again)
    return ok, ", ".join(f"{status} {code}" for _, status, code in again) or "no second Approve was posted"


def bounce_files_nothing(run):
    """The card sent back to Review went back, left Review again, and still has its one subtask."""
    seen = run.handoff
    card = seen["bounced"]
    if not card:
        return False, "no card reached In development to send back"
    back = seen["bounce"][0][1] if seen["bounce"] else None
    count, stage = len(subtasks(run.w, card)), stage_of(run.w, card)
    ok = back == 200 and stage != "Review" and count == 1
    return ok, f"moves {seen['bounce']}; now at {stage} with {count} subtasks"


def stays_until_shipped(run):
    """No card was ever seen at Validate or Done while one of its Engineering subtasks was unshipped."""
    early = run.handoff["early"]
    return not early, ", ".join(f"{run.w.title(c)} at {s}, {n} unshipped" for _, c, s, n in early[:6])


def moves_once_shipped(run):
    """A card whose subtasks all shipped did not wait at In development past the next tick, and a card reached Done."""
    w = run.w
    late = sorted({w.title(c) for _, c in run.handoff["late"]})
    done = [c for c in run.cards if stage_of(w, c) == "Done"]
    return not late and bool(done), (
        f"{len(done)} cards Done; still in development after shipping: {', '.join(late[:6]) or 'none'}"
    )


SCENARIOS = [
    Scenario(
        "pipeline_handoff",
        "Product's Review spawns one Engineering subtask per approved spec; the card waits at In development until its "
        "subtasks ship, then goes to Validate and Done.",
        setup=setup,
        during=during,
        pipelines=(PRODUCT,),
        hours=10,
        drain_hours=2,
        release_every_hours=1,
        checks=[
            *BASE,
            ("one Engineering subtask per approved spec", GATE, one_subtask_per_spec),
            ("a second Approve is refused", GATE, second_approve_refused),
            ("leaving Review again files nothing", GATE, bounce_files_nothing),
            ("the card stays in development until its subtasks ship", GATE, stays_until_shipped),
            ("the card moves on once they ship, and reaches Done", GATE, moves_once_shipped),
        ],
    )
]
