"""A Marketing pipeline run by the engine, handing qualified leads to Sales (Track G; the validation checklist in
docs/pipelines-validation.md).

The world has two pipelines: the sim's board is Marketing (each ticket a lead: New lead > Qualifying > Peer Check >
Qualified > Handed over), and a Sales pipeline is built beside it (Scenario `pipelines`). Everything is set up through the
API, as a pipeline's owner would: two requirements on Qualifying, "Lead has company and contact" (a text file the judge
bot reads against a rubric) and "Campaign brief approved" (ana's approval), and a Warn after of 90 minutes on
Qualifying. A spawn link on entering Handed over (the preset's Lead > Qualified link; the links route makes only leave
links, so setup uses the helper the route and the preset share) files one Sales opportunity as the lead's subtask when
the Release Manager hands it over.

The builders read the server's `requirement` refusal (LeadBuilder here): they publish the lead record as a file and
attach it through the attach route. builder-openai's first record has no email: the judge (a fake engine on the real
judge pass, run inline on each scheduler pass so a seed runs the same way) says not met, the pipeline manager tells the
builder, and the builder attaches a corrected record. ana reviews the brief from her review queue once the lead's contact
record is met; her approval, the last requirement, moves the lead on to Peer Check.

The checks: no lead is at Qualified or later without both requirements met at its current work version, a record judged
not met was corrected and its lead still qualified, no requirement of a visit was asked about twice and every lead past
its Warn after was asked, each lead handed over has one Sales subtask, and no call got a server error."""

import datetime as dt

from backend import pipeline_judges, pipeline_links
from evals.factory.board import MERGE, Board, Check, Step
from evals.factory.bots import Builder
from evals.factory.scenarios import BASE, Scenario, at, tickets
from evals.factory.world import START, Refusal

GATE = "pipeline requirements"
LEADS = 6
WARN_AFTER_MINUTES = 90
CONTACT, BRIEF = "Lead has company and contact", "Campaign brief approved"
REQUIREMENTS = (
    {
        "name": CONTACT,
        "kind": "attachment",
        "needs": {"formats": ["file"]},  # a text file: Tico reads its words, so the judge can see it all
        "reviewed_by": "bot",
        "rubric": "The record names the lead's company and a contact person with an email address.",
    },
    {"name": BRIEF, "kind": "approval", "reviewed_by": "person", "reviewer_id": "human:ana"},
)
SALES = {
    "name": "Sales",
    "default_visibility": "public",
    "steps": [
        {"name": "Opportunity", "status": "open"},
        {"name": "Won", "status": "done"},
        {"name": "Lost", "status": "closed"},
    ],
}
QUALIFIED = ("Qualified", "Handed over")


def marketing_board():
    """A Studio-style board (design_board's shape) with marketing names. Peer Check's changes go to Rework, not back to
    Qualifying: a work step a check sends fixes to is a fixes step, and Qualifying is where the lead is first worked."""
    steps = [
        Step("New lead", "open", assign="builder"),
        Step("Qualifying", "doing", work=True, next="Peer Check"),
        Step("Peer Check", "review", check=Check("reviewer", MERGE, "Rework", timeout_hours=1.5, kind="review")),
        Step("Rework", "doing", work=True, after_green="Peer Check", wake_after_hours=3),
        Step("Qualified", "review", can_hold=False, merged=True, release=True),
        Step("Handed over", "done", can_hold=False, merged=True),
        Step("Dropped", "closed", can_hold=False),
    ]
    return Board("Marketing leads", steps, on_merge="Qualified")


def record(task, email):
    """The lead record a builder publishes: the company and the contact, with the contact's email unless it left it out."""
    lines = [f"Lead: {task['title']}", "Company: Acme Lights", "Contact: Dana Reyes, events lead"]
    if email:
        lines.append("Email: dana@acme-lights.example")
    return "\n".join(lines) + "\n"


def judge(model, state, questions, label):
    """The judge model's answer, made up from what it was shown: met when the record names a company and an email."""
    text = state["attachment"]["text"] or ""
    found = "Company:" in text and "Email:" in text
    return {
        "p": 0.95 if found else 0.05,
        "reason": "Company and contact email are there" if found else "The record has no contact email",
        "provider": "sim",
        "model": model,
        "cost_usd": 0.0,
        "latency_ms": 1,
    }


def inline(original):
    """The judge pass on the scheduler's own thread: the server runs it on a thread of its own, which a seeded run cannot
    order. Its answers are recorded before the tick's turns run."""

    def dispatch(store, at_when, failures):
        try:
            pipeline_judges.tick(store, at_when)
        except Exception as exc:  # noqa: BLE001 - as the thread does: one failed pass never stops the scheduler
            failures.append({"pipeline_judges": type(exc).__name__})
        return None

    return dispatch


def latest_verdict(w, task_id, requirement_id):
    """The requirement's newest standing decision on the ticket: (verdict, work version), or (None, None)."""
    found = w.sql(
        "SELECT verdict, work_version FROM stage_decisions WHERE task_id=? AND stage_requirement_id=? "
        "AND undone_at IS NULL ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (task_id, requirement_id),
    )
    return (found[0]["verdict"], found[0]["work_version"]) if found else (None, None)


def contact_met(w, task_id, requirement_id):
    verdict, version = latest_verdict(w, task_id, requirement_id)
    revision = w.sql("SELECT work_revision FROM tasks WHERE id=?", (task_id,))[0]["work_revision"]
    return verdict == "met" and version == revision


class LeadBuilder(Builder):
    """A builder that reads a requirement refusal as a real one does: it publishes the lead record and attaches it, and
    when the judge said the record falls short, it attaches a corrected one. What only a person gives (the brief) it
    leaves to them."""

    def move(self, task, step):
        try:
            return super().move(task, step)
        except Refusal as exc:
            if not ("requirement" in exc.body and "leave" in exc.body):
                raise
            return self.send_record(task)

    def send_record(self, task):
        w, rid = self.world, self.marketing[CONTACT]
        sent = self.records.get(task["id"], 0)
        verdict, _ = latest_verdict(w, task["id"], rid)
        if sent and verdict != "failed":
            return "waiting on " + ("the judge" if verdict != "met" else "the brief")
        if sent >= 2:
            return "waiting on the record's review"
        email = sent > 0 or not self.chance("no_email")
        made = w.post(
            "files/uploads",
            {
                "name": f"lead-{task['id'][:8]}.txt",
                "title": "Lead record: " + task["title"],
                "text": record(task, email),
                "task": task["id"],
            },
            token=self.token,
        )["file"]
        w.post(
            f"tasks/{task['id']}/requirements/{rid}/attachments",
            {"source": "file", "file_id": made["id"], "file_version": made["version"]},
            token=self.token,
        )
        self.records[task["id"]] = sent + 1
        self.sent.append((w.now(), task["id"], email))
        return "attached the " + ("corrected " if sent else "") + "lead record"


def setup(run, w, team):
    stage = next(s["id"] for s in w.dev["steps"] if s["name"] == "Qualifying")
    run.marketing = {}
    for rule in REQUIREMENTS:
        run.marketing[rule["name"]] = w.call("post", f"pipeline-stages/{stage}/requirements", rule)["id"]
    w.call("patch", f"pipeline-stages/{stage}", {"limit_minutes": WARN_AFTER_MINUTES})
    # Entering Handed over files the lead's Sales opportunity, as the preset's Lead > Qualified link does (fires on
    # enter). The links route makes only leave links, so the link is made by the helper the route and the preset share.
    # A leave link out of Qualified would not do: its subtask would be filed by the move that finishes the lead, and the
    # finish closes the lead's open subtasks.
    handed = next(s["id"] for s in w.dev["steps"] if s["name"] == "Handed over")
    with w.store.transaction() as c:
        pipeline_links.create_link(c, "human:ana", handed, w.pipelines["Sales"]["id"], "spawn", fires="enter")
    run.approvals = []  # (when, lead, status) of each of ana's Approves
    for bot in team.builders:
        bot.__class__ = LeadBuilder
        bot.marketing, bot.records, bot.sent = run.marketing, {}, []
    w.patch(pipeline_judges, "ENGINE", lambda original: judge)
    w.patch(pipeline_judges, "dispatch", inline)
    tickets(run, w, LEADS, title="Lead")


def during(run, w, team, i):
    """ana's pass over her review queue: she approves a lead's brief once its contact record is met."""
    contact, brief = run.marketing[CONTACT], run.marketing[BRIEF]
    for item in w.get("pipelines/review/queue")["items"]:
        lead = item["task_id"]
        if item["requirement_id"] != brief or item.get("form", "review") != "review":
            continue
        if w.step_of(w.task(lead)) != "Qualifying" or not contact_met(w, lead, contact):
            continue
        status, _ = w.request(
            "post",
            f"pipelines/tasks/{lead}/requirements/{brief}/approve",
            {
                "work_version": item["work_version"],
                "attachment_digest": item["attachment_digest"],
                "requirement_version": item["requirement_version"],
            },
        )
        run.approvals.append((w.now(), lead, status))


def qualified_only_with_evidence(run):
    """Every lead at Qualified or later has both requirements met at its current work revision (a standing met or override
    decision on the requirement itself), and at least one lead got there, so a stalled run cannot pass."""
    bad, reached = [], 0
    ids = tuple(run.marketing.values())
    for t in run.final:
        if t["step"] not in QUALIFIED:
            continue
        reached += 1
        met = {
            r["stage_requirement_id"]
            for r in run.w.sql(
                "SELECT d.stage_requirement_id FROM stage_decisions d JOIN tasks t ON t.id=d.task_id "
                "WHERE d.task_id=? AND d.verdict IN ('met','override') AND d.undone_at IS NULL "
                "AND d.stage_requirement_id IN (?,?) AND d.work_version=t.work_revision",
                (t["id"], *ids),
            )
        }
        if met != set(ids):
            bad.append(t["title"])
    if not reached:
        return False, "no lead reached Qualified"
    return not bad, ", ".join(bad[:6]) or f"{reached} leads qualified with both met"


def corrected_after_not_met(run):
    """A record without an email was judged not met, its builder attached a corrected one, and that lead qualified."""
    failed = {  # only the judge rules on a bot-reviewed requirement
        r["task_id"]
        for r in run.w.sql(
            "SELECT DISTINCT task_id FROM stage_decisions WHERE stage_requirement_id=? AND verdict='failed'",
            (run.marketing[CONTACT],),
        )
    }
    corrected = {t for b in run.t.builders for _, t, email in getattr(b, "sent", ()) if email and t in failed}
    steps = {t["id"]: t["step"] for t in run.final}
    qualified = [t for t in corrected if steps.get(t) in QUALIFIED]
    ok = bool(failed) and bool(qualified)
    return ok, f"{len(failed)} records judged not met, {len(corrected)} corrected, {len(qualified)} of those qualified"


def _entered(run, task_id):
    """When a lead entered its current stage: its last step change, or the sim's start for a lead that never moved."""
    raw = run.w.step_entered_at(task_id)
    return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else START


def asked_once_and_late_asked(run):
    """The pipeline manager asks once per visit and requirement (D40): no requirement of a visit was asked twice. A lead
    still in Qualifying past its Warn after was asked during that visit."""
    twice = run.w.sql(
        "SELECT task_id, count(*) n FROM pipeline_manager_actions WHERE action='ask' AND task_id IN "
        "(SELECT id FROM tasks WHERE type_id=?) GROUP BY stage_visit_id, stage_requirement_id HAVING n>1",
        (run.w.dev["id"],),
    )
    cutoff = run.end - at(0, WARN_AFTER_MINUTES)
    silent = []
    for t in run.final:
        if t["step"] != "Qualifying":
            continue
        entered = _entered(run, t["id"])
        if entered > cutoff:
            continue
        asks = run.w.sql(
            "SELECT count(*) AS n FROM pipeline_manager_actions WHERE task_id=? AND action='ask' AND created_at>=?",
            (t["id"], entered.strftime("%Y-%m-%dT%H:%M:%S.000000Z")),
        )
        if not asks or not asks[0]["n"]:
            silent.append(t["title"])
    bad = [f"{run.w.title(r['task_id'])} asked {r['n']}x" for r in twice] + [f"{s} never asked" for s in silent]
    return not bad, ", ".join(bad[:6])


def handed_to_sales(run):
    """Each lead handed over has exactly one Sales opportunity as its subtask, and one handoff row; at least one was."""
    w, sales, bad = run.w, run.w.pipelines["Sales"]["id"], []
    handed = [t for t in run.final if t["step"] == "Handed over"]
    for t in handed:
        made = w.sql(
            "SELECT t.id FROM tasks t JOIN task_relations r ON r.from_task=t.id AND r.kind='parent' "
            "WHERE r.to_task=? AND t.type_id=?",
            (t["id"], sales),
        )
        rows = w.sql("SELECT 1 FROM pipeline_handoffs WHERE source_task_id=?", (t["id"],))
        if len(made) != 1 or len(rows) != 1:
            bad.append(f"{t['title']}: {len(made)} opportunities, {len(rows)} handoffs")
    if not handed:
        return False, "no lead was handed over"
    return not bad, ", ".join(bad[:6]) or f"{len(handed)} leads handed to Sales, one opportunity each"


SCENARIOS = [
    Scenario(
        "pipeline_marketing",
        "A Marketing pipeline: leads need a contact record the judge accepts and an approved brief before Qualified, "
        "and each lead handed over files one Sales opportunity.",
        setup=setup,
        during=during,
        board=marketing_board,
        pipelines=(SALES,),
        styles={"builder-openai": {"no_email": 1.0}},
        hours=8,
        drain_hours=2,
        release_every_hours=1,
        checks=[
            *BASE,
            ("no lead qualifies without its evidence", GATE, qualified_only_with_evidence),
            ("a record judged not met is corrected", GATE, corrected_after_not_met),
            ("each requirement asked once a visit; late leads asked", "pipeline manager", asked_once_and_late_asked),
            ("each lead handed over has one Sales opportunity", "pipeline links", handed_to_sales),
        ],
    )
]
