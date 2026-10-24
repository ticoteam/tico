"""Deterministic review handoff gate; runs before any step change or review child wake."""
import json
import re
from urllib.parse import quote

from . import test_receipts

REVIEW = re.compile(r"^Review [12](?:\b|:)", re.I)


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
                raise ValueError("unresolved PR")
            token, _ = service.mint([repo], {"pull_requests": "read", "checks": "read", "statuses": "read"})
            headers = {"Authorization": "Bearer " + token}
            base = f"/repos/{quote(repo, safe='/')}"
            def read(path, params=None):
                response = service._call("GET", base + path, headers=headers, params=params)
                if response.status_code != 200:
                    raise ValueError("GitHub read failed")
                return response.json()
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
                    raise ValueError("checks incomplete")
            # Detect a push while collecting, rather than blessing a now-stale head.
            if read(f"/pulls/{pr['number']}")["head"]["sha"] != head:
                raise ValueError("head changed during collection")
            out[pr["id"]] = {"head_sha": head, "checks": checks, "at": H.now()}
        except Exception:
            out[pr["id"]] = {"error": True}
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
            reject(c, actor, row, "ci", head, pr, f"Review refused: cannot verify the current head and CI for {pr['url']}. Refresh the PR link and retry the review move.")
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


def reject(c, actor, row, kind, head, pr, text):
    from . import hubdb as H
    try:
        H.refuse(c, actor, "review_gate", text, task=row["id"])
    except H.Refused as exc:
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
                     {"task": row["id"], "comment": True, "review_gate": json.dumps(key, sort_keys=True)}, None, None)
