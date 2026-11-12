"""Stage rules over HTTP (Stage rules spec, step 1, part 3): read the rules that apply to a stage, add, edit, retire,
and a person's accept or skip of a bot's suggestion; and the Rules page's groups with a count each.

The rules, their checks and the engine are backend/stage_rules.py's; this module only says who may write and how a
rule reads to its caller.

- Reading: every teammate reads every stage rule. `GET /api/v2/stage-rules?pipeline=&stage=` is the rules that apply
  there (that stage, the whole pipeline, the company) and the built-in behaviours (stage_rules.BUILT_IN, read-only).
- Writing: a person who may change the pipeline (pipeline_settings.may_change: its owner, its manager, the company's
  owner and Admins) adds, edits and retires its rules, and accepts or skips a suggestion on it. A company rule (no
  pipeline) is the company's owner's and Admins'. A bot's add or edit is a suggestion (stage_rules makes it one), and a
  bot never retires, accepts or skips.
- Each rule says whether the caller may change it (`can_edit`) and what it costs (`cost`: free, or one Jev call per
  event).
- The Rules page's groups (`GET /api/v2/rule-groups`): Company, Outside, Pipelines and stages, Due, Wakes, Automations
  on tasks, Built in and Suggested, each with a count. Wakes and Automations on tasks are listed with their ids and
  edited on the Automations page, as before.
"""

import json
from typing import Any, Literal

from fastapi import Request
from pydantic import Field

from . import hubdb as H
from . import pipeline_settings
from . import stage_rules as SR
from .models import Contract
from .store import Problem

EVENTS = ("enters", "owner_changes", "bot_snoozes", "timer")
THENS = ("block", "tell_manager", "tell_owner", "move_back")
SCOPES = ("stage", "pipeline", "company")
NAME_MAX = 200
TEXT_MAX = 1000
STAGES_MAX = 50
WAKES_PREFIX = "auto_wakes_"  # backend/redesign_migration.py WAKES_PREFIX: the "What wakes" automations


class StageRuleFields(Contract):
    """A stage rule as a caller writes it. On: `pipeline` ('' or absent for the company, every pipeline) and `stages`
    (empty for the whole pipeline). When: `event`, with `timer` for a timer. If: `cond` (fixed keys) and `jev` (one Jev
    question, or empty). Then: `then`, its `then_params` and the `reason` a Block or Tell says."""

    name: str | None = Field(default=None, max_length=NAME_MAX)
    pipeline: str | None = Field(default=None, max_length=100)
    stages: list[str] | None = Field(default=None, max_length=STAGES_MAX)
    event: Literal["enters", "owner_changes", "bot_snoozes", "timer"] | None = None
    timer: dict[str, Any] | None = None
    cond: dict[str, Any] | None = None
    jev: str | None = Field(default=None, max_length=TEXT_MAX)
    then: Literal["block", "tell_manager", "tell_owner", "move_back"] | None = None
    then_params: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=TEXT_MAX)


class StageRuleEdit(StageRuleFields):
    revision: int = Field(ge=1, description="The rule's revision as you read it; another answers 409")


class StageRuleDecide(Contract):
    revision: int | None = Field(default=None, ge=1)
    why: str = Field(default="", max_length=TEXT_MAX)


def _pipeline_row(c, pipeline_id):
    if not pipeline_id:
        return None
    row = c.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
    if not row:
        raise Problem("not_found", "No such pipeline", 404)
    return dict(row)


def _stage_names(c, pipeline_id):
    """{stage id: name} of a pipeline's stages, in order."""
    if not pipeline_id:
        return {}
    rows = c.execute("SELECT id, name FROM stages WHERE pipeline_id=? ORDER BY position, id", (pipeline_id,))
    return {r["id"]: r["name"] for r in rows}


def may_change(c, actor, pipeline_id):
    """Whether a person may change the rules on this pipeline ('' is the company: its owner and Admins). A bot never
    does: its writes are suggestions."""
    if not H.is_human(actor):
        return False
    try:
        row = _pipeline_row(c, pipeline_id)
    except Problem:
        return False
    return pipeline_settings.may_change(c, actor, row)


def _refuse_unless(c, actor, pipeline_id):
    if not may_change(c, actor, pipeline_id):
        if pipeline_id:
            raise Problem("forbidden", "Only the pipeline's owner, its manager and Admins change its rules.", 403)
        raise Problem("forbidden", "Only the company's owner and Admins change company rules.", 403)


def _checked(c, fields):
    """The fields as stage_rules takes them: `pipeline` becomes `pipeline_id`, and each stage must be the pipeline's."""
    out = {k: v for k, v in fields.items() if v is not None and k != "revision"}
    if "pipeline" in out:
        out["pipeline_id"] = out.pop("pipeline") or ""
        _pipeline_row(c, out["pipeline_id"])
    if "name" in out and not out["name"]:
        raise Problem("invalid", "A rule needs a name.", 422)
    return out


def _stages_ok(c, pipeline_id, stages):
    if not stages:
        return
    if not pipeline_id:
        raise Problem("invalid", "A company rule applies to every stage; name a pipeline to pick stages.", 422)
    known = _stage_names(c, pipeline_id)
    unknown = [s for s in stages if s not in known]
    if unknown:
        raise Problem("invalid", f"Not a stage of {pipeline_id}: {', '.join(unknown)}", 422)


def _row(c, rule_id):
    row = c.execute("SELECT * FROM stage_rules WHERE id=?", (rule_id,)).fetchone()
    if not row:
        raise Problem("not_found", "No such stage rule", 404)
    return dict(row)


def view(c, actor, row, names=None):
    """A rule as its caller reads it: stage_rules.view, the names of its pipeline and stages, `can_edit`."""
    out = SR.view(row)
    pid = out.get("pipeline_id") or ""
    stage_names = (names or {}).get(pid)
    if stage_names is None:
        stage_names = _stage_names(c, pid)
        if names is not None:
            names[pid] = stage_names
    out["pipeline_name"] = ""
    if pid:
        found = c.execute("SELECT name FROM pipelines WHERE id=?", (pid,)).fetchone()
        out["pipeline_name"] = found["name"] if found else pid
    out["stage_names"] = [stage_names.get(s, s) for s in out.get("stages") or []]
    out["can_edit"] = (
        may_change(c, actor, pid) and not out.get("retired_at") and out.get("status") in ("active", "suggested")
    )
    return out


def listing(c, actor, pipeline=None, stage=None, status="active", mine=False):
    """The rules, optionally only those that apply to a pipeline (and a stage), each as `view` reads it; retired ones
    only with status=retired. With `mine`, only the rules the caller wrote or suggested."""
    if stage and not pipeline:
        raise Problem("invalid", "A stage is named with its pipeline.", 422)
    if pipeline:
        _pipeline_row(c, pipeline)
    if stage and stage not in _stage_names(c, pipeline):
        raise Problem("not_found", f"No stage {stage} in {pipeline}", 404)
    wanted = None if status in (None, "", "any") else status
    rows = SR.listed(c, pipeline_id=pipeline, stage_id=stage, status=wanted)
    if status == "any":
        rows += SR.listed(c, pipeline_id=pipeline, stage_id=stage, status="retired")
    if mine:
        rows = [r for r in rows if r.get("created_by") == actor]
    names = {}
    items = [view(c, actor, r, names) for r in rows]
    items.sort(key=lambda r: (SCOPES.index(r.get("scope") or "company"), r.get("created_at") or "", r["id"]))
    return {
        "items": items,
        "built_in": list(SR.BUILT_IN),
        "may_add": bool(pipeline) and may_change(c, actor, pipeline),
        "may_suggest": H.is_bot(actor),
    }


def _automations(c):
    """(wakes, automations on tasks): the "What wakes" automations and the automations a task starts, as id, name and
    whether it is on. Both are edited on the Automations page."""
    if not H._has_table(c, "automations"):
        return [], []
    from . import automations as AU

    wakes, tasks = [], []
    for r in c.execute("SELECT * FROM automations WHERE deleted_at IS NULL ORDER BY created_at, id").fetchall():
        try:
            flow = AU.flow_of(r)
        except Exception:  # noqa: BLE001 - a flow that cannot be read is not listed
            continue
        if flow.get("builtin"):
            continue
        item = {"id": r["id"], "name": flow.get("name") or r["id"], "enabled": bool(r["enabled"])}
        trigger = flow.get("trigger") or {}
        if str(r["id"]).startswith(WAKES_PREFIX):
            wakes.append(item)
        elif trigger.get("type") == "task_received" or (
            trigger.get("type") == "event" and str(trigger.get("name") or "").startswith("task.")
        ):
            tasks.append(item)
    return wakes, tasks


def groups(c, auth, who):
    """The Rules page's groups, in the page's order, each with its count; and the Wakes, Automations on tasks and Built
    in rows the page lists read-only."""
    from . import org_rules

    org = org_rules.counts(c, auth, who)
    stage = SR.listed(c, status="active")
    stage_suggested = SR.listed(c, status="suggested")
    wakes, tasks = _automations(c)
    counts = [
        ("internal", "Company", org["internal"]),
        ("external", "Outside", org["external"]),
        ("stages", "Pipelines and stages", len(stage)),
        ("due", "Due", org["due"]),
        ("wakes", "Wakes", len(wakes)),
        ("automations", "Automations on tasks", len(tasks)),
        ("builtin", "Built in", len(SR.BUILT_IN)),
        ("suggested", "Suggested", org["suggested"] + len(stage_suggested)),
    ]
    return {
        "groups": [{"key": k, "label": label, "count": n} for k, label, n in counts],
        "wakes": wakes,
        "automations": tasks,
        "built_in": list(SR.BUILT_IN),
    }


def install(app, store, auth, mutate):
    def fresh(who, rule_id):
        with store.read() as c:
            return view(c, who.actor, _row(c, rule_id))

    @app.get("/api/v2/rule-groups")
    def rule_groups(request: Request):
        """The Rules page's groups with a count each: Company, Outside, Pipelines and stages, Due, Wakes, Automations
        on tasks, Built in and Suggested; and the Wakes, Automations on tasks and Built in rows."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return groups(c, auth, who)

    @app.get("/api/v2/stage-rules")
    def stage_rules_list(
        request: Request,
        pipeline: str | None = None,
        stage: str | None = None,
        status: Literal["active", "suggested", "retired", "any"] = "active",
        mine: bool = False,
    ):
        """The stage rules. With `pipeline` (and `stage`), only those that apply there: that stage's, the whole
        pipeline's and the company's, most specific first. `status` active (default), suggested, retired or any; `mine`
        only those the caller wrote or suggested. `built_in` lists the behaviours in code, read-only."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return listing(c, who.actor, pipeline, stage, status, mine)

    @app.get("/api/v2/stage-rules/{rule_id}")
    def stage_rules_show(request: Request, rule_id: str):
        """One stage rule: On, When, If, Then, what it costs, and whether the caller may change it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return view(c, who.actor, _row(c, rule_id))

    @app.post("/api/v2/stage-rules")
    def stage_rules_add(request: Request, body: StageRuleFields):
        """Add a stage rule. A person who may change the pipeline adds it in force; a bot's is a suggestion that a
        person accepts or skips."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            fields = _checked(c, body.model_dump())
            fields.setdefault("pipeline_id", "")
            _stages_ok(c, fields["pipeline_id"], fields.get("stages"))
            if not fields.get("name"):
                raise Problem("invalid", "A rule needs a name.", 422)
            if not H.is_bot(who.actor):
                _refuse_unless(c, who.actor, fields["pipeline_id"])
            return {"id": SR.add(c, who.actor, fields)["id"]}

        made = mutate(request, body, work)
        return fresh(who, made["id"])

    @app.post("/api/v2/stage-rules/{rule_id}")
    def stage_rules_edit(request: Request, rule_id: str, body: StageRuleEdit):
        """Change a stage rule: only the fields sent, against the `revision` read. A bot's change is a suggestion that
        replaces the rule once a person accepts it."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            row = _row(c, rule_id)
            if row.get("retired_at"):
                raise Problem("conflict", "This rule is retired.", 409)
            if row["revision"] != body.revision:
                raise Problem("conflict", "The rule changed since you read it. Read it again.", 409)
            fields = _checked(c, body.model_dump())
            pid = fields.get("pipeline_id", row["pipeline_id"])
            stages = fields["stages"] if "stages" in fields else json.loads(row["stages"] or "[]")
            _stages_ok(c, pid, stages)
            if not H.is_bot(who.actor):
                _refuse_unless(c, who.actor, row["pipeline_id"])
                _refuse_unless(c, who.actor, pid)
            return {"id": SR.edit(c, who.actor, rule_id, fields, body.revision)["id"]}

        made = mutate(request, body, work)
        return fresh(who, made["id"])

    def decide(request, rule_id, body, action):
        who = request.state.identity
        auth.domain(who)
        if not H.is_human(who.actor):
            raise Problem("forbidden", "A bot suggests rules; a person retires, accepts or skips them.", 403)

        def work(c):
            row = _row(c, rule_id)
            if body.revision is not None and row["revision"] != body.revision:
                raise Problem("conflict", "The rule changed since you read it. Read it again.", 409)
            if row.get("retired_at"):
                raise Problem("conflict", "This rule is retired.", 409)
            if action == "retire" and row["status"] != "active":
                raise Problem("conflict", "Only a rule in force is retired; skip a suggestion.", 409)
            if action in ("accept", "skip") and row["status"] != "suggested":
                raise Problem("conflict", "This rule is not a suggestion.", 409)
            _refuse_unless(c, who.actor, row["pipeline_id"])
            if action == "retire":
                SR.retire(c, who.actor, rule_id, body.why)
            else:
                getattr(SR, action)(c, who.actor, rule_id)
            return {"id": rule_id}

        mutate(request, body, work)
        return fresh(who, rule_id)

    @app.post("/api/v2/stage-rules/{rule_id}/retire")
    def stage_rules_retire(request: Request, rule_id: str, body: StageRuleDecide):
        """Retire a stage rule in force: it stops applying, and stays readable with status=retired."""
        return decide(request, rule_id, body, "retire")

    @app.post("/api/v2/stage-rules/{rule_id}/accept")
    def stage_rules_accept(request: Request, rule_id: str, body: StageRuleDecide):
        """A person accepts a bot's suggestion: a new rule comes into force, a change replaces the rule it names."""
        return decide(request, rule_id, body, "accept")

    @app.post("/api/v2/stage-rules/{rule_id}/skip")
    def stage_rules_skip(request: Request, rule_id: str, body: StageRuleDecide):
        """A person skips a bot's suggestion: it is set aside and nothing changes."""
        return decide(request, rule_id, body, "skip")
