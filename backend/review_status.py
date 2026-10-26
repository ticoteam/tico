"""The `tico/reviews` commit status: GitHub's copy of the past-review gate (review_gate.passed, Dev 567).

When a Dev ticket's pull request head changes or one of its review or QA children records a verdict, the ticket is
marked; the GitHub sync tick then posts, on each open linked PR's current head, `success` when every required Review
1 and Review 2 slot approves the change the PR makes there, else `failure` naming the first missing slot. With the
status required on the base branch, GitHub refuses a merge that skipped a review. Posting needs the GitHub App's
Commit statuses permission set to Read and write; without it nothing is posted and the link records why.
"""
import json
import logging
import threading
from urllib.parse import quote

from . import hubdb as H

CONTEXT = "tico/reviews"
_dirty = set()
_lock = threading.Lock()
_started = {"done": False}
log = logging.getLogger("tico.review_status")


def mark(c, task_id):
    """Post this ticket's status again once the write commits."""
    def add():
        with _lock:
            _dirty.add(task_id)
    if getattr(c, "after_commit", None) is not None and getattr(c, "write_transaction", False):
        c.after_commit.append(add)
    else:
        add()


def mark_parent(c, row):
    """A review or QA child's verdict changes its Dev ticket's status."""
    from . import review_gate
    from . import task_relations as TR
    parent = H.task(c, TR.parent_of(c, row["id"]) or "")
    if parent and review_gate.engineering(c, parent):
        mark(c, parent["id"])


def describe(missing):
    if not missing:
        return "success", "Every required review approves this change"
    first = missing[0]
    who = H.actor_id(first["reviewer"]) if first["reviewer"] else "its reviewer"
    text = f"{first['slot']} {first['why']}; re-review by {who}"
    return "failure", text if len(text) <= 140 else text[:139] + "…"


def tick(service):
    """Post every marked ticket's status (from the GitHub sync tick). The first tick after a start marks every Dev
    ticket with an open pull request, so a status missed while the server was down is posted."""
    if service is None:
        return
    with service.store.read() as c:
        if not service.row(c):
            return
        if not _started["done"]:
            _started["done"] = True
            from . import review_gate
            for (task_id,) in c.execute("SELECT DISTINCT task_id FROM task_links WHERE kind='pr' "
                                        "AND state IN ('open','draft')").fetchall():
                if review_gate.engineering(c, H.task(c, task_id)):
                    _dirty.add(task_id)
    with _lock:
        todo = sorted(_dirty)
        _dirty.clear()
    for task_id in todo:
        try:
            sync(service, task_id)
        except Exception as exc:
            log.warning("tico/reviews status for %s not posted: %s", task_id, type(exc).__name__)


def sync(service, task_id):
    """Work out and post the status for each open PR of one ticket. Reads GitHub outside the write lock."""
    from . import review_gate
    with service.store.read() as c:
        row = H.task(c, task_id)
        if not row or not review_gate.engineering(c, row):
            return
        prs = [p for p in review_gate.judged_links(c, row) if p.get("state") in ("open", "draft")]
    if not prs:
        return
    read = review_gate.snapshot(service, prs) or {}
    with service.store.read() as c:
        wanted = review_gate.wanted_changes(c, row, read)
    compared = review_gate.changes(service, prs, wanted) if wanted else {}
    results = []
    with service.store.read() as c:
        row = H.task(c, task_id)
        for pr in prs:
            detail = read.get(pr["id"]) or {}
            head = str(detail.get("head_sha") or "").lower()
            if detail.get("error") or not review_gate.test_receipts.SHA.fullmatch(head):
                continue
            missing = review_gate.unapproved(c, row, {pr["id"]: head}, review_gate.comparer(compared))
            results.append((pr, head, *describe(missing)))
    for pr, head, state, text in results:
        cached = (H._json(pr.get("detail_json"), {}) or {}).get("review_status") or {}
        if cached.get("posted") and (cached.get("head"), cached.get("state"), cached.get("description")) == (head, state, text):
            continue
        posted, why = post(service, pr, head, state, text)
        record(service, pr["id"], {"head": head, "state": state, "description": text, "posted": posted,
                                   **({"why": why} if why else {}), "at": H.now()})


def post(service, pr, head, state, text):
    try:
        token, _ = service.mint([pr["repo"]], {"statuses": "write"})
    except Exception as exc:
        return False, review_gate_failure(exc, pr["repo"])
    response = service._call("POST", f"/repos/{quote(pr['repo'], safe='/')}/statuses/{head}",
                             headers={"Authorization": "Bearer " + token},
                             json={"state": state, "context": CONTEXT, "description": text})
    if response.status_code != 201:
        return False, f"GitHub answered {response.status_code}"
    return True, None


def review_gate_failure(exc, repo):
    from .review_gate import mint_failure
    return mint_failure(exc, repo)["why"]


def record(service, link_id, status):
    with service.store.transaction() as c:
        found = c.execute("SELECT detail_json FROM task_links WHERE id=?", (link_id,)).fetchone()
        if not found:
            return
        detail = H._json(found[0], {}) or {}
        detail["review_status"] = status
        c.execute("UPDATE task_links SET detail_json=? WHERE id=?", (json.dumps(detail), link_id))
