"""Contacts.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from clients.commands._registry import tool
from clients.commands._schemas import _key, _s


# ----------------------------------------------------------------------------- contacts
# Contacts (backend/entities/routes.py): `tico contacts`. The one read rule decides what each reader sees; a tool here
# only names what to ask. Reads are offered to every kind of caller; writes to people and bots, not the Assistant.
CONTACTS_VALUE_TYPES = [
    "text",
    "number",
    "currency",
    "date",
    "timestamp",
    "checkbox",
    "select",
    "status",
    "email",
    "phone",
    "domain",
    "url",
    "personal_name",
    "rating",
    "actor",
]


CONTACTS_REF = _s("An entity: its id, an email, a domain, a name or an external id")


CONTACTS_MATCH = {
    "type": "object",
    "description": "How to find the one entity: one of id, email, domain or name (a name needs a type match)",
    "additionalProperties": {"type": "string"},
}


def _clean(fields):
    """A request body without the fields the caller did not give (the server takes its own defaults for them)."""
    return {k: v for k, v in fields.items() if v is not None}


@tool(
    "tico_contacts_self",
    "Who we are in Contacts: our company, the places we operate in, and the obligations that bind us.",
    {},
)
def contacts_self(api, args):
    return api.get("entities/self")


@tool(
    "tico_contacts_types",
    "The contact types (person, organization, place ...) and the ones added, each with its description and who added it.",
    {},
)
def contacts_types(api, args):
    return api.get("entities/types")


@tool(
    "tico_contacts_describe",
    "One entity type: its attributes with value type, owner and options, and the link types that may touch it.",
    {"type": _s("The type key, like organization")},
    required=("type",),
)
def contacts_describe(api, args):
    return api.get("entities/types/" + args["type"])


@tool(
    "tico_contacts_find",
    "Entities of one type that you can see, by name (a prefix), status or a link to us (customer_of). "
    "A page is 50; pass its next as after for the next page.",
    {
        "type": _s("The type key, like organization"),
        "name": _s("A name prefix"),
        "status": _s("A status key"),
        "link": _s("A link to us, like customer_of"),
        "after": _s("The next the last page gave"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    },
    required=("type",),
)
def contacts_find(api, args):
    return api.get(
        "entities/find/" + args["type"],
        name=args.get("name"),
        status=args.get("status"),
        link=args.get("link"),
        after=args.get("after"),
        limit=args.get("limit"),
    )


@tool(
    "tico_contacts_get",
    "One entity as you see it: its values with source and age, its links, external records and your notes. "
    "A reference that two entities match returns both, marked ambiguous.",
    {"ref": CONTACTS_REF},
    required=("ref",),
)
def contacts_get(api, args):
    return api.get("entities/card", ref=args["ref"])


@tool(
    "tico_contacts_rules",
    "The rules that apply to an entity, each with the path it comes by.",
    {
        "ref": CONTACTS_REF,
        "action": _s("What you would do, like email"),
        "as_bot": _s("The bot the rules are for"),
    },
    required=("ref",),
)
def contacts_rules(api, args):
    return api.get("entities/rules", ref=args["ref"], action=args.get("action"), as_bot=args.get("as_bot"))


@tool(
    "tico_contacts_upsert",
    "Create or update one entity of a type. The match names it; one that matches more than one entity is refused, never guessed. "
    "Values are set by attribute key, and a value that changes keeps its old one in history.",
    {
        "type": _s("The type key, like person"),
        "match": CONTACTS_MATCH,
        "values": {"type": "object", "description": "Attribute key to value"},
        "name": _s("The entity's name (required to create one unless the match gives it)"),
        "evidence_id": _s("A market evidence id that supports the values"),
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    required=("type", "match"),
    writes=True,
)
def contacts_upsert(api, args):
    body = _clean(
        {
            "type": args["type"],
            "match": args["match"],
            "values": args.get("values") or {},
            "name": args.get("name"),
            "evidence_id": args.get("evidence_id"),
            "confidence": args.get("confidence"),
        }
    )
    return api.post("entities", body, key=_key(args))


@tool(
    "tico_contacts_link",
    "A typed, dated link between two entities you can see. The link type says which ends it may join; a link already current is returned, not written twice.",
    {
        "from_ref": CONTACTS_REF,
        "link_key": _s("The link type, like works_at"),
        "to_ref": CONTACTS_REF,
        "valid_from_on": _s("YYYY-MM-DD the link starts"),
        "valid_until_on": _s("YYYY-MM-DD the link ends"),
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_id": _s("A market evidence id that supports the link"),
    },
    required=("from_ref", "link_key", "to_ref"),
    writes=True,
)
def contacts_link(api, args):
    body = _clean(
        {
            "from_ref": args["from_ref"],
            "link_key": args["link_key"],
            "to_ref": args["to_ref"],
            "valid_from_on": args.get("valid_from_on"),
            "valid_until_on": args.get("valid_until_on"),
            "confidence": args.get("confidence"),
            "evidence_id": args.get("evidence_id"),
        }
    )
    return api.post("entities/links", body, key=_key(args))


@tool(
    "tico_contacts_unlink",
    "End a link: it stops being current today. Its history stays.",
    {"link_id": _s("The link's id, from tico_contacts_get")},
    required=("link_id",),
    writes=True,
)
def contacts_unlink(api, args):
    return api.post("entities/unlink", {"link_id": args["link_id"]}, key=_key(args))


@tool(
    "tico_contacts_merge",
    "Merge a duplicate (drop) into the entity it is a copy of (keep): the dropped entity's rows move to the kept one, "
    "and tico_contacts_unmerge reverses it exactly.",
    {"keep": CONTACTS_REF, "drop": CONTACTS_REF},
    required=("keep", "drop"),
    writes=True,
)
def contacts_merge(api, args):
    return api.post("entities/merge", {"keep": args["keep"], "drop": args["drop"]}, key=_key(args))


@tool(
    "tico_contacts_unmerge",
    "Reverse the last merge of a dropped entity, named by its own id (a merged reference answers with the kept entity).",
    {"drop": _s("The dropped entity's id")},
    required=("drop",),
    writes=True,
)
def contacts_unmerge(api, args):
    return api.post("entities/unmerge", {"drop": args["drop"]}, key=_key(args))


@tool(
    "tico_contacts_history",
    "Past values of an entity's fields, newest first. Name an attribute for one field. A page is 50; pass its next as after.",
    {
        "ref": CONTACTS_REF,
        "attribute": _s("One attribute key, like title"),
        "after": _s("The next the last page gave"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    },
    required=("ref",),
)
def contacts_history(api, args):
    return api.get(
        "entities/history",
        ref=args["ref"],
        attribute=args.get("attribute"),
        after=args.get("after"),
        limit=args.get("limit"),
    )


@tool(
    "tico_contacts_note",
    "A note on an entity. audience me is yours alone; company is shown to everyone who can see the entity.",
    {
        "ref": CONTACTS_REF,
        "body": _s("The note"),
        "audience": {"type": "string", "enum": ["me", "company"]},
        "evidence_id": _s("A market evidence id the note cites"),
    },
    required=("ref", "body"),
    writes=True,
)
def contacts_note(api, args):
    body = _clean(
        {
            "ref": args["ref"],
            "body": args["body"],
            "audience": args.get("audience") or "company",
            "evidence_id": args.get("evidence_id"),
        }
    )
    return api.post("entities/notes", body, key=_key(args))


@tool(
    "tico_contacts_attribute_add",
    "Add an attribute to an entity type. Its description is required; a select or status needs its options; "
    "an owner of connection:<id> makes the field that connection's.",
    {
        "type": _s("The type key, like organization"),
        "key": _s("The attribute key, lowercase, like crm_stage"),
        "name": _s("The label people see"),
        "value_type": {"type": "string", "enum": CONTACTS_VALUE_TYPES},
        "description": _s("What the attribute holds"),
        "options": {
            "type": "array",
            "description": "For a select or status: each option's key and label",
            "items": {
                "type": "object",
                "properties": {"key": _s("The option key"), "label": _s("The option's label")},
                "required": ["key", "label"],
                "additionalProperties": False,
            },
        },
        "multiple": {"type": "boolean"},
        "unique_value": {"type": "boolean"},
        "required": {"type": "boolean"},
        "sensitive": {"type": "boolean", "description": "Only Finance's owner and the system read it"},
        "owner": _s("tico (the default) or connection:<id>"),
    },
    required=("type", "key", "name", "value_type", "description"),
    writes=True,
)
def contacts_attribute_add(api, args):
    body = _clean(
        {
            "key": args["key"],
            "name": args["name"],
            "description": args["description"],
            "value_type": args["value_type"],
            "options": args.get("options") or [],
            "multiple": args.get("multiple"),
            "unique_value": args.get("unique_value"),
            "required": args.get("required"),
            "sensitive": args.get("sensitive"),
            "owner": args.get("owner"),
        }
    )
    return api.post("entities/types/" + args["type"] + "/attributes", body, key=_key(args))


@tool(
    "tico_contacts_type_add",
    "Add an entity type, with its description. A near-duplicate of a type that exists is refused with that type.",
    {
        "key": _s("The type key, lowercase, like vendor"),
        "name": _s("The name people see"),
        "description": _s("What the type is for"),
        "plural_name": _s("Its plural, if not name plus s"),
        "icon": _s("A short icon name"),
    },
    required=("key", "name", "description"),
    writes=True,
)
def contacts_type_add(api, args):
    body = _clean(
        {
            "key": args["key"],
            "name": args["name"],
            "description": args["description"],
            "plural_name": args.get("plural_name"),
            "icon": args.get("icon"),
        }
    )
    return api.post("entities/types", body, key=_key(args))


@tool(
    "tico_contacts_link_type_add",
    "Add a link type, with its description. inherits_rules says a rule toward one end reaches the other (works_at does; "
    "a competitor does not). symmetric stores the link once, both ways.",
    {
        "key": _s("The link key, lowercase, like advises"),
        "name": _s("The name from the near end"),
        "inverse_name": _s("The name from the far end"),
        "description": _s("What the link means"),
        "from_type": _s("The type the link starts at, if it must"),
        "to_type": _s("The type the link ends at, if it must"),
        "inherits_rules": {"type": "boolean"},
        "symmetric": {"type": "boolean"},
    },
    required=("key", "name", "inverse_name", "description"),
    writes=True,
)
def contacts_link_type_add(api, args):
    body = _clean(
        {
            "key": args["key"],
            "name": args["name"],
            "inverse_name": args["inverse_name"],
            "description": args["description"],
            "from_type": args.get("from_type"),
            "to_type": args.get("to_type"),
            "inherits_rules": args.get("inherits_rules"),
            "symmetric": args.get("symmetric"),
        }
    )
    return api.post("entities/link-types", body, key=_key(args))
