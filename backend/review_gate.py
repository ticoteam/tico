"""Deterministic review handoff gate; runs before any step change or review child wake."""
import json
import re
import time
from urllib.parse import quote

import httpx

from . import test_receipts
from .store import Problem

ATTEMPTS = 2
RETRY_BACKOFF = 0.15


class SnapshotInvalid(ValueError):
    """A collector validation error whose fixed text is safe to return."""


DEVOPS_CAUSES = {"github_app_permissions", "github_repo_not_accessible", "github_app_key",
                 "github_key_missing", "github_not_installed", "github_unavailable"}


def github_failure(exc):
    """Only known upstream outages are temporary; key errors can also have an HTTP 502 envelope."""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, TimeoutError, ConnectionError)):
        return {"problem_code": "github_unreachable", "why": "GitHub did not answer in time",
                "temporary": True, "devops": False}
    if isinstance(exc, Problem):
        status = exc.extra.get("github_status")
        if status is None and exc.code == "github_token":
            found = re.search(r"HTTP (\d{3})\b", exc.detail)
            status = int(found[1]) if found else exc.status
        temporary = exc.code == "github_unreachable" or (
            exc.code in ("github_token", "github_read", "github_installations")
            and isinstance(status, int) and (status == 429 or 500 <= status <= 599))
        return {"problem_code": exc.code,
                "why": "GitHub did not answer in time" if exc.code == "github_unreachable" else exc.detail,
                "temporary": temporary, "devops": not temporary and (exc.code in DEVOPS_CAUSES
                    or exc.code == "github_installations" and status in (401, 403, 404)),
                **({"upstream_status": status} if status is not None else {})}
    return {"problem_code": "github_read", "why": "GitHub read failed: " + type(exc).__name__,
            "temporary": False, "devops": False}


def github_retry(call):
    """A brief retry outside the write lock; never retry permanent access or configuration failures."""
    for attempt in range(ATTEMPTS):
        try:
            return call()
        except Exception as exc:
            if attempt + 1 == ATTEMPTS or not github_failure(exc)["temporary"]:
                raise
            time.sleep(RETRY_BACKOFF)


REVIEW = re.compile(r"^Review [12](?:\b|:)", re.I)
# A request to a reviewer, to QA or to Security: a subtask of the ticket, titled by what it asks.
GATE_CHILD = re.compile(r"^(?:Review [12]|QA|Security)(?:\b|:)", re.I)


# The children whose owners only review: they comment and give a verdict, and never move the ticket. QA is not one: QA
# sends a ticket back to QA Fixes and on to Pending Release and Done (docs/software-factory.md).
REVIEWER_CHILD = re.compile(r"^(?:Review [12]|Security)(?:\b|:)", re.I)


def reviewing(c, actor, row):
    """The review or Security subtask of this Dev ticket that `actor` owns, or None: a reviewer who is not the
    ticket's owner. A person is never one here (a person may move any ticket they could move before)."""
    from . import hubdb as H
    from . import task_relations as TR
    if not H.is_bot(actor) or actor == row["owner"] or not engineering(c, row):
        return None
    for child_id in TR.child_ids(c, row["id"]):
        child = H.task(c, child_id)
        if child and child["owner"] == actor and REVIEWER_CHILD.match(str(child.get("title") or "").strip()):
            return child
    return None


# One open request per kind of check on a ticket: Review 1, Review 2 or QA (a Security check can be asked again).
ONE_OPEN = re.compile(r"^(Review [12]|QA)(?:\b|:)", re.I)


def heads(c, parent, snapshots=None):
    """The pull request heads a check on this ticket would judge now: the fresh read when there is one, else the
    head the link last recorded. Empty when none is known."""
    from . import hubdb as H
    out = set()
    for pr in links(c, parent):
        detail = (snapshots or {}).get(pr["id"]) or H._json(pr.get("detail_json"), {}) or {}
        head = str(detail.get("head_sha") or "").lower()
        if not detail.get("error") and test_receipts.SHA.fullmatch(head):
            out.add(head)
    return out


def refuse_second(c, actor, parent, title, snapshots=None):
    """One open Review 1, Review 2 or QA subtask per ticket and pull request head: a second request for the same
    check on the same head (or one whose head is unknown) points at the one already open instead of buying a
    second reviewer turn. A push gives a new head, and with it a new check."""
    from . import hubdb as H
    from . import task_relations as TR
    kind = ONE_OPEN.match(str(title).strip())
    if not kind:
        return
    kind = " ".join(kind[1].lower().split())
    wanted = heads(c, parent, snapshots)
    for child_id in TR.child_ids(c, parent["id"]):
        other = H.task(c, child_id)
        other_kind = ONE_OPEN.match(str(other.get("title") or "").strip())
        if (not other_kind or " ".join(other_kind[1].lower().split()) != kind
                or other["status"] not in H.ACTIVE_STATUSES):
            continue
        seen = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='review_head' "
                         "ORDER BY ts DESC, rowid DESC LIMIT 1", (child_id,)).fetchone()
        judged = set(filter(None, (seen[0] if seen else "").split(",")))
        if wanted and judged and not wanted & judged:
            continue
        who = H.actor_id(other["owner"])
        H.refuse(c, actor, "duplicate",
                 f"{kind.title() if kind != 'qa' else 'QA'} is already open on this ticket"
                 f"{' for this head' if wanted else ''}: task {other['id']}, with {who}. Wait for its verdict; "
                 "if the code changed, push the new head first, and if that check is no longer needed, close it.")


def remember_heads(c, actor, child_id, parent, snapshots=None):
    """Record the heads a check was asked for, so a later request for the same head finds it (refuse_second)."""
    from . import hubdb as H
    seen = heads(c, parent, snapshots)
    if seen:
        H._task_event(c, child_id, actor, "review_head", None, ",".join(sorted(seen)), "")


def engineering(c, row):
    from .roadmap_flow import ENGINEERING
    return bool(row and c.execute("SELECT 1 FROM task_types WHERE id=? AND (role=? OR name='Dev tickets')",
                                  (row.get("type_id"), ENGINEERING)).fetchone())


def links(c, row):
    from . import hubdb as H
    return [p for p in H.task_links(c, row["id"]) if p["kind"] == "pr"
            and p.get("state") not in ("merged", "closed", "shipped")]


def snapshot(service, prs):
    from . import hubdb as H
    """Read current PR heads and all checks outside the database write lock.

    An unconfigured GitHub integration uses its signed-webhook link snapshot.
    Configured integrations fail closed on incomplete or unavailable reads.
    """
    with service.store.read() as c:
        if not service.row(c):
            return None
    out = {}
    for pr in prs:
        try:
            repo = pr.get("repo")
            if not repo or not pr.get("number"):
                raise SnapshotInvalid("unresolved PR")
            token, _ = github_retry(lambda: service.mint(
                [repo], {"pull_requests": "read", "checks": "read", "statuses": "read"}))
            headers = {"Authorization": "Bearer " + token}
            base = f"/repos/{quote(repo, safe='/')}"
            def read(path, params=None):
                def fetch():
                    response = service._call("GET", base + path, headers=headers, params=params)
                    status = response.status_code
                    if status != 200:
                        code = ("github_app_key" if status == 401 else "github_repo_not_accessible"
                                if status in (403, 404) else "github_read")
                        raise Problem(code, f"GitHub answered {status}", 502, extra={"github_status": status})
                    return response.json()
                return github_retry(fetch)
            data = read(f"/pulls/{pr['number']}")
            head = data["head"]["sha"]
            checks = {}
            for endpoint, field in (("check-runs", "check_runs"), ("statuses", None)):
                for page in range(1, 11):
                    data = read(f"/commits/{head}/{endpoint}", {"per_page": 100, "page": page})
                    rows = data[field] if field else data
                    for check in rows:
                        name = check.get("name") or check.get("context") or "Checks"
                        # The commit statuses API returns newest first; older statuses cannot overwrite it.
                        if endpoint == "statuses" and "status:" + name in checks:
                            continue
                        key = ("check:" + name + (f" ({check['id']})" if check.get("id") else "")) if field else "status:" + name
                        result = check.get("conclusion") if field else check.get("state")
                        failed = result in ("failure", "error", "timed_out", "cancelled", "canceled", "action_required", "startup_failure", "stale")
                        checks[key] = "failing" if failed else "passing" if result in ("success", "neutral", "skipped") else "pending"
                    if len(rows) < 100:
                        break
                else:
                    raise SnapshotInvalid("checks incomplete")
            # Detect a push while collecting, rather than blessing a now-stale head.
            if read(f"/pulls/{pr['number']}")["head"]["sha"] != head:
                raise SnapshotInvalid("head changed during collection")
            out[pr["id"]] = {"head_sha": head, "checks": checks, "at": H.now()}
        except SnapshotInvalid as exc:
            out[pr["id"]] = {"error": True, "why": str(exc)}
        except Exception as exc:
            out[pr["id"]] = {"error": True, **github_failure(exc)}
    return out


def evidence(c, row, actor):
    from . import hubdb as H
    from . import task_privacy as privacy
    def collect(item):
        messages = []
        conv = H.conversation(c, item.get("conversation_id"))
        for raw in c.execute("SELECT * FROM messages WHERE conversation_id=? AND deleted_at IS NULL ORDER BY created,id",
                             (item.get("conversation_id"),)).fetchall():
            m = {**dict(raw), "refs": H._json(raw["refs_json"], {}) or {}}
            if H.message_task_id(m, conv) != item["id"] or any(m.get(k) for k in test_receipts.REMOVED):
                continue
            if (privacy.message_readable(c, actor, m, current=True)
                    and privacy.message_readable(c, row["owner"], m, current=True)):
                messages.append(m)
        return {**item, "messages": messages}
    result = collect(row)
    result["children"] = []
    from . import task_relations as TR
    for child_id in TR.child_ids(c, row["id"]):
        item = H.task(c, child_id)
        if (REVIEW.match(item.get("title", "")) and H.task_private_readable(c, actor, item)
                and H.task_private_readable(c, row["owner"], item)):
            result["children"].append(collect(item))
    return result


def check(c, actor, row, snapshots=None, note=None, require_pr=False):
    from . import hubdb as H
    if not engineering(c, row):
        return
    prs = links(c, row)
    if not prs:
        if require_pr:
            reject(c, actor, row, "receipt", "unknown", {"url": "no linked PR"},
                   "Review refused: no linked PR or test receipt. Link the current PR and post its passing test receipt before asking a reviewer.")
        return  # A board-only step move has no software reviewer to wake.
    facts = evidence(c, row, actor)
    if note is not None:
        facts["note"] = note
        facts["updated"] = H.now()
    # CI is checked across all linked PRs before looking at any receipt.
    for pr in prs:
        detail = snapshots.get(pr["id"], {"error": True}) if snapshots is not None else (H._json(pr.get("detail_json"), {}) or {})
        head = detail.get("head_sha") or "unknown"
        cached = H._json(pr.get("detail_json"), {}) or {}
        changed = snapshots is not None and (pr.get("updated") or "") > detail.get("at", "") and cached.get("head_sha") != head
        if changed or detail.get("error") or not test_receipts.SHA.fullmatch(head):
            why = (detail.get("why") or "GitHub read failed" if detail.get("error")
                   else "the PR link changed while GitHub was read" if changed else "no PR head recorded")
            cause = "[" + detail["problem_code"] + "] " if detail.get("problem_code") else ""
            action = ("Temporary GitHub failure; Tico will retry this move." if detail.get("temporary")
                      else "Retry the review move; if it repeats, refresh the PR link.")
            reject(c, actor, row, "ci", head, pr,
                   f"Review refused: cannot verify the current head and CI for {pr['url']} ({cause}{why}). {action}",
                   problem=detail)
        failing = sorted(k.removeprefix("check:").removeprefix("status:") for k, v in (detail.get("checks") or {}).items() if v == "failing")
        if failing or snapshots is None and pr.get("checks") == "failing":
            names = ", ".join(failing) or "Checks (refresh the PR link for check names)"
            reject(c, actor, row, "ci", head, pr, f"Review refused: CI failing at head {head} on {pr['url']}: {names}. Fix the failing checks, then retry the review move.")
    for pr in prs:
        detail = snapshots[pr["id"]] if snapshots is not None else (H._json(pr.get("detail_json"), {}) or {})
        head = detail["head_sha"]
        state, receipts = test_receipts.at_head(facts, head)
        if state != "passed":
            newest = next((r["head"] for r in reversed(receipts) if r["head"]), "no receipt")
            action = ("Fix the recorded test failure and push a new head with a passing receipt" if state == "failed"
                      else "Post a passing test receipt naming the current head on this ticket")
            reject(c, actor, row, "receipt", head, pr, f"Review refused: {state} test receipt for {pr['url']} current head {head}; newest receipt head: {newest}. {action}, then retry the review move.")


def reject(c, actor, row, kind, head, pr, text, problem=None):
    from . import hubdb as H
    try:
        H.refuse(c, actor, "review_gate", text, task=row["id"])
    except H.Refused as exc:
        exc.review_gate = {"task": row["id"], "kind": kind, "head": head, "pr": pr["url"], "text": text,
                           **{key: problem[key] for key in ("problem_code", "temporary", "devops", "upstream_status")
                              if problem is not None and key in problem}}
        raise


def notify(c, actor, notice):
    """After refusal rollback, keep one Builder comment per ticket/head/check kind."""
    from . import hubdb as H
    row = H.task(c, notice["task"])
    if not row or actor == row["owner"]:
        return
    conv = H.conversation(c, row["conversation_id"])
    key = {k: notice[k] for k in ("kind", "head")}
    if c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND json_extract(refs_json,'$.task')=? "
                 "AND json_extract(refs_json,'$.review_gate')=? LIMIT 1",
                 (conv["id"], row["id"], json.dumps(key, sort_keys=True))).fetchone():
        return
    H._write_message(c, H.KEEPER, row["owner"], notice["text"], conv, "say",
                     {"task": row["id"], "comment": True, "news": "blocker", "review_gate": json.dumps(key, sort_keys=True)}, None, None)
