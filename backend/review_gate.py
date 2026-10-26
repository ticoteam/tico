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


# A move into one of these steps passes review: every required review slot must approve the change the PR makes now.
PAST_REVIEW = ("In QA", "Pending Release", "Post Release QA", "Done")
SLOT = re.compile(r"^Review ([12])(?:\b|:)", re.I)
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
    """Each required review slot's verdicts on this ticket, oldest first, as (verdict, head, reviewer, child id).
    A verdict counts from the copy its ticket keeps (remember_verdict); a child with no copy, from before the copies,
    counts from its own events while it still carries the title it was filed with. Only a "Review 1" or "Review 2"
    child fills a slot: a QA verdict, `approve` or `pass`, never does, and neither does a verdict the ticket's own owner
    gave or was the reviewer for. Review 1 is required on a board that has the step; Review 2 once it was asked or the
    ticket entered it (small changes skip it, docs/software-factory.md). A child reopened for a new head keeps its old
    verdict until it gives a new one."""
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


def unapproved(c, row, heads, same=None):
    """Why this ticket has not passed review: one entry per required slot that lacks an approval of the change at a
    current PR head, else []. `heads` maps each judged PR link to its current head ('' when unknown); `same(link, a,
    b)` says whether the change at head b equals the change at head a apart from a clean main merge
    (freeze_guard.change_id), and is None when no comparison was read."""
    out = []
    for name, slot in slots(c, row).items():
        verdicts = slot["verdicts"]
        reviewer = (verdicts[-1][2] if verdicts else (slot["asked"] or [None])[-1])
        current = next((h for h in heads.values() if h), "")
        if not verdicts:
            out.append({"slot": name, "reviewer": reviewer, "head": current,
                        "why": "has no verdict" if slot["asked"] else "was never asked"})
            continue
        verdict, judged = verdicts[-1][0], verdicts[-1][1]
        if verdict != "approve":
            out.append({"slot": name, "reviewer": reviewer, "head": current,
                        "why": f"has `{verdict}` at {judged[:12] or 'an unnamed head'}"})
            continue
        last_no = max((i for i, v in enumerate(verdicts) if v[0] != "approve"), default=-1)
        approved = [v[1] for v in verdicts[last_no + 1:] if v[1]]
        for link, head in heads.items():
            if not head:
                continue
            found = [True if head.startswith(a) or a.startswith(head) else same(link, a, head) if same else None
                     for a in approved]
            if True not in found:
                shown = approved[-1][:12] if approved else "no named head"
                why = (f"approved {shown}, and the change at {head[:12]} differs from it (more than a clean main merge)"
                       if False in found else
                       f"approved {shown}, and GitHub could not compare that change with the one at {head[:12]}")
                out.append({"slot": name, "reviewer": reviewer, "head": head, "why": why})
                break
    return out


def passed(c, actor, row, snapshots=None, step="the next step"):
    """Refuse a move past review while a required Review 1 or Review 2 slot lacks an approval of the current change.
    A person may still move it; nothing is checked on a ticket with no linked pull request."""
    from . import hubdb as H
    if H.is_human(actor) or not engineering(c, row):
        return
    prs = judged_links(c, row)
    if not prs:
        return
    heads = {}
    for pr in prs:
        detail = (snapshots or {}).get(pr["id"]) if snapshots is not None else None
        cached = H._json(pr.get("detail_json"), {}) or {}
        head = str((detail or cached).get("head_sha") or "").lower()
        # A webhook that moved the head after the preflight read GitHub leaves a head nobody compared.
        changed = detail is not None and not detail.get("error") and (pr.get("updated") or "") > detail.get("at", "") \
            and str(cached.get("head_sha") or "").lower() != head
        if detail is not None and detail.get("error") or changed:
            why = detail.get("why") or "GitHub read failed" if detail.get("error") else "the PR link changed while GitHub was read"
            reject(c, actor, row, "reviews", "unknown", pr,
                   f"Move refused: cannot read the current head of {pr['url']} ({why}). "
                   "Retry the move.", retryable=bool(changed) or detail.get("retryable", False))
        heads[pr["id"]] = head if test_receipts.SHA.fullmatch(head) else ""
    read = (snapshots or {}).get(CHANGES) or {}
    missing = unapproved(c, row, heads, comparer(read))
    if not missing:
        return
    urls = {pr["id"]: pr["url"] for pr in prs}
    lines = []
    for m in missing:
        who = H.actor_id(m["reviewer"]) if m["reviewer"] else "its reviewer"
        at = f" on head {m['head']}" if m["head"] else ""
        if m["why"] == "was never asked":
            lines.append(f"{m['slot']} was never asked: file {m['slot']}{at} and move once it approves.")
        else:
            lines.append(f"{m['slot']} {m['why']}: ask {who} to re-review{at} (the delta since its last verdict) "
                         f"and move once it approves.")
    pr = prs[0]
    head = missing[0]["head"] or "unknown"
    reject(c, actor, row, "reviews", head, pr,
           f"Move to {step} refused: a required review has not approved the current change. " + " ".join(lines)
           + " A person may still move the ticket.")


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
            # Every move reads GitHub afresh, so a transient failure clears on a plain retry.
            return ("ci", head, pr, f"Review refused: cannot verify the current head and CI for {pr['url']} ({why}). "
                                  "Retry the review move. Anyone allowed to move this ticket can retry.",
                    detail.get("retryable", False))
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


def reject(c, actor, row, kind, head, pr, text, retryable=False):
    from . import hubdb as H
    from . import move_retry
    text += move_retry.hint(kind, text)
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
