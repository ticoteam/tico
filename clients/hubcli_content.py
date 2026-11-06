"""Content commands: files, pages, cards, pins, docs, chat, assistant and tags."""

from clients.hubcli_core import (
    delivery_flags,
)


def add_content(sub):
    files = sub.add_parser(
        "file", help="what you publish for people: reports, documents, imported objects"
    ).add_subparsers(dest="sub")
    s = files.add_parser("archive", help="archive a File with your rights")
    s.add_argument("id")
    s.set_defaults(fn="file archive")
    s = files.add_parser("list", help="the files on your page, newest activity first")
    s.add_argument("--bot")
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor")
    s.set_defaults(fn="file list")
    s = files.add_parser("publish", help="upload a file from this checkout (reports/x.md); again adds a version")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--task", help="the task it is for; defaults to the one you are working on")
    s.add_argument("--scope", choices=["task", "bot"])
    s.set_defaults(fn="file publish")
    s = files.add_parser("link", help="list a Google Doc, Notion page, Figma file or any https link")
    s.add_argument("url")
    s.add_argument("--title")
    s.add_argument("--task")
    s.add_argument("--scope", choices=["task", "bot"])
    s.set_defaults(fn="file link")
    s = files.add_parser("touch", help="you edited a linked document again: move it to the top")
    s.add_argument("target", help="a file id or its https link")
    s.set_defaults(fn="file touch")
    s = files.add_parser("import", help="copy an s3:// object with this computer's credentials into Tico")
    s.add_argument("uri")
    s.add_argument("--title")
    s.add_argument("--task")
    s.set_defaults(fn="file import")
    s = files.add_parser("search", help="every file you may read, by title and text")
    s.add_argument("q")
    s.add_argument("--kind", choices=["page", "document", "image", "data", "media", "other"])
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="file search")
    s = files.add_parser("read", help="one version of a file you may read, as text, in full (default: the current one)")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    s.set_defaults(fn="file read")
    s = files.add_parser("delete", help="the owner deletes a file for good: its bytes leave storage")
    s.add_argument("id")
    s.set_defaults(fn="file delete")
    s = files.add_parser(
        "approved",
        help="exit 0 when a person approved this exact version on a review question, "
        "1 when not (its JSON answer says \"approved\": false), 2 on bad input. "
        "A failed call exits 1 or 2 with an error answer: check the answer, not only "
        "the code",
    )
    s.add_argument(
        "target", help="a path in this checkout (its bytes now), or FILE[@N] (the current version without @N)"
    )
    s.add_argument("--by", help="whose verdicts count: human (default), any, or bot:SLUG")
    s.set_defaults(fn="file approved")
    s = files.add_parser("review", help="approve or ask for changes on another bot's file version, on a task you read")
    s.add_argument(
        "target", help="FILE[@N] (the current version without @N), or a path in this checkout (its bytes now)"
    )
    verdict = s.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--approve", dest="verdict", action="store_const", const="approve")
    verdict.add_argument("--changes", dest="verdict", action="store_const", const="changes")
    s.add_argument("--task", required=True, help="the task the review is for")
    s.add_argument("--note", help="what to change, or why it is approved")
    s.set_defaults(fn="file review")
    page = sub.add_parser("page", help="pages: HTML a bot keeps up to date, with Tico's updated line").add_subparsers(
        dest="sub"
    )
    s = page.add_parser("publish", help="publish an .html file from this checkout; again adds a version")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--cadence", choices=["none", "daily", "weekly", "monthly"])
    s.add_argument("--as-of", dest="as_of", help="the date the data is from, YYYY-MM-DD")
    s.add_argument("--task", help="the task it is for; it shows at the top of that task")
    s.add_argument("--replaces", help="the id of an older page this one takes the place of")
    s.add_argument("--scope", choices=["task", "bot"])
    s.add_argument("--audience", help="everyone, or ben,group:legal,bot:analyst")
    s.set_defaults(fn="page publish")
    s = page.add_parser("list", help="pages you may read, newest first")
    s.add_argument("--bot")
    s.add_argument("--task", help="pages filed on or pinned to this task")
    s.add_argument("--archived", action="store_true", default=None)
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor")
    s.set_defaults(fn="page list")
    s = page.add_parser("show", help="one page's status")
    s.add_argument("id")
    s.set_defaults(fn="page show")
    s = page.add_parser("access", help="show or change who can see a page")
    s.add_argument("id")
    s.add_argument("--audience", help="everyone, or ben,group:legal,bot:analyst")
    s.set_defaults(fn="page access")
    s = page.add_parser("archive", help="hide a page from lists and search, or --restore it")
    s.add_argument("id")
    s.add_argument("--restore", action="store_true", default=None)
    s.set_defaults(fn="page archive")
    cards = sub.add_parser(
        "card", help="Tico's built-in cards on the Dashboard, team pages, sidebars and tasks"
    ).add_subparsers(dest="sub")
    s = cards.add_parser("list", help="what is pinned at a place, with what each card shows you")
    s.add_argument("--place")
    s.set_defaults(fn="card list")
    for name, helptext in (
        ("add", "add a built-in card, pinned at --place"),
        ("update", "change a card's heading or settings"),
    ):
        s = cards.add_parser(name, help=helptext)
        s.add_argument("template" if name == "add" else "card")
        s.add_argument("--title")
        s.add_argument("--team")
        s.add_argument("--weeks", type=int)
        s.add_argument("--count", type=int)
        s.add_argument("--kpi")
        s.add_argument("--metric")
        s.add_argument("--channel")
        s.add_argument("--months", type=int)
        if name == "add":
            s.add_argument("--place")
            s.add_argument("--size", choices=["small", "medium", "large"])
        s.set_defaults(fn="card " + name)
    s = cards.add_parser("remove", help="remove a card and its pins")
    s.add_argument("card")
    s.set_defaults(fn="card remove")
    pins = sub.add_parser("pin", help="pin a page or a card at a place").add_subparsers(dest="sub")
    s = pins.add_parser("add", help="pin page:<file id> or card:<id> at a place")
    s.add_argument("place")
    s.add_argument("item")
    s.add_argument("--size", choices=["small", "medium", "large"])
    s.add_argument("--position", type=int)
    s.set_defaults(fn="pin add")
    s = pins.add_parser("update", help="resize or move a pin")
    s.add_argument("pin")
    s.add_argument("--size", choices=["small", "medium", "large"])
    s.add_argument("--position", type=int)
    s.set_defaults(fn="pin update")
    s = pins.add_parser("remove", help="unpin")
    s.add_argument("pin")
    s.set_defaults(fn="pin remove")
    docs = sub.add_parser(
        "doc", help="read and search team docs and external sources, write internal docs, ask the Librarian"
    ).add_subparsers(dest="sub")
    s = docs.add_parser("archive", help="archive an internal Doc with your rights")
    s.add_argument("ref")
    s.set_defaults(fn="doc archive")
    s = docs.add_parser("list", help="internal docs by path")
    s.add_argument("--prefix", help="only paths starting with this, e.g. sales/")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="doc list")
    s = docs.add_parser("read", help="one internal or indexed external doc in full")
    s.add_argument("ref", help="a doc id/path, external:<id>, or manual:<name>")
    s.add_argument("--revision", help="external commit returned by search")
    s.set_defaults(fn="doc read")
    s = docs.add_parser("search", help="search internal, indexed external and linked docs, then the Tico manual")
    s.add_argument("q")
    s.add_argument("--limit", type=int)
    s.add_argument(
        "--manual",
        dest="collection",
        action="store_const",
        const="manual",
        help="only the read-only Tico manual (how to do something in Tico)",
    )
    s.add_argument("--team", dest="collection", action="store_const", const="team", help="only team docs")
    s.add_argument("--market", action="store_true", default=None, help="also the market's notes, entities and evidence")
    s.add_argument("--source-id", help="only this external source")
    s.set_defaults(fn="doc search")
    s = docs.add_parser("write", help="create or replace an internal doc at a path")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--body-file", dest="body_file", help="the Markdown file; standard input when omitted")
    s.add_argument("--note", help="one line on what changed")
    s.set_defaults(fn="doc write")
    s = docs.add_parser("history", help="the versions of a doc")
    s.add_argument("ref", help="a doc id or a path")
    s.set_defaults(fn="doc history")
    s = docs.add_parser("link-list", help="the linked docs: where the company's other docs live")
    s.set_defaults(fn="doc link-list")
    chat = sub.add_parser("chat", help="bot chat goals and commands").add_subparsers(dest="sub")
    s = chat.add_parser("goal", help="read, set, edit, pause, resume or clear a chat goal")
    s.add_argument("conversation_id")
    s.add_argument("action", nargs="?", default="get", choices=["get", "set", "edit", "pause", "resume", "clear"])
    s.add_argument("objective", nargs="?")
    s.set_defaults(fn="chat goal")
    s = chat.add_parser("send", help="send a chat message or harness command")
    s.add_argument("to")
    s.add_argument("text")
    s.add_argument("--conversation", dest="conversation_id")
    s.add_argument("--command", action="store_true")
    delivery_flags(s)
    s.add_argument(
        "--visual",
        action="append",
        metavar="PATH",
        help="a picture, SVG, HTML page, video or audio to show with it (a file in your checkout, or ID@VERSION "
        "already in Tico); repeat for more",
    )
    s.add_argument(
        "--title",
        action="append",
        dest="visual_title",
        metavar="TITLE",
        help="the caption for each --visual, in the same order",
    )
    s.set_defaults(fn="chat send")
    assistant = sub.add_parser("assistant", help="the Assistant's proposals for a person to confirm").add_subparsers(
        dest="sub"
    )
    assistant.add_parser("read", help="read your private Assistant chat").set_defaults(fn="assistant read")
    s = assistant.add_parser("send", help="ask your private Assistant")
    s.add_argument("text")
    s.set_defaults(fn="assistant send")
    s = assistant.add_parser("propose", help="ask the person to confirm one side-effecting operation")
    s.add_argument("--summary", required=True)
    s.add_argument("--path", required=True)
    s.add_argument("--method", default="POST", choices=["POST", "PUT", "PATCH", "DELETE"])
    s.add_argument("--body", default="{}", help="the request's JSON body")
    s.add_argument(
        "--for",
        dest="for_person",
        default=None,
        help="the Assistant's own background run only: the person (human:<id>) whose chat gets the card",
    )
    s.set_defaults(fn="assistant propose")
    s = assistant.add_parser("morning", help="the Assistant's one morning message to a person (background run only)")
    s.add_argument("--for", dest="for_person", required=True, help="the person (human:<id>) whose chat gets it")
    s.add_argument("--body", required=True, help="the message, in Markdown, at most 2000 characters")
    s.set_defaults(fn="assistant morning")
    s = assistant.add_parser("learn", help="save one thing the person taught you (What it has learned)")
    s.add_argument("text", help="one short line, in their words")
    s.add_argument(
        "--company",
        dest="layer",
        action="store_const",
        const="company",
        default="personal",
        help="the Strategy Bot in the owner's chat: about the company, not how they like things",
    )
    s.set_defaults(fn="assistant learn")
    assistant.add_parser(
        "learned", help="what your chat bot has learned: your layer, and the company's for the owner"
    ).set_defaults(fn="assistant learned")
    s = assistant.add_parser("withdraw", help="take back a Confirm card that waits: it never runs (any teammate)")
    s.add_argument("id", help="the card's proposal id (hub assistant proposals)")
    s.add_argument("--reason", default="", help="why, in one short sentence: the person reads it in their chat")
    s.set_defaults(fn="assistant withdraw")
    change = assistant.add_parser(
        "change", help="what your Assistant did for you, and undoing one of those changes"
    ).add_subparsers(dest="subsub")
    s = change.add_parser("list", help="the Assistant's changes for you, newest first, with the words it acted on")
    s.add_argument("--days", type=int, help="how far back (default 30, at most 90)")
    s.add_argument("--limit", type=int, help="changes per page (default 50, at most 100)")
    s.add_argument("--before", help="the `next` of the page before, to read older changes")
    s.set_defaults(fn="assistant change list")
    s = change.add_parser("restore", help="undo one change as you; restoring it again changes nothing")
    s.add_argument("id", help="the change's id (hub assistant change list)")
    s.set_defaults(fn="assistant change restore")
    s = assistant.add_parser("proposals", help="the Confirm cards still waiting for their owner (find one to withdraw)")
    s.add_argument("--proposer", choices=["botops", "assistant"], default=None, help="only the cards one proposed")
    s.add_argument("--owner", default=None, help="only one person's cards, e.g. human:ana")
    s.set_defaults(fn="assistant proposals")
    # Tags keep the existing task --label key contract.
    tag = sub.add_parser("tag", help="tags and checklist templates").add_subparsers(dest="sub")
    s = tag.add_parser("list")
    s.add_argument("--templates", dest="is_template", action="store_const", const=True, default=None)
    s.add_argument("--instances", dest="is_template", action="store_const", const=False)
    s.set_defaults(fn="tag list")
    s = tag.add_parser("show")
    s.add_argument("id", help="tag key or id")
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="tag show")
    s = tag.add_parser("create")
    s.add_argument("key")
    s.add_argument("--label")
    s.add_argument("--metadata", help="JSON object")
    s.add_argument("--markdown")
    s.add_argument("--markdown-file")
    s.add_argument("--template", dest="is_template", action="store_const", const=True, default=None)
    s.add_argument("--from-template", dest="template_id")
    s.add_argument("--owner")
    s.set_defaults(fn="tag create")
    s = tag.add_parser("update")
    s.add_argument("id", help="tag key or id")
    s.add_argument("--version", type=int, required=True)
    s.add_argument("--label")
    s.add_argument("--metadata", help="JSON object; replaces metadata")
    s.add_argument("--markdown")
    s.add_argument("--markdown-file")
    s.add_argument("--owner")
    s.set_defaults(fn="tag update")
    return docs
