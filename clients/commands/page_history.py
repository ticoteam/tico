"""Page history (backend/page_history.py): `tico page-history`. clients/commands/__init__.py says how the table is put
together.

The pages a person opened in the app, newest first, kept 30 days. Only a person and their own Assistant turn read it,
each their own; the server refuses a bot."""

from clients.cli_spec import Command
from clients.commands._registry import tool
from clients.commands._requester import HUMANS_AND_ASSISTANT
from clients.commands._schemas import _s

# The Assistant reads its person's history; a bot is refused (views.human_only), so it is not offered one.
AUDIENCE = {"tico_page_history": HUMANS_AND_ASSISTANT}


@tool(
    "tico_page_history",
    "The pages the person opened in the app (last 30 days), newest first: each `page` path, its `title`, the record it "
    "shows and when. Use it for \"this\", \"that one\" or \"the last thing\", or to find where they have been.",
    {
        "page": _s("Only this page and those under it, e.g. /tasks"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Visits per page (default 50)"},
        "before": _s("The `next` of the page before, to read older visits"),
    },
)
def page_history(api, args):
    return api.get("page-history", page=args.get("page"), limit=args.get("limit"), before=args.get("before"))


PAGE_HISTORY = (
    Command(
        "page-history",
        "the pages you opened in the app, newest first (last 30 days)",
        example="tico page-history --page /tasks",
    ),
)
