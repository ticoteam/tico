"""Step flow: what a step of a task type does with a ticket, set on the step (Settings > Tasks) and never read from
its name. A board is a set of these settings; the Dev tickets board is one such set, given once to each Dev type.

A step's `flow` (all keys optional):
- `on_pass`: a check step's next step when its check passes or approves; `merge` when the merge is next.
- `on_fail`: where changes, a fail, or a check finished with no verdict (its comments) send the ticket.
- `after_green`: a work step's next step once CI is green on a head newer than the one its check judged; `merge`.
- `on_merge`: true on the one step a ticket goes to when its pull request merges, from any earlier step.
- `skip_tag`: a ticket carrying this tag passes this step by: it goes straight on to `on_pass`, and the skip is
  recorded on the ticket.
- `timeout_hours`: a check open this long is closed unanswered and the round is skipped (the ticket goes on as if it
  had passed with no comments).
- `status_hours`, `late_hours`: at a working step, a ticket quiet this long gets its bot owner one status check, and
  one this long at the step gets its manager one notice (backend/step_age.py). An engineering type's working steps
  have 2 and 4 without a setting.
- `backlog`: {over, per, notify, every_hours}: when more than `over` tickets wait at the step (or for one owner there),
  who to tell (backend/step_backlog.py).
- `can_hold`: false on steps a ticket is never put on hold from (a merged change is reverted or fixed forward).
- `assign`: {"pool": [bot slugs], "slots": n}: tickets here go to the least busy pool bot with a free slot
  (backend/task_allocator.py).
- `gate`: true on steps a ticket enters only with green CI and a test receipt at its head (backend/review_gate.py).
- `check`: the kind of check the step asks for: `review`, `qa` or `release_qa` (after the release). Every rule about
  reviews, QA, waivers and check pools finds its steps by this, never by their names (`check_kind`, `titled`). A saved
  flow that leaves `check` out keeps the step's kind; `"check": null` clears it.
- `other_company`: on a check step only, `prefer` (the default) or `require`: whether the check goes to a bot of another
  model company than the builder's before one of the builder's company (`prefer`), or only ever to another company's
  bot, waiting when none is free (`require`) (backend/check_pools.py, `ranked`).

Step references are stored by step id, so renaming a step keeps its flow; the API takes names or ids."""
import json
import re

from . import hubdb as H

MERGE = "merge"
KEYS = ("on_pass", "on_fail", "after_green", "on_merge", "skip_tag", "timeout_hours", "status_hours", "late_hours",
        "can_hold", "gate", "assign", "backlog", "check", "other_company")
HOURS = ("timeout_hours", "status_hours", "late_hours")
STEP_KEYS = ("on_pass", "on_fail", "after_green")
SEEDED = "step_flow.seeded"
CHECKS_SEEDED = "step_flow.checks_seeded"
CHECK_KINDS = ("review", "qa", "release_qa")
OTHER_COMPANY = ("prefer", "require")
MAX_TIMEOUT_HOURS = 24 * 14


def ensure(c):
    H.add_column(c, "task_steps", "flow", "TEXT")


def _ready(c):
    return "flow" in {r[1] for r in c.execute("PRAGMA table_info(task_steps)")}


# ----------------------------------------------------------------------------------------------- the setting
def of(step):
    """A step row's flow as a dict (empty when it has none)."""
    raw = (step or {}).get("flow")
    value = H._json(raw, {}) if isinstance(raw, str) else (raw or {})
    return value if isinstance(value, dict) else {}


def clean(c, actor, raw, existing):
    """The flow to store for one step being saved, before step references are resolved: what was sent, else what it
    had. Unknown keys and wrong kinds are refused."""
    if raw.get("flow") is None:
        return of(existing)
    flow = dict(raw["flow"])
    unknown = set(flow) - set(KEYS)
    if unknown:
        H.refuse(c, actor, "kind", "A step's flow takes " + ", ".join(KEYS) + "; not " + ", ".join(sorted(unknown)))
    out = {}
    for key in STEP_KEYS:
        if flow.get(key) not in (None, ""):
            out[key] = str(flow[key]).strip()
    if flow.get("skip_tag") not in (None, ""):
        out["skip_tag"] = str(flow["skip_tag"]).strip().lower()
    for key in HOURS:
        if flow.get(key) in (None, ""):
            continue
        hours = flow[key]
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < hours <= MAX_TIMEOUT_HOURS:
            H.refuse(c, actor, "kind", f"A step's {key} is a number of hours above 0, at most {MAX_TIMEOUT_HOURS}")
        out[key] = hours
    if flow.get("backlog") is not None:
        from . import step_backlog
        out["backlog"] = step_backlog.clean(c, actor, flow["backlog"])
    if flow.get("assign") is not None:
        from . import task_allocator
        out["assign"] = task_allocator.clean(c, actor, flow["assign"])
    if flow.get("check") not in (None, ""):
        if flow["check"] not in CHECK_KINDS:
            H.refuse(c, actor, "kind", "A step's check is " + ", ".join(CHECK_KINDS))
        out["check"] = flow["check"]
    elif "check" not in flow and check_kind(existing):
        # A flow sent without `check` (a steps file written before steps had kinds) keeps the step's kind: losing it
        # quietly makes its QA request an open child that holds the ticket. Send `"check": null` to clear it.
        out["check"] = check_kind(existing)
    if flow.get("other_company") not in (None, ""):
        if not out.get("check"):
            H.refuse(c, actor, "kind", "A step's other_company is set on a check step (review, qa or release_qa) only")
        if flow["other_company"] not in OTHER_COMPANY:
            H.refuse(c, actor, "kind", "A step's other_company is " + " or ".join(OTHER_COMPANY))
        out["other_company"] = flow["other_company"]
    for key in ("on_merge", "can_hold", "gate"):
        if flow.get(key) is not None:
            if not isinstance(flow[key], bool):
                H.refuse(c, actor, "kind", f"A step's {key} is true or false")
            out[key] = flow[key]
    return out


def resolve(c, actor, steps):
    """Point each flow's step references at step ids, once every step of the type is known (`steps`: the saved rows
    as dicts with id, name and flow). A reference to no step of the type is refused; at most one step is on_merge."""
    by_name = {s["name"].lower(): s["id"] for s in steps}
    ids = {s["id"] for s in steps}
    merged = [s["name"] for s in steps if s["flow"].get("on_merge")]
    if len(merged) > 1:
        H.refuse(c, actor, "kind", "Only one step takes a ticket whose pull request merged: " + ", ".join(merged))
    for s in steps:
        for key in STEP_KEYS:
            value = s["flow"].get(key)
            if value is None or value == MERGE:
                continue
            found = value if value in ids else by_name.get(value.lower())
            if not found:
                H.refuse(c, actor, "not-found", f"{s['name']}: {key} names {value}, which is no step of this type")
            s["flow"][key] = found
    return steps


def view(c, steps):
    """Steps as the API shows them: each flow with step names as well as ids."""
    names = {s["id"]: s["name"] for s in steps}
    out = []
    for s in steps:
        flow = of(s)
        shown = {k: v for k, v in flow.items()}
        for key in STEP_KEYS:
            if flow.get(key) and flow[key] != MERGE:
                shown[key] = names.get(flow[key], flow[key])
        row = {k: v for k, v in s.items() if k != "flow"}
        out.append({**row, "flow": shown})
    return out


# ----------------------------------------------------------------------------------------------- reading it
def step(c, step_id):
    row = c.execute("SELECT * FROM task_steps WHERE id=?", (step_id or "",)).fetchone()
    return dict(row) if row else None


def steps(c, type_id):
    return [dict(r) for r in c.execute("SELECT * FROM task_steps WHERE type_id=? ORDER BY position,id", (type_id,))]


def name(c, step_id):
    s = step(c, step_id)
    return s["name"] if s else None


def merge_step(c, type_id):
    """The step a ticket goes to when its pull request merges, or None when the type has none."""
    return next((s for s in steps(c, type_id) if of(s).get("on_merge")), None)


def has_flow(c, type_id):
    """Whether any step of the type has a flow: only then does Tico move its tickets."""
    return bool(type_id) and _ready(c) and bool(c.execute(
        "SELECT 1 FROM task_steps WHERE type_id=? AND flow IS NOT NULL AND flow NOT IN ('','{}') LIMIT 1",
        (type_id,)).fetchone())


def gated(c, step_id):
    """Whether moving onto this step passes the review gate (backend/review_gate.py): its `gate`."""
    return bool(of(step(c, step_id)).get("gate"))


def is_check(s):
    flow = of(s)
    return bool(flow.get("on_pass") or flow.get("on_fail"))


# ----------------------------------------------------------------------------------------------- check kinds
# A hand-filed request may still be titled the way the Dev board names its checks: `Review <n>` is the board's nth
# review step, `QA` its QA step and `Post Release QA` its check after the release.
GENERIC = re.compile(r"^(?:review\s+(\d+)|(post[- ]release\s+qa)|(qa))(?=\b|:|$)", re.I)


def check_kind(s):
    """The kind of check step `s` asks for (`review`, `qa`, `release_qa`), or None for a step that is no check."""
    value = of(s).get("check")
    return value if value in CHECK_KINDS else None


def other_company(s):
    """How a check step treats the builder's model company: `require` when only another company's bot may take its
    check, else `prefer` (the default, and every step that is no check)."""
    value = of(s).get("other_company")
    return value if value in OTHER_COMPANY else "prefer"


def checks(c, type_id, kind=None):
    """A type's check steps in board order, only those of one kind when `kind` is given."""
    if not type_id or not _ready(c):
        return []
    return [s for s in steps(c, type_id) if check_kind(s) and (kind is None or check_kind(s) == kind)]


def label(s):
    """What a check request for step `s` is titled with: `QA` for a QA step, else the step's name."""
    return "QA" if check_kind(s) == "qa" else s["name"]


def _starts(title, prefix):
    """A request is titled for a step by the step's name (or label) alone or followed by a colon, as Tico files them
    (`First look: PR #12`): a renamed `Review` step never makes "Review the docs" a check."""
    t, p = title.lower(), prefix.strip().lower()
    return bool(p) and (t == p or t.startswith(p + ":"))


def titled(c, type_id, title):
    """The check step of a type's board a request with this title is for, or None: the step whose name (or label)
    the title starts with, else the generic Dev board title for the board's step of that kind and order."""
    title = str(title or "").strip()
    board = checks(c, type_id)
    found = [(len(p), s) for s in board for p in {s["name"], label(s)} if _starts(title, p)]
    if found:
        return max(found, key=lambda x: (x[0], -x[1]["position"]))[1]
    m = GENERIC.match(title)
    if not m:
        return None
    kind = "review" if m[1] else "release_qa" if m[2] else "qa"
    order = int(m[1]) if m[1] else 1
    ofkind = [s for s in board if check_kind(s) == kind]
    if ofkind:
        # Only a step the board has: "Review 3 failing tests" on a two-review board is ordinary work.
        return ofkind[order - 1] if 0 < order <= len(ofkind) else None
    if order not in ((1, 2) if kind == "review" else (1,)):
        return None
    # The board has no step of that kind (or no flow at all): the Dev board's own titles, Review 1 and 2, QA and
    # Post Release QA, still name a kind of check, of no step.
    return {"id": f"{kind}:{order}", "type_id": type_id, "name": " ".join(m[0].split()), "position": -1,
            "status": "review", "flow": json.dumps({"check": kind}), "generic": True}


def child_check(c, child, ticket=None):
    """The check step of its ticket's board that a subtask is a request for (by its title), or None."""
    from . import task_relations as TR
    if not child:
        return None
    if ticket is None:
        parent = TR.parent_of(c, child["id"]) if child.get("id") else None
        ticket = H.task(c, parent) if parent else None
    return titled(c, (ticket or {}).get("type_id"), child.get("title")) if ticket else None


def is_kind(c, ticket, title, *kinds):
    """Whether a request with this title under `ticket` is a check of one of `kinds` (any check when none given)."""
    s = titled(c, (ticket or {}).get("type_id"), title) if ticket else None
    return bool(s) and (not kinds or check_kind(s) in kinds)


def reviews_before(c, s):
    """The review steps of the board ahead of check step `s` (Review 1 for Review 2 on the Dev board)."""
    return [x for x in checks(c, s["type_id"], "review") if x["position"] < s["position"] and x["id"] != s["id"]]


def fixes_for(c, s):
    """The check step whose changes come to work step `s` (its `on_fail` is `s`), or None."""
    return next((x for x in steps(c, s["type_id"]) if of(x).get("on_fail") == s["id"]), None)


def at_or_past(c, row, target):
    """Whether the ticket is already at `target` or at a later step of its type (position), or finished."""
    here = step(c, row.get("step_id"))
    return bool(here) and (here["id"] == target["id"] or here["position"] > target["position"]
                           or here["status"] in ("done", "closed", "declined"))


def check_step(c, check, ticket=None):
    """The step a check subtask was filed for: where its ticket was when the check was made."""
    from . import task_relations as TR
    ticket_id = (ticket or {}).get("id") or TR.parent_of(c, check["id"])
    if not ticket_id:
        return None
    found = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='step' AND ts<=? "
                      "ORDER BY ts DESC, rowid DESC LIMIT 1", (ticket_id, check.get("created_at") or "")).fetchone()
    s = step(c, found[0]) if found else None
    if s is None:
        parent = H.task(c, ticket_id)
        s = step(c, (parent or {}).get("step_id"))
    if not s or not is_check(s):
        return None
    # Ordinary subtasks made during a check are independent work, never checks to expire or consume.
    from .check_pools import is_security
    title = str(check.get("title") or "").strip()
    filed = c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='check_filed' AND new=? LIMIT 1",
                      (ticket_id, check["id"])).fetchone()
    return s if (filed or titled(c, s["type_id"], title) or is_security(title)
                 or title.lower() == label(s).lower() or title.lower().startswith(label(s).lower() + ":")) else None


def can_hold(c, step_id):
    return of(step(c, step_id)).get("can_hold", True) is not False


def skip_target(c, row, s):
    """Where a ticket entering step `s` goes instead when it carries the step's skip tag: its `on_pass`, or None."""
    flow = of(s)
    tag = flow.get("skip_tag")
    if not tag or tag not in {str(t).lower() for t in H.task_labels(row)}:
        return None
    return flow.get("on_pass")


# ----------------------------------------------------------------------------------------------- the Dev board
DEV = {   # the settings each Dev tickets type is given once; a person changes them on its steps
    "On Deck": {"backlog": {"over": 10, "per": "step", "notify": ["manager"], "every_hours": 4}},
    # A review is feedback, not a gate: whatever it says goes back to the builder, who moves the ticket on once its
    # feedback is in. Only QA passes or fails, and its pass makes the merge next.
    "Review 1": {"on_pass": "Review 1 Fixes", "on_fail": "Review 1 Fixes", "timeout_hours": 1, "gate": True,
                 "check": "review"},
    "Review 2": {"on_pass": "Review 2 Fixes", "on_fail": "Review 2 Fixes", "timeout_hours": 1, "gate": True,
                 "skip_tag": "review-1-only", "check": "review"},
    "In QA": {"on_pass": MERGE, "on_fail": "QA Fixes", "skip_tag": "skip-qa", "timeout_hours": 2, "check": "qa"},
    "Pending Release": {"on_merge": True, "can_hold": False},
    "Post Release QA": {"can_hold": False, "check": "release_qa"},
    "Done": {"can_hold": False},
}


def seed(c):
    """Give each Dev tickets type the Dev board's flow once, on the steps it has; a step that already has a flow
    keeps it."""
    if not _ready(c):
        return
    from . import roadmap_flow
    for ident, type_name in c.execute("SELECT id,name FROM task_types").fetchall():
        if not roadmap_flow._dev((ident, type_name)) or c.execute(
                "SELECT 1 FROM events WHERE action=? AND target=? LIMIT 1", (SEEDED, ident)).fetchone():
            continue
        apply(c, ident, DEV)
        H.event(c, H.KEEPER, SEEDED, ident, {"flow": sorted(DEV)})
    seed_checks(c)


def _inferred(s, merged_at=None):
    """The check kind a step's flow already implies, for a board flowed before steps had one: a gated step is a
    review, a check whose pass leads to the merge is QA, and a step with a check pool after the step merged tickets
    go to is the check after the release."""
    flow = of(s)
    if flow.get("gate"):
        return "review"
    if flow.get("on_pass") == MERGE:
        return "qa"
    if merged_at is not None and s["position"] > merged_at and s.get("check_pool") not in (None, "", "[]"):
        return "release_qa"
    return None


def seed_checks(c):
    """Give each type's check steps their kind once: the Dev board's steps by their seeded names, and any step whose
    flow already says what it checks. A step that has a kind keeps it."""
    if not _ready(c):
        return
    for (ident,) in c.execute("SELECT id FROM task_types").fetchall():
        if c.execute("SELECT 1 FROM events WHERE action=? AND target=? LIMIT 1", (CHECKS_SEEDED, ident)).fetchone():
            continue
        if not has_flow(c, ident):
            continue        # a board Tico does not move keeps no flow; its checks are known by their titles
        given = {}
        merged = merge_step(c, ident)
        for s in steps(c, ident):
            flow = of(s)
            if "check" in flow:
                continue
            kind = (DEV.get(s["name"]) or {}).get("check") or _inferred(s, merged["position"] if merged else None)
            if not kind:
                continue
            c.execute("UPDATE task_steps SET flow=? WHERE id=?",
                      (json.dumps({**flow, "check": kind}, sort_keys=True), s["id"]))
            given[s["name"]] = kind
        H.event(c, H.KEEPER, CHECKS_SEEDED, ident, {"checks": given})


def apply(c, type_id, flows):
    """Set `flows` ({step name: flow}) on a type's steps that have none, resolving step names to ids."""
    rows = steps(c, type_id)
    by_name = {s["name"]: s for s in rows}
    for step_name, flow in flows.items():
        s = by_name.get(step_name)
        if not s or of(s):
            continue
        out = dict(flow)
        for key in STEP_KEYS:
            if out.get(key) and out[key] != MERGE:
                target = by_name.get(out[key])
                if not target:
                    out.pop(key)
                    continue
                out[key] = target["id"]
        c.execute("UPDATE task_steps SET flow=? WHERE id=?", (json.dumps(out, sort_keys=True), s["id"]))
