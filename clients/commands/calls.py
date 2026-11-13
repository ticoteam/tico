"""Calls, meetings and the calendar.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import sys
from urllib.parse import quote
from datetime import datetime
from pathlib import Path

from clients.commands._registry import tool
from clients.commands._schemas import TASK_ID, _key, _s
from clients.commands._requester import _as_person


@tool(
    "tico_meeting_delete",
    "Delete a Meeting you may edit. Its record remains recoverable.",
    {"id": _s("Meeting id")},
    required=("id",),
    writes=True,
)
def meeting_delete(api, args):
    return _as_person(api).post(f"meetings/{args['id']}/delete", {}, key=_key(args))


# ----------------------------------------------------------------------------- meetings
@tool(
    "tico_meeting_link",
    "Link existing tasks to a saved meeting. Results match input order; status 404 skips an unavailable task. Links preserve each resource's access.",
    {
        "id": _s("Meeting id"),
        "task_ids": {"type": "array", "items": TASK_ID, "minItems": 1, "maxItems": 20},
        "kind": _s("linked (default), or sent for tasks you requested", enum=["linked", "sent"]),
    },
    required=("id", "task_ids"),
    writes=True,
)
def meeting_link(api, args):
    return _as_person(api).post(
        f"meetings/{args['id']}/tasks",
        {"task_ids": args['task_ids'], "kind": args.get('kind', 'linked')},
        key=_key(args),
    )


@tool(
    "tico_meeting_unlink",
    "Remove a meeting's task link.",
    {"id": _s("Meeting id"), "task": TASK_ID},
    required=("id", "task"),
    writes=True,
)
def meeting_unlink(api, args):
    return _as_person(api).call("DELETE", f"meetings/{args['id']}/tasks/{args['task']}", key=_key(args))


@tool("tico_meeting_pending", "List your own Pending meetings, before sharing with the Team.", {})
def meeting_pending(api, args):
    return _as_person(api).get("meetings", review="pending")


@tool(
    "tico_meeting_approve",
    "Share one of your Pending meetings, or all of them. Only meetings filed for you may be reviewed.",
    {"id": _s("Meeting id"), "all": {"type": "boolean"}, "private": {"type": "boolean"}},
    writes=True,
)
def meeting_approve(api, args):
    if bool(args.get("id")) == bool(args.get("all")):
        raise ValueError("Give a meeting id or all=true")
    person = _as_person(api)
    if args.get("all"):
        return person.post("meetings/review", {"action": "approve_all"}, key=_key(args))
    body = {"action": "approve"}
    if "private" in args:
        body["private"] = args["private"]
    return person.post(f"meetings/{args['id']}/review", body, key=_key(args))


@tool(
    "tico_meeting_dismiss",
    "Dismiss one of your Pending meetings; later syncs keep it dismissed.",
    {"id": _s("Meeting id")},
    required=("id",),
    writes=True,
)
def meeting_dismiss(api, args):
    return _as_person(api).post(f"meetings/{args['id']}/review", {"action": "dismiss"}, key=_key(args))


@tool(
    "tico_meeting_restore",
    "Restore one of your dismissed meetings to Pending.",
    {"id": _s("Meeting id")},
    required=("id",),
    writes=True,
)
def meeting_restore(api, args):
    return _as_person(api).post(f"meetings/{args['id']}/review", {"action": "restore"}, key=_key(args))


@tool(
    "tico_meeting_granola_status",
    "Read your own Granola connection status. To connect, open #/meetings in Tico in your browser and choose Connect Granola; sign-in must be completed by the person.",
    {},
)
def granola_status(api, args):
    return _as_person(api).get("meetings/granola")


@tool(
    "tico_meeting_granola_sync",
    "Start a background sync of your own Granola notes. Recent syncs are reused. Connect your account in Tico's Meetings page (#/meetings) in your browser; a bot cannot complete sign-in.",
    {},
)
def granola_sync(api, args):
    return _as_person(api).post("meetings/granola/sync", {})


@tool(
    "tico_meeting_search",
    "Search meeting history and transcripts, with excerpts and available speaker timestamps. Bots see shared Team meetings and filed meetings they were attached to, never personal notes. Empty q lists recent accessible meetings.",
    {
        "q": _s("Words to search for; omit for recent history"),
        "person": _s("Owner, participant, or speaker"),
        "since": _s("Inclusive meeting date, YYYY-MM-DD; creation date when no start is recorded"),
        "until": _s("Inclusive meeting date, YYYY-MM-DD"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "offset": {"type": "integer", "minimum": 0},
    },
)
def meetings_search(api, args):
    return api.get("meetings/search", **args)


@tool(
    "tico_meeting_read",
    "Read transcript or notes; follow next_offset for the rest.",
    {
        "id": _s("Meeting id"),
        "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50000},
        "notes": {"type": "boolean", "description": "Notes only"},
    },
    required=("id",),
)
def meetings_transcript(api, args):
    return api.get("meetings/transcript", **args)


@tool(
    "tico_meeting_import",
    "File a meeting transcript or notes from another tool (Zoom, Google Meet, Granola, Otter, "
    "Fireflies, a file...) into your Pending queue unless you choose live or auto-share. The transcript is plain text (one line per turn: an optional "
    "[mm:ss] and 'Name: text'), WebVTT, SRT, or JSON segments [{speaker, start, end, text}] with times in seconds; the "
    "format is detected. Send the same source and external_id again to update it instead of adding another.",
    {
        "title": _s("What the meeting was"),
        "transcript": {
            "description": "The transcript: text, WebVTT, SRT, or JSON segments as a string or a list of objects",
            "type": ["string", "array"],
        },
        "format": _s("auto (default), text, vtt, srt or json"),
        "notes": _s("Notes or a summary, in Markdown"),
        "started_at": _s("When it started, ISO-8601 with a timezone"),
        "duration_seconds": {"type": "number", "minimum": 0},
        "participants": {"type": "array", "items": {"type": "string"}, "description": "Emails or names"},
        "source": _s("Where it came from: zoom, granola, otter, fireflies, upload... (default api)"),
        "external_id": _s("That system's id for it, to update it later"),
        "media_url": _s("An https link to the recording"),
        "private": {"type": "boolean"},
        "review": _s("Share immediately, or keep in Pending", enum=["pending", "live"]),
        "send_to": _s("A bot to hand the meeting to once shared, as Send does"),
    },
)
def meetings_import(api, args):
    return api.post("meetings/import", args)


def meetings_import_file(api, args):
    """`tico meeting import <file>`: the CLI reads the files, so nothing here needs a path on the server."""
    args = dict(args)
    path = args.pop("file")
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    notes = args.pop("notes_file", None)
    when = args.pop("date", None)
    fields = {
        "transcript": text,
        "title": args.pop("title", None) or ("" if path == "-" else Path(path).stem),
        "participants": args.pop("participants", None) or [],
    }
    if notes:
        fields["notes"] = Path(notes).read_text(encoding="utf-8")
    if when:
        try:
            fields["started_at"] = datetime.fromisoformat(when).astimezone().isoformat()
        except ValueError:
            raise ValueError(f"--date {when!r} is not a date: use 2026-09-28 or 2026-09-28T16:00") from None
    return api.post("meetings/import", {**{k: v for k, v in args.items() if v is not None}, **fields})


# ----------------------------------------------------------------------------- calendar
@tool(
    "tico_calendar_list",
    "Read upcoming appointments on a team calendar. Every bot may "
    "use this tool. The default is the team owner's calendar; pass `calendar` for your "
    "operator's. "
    "Results are Tico's provider-normalized snapshot and include its freshness.",
    {"calendar": _s("Roster email whose calendar to read; default the team owner's", default="")},
)
def calendar_upcoming(api, args):
    return api.get("calendar/appointments", calendar=args.get("calendar") or None)


@tool(
    "tico_calendar_schedule",
    "Schedule an appointment on a team calendar. Every bot may use "
    "this tool without a separate permission grant. Tico queues one idempotent provider "
    "action; attendees receive the invitation when the calendar tool completes it. Use "
    "tico_calendar_status before claiming success.",
    {
        "calendar": _s("Roster email whose calendar owns the event; default the team owner's", default=""),
        "title": _s("Calendar event title"),
        "start": _s("ISO-8601 start with timezone, for example 2026-09-22T09:00:00-07:00"),
        "end": _s("ISO-8601 end with timezone"),
        "attendees": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Email addresses to invite; may be empty for a private hold",
        },
        "description": _s("Optional attendee-visible event description", default=""),
        "add_meet": {"type": "boolean", "default": True, "description": "Create a Google Meet link"},
    },
    required=("title", "start", "end"),
    writes=True,
)
def calendar_schedule(api, args):
    return api.post(
        "calendar/appointments",
        {
            "calendar": args.get("calendar") or "",
            "title": args["title"],
            "start": args["start"],
            "end": args["end"],
            "attendees": args.get("attendees") or [],
            "description": args.get("description") or "",
            "add_meet": args.get("add_meet", True),
        },
        key=_key(args),
    )


@tool(
    "tico_calendar_status",
    "Check a queued calendar scheduling action. `succeeded` means the "
    "event exists; `pending` or `running` is not confirmation; `unknown` requires inspection "
    "before any retry.",
    {"id": _s("Action id returned by tico_calendar_schedule")},
    required=("id",),
)
def calendar_status(api, args):
    return api.get("calendar/actions/" + args["id"])


# The Calendar (events, bookings, free times): the occurrence is the instance on the grid, and its id is what the
# edit tools take (backend/calendar_api.py).
CALENDAR_SCOPE = _s(
    "For a repeated event: this occurrence, this one and the later ones, or the whole series",
    enum=["this", "following", "all"],
)
CALENDAR_WHO = _s(
    "Comma-separated calendars: refs (human:<id>, bot:<id>, shared:<id>, team:<id>, google:<id>) or emails"
)


def _clean(fields):
    """A request body without the fields the caller did not give (the server takes its own defaults for them)."""
    return {k: v for k, v in fields.items() if v is not None}


@tool(
    "tico_calendar_events",
    "Events, bot runs, due dates and time away on the calendars in `who` (default: yours), in a window of up to 62 "
    "days. Everyone in the company reads a Tico calendar; a Google calendar shows what Google's sharing allows. A "
    "busy-only event shows as Busy. Returns occurrences.",
    {
        "who": CALENDAR_WHO,
        "from": _s("Window start: ISO-8601 with zone, or YYYY-MM-DD. Default: today"),
        "to": _s("Window end, exclusive; at most 62 days after from"),
        "kinds": _s("Comma-separated: event, run, due, away. Default: all"),
    },
)
def calendar_events(api, args):
    return api.get(
        "calendar/events",
        who=args.get("who"),
        **{"from": args.get("from"), "to": args.get("to"), "kinds": args.get("kinds")},
    )


@tool(
    "tico_calendar_free",
    "Times when each person or bot in `who` is free for `minutes`, within their working hours and zone. Times only, "
    "never titles. A bot gets times only for people it may book.",
    {
        "who": _s("Comma-separated people or bots, refs or emails. Default: you"),
        "minutes": {"type": "integer", "minimum": 1, "default": 30, "description": "Length of the free slot"},
        "within": _s("How far ahead to look, such as 5d"),
    },
)
def calendar_free(api, args):
    return api.get("calendar/free", who=args.get("who"), minutes=args.get("minutes", 30), within=args.get("within"))


@tool(
    "tico_calendar_add",
    "Book an event with `attendees`, optionally repeated. Bots and the Assistant may book anyone unless the Who bots "
    "may book rule prevents it, and the refusal names that rule. A person may add only to their own Tico calendar, "
    "and only without Google connected. A write to a connected person's calendar lands on their Google calendar. "
    "Returns the occurrence.",
    {
        "calendar": _s(
            "Whose calendar: a ref (human:<id>, bot:<id>, shared:<id>, google:<id>) or an email such as "
            "legal@acme.example. Default: yours"
        ),
        "title": _s("Event title"),
        "start": _s("ISO-8601 with zone, or YYYY-MM-DD with all_day"),
        "end": _s("ISO-8601 with zone, or YYYY-MM-DD with all_day (exclusive)"),
        "all_day": {"type": "boolean", "default": False, "description": "start and end are dates"},
        "attendees": {"type": "array", "items": {"type": "string"}, "description": "Refs or emails to invite"},
        "repeat": _s("Words or an RRULE, such as 'weekly on mon,wed' or 'daily until 2026-12-31'"),
        "notes": _s("Description for attendees"),
        "add_meet": {"type": "boolean", "default": True, "description": "Add a Google Meet link"},
    },
    required=("title", "start", "end"),
    writes=True,
)
def calendar_add(api, args):
    body = _clean(
        {
            "calendar": args.get("calendar"),
            "title": args["title"],
            "start": args["start"],
            "end": args["end"],
            "all_day": args.get("all_day"),
            "attendees": args.get("attendees"),
            "repeat": args.get("repeat"),
            "notes": args.get("notes"),
            "add_meet": args.get("add_meet", True),
        }
    )
    return api.post("calendar/events", body, key=_key(args))


@tool(
    "tico_calendar_move",
    "Move one occurrence (its occurrence_id from tico_calendar_events or tico_calendar_search) to new times. A Google "
    "event moves on Google. The same rules as tico_calendar_add apply to who may write.",
    {
        "occurrence": _s("occurrence_id of the instance to move"),
        "start": _s("New start: ISO-8601 with zone, or YYYY-MM-DD for all day"),
        "end": _s("New end, in the same form"),
        "scope": CALENDAR_SCOPE,
    },
    required=("occurrence", "start"),
    writes=True,
)
def calendar_move(api, args):
    body = _clean({"start": args["start"], "end": args.get("end"), "scope": args.get("scope")})
    return api.patch("calendar/events/" + quote(args["occurrence"], safe=""), body, key=_key(args))


@tool(
    "tico_calendar_cancel",
    "Cancel one occurrence, or with scope the repeated event from it on. The same rules as tico_calendar_add apply to "
    "who may write.",
    {"occurrence": _s("occurrence_id of the instance to cancel"), "scope": CALENDAR_SCOPE},
    required=("occurrence",),
    writes=True,
)
def calendar_cancel(api, args):
    return api.call(
        "DELETE",
        "calendar/events/" + quote(args["occurrence"], safe=""),
        key=_key(args),
        query={"scope": args.get("scope")},
    )


@tool(
    "tico_calendar_search",
    "Find events whose title, notes or location match the words, across the calendars in `who` (default: yours), "
    "nearest first. Tokens in q: with:<name>, bot:<name>, kind:<event|run|due|away>, before:<date>, after:<date>, "
    "has:transcript, automation:<name>, trigger:<kind>, longer:<N>m. `partial` true means some Google calendars ran "
    "out of time.",
    {"q": _s("Words and tokens"), "who": CALENDAR_WHO},
    required=("q",),
)
def calendar_search(api, args):
    return api.get("calendar/search", q=args["q"], who=args.get("who"))


@tool(
    "tico_call_list",
    "The calls in a thread, newest first: id, title, state (`live` or `ended`), connector, and how each "
    "ended. `next_before` reads older ones.",
    {
        "thread_id": _s("The thread's id"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "before": _s("The `next_before` of the page you read, from that page's answer"),
    },
    required=("thread_id",),
)
def call_list(api, args):
    query = {key: args[key] for key in ("limit", "before") if args.get(key) not in (None, "")}
    return _as_person(api).get(f"threads/{args['thread_id']}/calls", **query)


@tool(
    "tico_call_show",
    "A call: its state, title, connector, who joined (and when they left or were removed), the bots "
    "listening, and how it ended. Only people who can read its thread see it.",
    {"call_id": _s("The call's id")},
    required=("call_id",),
)
def call_show(api, args):
    return _as_person(api).get(f"calls/{args['call_id']}")


@tool(
    "tico_call_start",
    "Start a call in a thread you can write to. Its audio and screen share reach the people in the "
    "thread, and a live transcript is kept while it runs and posted to the thread when it ends. You are its connector. "
    "Refused while another call runs in the thread, and while Tico Meet is not set up on this server.",
    {
        "thread_id": _s("The thread's id"),
        "title": _s("What the call is called, up to 200 characters"),
        "speak_replies": {
            "type": "boolean",
            "description": "Bots speak their replies on this call. Off unless asked; "
            "spoken replies are refused until the Gateway has text-to-speech, so leave it out",
        },
    },
    required=("thread_id",),
    writes=True,
)
def call_start(api, args):
    body = {"title": str(args["title"])} if args.get("title") else {}
    if args.get("speak_replies"):
        body["speak_replies"] = True
    return _as_person(api).post(f"threads/{args['thread_id']}/calls", body, key=_key(args))


@tool(
    "tico_call_join",
    "Join a call you can read. The answer has `url` and `token`: connect a LiveKit client to them to speak "
    "and share your screen (never a camera). The token lasts 5 minutes; get a fresh one with tico_call_token while you stay.",
    {"call_id": _s("The call's id")},
    required=("call_id",),
    writes=True,
)
def call_join(api, args):
    return _as_person(api).post(f"calls/{args['call_id']}/join", {}, key=_key(args))


@tool(
    "tico_call_token",
    "A fresh token for a call you have joined, good for 5 minutes. Refused once you were removed from the "
    "call (you lost access to its thread), and once you have left it or it has ended.",
    {"call_id": _s("The call's id")},
    required=("call_id",),
)
def call_token(api, args):
    return _as_person(api).post(f"calls/{args['call_id']}/token", {})


@tool(
    "tico_call_leave",
    "Leave a call you joined. The others stay in it.",
    {"call_id": _s("The call's id")},
    required=("call_id",),
    writes=True,
)
def call_leave(api, args):
    return _as_person(api).post(f"calls/{args['call_id']}/leave", {}, key=_key(args))


@tool(
    "tico_call_end",
    "End a call you started. The speech so far is transcribed and the transcript is posted to the thread. "
    "Tasks are made by hand.",
    {"call_id": _s("The call's id")},
    required=("call_id",),
    writes=True,
)
def call_end(api, args):
    return _as_person(api).post(f"calls/{args['call_id']}/end", {}, key=_key(args))


@tool(
    "tico_call_retry",
    "Ask again for a call's action items when the Gateway could not make them. Only the person who "
    "started the call may ask, and only after it ended. The sweep makes them again shortly.",
    {"call_id": _s("The call's id")},
    required=("call_id",),
    writes=True,
)
def call_retry(api, args):
    return _as_person(api).post(f"calls/{args['call_id']}/action-items/retry", {}, key=_key(args))


@tool(
    "tico_call_bot_add",
    "Let bots listen to a call you have joined and reply in its thread. You must be able to write to "
    "each bot; up to 10 at once.",
    {
        "call_id": _s("The call's id"),
        "bots": {"type": "array", "items": _s("A bot's slug"), "minItems": 1, "maxItems": 10},
    },
    required=("call_id", "bots"),
    writes=True,
)
def call_bot_add(api, args):
    return _as_person(api).post(
        f"calls/{args['call_id']}/bots", {"bots": [str(bot) for bot in args["bots"]]}, key=_key(args)
    )


@tool(
    "tico_call_bot_remove",
    "Take a bot out of a call. Its replies already in the thread stay.",
    {"call_id": _s("The call's id"), "bot": _s("The bot's slug")},
    required=("call_id", "bot"),
    writes=True,
)
def call_bot_remove(api, args):
    return _as_person(api).call(
        "DELETE", f"calls/{args['call_id']}/bots/{quote(str(args['bot']), safe='')}", {}, key=_key(args)
    )
