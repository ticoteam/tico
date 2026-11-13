# pyright: strict
"""Stage rules: each stage says what it enforces, as When -> If -> Then (the Stage rules spec, step 1).

A rule is one row of `stage_rules`, its own table: the `rules` table's rows are read by the wake engine (its `*`
rows), count toward its row limit and are turned into "What wakes" automations by the redesign migration, none of
which a stage rule is.

- **On**: `pipeline_id` ('' = every pipeline, the company) and `stages` (a JSON list of stage ids; empty = every stage).
- **When** (`event`): one action, or several comma-separated: `enters` (the task enters the stage, filed into it or
  moved), `owner_changes` (someone gives it another owner), `bot_snoozes` (a bot sets it aside); or `timer` with
  `timer` = {"kind": "in_stage" | "quiet" | "waiting_person", "hours": N} (backend/stage_rule_timers.py).
- **If** (`cond`): fixed conditions, all of which must hold (`CONDITIONS`), and optionally one Jev question (`jev`).
  All of a task's Jev questions on one event go to Jev in one call (`judge_snooze`); Jev failing or unset passes the
  write (fails open). Timers never ask a model.
- **Then** (`then`): `block` (the write is refused with the rule's reason; an owner the rule refuses as a task enters is
  cleared and the task still enters), `tell_manager`, `tell_owner`, `move_back` (`then_params.stage`).

Every Block, Tell and Move back is written on the task's history (kind `stage_rule`), naming the rule and the reason,
and published as `stage_rule.refused` / `stage_rule.told` / `stage_rule.moved_back`. A rule's own change sets off no
other rule.

The default rules (`DEFAULTS`) are ordinary rows: read, edit, retire. `ensure_defaults` seeds each once by id, so a
retired one stays retired. A bot's new rule or change is `suggested` until a person accepts or skips it.
"""

import json
import logging
import uuid
from typing import Any, cast

from . import hubdb_core as _hubdb_core

_core: Any = _hubdb_core  # untyped module, through Any as in pipeline_judges.py

log = logging.getLogger(__name__)


ACTIONS = ("enters", "owner_changes", "bot_snoozes")
EVENTS = (*ACTIONS, "timer")
TIMERS = ("in_stage", "quiet", "waiting_person")
THENS = ("block", "tell_manager", "tell_owner", "move_back")
# The fixed conditions an If may hold. Those this module reads are in `matches`; the rest are read by the module that
# owns their When (stage_rule_timers).
CONDITIONS = (
    "owner_on_team",  # a group id, or '' for the pipeline manager's group
    "owner_not_on_team",
    "owner_kind",  # 'bot' | 'person'
    "snooze_hours_over",
    "label",
    "requester_person",
    "sent_back",
    "turn",
)
QUESTION_SET = "stage-rule"
LABEL = "stage-rule@1"
# Jev's probability the question is true at which a rule's If holds. Below it the write goes: refusing a real wait
# costs a bot a turn, so the bar sits above a coin toss.
MATCH_AT = 0.7
# The engine a test sets: (state, questions, label) -> {"answers": {qid: {"noul": p}}}. None asks the server's Jev.
JEV = None
_installed_store = None

OWNER_ON_TEAM = "sr_owner_on_team"
REAL_WAIT = "sr_real_wait"
BLOCKED = "You're blocked, not waiting. Ask a person on the task, or file a sub-task to whoever can unblock it."
DEFAULTS: list[dict[str, Any]] = [
    {
        "id": OWNER_ON_TEAM,
        "name": "Owner on the team",
        "pipeline_id": "engineering",
        "stages": ["engineering-on-deck", "engineering-build", "engineering-fixes-1", "engineering-fixes-2"],
        "event": "enters,owner_changes",
        "timer": {},
        "cond": {"owner_not_on_team": ""},
        "jev": "",
        "then": "block",
        "then_params": {},
        "reason": "The stage has nobody; the pipeline's manager picks an owner. A person may set it anyway.",
        "builtin": OWNER_ON_TEAM,
    },
    {
        "id": REAL_WAIT,
        "name": "Snooze only for a real wait",
        "pipeline_id": "",
        "stages": [],
        "event": "bot_snoozes",
        "timer": {},
        "cond": {},
        "jev": (
            "The bot is blocked, not waiting: it cannot go on because of something it lacks or that it must ask for "
            "(no access, a missing tool or credential, a decision someone must make), rather than waiting on something "
            "outside that will come by itself (a date, a release, a merge, a customer, another task)."
        ),
        "then": "block",
        "then_params": {},
        "reason": BLOCKED,
        "builtin": REAL_WAIT,
    },
]
# What the engine still does in code, read-only on the Rules page ("in code"). Candidates for later default rules.
BUILT_IN: list[dict[str, str]] = [
    {
        "name": "Snooze ladder",
        "what": "A bot's fourth snooze in one stage is not taken: the pipeline's manager gets the task.",
        "where": "backend/task_engine.py snooze (BOT_SNOOZES)",
    },
    {
        "name": "On ice tells the manager",
        "what": "A bot's snooze with no end date tells the pipeline's manager.",
        "where": "backend/task_engine.py snooze",
    },
    {
        "name": "Late",
        "what": "A task past its stage's expected time is Late and its manager is told.",
        "where": "backend/never_stuck.py",
    },
    {
        "name": "No owner: the manager picks",
        "what": "A stage with nobody set wakes the pipeline's manager to pick an owner (C25).",
        "where": "backend/task_engine.py _turn_of",
    },
    {
        "name": "Pushed past tells the manager",
        "what": "A move forward past what is not met tells the pipeline's manager.",
        "where": "backend/task_engine.py move",
    },
    {
        "name": "Private tasks stay where they can be read",
        "what": "A private task never enters a stage whose owner cannot read it.",
        "where": "backend/task_engine.py shut_out",
    },
    {
        "name": "No bot past a person's OK",
        "what": "A bot never starts or moves a task past a stage that needs a person's OK.",
        "where": "backend/task_engine.py enter, move",
    },
]
_JSON = ("stages", "timer", "cond", "then_params")


def _h() -> Any:
    from . import hubdb as _hubdb_mod

    hubdb: Any = _hubdb_mod

    return hubdb


def _problem(code: str, detail: str, status: int = 422) -> Exception:
    from .store import Problem as _Problem

    Problem: Any = _Problem

    return Problem(code, detail, status)


def has_table(conn: Any) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stage_rules'").fetchone())


# ----------------------------------------------------------------------------- reading
def view(row: Any) -> dict[str, Any]:
    """A rule as the API and the page read it: JSON decoded, its scope, its cost."""
    out = dict(row)
    if "then_do" in out:
        out["then"] = out.pop("then_do")
    for key in _JSON:
        try:
            out[key] = json.loads(out.get(key) or ("[]" if key == "stages" else "{}"))
        except ValueError:
            out[key] = [] if key == "stages" else {}
    out["events"] = [e for e in str(out.get("event") or "").split(",") if e]
    out["scope"] = "stage" if out["stages"] else ("pipeline" if out.get("pipeline_id") else "company")
    out["cost"] = cost(out)
    return out


def cost(row: Any) -> str:
    """Free, or one Jev call per event. Timers never call a model."""
    return "one Jev call per event" if (row.get("jev") or "").strip() and row.get("event") != "timer" else "free"


def _scope_rank(rule: Any) -> int:
    return 0 if rule["stages"] else (1 if rule["pipeline_id"] else 2)


def applying(
    conn: Any, pipeline_id: str | None, stage_id: str | None, event: str | None = None
) -> list[dict[str, Any]]:
    """The active, live rules that apply to a task in `stage_id` of `pipeline_id` on `event` (None: every event):
    this stage's first, then the whole pipeline's, then the company's."""
    if not has_table(conn) or not pipeline_id or not stage_id:
        return []
    rows = conn.execute(
        "SELECT * FROM stage_rules WHERE status='active' AND retired_at IS NULL AND pipeline_id IN ('', ?) "
        "ORDER BY created_at, id",
        (pipeline_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        rule = view(row)
        if rule["stages"] and stage_id not in rule["stages"]:
            continue
        if event is not None and event not in rule["events"]:
            continue
        out.append(rule)
    return sorted(out, key=_scope_rank)


def listed(
    conn: Any, pipeline_id: Any = None, stage_id: Any = None, status: Any = None, retired: bool = False
) -> list[dict[str, Any]]:
    """Every rule, for the Rules page and the stage panel: on `pipeline_id` (and the company's), on `stage_id`."""
    if not has_table(conn):
        return []
    rows = conn.execute("SELECT * FROM stage_rules ORDER BY pipeline_id, created_at, id").fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        rule = view(row)
        if not retired and rule.get("retired_at"):
            continue
        if status and rule["status"] != status:
            continue
        if pipeline_id and rule["pipeline_id"] not in ("", pipeline_id):
            continue
        if stage_id and rule["stages"] and stage_id not in rule["stages"]:
            continue
        out.append(rule)
    return out


def get(conn: Any, rule_id: str) -> dict[str, Any] | None:
    if not has_table(conn):
        return None
    row = conn.execute("SELECT * FROM stage_rules WHERE id=?", (rule_id,)).fetchone()
    return view(row) if row else None


# ----------------------------------------------------------------------------- teams
def _manager_group(conn: Any, pipeline_id: str) -> str:
    """The pipeline's team: its manager's group. A manager in the built-in group (the General Manager standing in
    for a pipeline with no manager of its own) names no team, so the group named after the pipeline stands in, and
    without one the rule does not hold."""
    from . import groups as _groups_mod

    groups: Any = _groups_mod
    row = conn.execute("SELECT manager_id FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
    manager = row[0] if row else None
    gid = _group_of(conn, manager)[0] if manager else ""
    if gid and gid != groups.BUILT_IN:
        return gid
    every: Any = groups._roster(conn).get("org_groups") or {}
    return pipeline_id if pipeline_id and pipeline_id in every else ""


def _group_of(conn: Any, actor: str | None) -> tuple[str, list[str]]:
    """The group `actor` is in and every group above it ('' and [] for none)."""
    from . import groups as _groups_mod
    from . import people as _people_mod

    groups: Any = _groups_mod
    people: Any = _people_mod

    if not actor:
        return "", []
    roster = groups._roster(conn)
    kind, _, ident = str(actor).partition(":")
    if kind == "bot":
        gid = people.team_of(ident, groups._configs(conn), roster) or ""
    elif kind in ("human", "person"):
        found: Any = people.person(ident, roster) or {}
        gid = str(found.get("team") or "")
    else:
        return "", []
    gid = str(gid or "")
    chain: list[str] = list(people.group_chain(gid, roster)) if gid else []
    return gid, chain


def team_name(conn: Any, gid: str) -> str:
    from . import groups as _groups_mod

    groups: Any = _groups_mod

    every: Any = groups._roster(conn).get("org_groups") or {}
    group: Any = every.get(gid) or {}
    return str(group.get("name") or gid)


def _team(conn: Any, rule: Any, task_row: Any, key: Any) -> str:
    gid = str(rule["cond"].get(key) or "")
    return gid or _manager_group(conn, task_row.get("pipeline_id") or rule.get("pipeline_id") or "")


# ----------------------------------------------------------------------------- the If
def matches(conn: Any, rule: Any, task_row: Any, *, owner: Any = ..., facts: Any = None) -> tuple[bool, str]:
    """Whether the rule's fixed conditions hold for the task, and the words that say why (for the history). `owner`:
    who would own it (default: its owner now). `facts`: what the event carries (`until`, `why`, `hours`). A condition
    Tico cannot judge (no such group, no manager's group) does not hold: a rule never refuses on a guess. The Jev
    question is not asked here (`judge_snooze`)."""
    cond = rule["cond"] if isinstance(rule.get("cond"), dict) else view(rule)["cond"]
    rule = {**rule, "cond": cond}
    facts = facts or {}
    who = task_row.get("owner_id") if owner is ... else owner
    said: list[str] = []
    for key in ("owner_on_team", "owner_not_on_team"):
        if key not in cond:
            continue
        gid = _team(conn, rule, task_row, key)
        if not gid or not who:
            return False, ""
        inside = gid in _group_of(conn, who)[1]
        if inside != (key == "owner_on_team"):
            return False, ""
        name = _h().actor_name(conn, who)
        said.append(f"{name} is {'' if inside else 'not '}on the {team_name(conn, gid)} team")
    kind = cond.get("owner_kind")
    if kind:
        if not who or (kind == "bot") != _core.is_bot(who):
            return False, ""
    hours = cond.get("snooze_hours_over")
    if hours is not None:
        if facts.get("hours") is not None and float(facts["hours"]) <= float(hours):
            return False, ""
        said.append(f"snoozed longer than {hours} h")
    label = cond.get("label")
    if label:
        listed_tags: Any = task_row.get("tags") or []
        tags = {str(cast(dict[str, Any], t).get("name") if isinstance(t, dict) else t).lower() for t in listed_tags}
        labels = task_row.get("labels_json")
        if isinstance(labels, str):
            try:
                named: Any = json.loads(labels) or []
                tags |= {str(x).lower() for x in named}
            except ValueError:
                pass
        if str(label).lower() not in tags:
            return False, ""
    if cond.get("requester_person") is not None:
        if bool(cond["requester_person"]) != _core.is_human(task_row.get("requester_id") or ""):
            return False, ""
    return True, "; ".join(said)


def _line(rule: Any, said: str) -> str:
    """What the history says: "Rule <name>: <why>"."""
    return f"Rule {rule['name']}: {said or rule.get('reason') or 'its condition held'}"


# ----------------------------------------------------------------------------- owner: enters, owner changes
def owner_check(conn: Any, task_row: Any, stage_row: Any, owner: Any, event: str) -> tuple[dict[str, Any], str] | None:
    """The first Block rule on `event` (`enters` or `owner_changes`) that refuses `owner` for the task in `stage_row`,
    with the history line naming it; None when no rule refuses. A rule with a Jev question is not read here: an owner
    is never refused on a model call inside a write."""
    if not owner or stage_row.get("kind") != "work" or from_rule():
        return None
    for rule in applying(conn, task_row.get("pipeline_id"), stage_row["id"], event):
        if rule["then"] != "block" or rule["jev"]:
            continue
        held, said = matches(conn, rule, {**task_row, "stage_id": stage_row["id"]}, owner=owner)
        if held:
            return rule, _line(rule, said)
    return None


def from_rule() -> bool:
    """Whether this write is a rule's own action (a Move back, a Task Trigger): it sets off no other rule."""
    from . import task_trigger_rules as _TTR_mod

    TTR: Any = _TTR_mod

    return bool(TTR._CHAIN.get())


def _picks_owner(stage_row: Any) -> bool:
    """Whether the stage picks its owner from several and the pick becomes the task's owner (backend/stage_pick.py):
    there, as in a stage owned by the assignee, an owner a rule clears takes the assignee with it."""
    from . import stage_pick

    pick = stage_pick.config(stage_row)
    return bool(pick and pick.owns_task)


def record(conn: Any, task_id: str, rule: Any, line: str, by: str, what: str = "refused", data: Any = None) -> None:
    """A Block, Tell or Move back on the task's history, naming the rule, and its event."""
    H = _h()
    more: Any = data or {}
    detail: dict[str, Any] = {"rule": rule["id"], "name": rule["name"], "reason": line, **more}
    H._task_event(conn, task_id, by, "stage_rule", None, rule["id"], line, data=detail)
    H.event(conn, by, f"stage_rule.{what}", task_id, detail)


# ----------------------------------------------------------------------------- bot snoozes: one Jev call
def _hours(until: str | None) -> float | None:
    if not until:
        return None
    from datetime import datetime, timezone

    try:
        end = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return (end - datetime.now(timezone.utc)).total_seconds() / 3600


def _questions(rules: Any) -> dict[str, Any]:
    from clients import judge as _judge_mod

    J: Any = _judge_mod

    shared = J.load_set(QUESTION_SET)["questions"]["rule"]["instructions"]
    return {
        f"rule_{i}": {"type": "noul", "instructions": {**shared, "question": rule["jev"]}}
        for i, rule in enumerate(rules)
    }


def _state(task_row: Any, why: str, until: str | None) -> dict[str, Any]:
    stage: Any = task_row.get("stage") or {}
    return {
        "task": {
            "title": str(task_row.get("title") or "")[:300],
            "body": "" if task_row.get("private") else str(task_row.get("body") or "")[:4000],
            "stage": stage.get("name") or task_row.get("stage_id") or "",
        },
        "event": {"kind": "a bot snoozes the task", "why": str(why or "")[:2000], "until": until or ""},
    }


def _ask_jev(store: Any, state: Any, questions: Any) -> dict[str, float] | None:
    """Jev's p for each question, or None when Jev is off, unset or failed (the write goes: fail open)."""
    ask = JEV
    if ask is None:
        store = store or _installed_store
        if store is None:
            return None
        from . import judge as _judge_mod

        judge: Any = _judge_mod

        with store.read() as c:
            ask = judge.server_engine(store, c, judge.primary_engine(store.settings, store))
        if ask is None:
            return None
    try:
        result = ask(state, questions, LABEL)
        answers: Any = result.get("answers") or {}
        out: dict[str, float] = {}
        for q in questions:
            one: Any = answers.get(q) or {}
            p: Any = one.get("noul")
            out[q] = float(p)
        return out
    except Exception:  # noqa: BLE001 - any failure of the call passes the write: a rule never blocks on an error
        log.warning("stage rule: the Jev call failed", exc_info=True)
        return None


def snooze_rules(conn: Any, task_row: Any, by: str, why: str, until: str | None) -> Any:
    """What a bot's snooze of `task_row` meets, read with no model call: (a Block whose If is fixed conditions only, as
    (rule, line), or None; the rules whose fixed conditions hold and whose Jev question is still to ask). A person's
    snooze is not judged: (None, [])."""
    if not _core.is_bot(by):
        return None, []
    rules = [
        r
        for r in applying(conn, task_row.get("pipeline_id"), task_row.get("stage_id"), "bot_snoozes")
        if r["then"] == "block"
    ]
    facts = {"why": why, "until": until, "hours": _hours(until)}
    asked: list[dict[str, Any]] = []
    for rule in rules:
        held, said = matches(conn, rule, task_row, facts=facts)
        if not held:
            continue
        if not rule["jev"]:
            return (rule, _line(rule, said)), []
        asked.append(rule)
    return None, asked


def _match_at() -> float:
    """The question set's match threshold (questions/stage-rule.json), so an edit there is the one that counts;
    MATCH_AT when the set cannot be read."""
    try:
        from clients import judge as _judge_mod

        judge: Any = _judge_mod
        found: Any = judge.load_set(QUESTION_SET)
        return float(found["thresholds"]["match"])
    except Exception:  # noqa: BLE001 - a missing or odd set keeps the default
        return MATCH_AT


def ask_snooze(
    task_row: Any, why: str, until: str | None, asked: Any, *, store: Any = None, unjudged: list[str] | None = None
) -> Any:
    """Every Jev question of `asked` in ONE call, with no connection held: the first rule Jev says holds, as (rule,
    line), or None (none holds, or Jev is off or failed: the snooze goes). `unjudged` collects the task when Jev gave
    no answer, so a sweep can tell judged from let through."""
    if not asked:
        return None
    questions = _questions(asked)
    answers = _ask_jev(store, _state(task_row, why, until), questions)
    if answers is None:
        if unjudged is not None:
            unjudged.append(str(task_row["id"]))
        return None
    for i, rule in enumerate(asked):
        p = answers.get(f"rule_{i}")
        if p is not None and p >= _match_at():
            return rule, _line(rule, "")
    return None


def judge_snooze(
    conn: Any, task_row: Any, by: str, why: str, until: str | None, *, store: Any = None, fixed_only: Any = False
) -> Any:
    """`snooze_rules` then, unless `fixed_only` (inside a write), `ask_snooze`."""
    fixed, asked = snooze_rules(conn, task_row, by, why, until)
    if fixed or fixed_only:
        return fixed
    return ask_snooze(task_row, why, until, asked, store=store)


# ----------------------------------------------------------------------------- defaults and the release sweep
def ensure_defaults(conn: Any) -> list[str]:
    """Seed each default rule once, by id (a retired or edited one is left as it is). A default whose pipeline the
    company does not have is not made; its stages are those the pipeline has. Returns the ids made. The first time
    Owner on the team is made, the release sweep clears the owners it refuses on open tasks (`sweep_owners`)."""
    if not has_table(conn):
        return []
    made: list[str] = []
    H = _h()
    for rule in DEFAULTS:
        if conn.execute("SELECT 1 FROM stage_rules WHERE id=?", (rule["id"],)).fetchone():
            continue
        stages = list(rule["stages"])
        if rule["pipeline_id"]:
            have = {r[0] for r in conn.execute("SELECT id FROM stages WHERE pipeline_id=?", (rule["pipeline_id"],))}
            if not have:
                continue
            stages = [s for s in stages if s in have]
            if rule["stages"] and not stages:
                continue
        ts = H.now()
        conn.execute(
            "INSERT INTO stage_rules (id, name, pipeline_id, stages, event, timer, cond, jev, then_do, then_params, "
            "reason, status, builtin, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?)",
            (
                rule["id"],
                rule["name"],
                rule["pipeline_id"],
                json.dumps(stages),
                rule["event"],
                json.dumps(rule["timer"]),
                json.dumps(rule["cond"]),
                rule["jev"],
                rule["then"],
                json.dumps(rule["then_params"]),
                rule["reason"],
                rule["builtin"],
                _core.KEEPER,
                ts,
                ts,
            ),
        )
        made.append(rule["id"])
    if OWNER_ON_TEAM in made:
        sweep_owners(conn)
    return made


def sweep_owners(conn: Any) -> list[str]:
    """Owner on the team over the open tasks in its stages, once at release: each refused owner is cleared (the stage
    has nobody, as with no assignee), the history names the rule, and each manager gets ONE wake listing them.
    Returns the task ids changed."""
    from . import task_engine as _E_mod
    from . import task_wakes as _task_wakes_mod

    E: Any = _E_mod
    task_wakes: Any = _task_wakes_mod

    rule = get(conn, OWNER_ON_TEAM)
    if not rule or rule.get("retired_at") or rule["status"] != "active":
        return []
    marks = ",".join("?" * len(rule["stages"])) or "''"
    rows = conn.execute(
        f"SELECT id FROM tasks WHERE status='open' AND pipeline_id=? AND stage_id IN ({marks}) AND owner_id IS NOT NULL",
        (rule["pipeline_id"], *rule["stages"]),
    ).fetchall()
    changed: dict[str, list[dict[str, Any]]] = {}
    for (task_id,) in rows:
        row = E.task(conn, task_id)
        if not row:
            continue
        held, said = matches(conn, rule, row, owner=row.get("owner_id"))
        if not held:
            continue
        line = _line(rule, said)
        fields = {"owner_id": None, "version": int(row.get("version") or 0) + 1, "updated_at": _h().now()}
        if row["stage"].get("owner") == "assignee" or _picks_owner(row["stage"]):
            fields["assignee_id"] = None
        E._write(conn, task_id, **fields)
        record(conn, task_id, rule, line, _core.KEEPER, data={"owner": row.get("owner_id"), "sweep": True})
        pipe: Any = row.get("pipeline") or {}
        manager = pipe.get("manager_id")
        changed.setdefault(manager or "", []).append({**row, "line": line})
    for manager, found in changed.items():
        if not _core.is_bot(manager):
            continue
        lines = "\n".join(f"- {t.get('title') or t['id']}: {t['line']}" for t in found)
        first: Any = E.task(conn, found[0]["id"])
        if not first:
            continue
        task_wakes.add(
            conn,
            first,
            manager,
            first["visit"],
            f"Stage rules: {len(found)} tasks have no owner now. Pick one for each and reassign it.\n{lines}",
            wake_key=f"stage-rules-sweep:{OWNER_ON_TEAM}:{manager}",
        )
    return [t["id"] for found in changed.values() for t in found]


SWEPT = "stage_rules_snooze_sweep"


def sweep_snoozes(store: Any) -> list[str]:
    """Snooze only for a real wait over the open bot snoozes, once at release: each one its rules refuse ends now
    (the task is the bot's again), the history names the rule, and each pipeline manager gets ONE wake listing them.
    Jev is asked with no write open, one call per snooze. Returns the task ids unsnoozed; [] once it has run."""
    from . import task_engine as _E_mod
    from . import task_wakes as _task_wakes_mod

    E: Any = _E_mod
    task_wakes: Any = _task_wakes_mod
    with store.transaction() as c:
        if (
            not has_table(c)
            or not c.execute(
                "INSERT OR IGNORE INTO registry_metadata (key, value) VALUES (?, ?)", (SWEPT, _h().now())
            ).rowcount
        ):
            return []
        ids = [
            str(r[0])
            for r in c.execute(
                "SELECT t.id FROM tasks t JOIN snoozes z ON z.task_id = t.id AND z.ended_at IS NULL "
                "WHERE t.status='open' AND z.by_id LIKE 'bot:%' ORDER BY t.id"
            ).fetchall()
        ]
    refused: list[tuple[str, dict[str, Any], str]] = []
    unjudged: list[str] = []
    for task_id in ids:
        with store.read() as c:
            row: Any = E.task(c, task_id)
            if not row or not row.get("snooze"):
                continue
            snooze: Any = row["snooze"]
            why, until = str(snooze.get("why") or ""), snooze.get("until_at")
            fixed, asked = snooze_rules(c, row, str(snooze.get("by_id")), why, until)
        found = fixed or ask_snooze(row, why, until, asked, store=store, unjudged=unjudged)
        if found:
            refused.append((task_id, found[0], found[1]))
    told: dict[str, list[tuple[Any, str]]] = {}
    with store.transaction() as c:
        for task_id, rule, line in refused:
            row = E.task(c, task_id)
            if not row or not row.get("snooze"):
                continue
            E.unsnooze(c, task_id, _core.KEEPER, line)
            record(c, task_id, rule, line, _core.KEEPER, data={"sweep": True})
            pipe: Any = row.get("pipeline") or {}
            told.setdefault(str(pipe.get("manager_id") or ""), []).append((row, line))
        for manager, found_rows in told.items():
            if not _core.is_bot(manager):
                continue
            lines = "\n".join(f"- {r.get('title') or r['id']}: {line}" for r, line in found_rows)
            first: Any = E.task(c, found_rows[0][0]["id"])
            task_wakes.add(
                c,
                first,
                manager,
                first["visit"],
                f"Stage rules: {len(found_rows)} snoozed tasks were blocked, not waiting, and are back with their "
                f"bots.\n{lines}",
                wake_key=f"stage-rules-sweep:{REAL_WAIT}:{manager}",
            )
        if unjudged:
            # Jev gave no answer on some (down, or not set up yet): the sweep is not used up, so the next start
            # judges the snoozes still open; the ones ended here are no longer open bot snoozes.
            c.execute("DELETE FROM registry_metadata WHERE key=?", (SWEPT,))
    log.info(
        "stage rules: snooze sweep judged %d of %d bot snoozes, ended %d",
        len(ids) - len(unjudged),
        len(ids),
        len(refused),
    )
    return [task_id for task_id, _, _ in refused]


# ----------------------------------------------------------------------------- writes (the routes are part 3's)
def _check(fields: Any) -> dict[str, Any]:
    out = dict(fields)
    events = [e for e in str(out.get("event") or "").split(",") if e]
    if not events or any(e not in EVENTS for e in events) or ("timer" in events and len(events) > 1):
        raise _problem("validation", f"When is one of {', '.join(EVENTS)} (actions may be comma-separated)")
    if "timer" in events:
        timer: Any = out.get("timer") or {}
        if timer.get("kind") not in TIMERS or not isinstance(timer.get("hours"), (int, float)) or timer["hours"] <= 0:
            raise _problem("validation", f"A timer is a kind ({', '.join(TIMERS)}) and a number of hours")
        if (out.get("jev") or "").strip():
            raise _problem("validation", "A timer never asks Jev: use fixed conditions")
    if out.get("then") not in THENS:
        raise _problem("validation", f"Then is one of {', '.join(THENS)}")
    if out["then"] == "block" and not set(events) & {"enters", "owner_changes", "bot_snoozes"}:
        raise _problem("validation", "Block needs an action to refuse: enters, owner changes or a bot snoozes")
    unknown = set(out.get("cond") or {}) - set(CONDITIONS)
    if unknown:
        raise _problem("validation", f"Unknown condition: {', '.join(sorted(unknown))}")
    if not str(out.get("name") or "").strip():
        raise _problem("validation", "A rule has a name")
    return out


def _columns(fields: Any) -> dict[str, Any]:
    keep = ("name", "pipeline_id", "stages", "event", "timer", "cond", "jev", "then", "then_params", "reason")
    return {
        ("then_do" if k == "then" else k): (json.dumps(fields[k]) if k in _JSON else fields[k])
        for k in keep
        if k in fields
    }


def _fields(rule: Any) -> dict[str, Any]:
    keep = ("name", "pipeline_id", "stages", "event", "timer", "cond", "jev", "then", "then_params", "reason")
    return {k: rule[k] for k in keep if k in rule}


def add(conn: Any, by: str, fields: Any, *, suggests: str = "") -> dict[str, Any]:
    """A new rule; a bot's is Suggested until a person accepts it."""
    H = _h()
    base: dict[str, Any] = {
        "pipeline_id": "",
        "stages": [],
        "timer": {},
        "cond": {},
        "jev": "",
        "then_params": {},
        "reason": "",
    }
    data = _check({**base, **fields})
    ts, rid = H.now(), "sr_" + uuid.uuid4().hex[:12]
    status = "active" if _core.is_human(by) else "suggested"
    cols = _columns(data)
    names = ", ".join(cols)
    conn.execute(
        f"INSERT INTO stage_rules (id, {names}, status, suggests, created_by, created_at, updated_at) "
        f"VALUES (?, {', '.join('?' * len(cols))}, ?, ?, ?, ?, ?)",
        (rid, *cols.values(), status, suggests, by, ts, ts),
    )
    return get(conn, rid) or {}


def edit(conn: Any, by: str, rule_id: str, fields: Any, revision: int | None = None) -> dict[str, Any]:
    """Change a rule. A bot's change is a Suggested copy that replaces it once accepted."""
    rule = get(conn, rule_id)
    if not rule or rule.get("retired_at"):
        raise _problem("not_found", "No such rule", 404)
    if revision is not None and int(revision) != int(rule["revision"]):
        raise _problem("rule_changed", "The rule changed since you read it. Read it again.", 409)
    data = _check({**_fields(rule), **fields})
    if not _core.is_human(by):
        return add(conn, by, data, suggests=rule_id)
    cols = _columns(data)
    conn.execute(
        f"UPDATE stage_rules SET {', '.join(f'{k}=?' for k in cols)}, updated_at=?, revision=revision+1 WHERE id=?",
        (*cols.values(), _h().now(), rule_id),
    )
    return get(conn, rule_id) or {}


def retire(conn: Any, by: str, rule_id: str, why: str = "") -> dict[str, Any]:
    """Retire a rule. A bot only suggests it: a Suggested row with `then_params.retire` set."""
    rule = get(conn, rule_id)
    if not rule or rule.get("retired_at"):
        raise _problem("not_found", "No such rule", 404)
    if not _core.is_human(by):
        fields = _fields(rule)
        return add(
            conn, by, {**fields, "then_params": {**rule["then_params"], "retire": why or True}}, suggests=rule_id
        )
    conn.execute("UPDATE stage_rules SET retired_at=?, updated_at=? WHERE id=?", (_h().now(), _h().now(), rule_id))
    return get(conn, rule_id) or {}


def accept(conn: Any, person: str, rule_id: str) -> dict[str, Any]:
    """A person takes a Suggested rule: a new one goes live; a change replaces the rule it suggests."""
    if not _core.is_human(person):
        raise _problem("forbidden", "A person accepts a suggested rule", 403)
    rule = get(conn, rule_id)
    if not rule or rule["status"] != "suggested" or rule.get("retired_at"):
        raise _problem("not_found", "No such suggested rule", 404)
    ts = _h().now()
    if rule["suggests"]:
        params = dict(rule["then_params"])
        if params.pop("retire", None) is not None:
            out = retire(conn, person, rule["suggests"])
        else:
            out = edit(conn, person, rule["suggests"], {**_fields(rule), "then_params": params})
        conn.execute("UPDATE stage_rules SET retired_at=?, updated_at=? WHERE id=?", (ts, ts, rule_id))
        return out
    conn.execute("UPDATE stage_rules SET status='active', updated_at=? WHERE id=?", (ts, rule_id))
    return get(conn, rule_id) or {}


def skip(conn: Any, person: str, rule_id: str) -> dict[str, Any]:
    if not _core.is_human(person):
        raise _problem("forbidden", "A person skips a suggested rule", 403)
    rule = get(conn, rule_id)
    if not rule or rule["status"] != "suggested" or rule.get("retired_at"):
        raise _problem("not_found", "No such suggested rule", 404)
    conn.execute("UPDATE stage_rules SET retired_at=?, updated_at=? WHERE id=?", (_h().now(), _h().now(), rule_id))
    return get(conn, rule_id) or {}


def install(store: Any) -> None:
    """The store Jev's calls are counted on (server start), and the release sweep of bot snoozes, once, in the
    background: it asks Jev, so it never holds up the start."""
    import threading

    global _installed_store
    _installed_store = store

    def sweep() -> None:
        try:
            sweep_snoozes(store)
        except Exception:  # noqa: BLE001 - the sweep is a one-off tidy; a start never fails on it
            log.warning("stage rules: the snooze sweep failed", exc_info=True)

    try:
        with store.read() as c:
            waiting = has_table(c) and not c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (SWEPT,)).fetchone()
            waiting = (
                waiting
                and c.execute("SELECT 1 FROM snoozes WHERE ended_at IS NULL AND by_id LIKE 'bot:%' LIMIT 1").fetchone()
            )
    except Exception:  # noqa: BLE001 - a store with no tables yet has nothing to sweep
        waiting = None
    if waiting:
        threading.Thread(target=sweep, name="stage-rules-sweep", daemon=True).start()
