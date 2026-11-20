"""Risks and matters (backend/risk.py).

A risk is a tag of kind risk (`risk:{slug}`); a matter is a task carrying one, with its facts. Tagging a task stays
`tico task label {task} --add risk:{slug}`. clients/commands/__init__.py says how the table is put together."""

from urllib.parse import quote

from clients.tico import APIError

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._requester import _as_person
from clients.commands._schemas import TASK_ID, _key, _s

MATTER_KINDS = ["dispute", "request", "notice", "incident"]
RISK = _s("The risk: its key (risk:customer-theft-claims), slug or id")
PAGE = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    "offset": {"type": "integer", "minimum": 0, "description": "next_offset from the last page"},
}
# The facts `unset` may clear, and the field each is sent as.
FACTS = {
    "party": "party_entity_id",
    "amount": "amount",
    "currency": "currency",
    "claim": "claim",
    "side": "side",
    "forum": "forum",
    "place": "place_id",
    "product": "product_id",
    "not_before": "starts_at",
    "due_by": "due_at",
    "dates_label": "dates_label",
}


def _path(value):
    return quote(str(value), safe=":")


def _present(values):
    return {k: v for k, v in values.items() if v is not None}


@tool(
    "tico_risk_list",
    "Every risk, live first: level, open and closed matters, amount claimed, next date (from tasks you can read).",
    {"task": _s("Only the risks this task carries")},
)
def risk_list(api, args):
    return _as_person(api).get("risks", **_present({"task": args.get("task")}))


@tool(
    "tico_risk_show",
    "One risk: its page text, the matters you can read with their facts, its rules and documents.",
    {"risk": RISK, **PAGE},
    required=("risk",),
)
def risk_show(api, args):
    return _as_person(api).get(
        f"risks/{_path(args['risk'])}", **_present({"limit": args.get("limit"), "offset": args.get("offset")})
    )


@tool(
    "tico_risk_matters",
    "Matters you can read (tasks carrying a risk) with their facts, newest first. kind=dispute open=true: Disputes.",
    {
        "kind": _s("Only this kind", enum=MATTER_KINDS),
        "open": {"type": "boolean", "description": "true: open matters only; false: closed ones only"},
        "risk": RISK,
        "product": _s("Only the matters about this product: its key or id (tico products list)"),
        **PAGE,
    },
)
def risk_matters(api, args):
    query = _present({k: args.get(k) for k in ("kind", "risk", "product", "limit", "offset")})
    if args.get("open") is not None:
        query["open"] = "true" if args["open"] else "false"
    return _as_person(api).get("matters", **query)


@tool(
    "tico_risk_obligations",
    "The dates of every risk (or one) you can see: unconfirmed first, then due within 14 days, later, and settled or "
    "passed in the last 30 days. Each says its basis, state and proof. Mark one with tico_rules_due_satisfied.",
    {
        "risk": RISK,
        "state": _s("Only this state", enum=["due", "met", "waived", "missed", "passed", "unconfirmed", "watched"]),
        "group": _s("Only this group", enum=["unconfirmed", "next", "later", "past"]),
        "days": {"type": "integer", "minimum": 1, "maximum": 366, "description": "How far ahead (default 90)"},
        **PAGE,
    },
)
def risk_obligations(api, args):
    query = _present({k: args.get(k) for k in ("risk", "state", "group", "days", "limit", "offset")})
    return _as_person(api).get("risk/obligations", **query)


@tool(
    "tico_risk_add",
    "Add a risk (a tag risk:{slug}). The Risk page's owner bot may then tag any task it can read with it.",
    {
        "label": _s("Its name, such as Customer theft claims"),
        "key": _s("The slug; made from the label when left out"),
        "markdown": _s("Its page text"),
        "owner": _s("Who owns it; default the Risk page's owner bot"),
    },
    required=("label",),
    writes=True,
)
def risk_add(api, args):
    body = _present({k: args.get(k) for k in ("label", "key", "markdown", "owner")})
    return api.post("risks", body, key=_key(args))


@tool(
    "tico_risk_update",
    "Change a risk's label, page text or owner, with the version you read.",
    {
        "risk": RISK,
        "version": {"type": "integer", "minimum": 1, "description": "Its version, from `risk show`"},
        "label": _s("New name"),
        "markdown": _s("New page text"),
        "owner": _s("New owner"),
    },
    required=("risk", "version"),
    writes=True,
)
def risk_update(api, args):
    body = _present({k: args.get(k) for k in ("version", "label", "markdown", "owner")})
    return api.post(f"risks/{_path(args['risk'])}", body, key=_key(args))


# The fields a key date takes, by the flag each is given as.
DATE_FIELDS = {"not_before": "starts_at", "due_by": "due_at", "dates_label": "label"}
DATE_OPS = ("add_date", "date_id", "remove_date")


@tool(
    "tico_risk_matter",
    "Set a task's matter facts (it must carry a risk); only the facts given change. Amount is in cents. A matter has "
    "key dates (a hearing, discovery due), each with a label, not_before (the earliest its step may be done, never a "
    "deadline) and due_by (the deadline); open ones show on the calendar. add_date adds one; date_id changes one "
    "(not_before, due_by, dates_label as its label, done); remove_date removes one. Without those, not_before, due_by "
    "and dates_label edit its first date.",
    {
        "task": TASK_ID,
        "kind": _s("What it is", enum=MATTER_KINDS),
        "party": _s("The other party: a person or organization id from Contacts"),
        "amount": {"type": "integer", "minimum": 0, "description": "Amount claimed, in cents"},
        "currency": _s("ISO code, such as USD"),
        "claim": _s("What is claimed, in a line"),
        "side": _s("Whose claim: theirs (default) or ours", enum=["theirs", "ours"]),
        "forum": _s("Where it is: AAA, small claims, a demand letter"),
        "place": _s("A place id from Contacts"),
        "product": _s("The product it is about: its key or id (tico products list)"),
        "not_before": _s(
            "The earliest it may be done, never a deadline: a day (2026-11-19) or a time (2026-11-19T10:00, the "
            "company's time zone unless it has an offset)"
        ),
        "due_by": _s("The deadline: a day (2026-11-26) or a time, as not_before"),
        "dates_label": _s("What the date is for, such as Produce records"),
        "add_date": _s("Add a key date with this label, such as Hearing; give not_before or due_by"),
        "date_id": _s("The key date to change (its id, from the matter's dates)"),
        "done": {
            "type": "boolean",
            "description": "With add_date or date_id: true marks the date done, false reopens it",
        },
        "remove_date": _s("Remove this key date (its id)"),
        "unset": {"type": "array", "items": {"type": "string", "enum": list(FACTS)}, "description": "Facts to clear"},
        "clear": {"type": "boolean", "description": "Remove all its facts"},
        "version": {"type": "integer", "minimum": 1, "description": "The matter's version as you read it"},
    },
    required=("task",),
    writes=True,
)
def risk_matter(api, args):
    task = quote(str(args["task"]), safe="")
    ops = [k for k in DATE_OPS if args.get(k) is not None]
    if len(ops) > 1:
        raise APIError("validation", "Give one of add_date, date_id or remove_date")
    unset = list(args.get("unset") or ())
    on_date = set(DATE_FIELDS) if ops else set()
    body = _present({"kind": args.get("kind"), "version": None if ops else args.get("version")})
    body.update(_present({FACTS[k]: args.get(k) for k in FACTS if k not in on_date}))
    if body.get("product_id") and "/" not in body["product_id"]:
        body["product_id"] = "product/" + body["product_id"]  # a product's key, as `tico products list` shows it
    for name in unset:
        if name not in on_date:
            body[FACTS[name]] = None
    if args.get("clear"):
        body["clear"] = True
    if not ops:
        return api.post(f"tasks/{task}/matter", body, key=_key(args))
    if body:
        if args.get("version") is not None:
            body["version"] = args["version"]
        api.post(f"tasks/{task}/matter", body, key=_key(args))
    date = _present({DATE_FIELDS[k]: args.get(k) for k in DATE_FIELDS})
    for name in unset:
        if name in on_date:
            date[DATE_FIELDS[name]] = None
    if args.get("done") is not None:
        date["done"] = args["done"]
    if not body and args.get("version") is not None:
        date["version"] = args["version"]
    if args.get("remove_date") is not None:
        return api.call(
            "DELETE", f"tasks/{task}/matter/dates/{quote(str(args['remove_date']), safe='')}", None, _key(args, ":date")
        )
    if args.get("add_date") is not None:
        date["label"] = args["add_date"]
        return api.post(f"tasks/{task}/matter/dates", date, key=_key(args, ":date"))
    return api.post(f"tasks/{task}/matter/dates/{quote(str(args['date_id']), safe='')}", date, key=_key(args, ":date"))


LINK = {
    "risk": RISK,
    "rule": _s("An org rule's id"),
    "document": _s("A document's id from Contacts"),
    "role": _s("A document's role: source (default) or covers (a policy)", enum=["source", "covers"]),
}


def _link_body(args):
    if bool(args.get("rule")) == bool(args.get("document")):
        raise APIError("validation", "Give one of rule or document")
    if args.get("rule"):
        return {"linkable_type": "org_rule", "linkable_id": args["rule"], "role": "rule"}
    return _present({"linkable_type": "entity", "linkable_id": args["document"], "role": args.get("role")})


@tool("tico_risk_link", "Link a rule or a document to a risk.", LINK, required=("risk",), writes=True)
def risk_link(api, args):
    return api.post(f"risks/{_path(args['risk'])}/links", _link_body(args), key=_key(args))


@tool("tico_risk_unlink", "Remove a rule or document from a risk.", LINK, required=("risk",), writes=True)
def risk_unlink(api, args):
    return api.post(f"risks/{_path(args['risk'])}/unlink", _link_body(args), key=_key(args))


# ----------------------------------------------------------------------------- `tico risk` (clients/cli_spec.py)
CLI = (
    Group(
        "risk",
        "risks and their matters: tag a task with `tico task label {task} --add risk:{slug}`",
        aliases=("risks",),
    ),
    Command("risk list", "every risk, live first, with its counts and amount claimed", example="tico risk list"),
    Command("risk add", "add a risk", example="tico risk add 'Customer theft claims'"),
    Command(
        "risk show", "one risk: its matters, rules and documents", example="tico risk show risk:customer-theft-claims"
    ),
    Command(
        "risk update",
        "change a risk's label, page text or owner",
        args=(Arg("version", positional=False),),
        example="tico risk update risk:acme-claims --version 1 --label 'Acme claims'",
    ),
    Command(
        "risk matter",
        "set a tagged task's matter facts and key dates",
        args=(
            Arg("done", "--done", action="store_const", const=True, default=None),
            Arg("done", "--not-done", action="store_const", const=False, default=None),
        ),
        exclusive=(("add_date", "date_id", "remove_date"),),
        example="tico risk matter D18450 --add-date Hearing --due-by 2026-12-15T09:00",
    ),
    Command(
        "risk matters",
        "matters you can read, newest first",
        args=(Arg("open", default=None),),  # left out: open and closed alike
        example="tico risk matters --product lightshow-kit --open",
    ),
    Command(
        "risk obligations",
        "the dates of every risk: unconfirmed, next 14 days, later, past 30 days",
        example="tico risk obligations --group unconfirmed",
    ),
    Command(
        "risk link",
        "link a rule or a document to a risk",
        exclusive=(("rule", "document"),),
        example="tico risk link risk:customer-theft-claims --document ent_policy",
    ),
    Command(
        "risk unlink",
        "remove a rule or a document from a risk",
        exclusive=(("rule", "document"),),
        example="tico risk unlink risk:customer-theft-claims --rule rule_123",
    ),
)
