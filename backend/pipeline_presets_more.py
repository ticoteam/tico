# pyright: strict
"""The default pipelines beyond General, Product and Engineering (docs/pipelines-v3.md "Default pipelines"): Growth
Experiments, Growth Monitoring, Lead and Opportunity in the Growth factory; Customer, Success Experiments and Success
Monitoring in the Success factory; Legal and Finance, both private, in the Corporate factory.

`pipeline_settings` takes PRESETS and ROLES into its own and makes these the way it makes Product and Engineering
(`ensure_presets`): once, after setup finished, never again once the company has one under its id, and with the same
ladder for a role no bot fills yet (a stage's owner goes to the pipeline's manager, a manager to the Task Manager).

- A pipeline's manager is its own Pipeline Manager: the bot whose slug is `<pipeline>-pipeline-manager` (the default
  chart's seat; every Pipeline Manager shares the `pipeline-manager` template, so the template can't tell them apart).
  A Monitoring pipeline is the exception: the seat that manages its factory's Experiments manages it too (WITH), and it
  is made only where that Experiments pipeline is.
- A stage's owner is a role found by its template (`pipeline_seats`' way): Lead Generation Owner, Outbound Sales Owner,
  Inbound Sales Owner, Success Owner. The Experiments and Monitoring pipelines' stages belong to the task's assignee
  instead: the channel Owner who runs that experiment and then watches what it kept; a task with nobody assigned wakes
  the manager to pick one (C25).
- Legal and Finance are private pipelines (backend/private_pipelines.py): every task in them is private, read by its
  requester, its owner, the pipeline's manager and the company's owner. Each is managed, and each stage owned, by its
  owner bot: the Legal Owner (template `legal-owner`, not the built-in Counsel) and the Finance Owner (the Bookkeeper,
  template `bookkeeper`). Nothing on them falls back to the Task Manager, which reads no private task: while the owner
  bot does not exist the pipeline is not made (PRIVATE, `pipeline_settings.ensure_presets`). Their stages have no
  limit (a limit counts tasks a reader can't see) and no OK from a person.

Only kinds that exist: Owner says done, a page (Jev checks it; a person OKs Opportunity's Proposal and, people only, an
experiment's Go-live page), Linked work done. Ideas and Watching are by hand with "a person's request moves on" off:
what rests there stays until someone moves it, and nothing there is late or woken.
"""

from typing import TYPE_CHECKING, Any

from . import pipeline_seats as S

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
ASSIGNEE = "assignee"
DAY = 1440

# Roles found by template (templates/catalog/<template>).
LEAD_GENERATION = "lead-generation-owner"
OUTBOUND_SALES = "outbound-sales-owner"
INBOUND_SALES = "inbound-sales-owner"
SUCCESS = "success-owner"
LEGAL_OWNER = "legal-owner"
FINANCE_OWNER = "bookkeeper"  # the Finance Owner seat is filled by the built-in Bookkeeper (backend/bookkeeper.py)
TEMPLATE_ROLES = (LEAD_GENERATION, OUTBOUND_SALES, INBOUND_SALES, SUCCESS, LEGAL_OWNER, FINANCE_OWNER)


def manager_role(pipeline_id: str) -> str:
    """A pipeline's manager role: its Pipeline Manager seat's slug. A Monitoring pipeline's is its Experiments
    pipeline's (WITH): one seat manages both."""
    return f"{WITH.get(pipeline_id, pipeline_id)}-pipeline-manager"


def _stage(sid: str, name: str, purpose: str, minutes: int | None, requirements: list[Row], **more: Any) -> Row:
    return {
        "id": sid,
        "name": name,
        "purpose": purpose,
        "owner": None,  # the stage's role (ROLES), filled by pipeline_settings.preset
        "by_hand": False,
        "requests_move_on": False,  # a by-hand stage here holds every task until someone moves it
        "task_limit": None,
        "expected_minutes": minutes,
        "expected_bots_only": True,
        "on_enter": {},
        "requirements": requirements,
        **more,
    }


DONE: list[Row] = [{"kind": "owner_done"}]


def _pipeline(
    pid: str, name: str, goal: str, prefix: str, factory: str, stages: list[Row], private: bool = False
) -> Row:
    return {
        "id": pid,
        "name": name,
        "goal": goal,
        "prefix": prefix,
        "manager_id": None,
        "owner_id": None,
        "private": private,
        "factory_id": factory,
        "preset": pid,
        "stages": stages,
        "done": {"id": f"{pid}-done"},
        "closed": {"id": f"{pid}-closed"},
    }


GO_LIVE_RUBRIC = (
    "Shows the thing as it will look when it goes live (the email, the page change, the ad), and names the metric it "
    "moves, the prediction as a number, the cost, how long it runs and when to stop it early. Says the brand check "
    "passed."
)
RESULTS_RUBRIC = (
    "Gives the number before and after, each with its dates and its source, and the result, tagged result:failed, "
    "result:worked-once or result:watching. Worked means the baseline was beaten and at least 60% of the predicted "
    "change was reached. On result:watching it links the Monitoring task filed as this task's follow-up."
)


def _people_only(requirement: Row) -> Row:
    """A person's OK no bot can give or push past (pipeline_settings `_people_only`)."""
    return {**requirement, "who_oks": "people_only"}


def _experiments(pid: str, name: str, goal: str, prefix: str, factory: str) -> Row:
    """Ideas → Building → Running. Every stage is the assignee's: the channel Owner whose number the bet moves, start to
    finish. Ideas rest until someone starts one (no person's request moves on by itself). The one person gate is the
    Go-live page in Building, people only. Done is an experiment measured, its result tagged; Closed is one stopped
    before it was measured."""
    stages = [
        _stage(
            f"{pid}-ideas",
            "Ideas",
            "One line each: what to try and why. Ideas rest here. Start your own by moving it to Building when "
            "Building has room.",
            None,
            [],
            by_hand=True,
        ),
        _stage(
            f"{pid}-building",
            "Building",
            "Make the real thing, not yet live: the email, the page change, the ad. Attach a Go-live page. A person "
            "OKs it before it goes live.",
            3 * DAY,
            [_people_only({"kind": "page", "line": "Go-live page", "settings": {"rubric": GO_LIVE_RUBRIC}})],
            task_limit=6,
        ),
        _stage(
            f"{pid}-running",
            "Running",
            "Put it live and snooze this task to its end date. When it wakes, measure it, tag the result and attach a "
            "Results page. If it is kept and watched, file a Monitoring task as its follow-up first.",
            2 * DAY,
            [{"kind": "page", "line": "Results page", "settings": {"rubric": RESULTS_RUBRIC}}],
        ),
    ]
    for stage in stages:
        stage["owner"] = ASSIGNEE
    return _pipeline(pid, name, goal, prefix, factory, stages)


def _monitoring(pid: str, name: str, goal: str, prefix: str, factory: str) -> Row:
    """Watching → Slipping. One task per kept thing (a campaign, a page set, a flow), the channel Owner's in both
    stages. Watching is where it rests: by hand, nothing is late and nobody is woken. The Owner's Watch routine moves
    it to Slipping when its number leaves its range; fixed, the Owner moves it back. Closed is a kept thing retired,
    with the reason."""
    stages = [
        _stage(
            f"{pid}-watching",
            "Watching",
            "One task per kept thing: what it is (a link), its KPI and range, and the experiment it came from. It "
            "rests here. Move it to Slipping when its number leaves its range.",
            None,
            [],
            by_hand=True,
        ),
        _stage(
            f"{pid}-slipping",
            "Slipping",
            "Fix it, then move it back to Watching. If it can't be fixed, file an idea and close this task with the "
            "reason.",
            3 * DAY,
            list(DONE),
        ),
    ]
    for stage in stages:
        stage["owner"] = ASSIGNEE
    return _pipeline(pid, name, goal, prefix, factory, stages)


FACTORIES: dict[str, Row] = {
    "growth": {"id": "growth", "name": "Growth", "goal": "Find leads and turn them into customers."},
    "success": {"id": "success", "name": "Success", "goal": "Customers reach their goals and refer others."},
    "corporate": {"id": "corporate", "name": "Corporate", "goal": "Legal and money matters handled, privately."},
}

# In the order they are made: Opportunity before Lead, which files into it.
PRESETS: dict[str, Row] = {
    "growth-experiments": _experiments(
        "growth-experiments",
        "Growth Experiments",
        "Test growth ideas, keep what works, drop the rest.",
        "GX",
        "growth",
    ),
    "opportunity": _pipeline(
        "opportunity",
        "Opportunity",
        "Turn leads into customers.",
        "O",
        "growth",
        [
            _stage(
                "opportunity-talking",
                "Talking",
                "Learn what they need and whether we fit. Say done when they want a proposal.",
                7 * DAY,
                list(DONE),
            ),
            _stage(
                "opportunity-proposal",
                "Proposal",
                "Write the proposal and attach it. A person OKs it before it is sent.",
                2 * DAY,
                [
                    {
                        "kind": "page",
                        "line": "Proposal",
                        "settings": {
                            "rubric": "Says what they get, the price and the terms, and matches what they asked for."
                        },
                    }
                ],
            ),
            _stage(
                "opportunity-closing",
                "Closing",
                "Send it and follow up. Say done when they sign. Close the task if they say no.",
                14 * DAY,
                list(DONE),
            ),
        ],
    ),
    "lead": _pipeline(
        "lead",
        "Lead",
        "Find leads and bring the qualified ones to Opportunity.",
        "L",
        "growth",
        [
            _stage(
                "lead-targets",
                "Targets",
                "Find who to reach: who they are, why they fit, and how to reach them.",
                DAY,
                list(DONE),
            ),
            _stage("lead-outreach", "Outreach", "Reach out. Say done once it is sent.", DAY, list(DONE)),
            _stage(
                "lead-replies",
                "Replies",
                "Follow up until they answer. Say done when they want to talk. Close the task if they don't.",
                7 * DAY,
                list(DONE),
            ),
            _stage(
                "lead-qualified",
                "Qualified",
                "Opportunity takes it from here. This task leaves when the Opportunity filed for it is won.",
                30 * DAY,
                [{"kind": "linked_done"}],
                on_enter={"file_into": "opportunity"},
            ),
        ],
    ),
    "customer": _pipeline(
        "customer",
        "Customer",
        "Customers reach their goals; referrals are the main result.",
        "C",
        "success",
        [
            _stage(
                "customer-onboarding", "Onboarding", "Get them set up and to their first result.", 7 * DAY, list(DONE)
            ),
            _stage(
                "customer-active",
                "Active",
                "Help them reach their goal. Say done when they have.",
                30 * DAY,
                list(DONE),
            ),
            _stage(
                "customer-referral-asked",
                "Referral asked",
                "Ask for a referral. Say done when they refer someone. Close the task if they won't.",
                14 * DAY,
                list(DONE),
            ),
        ],
    ),
    "success-experiments": _experiments(
        "success-experiments",
        "Success Experiments",
        "Test customer success playbooks, keep what works, drop the rest.",
        "SX",
        "success",
    ),
    "growth-monitoring": _monitoring(
        "growth-monitoring", "Growth Monitoring", "Keep what worked working.", "GM", "growth"
    ),
    "success-monitoring": _monitoring(
        "success-monitoring", "Success Monitoring", "Keep what worked for customers working.", "SM", "success"
    ),
    "legal": _pipeline(
        "legal",
        "Legal",
        "Contracts and compliance questions answered before they hold up the business",
        "LG",
        "corporate",
        [
            _stage(
                "legal-intake",
                "Intake",
                "Say what is asked, attach the contract or the facts, and what it holds up.",
                DAY,
                list(DONE),
            ),
            _stage(
                "legal-review",
                "Review",
                "Read it and write the answer: what it means, what to change, and the risk.",
                2 * DAY,
                list(DONE),
            ),
            _stage(
                "legal-rules-check",
                "Rules check",
                "Ask the Rules Owner to check the answer against the rules. Say done once its answer is on the task.",
                DAY,
                list(DONE),
            ),
        ],
        private=True,
    ),
    "finance": _pipeline(
        "finance",
        "Finance",
        "Money in, money out, and the books that show both",
        "F",
        "corporate",
        [
            _stage("finance-intake", "Intake", "Say what is needed, with the amounts and dates.", DAY, list(DONE)),
            _stage("finance-doing", "Doing", "Do it in Finance.", DAY, list(DONE)),
            _stage(
                "finance-review",
                "Review",
                "Check the books show it right. Say done when they do.",
                DAY,
                list(DONE),
            ),
        ],
        private=True,
    ),
}
# The private presets: made only once their owner bot exists, never handed to the Task Manager.
PRIVATE = ("legal", "finance")
# Each Monitoring pipeline goes with its factory's Experiments: made only where that one is, and managed by the same
# seat (no Monitoring Pipeline Manager seat).
WITH: dict[str, str] = {"growth-monitoring": "growth-experiments", "success-monitoring": "success-experiments"}


def _roles(pid: str, stages: dict[str, str], ok: tuple[str, ...] = ()) -> Row:
    return {"manager": manager_role(pid), "stages": stages, "ok": ok}


# Who fills each preset's manager and stage owners, and which stages' page a person OKs (pipeline_settings.ROLES).
# A stage left out keeps its preset owner: the Experiments pipelines' assignee.
ROLES: dict[str, Row] = {
    "growth-experiments": _roles("growth-experiments", {}, ok=("growth-experiments-building",)),
    "growth-monitoring": _roles("growth-monitoring", {}),
    "success-monitoring": _roles("success-monitoring", {}),
    "opportunity": _roles(
        "opportunity",
        dict.fromkeys(("opportunity-talking", "opportunity-proposal", "opportunity-closing"), INBOUND_SALES),
        ok=("opportunity-proposal",),
    ),
    "lead": _roles(
        "lead",
        {
            "lead-targets": LEAD_GENERATION,
            "lead-outreach": OUTBOUND_SALES,
            "lead-replies": OUTBOUND_SALES,
            "lead-qualified": OUTBOUND_SALES,
        },
    ),
    "customer": _roles(
        "customer", dict.fromkeys(("customer-onboarding", "customer-active", "customer-referral-asked"), SUCCESS)
    ),
    "success-experiments": _roles("success-experiments", {}, ok=("success-experiments-building",)),
    "legal": {
        "manager": LEGAL_OWNER,
        "stages": dict.fromkeys(("legal-intake", "legal-review", "legal-rules-check"), LEGAL_OWNER),
        "ok": (),
    },
    "finance": {
        "manager": FINANCE_OWNER,
        "stages": dict.fromkeys(("finance-intake", "finance-doing", "finance-review"), FINANCE_OWNER),
        "ok": (),
    },
}

_SLUG = "SELECT id FROM bots WHERE slug=? AND COALESCE(state, 'active') <> 'archived'"


def resolve(conn: "sqlite3.Connection") -> dict[str, str | None]:
    """{role: the bot's actor (`bot:<id>`) or None} for every role these presets name: each Pipeline Manager by its
    slug, each other role by its template, the way `pipeline_seats.resolve` finds one."""
    seats: Any = S
    has_seats = bool(seats._has_seats(conn))
    found: dict[str, str | None] = {}
    for pid in PRESETS:
        if pid in PRIVATE:
            continue  # managed by its owner bot, a template role
        row = conn.execute(_SLUG, (manager_role(pid),)).fetchone()
        found[manager_role(pid)] = f"bot:{row[0]}" if row else None
    for template in TEMPLATE_ROLES:
        bots: list[str] = seats._bots(conn, template, has_seats)
        found[template] = f"bot:{bots[0]}" if bots else None
    return found


def ensure_factory(conn: "sqlite3.Connection", factory_id: str, now: str) -> None:
    """A preset's factory, made when the company never had it: never one it retired, nor a second one under a name
    another factory already uses (then the preset's pipeline is not made: its factory is missing)."""
    spec = FACTORIES.get(factory_id)
    if not spec or conn.execute("SELECT 1 FROM factories WHERE id=?", (factory_id,)).fetchone():
        return
    if conn.execute(
        "SELECT 1 FROM factories WHERE retired_at IS NULL AND lower(name)=lower(?)", (spec["name"],)
    ).fetchone():
        return
    position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM factories WHERE retired_at IS NULL").fetchone()
    conn.execute(
        "INSERT INTO factories (id, name, goal, position, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (spec["id"], spec["name"], spec["goal"], int(position[0]), now, now),
    )
