# pyright: strict
"""One record for a pipeline, its stages and their requirements (docs/pipelines-v3.md, "Modules", "Stage settings").

The same record is what the editor shows, what the API and the hub send, and what MCP reads. This module is the only
writer of `pipelines`, `stages` and of the requirements set on a stage (`requirements.stage_id`).

- `read` gives the record. Only work stages are in `stages`, in order. Done and Closed come apart, under `done` and
  `closed`: every pipeline ends in them, so a caller can neither remove nor reorder them.
- `check` lists every problem with a record, as sentences. `save` refuses the first one.
- `save` creates or changes a pipeline. Stage and requirement ids are stable: a row in the record with an id is
  changed in place, a row without one is made. A stage left out is retired, a requirement left out is withdrawn, so
  history and answers stay readable. Each save raises `settings_version` by one and logs who changed what.
- `retire` takes a pipeline out of use once it holds no open task.
- `ensure_presets` makes each preset an org does not have yet. It never changes one the org has.

Every function takes the caller's open write transaction and never commits. Names and signatures are in
docs/dev/pipelines-v3-build.md.
"""

import copy
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NoReturn, cast

from . import access as Access
from . import hubdb_core, pipeline_rights, task_engine
from . import pipeline_presets_more as more
from . import pipeline_seats as S
from . import task_requirements as R
from .hubdb_core import KEEPER as SYSTEM

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

# hubdb_core is untyped: its actor helpers get their types here.
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human

PIPELINE_FIELDS = ("name", "goal", "prefix", "manager_id", "owner_id", "private", "factory_id", "preset")
STAGE_FIELDS = (
    "name",
    "purpose",
    "owner",
    "by_hand",
    "task_limit",
    "expected_minutes",
    "expected_bots_only",
    "on_enter",
)
REQUIREMENT_FIELDS = ("kind", "settings", "can_push_past", "who_oks", "person_id", "line")
WHO_OKS = ("none", "people_only", "people_with_exceptions")
OK_KINDS = ("ok", "page")  # the kinds whose "Who OKs" switch means something on a stage (kinds 2 and 3)
NAMES_PERSON = ("ok", "page")  # only a requirement met with a button names someone (C29): a person or a bot
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BUILT_IN = ("done", "closed")
PREFIX = re.compile(r"^[A-Za-z]{1,2}$")
ASSIGNEE = "assignee"

PRESETS: dict[str, Row] = {
    "general": {
        "id": "general",
        "name": "General",
        "goal": "Where every task goes unless it belongs to another pipeline.",
        "prefix": None,
        "manager_id": None,  # the Task Manager bot, when the org has one (ensure_presets)
        "owner_id": None,
        "private": False,
        "factory_id": None,
        "preset": "general",
        "stages": [
            {
                "id": "general-todo",
                "name": "To do",
                "purpose": "",
                "owner": ASSIGNEE,
                "by_hand": False,
                "task_limit": None,
                "expected_minutes": 1440,
                "expected_bots_only": True,
                "on_enter": {},
                "requirements": [{"kind": "owner_done"}],
            }
        ],
        "done": {"id": "general-done"},
        "closed": {"id": "general-closed"},
    },
}
TASK_MANAGER = "task-manager"  # the built-in bot that manages General (backend/task_manager.py)


def _stage(sid: str, name: str, purpose: str, minutes: int | None, requirements: list[Row], **more: Any) -> Row:
    return {
        "id": sid,
        "name": name,
        "purpose": purpose,
        "owner": None,  # the stage's role (ROLES), filled by preset()
        "by_hand": False,
        "task_limit": None,
        "expected_minutes": minutes,
        "expected_bots_only": True,
        "on_enter": {},
        "requirements": requirements,
        **more,
    }


def _page(line: str, rubric: str, **settings: bool) -> Row:
    return {"kind": "page", "line": line, "settings": {"rubric": rubric, **settings}}


REVIEW_RUBRIC = (
    "A real review of the newest commit of every linked PR: what was read and run, what is wrong, and what to fix. "
    "Not a summary of the change."
)
# The Product and Engineering presets (docs/pipelines-v3.md "Default pipelines"). Made only for a company that builds
# software (a blank answer is yes), once its setup is finished (ensure_presets).
PRESETS["engineering"] = {
    "id": "engineering",
    "name": "Engineering",
    "goal": "From a task to a published release.",
    "prefix": "E",
    "manager_id": None,
    "owner_id": None,
    "private": False,
    "factory_id": "product",
    "preset": "engineering",
    "stages": [
        _stage("engineering-on-deck", "On deck", "Waits for room in Build, oldest first.", None, []),
        _stage(
            "engineering-build",
            "Build",
            "Build it in its own thread, open a PR and link it, and get the checks green.",
            1440,
            [{"kind": "pr_open"}, {"kind": "checks_pass"}],
            task_limit=3,
        ),
        _stage(
            "engineering-review-1",
            "Review 1",
            "Review the newest commit and attach a Review page.",
            120,
            [_page("Review page", REVIEW_RUBRIC, at_head=True)],
        ),
        _stage(
            "engineering-fixes-1",
            "Fixes 1",
            "Fix what the review found, push, and say done once the checks are green.",
            240,
            [{"kind": "owner_done"}, {"kind": "checks_pass"}],
        ),
        _stage(
            "engineering-review-2",
            "Review 2",
            "Review the newest commit again and attach a Review page.",
            120,
            [_page("Review page", REVIEW_RUBRIC, at_head=True)],
        ),
        _stage(
            "engineering-fixes-2",
            "Fixes 2",
            "Fix what the second review found and attach a Review page that shows what was built.",
            240,
            [
                {"kind": "checks_pass"},
                _page(
                    "Review page",
                    "Shows what was built, with screenshots or output, and how it was checked. Enough for a person "
                    "to OK it without opening the code.",
                ),
            ],
        ),
        _stage(
            "engineering-merge",
            "Merge",
            "The PRs are in the merge queue. If GitHub takes one out, queue it again, fix it on its branch, or send "
            "the task back to Fixes 2.",
            120,
            [{"kind": "pr_merged"}],
            on_enter={"queue_prs": True},
        ),
        _stage(
            "engineering-release",
            "Release",
            "Waits for a published release that holds the merge.",
            1440,
            [{"kind": "in_release"}],
        ),
    ],
    "done": {"id": "engineering-done"},
    "closed": {"id": "engineering-closed"},
}
PRESETS["product"] = {
    "id": "product",
    "name": "Product",
    "goal": "Discerning, high-quality specs that ship",
    "prefix": "P",
    "manager_id": None,
    "owner_id": None,
    "private": False,
    "factory_id": "product",
    "preset": "product",
    "stages": [
        _stage(
            "product-ideas",
            "Ideas",
            "Ideas wait here. Move one to Spec when it is worth a spec. Snooze one to park it.",
            None,
            [],
            by_hand=True,
        ),
        _stage(
            "product-spec",
            "Spec",
            "A spec a builder can build from.",
            2880,
            [_page("Spec page", "Covers goal, scope, what done looks like")],
            task_limit=2,
        ),
        _stage(
            "product-in-development",
            "In development",
            "Engineering builds it. This task leaves when every Engineering task filed for it is Done.",
            7200,
            [{"kind": "linked_done"}],
            on_enter={"file_into": "engineering"},
        ),
        _stage(
            "product-validate",
            "Validate",
            "Check it in production and attach a Checked in production page that names the version production runs.",
            1440,
            [
                _page(
                    "Checked in production",
                    "Names the version production runs and shows the change working there. If that version is older "
                    "than the release that holds this work, it is not yet.",
                    with_release=True,
                )
            ],
        ),
    ],
    "done": {"id": "product-done"},
    "closed": {"id": "product-closed"},
}
SOFTWARE = ("engineering", "product")  # the presets only a company that builds software gets
FACTORY = {"id": "product", "name": "Product", "goal": "From an idea to something checked in production."}
# Who fills each preset's manager, its stage owners, and which stages' page requirement a person OKs (D5, D9).
ROLES: dict[str, Row] = {
    "engineering": {
        "manager": S.ENGINEERING_PIPELINE_MANAGER,
        "stages": {
            "engineering-on-deck": S.BUILDER,
            "engineering-build": S.BUILDER,
            "engineering-review-1": S.REVIEWER_1,
            "engineering-fixes-1": S.BUILDER,
            "engineering-review-2": S.REVIEWER_2,
            "engineering-fixes-2": S.BUILDER,
            "engineering-merge": S.RELEASE_MANAGER,
            "engineering-release": S.RELEASE_MANAGER,
        },
        "ok": ("engineering-fixes-2",),
    },
    "product": {
        "manager": S.PRODUCT_PIPELINE_MANAGER,
        "stages": {
            "product-ideas": S.PRODUCT_OWNER,
            "product-spec": S.PRODUCT_OWNER,
            "product-in-development": S.PRODUCT_OWNER,
            "product-validate": S.PRODUCT_OWNER,
        },
        "ok": ("product-spec",),
    },
}
# Growth Experiments, Lead, Opportunity, Customer and Success Experiments, with their Growth and Success factories.
PRESETS.update(more.PRESETS)
ROLES.update(more.ROLES)


def _h() -> Any:
    from . import hubdb

    return hubdb


def _refuse(conn: "sqlite3.Connection", by: str, rule: str, detail: str) -> NoReturn:
    _h().refuse(conn, by, rule, detail)
    raise AssertionError("refuse() always raises")  # pragma: no cover


def _loads(value: object) -> Any:
    return _h()._json(value, {}) if isinstance(value, str) else value


def _as_dict(value: object) -> Row | None:
    """A dict with text keys, or None for anything that is not a dict."""
    if not isinstance(value, dict):
        return None
    return {str(k): v for k, v in cast("dict[object, Any]", value).items()}


# ----------------------------------------------------------------------------- read
def _requirement_out(row: Row) -> Row:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "settings": _loads(row.get("settings")) or {},
        "can_push_past": bool(row.get("can_push_past")),
        "who_oks": row.get("who_oks") or "none",
        "person_id": row.get("person_id"),
        "line": row.get("line") or "",
    }


def _stage_out(conn: "sqlite3.Connection", row: Row) -> Row:
    return {
        "id": row["id"],
        "name": row["name"],
        "purpose": row.get("purpose") or "",
        "owner": row.get("owner"),
        "by_hand": bool(row.get("by_hand")),
        "task_limit": row.get("task_limit"),
        "expected_minutes": row.get("expected_minutes"),
        "expected_bots_only": bool(row.get("expected_bots_only")),
        "on_enter": _loads(row.get("on_enter")) or {},
        "requirements": [_requirement_out(r) for r in R.on_stage(conn, row["id"])],
    }


def read(conn: "sqlite3.Connection", pipeline_id: str) -> Row | None:
    """The record of one pipeline: its own fields, its work stages in order with their requirements, and its Done and
    Closed stages apart. None when there is no such pipeline. A retired one reads with `retired_at` set."""
    row = task_engine.pipeline(conn, pipeline_id)
    if not row:
        return None
    order = task_engine.stages(conn, pipeline_id)
    out: Row = {"id": row["id"], **{key: row.get(key) for key in PIPELINE_FIELDS}}
    out["goal"] = out["goal"] or ""
    out["private"] = bool(out["private"])
    out["settings_version"] = int(row.get("settings_version") or 1)
    out["retired_at"] = row.get("retired_at")
    out["stages"] = [_stage_out(conn, s) for s in order if s["kind"] == "work"]
    for kind in BUILT_IN:
        found = next((s for s in order if s["kind"] == kind), None)
        out[kind] = {"id": found["id"], "name": found["name"]} if found else None
    return out


# ----------------------------------------------------------------------------- the shape of a record
def _text(value: object, what: str, problems: list[str]) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        problems.append(f"{what} must be text.")
        return ""
    return value.strip()


def _ref(value: object, what: str, problems: list[str]) -> str | None:
    return _text(value, what, problems) or None


def _flag(value: object, what: str, problems: list[str], default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    problems.append(f"{what} is on or off.")
    return default


def _count(value: object, sentence: str, problems: list[str]) -> int | None:
    """A whole number above zero, or None for nothing."""
    if value is None or value == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    problems.append(sentence)
    return None


def _dict(value: object, sentence: str, problems: list[str]) -> Row:
    if value is None:
        return {}
    found = _as_dict(value)
    if found is None:
        problems.append(sentence)
        return {}
    return found


def _list(value: object, sentence: str, problems: list[str]) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(sentence)
        return []
    return list(value)  # pyright: ignore[reportUnknownArgumentType]


def _requirement_in(raw: object, stage_name: str, problems: list[str]) -> Row:
    item = _dict(raw, f"A requirement on {stage_name} is not a requirement.", problems)
    kind = _text(item.get("kind"), f"A requirement's kind on {stage_name}", problems)
    ok_kind = kind in OK_KINDS
    never = kind in R.NEVER_PUSHED_PAST
    push = _flag(item.get("can_push_past"), f"Can be pushed past on {stage_name}", problems, default=not never)
    who = _text(item.get("who_oks"), f"Who OKs on {stage_name}", problems) or (
        "people_with_exceptions" if ok_kind else "none"
    )
    return {
        "id": _ref(item.get("id"), f"A requirement id on {stage_name}", problems),
        "kind": kind,
        "settings": _dict(item.get("settings"), f"A requirement's settings on {stage_name} are not a set.", problems),
        "can_push_past": push,
        "who_oks": who,
        "person_id": _ref(item.get("person_id"), f"The person on a requirement on {stage_name}", problems),
        "line": _text(item.get("line"), f"A requirement's line on {stage_name}", problems),
    }


def _stage_in(raw: object, position: int, problems: list[str]) -> Row:
    item = _dict(raw, f"Stage {position + 1} is not a stage.", problems)
    name = _text(item.get("name"), f"Stage {position + 1}'s name", problems)
    label = name or f"stage {position + 1}"
    return {
        "id": _ref(item.get("id"), f"The id of {label}", problems),
        "name": name,
        "purpose": _text(item.get("purpose"), f"The purpose of {label}", problems),
        "owner": _ref(item.get("owner"), f"The owner of {label}", problems),
        "by_hand": _flag(item.get("by_hand"), f"Tasks leave by hand on {label}", problems),
        "task_limit": _count(item.get("task_limit"), f"The limit on {label} is a whole number above zero.", problems),
        "expected_minutes": _count(
            item.get("expected_minutes"), f"Expected time on {label} is a whole number of minutes above zero.", problems
        ),
        "expected_bots_only": _flag(
            item.get("expected_bots_only"), f"Expected time for bots only on {label}", problems
        ),
        "on_enter": _dict(
            item.get("on_enter"),
            f"On enter on {label} is nothing, file a linked task into a pipeline, or queue the PRs to merge.",
            problems,
        ),
        "requirements": [
            _requirement_in(r, label, problems)
            for r in _list(item.get("requirements"), f"The requirements on {label} are not a list.", problems)
        ],
    }


def _parse(record: object) -> tuple[Row, list[str]]:
    """The record with every field in its place and its defaults filled in, and the problems with its shape."""
    problems: list[str] = []
    item = _dict(record, "A pipeline record is a set of fields.", problems)
    prefix = _ref(item.get("prefix"), "The prefix", problems)
    out: Row = {
        "id": _ref(item.get("id"), "The pipeline's id", problems),
        "name": _text(item.get("name"), "The name", problems),
        "goal": _text(item.get("goal"), "The goal", problems),
        "prefix": prefix.upper() if prefix and PREFIX.match(prefix) else prefix,
        "manager_id": _ref(item.get("manager_id"), "The manager", problems),
        "owner_id": _ref(item.get("owner_id"), "The owner", problems),
        "private": _flag(item.get("private"), "Private", problems),
        "factory_id": _ref(item.get("factory_id"), "The factory", problems),
        "preset": _ref(item.get("preset"), "The preset", problems),
    }
    out["stages"] = [
        _stage_in(s, i, problems)
        for i, s in enumerate(_list(item.get("stages"), "The stages are not a list.", problems))
    ]
    for kind in BUILT_IN:
        given = _as_dict(item.get(kind)) or {}
        out[kind] = {"id": _ref(given.get("id"), f"The id of {kind}", problems)}
    return out, problems


# ----------------------------------------------------------------------------- check
def _actor_exists(conn: "sqlite3.Connection", actor: str) -> bool:
    """A bot that exists, or a person who is here and can act (not left, removed, or with sign-in off)."""
    if is_bot(actor):
        return _h().bot(conn, actor) is not None
    if is_human(actor):
        return R.active_person(conn, actor)
    return False


def _kind_name(kind: str) -> str:
    return R.KIND_NAMES.get(kind, kind)


def _check_settings(req: Row, label: str, problems: list[str]) -> None:
    """A requirement's settings: only its kind's keys (task_requirements.SETTINGS), each of its type."""
    name = _kind_name(req["kind"])
    for key, value in req["settings"].items():
        if key not in R.SETTINGS.get(req["kind"], ()):
            problems.append(f"{name} has no setting {key} (on {label}).")
        elif key in ("at_head", "with_release", "prereleases") and not isinstance(value, bool):
            problems.append(f"{key} on {label} is on or off.")
        elif key == "rubric" and not isinstance(value, str):
            problems.append(f"What Jev checks on {label} must be text.")
        elif key == "release_repo" and (
            not isinstance(value, str) or (value.strip() and not REPO.match(value.strip()))
        ):
            problems.append(f"The release repo on {label} is owner/name, or empty for the server's.")


def _check_requirement(conn: "sqlite3.Connection", stage: Row, req: Row, problems: list[str]) -> None:
    kind, label = req["kind"], stage["name"] or "a stage"
    if kind not in R.KINDS:
        problems.append(f"{kind or 'A blank kind'} is not a requirement kind (on {label}).")
        return
    name = _kind_name(kind)
    if kind == "answer":
        problems.append(f"Answer from someone is never set on a stage (on {label}). Add it to one task instead.")
    _check_settings(req, label, problems)
    if kind in R.NEVER_PUSHED_PAST and req["can_push_past"]:
        problems.append(f"{name} can't be pushed past (C3), on {label}.")
    if req["who_oks"] not in WHO_OKS:
        problems.append(f"Who OKs on {label} is people only or people, with exceptions.")
    elif req["who_oks"] != "none" and kind not in OK_KINDS:
        problems.append(f"Who OKs is only for an OK or a page, not {name} (on {label}).")
    person = req["person_id"]
    if person and kind not in NAMES_PERSON:
        problems.append(
            f"Only a requirement a person can meet with a button can name a person (C29): not {name}, on {label}."
        )
    elif person and not _actor_exists(conn, person):
        problems.append(f"{person} is not a person or a bot here (on {label}).")  # removed, or left
    elif kind == "ok" and not person:
        problems.append(f"An OK from someone on {label} names the person or bot who OKs it.")
    if req["id"]:
        found = _h()._one(conn, "SELECT stage_id, visit_id, kind FROM requirements WHERE id=?", (req["id"],))
        if found and (found.get("visit_id") or found.get("stage_id") != stage["id"]):
            problems.append(f"Requirement {req['id']} belongs to another stage, not {label}.")
        elif found and found["kind"] != kind:
            problems.append(f"A requirement's kind can't change on {label}: remove it and add a new one.")


def _check_on_enter(conn: "sqlite3.Connection", record: Row, stage: Row, label: str, problems: list[str]) -> None:
    action = stage["on_enter"]
    if not action:
        return
    if list(action) == ["queue_prs"] and action["queue_prs"] is True:
        return
    target = action.get("file_into") if list(action) == ["file_into"] else None
    if not isinstance(target, str) or not target:
        problems.append(
            f"On enter on {label} is nothing, file a linked task into a pipeline, or queue the PRs to merge."
        )
        return
    found = task_engine.pipeline(conn, target)
    if target != record["id"] and (not found or found.get("retired_at")):
        problems.append(f"{label} files into a pipeline that does not exist: {target}.")
    if not any(r["kind"] == "linked_done" for r in stage["requirements"]):
        problems.append(f"A stage that files a task always has Linked work done among its requirements ({label}).")


def _handoffs(conn: "sqlite3.Connection", leave_out: str | None) -> dict[str, set[str]]:
    """Every saved pipeline's handoffs: {pipeline id: the pipelines its stages file into}."""
    edges: dict[str, set[str]] = {}
    for pipeline_id, on_enter in conn.execute(
        "SELECT s.pipeline_id, s.on_enter FROM stages s JOIN pipelines p ON p.id = s.pipeline_id "
        "WHERE s.retired_at IS NULL AND p.retired_at IS NULL AND s.kind='work' AND s.on_enter <> '{}'"
    ).fetchall():
        target = (_as_dict(_loads(on_enter)) or {}).get("file_into")
        if pipeline_id != leave_out and isinstance(target, str):
            edges.setdefault(str(pipeline_id), set()).add(target)
    return edges


def _loop(conn: "sqlite3.Connection", record: Row) -> str | None:
    """The problem when the record's handoffs, with every saved pipeline's, form a loop back to it."""
    me = record["id"] or "\0new"
    edges = _handoffs(conn, record["id"])
    for stage in record["stages"]:
        target = stage["on_enter"].get("file_into")
        if isinstance(target, str) and target:
            edges.setdefault(me, set()).add(target)
    seen: set[str] = set()
    todo = [(t, [me, t]) for t in sorted(edges.get(me, set()))]
    while todo:
        here, path = todo.pop()
        if here == me:
            names = [
                record["name"] if p == me else ((task_engine.pipeline(conn, p) or {}).get("name") or p) for p in path
            ]
            return f"Filing {' into '.join(names)} would loop. A pipeline can't be saved if its handoffs form a loop."
        if here in seen:
            continue
        seen.add(here)
        todo.extend((t, [*path, t]) for t in sorted(edges.get(here, set())))
    return None


def _tasks(n: int) -> str:
    return f"{n} task" if n == 1 else f"{n} tasks"


def _open_tasks(conn: "sqlite3.Connection", stage_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE pipeline_id IS NOT NULL AND stage_id=? AND status='open'", (stage_id,)
        ).fetchone()[0]
    )


def _problems(conn: "sqlite3.Connection", record: Row) -> list[str]:
    """What is wrong with a parsed record, against the database."""
    problems: list[str] = []
    pid = record["id"]
    existing = task_engine.pipeline(conn, pid) if pid else None
    if existing and existing.get("retired_at"):
        problems.append("This pipeline is retired.")
    problems.extend(_private_problems(conn, record, existing))
    name = record["name"]
    if not name:
        problems.append("A pipeline needs a name.")
    elif conn.execute(
        "SELECT 1 FROM pipelines WHERE name=? COLLATE NOCASE AND retired_at IS NULL AND id IS NOT ?", (name, pid)
    ).fetchone():
        problems.append(f"Another pipeline is called {name}.")
    prefix = record["prefix"]
    if prefix and not PREFIX.match(prefix):
        problems.append("A prefix is one or two letters.")
    elif (
        prefix
        and conn.execute(
            "SELECT 1 FROM pipelines WHERE prefix=? AND retired_at IS NULL AND id IS NOT ?", (prefix, pid)
        ).fetchone()
    ):
        problems.append(f"Another pipeline uses the prefix {prefix}.")
    for key, what in (("manager_id", "The manager"), ("owner_id", "The owner")):
        if record[key] and not _actor_exists(conn, record[key]):
            problems.append(f"{what} must be a bot or a person here, not {record[key]}.")
    if (
        record["factory_id"]
        and not conn.execute(
            "SELECT 1 FROM factories WHERE id=? AND retired_at IS NULL", (record["factory_id"],)
        ).fetchone()
    ):
        problems.append(f"There is no factory {record['factory_id']}.")
    stages: list[Row] = record["stages"]
    if not stages:
        problems.append("A pipeline needs at least one stage.")
    names: set[str] = set()
    ids: set[str] = set()
    # A requirement id is one row: listed on two stages, the second write would take the first stage's requirement.
    req_ids: set[str] = set()
    for position, stage in enumerate(stages):
        label = stage["name"] or f"Stage {position + 1}"
        if not stage["name"]:
            problems.append(f"Stage {position + 1} needs a name.")
        elif stage["name"].lower() in BUILT_IN:
            problems.append(f"Every pipeline ends in Done and Closed: call {stage['name']} something else.")
        elif stage["name"].lower() in names:
            problems.append(f"Two stages are called {stage['name']}.")
        names.add(stage["name"].lower())
        if stage["id"]:
            if stage["id"] in ids:
                problems.append(f"Stage {stage['id']} is listed twice.")
            ids.add(stage["id"])
            found = task_engine.stage(conn, stage["id"])
            if found and (found["pipeline_id"] != pid or found["kind"] != "work"):
                problems.append(f"Stage {stage['id']} is not a work stage of this pipeline.")
        owner = stage["owner"]
        if owner and owner != ASSIGNEE and not _actor_exists(conn, owner):
            problems.append(f"The owner of {label} must be a bot, a person or the task's assignee, not {owner}.")
        if position == 0 and stage["task_limit"]:
            problems.append(f"The first stage of a pipeline has no limit ({label}).")
        _check_on_enter(conn, record, stage, label, problems)
        for req in stage["requirements"]:
            if req["id"] and req["id"] in req_ids:
                problems.append(f"Requirement {req['id']} is listed twice.")
            if req["id"]:
                req_ids.add(req["id"])
            _check_requirement(conn, stage, req, problems)
    if existing:
        for gone in task_engine.stages(conn, pid or ""):
            if gone["kind"] == "work" and gone["id"] not in ids:
                held = _open_tasks(conn, gone["id"])
                if held:
                    problems.append(f"{gone['name']} holds open tasks. Move its {_tasks(held)} first.")
    loop = _loop(conn, record)
    if loop:
        problems.append(loop)
    return problems


def _has_tasks(conn: "sqlite3.Connection", pipeline_id: str) -> bool:
    """Whether any task, open or ended, is in the pipeline. A deleted one is not: a restore into a private pipeline
    makes it private (task_engine.restore)."""
    return bool(
        conn.execute(
            "SELECT 1 FROM tasks WHERE pipeline_id IS NOT NULL AND pipeline_id=? LIMIT 1", (pipeline_id,)
        ).fetchone()
    )


def _private_problems(conn: "sqlite3.Connection", record: Row, existing: Row | None) -> list[str]:
    """A private pipeline (backend/private_pipelines.py) makes every task in it private, read by its manager and the
    company's owner. So it is chosen as it is made (a preset, or a brand-new pipeline) or while it is still empty, never
    changed on one that holds tasks, and its manager must be one who reads private tasks: never the Task Manager."""
    problems: list[str] = []
    was = bool(existing and existing.get("private"))
    if existing and was != record["private"] and _has_tasks(conn, str(existing["id"])):
        problems.append(
            "A pipeline that holds tasks can't be made private or company-wide. Make a new pipeline instead."
        )
    if record["private"]:
        manager = record["manager_id"]
        if not manager:
            problems.append("A private pipeline needs a manager. Its tasks are read by its manager and the owner.")
        elif pipeline_rights.canonical(conn, manager) in {
            pipeline_rights.canonical(conn, f"bot:{TASK_MANAGER}"),
            pipeline_rights.canonical(conn, _task_manager(conn)),
        }:
            problems.append("The Task Manager can't manage a private pipeline: it reads no private task.")
    return problems


def check(conn: "sqlite3.Connection", record: Row) -> list[str]:
    """Every problem with a record, as sentences. Empty means it can be saved."""
    parsed, problems = _parse(record)
    return problems + _problems(conn, parsed)


# ----------------------------------------------------------------------------- rights
def may_change(conn: "sqlite3.Connection", by: str, pipeline_row: Row | None) -> bool:
    """Who may change a pipeline: the company's owner and Admins, the pipeline's owner and its manager (a manager bot
    included: "Customers and manager bots can edit all of it"). Only the company's owner and Admins make a new one. The
    engine itself may do both."""
    if by == SYSTEM:
        return True
    if is_human(by):
        found: Row = _h().human(conn, by) or {}
        email = str(found.get("email") or "").strip().lower()
        if email and email in set(Access.owner_and_admin_emails(conn)):
            return True
    if not pipeline_row:
        return False
    me = pipeline_rights.canonical(conn, by)
    return bool(me) and me in (
        pipeline_rights.canonical(conn, pipeline_row.get("owner_id")),
        pipeline_rights.canonical(conn, pipeline_row.get("manager_id")),
    )


def _may_change(conn: "sqlite3.Connection", by: str, row: Row | None, seen_version: int | str | None) -> None:
    if not may_change(conn, by, row):
        if row:
            _refuse(conn, by, "task_settings", "Only the pipeline's owner, its manager and Admins can change it.")
        _refuse(conn, by, "task_settings", "Only the company's owner and Admins can make a pipeline.")
    if not row:
        return
    if seen_version is None:
        if is_bot(by):
            _refuse(conn, by, "task_changed", "Say which version of the pipeline you saw.")
        return
    if str(seen_version) != str(row.get("settings_version")):
        _refuse(conn, by, "task_changed", "The pipeline changed since you looked. Read it again.")


# ----------------------------------------------------------------------------- save
def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "stage"


def _free_stage_id(conn: "sqlite3.Connection", base: str, taken: set[str]) -> str:
    """`base`, or `base-2`, `base-3`... the first that no stage has."""
    n, candidate = 1, base
    while candidate in taken or task_engine.stage(conn, candidate):
        n += 1
        candidate = f"{base}-{n}"
    taken.add(candidate)
    return candidate


def _flat(record: Row | None) -> Row:
    """A record as {path: value}, for the log of what changed."""
    if not record:
        return {}
    out: Row = {key: record.get(key) for key in PIPELINE_FIELDS}
    for i, stage in enumerate(record["stages"]):
        at = f"stages.{stage['id']}"
        out[f"{at}.position"] = i
        out.update({f"{at}.{key}": stage.get(key) for key in STAGE_FIELDS})
        for j, req in enumerate(stage["requirements"]):
            on = f"{at}.requirements.{req['id']}"
            out[f"{on}.position"] = j
            out.update({f"{on}.{key}": req.get(key) for key in REQUIREMENT_FIELDS})
    return out


def _changes(before: Row | None, after: Row | None) -> Row:
    old, new = _flat(before), _flat(after)
    return {key: [old.get(key), new.get(key)] for key in sorted({*old, *new}) if old.get(key) != new.get(key)}


def _write_pipeline(conn: "sqlite3.Connection", record: Row, existing: Row | None, ts: str) -> str:
    H = _h()
    values = [
        record["name"],
        record["goal"],
        record["prefix"],
        record["manager_id"],
        record["owner_id"],
        1 if record["private"] else 0,
        record["factory_id"],
    ]
    if existing:
        conn.execute(
            "UPDATE pipelines SET name=?, goal=?, prefix=?, manager_id=?, owner_id=?, private=?, factory_id=?, "
            "settings_version = settings_version + 1, updated_at=? WHERE id=?",
            (*values, ts, existing["id"]),
        )
        return str(existing["id"])
    pid = record["id"] or H.new_id()
    conn.execute(
        "INSERT INTO pipelines (id, name, goal, prefix, manager_id, owner_id, private, factory_id, preset, "
        "settings_version, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
        (pid, *values, record["preset"], ts, ts),
    )
    return pid


def _write_requirements(conn: "sqlite3.Connection", by: str, stage_id: str, reqs: list[Row], ts: str) -> bool:
    """Write a stage's requirements in order. Returns True when one was withdrawn."""
    H = _h()
    kept: list[str] = []
    for position, req in enumerate(reqs):
        values = (
            req["kind"],
            H._dump(req["settings"]),
            1 if req["can_push_past"] else 0,
            req["who_oks"],
            req["person_id"],
            req["line"],
            position,
        )
        found = req["id"] and conn.execute("SELECT 1 FROM requirements WHERE id=?", (req["id"],)).fetchone()
        if found:
            conn.execute(
                "UPDATE requirements SET kind=?, settings=?, can_push_past=?, who_oks=?, person_id=?, line=?, "
                "position=?, withdrawn_at=NULL WHERE id=?",
                (*values, req["id"]),
            )
            kept.append(req["id"])
            continue
        rid = req["id"] or H.new_id()
        conn.execute(
            "INSERT INTO requirements (kind, settings, can_push_past, who_oks, person_id, line, position, id, "
            "stage_id, added_by_id, by_bot, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (*values, rid, stage_id, str(by), 1 if is_bot(by) else 0, ts),
        )
        kept.append(rid)
    marks = ",".join("?" * len(kept))
    gone = conn.execute(
        "UPDATE requirements SET withdrawn_at=? WHERE stage_id=? AND withdrawn_at IS NULL"
        + (f" AND id NOT IN ({marks})" if kept else ""),
        (ts, stage_id, *kept),
    ).rowcount
    return gone > 0


def _write_stages(conn: "sqlite3.Connection", by: str, pid: str, record: Row, ts: str) -> bool:
    """Write the work stages in order, then Done and Closed after them. Returns True when the change could let a
    waiting task move on: a requirement withdrawn, a limit raised or lifted, a stage no longer by hand."""
    H = _h()
    before = {s["id"]: s for s in task_engine.stages(conn, pid)}
    old_rows = {
        r["id"]: r
        for r in H._rows(conn.execute("SELECT * FROM stages WHERE pipeline_id=?", (pid,)))  # retired ones too
    }
    wanted: list[str] = [s["id"] for s in record["stages"] if s["id"] and s["id"] in old_rows]
    loosened = False
    for sid, row in before.items():
        if row["kind"] == "work" and sid not in wanted:
            conn.execute("UPDATE stages SET retired_at=?, updated_at=? WHERE id=?", (ts, ts, sid))
    # Names are unique among a pipeline's live stages: park the kept ones on their ids first, so a swap of two names
    # never meets itself halfway. Not "\0": SQLite's NOCASE stops at a NUL, so "\0review-1" and "\0review-2" are one.
    for sid in wanted:
        conn.execute("UPDATE stages SET name=? WHERE id=?", ("\x01" + sid, sid))
    taken: set[str] = set()
    for position, stage in enumerate(record["stages"]):
        values = (
            position,
            stage["name"],
            stage["purpose"],
            stage["owner"],
            1 if stage["by_hand"] else 0,
            stage["task_limit"],
            stage["expected_minutes"],
            1 if stage["expected_bots_only"] else 0,
            H._dump(stage["on_enter"]),
        )
        sid = stage["id"]
        old = old_rows.get(sid) if sid else None
        if old:
            conn.execute(
                "UPDATE stages SET position=?, name=?, purpose=?, owner=?, by_hand=?, task_limit=?, "
                "expected_minutes=?, expected_bots_only=?, on_enter=?, updated_at=?, retired_at=NULL WHERE id=?",
                (*values, ts, sid),
            )
            limit, was = stage["task_limit"], old.get("task_limit")
            if (was and (not limit or limit > was)) or (old.get("by_hand") and not stage["by_hand"]):
                loosened = True
        else:
            sid = sid or _free_stage_id(conn, f"{pid}-{_slug(stage['name'])}", taken)
            conn.execute(
                "INSERT INTO stages (position, name, purpose, owner, by_hand, task_limit, expected_minutes, "
                "expected_bots_only, on_enter, id, pipeline_id, kind, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'work',?,?)",
                (*values, sid, pid, ts, ts),
            )
        loosened = _write_requirements(conn, by, sid, stage["requirements"], ts) or loosened
    end = len(record["stages"])
    for offset, kind in enumerate(BUILT_IN):
        found = next((s for s in before.values() if s["kind"] == kind), None)
        if found:
            conn.execute("UPDATE stages SET position=?, updated_at=? WHERE id=?", (end + offset, ts, found["id"]))
            continue
        given = record[kind].get("id")
        sid = given if given and not task_engine.stage(conn, given) else _free_stage_id(conn, f"{pid}-{kind}", taken)
        conn.execute(
            "INSERT INTO stages (id, pipeline_id, position, kind, name, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (sid, pid, end + offset, kind, kind.capitalize(), ts, ts),
        )
    return loosened


def save(conn: "sqlite3.Connection", by: str, record: Row, seen_version: int | str | None = None) -> Row:
    """Create a pipeline (no id, or an id that does not exist yet) or change one. Returns the record as `read` gives
    it. Refuses `task_settings` with the first problem `check` finds, and `task_changed` when `seen_version` is not the
    pipeline's `settings_version`; a bot must give it to change a pipeline, a person may leave it out."""
    parsed, problems = _parse(record)
    existing = task_engine.pipeline(conn, parsed["id"]) if parsed["id"] else None
    _may_change(conn, by, existing, seen_version)
    if existing and existing.get("private") and not parsed["private"] and is_bot(by):
        _refuse(conn, by, "task_settings", "Only a person makes a private pipeline company-wide.")
    problems += _problems(conn, parsed)
    if problems:
        _refuse(conn, by, "task_settings", problems[0])
    before = read(conn, existing["id"]) if existing else None
    ts = _h().now()
    pid = _write_pipeline(conn, parsed, existing, ts)
    loosened = _write_stages(conn, by, pid, parsed, ts)
    after = read(conn, pid)
    assert after is not None
    _h().event(
        conn,
        by,
        "pipeline.save",
        pid,
        {"version": after["settings_version"], "made": existing is None, "changes": _changes(before, after)},
    )
    if existing and loosened:
        task_engine.fill_open_slots(conn, [s["id"] for s in after["stages"]])
        after = read(conn, pid)
        assert after is not None
    return after


def retire(conn: "sqlite3.Connection", by: str, pipeline_id: str, seen_version: int | str | None = None) -> Row:
    """Take a pipeline out of use. Its tasks that ended stay where they are. Refused while it holds an open task, or
    while another pipeline files tasks into it."""
    row = task_engine.pipeline(conn, pipeline_id)
    if not row:
        _refuse(conn, by, "not-found", "No such pipeline")
    _may_change(conn, by, row, seen_version)
    if not row.get("retired_at"):
        held = int(
            conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE pipeline_id IS NOT NULL AND pipeline_id=? AND status='open'",
                (pipeline_id,),
            ).fetchone()[0]
        )
        if held:
            _refuse(conn, by, "task_settings", f"{row['name']} holds open tasks. Move its {_tasks(held)} first.")
        into = [p for p, targets in _handoffs(conn, pipeline_id).items() if pipeline_id in targets]
        if into:
            names = ", ".join((task_engine.pipeline(conn, p) or {}).get("name") or p for p in sorted(into))
            _refuse(conn, by, "task_settings", f"{names} files tasks into {row['name']}. Change that first.")
        ts = _h().now()
        conn.execute(
            "UPDATE pipelines SET retired_at=?, updated_at=?, settings_version = settings_version + 1 WHERE id=?",
            (ts, ts, pipeline_id),
        )
        _h().event(conn, by, "pipeline.retire", pipeline_id, {"version": int(row["settings_version"]) + 1})
    out = read(conn, pipeline_id)
    assert out is not None
    return out


# ----------------------------------------------------------------------------- presets
def _task_manager(conn: "sqlite3.Connection") -> str | None:
    """The built-in Task Manager bot's actor, when the org has one that is not archived."""
    found = _h().bot(conn, TASK_MANAGER)
    if not found or str(found.get("state") or "active") == "archived":
        return None
    return f"bot:{found['id']}"


def _onboarding(conn: "sqlite3.Connection") -> Row:
    """The setup record's answers and whether setup finished (backend/onboarding.py `load`), read raw: this module
    runs at every start, before the rest of the app is loaded."""
    found = conn.execute("SELECT value FROM registry_metadata WHERE key='onboarding'").fetchone()
    return _as_dict(_loads(found[0]) if found else None) or {}


def makes_software(conn: "sqlite3.Connection") -> bool:
    """Whether the company builds software: its answer to "Software is the product" is not "no" (a blank is yes, D7)."""
    answers = _as_dict(_onboarding(conn).get("answers")) or {}
    return str(answers.get("software_product") or "").strip().lower() != "no"


def _set_up(conn: "sqlite3.Connection") -> bool:
    """Whether the company finished its setup, so its answers and its bots are known."""
    return bool(_onboarding(conn).get("completed"))


def preset(conn: "sqlite3.Connection", key: str) -> Row:
    """A preset's record for this org, ready to save: its manager, its stage owners and the person on its people OKs
    filled in from the bots and people the org has (pipeline_seats). No work stage is left without an owner: a role
    nobody fills goes up the ladder, a stage's to the pipeline's manager and a manager's to the Task Manager, until the
    role's bot appears (`_fill`). A private preset's ladder stops at its manager: never the Task Manager."""
    record = copy.deepcopy(PRESETS[key])
    if key == "general":
        record["manager_id"] = _task_manager(conn)
    roles = ROLES.get(key)
    if not roles:
        return record
    who = {**S.resolve(conn), **more.resolve(conn)}
    # A private preset never falls to the Task Manager, which reads no private task: no bot, no manager (and then
    # ensure_presets does not make it).
    stand_in = None if record["private"] else _task_manager(conn)
    record["manager_id"] = who.get(roles["manager"]) or stand_in
    person = S.ok_person(conn, record["manager_id"])
    for stage in record["stages"]:
        role = roles["stages"].get(stage["id"])
        stage["owner"] = (who.get(role) or record["manager_id"]) if role else stage["owner"]
        if stage["id"] in roles["ok"]:
            for req in stage["requirements"]:
                if req["kind"] == "page":
                    req["person_id"] = person
    return record


def _untouched(conn: "sqlite3.Connection", pipeline_id: str) -> bool:
    """A preset nobody changed: still at its first settings version and not retired."""
    found = conn.execute("SELECT settings_version, retired_at FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
    return bool(found) and int(found[0] or 1) == 1 and not found[1]


def _fill(conn: "sqlite3.Connection", key: str, pipeline_id: str) -> None:
    """An untouched preset made before its roles' bots existed gets them once they do: an empty manager, an empty
    stage owner, an empty person on a stage's people OK. Nothing a person set is changed, and the settings version
    stays 1, so the next bot that appears is filled in too. A stand-in up the ladder (`preset`: the Task Manager for
    a manager, the manager for a stage) gives way to the role's bot once it exists."""
    record = preset(conn, key)
    ts = _h().now()
    found = conn.execute("SELECT manager_id, private FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
    if found and found[1] and not record.get("manager_id"):
        return  # a private pipeline whose owner bot is gone keeps its manager: nobody stands in
    stand_ins = (found[0] if found else None, _task_manager(conn))
    if record.get("manager_id"):
        conn.execute(
            "UPDATE pipelines SET manager_id=?, updated_at=? WHERE id=? AND (manager_id IS NULL OR manager_id=?)",
            (record["manager_id"], ts, pipeline_id, stand_ins[1]),
        )
    for stage in record["stages"] if key in ROLES else ():
        if stage.get("owner"):
            conn.execute(
                "UPDATE stages SET owner=?, updated_at=? WHERE id=? AND pipeline_id=? "
                "AND (owner IS NULL OR owner=? OR owner=?)",
                (stage["owner"], ts, stage["id"], pipeline_id, *stand_ins),
            )
        for req in stage["requirements"]:
            if req.get("person_id"):
                conn.execute(
                    "UPDATE requirements SET person_id=? WHERE stage_id=? AND kind=? AND person_id IS NULL "
                    "AND withdrawn_at IS NULL AND visit_id IS NULL",
                    (req["person_id"], stage["id"], req["kind"]),
                )


def _ensure_factory(conn: "sqlite3.Connection") -> None:
    """The Product factory that holds Product and Engineering (S12), made when missing."""
    if conn.execute("SELECT 1 FROM factories WHERE id=?", (FACTORY["id"],)).fetchone():
        return
    ts = _h().now()
    position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM factories WHERE retired_at IS NULL").fetchone()
    conn.execute(
        "INSERT INTO factories (id, name, goal, position, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (FACTORY["id"], FACTORY["name"], FACTORY["goal"], int(position[0]), ts, ts),
    )


def _held(conn: "sqlite3.Connection", pipeline_id: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM tasks WHERE pipeline_id IS NOT NULL AND pipeline_id=? AND status='open' LIMIT 1",
            (pipeline_id,),
        ).fetchone()
    )


def _retire_software(conn: "sqlite3.Connection") -> list[str]:
    """A company that says it builds no software: its untouched Product and Engineering, holding no open task, are
    retired. The settings version stays 1, so they come back if the answer turns to yes. Product first: Engineering
    stays while a pipeline still in use files into it. Returns the ids retired."""
    retired: list[str] = []
    for key in reversed(SOFTWARE):
        pid = PRESETS[key]["id"]
        if not _untouched(conn, pid) or _held(conn, pid):
            continue
        if any(pid in targets for targets in _handoffs(conn, pid).values()):
            continue
        ts = _h().now()
        conn.execute("UPDATE pipelines SET retired_at=?, updated_at=? WHERE id=?", (ts, ts, pid))
        _h().event(conn, SYSTEM, "pipeline.retire", pid, {"version": 1, "why": "no software"})
        retired.append(pid)
    return retired


def _bring_back(conn: "sqlite3.Connection", pipeline_id: str) -> bool:
    """A preset retired because the company said it builds no software (retired at version 1), now that it says yes:
    in use again, unless another pipeline took its name or prefix meanwhile."""
    found = conn.execute(
        "SELECT name, prefix, settings_version, retired_at FROM pipelines WHERE id=?", (pipeline_id,)
    ).fetchone()
    if not found or not found[3] or int(found[2] or 1) != 1:
        return False
    if conn.execute(
        "SELECT 1 FROM pipelines WHERE id<>? AND retired_at IS NULL AND (name=? COLLATE NOCASE OR prefix=?)",
        (pipeline_id, found[0], found[1]),
    ).fetchone():
        return False
    conn.execute("UPDATE pipelines SET retired_at=NULL, updated_at=? WHERE id=?", (_h().now(), pipeline_id))
    return True


def ensure_presets(conn: "sqlite3.Connection") -> list[str]:
    """Every start, and the end of setup (onboarding.complete_company): make each preset pipeline the org does not
    have yet. Returns the ids it made.

    - General is made for every company, at the first start.
    - Product and Engineering are made for a company that builds software (`makes_software`: a blank answer is yes),
      once its setup finished, so their roles find the bots setup made. With them comes the Product factory. A company
      that says no gets its untouched pair retired; saying yes again brings that pair back.
    - Growth Experiments, Lead, Opportunity, Customer and Success Experiments (pipeline_presets_more) are made for every
      company once its setup finished, each with its Growth or Success factory. A company set up before they existed
      gets them at its next start.
    - Legal and Finance, private, in the Corporate factory, likewise, but only once their owner bot exists (the Legal
      Owner, the Bookkeeper): never with the Task Manager standing in. `ensure_private` makes them when that bot is
      made.
    - A preset the org has, under its id or as its preset, is never changed, nor one it retired, with one exception:
      while nobody has changed it (settings version 1), an empty manager, stage owner or person on a people OK is
      filled in once the bot or person for it exists.
    - A preset whose name or prefix another pipeline already uses is left out, so a start never fails on it."""
    made: list[str] = []
    software = makes_software(conn)
    if not software:
        _retire_software(conn)
    for key in PRESETS:
        if key in SOFTWARE and not software:
            continue
        have = conn.execute("SELECT id FROM pipelines WHERE id=? OR preset=?", (PRESETS[key]["id"], key)).fetchone()
        if have:
            if key in SOFTWARE:
                _bring_back(conn, str(have[0]))
            if _untouched(conn, str(have[0])):
                _fill(conn, key, str(have[0]))
            continue
        if key in SOFTWARE:
            if not _set_up(conn):
                continue
            _ensure_factory(conn)
        elif key in more.PRESETS:
            if not _set_up(conn):
                continue
            if key in more.PRIVATE and not preset(conn, key)["manager_id"]:
                continue  # made once its owner bot exists (ensure_private), its factory with it
            more.ensure_factory(conn, PRESETS[key]["factory_id"], _h().now())
        record = preset(conn, key)
        if check(conn, record):
            continue
        made.append(save(conn, SYSTEM, record)["id"])
    return made


def ensure_private(conn: "sqlite3.Connection") -> list[str]:
    """Legal and Finance (pipeline_presets_more.PRIVATE), made the moment their owner bot is: a bot made
    (settings_admin.create_bot) and the Bookkeeper's ensure call this. It is the whole `ensure_presets` pass, the same
    one every start runs, so the factories keep their order (Corporate after the others). Returns the private ids it
    made."""
    return [pid for pid in ensure_presets(conn) if pid in more.PRIVATE]


def from_preset(conn: "sqlite3.Connection") -> list[str]:
    """The pipelines a company has only because every company of its kind gets them: presets nobody changed that hold
    no task. `company_import.new_company` passes over them."""
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT p.id FROM pipelines p WHERE p.preset IS NOT NULL AND p.settings_version=1 "
            "AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.pipeline_id = p.id)"
        ).fetchall()
    ]
