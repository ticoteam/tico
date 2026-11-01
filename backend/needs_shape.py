"""One item shape for everything in a person's Needs you (OpenAPI `NeedsYouItem`).

Each producer builds its item with what its card needs (a task's view, an ask, an approval row, a card's rows); `item`
adds the fields every item has, whatever its kind, so a client lists, orders and acts on them without a branch per kind:

- `id`, `kind`, `title`, `created_at` (when it began waiting on the person);
- `body`: its text, or null when the card has none;
- `subject`: `{type, id}`, the one thing it is about (a task, a message, an approval, a proposal, a KPI), or null for
  a card that stands for several things or a setting;
- `actions`: the writes that settle it, from `ACTIONS`.
"""

# Each action, and the write it is (docs/openapi/v2.json has them all).
ACTIONS = {
    "answer": "POST /api/v2/messages/{ask id}/answer: answer the question it carries",
    "update": "POST /api/v2/tasks/{task id}: finish, decline, move or comment on the task",
    "decide": "the subject's decision: POST /api/v2/approvals/{id}, /api/v2/proposals/{id}/decide, or "
              "/api/v2/stale-review/{row id} for each row; a tidy card, POST /api/v2/memory-tidy/cards/{id}/decide",
    "add_reading": "POST /api/v2/kpis/{kpi id}/readings",
    "open": "nothing to send from the card: it opens its page (`link`, the subject, or the Goals page)",
}
TASK_KINDS = ("task", "question", "waiting", "declined")
# The Goals page's own asks (backend/goal_asks.py): what each is about, and what settles it.
GOAL_SUBJECTS = {"vision": "strategy", "kpi_red": "kpi", "kpi_stale": "kpi", "recovery": "proposal",
                 "proposal": "proposal", "why": None}


def item(x, subject, actions, body=None):
    """`x` with the common fields set; its own fields stay as its card reads them."""
    assert all(a in ACTIONS for a in actions), actions
    return {**x, "id": x["id"], "kind": x["kind"], "title": x.get("title") or "", "body": body,
            "subject": subject, "created_at": x.get("created_at"), "actions": list(actions)}


def needs_item(x, reader):
    """An item of `GET /api/v2/needs-you` (views.needs_items) in the common shape."""
    kind = x["kind"]
    if kind in TASK_KINDS:
        ask = x.get("ask") or {}
        answer = ("answer",) if ask.get("to_actor") == reader else ()
        return item(x, {"type": "task", "id": x["id"]}, answer + ("update",), x.get("body"))
    if kind == "ask":
        return item(x, {"type": "message", "id": x["message_id"]}, ("answer",), x.get("body"))
    if kind == "approval":
        return item(x, {"type": "approval", "id": x["id"]}, ("decide",))
    if kind == "stale":
        return item(x, None, ("decide",))
    if kind == "memory_tidy":
        return item(x, None, ("decide",), x.get("diary"))
    return item(x, None, ("open",), x.get("first_line") or None)


def goal_item(x, title, body, created):
    """An item of `GET /api/v2/goals/needs-you` (backend/goal_asks.py for_person) in the common shape."""
    kind, ref = x["kind"], x["ref"]
    kind_of = GOAL_SUBJECTS[kind]
    subject = {"type": kind_of, "id": ref} if kind_of else None
    actions = {"recovery": ("decide",), "proposal": ("decide",), "kpi_stale": ("add_reading",)}.get(kind, ("open",))
    # `ref` is the asks table's key; `id` and `subject` carry it.
    shown = {k: v for k, v in x.items() if k != "ref"}
    return item({**shown, "id": f"{kind}:{ref}", "title": title, "created_at": created}, subject, actions, body)
