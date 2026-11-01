"""Deterministic review handoff gate; runs before any step change or review child wake."""
import json
import re
from urllib.parse import quote

import httpx
from jwt.exceptions import InvalidKeyError

from . import test_receipts



def is_review(c, parent, title):
    """A request titled for one of the parent board's review steps (backend/step_flow.titled)."""
    from . import step_flow
    return step_flow.is_kind(c, parent, str(title or "").strip(), "review")


def check_of(c, parent, title):
    """The check step a request with this title is for on the parent's board: one open request per check step."""
    from . import step_flow
    return step_flow.titled(c, (parent or {}).get("type_id"), str(title or "").strip()) if parent else None


def reviewing(c, actor, row):
    """The review or Security subtask of this Dev ticket that `actor` owns, or None: a reviewer who is not the
    ticket's owner. A person is never one here (a person may move any ticket they could move before)."""
    from . import hubdb as H
    from . import task_relations as TR
    if not H.is_bot(actor) or actor == row["owner"] or not engineering(c, row):
        return None
    for child_id in TR.child_ids(c, row["id"]):
        child = H.task(c, child_id)
        # The children whose owners only review: they comment and give a verdict, and never move the ticket. QA is not
        # one: QA sends a ticket back to its fixes step and on to the merge (docs/software-factory.md).
        from .check_pools import is_security
        if child and child["owner"] == actor and (is_review(c, row, child.get("title")) or is_security(child.get("title"))):
            return child
    return None




def heads(c, parent, snapshots=None):
    """The pull request heads a check on this ticket would judge now: the fresh read when there is one, else the
    head the link last recorded. Empty when none is known."""
    from . import hubdb as H
    out = set()
    for pr in links(c, parent):
        detail = (snapshots or {}).get(pr["id"]) or H._json(pr.get("detail"), {}) or {}
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
    step = check_of(c, parent, title)
    if not step:
        return
    from . import step_flow
    kind = step_flow.label(step)
    wanted = heads(c, parent, snapshots)
    from . import check_pools
    for wait in check_pools.waiting(c, parent["id"], step["id"]):
        judged = set(filter(None, wait["heads"].split(",")))
        if wanted and judged and not wanted & judged:
            continue
        H.refuse(c, actor, "duplicate",
                 f"{wait['title']} already exists on this ticket{' for this head' if wanted else ''}: Tico filed it to "
                 f"the step's pool, where it waits for a reviewer ({wait['reason']}). The first free one takes it; "
                 "you need not file another.")
    for child_id in TR.child_ids(c, parent["id"]):
        other = H.task(c, child_id)
        other_step = check_of(c, parent, other.get("title"))
        if not other_step or other_step["id"] != step["id"] or other["status"] not in H.ACTIVE_STATUSES:
            continue
        seen = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='review_head' "
                         "ORDER BY ts DESC, rowid DESC LIMIT 1", (child_id,)).fetchone()
        judged = set(filter(None, (seen[0] if seen else "").split(",")))
        if wanted and judged and not wanted & judged:
            continue
        who = H.actor_id(other["owner"])
        H.refuse(c, actor, "duplicate",
                 f"{kind} is already open on this ticket"
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
    from .read_cache import one
    return bool(row and one(c, "SELECT 1 FROM task_types WHERE id=? AND (role=? OR name='Dev tickets')",
                            (row.get("type_id"), ENGINEERING)))


def links(c, row):
    from . import hubdb as H
    return [p for p in H.task_links(c, row["id"]) if p["kind"] == "pr"
            and p.get("state") not in ("merged", "closed", "shipped")]


def mint_failure(exc, repo):
    """Describe a failed token request without exposing exception text or response bodies."""
    from .store import Problem
    from .github_app import MISSING_KEY, rate_limited

    status = None
    code = None
    auth = isinstance(exc, InvalidKeyError)
    timeout = isinstance(exc, (TimeoutError, httpx.TimeoutException))
    retryable = timeout
    limited = False
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
        limited = exc.extra.get("rate_limited") is True
    elif isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        limited = rate_limited(exc.response)
    auth = not limited and (auth or status in (401, 403))
    retryable = not auth and (limited or timeout or retryable or status in (408, 429)
                             or isinstance(status, int) and 500 <= status < 600)
    # Only fixed-format diagnostic fields are safe to include; never copy Problem.detail.
    cause = []
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
        cause.append(code)
    if isinstance(status, int) and 100 <= status < 600:
        cause.append(f"HTTP {status}")
    if timeout:
        cause.append("timed out")
    if limited:
        cause.append("rate limited")
    if not cause:
        cause.append(type(exc).__name__)
    action = "the GitHub App could not sign in" if auth else "GitHub token request failed"
    # `auth` travels with the refusal so a refused move remembers whether access help is needed
    # (backend/move_retry.py); the prose `why` already names it, so this adds no new exposure.
    return {"error": True, "why": f"{action} for {repo}: {'; '.join(cause)}", "retryable": bool(retryable),
            "auth": bool(auth)}


def answered_cause(why):
    """Cause flags for GitHub's answer to a content read (backend/move_retry.py): a 401 or plain 403 needs access help;
    a rate limit, 408 or 5xx is temporary. Any other failure carries no cause. `why` is this module's own fixed-format
    string, never a remote body."""
    if why == "GitHub answered 403 rate limited":
        return {"retryable": True, "auth": False}
    match = re.fullmatch(r"GitHub answered (\d+)", why or "")
    status = int(match[1]) if match else None
    if status in (401, 403):
        return {"retryable": False, "auth": True}
    if status == 408 or status == 429 or isinstance(status, int) and 500 <= status < 600:
        return {"retryable": True, "auth": False}
    return {"retryable": False, "auth": False}


def snapshot(service, prs):
    from . import hubdb as H
    from .github_app import rate_limited
    """Read current PR heads and all checks outside the database write lock.

    An unconfigured GitHub integration uses its signed-webhook link snapshot.
    Configured integrations fail closed on incomplete or unavailable reads.
    """
    with service.store.read() as c:
        if not service.row(c):
            return None
    out = {}
    for pr in prs:
        repo = None
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
                    limited = " rate limited" if response.status_code == 403 and rate_limited(response) else ""
                    raise ValueError(f"GitHub answered {response.status_code}{limited}")
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
            # An unresolved link is Tico's own record, not GitHub's answer: relinking clears it, not access help.
            out[pr["id"]] = {"error": True, "why": str(exc), **answered_cause(str(exc)),
                             **({"local": True} if str(exc) == "unresolved PR" else {})}
        except Exception as exc:
            # Only the exception's type: a transport error's text can carry request details. Its cause flags
            # still travel, classified the same way as a token failure, so a refused move remembers them.
            cause = mint_failure(exc, repo)
            out[pr["id"]] = {"error": True, "why": "GitHub read failed: " + type(exc).__name__,
                             "retryable": cause["retryable"], "auth": cause["auth"]}
    if any(r.get("head_sha") and not r.get("error") for r in out.values()):
        from . import source_outages
        with service.store.transaction() as c:
            source_outages.github_read(c, prs, out)
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
        owner = c.execute("SELECT value FROM registry_metadata WHERE key='owner'").fetchone()
        receipt_owner = (H._json(owner[0], {}) or {}).get("email") if owner else None
    author_cache = {}
    parents = {r["bot"]: r["reports_to"] or "" for r in configs(c)}
    def collect(item):
        messages = []
        authors = set()
        conv = H.conversation(c, item.get("conversation_id"))
        for raw in c.execute("SELECT * FROM messages WHERE conversation_id=? AND deleted_at IS NULL ORDER BY created_at,id",
                             (item.get("conversation_id"),)).fetchall():
            m = {**dict(raw), "refs": H._json(raw["refs"], {}) or {}}
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
    from . import step_flow
    result = collect(row)
    result["children"] = []
    result["review_steps"] = [s["name"] for s in step_flow.checks(c, row.get("type_id"), "review")]
    from . import task_relations as TR
    for child_id in TR.child_ids(c, row["id"]):
        item = H.task(c, child_id)
        if (is_review(c, row, item.get("title")) and H.task_private_readable(c, actor, item)
                and H.task_private_readable(c, row["owner"], item)):
            result["children"].append({**collect(item), "review_check": True})
    return result


def check(c, actor, row, snapshots=None, note=None, require_pr=False, reader=None, receipt_owner=None):
    from . import hubdb as H
    try:
        if notice := problem(c, row, actor, snapshots, note, require_pr, receipt_owner, reader=reader):
            reject(c, actor, row, *notice)
    except H.Refused as exc:
        # The mover a refused move is retried as (backend/move_retry.py): the actor the move was made as, which can
        # differ from the request's caller (BotOps acting for a person, a service key acting as Tico).
        if getattr(exc, "review_gate", None) is not None:
            exc.review_gate.setdefault("by", reader or actor)
        raise


def problem(c, row, actor, snapshots=None, note=None, require_pr=False, receipt_owner=None, reader=None):
    """Read the current gate without writing refusals; shared by automatic hold clearing."""
    from . import hubdb as H
    if not engineering(c, row):
        return
    writers = "The owner, a manager above the owner, or a person who may edit this ticket"
    prs = links(c, row)
    if not prs:
        if require_pr:
            return ("receipt", "unknown", {"url": "no linked PR"},
                   "Review refused: no linked PR. Link the current PR before asking a reviewer. "
                   "The owner or a person who may edit this ticket can link it.")
        return  # A board-only step move has no software reviewer to wake.
    # Tico's retry of a refused move (backend/move_retry.py) reads as whoever made the move: the keeper is on no private
    # task, and evidence that mover cannot read must not pass the move for them.
    facts = evidence(c, row, reader or actor, receipt_owner)
    if note is not None:
        facts["note"] = note
        facts["updated_at"] = H.now()
    # CI is checked across all linked PRs before looking at any receipt.
    for pr in prs:
        detail = snapshots.get(pr["id"], {"error": True}) if snapshots is not None else (H._json(pr.get("detail"), {}) or {})
        head = detail.get("head_sha") or "unknown"
        cached = H._json(pr.get("detail"), {}) or {}
        changed = snapshots is not None and (pr.get("updated_at") or "") > detail.get("at", "") and cached.get("head_sha") != head
        if changed or detail.get("error") or not test_receipts.SHA.fullmatch(head):
            why = (detail.get("why") or "GitHub read failed" if detail.get("error")
                   else "the PR link changed while GitHub was read" if changed else "no PR head recorded")
            # Every move reads GitHub afresh, so a transient failure clears on a plain retry. The cause flags
            # travel with the refusal so the remembered move routes like the read reported (backend/move_retry.py).
            # A changed link or a missing recorded head is Tico's own record, not a GitHub failure.
            local = detail.get("local", False) if detail.get("error") else True
            return ("ci", head, pr, f"Review refused: cannot verify the current head and CI for {pr['url']} ({why}). "
                                  "Retry the review move. Anyone allowed to move this ticket can retry.",
                    detail.get("retryable", False), detail.get("auth", False), local)
        failing = sorted(k.removeprefix("check:").removeprefix("status:") for k, v in (detail.get("checks") or {}).items() if v == "failing")
        if failing or snapshots is None and pr.get("checks") == "failing":
            names = ", ".join(failing) or "Checks (refresh the PR link for check names)"
            return ("ci", head, pr, f"Review refused: CI failing at head {head} on {pr['url']}: {names}. "
                   "A failed GitHub check attempt still blocks after a passing rerun at the same head. "
                   "Have the owner or another contributor fix the failure and push a new head, then retry the review move.")
    for pr in prs:
        detail = snapshots[pr["id"]] if snapshots is not None else (H._json(pr.get("detail"), {}) or {})
        head = detail["head_sha"]
        checks = detail.get("checks") or {}
        # Current all-green CI is stronger evidence than prose, but only from a fresh read: the webhook-built cache
        # records skipped and neutral checks as passing, so it cannot tell a real pass from a skip.
        if snapshots is not None and checks and "passing" in checks.values() and all(v in ("passing", "skipped") for v in checks.values()):
            continue
        state, receipts = test_receipts.at_head(facts, head)
        if state != "passed":
            newest = next((r["head"] for r in reversed(receipts) if r["head"]), "no receipt")
            if state == "failed":
                action = ("Have the owner or another contributor fix the recorded test failure and push a new head, "
                          "then retry with passing CI or a passing receipt. A written same-head rerun cannot clear it; "
                          "all-green GitHub checks can. " + writers + " can post the receipt.")
            else:
                action = ("Post a passing test receipt naming the current head on this ticket, then retry the review move. "
                          + writers + " can post it.")
            return ("receipt", head, pr, f"Review refused: {state} test receipt for {pr['url']} current head {head}; newest receipt head: {newest}. {action}")


def reject(c, actor, row, kind, head, pr, text, retryable=False, auth=False, local=False):
    from . import hubdb as H
    from . import move_retry
    text += move_retry.hint(kind, text)
    try:
        H.refuse(c, actor, "review_gate", text, task=row["id"])
    except H.Refused as exc:
        exc.retryable = retryable
        # The read's verdict on the failure, in the notice's own vocabulary: a retryable read is temporary and
        # never routes; an access failure routes on the first refusal; a local one (Tico's own link record) never
        # routes. `remember` keeps these (backend/move_retry.py).
        exc.review_gate = {"task": row["id"], "kind": kind, "head": head, "pr": pr["url"], "text": text,
                           "temporary": bool(retryable), "devops": bool(auth), "local": bool(local)}
        raise


def notify(c, actor, notice):
    """After refusal rollback, keep one Builder comment per ticket/head/check kind."""
    from . import hubdb as H
    row = H.task(c, notice["task"])
    from . import move_retry
    source_wait = not notice.get('local') and (move_retry.outage(notice.get('text')) or notice.get('problem_code'))
    if row and not source_wait:
        from .task_blocked import hold
        hold(c, row["id"], notice["text"], "gate")
        c.execute("UPDATE tasks SET blocked_gate=? WHERE id=?", (
            json.dumps({**{k: notice[k] for k in ("head", "kind", "pr")}, "at": H.now()}), row["id"]))
    if not row or actor == row["owner"]:
        return
    conv = H.conversation(c, row["conversation_id"])
    key = {k: notice[k] for k in ("kind", "head")}
    if c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND json_extract(refs,'$.task')=? "
                 "AND json_extract(refs,'$.review_gate')=? LIMIT 1",
                 (conv["id"], row["id"], json.dumps(key, sort_keys=True))).fetchone():
        return
    # A temporary GitHub failure is Tico's to retry on its timer (backend/move_retry.py) and nobody's to fix: the
    # comment stays on the ticket without waking its owner for a turn with nothing to do, which by the time the
    # owner's queue reaches it reads a ticket the retry has already moved.
    H._write_message(c, H.KEEPER, row["owner"], notice["text"], conv, "say",
                     {"task": row["id"], "comment": True, "news": "blocker", "review_gate": json.dumps(key, sort_keys=True),
                      **({"quiet": True} if source_wait else {})}, None, None)
