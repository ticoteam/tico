# pyright: strict
"""Page history (backend/page_history.py): `tico page-history`. clients/commands/__init__.py says how the table is put
together.

The pages a person opened in the app, newest first, kept 30 days. Only a person and their own Assistant turn read it,
each their own; the server refuses a bot."""

from collections.abc import Callable
from typing import Any, Protocol, cast

from clients.cli_spec import Command
from clients.commands import _registry, _schemas
from clients.commands._requester import HUMANS_AND_ASSISTANT

# The tool table's helpers are untyped; these are the shapes this module calls them with.
Tool = Callable[[Any, dict[str, Any]], Any]
tool = cast("Callable[..., Callable[[Tool], Tool]]", _registry.tool)
text = cast("Callable[[str], dict[str, Any]]", _schemas._s)  # pyright: ignore[reportPrivateUsage] -- every tool module shares it


class Api(Protocol):
    def get(self, path: str, /, **query: object) -> Any: ...


# The Assistant reads its person's history; a bot is refused (views.human_only), so it is not offered one.
AUDIENCE = {"tico_page_history": HUMANS_AND_ASSISTANT}


@tool(
    "tico_page_history",
    "The pages the person opened in the app (last 30 days), newest first: each `page` path, its `title`, the record it "
    "shows and when. Use it for \"this\", \"that one\" or \"the last thing\", or to find where they have been.",
    {
        "page": text("Only this page and those under it, e.g. /tasks"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Visits per page (default 50)"},
        "before": text("The `next` of the page before, to read older visits"),
    },
)
def page_history(api: Api, args: dict[str, Any]) -> Any:
    return api.get("page-history", page=args.get("page"), limit=args.get("limit"), before=args.get("before"))


PAGE_HISTORY = (
    Command(
        "page-history",
        "the pages you opened in the app, newest first (last 30 days)",
        example="tico page-history --page /tasks",
    ),
)
