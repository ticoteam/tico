# pyright: strict
"""A bot made from a template follows it (docs/updates.md, "Bots follow their template").

The computer running the bot brings its files up to the release's template before the bot's first turn after the
computer starts (clients/catalog.py refresh, runner_support/catalog_job.py) and reports here what it did. The report
becomes one event naming the bot, the release its files came from and the one they come from now, and the files added,
updated, removed, and kept because the bot or a person changed them. It moves the bot's `template_version`, so
`tico bot show` and GET /api/v2/bots/{bot} name the release its files are from. A bot whose template no longer exists
(a renamed or removed card) is left alone; the first report of that is one `bot.template_missing` event.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import Request
from pydantic import Field

from . import bot_settings as BS
from . import hubdb as H
from . import models as M
from . import releases
from .store import Problem

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

Path = Annotated[str, Field(min_length=1, max_length=300)]
FILES = ("added", "updated", "removed", "kept")
# The untyped helpers this module calls, typed here.
_update = cast(Callable[[Any, str, dict[str, Any]], dict[str, Any]], BS.update)
_event = cast(Callable[[Any, str, str, str, dict[str, Any]], Any], H.event)
_resolve = cast(Callable[[Any, str], dict[str, Any] | None], H.resolve_bot)


class TemplateRefresh(M.Contract):
    template: M.Slug
    # The release the files came from; empty when the computer runs a source build, and then the server's is used.
    to_version: str = Field(default="", max_length=60)
    missing: bool = False
    added: list[Path] = Field(default_factory=list, max_length=500)
    updated: list[Path] = Field(default_factory=list, max_length=500)
    removed: list[Path] = Field(default_factory=list, max_length=500)
    kept: list[Path] = Field(default_factory=list, max_length=500)


def record(c: Any, who: Any, bot: str, body: TemplateRefresh) -> dict[str, Any]:
    """Store what a computer reported for a bot assigned to it. Idempotent: a report that changes nothing records
    nothing."""
    if not c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?", (bot, who.runner_id)).fetchone():
        raise Problem("forbidden", "That bot is not assigned to this computer", 403)
    declared = BS.read(c, bot)
    # The bot was given another template since the computer read it: that report is about files it no longer follows.
    if str(declared.get("template") or "") != body.template:
        return {"recorded": False}
    if body.missing:
        if declared.get("template_missing") == body.template:
            return {"recorded": False}
        _update(c, bot, {"template_missing": body.template})
        _event(c, who.actor, "bot.template_missing", bot, {"template": body.template})
        return {"recorded": True}
    version = body.to_version.strip() or releases.version()
    before = str(declared.get("template_version") or "")
    files: dict[str, list[str]] = {key: getattr(body, key) for key in FILES if getattr(body, key)}
    if before == version and not files and "template_missing" not in declared:
        return {"recorded": False}
    _update(c, bot, {"template_version": version, "template_missing": None})
    _event(
        c,
        who.actor,
        "bot.template_refreshed",
        bot,
        {"template": body.template, "from_version": before, "to_version": version, **files},
    )
    return {"recorded": True, "template_version": version}


def install(app: "FastAPI", store: "Store", auth: "Auth", mutate: "Callable[..., Any]") -> None:
    @app.post("/api/v2/bots/{bot}/template/refresh")
    def report(request: Request, bot: str, body: TemplateRefresh) -> Any:  # pyright: ignore[reportUnusedFunction]
        """The computer running a bot reports bringing its files up to the release's template."""
        who = request.state.identity
        if who.role != "runner":
            raise Problem("forbidden", "Only the computer running a bot reports its template refresh", 403)

        def write(c: Any) -> dict[str, Any]:
            return record(c, who, str((_resolve(c, bot) or {}).get("id", bot)), body)

        return mutate(request, body, write)
