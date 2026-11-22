"""Contacts.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from clients import cli_spec
from clients.cli_spec import Arg, Command, Group
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
        "owner": _s("Only these owners' entities: me, human:<id> or bot:<id>"),
        "sort": {
            "type": "string",
            "enum": ["name", "next"],
            "description": "name (default), or next: soonest next step due first, each with its relationship status",
        },
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
        owner=args.get("owner"),
        sort=args.get("sort"),
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
    "tico_contacts_status",
    "Set a relationship's status, next step, its due date and flags together (an organization or a person). Each is "
    "replaced; one left out is cleared. Send the status_version you read (tico_contacts_get): when someone wrote it "
    "since, nothing changes and the answer is 409 status_changed; read it again. Only facts every reader may see.",
    {
        "ref": CONTACTS_REF,
        "version": {"type": "integer", "minimum": 0, "description": "The status_version you read"},
        "status": _s("Where it stands, in a word or two: lead, active, at risk"),
        "next_step": _s("The one next thing to do"),
        "next_step_due": _s("YYYY-MM-DD the next step is due"),
        "flags": {"type": "array", "items": {"type": "string"}, "description": "Short markers: vip, billing issue"},
    },
    required=("ref", "version"),
    writes=True,
)
def contacts_status(api, args):
    body = _clean(
        {
            "ref": args["ref"],
            "version": args["version"],
            "status": args.get("status"),
            "next_step": args.get("next_step"),
            "next_step_due": args.get("next_step_due"),
            "flags": args.get("flags"),
        }
    )
    return api.post("entities/status", body, key=_key(args))


@tool(
    "tico_contacts_owner",
    "Name the teammate who owns an entity (a relationship's owner): me, human:<id> or bot:<id>; an empty one clears it.",
    {"ref": CONTACTS_REF, "owner": _s("me, human:<id>, bot:<id>, or empty for none")},
    required=("ref", "owner"),
    writes=True,
)
def contacts_owner(api, args):
    return api.post("entities/owner", {"ref": args["ref"], "owner": args["owner"]}, key=_key(args))


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
    "tico_contacts_record",
    "Record that an entity is a record in another system (a CRM account, a Jira customer): system, record type and id, "
    "with the connection (tool id) it came through. Saving the same record again updates it, never a second copy; one "
    "already on another entity, or erased at its owner's request, is refused. status gone takes it off the card. "
    "The answer is the record's id and its entity's.",
    {
        "ref": CONTACTS_REF,
        "system": _s("The other system, like jira or hubspot"),
        "external_type": _s("The record's type there, like customer or company"),
        "external_id": _s("The record's id there"),
        "connection_id": _s("The connection (tool id) the record came through; empty for none"),
        "url": _s("A link to the record"),
        "revision": _s("The record's revision or updated stamp there"),
        "status": {"type": "string", "enum": ["active", "gone"]},
    },
    required=("ref", "system", "external_type", "external_id"),
    writes=True,
)
def contacts_record(api, args):
    body = _clean(
        {
            "ref": args["ref"],
            "system": args["system"],
            "external_type": args["external_type"],
            "external_id": args["external_id"],
            "connection_id": args.get("connection_id"),
            "url": args.get("url"),
            "revision": args.get("revision"),
            "status": args.get("status"),
        }
    )
    return api.post("entities/external-refs", body, key=_key(args))


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


def _upsert_fields(fields):
    """The tool takes objects: a KEY=VALUE match is the match object, and the --set pairs the values object."""
    if "match" in fields:
        fields["match"] = {fields["match"][0]: fields["match"][1]}
    if "values" in fields:
        fields["values"] = dict(fields["values"])
    return fields


# `tico contacts ...` (clients/cli_spec.py): `--text` is the command line's own, a short answer instead of JSON.
# `contacts attribute add` stays hand-built (clients/hubcli.py): its --value-type takes any text, where the tool's schema
# lists the types, so a spec would refuse what the server takes.
_ID = "id|email|domain|name"
CONTACTS = (
    Group("contacts", "contacts: people, organizations and places, their links, values and notes, as you see them"),
    Command(
        "contacts self",
        "who we are: our company, where we operate, the obligations that bind us",
        args=(Arg("as_text", flag="--text", action="store_true", local=True, help="a short summary, not JSON"),),
        example="tico contacts self --text",
    ),
    Command(
        "contacts types",
        "the entity types and their descriptions, and who added each",
        example="tico contacts types",
    ),
    Command(
        "contacts describe",
        "one type: its attributes (owners, options) and the link types that touch it",
        example="tico contacts describe company",
    ),
    Command(
        "contacts find",
        "entities of one type you can see, by name, status or a link to us",
        args=(Arg("as_text", flag="--text", action="store_true", local=True, help="one line per entity, not JSON"),),
        example="tico contacts find organization --owner me --sort next",
    ),
    Command(
        "contacts get",
        "one entity as you see it: values with source, links, records and your notes",
        args=(
            Arg("ref", metavar=_ID),
            Arg("as_text", flag="--text", action="store_true", local=True, help="a short card, not JSON"),
        ),
        example="tico contacts get ana@example.com --text",
    ),
    Command(
        "contacts rules",
        "the rules that apply to an entity (none until rules arrive)",
        args=(Arg("ref", metavar=_ID), Arg("as_bot", flag="--as", metavar="BOT")),
        example="tico contacts rules acme.com --as ana-bot --action email",
    ),
    Command(
        "contacts upsert",
        "create or update one entity: --match names it, --set sets its values",
        args=(
            Arg("match", flag="--match", type=cli_spec.pair, required=True, metavar="KEY=VALUE"),
            Arg("values", flag="--set", type=cli_spec.pair, action="append", metavar="KEY=VALUE"),
            Arg("evidence_id", flag="--evidence", metavar="EVIDENCE_ID"),
        ),
        convert=_upsert_fields,
        example="tico contacts upsert company --match domain=acme.com --set status=customer --name Acme",
    ),
    Command(
        "contacts link",
        "a typed, dated link between two entities: a works_at b",
        args=(
            Arg("from_ref", metavar="a"),
            Arg("link_key", metavar="link"),
            Arg("to_ref", metavar="b"),
            Arg("valid_from_on", flag="--from", metavar="DATE"),
            Arg("valid_until_on", flag="--until", metavar="DATE"),
            Arg("evidence_id", flag="--evidence", metavar="EVIDENCE_ID"),
        ),
        example="tico contacts link ana works_at acme --from 2026-01-01",
    ),
    Command(
        "contacts unlink",
        "end a link: it stops being current today; its history stays",
        example="tico contacts unlink l1",
    ),
    Command(
        "contacts merge",
        "merge a duplicate into the entity it copies; unmerge reverses it",
        example="tico contacts merge acme dup-acme",
    ),
    Command(
        "contacts unmerge",
        "reverse the last merge of a dropped entity, named by its own id",
        example="tico contacts unmerge dup-acme",
    ),
    Command(
        "contacts status",
        "set a relationship's status, next step, due date and flags together, with the version you read",
        args=(
            Arg("ref", metavar=_ID),
            Arg("version", metavar="VERSION", type=int),
            Arg("next_step_due", flag="--due", metavar="DATE"),
            Arg("flags", flag="--flag", action="append", metavar="FLAG"),
        ),
        example="tico contacts status acme.com 3 --status active --next-step 'Send the renewal' --due 2026-12-01",
    ),
    Command(
        "contacts owner",
        "name the teammate who owns an entity: me, human:<id> or bot:<id>; '' clears it",
        args=(Arg("ref", metavar=_ID),),
        example="tico contacts owner acme.com me",
    ),
    Command(
        "contacts history",
        "past values of an entity's fields, newest first",
        args=(Arg("ref", metavar=_ID),),
        example="tico contacts history acme.com --attribute status",
    ),
    Command(
        "contacts note",
        "a note on an entity; --audience me keeps it to you",
        args=(Arg("ref", metavar=_ID), Arg("evidence_id", flag="--evidence", metavar="EVIDENCE_ID")),
        example="tico contacts note acme.com 'Renewal in March' --audience me",
    ),
    Command(
        "contacts record",
        "record that an entity is a record in another system, through a connection",
        args=(
            Arg("ref", metavar=_ID),
            Arg("connection_id", flag="--connection", metavar="TOOL_ID"),
        ),
        example="tico contacts record acme.com jira customer 10042 --connection tool_jira",
    ),
    Group("contacts type", "add an entity type"),
    Command(
        "contacts type add",
        "add an entity type, with its description",
        args=(
            Arg("name", flag="--name"),
            Arg("description", flag="--description"),
            Arg("plural_name", flag="--plural"),
        ),
        example="tico contacts type add vendor --name Vendor --description 'Who we buy from'",
    ),
    Group("contacts link-type", "add a link type"),
    Command(
        "contacts link-type add",
        "add a link type: its name both ways, its description, the ends it may join",
        args=(
            Arg("name", flag="--name"),
            Arg("description", flag="--description"),
            Arg("inverse_name", flag="--inverse"),
            Arg("from_type", flag="--from"),
            Arg("to_type", flag="--to"),
        ),
        example="tico contacts link-type add works_at --name 'works at' --inverse employs --description 'Employment'",
    ),
)
