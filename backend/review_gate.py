"""Deterministic review handoff gate; runs before any step change or review child wake."""
import json
import re
from urllib.parse import quote

import httpx
from jwt.exceptions import InvalidKeyError

from . import test_receipts

REVIEW = re.compile(r"^Review [12](?:\b|:)", re.I)
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
    for pr in review_links(c, parent):
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


# A merged PR's head is the change a review filed after the merge judges (Dev 569).
MERGED = ("merged", "shipped")


def review_links(c, row):
    """The pull requests a Review or QA request on this ticket judges: the open ones, else the merged ones at the head
    that merged. A PR closed without merging is never judged."""
    from . import hubdb as H
    return links(c, row) or [p for p in H.task_links(c, row["id"]) if p["kind"] == "pr" and p.get("state") in MERGED]


def merged_refusal(c, actor, row, kind, head, pr, problem, action, retryable=False):
    """Refuse a review of a merged PR: say it is merged, which head the review judges, and what to do."""
    reject(c, actor, row, kind, head, pr, f"Review refused: {pr['url']} is merged, so this review judges its merged "
                                          f"head {head}. {problem} {action}", retryable=retryable)


def engineering(c, row):
    from .roadmap_flow import ENGINEERING
    return bool(row and c.execute("SELECT 1 FROM task_types WHERE id=? AND (role=? OR name='Dev tickets')",
                                  (row.get("type_id"), ENGINEERING)).fetchone())


def links(c, row):
    from . import hubdb as H
    return [p for p in H.task_links(c, row["id"]) if p["kind"] == "pr"
            and p.get("state") not in ("merged", "closed", "shipped")]


def mint_failure(exc, repo):
    """Describe a failed token request without exposing exception text or response bodies."""
    from .store import Problem
    from .github_app import MISSING_KEY

    status = None
    code = None
    auth = isinstance(exc, InvalidKeyError)
    timeout = isinstance(exc, (TimeoutError, httpx.TimeoutException))
    retryable = timeout
    if isinstance(exc, Problem):
        code = exc.code
        # A transport failure has no GitHub HTTP response; 502 is Tico's own envelope status.
        status = exc.extra.get("github_status", None if code == "github_unreachable" else exc.status)
        auth = code in {"github_app_key", "github_key_missing", "github_not_installed", "github_not_connected",
                        "github_app_permissions"}
        auth |= code == "github_unavailable" and exc.detail in {
            MISSING_KEY, "The stored GitHub App key could not be decrypted"}
        timeout |= exc.extra.get("timed_out") is True
        retryable |= exc.retryable
    elif isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    auth |= status in (401, 403)
    retryable = not auth and (timeout or retryable or status in (408, 429)
                             or isinstance(status, int) and 500 <= status < 600)
    # Only fixed-format diagnostic fields are safe to include; never copy Problem.detail.
    cause = []
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
        cause.append(code)
    if isinstance(status, int) and 100 <= status < 600:
        cause.append(f"HTTP {status}")
    if timeout:
        cause.append("timed out")
    if not cause:
        cause.append(type(exc).__name__)
    action = "the GitHub App could not sign in" if auth else "GitHub token request failed"
    return {"error": True, "why": f"{action} for {repo}: {'; '.join(cause)}", "retryable": bool(retryable)}


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
                raise ValueError("unresolved PR")
            try:
                token, _ = service.mint([repo], {"pull_requests": "read", "checks": "read", "statuses": "read"})
            except Exception as exc:
                out[pr["id"]] = mint_failure(exc, repo)
                continue
            headers = {"Authorization": "Bearer " + token}
            base = f"/repos/{quote(repo, safe='/')}"
            def read(path, params=None):
                response = service._call("GET", base + path, headers=headers, params=params)
                if response.status_code != 200:
                    raise ValueError(f"GitHub answered {response.status_code}")
                return response.json()
            data = read(f"/pulls/{pr['number']}")
            head = data["head"]["sha"]
            checks = {}
            for endpoint, field in (("check-runs", "check_runs"), ("statuses", None)):
                for page in range(1, 11):
                    # GitHub defaults to latest; all keeps a failed check attempt
                    # visible even after a green rerun at the same commit.
                    params = {"per_page": 100, "page": page, **({"filter": "all"} if field else {})}
                    data = read(f"/commits/{head}/{endpoint}", params)
                    rows = data[field] if field else data
                    for check in rows:
                        name = check.get("name") or check.get("context") or "Checks"
                        result = check.get("conclusion") if field else check.get("state")
                        # Keep the latest status, and any earlier failure. A
                        # success update must not hide a same-head failed run.
                        if endpoint == "statuses" and "status:" + name in checks:
                            if result in ("failure", "error"):
                                checks["status:" + name + " (earlier failure)"] = "failing"
                            continue
                        key = ("check:" + name + (f" ({check['id']})" if check.get("id") else "")) if field else "status:" + name
                        failed = result in ("failure", "error", "timed_out", "cancelled", "canceled", "action_required", "startup_failure", "stale")
                        checks[key] = ("failing" if failed else "passing" if result == "success"
                                       else "skipped" if result in ("neutral", "skipped") else "pending")
                    if len(rows) < 100:
                        break
                else:
                    raise ValueError("checks incomplete")
            # Detect a push while collecting, rather than blessing a now-stale head.
            if read(f"/pulls/{pr['number']}")["head"]["sha"] != head:
                raise ValueError("head changed during collection")
            out[pr["id"]] = {"head_sha": head, "checks": checks, "at": H.now()}
        except ValueError as exc:
            out[pr["id"]] = {"error": True, "why": str(exc)}
        except Exception as exc:
            # Only the exception's type: a transport error's text can carry request details.
            out[pr["id"]] = {"error": True, "why": "GitHub read failed: " + type(exc).__name__}
    return out


def receipt_author(c, row, actor, parents, owner_email):
    """Additional receipt authors; this grants no task actions or visibility."""
    from . import hubdb as H
    from .auth import above
    if not H.task_private_readable(c, actor, row):
        return False
    if H.is_bot(actor):
        return H.is_bot(row["owner"]) and above(parents, H.actor_id(actor), H.actor_id(row["owner"]))
    if H.is_human(actor) and (person := H.human(c, actor)):
        return (H._task_party(c, actor, row) or H.can_move(c, actor)
                or bool(owner_email and (person.get("email") or "").lower() == owner_email.lower()))
    return False


def evidence(c, row, actor, receipt_owner=None):
    from . import hubdb as H
    from . import task_privacy as privacy
    from .read_cache import configs
    if receipt_owner is None:
        owner = c.execute("SELECT value_json FROM registry_metadata WHERE key='owner'").fetchone()
        receipt_owner = (H._json(owner[0], {}) or {}).get("email") if owner else None
    author_cache = {}
    parents = {r["bot"]: r["reports_to"] or "" for r in configs(c)}
    def collect(item):
        messages = []
        authors = set()
        conv = H.conversation(c, item.get("conversation_id"))
        for raw in c.execute("SELECT * FROM messages WHERE conversation_id=? AND deleted_at IS NULL ORDER BY created,id",
                             (item.get("conversation_id"),)).fetchall():
            m = {**dict(raw), "refs": H._json(raw["refs_json"], {}) or {}}
            if H.message_task_id(m, conv) != item["id"] or any(m.get(k) for k in test_receipts.REMOVED):
                continue
            if (privacy.message_readable(c, actor, m, current=True)
                    and privacy.message_readable(c, row["owner"], m, current=True)):
                messages.append(m)
                author = m.get("from_actor")
                if author not in author_cache:
                    author_cache[author] = receipt_author(c, row, author, parents, receipt_owner)
                if author_cache[author]:
                    authors.add(author)
        return {**item, "messages": messages, "receipt_authors": sorted(authors)}
    result = collect(row)
    result["children"] = []
    from . import task_relations as TR
    for child_id in TR.child_ids(c, row["id"]):
        item = H.task(c, child_id)
        if (REVIEW.match(item.get("title", "")) and H.task_private_readable(c, actor, item)
                and H.task_private_readable(c, row["owner"], item)):
            result["children"].append(collect(item))
    return result


def check(c, actor, row, snapshots=None, note=None, require_pr=False, receipt_owner=None):
    from . import hubdb as H
    if not engineering(c, row):
        return
    writers = "The owner, a manager above the owner, or a person who may edit this ticket"
    prs = review_links(c, row) if require_pr else links(c, row)
    if not prs:
        if require_pr:
            closed = [p for p in H.task_links(c, row["id"]) if p["kind"] == "pr" and p.get("state") == "closed"]
            if closed:
                reject(c, actor, row, "receipt", "unknown", closed[-1],
                       f"Review refused: {closed[-1]['url']} was closed without merging, so it has no change to review. "
                       "Link the PR that carries the change (reopen this one or open a new one) before asking a "
                       "reviewer. The owner or a person who may edit this ticket can link it.")
            reject(c, actor, row, "receipt", "unknown", {"url": "no linked PR"},
                   "Review refused: no linked PR. Link the current PR before asking a reviewer. "
                   "The owner or a person who may edit this ticket can link it.")
        return  # A board-only step move has no software reviewer to wake.
    facts = evidence(c, row, actor, receipt_owner)
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
            if pr.get("state") in MERGED:
                merged_refusal(c, actor, row, "ci", head, pr, f"Cannot verify that head and its CI ({why}).",
                               "Retry the review request.", retryable=detail.get("retryable", False))
            # Every move reads GitHub afresh, so a transient failure clears on a plain retry.
            reject(c, actor, row, "ci", head, pr, f"Review refused: cannot verify the current head and CI for {pr['url']} ({why}). "
                                                  "Retry the review move. Anyone allowed to move this ticket can retry.",
                   retryable=detail.get("retryable", False))
        failing = sorted(k.removeprefix("check:").removeprefix("status:") for k, v in (detail.get("checks") or {}).items() if v == "failing")
        if failing or snapshots is None and pr.get("checks") == "failing":
            names = ", ".join(failing) or "Checks (refresh the PR link for check names)"
            if pr.get("state") in MERGED:
                merged_refusal(c, actor, row, "ci", head, pr, f"CI failing at that head: {names}.",
                               "A merged PR takes no new head: fix the failure in a follow-up PR and ask for the "
                               "review on that PR.")
            reject(c, actor, row, "ci", head, pr, f"Review refused: CI failing at head {head} on {pr['url']}: {names}. "
                   "A failed GitHub check attempt still blocks after a passing rerun at the same head. "
                   "Have the owner or another contributor fix the failure and push a new head, then retry the review move.")
    for pr in prs:
        detail = snapshots[pr["id"]] if snapshots is not None else (H._json(pr.get("detail_json"), {}) or {})
        head = detail["head_sha"]
        checks = detail.get("checks") or {}
        if pr.get("state") in MERGED and snapshots is not None:
            # Head of Engineering, Oct 26: the merge already required green checks, so all-green checks at the merged
            # head are its receipt. A pending or missing check, or a failed receipt at that head, still refuses.
            waiting = sorted(k.removeprefix("check:").removeprefix("status:") for k, v in checks.items()
                             if v not in ("passing", "skipped"))
            if waiting or "passing" not in checks.values():
                merged_refusal(c, actor, row, "ci", head, pr,
                               f"Its checks at that head are not all passing: {', '.join(waiting)} still pending."
                               if waiting else "No passing GitHub check is recorded at that head.",
                               "Wait for the checks to pass (or rerun them on GitHub), then ask for the review again.")
            if test_receipts.at_head(facts, head)[0] == "failed":
                merged_refusal(c, actor, row, "receipt", head, pr, "A failed test receipt is recorded at that head.",
                               "A merged PR takes no new head: fix the failure in a follow-up PR and ask for the "
                               "review on that PR.")
            continue
        # Current all-green CI is stronger evidence than prose, but only from a fresh read: the webhook-built cache
        # records skipped and neutral checks as passing, so it cannot tell a real pass from a skip.
        if snapshots is not None and checks and "passing" in checks.values() and all(v in ("passing", "skipped") for v in checks.values()):
            continue
        state, receipts = test_receipts.at_head(facts, head)
        if state != "passed":
            newest = next((r["head"] for r in reversed(receipts) if r["head"]), "no receipt")
            if pr.get("state") in MERGED:
                merged_refusal(c, actor, row, "receipt", head, pr,
                               f"{state.capitalize()} test receipt at that head; newest receipt head: {newest}.",
                               "Fix the failure in a follow-up PR and ask for the review on that PR."
                               if state == "failed" else
                               "Post a passing test receipt naming the merged head on this ticket, then ask for the "
                               "review again. " + writers + " can post it.")
            if state == "failed":
                action = ("Have the owner or another contributor fix the recorded test failure and push a new head, "
                          "then retry with passing CI or a passing receipt. A written same-head rerun cannot clear it; "
                          "all-green GitHub checks can. " + writers + " can post the receipt.")
            else:
                action = ("Post a passing test receipt naming the current head on this ticket, then retry the review move. "
                          + writers + " can post it.")
            reject(c, actor, row, "receipt", head, pr, f"Review refused: {state} test receipt for {pr['url']} current head {head}; newest receipt head: {newest}. {action}")


def reject(c, actor, row, kind, head, pr, text, retryable=False):
    from . import hubdb as H
    try:
        H.refuse(c, actor, "review_gate", text, task=row["id"])
    except H.Refused as exc:
        exc.retryable = retryable
        exc.review_gate = {"task": row["id"], "kind": kind, "head": head, "pr": pr["url"], "text": text}
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
