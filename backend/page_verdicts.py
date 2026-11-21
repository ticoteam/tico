# pyright: strict
"""Jev's verdicts on pages: one per page version and rubric (docs/dev/pipelines-v3.md "The nine requirement kinds", 3).

A page attached to a page requirement (backend/task_pages.py) is owed a verdict. The Jev step on the Heartbeat (`run`)
judges what is owed through `pipeline_judges.judge_many`, with no write transaction held during the model call, and
records each answer here. The page requirement reads the verdict (`task_requirements._page`); it never asks Jev.

- The key is (file id, version, `rubric_hash`). `rubric_hash` covers the rubric and everything else Jev is shown beside
  the page (the commit a review is for, the release a Validate page is checked against), so a change to any of them is
  a new verdict, and the same page judged the same way is judged once.
- `verdict` is NULL while owed, then `met`, `not_met` or `unsure` (pipeline_judges' bands), or `failed` when every one
  of TRIES calls failed: a person decides ("Jev couldn't check this").
- A failed call is not a verdict. It counts a try and sets `next_try_at` RETRY_S later.

Every function takes the caller's open write transaction and never commits, except `run`.
"""

import hashlib
import json
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

VERDICTS = ("met", "not_met", "unsure", "failed")
TRIES = 3  # failed calls before a person decides
RETRY_S = (60, 300, 900)  # wait after the first, second and third failed call


def _h() -> Any:
    from . import hubdb

    return hubdb


def rubric_hash(rubric: str, context: Row | None = None, rules: list[str] | None = None) -> str:
    """The key for what a page is judged against: the rubric, the context Jev is shown beside the page (JSON-able,
    key order does not matter) and the rules added to the check (Flow's suggested rules), in order. With no rules the
    key is what it was before rules existed, so no verdict already given is owed again."""
    held: Row = {"rubric": rubric.strip(), "context": context or {}}
    if rules:
        held["rules"] = list(rules)
    text = json.dumps(held, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _one(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> Row | None:
    cursor = conn.execute(sql, args)
    found = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], found, strict=True)) if found else None


GET_SQL = "SELECT * FROM page_verdicts WHERE file_id=? AND version=? AND rubric_hash=?"


def get(conn: "sqlite3.Connection", file_id: str, version: int, key: str) -> Row | None:
    """The verdict row for a page version and rubric hash, or None when none is owed yet. Kept for the read snapshot
    (read_cache: a write starts it again); `prime` reads many together."""
    from . import read_cache

    values: dict[Any, Any] | None = read_cache.cache(conn)
    cached = (GET_SQL, str(file_id), int(version), str(key))
    if values is not None and cached in values:
        found: Row | None = values[cached]
        return dict(found) if found else None
    found = _one(conn, GET_SQL, (file_id, int(version), key))
    if values is not None:
        values[cached] = dict(found) if found else None
    return found


# `get` of many keys: only the asked (file id, version, rubric hash), each found by the unique key's index, never every
# verdict a page ever had (a page revised often has many).
PRIME_SQL = (
    "SELECT v.* FROM json_each(?) AS k CROSS JOIN page_verdicts AS v WHERE v.file_id = json_extract(k.value, '$[0]') "
    "AND v.version = json_extract(k.value, '$[1]') AND v.rubric_hash = json_extract(k.value, '$[2]')"
)


def prime(conn: "sqlite3.Connection", keys: Iterable[tuple[str, int, str]]) -> None:
    """Read `get` of many (file id, version, rubric hash) in one statement, misses too, for the reads that follow in
    this snapshot. Nothing outside a snapshot."""
    from . import read_cache

    values: dict[Any, Any] | None = read_cache.cache(conn)
    if values is None:
        return
    wanted = {(str(f), int(v), str(k)) for f, v, k in keys}
    wanted = {key for key in wanted if (GET_SQL, *key) not in values}
    if not wanted:
        return
    found: dict[tuple[str, int, str], Row] = {}
    cursor = conn.execute(PRIME_SQL, (json.dumps(sorted(wanted)),))
    names = [d[0] for d in cursor.description]
    for raw in cursor.fetchall():
        row = dict(zip(names, raw, strict=True))
        found[(str(row["file_id"]), int(row["version"]), str(row["rubric_hash"]))] = row
    for key in wanted:
        values.setdefault((GET_SQL, *key), found.get(key))


# Each page's versions Jev passed, with the page requirement each counted for: newest version first, then the newest
# verdict. `checks_of` reads one page; `prime_checks` reads many together.
PASSED = (
    "SELECT p.file_id, p.version, p.requirement_id FROM requirement_pages p JOIN page_verdicts v ON v.file_id = p.file_id "
    "AND v.version = p.version AND v.rubric_hash = COALESCE(p.rubric_hash, '') WHERE p.file_id IN (SELECT value FROM "
    "json_each(?)) AND v.verdict='met' ORDER BY p.version DESC, v.updated_at DESC"
)


def _passed(conn: "sqlite3.Connection", file_ids: list[str]) -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {fid: [] for fid in file_ids}
    if file_ids:
        for fid, version, rid in conn.execute(PASSED, (json.dumps(file_ids),)).fetchall():
            out[str(fid)].append((int(version), str(rid)))
    return out


def prime_checks(conn: "sqlite3.Connection", refs: Iterable[object]) -> None:
    """Read what Jev passed on the pages of many `page:<id>[@<version>]` refs together, for the `checks_of` that follow
    in this snapshot (Needs you shows an OK's check rows for each OK on it). Without a read snapshot it reads nothing."""
    from . import read_cache

    values = read_cache.cache(conn)
    if values is None:
        return
    ids = {
        m.group(1) for m in (re.fullmatch(r"page:([A-Za-z0-9_-]+)(?:@[0-9]{1,9})?", str(r or "")) for r in refs) if m
    }
    for fid, found in _passed(conn, sorted(i for i in ids if (PASSED, i) not in values)).items():
        values.setdefault((PASSED, fid), found)


def checks_of(conn: "sqlite3.Connection", file_id: str, version: int | None = None) -> list[str]:
    """The check rows of a page version (its newest, with no version) that Jev passed: one short line per part of the
    check it was judged by (pipeline_judges.CHECKS, `Part.found`). An OK that shows the page lists them under its
    question; [] when no verdict on it passed, or its check is a company's own."""
    from . import pipeline_judges as PJ
    from . import read_cache
    from . import task_requirements as R

    passed = read_cache.value(conn, (PASSED, file_id), lambda: _passed(conn, [file_id])[file_id], shared=True)
    found = next((rid for at, rid in passed if version is None or at == int(version)), None)
    requirement: Row = (R.get(conn, found) if found else None) or {}
    settings: Row = requirement.get("settings") or {}
    check = PJ.check_of(settings.get("rubric"))
    return [part.found for part in PJ.CHECKS[check] if part.found] if check else []


def owe(conn: "sqlite3.Connection", file_id: str, version: int, key: str) -> Row:
    """The verdict row for a page version and rubric hash, made owed (due now) when there is none. Attaching a page calls
    it, so the Jev step finds the page without reading every requirement."""
    found = get(conn, file_id, version, key)
    if found:
        return found
    H = _h()
    now = H.now()
    conn.execute(
        "INSERT INTO page_verdicts (id, file_id, version, rubric_hash, next_try_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (H.new_id(), file_id, int(version), key, now, now, now),
    )
    made = get(conn, file_id, version, key)
    assert made is not None
    return made


def record(conn: "sqlite3.Connection", verdict_id: str, result: Row, *, failed: bool = False) -> Row:
    """Record one Jev call on an owed verdict. `result` is `pipeline_judges.judge_once`'s. With `failed` (the call
    itself failed: no model, an error, over the Decide cap) a try is counted and the verdict stays owed until TRIES,
    then it is `failed`. Otherwise its verdict, p, reason, model and cost are kept. A verdict already given is left as
    it is."""
    H = _h()
    now = H.now()
    row = _one(conn, "SELECT * FROM page_verdicts WHERE id=?", (verdict_id,))
    if row is None:
        raise KeyError(verdict_id)
    if row["verdict"] is not None:
        return row
    cost = float(row["cost_usd"] or 0) + float(result.get("cost_usd") or 0)
    if failed:
        tries = int(row["tries"]) + 1
        gave_up = tries >= TRIES
        conn.execute(
            "UPDATE page_verdicts SET tries=?, last_error=?, cost_usd=?, verdict=?, next_try_at=?, updated_at=? "
            "WHERE id=?",
            (
                tries,
                str(result.get("reason") or "The call failed"),
                cost,
                "failed" if gave_up else None,
                None if gave_up else H.shift(now, seconds=RETRY_S[min(tries, len(RETRY_S)) - 1]),
                now,
                verdict_id,
            ),
        )
    else:
        verdict = str(result.get("verdict") or "unsure")
        if verdict not in VERDICTS[:3]:
            verdict = "unsure"
        conn.execute(
            "UPDATE page_verdicts SET verdict=?, p=?, reasons=?, model=?, cost_usd=?, tries=tries+1, next_try_at=NULL, "
            "updated_at=? WHERE id=?",
            (verdict, result.get("p"), str(result.get("reason") or ""), result.get("model"), cost, now, verdict_id),
        )
    out = _one(conn, "SELECT * FROM page_verdicts WHERE id=?", (verdict_id,))
    assert out is not None
    return out


def owed(conn: "sqlite3.Connection", now: str, limit: int = 20) -> list[Row]:
    """The verdicts still owed whose next try is due, oldest first."""
    cursor = conn.execute(
        "SELECT * FROM page_verdicts WHERE verdict IS NULL AND (next_try_at IS NULL OR next_try_at <= ?) "
        "ORDER BY created_at, id LIMIT ?",
        (now, int(limit)),
    )
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


# The pages still counted on an open visit that wait on one verdict: what the Jev step judges and then nudges.
WAITING_SQL = (
    "SELECT p.*, v.task_id FROM requirement_pages p JOIN visits v ON v.id = p.visit_id AND v.left_at IS NULL "
    "WHERE p.file_id=? AND p.version=? AND p.rubric_hash=? AND p.replaced_at IS NULL ORDER BY p.created_at, p.id"
)
ORPHAN_WAIT_H = 24  # a verdict no open task waits on is looked at again after this long, not judged
BATCH = 5  # verdicts a Heartbeat beat judges at most: each is a model call, and the beat runs every few seconds


def _waiting(conn: "sqlite3.Connection", row: Row) -> list[Row]:
    cursor = conn.execute(WAITING_SQL, (row["file_id"], int(row["version"]), row["rubric_hash"]))
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


def _job(conn: "sqlite3.Connection", row: Row, page: Row) -> tuple[Row, Row]:
    """What Jev is asked for one owed verdict: the requirement (name, rubric, the pipeline's goal) and the attachment
    (the page's text at its version, the task's title and spec, the context it is judged against)."""
    from . import pipeline_judges as PJ
    from . import task_engine as E
    from . import task_requirements as R

    requirement = R.get(conn, page["requirement_id"]) or {"id": page["requirement_id"], "kind": "page"}
    task = E.task(conn, page["task_id"]) or {}
    task["request"] = _h().task_request_text(conn, page["task_id"]) if task else ""
    judged: Row = json.loads(page.get("judged_against") or "{}")
    version = _one(
        conn,
        "SELECT v.mime, v.digest, v.size, f.title FROM bot_file_versions v JOIN bot_files f ON f.id = v.file_id "
        "WHERE v.file_id=? AND v.version=?",
        (row["file_id"], int(row["version"])),
    )
    text, cut = (
        PJ.text_of(conn, {"mime": version["mime"], "blob_digest": version["digest"], "blob_size": version["size"]})
        if version
        else (None, False)
    )
    links = conn.execute(
        "SELECT repo, number, detail FROM task_links WHERE task_id=? AND kind='pr' AND repo IS NOT NULL "
        "AND number IS NOT NULL ORDER BY created_at, id",
        (page["task_id"],),
    ).fetchall()
    return job_of(
        {**requirement, "name": R.name_of(requirement)},
        task,
        {"file_id": row["file_id"], "version": row["version"], "digest": (version or {}).get("digest") or ""},
        text,
        cut,
        judged,
        # The work's repo, PR and PR author, as an attachment is judged with (pipeline_judges.pr_work), when the task
        # links one PR. With more, each PR and its newest commit are in the context's `heads`.
        {"repo": links[0][0], "number": links[0][1], "detail": links[0][2]} if len(links) == 1 else None,
    )


def job_of(
    requirement: Row, task: Row, page: Row, text: str | None, cut: bool, judged: Row, link: Row | None = None
) -> tuple[Row, Row]:
    """`_job` with nothing read: the requirement (its id and name), the task (title, request, version, pipeline), the page
    (file_id, version, digest), its text and whether it was cut, what it is judged against (`judged_against`: the
    rubric and the context), and the task's PR link (repo, number, detail) when it links one. The eval
    (evals/pipeline_judge) builds its page cases through this, so the judge is shown there exactly what it is shown
    here."""
    from . import pipeline_judges as PJ

    pipeline: Row = task.get("pipeline") or {}
    asked: Row = {
        "id": requirement["id"],
        "name": requirement.get("name") or "",
        "rubric": judged.get("rubric") or requirement.get("name") or "",
        "goal": pipeline.get("goal") or "",
        "pipeline_id": task.get("pipeline_id"),
        # The rules added to the check when the page was attached (task_pages.judged_against): each one more part.
        **({"rules": list(judged["rules"])} if judged.get("rules") else {}),
    }
    ref = f"page:{page['file_id']}@{page['version']}"
    attachment: Row = {
        "id": ref,
        "source": ref,
        "file_version": int(page["version"]),
        "digest": page.get("digest") or "",
        "text": text,
        "truncated": cut,
        "task_title": task.get("title") or "",
        "task_spec": task.get("request") or "",  # what was asked: the task's first message
        "task_work_version": task.get("version") or 0,
        # The page is made for the work as it is now: without this Jev is shown evidence for version 0 of work at
        # version N, and hedges on a page that is right (live rc.4, Nov 11).
        "work_version": int(task.get("version") or 0),
        # The page is pinned (file and version), and what it is judged against (the rubric, the task, the heads, the
        # release) is in its rubric_hash: a change to any of them is another verdict. The task's own version moves on
        # with every edit and says nothing about this page, so a page is never out of date by it.
        "older_evidence_ok": True,
        "context": judged.get("context") or {},
        **(PJ.pr_work(link) if link else {}),
    }
    return asked, attachment


def _nudge(conn: "sqlite3.Connection", verdict: Row, pages: list[Row]) -> None:
    """After a verdict: a history line on each task whose page it is, then the task moves on if that was the last thing
    it needed, else its ladder starts again. Jev's send back wakes a bot owner with the reasons."""
    from . import task_engine as E
    from . import task_requirements as R
    from . import task_wakes

    said = str(verdict["verdict"])
    reasons = " ".join(str(verdict.get("reasons") or verdict.get("last_error") or "").split())
    for page in pages:
        task_id = str(page["task_id"])
        requirement = R.get(conn, page["requirement_id"]) or {"kind": "page"}
        # On a page a person OKs, the task page shows Jev's line only when a part is missing (task #351).
        quiet = said != "not_met" and bool(R.named(requirement) or R.people_only(requirement))
        E._event(  # pyright: ignore[reportPrivateUsage]
            conn,
            task_id,
            E.SYSTEM,
            "jev_verdict",
            None,
            said,
            None if quiet else (reasons or None),
            {"requirement": page["requirement_id"], "file_id": page["file_id"], "version": page["version"]},
        )
        if E.advance(conn, task_id) is not None:
            continue
        E.changed(conn, task_id, E.SYSTEM)
        if said == "not_met" and R.jev_gates(requirement):
            line = (
                f'Decision model sent back "{R.name_of(requirement)}": {reasons}'
                if reasons
                else (f'Decision model sent back "{R.name_of(requirement)}"')
            )
            E.tell_owner(conn, task_id, line, f"jev:{verdict['id']}")
        elif said in ("unsure", "failed"):
            # Once per page version: a second unsure of the same version (judged again) wakes nobody new.
            _unsure_wake(conn, task_id, page, f"jev:{page['file_id']}@{page['version']}")
        # Jev has looked at a page a person OKs: commits since the reviews are a bot's to review first.
        task_wakes.review_new(conn, task_id)


def _unsure_wake(conn: "sqlite3.Connection", task_id: str, page: Row, what: str) -> None:
    """Jev unsure on a stage a bot holds is the manager bot's to look at once (task_requirements._unsure_to_bots): wake
    it with Jev's line naming the part. The page's own bot is not woken. A person's turn wakes nobody."""
    from . import task_engine as E
    from . import task_requirements as R
    from . import task_wakes

    row = E.task(conn, task_id)
    requirement = R.get(conn, page["requirement_id"])
    if not row or not requirement or row["status"] != "open" or row.get("snooze"):
        return
    answer = R.newest_answers(conn, row["visit"]["id"]).get(requirement["id"])
    standing = R.standing(conn, row, requirement, answer)
    name = R.name_of(requirement)
    if standing.state == R.WAITING and standing.line.startswith(R.DECIDE) and standing.on:
        model_action = "could not check" if "Decision model couldn't check" in standing.line else "is unsure of"
        lead = f'The decision model {model_action} "{name}": you decide it, as the pipeline\'s manager.'
        task_wakes.add(
            conn,
            row,
            standing.on,
            row["visit"],
            task_wakes.body(conn, row, lead, to=standing.on),
            wake_key=f"{row['id']}:{row['visit']['id']}:{what}",
        )


def run(store: Any, now: str | None = None) -> int:
    """The Jev step on the Heartbeat: read what is `owed` in a short read, judge them with no transaction held
    (pipeline_judges.judge_many: a page owed for several requirements read once, small pages sharing a call; Decide's
    cap checked), then `record` each in its own short write and nudge its tasks
    (task_engine.advance, else changed). A verdict no open task waits on any more is put off ORPHAN_WAIT_H, not judged.
    Returns how many verdicts it gave."""
    from . import pipeline_judges as PJ

    H = _h()
    now = str(now or H.now())
    jobs: list[tuple[Row, Row, Row]] = []
    orphans: list[str] = []
    with store.read() as c:
        for row in owed(c, now, BATCH):
            pages = _waiting(c, row)
            if not pages:
                orphans.append(str(row["id"]))
                continue
            asked, attachment = _job(c, row, pages[0])
            jobs.append((row, asked, attachment))
    if orphans:
        with store.transaction() as c:
            later = H.shift(now, hours=ORPHAN_WAIT_H)
            for verdict_id in orphans:
                c.execute("UPDATE page_verdicts SET next_try_at=? WHERE id=? AND verdict IS NULL", (later, verdict_id))
    given = 0
    # One page owed against several requirements is read once, and small pages share a call (PJ.judge_many).
    results = PJ.judge_many(store, [(asked, attachment) for _row, asked, attachment in jobs])
    for (row, _asked, _attachment), result in zip(jobs, results, strict=True):
        with store.transaction() as c:
            again = _one(c, "SELECT verdict FROM page_verdicts WHERE id=?", (row["id"],))
            if not again or again["verdict"] is not None:
                continue  # given meanwhile: nothing to record or nudge
            saved = record(c, str(row["id"]), result, failed=bool(result.get("failed")))
            if saved["verdict"] is None:
                continue
            given += 1
            _nudge(c, saved, _waiting(c, saved))
    return given
