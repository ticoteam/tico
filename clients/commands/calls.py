"""Calls, meetings and the calendar.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import json
import sys
from urllib.parse import quote
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from clients.cli_spec import Arg, Command, Group
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
    "Search meeting history and transcripts, with excerpts and available speaker timestamps. Bots "
    "see shared Team meetings and filed meetings they were attached to, never personal notes. "
    "Empty q lists recent accessible meetings.",
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
    "Fireflies, a file...) into your Pending queue, unless you choose live or auto-share. Plain "
    "text (one 'Name: text' line per turn, optional [mm:ss]), WebVTT, SRT, or JSON segments "
    "[{speaker, start, end, text}] with times in seconds; the format is detected. The same source "
    "and external_id again updates it.",
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
    "Read upcoming appointments on a team calendar. Every bot may use this tool. The default is "
    "the team owner's calendar; pass `calendar` for your operator's. Results are read live from "
    "the calendar and include when they were read.",
    {"calendar": _s("Roster email whose calendar to read; default the team owner's", default="")},
)
def calendar_upcoming(api, args):
    return api.get("calendar/appointments", calendar=args.get("calendar") or None)


@tool(
    "tico_calendar_schedule",
    "Schedule an appointment on a team calendar. Every bot may use "
    "this tool without a separate permission grant. Tico makes the event and queues its "
    "invitation as an email under the send rules. Only status `succeeded` means it exists; "
    "tico_calendar_status reads it again.",
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
    "Events, bot runs, due dates and time away on the calendars in `who` (default: yours), in a "
    "window of up to 62 days. Everyone in the company reads a Tico calendar; a Google calendar "
    "shows what Google's sharing allows. A busy-only event shows as Busy. Returns occurrences.",
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
    "Book an event with `attendees`, optionally repeated. Bots and the Assistant may book anyone "
    "unless the Who bots may book rule prevents it; the refusal names that rule. A person may add "
    "only to their own Tico calendar, and only without Google connected. A write to a connected "
    "person's calendar lands on their Google calendar. Returns the occurrence.",
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
    "Find events whose title, notes or location match the words, across the calendars in `who` "
    "(default: yours), nearest first. Tokens in q: with:<name>, bot:<name>, "
    "kind:<event|run|due|away>, before:<date>, after:<date>, has:transcript, automation:<name>, "
    "trigger:<kind>, longer:<N>m. `partial` true means some Google calendars ran out of time.",
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


# `tico call ...` (clients/cli_spec.py): Tico Meet, a call in a thread. `--speak` is the tool's speak_replies.
CALL = (
    Group("call", "Tico Meet: audio and screen share in a thread, with a transcript posted to it"),
    Command(
        "call list",
        "the calls in a thread, newest first",
        args=(Arg("limit", default=25),),
        example="tico call list t1 --limit 10",
    ),
    Command(
        "call start",
        "start a call in a thread you can write to; you become its connector",
        args=(Arg("title", default=""), Arg("speak_replies", flag="--speak")),
        example="tico call start t1 --title 'Weekly sync'",
    ),
    Command("call show", "a call: its state, people, bots and how it ended", example="tico call show c1"),
    Command(
        "call join",
        "join a call: the URL and a token good for 5 minutes",
        example="tico call join c1",
    ),
    Command("call token", "a fresh token while you are joined", example="tico call token c1"),
    Command("call leave", "leave a call you joined", example="tico call leave c1"),
    Command("call end", "end a call you started; its transcript is posted to the thread", example="tico call end c1"),
    Command(
        "call retry",
        "ask again for action items the Gateway could not make",
        example="tico call retry c1",
    ),
    Group("call bot", "bots that listen to a call and reply in its thread"),
    Command(
        "call bot add",
        "let bots listen to a call you joined (up to 10)",
        args=(Arg("bots", action="store", nargs="+"),),
        example="tico call bot add c1 ana ben",
    ),
    Command(
        "call bot remove",
        "take a bot out of a call",
        example="tico call bot remove c1 ana",
    ),
)


def _no_key(fields):
    """The meeting tools without an operation id: the hand parser these specs replaced never sent one, so a meeting
    write carries no idempotency key, as before."""
    return {k: v for k, v in fields.items() if k != "operation_id"}


# `tico meeting ...` (clients/cli_spec.py). `meeting import` is still built by the hand parser (clients/hubcli.py): it
# reads the transcript and notes files on this computer.
MEETING = (
    Group("meeting", "search meetings, read transcripts, import a transcript from another tool"),
    Group("meeting granola", "your Granola account; connect in Meetings in your browser"),
    Command(
        "meeting granola status",
        "your Granola connection",
        convert=_no_key,
        example="tico meeting granola status",
    ),
    Command(
        "meeting granola sync",
        "start a background sync of your Granola notes",
        convert=_no_key,
        example="tico meeting granola sync",
    ),
    Command(
        "meeting link",
        "link existing tasks to a saved meeting",
        args=(Arg("task_ids", action="store", nargs="+"),),
        omit=("kind",),
        convert=_no_key,
        example="tico meeting link m1 T1 T2",
    ),
    Command("meeting unlink", "remove a meeting's task link", convert=_no_key, example="tico meeting unlink m1 T1"),
    Command(
        "meeting search",
        "search meeting history and transcripts; no words lists recent meetings",
        args=(
            Arg("q", positional=True, nargs="?", default=""),
            Arg("person", default=""),
            Arg("limit", default=20),
            Arg("offset", default=0),
        ),
        convert=_no_key,
        example="tico meeting search pricing --since 2026-09-01",
    ),
    Command(
        "meeting read",
        "read a transcript",
        args=(Arg("offset", default=0), Arg("limit", default=20000), Arg("notes", default=None)),
        convert=_no_key,
        example="tico meeting read m1 --notes",
    ),
    Command("meeting delete", "delete a Meeting you may edit", convert=_no_key, example="tico meeting delete m1"),
    Command("meeting pending", "your Pending queue", convert=_no_key, example="tico meeting pending"),
    Command(
        "meeting approve",
        "share your Pending meetings",
        args=(Arg("id", positional=True, nargs="?"), Arg("private", default=None)),
        one_of=(("id", "all"),),
        convert=_no_key,
        example="tico meeting approve --all",
    ),
    Command(
        "meeting dismiss",
        "dismiss one of your Pending meetings",
        convert=_no_key,
        example="tico meeting dismiss m1",
    ),
    Command(
        "meeting restore",
        "restore a dismissed meeting to Pending",
        convert=_no_key,
        example="tico meeting restore m1",
    ),
)


def calendar_names(value, *, text=False):
    """A comma-separated flag as its names, spaces dropped: a list, or the text the API reads; None when it is empty."""
    names = [part.strip() for part in (value or "").split(",") if part.strip()]
    if not names:
        return None
    return ",".join(names) if text else names


def _zone(name):
    """The IANA zone a time is shown in, or None when there is none to use."""
    try:
        return ZoneInfo(name) if name else None
    except (KeyError, ValueError):
        return None


def _answer_zone(result):
    """The zone an answer's times show in: the first calendar the server gives a time zone for, else a single event's
    own zone. None keeps the offsets the server sent (UTC for Z)."""
    if not isinstance(result, dict):
        return None
    for calendar in result.get("calendars") or []:
        if calendar.get("time_zone"):
            return _zone(calendar["time_zone"])
    return _zone(result.get("time_zone"))


def _instant(iso, zone):
    moment = datetime.fromisoformat(iso)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone) if zone else moment


def _day(day):
    return f"{day:%a} {day.day} {day:%b}"


def _when(start, end, all_day, zone):
    """`Wed 11 Nov 09:30–10:00`, or `Wed 11 Nov all day`: where a line says when, in the zone its answer names."""
    if all_day:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end or start) - timedelta(days=1)  # the end is exclusive
        if last <= first:
            return f"{_day(first)} all day"
        return f"{_day(first)}–{_day(last)} all day"
    begin, finish = _instant(start, zone), _instant(end or start, zone)
    if begin.date() == finish.date():
        return f"{_day(begin)} {begin:%H:%M}–{finish:%H:%M}"
    return f"{_day(begin)} {begin:%H:%M}–{_day(finish)} {finish:%H:%M}"


def _occurrence_line(occ, zone):
    """`Wed 11 Nov 09:30–10:00  Standup  (Ana)  [occurrence id]`: one occurrence, its calendar named."""
    parts = [_when(occ.get("start"), occ.get("end"), occ.get("all_day"), zone), occ.get("title") or "(no title)"]
    if occ.get("planned"):
        parts.append("(planned)")
    if occ.get("count"):
        parts.append(f"({occ['count']} runs)")
    name = (occ.get("calendar") or {}).get("name")
    if name:
        parts.append(f"({name})")
    parts.append(f"[{occ.get('occurrence_id')}]")
    return "  ".join(parts)


def calendar_text(args, result):
    """The Calendar command's answer as lines a person reads."""
    if args.sub == "cancel":
        return f"cancelled {args.occurrence}" + (f" ({args.scope})" if args.scope else "")
    if args.sub in ("add", "move"):
        if not isinstance(result, dict) or "start" not in result:
            return json.dumps(result, indent=2)
        return _occurrence_line(result, _answer_zone(result))
    zone = _answer_zone(result)
    if args.sub == "free":
        slots = (result or {}).get("slots") or []
        return (
            "\n".join(_when(slot["start"], slot["end"], False, zone) for slot in slots)
            or "No free time in that window."
        )
    occurrences = (result or {}).get("occurrences") or []
    empty = "No matches." if args.sub == "search" else "Nothing in that window."
    return "\n".join(_occurrence_line(occ, zone) for occ in occurrences) or empty


def calendar_notes(result):
    """What a Calendar answer could not give: a calendar that did not answer, a search that ran out of time."""
    if not isinstance(result, dict):
        return []
    lines = [f"tico: could not read {err.get('ref')}: {err.get('reason')}" for err in result.get("errors") or []]
    if result.get("partial"):
        lines.append("tico: some results are missing: the search ran out of time")
    return lines


# `tico calendar ...` (clients/cli_spec.py): every leaf but `list` and `schedule`. `list` reads the company calendar's
# appointments (tico_calendar_list) or, with a Calendar flag or --json, the events (tico_calendar_events; see
# clients/remotecli.py); `schedule` reads --description-file on this computer.
def _calendar_render(args, result):
    """The answer as lines a person reads; what it could not give goes to stderr."""
    for line in calendar_notes(result):
        print(line, file=sys.stderr)
    return calendar_text(args, result)


def _calendar_who(fields):
    """--who as the API reads it: the names, comma-separated, spaces dropped; left out when it is empty."""
    who = calendar_names(fields.pop("who", None), text=True)
    return {**fields, **({"who": who} if who else {})}


def _calendar_add(fields):
    """--who is the attendees, comma-separated; --all-day is sent only when given."""
    attendees = calendar_names(fields.pop("attendees", None))
    if not fields.get("all_day"):
        fields.pop("all_day", None)
    return {**fields, **({"attendees": attendees} if attendees else {})}


CALENDAR = (Group("calendar", "events, free times and bookings on calendars"),)
CALENDAR_AFTER_LIST = (
    Command(
        "calendar free",
        "times when people are free, within their working hours",
        render=_calendar_render,
        convert=_calendar_who,
        example="tico calendar free --who human:ana,human:ben --minutes 45 --within 5d",
    ),
    Command(
        "calendar add",
        "book an event; a bot or the Assistant may book anyone a rule allows",
        args=(
            Arg("title", flag="--title"),
            Arg("start", flag="--start"),
            Arg("end", flag="--end"),
            Arg("attendees", flag="--who", action="store"),
            Arg("add_meet", flag="--no-meet", action="store_false"),
        ),
        render=_calendar_render,
        convert=_calendar_add,
        example="tico calendar add --title 'Plan review' --start 2026-12-01T10:00:00-08:00 "
        "--end 2026-12-01T10:30:00-08:00 --who human:ana",
    ),
    Command(
        "calendar move",
        "move one occurrence, or a repeated event from it on with --scope",
        args=(Arg("start", flag="--start"),),
        render=_calendar_render,
        example="tico calendar move occ1 --start 2026-12-01T11:00:00-08:00",
    ),
    Command(
        "calendar cancel",
        "cancel one occurrence, or a repeated event from it on with --scope",
        render=_calendar_render,
        example="tico calendar cancel occ1 --scope following",
    ),
    Command(
        "calendar search",
        "find events by words, with tokens such as with:ben or before:2026-12-01",
        args=(Arg("q", metavar="words"),),
        render=_calendar_render,
        convert=_calendar_who,
        example="tico calendar search 'plan review with:ana'",
    ),
)
CALENDAR_AFTER_SCHEDULE = (
    Command(
        "calendar status",
        "whether a queued appointment was created",
        example="tico calendar status a1",
    ),
)
