# pyright: strict
"""Cutting a release: the Engineering pipeline's Release stage (docs/pipelines-v3.md, "Engineering" and "GitHub").

Release waits on "In a release": the merge commit inside a published release. A release is made the way the repository
is set up to make one, which the In a release requirement names in its `release_with` setting:

- `workflow:<file>` (e.g. `workflow:release.yml`): Tico runs that workflow (`workflow_dispatch`) on the repository's
  default branch. GitHub's Actions: write.
- `tag`: Tico creates the tag the Release Owner names at the task's merge commit, and the repository's own workflow
  publishes the release from the tag. GitHub's Contents: write.
- empty: the repository releases on its own (on every merge, or a release train) or a person cuts each release. Tico
  only waits.

Who acts: the Release Owner, a bot, is woken when a task enters Release with nothing asked yet, and asks with ONE Tico
tool, `tico task release <task>` (`cut`). Tico carries it out through its GitHub App on the Heartbeat (a `release`
source action, backend/source_actions.py), never through a bot's own token: the bot's settings deny `gh workflow run`,
tags and `gh release`. The tool is refused for a task not in a stage that waits for a release, for one no person signed
off before release (a bot asking), and while an earlier ask for the same repository still waits for its release.

One ask serves every task waiting on the same repository: a workflow run asked after a task's merge covers that task
too, so a batch of merged tasks is one release. A tag covers only the task that named it.

Nothing waits silently. With no way set, a task waits for the next release that holds its merge (`NEXT_RELEASE`): only
that release ends it, so it is nobody's turn and no bot is woken or told Late; after RELEASE_WAIT_S a person is told
once (never_stuck). Who may set the way: a person only (pipeline_settings `_changes_release`). GitHub refusing the ask, or no release CUT_WAIT_S after it, is the Release Owner's turn with why (it may ask again or
say why it cannot); a second failure for the repository since the merge, or a permission only a person can grant
(`MISSING`), is a person's, once. History: `release_asked`, `release_cut`, `release_refused`, `release_found`.

Never a second release: the repository may publish one with the merge on its own (on every push) before Tico has read
it. So the tool and the Heartbeat read the repository's releases from GitHub first (item_sources/github.py
`read_releases_now`), and an ask whose merges a published release already holds is not cut (`found_already`); the tool
says which release has them.
"""

import json
import re
from typing import TYPE_CHECKING, Any, cast

from .. import source_actions as SA

if TYPE_CHECKING:
    import sqlite3

    from ..task_requirements import Standing

Row = dict[str, Any]

SOURCE = "github"
RELEASE = "release"  # GitHub's source action that cuts a release
REPO = "repo"  # the item a release action is for: the release repository, `owner/name`
CUT_WAIT_S = 3600  # a release asked for that has not come after this is a failure
# With no way set, the next release ends the wait, whoever cuts it. A person is told once after a week: a weekly
# stable train is the slowest cadence a repository keeps on purpose, and repos that cut pre-releases several times a
# day never get near it. A stage that expects longer keeps its own time (never_stuck._late_release).
RELEASE_WAIT_S = 7 * 86400
NEXT_RELEASE = "Waiting for the next release of"
FAILS_TO_PERSON = 2  # failures for a repository since a task's merge before the task is a person's
WORKFLOW = re.compile(r"^workflow:([A-Za-z0-9_.-]+\.ya?ml)$")
TAG = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
# A reason only a person can clear: the App's installation has not accepted the permission the release needs. The
# words name what to do on GitHub.
MISSING = "Tico's GitHub App lacks"
MISSING_ACTIONS = (
    f"{MISSING} Actions: write, which running the release workflow needs. A person sets Actions to Read and write in "
    "the App's permissions on GitHub and accepts the change for the installation"
)
MISSING_CONTENTS = (
    f"{MISSING} Contents: write, which creating the release tag needs. A person sets Contents to Read and write in "
    "the App's permissions on GitHub and accepts the change for the installation"
)


def _h() -> Any:
    from .. import hubdb

    return hubdb


def _engine() -> Any:
    from .. import task_engine

    return task_engine


def _gs() -> Any:
    from . import github

    return github


def _rows(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


# ----------------------------------------------------------------------------- the repository's way
def way(settings: Row) -> tuple[str, str] | None:
    """How a release of the requirement's repository is cut: ("workflow", file) or ("tag", ""), or None when Tico has
    no way set (the repository releases on its own)."""
    value = str(settings.get("release_with") or "").strip()
    if value == "tag":
        return "tag", ""
    found = WORKFLOW.match(value)
    return ("workflow", found.group(1)) if found else None


def waits_for_next_release(requirement: Row) -> bool:
    """Whether a requirement is In a release with no way set: only the repository's next release, made without Tico,
    ends its wait (never_stuck tells a person once after RELEASE_WAIT_S, and no bot)."""
    return (
        requirement.get("kind") == "item"
        and (requirement.get("source"), requirement.get("condition")) == (SOURCE, "in_release")
        and way(requirement.get("settings") or {}) is None
    )


def valid(value: object) -> bool:
    """Whether a `release_with` setting is one Tico can carry out: empty, `tag`, or `workflow:<file>.yml`."""
    return isinstance(value, str) and (not value.strip() or way({"release_with": value}) is not None)


def _pinned(kind: str, name: str, sha: str, n: int) -> str:
    """What one ask carries out, as its action's `pinned`: `workflow:<file>#n` or `tag:<name>@<sha>#n`. `n` counts the
    visit's asks for the repository, so asking again owes a new action and an older failure stays counted."""
    return f"workflow:{name}#{n}" if kind == "workflow" else f"tag:{name}@{sha}#{n}"


def unpinned(pinned: str) -> tuple[str, str, str]:
    """(kind, name, sha) of an ask's `pinned`: ("workflow", file, "") or ("tag", name, sha)."""
    body = pinned.rpartition("#")[0] or pinned
    kind, _, rest = body.partition(":")
    if kind == "tag":
        name, _, sha = rest.rpartition("@")
        return "tag", name, sha
    return "workflow", rest, ""


def said_as(effect: Row) -> str:
    """An ask as a line says it: "workflow release.yml" or "tag v1.2.0"."""
    kind, name, _ = unpinned(str(effect.get("pinned") or ""))
    return f"{kind} {name}"


def runs_url(repo: str, effect: Row) -> str:
    """Where a person or the Release Owner reads what the ask started on GitHub."""
    kind, name, _ = unpinned(str(effect.get("pinned") or ""))
    return f"https://github.com/{repo}/actions" + (f"/workflows/{name}" if kind == "workflow" else "")


# ----------------------------------------------------------------------------- what the task waits on
def _seconds(since: object, now: object = None) -> float | None:
    H = _h()
    at, then = H.parse_ts(since), H.parse_ts(now or H.now())
    return None if at is None or then is None else (then - at).total_seconds()


def asks(conn: "sqlite3.Connection", repo: str, visit_id: str, merged_at: object) -> list[Row]:
    """The asks that may bring a release holding a merge into `repo`, oldest first: every ask of this visit, and a
    workflow run any task asked after the merge (it runs on the default branch, so it holds the merge). Moot ones are
    left out."""
    H = _h()
    since = H.parse_ts(merged_at) if merged_at else None
    out: list[Row] = []
    for effect in _rows(
        conn,
        "SELECT * FROM effects WHERE source=? AND action=? AND kind=? AND item_key=? AND state<>'moot' "
        "ORDER BY created_at, rowid",
        (SOURCE, RELEASE, SA.ACT, repo),
    ):
        if effect["visit_id"] == visit_id:
            out.append(effect)
            continue
        made = H.parse_ts(effect["created_at"])
        workflow = unpinned(str(effect["pinned"] or ""))[0] == "workflow"
        if workflow and since is not None and made is not None and made >= since:
            out.append(effect)
    return out


def failed(effect: Row, now: object = None) -> bool:
    """Whether an ask failed: GitHub refused it, or it was carried out and no release came for CUT_WAIT_S."""
    if effect["state"] == "failed":
        return True
    quiet = _seconds(effect["updated_at"], now) if effect["state"] == "done" else None
    return quiet is not None and quiet >= CUT_WAIT_S


def failure_line(repo: str, effect: Row) -> str:
    """Why an ask failed, and where to look."""
    reason = str(effect.get("last_error") or "")
    if effect["state"] == "failed":
        return (
            reason if reason.startswith(MISSING) else f"GitHub refused the release of {repo}: {reason or 'no reason'}"
        )
    return f"No release of {repo} an hour after Tico ran {said_as(effect)}: see {runs_url(repo, effect)}"


def _settings(conn: "sqlite3.Connection", task_id: str, visit_id: str | None = None) -> Row | None:
    """The In a release settings the task waits on: in visit `visit_id`, else in its newest visit that had one."""
    found = conn.execute(
        "SELECT r.settings FROM visits v JOIN requirements r ON (r.stage_id = v.stage_id OR r.visit_id = v.id) "
        "AND r.withdrawn_at IS NULL AND r.kind='item' AND r.source=? AND r.condition='in_release' "
        "WHERE v.task_id=? AND (? IS NULL OR v.id=?) ORDER BY v.entered_at DESC, v.rowid DESC LIMIT 1",
        (SOURCE, task_id, visit_id, visit_id),
    ).fetchone()
    if found is None:
        return None
    try:
        value = json.loads(found[0] or "{}")
    except ValueError:
        return {}
    return cast("Row", value) if isinstance(value, dict) else {}


def released(
    conn: "sqlite3.Connection", task_id: str, repo: str | None = None, visit_id: str | None = None
) -> list[str]:
    """Where the task's merges (into `repo` alone, when given) are released, as "v1.2.0 of owner/name", from the
    releases Tico has saved, counted as In a release counts them (pre-releases unless explicitly disabled). Empty
    while any of them is in no release yet, and when nothing merged."""
    GS = _gs()
    settings = _settings(conn, task_id, visit_id)
    if settings is None:
        return []
    out: list[str] = []
    for link in GS.prs_of(conn, task_id):
        metadata: Row | None = link["metadata"]
        if not metadata or metadata["state"] != "merged" or not metadata.get("merged_sha"):
            continue
        where = str(GS.release_repo(settings, str(link["repo"])))
        if repo is not None and where.lower() != repo.lower():
            continue
        release = GS.release_of(
            conn, where, str(metadata["merged_sha"]), prereleases=settings.get("prereleases", True) is not False
        )
        if release is None:
            return []
        out.append(f"{release['tag']} of {where}")
    return sorted(set(out))


def found_already(conn: "sqlite3.Connection", effect: Row) -> bool:
    """An ask about to be carried out whose task's merges into its repository a published release already holds: it is
    not cut (moot), and the task's history says which release has them."""
    task_id, repo = str(effect["task_id"]), str(effect["item_key"])
    found = released(conn, task_id, repo, str(effect["visit_id"]))
    if not found:
        return False
    E = _engine()
    conn.execute(
        "UPDATE effects SET state='moot', updated_at=? WHERE id=? AND state='owed'", (_h().now(), effect["id"])
    )
    data = {"repo": repo, "effect": effect["id"], "releases": found}
    E._event(conn, task_id, E.SYSTEM, "release_found", None, repo, ", ".join(found), data)
    E.changed(conn, task_id, E.SYSTEM)
    return True


def standing(conn: "sqlite3.Connection", task: Row, requirement: Row, repo: str, metadata: Row) -> "Standing":
    """In a release for a merged PR that no saved release holds yet, in `repo` that publishes releases."""
    from ..task_requirements import NOT_MET, WAITING, Standing

    owner = task.get("owner_id")
    visit: Row = task.get("visit") or {}
    how = way(requirement.get("settings") or {})
    found = asks(conn, repo, str(visit.get("id") or ""), metadata.get("merged_at"))
    if not found:
        if how:
            tag = " --tag <name>" if how[0] == "tag" else ""
            return Standing(
                requirement,
                NOT_MET,
                owner,
                f"No release of {repo} asked for yet: cut one with `tico task release {task['id']}{tag}`",
            )
        # The repository releases on its own or a person cuts its releases (it has published some: C31 meets the
        # rest). Only that release ends the wait: nobody's turn, and never_stuck tells a person once after
        # `RELEASE_WAIT_S` (never_stuck._late_release).
        return Standing(requirement, WAITING, SOURCE, f"{NEXT_RELEASE} {repo}")
    newest = found[-1]
    if newest["state"] == "owed":
        return Standing(requirement, WAITING, SOURCE, f"Asking GitHub for a release of {repo} ({said_as(newest)})")
    if not failed(newest):
        at = str(newest["updated_at"])[11:16]
        return Standing(requirement, WAITING, SOURCE, f"Waiting for a release of {repo}: {said_as(newest)} at {at}")
    line = failure_line(repo, newest)
    fails = sum(1 for effect in found if failed(effect))
    if line.startswith(MISSING) or fails >= FAILS_TO_PERSON:
        # Nothing the Release Owner can do again: a person's, once (waiting_on names the person the task falls to).
        return Standing(requirement, WAITING, None, f"{line}. Then ask again: `tico task release {task['id']}`")
    return Standing(requirement, NOT_MET, owner, line)


# ----------------------------------------------------------------------------- the tool
def _release_requirement(conn: "sqlite3.Connection", row: Row) -> Row | None:
    from .. import task_requirements as R

    visit = row.get("visit")
    for requirement in R.for_visit(conn, visit) if visit else []:
        if requirement["kind"] == "item" and (requirement["source"], requirement["condition"]) == (
            SOURCE,
            "in_release",
        ):
            return requirement
    return None


def unsigned(conn: "sqlite3.Connection", row: Row) -> str | None:
    """The stage of the pre-release sign-off this task has not passed, or None when it passed it: the nearest earlier
    stage with a people-only OK or page, whose newest visit holds a person's `ok` (or a person's push past) on each
    of them. A pipeline with no such stage before this one has no sign-off, and that is refused too ("no stage")."""
    from .. import task_engine as E
    from .. import task_requirements as R

    work = [s for s in E.stages(conn, row["pipeline_id"]) if s["kind"] == "work"]
    at = next((i for i, s in enumerate(work) if s["id"] == row["stage_id"]), 0)
    for stage in reversed(work[:at]):
        needed = [r for r in R.on_stage(conn, stage["id"]) if R.people_only(r)]
        if not needed:
            continue
        visit = conn.execute(
            "SELECT id FROM visits WHERE task_id=? AND stage_id=? AND left_at IS NOT NULL "
            "ORDER BY entered_at DESC, rowid DESC LIMIT 1",
            (row["id"], stage["id"]),
        ).fetchone()
        answers = R.newest_answers(conn, str(visit[0])) if visit else {}
        for requirement in needed:
            answer = answers.get(requirement["id"]) or {}
            if answer.get("answer") not in ("ok", "pushed_past") or not E.is_human(answer.get("by_id")):
                return str(stage["name"])
        return None
    return "no stage"


def cut(
    conn: "sqlite3.Connection", task_id: str, by: str, tag: str | None = None, *, seen_version: int | None = None
) -> list[Row]:
    """`tico task release`: ask Tico to cut a release of each repository the task's merged PRs wait on, the way the
    In a release requirement names (`release_with`). Owes one `release` action per such repository, carried out on the
    Heartbeat through the GitHub App. Who may: the stage owner, the pipeline's manager, any person; a bot only for a
    task a person signed off before release. Refuses `task_stage` (the stage waits for no release), `task_needs_person`
    (no person's sign-off), `task_requirement` (no way set, no tag named for a tag, nothing merged, already released,
    or an ask still waiting), `task_changed`, `task_ended`."""
    E = _engine()
    GS = _gs()
    H = _h()
    row: Row | None = E.task(conn, task_id)
    if not row:
        E._refuse(conn, by, "not-found", "No such task")
        return []

    def refuse(rule: str, detail: str) -> None:
        E._refuse(conn, by, rule, detail, task_id, row=row, then=("show",), version=row.get("version"))

    def already(found: list[str]) -> None:
        if found:
            refuse("task_requirement", f"Already in {', '.join(found)}: there is no release to cut")

    requirement = _release_requirement(conn, row)
    if row["status"] != "open" or requirement is None:
        # The merge reached a release first (the task may have just moved on because of it): say which.
        already(released(conn, task_id))
    if row["status"] != "open":
        refuse("task_ended", f"The task is {row['stage']['name']}. Reopen it first.")
    E.check_version(conn, by, row, seen_version)
    if requirement is None:
        refuse("task_stage", f"{row['stage']['name']} doesn't wait for a release")
        return []
    pipeline: Row = row.get("pipeline") or {}
    if not (E.is_human(by) or by in (row.get("owner_id"), pipeline.get("manager_id"))):
        refuse("task_requirement", "Only the stage owner, the pipeline's manager or a person can cut its release")
    if not E.is_human(by):
        stage = unsigned(conn, row)
        if stage:
            where = "" if stage == "no stage" else f" in {stage}"
            refuse("task_needs_person", f"A person signs off before release{where}, and nobody has for this task")
    settings: Row = requirement.get("settings") or {}
    how = way(settings)
    if how is None:
        refuse(
            "task_requirement",
            "Tico does not cut releases here: this repository's releases come on their own or from a person, and "
            "the task moves on when one holds the merge",
        )
        return []
    tag = (tag or "").strip()
    if how[0] == "tag" and not TAG.match(tag):
        refuse("task_requirement", "Name the release's tag: `tico task release <task> --tag v1.2.0`")
    merged = [
        link
        for link in GS.prs_of(conn, task_id)
        if link["metadata"] and link["metadata"]["state"] == "merged" and link["metadata"].get("merged_sha")
    ]
    if not merged:
        refuse("task_requirement", "Nothing of this task merged yet")
    visit_id = str(row["visit"]["id"])
    wanted: dict[str, Row] = {}  # repository -> the newest merge into it that no release holds yet
    for link in merged:
        metadata: Row = link["metadata"]
        repo = GS.release_repo(settings, str(link["repo"]))
        held = GS.release_of(
            conn, repo, str(metadata["merged_sha"]), prereleases=settings.get("prereleases", True) is not False
        )
        if held is not None:
            continue
        if how[0] == "tag" and repo.lower() != str(link["repo"]).lower():
            refuse("task_requirement", f"A tag goes on this task's merge commit, and {repo} is another repository")
        before = wanted.get(repo)
        if before is None or str(metadata.get("merged_at") or "") > str(before.get("merged_at") or ""):
            wanted[repo] = metadata
    if not wanted:
        already(released(conn, task_id, visit_id=str(row["visit"]["id"])))
        refuse("task_requirement", "Every merge of this task is in a release already")
    owed: list[Row] = []
    for repo, metadata in wanted.items():
        found = asks(conn, repo, visit_id, metadata.get("merged_at"))
        if found and not failed(found[-1]):
            newest = found[-1]
            refuse(
                "task_requirement",
                f"A release of {repo} was asked for at {str(newest['created_at'])[11:16]} ({said_as(newest)}): "
                "Tico waits for it",
            )
        n = len(SA.of_visit(conn, visit_id, SOURCE, RELEASE, item_key=repo)) + 1
        pinned = _pinned(how[0], how[1] or tag, str(metadata["merged_sha"]), n)
        effect = SA.owe(conn, visit_id, SA.ACT, SOURCE, RELEASE, REPO, repo, pinned)
        if effect:
            owed.append(effect)
            data = {"repo": repo, "how": how[0], "name": how[1] or tag, "effect": effect["id"]}
            E._event(conn, task_id, by, "release_asked", None, repo, said_as(effect), data)
    ts = H.now()
    conn.execute("UPDATE tasks SET version=?, updated_at=? WHERE id=?", (int(row.get("version") or 0) + 1, ts, task_id))
    E.changed(conn, task_id, by)
    return owed


# ----------------------------------------------------------------------------- carrying it out (source_actions)
def carry_out(github: Any, effect: Row) -> Row:
    """GitHub's call for one ask, no transaction held: run the workflow, or create the tag."""
    kind, name, sha = unpinned(str(effect["pinned"] or ""))
    said: Row = github.cut_release(str(effect["item_key"]), kind, name, sha)
    return said if said.get("ok") or said.get("reason") else {**said, "reason": "GitHub said no"}


def recorded(conn: "sqlite3.Connection", effect: Row) -> None:
    """GitHub took the ask: the task's history says so, and its line now waits for the release."""
    E = _engine()
    task_id = str(effect["task_id"])
    data = {"repo": effect["item_key"], "effect": effect["id"]}
    E._event(conn, task_id, E.SYSTEM, "release_cut", None, str(effect["item_key"]), said_as(effect), data)
    E.changed(conn, task_id, E.SYSTEM)


def gave_up(conn: "sqlite3.Connection", effect: Row, reason: str) -> None:
    """GitHub refused the ask for good: the task's history says why, and the Release Owner hears of it first (a
    second failure, or a permission only a person can grant, is a person's: `standing`)."""
    E = _engine()
    task_id, repo = str(effect["task_id"]), str(effect["item_key"])
    data = {"repo": repo, "effect": effect["id"], "reason": reason[:500]}
    E._event(conn, task_id, E.SYSTEM, "release_refused", None, repo, said_as(effect), data)
    row: Row | None = E.task(conn, task_id)
    visit: Row = (row.get("visit") or {}) if row else {}
    if not row or row["status"] != "open" or visit.get("id") != effect["visit_id"]:
        return
    E.changed(conn, task_id, E.SYSTEM)
    words = failure_line(repo, {**effect, "state": "failed", "last_error": reason})
    E.tell_owner(
        conn,
        task_id,
        f"{words}. Read why, then ask again with `tico task release {task_id}` or say on the task why it cannot.",
        f"release_refused:{effect['id']}",
    )
