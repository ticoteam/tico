"""One item shape for everything in a person's Needs you (OpenAPI `NeedsYouItem`).

Each producer builds its item with what it needs (a task, an ask on a task, a Paused cause, an @, a goal's ask); `item`
adds the fields every item has, whatever its kind, so a client lists, orders and acts on them without a branch per kind:

- `id`, `kind`, `title`, `created_at` (when it began waiting on the person);
- `body`: its text, or null when the card has none;
- `subject`: `{type, id}`, the one thing it is about (a task, a proposal, a KPI), or null for an item that stands for
  several things or a setting;
- `actions`: the writes that settle it, from `ACTIONS`.
"""

# Each action, and the write it is (the v2 spec, backend/openapi_v2.py, has them all).
ACTIONS = {
    "update": "work the task: POST /api/v2/tasks/{task id}/done, /move or /try-again, or comment on it",
    "answer": "POST /api/v2/tasks/{task id}/asks/{requirement id}/answer (or /give to hand it to someone else)",
    "decide": "the proposal's decision: POST /api/v2/proposals/{id}/decide",
    "add_reading": "POST /api/v2/kpis/{kpi id}/readings",
    "open": "nothing to send from the card: it opens its page (`link`, the subject, or the Goals page)",
}
TASK_KINDS = ("task", "paused")  # a task the person owns, or a Paused cause they can fix
# The Goals page's own asks (backend/goal_asks.py): what each is about, and what settles it.
GOAL_SUBJECTS = {
    "vision": "strategy",
    "kpi_red": "kpi",
    "kpi_stale": None,  # one ask for all of a person's out-of-date measures: it stands for several
    "recovery": "proposal",
    "proposal": "proposal",
    "why": None,
}


def item(x, subject, actions, body=None):
    """`x` with the common fields set; its own fields stay as its card reads them."""
    assert all(a in ACTIONS for a in actions), actions
    return {
        **x,
        "id": x["id"],
        "kind": x["kind"],
        "title": x.get("title") or "",
        "body": body,
        "subject": subject,
        "created_at": x.get("created_at"),
        "actions": list(actions),
    }


def needs_item(x, reader):
    """An item of `GET /api/v2/needs-you` (views.needs_items) in the common shape."""
    kind = x["kind"]
    task_id = x.get("task_id") or x["id"]
    if kind in TASK_KINDS:
        return item(x, {"type": "task", "id": task_id}, ("update",), x.get("body"))
    if kind == "ask":
        return item(x, {"type": "task", "id": task_id}, ("answer",), x.get("body"))
    return item(x, None, ("open",), x.get("first_line") or None)  # an @ of them: it opens where it was said


def goal_item(x, title, body, created):
    """An item of `GET /api/v2/goals/needs-you` (backend/goal_asks.py for_person) in the common shape."""
    kind, ref = x["kind"], x["ref"]
    kind_of = GOAL_SUBJECTS[kind]
    subject = {"type": kind_of, "id": ref} if kind_of else None
    actions = {"recovery": ("decide",), "proposal": ("decide",)}.get(kind, ("open",))
    # `ref` is the asks table's key; `id` and `subject` carry it.
    shown = {k: v for k, v in x.items() if k != "ref"}
    return item({**shown, "id": f"{kind}:{ref}", "title": title, "created_at": created}, subject, actions, body)
