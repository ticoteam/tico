"""Helpers that tools of several modules call.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from clients.commands._schemas import _key, _s
from clients.commands._requester import _for_person


VISUALS = {
    "type": "array",
    "maxItems": 10,
    "description": (
        "Visuals to show with this message, in order: a picture, SVG, self-contained HTML page, video or audio. Each is "
        "{path, title} for a file in your checkout (on your Computer: a page's local images and fonts are embedded first) "
        "or {file: \"<file id>@<version>\", title} for a file already on this task or conversation. Remote images in a page "
        "are not loaded; the result lists them. Too big or another type: it shows as a normal file."
    ),
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": _s("A file in your checkout"),
            "file": _s("<file id>@<version> already on this task or conversation"),
            "title": {"type": "string", "maxLength": 200, "description": "The caption people see"},
        },
    },
}


def _visuals(api, items, upload):
    """(attachments for the API, what stayed unembedded locally) from `visuals` items. `upload(index, name, data)` puts
    one local file's bytes where the message goes and returns "<file id>@<version>". The bytes go as they are (a raw
    body or multipart), never base64 in JSON, which grows them by a third past the path's request limit. `index` is
    the item's place in the list: it names that upload in the operation's idempotency key, so two files sharing a name
    are two uploads, and a retry of the same send repeats each one's own key."""
    out, local = [], []
    for index, item in enumerate(items or []):
        title = str(item.get("title") or "")
        if item.get("file"):
            out.append({"ref": item["file"], "title": title})
            continue
        if not item.get("path"):
            raise ValueError("A visual names a path or a file")
        if not hasattr(api, "post_multipart"):
            raise ValueError("Attach local files with the tico CLI on your Computer, or name a file already in Tico")
        from clients import bot_files as BF, visual_embed

        root = BF.checkout_root()
        path, name = BF.local_file(root, item["path"])
        if visual_embed.is_page(name):
            data, _, unembedded = visual_embed.embed(root, item["path"])
            local.append({"path": item["path"], "unembedded": unembedded})
        else:
            data = path.read_bytes()
        out.append({"ref": upload(str(index), path.name, data), "title": title})
    return out, local


def _uploaded(made):
    file = made.get("file") or {}
    if made.get("file_id"):
        return f"{made['file_id']}@{made['version']}"
    return f"{file['id']}@{file.get('version') or file.get('current_version')}"


# The Assistant, citing the person's message for something they asked for (`_asked`).
ASKED = _s(
    "Assistant only: the person asked for this in their Assistant chat: `turn` (the message that started "
    "this run) or its id. Omit unless they clearly asked; propose instead"
)


# The task engine's commands (docs/pipelines-v3.md S16, docs/dev/pipelines-v3-cutover.md "Bot commands"). Every change
# carries the version the caller was shown: tico_task_show, a list row and every wake print it. The tools are stateless:
# none reads the task to find a version just before it writes, because a version read then would check nothing. The
# one exception is a label change (tico_task_label): it only adds or takes off labels, so it reads the labels it changes
# and, given no version, sends the one it read; a change made between that read and the write is refused, never lost.
VERSION = {
    "type": "integer",
    "minimum": 0,
    "description": "The task's version you were last shown (tico_task_show, a list row or your wake: \"version 12\"). "
    "Changed since: refused as task_changed with the version it has now; read the task again and decide again",
}


def _seen(args):
    """The version the caller was shown, as it was given. Without one the caller is told where to find it."""
    version = args.get("version")
    if version in (None, ""):
        raise ValueError(version_needed(args.get("id")))
    return version


def version_needed(task_id=None):
    """The refusal of a change sent without a version: what to add, and where the number is printed."""
    return (
        f"Add --version N: the version you were last shown (`tico task show {task_id or '<id>'}` prints it, as do a "
        "list row and your wake)."
    )


def audience(value):
    """`everyone`, or `ben,group:legal,bot:analyst` (a person id, `group:<id>`, `bot:<slug>`), as an access level."""
    text = str(value or "").strip()
    if text.lower() == "everyone":
        return {"everyone": True}
    level = {"people": [], "teams": [], "bots": []}
    for part in filter(None, (p.strip() for p in text.split(","))):
        kind, _, name = part.partition(":")
        if kind in ("group", "team") and name:
            level["teams"].append(name)
        elif kind == "bot" and name:
            level["bots"].append(name)
        else:
            level["people"].append(name if kind == "human" and name else part)
    return level


def _teammate_access(api, path, args):
    """Show, or set, a bot's, person's or group's See, Read and Write: levels left out stay as they are."""
    on_behalf = _for_person(api)
    current = api.get(f"{path}/access", **on_behalf)
    wanted = {level: args[level] for level in ("see", "read", "write") if args.get(level)}
    if not wanted:
        return current
    body = {
        level: audience(wanted[level])
        if level in wanted
        else {k: current[level][k] for k in ("everyone", "people", "teams", "bots")}
        for level in ("see", "read", "write")
    }
    return api.post(f"{path}/access", {**body, "revision": current["revision"], **on_behalf}, key=_key(args))


BUDGET_VALUE = {
    "type": ["integer", "string"],
    "minimum": 0,
    "maximum": 100000,
    "description": "Runs allowed; 0 is no limit, \"default\" follows the team",
}


def _model_id(catalog, requested):
    """The id of the one current catalog model that `requested` names by id or label (case-blind, an exact match
    first). Raises ValueError when none, or more than one, match."""
    wanted = str(requested).strip().lower()
    choices = [m for m in catalog.get("models", []) if not m.get("deprecated")]
    hit = [m for m in choices if wanted in (str(m.get("id")).lower(), str(m.get("label")).lower())] or [
        m for m in choices if wanted in str(m.get("id")).lower() or wanted in str(m.get("label")).lower()
    ]
    if len(hit) != 1:
        raise ValueError(
            ("No model matches " if not hit else "More than one model matches ")
            + str(requested)
            + ". Models: "
            + ", ".join(str(m.get("id")) for m in choices)
        )
    return hit[0]["id"]


_CHANNEL = _s("The channel: #customer_success, customer_success or its id (C0123456789)")
