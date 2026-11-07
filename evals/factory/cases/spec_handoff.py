"""A visual spec hands off on a person's approval (backend/spec_approval.py, docs/software-factory.md).

At hour 1 a builder that owns a Product roadmap card publishes its spec page on the card while it runs a turn, with the
styles inlined. At hour 2 ana moves the card from Spec to Approved. The handoff files a Dev ticket under the card with a
frozen, pinned copy of the spec page and its text; every review and QA child of that ticket carries the same link and
text. The board keeps moving its tickets through the checks the whole time."""

from evals.factory.scenarios import Scenario, standard, tickets
from evals.factory.sim import at
from evals.factory.world import START

SPEC_AT, APPROVE_AT = at(1), at(2)
ROADMAP = "Product roadmap"
SPEC = (
    "<html><head><title>Exports</title></head><body><h1>Exports</h1>"
    "<p>Pick a range, then download the file.</p></body></html>"
)
SPEC_TEXT = "Pick a range, then download the file."
SPEC_MARK = "Spec page:"  # what spec_approval writes into a ticket body


def setup(run, w, team):
    tickets(run, w, 4)
    run.builder = team.builders[0]
    card = w.post(
        "tasks",
        {
            "owner": run.builder.slug,
            "title": "Exports spec",
            "body": "Spec the exports.",
            "type": ROADMAP,
            "step": "Spec",
        },
    )
    run.card = card.get("task", card)["id"]
    run.approved = None  # (status code, reply) of ana's move to Approved


def during(run, w, team, i):
    now = w.now()
    if now < START + SPEC_AT or run.approved is not None:
        return
    if not spec_published(run):
        if run.builder.running and run.builder.attempt:
            w.request(
                "post",
                "pages",
                {"name": "exports.html", "text": SPEC, "task": run.card, "spec": True},
                token=run.builder.attempt["token"],
            )
        return
    if now >= START + APPROVE_AT:
        task = w.task(run.card)
        status, reply = w.request(
            "post", "tasks/" + run.card, {"version": task["version"], "step": "Approved"}, token="ana-test"
        )
        run.approved = (status, reply)


def spec_published(run):
    return bool(
        run.w.sql(
            "SELECT 1 FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE f.task_id=? AND p.spec='card'", (run.card,)
        )
    )


def handoff_tickets(run):
    return run.w.sql(
        "SELECT t.id,t.body FROM tasks t JOIN task_relations r ON r.from_task=t.id "
        "WHERE r.to_task=? AND r.kind='parent' AND t.type_id=?",
        (run.card, run.w.dev["id"]),
    )


def spec_published_once(run):
    """The builder published the spec page on the card, with its styles inlined."""
    if not spec_published(run):
        return False, "no spec page on the card"
    stored = run.w.sql(
        "SELECT p.spec, f.title FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE f.task_id=? AND p.spec='card'",
        (run.card,),
    )
    return len(stored) == 1, f"{len(stored)} spec pages on the card"


def approved_by_person(run):
    """ana's move to Approved went through, and the card has been to Approved since (the board may move it on)."""
    if run.approved is None:
        return False, "the move to Approved never went out"
    status, _ = run.approved
    moves = run.w.sql(
        "SELECT 1 FROM history_events e WHERE e.subject_type='task' AND e.subject_id=? AND e.kind='step' "
        "AND e.actor_id='human:ana' AND e.new=(SELECT s.id FROM task_steps s JOIN tasks t ON t.type_id=s.type_id "
        "WHERE t.id=e.subject_id AND s.name='Approved')",
        (run.card,),
    )
    return status == 200 and bool(moves), f"HTTP {status}, {len(moves)} moves by ana to Approved"


def builder_refused(run):
    """A bot may not approve: the builder's own move of its spec card to Approved is refused (spec_approval.check)."""
    rows = run.w.sql(
        "SELECT 1 FROM history_events e WHERE e.subject_type='task' AND e.subject_id=? AND e.kind='step' "
        "AND e.actor_id LIKE 'bot:%' AND e.new=(SELECT s.id FROM task_steps s JOIN tasks t ON t.type_id=s.type_id "
        "WHERE t.id=e.subject_id AND s.name='Approved')",
        (run.card,),
    )
    return not rows, f"{len(rows)} bot moves to Approved"


def ticket_frozen_and_pinned(run):
    """The builder ticket carries a frozen copy of the spec, filed and pinned on it, with the page link and text."""
    found = handoff_tickets(run)
    if not found:
        return False, "no Dev ticket under the card"
    bad = []
    for row in found:
        frozen = run.w.sql(
            "SELECT p.file_id FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE f.task_id=? AND p.spec='frozen'",
            (row["id"],),
        )
        pinned = run.w.sql("SELECT 1 FROM pins WHERE place=?", ("task:" + row["id"],))
        if not frozen or not pinned:
            bad.append(f"{run.w.title(row['id'])}: frozen={len(frozen)} pinned={len(pinned)}")
        elif SPEC_MARK not in (row["body"] or "") or SPEC_TEXT not in (row["body"] or ""):
            bad.append(f"{run.w.title(row['id'])}: body lacks the spec link or text")
    return not bad, ", ".join(bad[:4]) or f"{len(found)} Dev tickets with a frozen spec"


def children_carry_spec(run):
    """Every review and QA child of the handoff ticket has the spec's link and text in its body."""
    children, missing = 0, []
    for ticket in handoff_tickets(run):
        for child in run.w.sql(
            "SELECT t.id,t.title,t.body FROM tasks t JOIN task_relations r ON r.from_task=t.id "
            "WHERE r.to_task=? AND r.kind='parent'",
            (ticket["id"],),
        ):
            children += 1
            if SPEC_MARK not in (child["body"] or "") or SPEC_TEXT not in (child["body"] or ""):
                missing.append(child["title"])
    if not children:
        return False, "no review or QA child yet"
    return (
        not missing,
        f"{children} children, without the spec: {', '.join(missing[:4])}"
        if missing
        else f"{children} children with the spec",
    )


SCENARIOS = [
    Scenario(
        "spec_handoff",
        "A builder publishes its spec page on its Product roadmap card; a person moves the card to Approved, and the "
        "Dev ticket it files carries a frozen, pinned copy of the spec with its text, and so do its review and QA children.",
        setup=setup,
        during=during,
        checks=standard(
            ("the spec page is on the card, once", "Spec handoff", spec_published_once),
            ("a person's move takes the card to Approved", "Spec handoff", approved_by_person),
            ("a bot never moves a spec to Approved", "Spec handoff", builder_refused),
            ("the Dev ticket carries a frozen, pinned spec", "Spec handoff", ticket_frozen_and_pinned),
            ("review and QA children carry the spec", "Spec handoff", children_carry_spec),
        ),
    ),
]
