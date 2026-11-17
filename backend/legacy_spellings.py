# pyright: strict
"""Old route spellings that rc.1 clients still call, and the canonical path each one now answers as.

The v0.6.0-rc.1 runner, `tico` CLI, UI, bots' playbooks and mail setup text call these paths, so the routes stay, marked
`deprecated` in OpenAPI, until those clients are gone. Every other old spelling was removed with the v0.2.21 rename
layer. The gates (backend/assistant.py, backend/settings_catalog.py) check the canonical spelling, so a kept spelling is
mapped here before they look at it.
"""

import re
from collections.abc import Iterator

# (method, old path, canonical path). Each is registered on the handler of its canonical path (deprecated).
KEPT: tuple[tuple[str, str, str], ...] = (
    ("GET", "/api/people", "/api/humans"),
    ("GET", "/api/v2/getting-started", "/api/v2/setup/getting-started"),
    ("GET", "/api/v2/onboarding", "/api/v2/setup"),
    ("GET", "/api/v2/integrations/{service}/queries", "/api/v2/tools/{service}/queries"),
    ("POST", "/api/v2/access/people/{pid}", "/api/v2/access/humans/{pid}"),
    ("POST", "/api/v2/goal-proposals/{pid}/decide", "/api/v2/proposals/{pid}/decide"),
    ("POST", "/api/v2/goal-proposals/turned-down", "/api/v2/proposals/turned-down"),
    ("POST", "/api/v2/runners/{rid}/inbox-only", "/api/v2/computers/{rid}/inbox-only"),
)

_PLACEHOLDER: re.Pattern[str] = re.compile(r"\{(\w+)\}")


def _match(template: str, path: str) -> dict[str, str] | None:
    """The placeholder values of `template` in `path` (`{pid}` is one segment), or None when `path` is not it."""
    pattern = "".join("(?P<{}>[^/]+)".format(name) if name else re.escape(text) for text, name in _tokens(template))
    found = re.fullmatch(pattern, path)
    if found is None:
        return None
    return {key: str(value) for key, value in found.groupdict().items()}


def _tokens(template: str) -> Iterator[tuple[str, str]]:
    """(literal text, '') and (placeholder text, name) pieces of a path template, in order."""
    pos = 0
    for found in _PLACEHOLDER.finditer(template):
        if found.start() > pos:
            yield template[pos : found.start()], ""
        yield found.group(0), found.group(1)
        pos = found.end()
    if pos < len(template):
        yield template[pos:], ""


def canonical(path: str) -> str | None:
    """The canonical spelling of a kept old path (`/api/v2/runners/{id}/inbox-only`), or None when `path` is not one."""
    for _, old, new in KEPT:
        values = _match(old, path)
        if values is not None:
            return new.format(**values)
    return None


def legacy_of(path: str) -> list[str]:
    """The kept old spellings of a canonical path, usually none: the same handler answers both."""
    found: list[str] = []
    for _, old, new in KEPT:
        values = _match(new, path)
        if values is not None:
            found.append(old.format(**values))
    return found
