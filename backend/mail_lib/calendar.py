# pyright: strict
"""The video meeting link of a Google Calendar event, wherever the event keeps it."""

import re
from typing import Any

Json = dict[str, Any]

MEETING_URL = re.compile(
    r"https?://(?:meet\.google\.com|(?:[a-z0-9-]+\.)?zoom\.us|teams\.microsoft\.com|"
    r"(?:[a-z0-9-]+\.)?webex\.com)/[^\s<>\"]+",
    re.I,
)


def meeting_url(event: Json) -> str:
    """The event's Meet, Zoom, Teams or Webex link: its hangoutLink, a video entry point, else one written in its
    location or description. '' when it has none."""
    for candidate in [event.get("hangoutLink")]:
        if candidate:
            hit = MEETING_URL.search(str(candidate))
            if hit:
                return hit.group(0).rstrip(".,)")
    conference: Json = event.get("conferenceData") or {}
    points: list[Json] = conference.get("entryPoints") or []
    for point in points:
        candidate = point.get("uri") if point.get("entryPointType") == "video" else ""
        if candidate:
            hit = MEETING_URL.search(str(candidate))
            if hit:
                return hit.group(0).rstrip(".,)")
    # Location and description are inspected only to find a conferencing URL; neither field is
    # returned to the caller.
    for field in (event.get("location"), event.get("description")):
        hit = MEETING_URL.search(str(field or ""))
        if hit:
            return hit.group(0).rstrip(".,)")
    return ""
