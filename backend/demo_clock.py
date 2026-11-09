# pyright: strict
"""The demo's clock. With TICO_DEMO_NOW set (an ISO 8601 time), datetime.now() is that time, so the server's "today" and
"how long ago" are the same on every build and match the browser clock the screenshot harness freezes. Unset, nothing
changes: the real time.

Import this module before any backend module that reads the clock: those bind `datetime` when they are imported."""

import datetime as dt
import os

FROZEN_ENV = "TICO_DEMO_NOW"


def demo_now() -> dt.datetime | None:
    """The demo's time, or None when TICO_DEMO_NOW is unset."""
    text = os.environ.get(FROZEN_ENV, "").strip()
    if not text:
        return None
    try:
        when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit(f"tico demo: TICO_DEMO_NOW is not an ISO 8601 time: {text!r}") from error
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def _freeze(when: dt.datetime) -> None:
    class FrozenDatetime(dt.datetime):
        """datetime whose now() is the demo's time, in the zone asked for (or local time, as datetime.now() is)."""

        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> "FrozenDatetime":
            return cls.fromtimestamp(when.timestamp(), tz)

    dt.datetime = FrozenDatetime  # pyright: ignore[reportAttributeAccessIssue]


_WHEN = demo_now()
if _WHEN is not None:
    _freeze(_WHEN)
