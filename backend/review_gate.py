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


# A move into one of these steps passes review when at least one review slot approves the current change.
PAST_REVIEW = ("In QA", "Pending Release", "Post Release QA", "Done")
SLOT = re.compile(r"^Review ([12])(?:\b|:)", re.I)
# The commit status context review_status posts.
REVIEW_STATUS = "tico/reviews"
# Where the move-time preflight leaves the changes it read from GitHub, beside the PR snapshots (keyed by link id).
CHANGES = "changes"


def judged_links(c, row):
    """The pull requests a move past review judges: every linked one but a PR closed without merging. A merged or
    shipped PR still counts, since the Builder merges before Pending Release."""
    from . import hubdb as H
    return [p for p in H.task_links(c, row["id"]) if p["kind"] == "pr" and p.get("state") != "closed"]


def remember_verdict(c, actor, child, verdict, head):
    """Copy a Review 1 or Review 2 child's verdict onto its Dev ticket the moment it is given, so declining, retitling
    or unlinking the child later cannot take a `changes` back (slots)."""
    from . import hubdb as H
    from . import task_relations as TR
    kind = SLOT.match(str(child.get("title") or "").strip())
    parent = kind and H.task(c, TR.parent_of(c, child["id"]) or "")
    if parent and engineering(c, parent):
        H._task_event(c, parent["id"], actor, "review_verdict", None,
                      f"{kind[1]} {verdict} {head or '-'} {child['id']} {child['owner']}", "")


def slots(c, row):
    """Review-slot verdicts on this ticket, oldest first, as (verdict, head, reviewer, child id).
    A verdict counts from the copy its ticket keeps (remember_verdict); a child with no copy, from before the copies,
    counts from its own events while it still carries the title it was filed with. Only a "Review 1" or "Review 2"
    child fills a slot: a QA verdict, `approve` or `pass`, never does, and neither does a verdict the ticket's own owner
    gave or was the reviewer for. The move gate accepts one slot whose latest verdict approves the current change; a
    child reopened for a new head keeps its old verdict until it gives a new one."""
    from . import hubdb as H
    from . import task_relations as TR
    steps = {r["name"]: r["id"] for r in c.execute("SELECT id,name FROM task_steps WHERE type_id=?", (row.get("type_id"),))}
    owner = row.get("owner")
    out, asked, copied = {}, {}, set()
    for ts, rowid, actor, new in c.execute("SELECT ts, rowid, actor, new FROM task_events WHERE task_id=? "
                                           "AND field='review_verdict'", (row["id"],)).fetchall():
        number, verdict, head, child_id, reviewer = (str(new or "").split(" ") + [""] * 5)[:5]
        copied.add(child_id)
        if number not in ("1", "2") or owner in (actor, reviewer):
            continue
        name = "Review " + number
        asked.setdefault(name, [])
        out.setdefault(name, []).append(((ts, rowid), verdict, "" if head == "-" else head.lower(), reviewer, child_id))
    for child_id in TR.child_ids(c, row["id"]):
        child = H.task(c, child_id)
        kind = child and SLOT.match(str(child.get("title") or "").strip())
        if not kind or child["owner"] == owner:
            continue
        name = "Review " + kind[1]
        events = [] if child_id in copied or c.execute(
            "SELECT 1 FROM task_events WHERE task_id=? AND field='title' LIMIT 1", (child_id,)).fetchone() else \
            c.execute("SELECT ts, rowid, actor, new FROM task_events WHERE task_id=? AND field='verdict'",
                      (child_id,)).fetchall()
        if child["status"] == "declined" and not events and child_id not in copied:
            continue
        asked.setdefault(name, []).append(child["owner"])
        for ts, rowid, actor, new in events:
            if actor == owner:
                continue
            verdict, _, head = str(new or "").partition(" ")
            out.setdefault(name, []).append(((ts, rowid), verdict, head.strip().lower(), child["owner"], child_id))
    required = {}
    for name in ("Review 1", "Review 2"):
        entered = name in steps and c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='step' AND new=? "
                                              "LIMIT 1", (row["id"], steps[name])).fetchone()
        if name in asked or entered or name == "Review 1" and name in steps:
            events = sorted(out.get(name, []), key=lambda e: e[0])
            required[name] = {"verdicts": [e[1:] for e in events], "asked": asked.get(name, [])}
    return required


def _matches_head(link, head, approved, same):
    return any(head.startswith(a) or a.startswith(head) or (same is not None and same(link, a, head))
               for a in approved if a)


def security_required(c, row):
    """Security tickets keep the QA gate even though ordinary Dev tickets do not."""
    from . import hubdb as H, task_relations as TR
    tags = {str(t.get("key") or "").strip().lower() for t in row.get("tags", [])}
    if "security" in tags:
        return True
    return any((child := H.task(c, child_id))
               and re.match(r"^Security(?:\b|:)", str(child.get("title") or "").strip(), re.I)
               for child_id in TR.child_ids(c, row["id"]))


def _qa_missing(c, row, heads, same):
    """Security tickets need a QA pass of the current change; ordinary QA never gates a move."""
    from . import hubdb as H, task_relations as TR
    if not security_required(c, row):
        return []
    open_ids = {p["id"] for p in H.task_links(c, row["id"])
                if p["kind"] == "pr" and p.get("state") in ("open", "draft")}
    heads = {link: head for link, head in heads.items() if link in open_ids}
    if not heads:
        return []  # QA is a pre-merge requirement and cannot hold a post-merge release step.
    children, asked = [], []
    for child_id in TR.child_ids(c, row["id"]):
        child = H.task(c, child_id)
        if child and re.match(r"^QA(?:\b|:)", str(child.get("title") or "").strip(), re.I):
            if child.get("owner") != row.get("owner"):
                asked.append(child["owner"])
            events = c.execute("SELECT ts,rowid,actor,new FROM task_events WHERE task_id=? AND field='verdict' "
                               "ORDER BY ts,rowid", (child_id,)).fetchall()
            for event in events:
                value, _, judged = str(event["new"] or "").partition(" ")
                if event["actor"] != row.get("owner") and child.get("owner") != row.get("owner"):
                    children.append((event["ts"], event["rowid"], value, judged.strip().lower(), child["owner"], child_id))
    latest = {}
    for event in sorted(children):
        latest[event[5]] = event
    good = [e for e in latest.values() if e[2] == "pass"]
    for link, head in heads.items():
        if not head:
            continue
        if any(_matches_head(link, head, [e[3]], same) for e in good):
            continue
        reviewer = good[-1][4] if good else (asked[-1] if asked else None)
        reason = "has no verdict" if asked else "was never asked"
        if children and children[-1][2] != "pass":
            reason = f"has `{children[-1][2]}` at {children[-1][3][:12] or 'an unnamed head'}"
        elif good:
            reason = f"passed {good[-1][3][:12]}, and the change at {head[:12]} differs from it"
        return [{"slot": "Security QA", "reviewer": reviewer, "head": head, "why": reason}]
    return []


def unapproved(c, row, heads, same=None):
    """Why this ticket has not passed its review gate. One current-change approval in either review slot is enough;
    security tickets also need a QA pass. `same` identifies a clean main-merge-only delta."""
    out = []
    all_slots = slots(c, row)
    current = next((h for h in heads.values() if h), "")
    for link, head in heads.items():
        if not head:
            continue
        failures = []
        for name, slot in all_slots.items():
            verdicts = slot["verdicts"]
            reviewer = verdicts[-1][2] if verdicts else (slot["asked"] or [None])[-1]
            if not verdicts:
                failures.append({"slot": name, "reviewer": reviewer, "head": head,
                                 "why": "has no verdict" if slot["asked"] else "was never asked"})
                continue
            verdict, judged = verdicts[-1][0], verdicts[-1][1]
            if verdict != "approve":
                failures.append({"slot": name, "reviewer": reviewer, "head": head,
                                 "why": f"has `{verdict}` at {judged[:12] or 'an unnamed head'}"})
                continue
            if judged and (head.startswith(judged) or judged.startswith(head) or same and same(link, judged, head)):
                break
            shown = judged[:12] if judged else "no named head"
            why = (f"approved {shown}, and the change at {head[:12]} differs from it (more than a clean main merge)"
                   if judged and same and same(link, judged, head) is False else
                   f"approved {shown}, and GitHub could not compare that change with the one at {head[:12]}")
            failures.append({"slot": name, "reviewer": reviewer, "head": head, "why": why})
        else:
            # No review slot approved this PR's current change. A board with no review step keeps its existing flow.
            if all_slots or security_required(c, row):
                out.extend(failures or [{"slot": "Review 1", "reviewer": None, "head": head,
                                         "why": "was never asked"}])
    out.extend(_qa_missing(c, row, heads, same))
    return out


def passed(c, actor, row, snapshots=None, step="the next step"):
    """Refuse a move past review until one slot approves the current change, plus QA on a Security ticket.
    A person may still move it; nothing is checked on a ticket with no linked pull request."""
    from . import hubdb as H
    if H.is_human(actor) or not engineering(c, row):
        return
    prs = judged_links(c, row)
    if not prs or (not slots(c, row) and not security_required(c, row)):
        return  # nothing to approve: a board with no review step, and no review ever asked
    heads = {}
    for pr in prs:
        detail = (snapshots or {}).get(pr["id"]) if snapshots is not None else None
        cached = H._json(pr.get("detail_json"), {}) or {}
        if snapshots is not None and detail is None:
            detail = {"error": True, "why": "the PR was linked after GitHub was read"}
        head = str((detail or cached).get("head_sha") or "").lower()
        # A webhook that moved the head after the preflight read GitHub leaves a head nobody compared.
        changed = detail is not None and not detail.get("error") and (pr.get("updated") or "") > detail.get("at", "") \
            and str(cached.get("head_sha") or "").lower() != head
        if detail is not None and detail.get("error") or changed:
            why = detail.get("why") or "GitHub read failed" if detail.get("error") else "the PR link changed while GitHub was read"
            _refuse(c, actor, row, f"Move refused: cannot read the current head of {pr['url']} ({why}). "
                                   "Retry the move.", retryable=bool(changed) or detail.get("retryable", False))
        if not test_receipts.SHA.fullmatch(head):
            # A head nobody knows has no approval to match: refuse rather than skip that PR.
            _refuse(c, actor, row, f"Move to {step} refused: the current head of {pr['url']} is not known yet, so no "
                                   "review approval can be matched to it. Refresh the PR link (or wait for GitHub's "
                                   "next update) and retry the move.", retryable=True)
        heads[pr["id"]] = head
    read = (snapshots or {}).get(CHANGES) or {}
    missing = unapproved(c, row, heads, comparer(read))
    if not missing:
        return
    lines = []
    for m in missing:
        who = H.actor_id(m["reviewer"]) if m["reviewer"] else "its reviewer"
        at = f" on head {m['head']}" if m["head"] else ""
        if m["why"] == "was never asked":
            lines.append(f"{m['slot']} was never asked: file {m['slot']}{at} and move once it approves.")
        else:
            lines.append(f"{m['slot']} {m['why']}: ask {who} to re-review{at} (the delta since its last verdict) "
                         f"and move once it approves.")
    _refuse(c, actor, row, f"Move to {step} refused: a required review has not approved the current change. "
                           + " ".join(lines) + " A person may still move the ticket.")


def _refuse(c, actor, row, text, retryable=False):
    """A plain refusal, unlike reject(): the ticket stays at its step rather than going Blocked, since what clears it
    is a review the mover asks for (or a person's move), not a check Tico can watch, and nothing retries it."""
    from . import hubdb as H
    try:
        H.refuse(c, actor, "review_gate", text, task=row["id"])
    except H.Refused as exc:
        exc.retryable = retryable
        raise


def comparer(read):
    """`same(link, a, b)` over the changes a preflight read: whether head b makes head a's change, None if unknown."""
    def same(link, a, b):
        left, right = read.get(f"{link} {a}"), read.get(f"{link} {b}")
        return None if left is None or right is None else left == right
    return same


def normalized_change(diff):
    """A diff reduced to what freeze_guard.change_id compares: file headers, modes, hunk breaks and every changed line
    verbatim, without the line numbers, blob ids and context lines that shift when main is merged in. None when the
    diff is empty or changes a binary file."""
    kept, in_body = [], False
    for line in str(diff or "").splitlines():
        if line.startswith("diff --git "):
            kept.append(line)
            in_body = False
        elif line.startswith("Binary files"):
            return None  # GitHub's diff hides binary content, so two different edits would read the same
        elif line.startswith("@@"):
            in_body = True
            kept.append("@@")
        elif in_body and line.startswith(("+", "-", "\\")) or line.startswith((
                "new file mode", "deleted file mode", "old mode", "new mode")):
            kept.append(line)
    return "\n".join(kept) or None


def changes(service, prs, wanted):
    """For each (link, head) in `wanted`, the change that head makes on top of its fork from the PR's base branch,
    read from GitHub's compare (outside the write lock); None when GitHub cannot give it, so the gate refuses."""
    out = {}
    for pr in prs:
        shas = sorted({sha for link, sha in wanted if link == pr["id"]})
        if not shas:
            continue
        try:
            repo = pr.get("repo")
            token, _ = service.mint([repo], {"pull_requests": "read", "contents": "read"})
            base = f"/repos/{quote(repo, safe='/')}"
            auth = {"Authorization": "Bearer " + token}
            data = service._call("GET", base + f"/pulls/{pr['number']}", headers=auth)
            pull = data.json() if data.status_code == 200 else {}
            branch = pull["base"]["ref"] if pull else None
            if pull.get("merged"):
                # Once merged, every head is behind the base branch and compares as empty; like freeze_guard, compare
                # from the base as it stood when the merge landed: the merge commit's first parent.
                merge = pull.get("merge_commit_sha") or ""
                commit = service._call("GET", base + f"/commits/{quote(merge, safe='')}", headers=auth) if merge else None
                parents = commit.json().get("parents") if commit is not None and commit.status_code == 200 else None
                branch = parents[0]["sha"] if parents else None
        except Exception:
            branch = None
        for sha in shas:
            text = None
            if branch:
                try:
                    response = service._call("GET", base + f"/compare/{quote(branch, safe='')}...{sha}",
                                             headers={**auth, "Accept": "application/vnd.github.diff"})
                    text = normalized_change(response.text) if response.status_code == 200 else None
                except Exception:
                    text = None
            out[f"{pr['id']} {sha}"] = text
    return out


def wanted_changes(c, row, snapshots):
    """The (link, head) pairs whose changes a move past review compares: each current head and every head a slot
    approved since its last `changes`, where the two differ."""
    from . import hubdb as H
    out = set()
    required = slots(c, row)
    for pr in judged_links(c, row):
        detail = (snapshots or {}).get(pr["id"]) or H._json(pr.get("detail_json"), {}) or {}
        head = str(detail.get("head_sha") or "").lower()
        if not test_receipts.SHA.fullmatch(head):
            continue
        for slot in required.values():
            verdicts = slot["verdicts"]
            last_no = max((i for i, v in enumerate(verdicts) if v[0] != "approve"), default=-1)
            for verdict in verdicts[last_no + 1:]:
                if verdict[1] and not (head.startswith(verdict[1]) or verdict[1].startswith(head)):
                    out |= {(pr["id"], head), (pr["id"], verdict[1])}
    return out


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
    # `auth` travels with the refusal so a refused move remembers whether access help is needed
    # (backend/move_retry.py); the prose `why` already names it, so this adds no new exposure.
    return {"error": True, "why": f"{action} for {repo}: {'; '.join(cause)}", "retryable": bool(retryable),
            "auth": bool(auth)}


def answered_cause(why):
    """Cause flags for GitHub's answer to a content read (backend/move_retry.py): a 401 or 403 needs access help;
    a 408, 429 or 5xx is temporary. Any other failure carries no cause. `why` is this module's own fixed-format
    string, never a remote body."""
    match = re.fullmatch(r"GitHub answered (\d+)", why or "")
    status = int(match[1]) if match else None
    if status in (401, 403):
        return {"retryable": False, "auth": True}
    if status == 408 or status == 429 or isinstance(status, int) and 500 <= status < 600:
        return {"retryable": True, "auth": False}
    return {"retryable": False, "auth": False}


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
                        if not field and check.get("context") == REVIEW_STATUS:
                            continue  # Tico's own review result, not CI (a webhook of it is dropped too, github.pr_signal)
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
        detail = snapshots[pr["id"]] if snapshots is not None else (H._json(pr.get("detail_json"), {}) or {})
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
    if row:
        from .task_blocked import hold
        hold(c, row["id"], notice["text"], "gate")
        c.execute("UPDATE tasks SET blocked_gate_json=? WHERE id=?", (
            json.dumps({**{k: notice[k] for k in ("head", "kind", "pr")}, "at": H.now()}), row["id"]))
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
