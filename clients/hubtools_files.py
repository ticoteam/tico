"""Company files: the `hub file` list, publish, link, touch and import tools."""

from clients.hubtools_core import (
    _key,
    _s,
    tool,
)


# ----------------------------------------------------------------------------- files
# What a bot publishes so people find it on the bot's page (docs/files.md). The bot is the caller:
# no tool takes a bot argument for a write, and only a bot's own files can be written.
def _files_fields(args, *names):
    return {n: args[n] for n in names if args.get(n)}


@tool(
    "hub_file_list",
    "The files on your page (or another bot's, if you may see it), newest activity first: "
    "id, title, kind, where each opens and when it last changed.",
    {
        "bot": _s("Bot slug; defaults to you"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "cursor": _s("next_cursor from the previous page"),
    },
)
def files_list(api, args):
    slug = args.get("bot") or str(api.get("me")["actor"]).split(":", 1)[-1]
    return api.get(f"bots/{slug}/files", limit=args.get("limit"), cursor=args.get("cursor"))


@tool(
    "hub_file_publish",
    "Publish a file you created or changed so people can open it from your page: "
    "a report, a draft, a spreadsheet. Send its text, or content_base64 for a binary file (up to about 1.4 MB "
    "here; `hub file publish <path>` sends up to 25 MB, and video or audio up to the server limit). "
    "Publishing the same name again adds a version. "
    "Documents, images, csv, json, md, html, pdf and office files only; never credentials.",
    {
        "name": _s("File name with its extension, e.g. 2026-09-15-pipeline-review.md"),
        "text": _s("The file's text"),
        "content_base64": _s("The bytes, base64-encoded, for anything not text"),
        "title": _s("What people see; defaults to the name"),
        "task": _s("The task this is for; defaults to the task you are working on"),
        "scope": {
            "enum": ["task", "bot"],
            "description": "bot makes it visible to everyone who sees this bot (refused from inside a private chat)",
        },
        "path": _s("The file's path in your repository, so an edit later is the same file"),
    },
    required=("name",),
    writes=True,
)
def files_publish(api, args):
    body = _files_fields(args, "name", "text", "content_base64", "title", "task", "scope", "path")
    return api.post("files/uploads", body, key=_key(args))


@tool(
    "hub_file_link",
    "List a document you created or edited in another tool (a Google Doc, Sheet or "
    "Slides, a Notion page, a Figma file, any https link) on your page. Tico keeps the address, never the "
    "document; whoever opens it needs access there. Adding it again, or `hub_file_touch`, moves it to the top.",
    {
        "url": _s("An https:// link"),
        "title": _s("What people see"),
        "task": _s("The task it is for"),
        "scope": {"enum": ["task", "bot"]},
    },
    required=("url",),
    writes=True,
)
def files_add_link(api, args):
    return api.post("files/links", _files_fields(args, "url", "title", "task", "scope"), key=_key(args))


@tool(
    "hub_file_touch",
    "Say you edited a linked document again, so it moves to the top of your files. Give its file id or its https link.",
    {"target": _s("A file id or an https:// link")},
    required=("target",),
    writes=True,
)
def files_touch(api, args):
    target = str(args["target"])
    return api.post(
        "files/links", {"url": target} if target.startswith("https://") else {"file": target}, key=_key(args)
    )


@tool(
    "hub_file_import",
    "Copy an S3 object into Tico so people can open it. Only where the bot's own computer "
    "runs the tool (the `hub file import` command): it reads the object with the credentials that computer "
    "has, within the size and type limits.",
    {"uri": _s("s3://bucket/key"), "title": _s("What people see"), "task": _s("The task it is for")},
    required=("uri",),
    writes=True,
)
def files_import(api, args):
    from clients import bot_files as BF
    from clients.tico import Client

    if not isinstance(api, Client):
        return {
            "refused": "import",
            "detail": "Run `hub file import` on the bot's computer; Tico never "
            "reads your buckets with its own credentials.",
        }
    try:
        key = BF.parse_s3(args["uri"])[1]
        if BF.is_media(key.rsplit("/", 1)[-1]):
            return _import_media(api, args)
        name, _, data, etag = BF.fetch_s3(args["uri"], client=args.get("_s3"))
    except BF.Refused as exc:
        return {"refused": "import", "detail": str(exc)}
    query = {"source": args["uri"], "etag": etag, "name": name, **_files_fields(args, "title", "task")}
    return api.request("POST", "/api/v2/files/imports?" + BF.urlencode(query), raw=data, key=_key(args))


def _import_media(api, args):
    """A video or audio object is copied to a file and sent from it, never held in memory (BF.fetch_s3_file)."""
    import tempfile
    from clients import bot_files as BF

    with tempfile.TemporaryDirectory(prefix="tico-import-") as work:
        name, _, path, etag = BF.fetch_s3_file(args["uri"], work, client=args.get("_s3"))
        BF.check_media(path, name)
        if path.stat().st_size > BF.MAX_BYTES and not api.features().get("file_media_stream"):
            raise BF.Refused(OLD_SERVER_MEDIA)
        query = {"source": args["uri"], "etag": etag, "name": name, **_files_fields(args, "title", "task")}
        with path.open("rb") as stream:
            return api.request("POST", "/api/v2/files/imports?" + BF.urlencode(query), raw=stream, key=_key(args))


# What an older server answers for a video or audio file over 25 MB: it takes those only as bytes up to 25 MB.
OLD_SERVER_MEDIA = (
    "This Tico server takes video and audio files up to 25 MB. Ask for a Tico update to send larger "
    "video or audio files."
)
