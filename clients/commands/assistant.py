"""The person's Assistant, Needs you, the brief, updates and runs.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import json
import re
from pathlib import Path
from urllib.parse import quote

from clients.commands._registry import tool
from clients.commands._schemas import _key, _s


@tool("tico_assistant_read", "Read your private Assistant chat and its pending Decisions. Uses only your own room.", {})
def assistant_read(api, args):
    return api.get("assistant")


@tool(
    "tico_assistant_send",
    "Ask your private Assistant. Uses only your own room; Confirm cards still need your click in Tico.",
    {"text": _s("Your request")},
    required=("text",),
    writes=True,
)
def assistant_send(api, args):
    return api.post("assistant/messages", {"text": args["text"]}, key=_key(args))


# ----------------------------------------------------------------------------- updates
@tool(
    "tico_changelog_list",
    "Product announcements, with your own read state and full change bullets. "
    "Use unread=true to catch up since your last look; reading does not mark anything read. "
    "next_offset reads the next page; unread_count counts all unread product entries, independently of filters.",
    {
        "kind": _s("product (default), activity, or all", enum=["product", "activity", "all"]),
        "unread": {"type": "boolean", "description": "Only product changes you have not acknowledged"},
        "q": _s("Search titles and change bullets"),
        "offset": {"type": "integer", "minimum": 0, "description": "next_offset from the preceding page"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 250},
    },
)
def changelog_list(api, args):
    return api.get(
        "changelog",
        kind=args.get("kind") or "product",
        unread="true" if args.get("unread") else None,
        q=args.get("q"),
        offset=args.get("offset"),
        limit=args.get("limit"),
    )


@tool(
    "tico_changelog_mark_read",
    "Acknowledge only the product entry ids you have shown the user. "
    "Read status belongs to the signed-in person and is shared with the web app; other people are unaffected. "
    "Newer or unseen entries stay unread.",
    {
        "ids": {
            "type": "array",
            "maxItems": 250,
            "items": {"type": "string", "maxLength": 100},
            "description": "Product entry ids returned by tico_changelog_list",
        }
    },
    required=("ids",),
    writes=True,
)
def changelog_read(api, args):
    return api.post("changelog/read", {"ids": args["ids"]}, key=_key(args))


_SLIDE_LINES = lambda what, most: {"type": "array", "maxItems": most, "items": {"type": "string"}, "description": what}


_SLIDES = {
    "type": "object",
    "additionalProperties": False,
    "description": "Your week in review as five slides, swiped left to right. Tico adds your goal KPIs to the KPI slide.",
    "properties": {
        "goal": _s("One sentence, at most 30 words: the goal you work toward and where it stands"),
        "kpis": {
            "type": "array",
            "maxItems": 4,
            "description": "Up to four numbers that show the week",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "value"],
                "properties": {
                    "name": _s("What it counts"),
                    "value": _s("This week's value, as it should read"),
                    "unit": _s("Unit, like $, % or signups"),
                    "series": {
                        "type": "array",
                        "maxItems": 30,
                        "items": {"type": "number"},
                        "description": "Recent values, oldest first, drawn as a chart",
                    },
                    "note": _s("One short line on what moved it"),
                },
            },
        },
        "done": _SLIDE_LINES("What got done last week: 1-5 plain lines, at most 25 words each", 5),
        "focus": _SLIDE_LINES("Your focus next week: 1-3 lines", 3),
        "blockers": _SLIDE_LINES(
            "Your biggest blockers and who can clear them: 0-3 lines (empty when nothing blocks you)", 3
        ),
    },
    "required": ["goal", "done", "focus"],
}


@tool(
    "tico_update_create",
    "Post your daily update (or, on Friday, your week in review) when Tico asks "
    "for it. A daily is `body`: three markdown bullets of work in plain English and nothing else, no title, "
    "headings or task ids, at most 25 words a bullet and 90 in all, plus a `Blocked:` bullet for each blocker "
    "and a `Suggestion:` bullet for each suggestion you acted on. A week in review is `slides`: goal, "
    "kpis, done, focus, blockers. One of each a day; posting again replaces it. One that breaks the "
    "rules is posted with a `warning` saying how to write the next one; an empty one is refused. A delivery "
    "turn (learnings or suggestions only) posts nothing: report `learnings_saved`, `suggestions_seen` and "
    "`delivery_batch_id` only if you post. Nothing real to report (nothing finished, started, decided, or newly "
    "waiting or blocked)? Send `skip` true with no body or slides instead: nothing is posted.",
    {
        "body": _s("A daily: three lines, each starting with '- ', plus any `Blocked:` or `Suggestion:` lines"),
        "slides": _SLIDES,
        "kind": _s("daily or weekly; Tico picks from the day when omitted", enum=["daily", "weekly"]),
        "day": _s("YYYY-MM-DD, only when Tico asks you to redo a past day"),
        "skip": {
            "type": "boolean",
            "description": "Nothing real to report: nothing is posted, and the request closes as skipped",
        },
        "learnings_saved": {
            "type": "integer",
            "minimum": 0,
            "maximum": 15,
            "description": "How many of the learnings Tico handed you you kept",
        },
        "suggestions_seen": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
            "description": "The suggestions you acted on, one line each",
        },
        "delivery_batch_id": _s("The delivery this post answers, when Tico named one"),
    },
    writes=True,
)
def update_post(api, args):
    slides = args.get("slides")
    if args.get("slides_file"):
        slides = json.loads(Path(args["slides_file"]).read_text())
    if isinstance(slides, str):
        slides = json.loads(slides)
    fields = (
        ("body", args.get("body")),
        ("slides", slides),
        ("kind", args.get("kind")),
        ("day", args.get("day")),
        ("skip", True if args.get("skip") else None),
        ("learnings_saved", args.get("learnings_saved")),
        ("suggestions_seen", args.get("suggestions_seen")),
        ("delivery_batch_id", args.get("delivery_batch_id")),
    )
    out = api.post("updates", {k: v for k, v in fields if v is not None}, key=_key(args))
    if "update" not in out:
        return out  # held by an org rule: the hold, and whom to appeal to
    return {**out["update"], **({"rule_warning": out["rule_warning"]} if out.get("rule_warning") else {})}


@tool(
    "tico_update_redo",
    "Ask the bots again for a past day's update in the current shape, such as last "
    "Friday's week in review as slides (the owner). Every active bot with that update on, or only `bots`.",
    {
        "day": _s("YYYY-MM-DD"),
        "kind": _s("daily or weekly (default weekly)", enum=["daily", "weekly"]),
        "bots": {"type": "array", "items": {"type": "string"}, "description": "Only these bots"},
        "managers": {
            "type": "boolean",
            "description": "With bots: also the bots above them, so their roll-up is rewritten",
        },
    },
    required=("day",),
    writes=True,
)
def update_redo(api, args):
    fields = (
        ("day", args["day"]),
        ("kind", args.get("kind")),
        ("bots", args.get("bots") or None),
        ("managers", True if args.get("managers") else None),
    )
    return api.post("updates/redo", {k: v for k, v in fields if v is not None}, key=_key(args))


@tool(
    "tico_update_list",
    "Updates from bots and people, newest first, with your read state: what each did, does "
    "next and needs from you. `unread` for what you have not read; `missed` lists bots that did not report; "
    "`scope` direct for only your direct reports on the org chart.",
    {
        "kind": _s("daily or weekly", enum=["daily", "weekly"]),
        "bot": _s("One author's updates: a bot slug or human:<id>"),
        "scope": _s("all (default) or direct: only your direct reports", enum=["all", "direct"]),
        "unread": {"type": "boolean", "description": "Only what you have not read"},
        "before": _s("Older than this created time (paging)"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
)
def updates_list(api, args):
    return api.get(
        "updates",
        kind=args.get("kind"),
        bot=args.get("bot"),
        scope=args.get("scope"),
        unread=("true" if args.get("unread") else None),
        before=args.get("before"),
        limit=args.get("limit"),
    )


@tool(
    "tico_update_show",
    "One update in full with its thread: your replies and the bot's answers, and in `inputs` "
    "the reports' updates it was written from that you may read.",
    {"update": _s("Update id")},
    required=("update",),
)
def update_show(api, args):
    return api.get("updates/" + args["update"])


@tool(
    "tico_update_mark_read",
    "Mark updates read (or unread again) for you.",
    {
        "ids": {"type": "array", "items": {"type": "string"}, "description": "Update ids"},
        "all": {"type": "boolean", "description": "Every update of the last 30 days"},
        "read": {"type": "boolean", "description": "false marks them unread again (default true)"},
    },
    writes=True,
)
def update_read(api, args):
    return api.post(
        "updates/read",
        {"ids": args.get("ids") or [], "all": bool(args.get("all")), "read": args.get("read", True) is not False},
        key=_key(args),
    )


@tool(
    "tico_update_reply",
    "Reply to a bot's update: it shows under the update and goes to the bot's chat "
    "as a message, so the bot answers there.",
    {"update": _s("Update id"), "text": _s("Your reply")},
    required=("update", "text"),
    writes=True,
)
def update_reply(api, args):
    return api.post("updates/" + args["update"] + "/reply", {"text": args["text"]}, key=_key(args))


@tool(
    "tico_update_settings",
    "Turn a bot's daily or weekly update on or off (the bot, its operator, a manager or the owner).",
    {"bot": _s("Bot slug"), "daily": {"type": "boolean"}, "weekly": {"type": "boolean"}},
    required=("bot",),
    writes=True,
)
def update_settings(api, args):
    flag = lambda v: v if isinstance(v, bool) else str(v).lower() in ("on", "true", "1", "yes")
    return api.post(
        "bots/" + args["bot"] + "/updates",
        {k: flag(args[k]) for k in ("daily", "weekly") if args.get(k) is not None},
        key=_key(args),
    )


@tool(
    "tico_run_list",
    "A bot's recent runs, failed and expired ones included, each with its `state` (run.state), end_reason, last_event_at, stalled, tokens and "
    "estimated cost (`est_cost_usd`) and start/end load snapshots (`load_start`, `load_end`). Yours, and those of the bots that report to you.",
    {"bot": _s("Bot slug"), "since": _s("Window like `24h` or an ISO timestamp")},
    required=("bot",),
)
def turns(api, args):
    return api.get(f"bots/{args['bot']}/turns", since=args.get("since"))


@tool(
    "tico_run_export",
    "Owners and engineering bots: whether a run's raw provider stream was captured, and its size. "
    "Write the replay fixture with `tico run export <run> --fixture`. A bounded stderr tail is available even without a stream; streams require capture_stream on, "
    "kept a week.",
    {"run": _s("The run id, from tico_run_list")},
    required=("run",),
)
def run_export(api, args):
    return api.get(f"attempts/{args['run']}/capture", full="false")


def run_export_full(api, run):
    """The whole export, for the CLI to write to files; never returned into a bot's context."""
    return api.get(f"attempts/{run}/capture")


def write_fixture(found, parent):
    """A run export as a replay fixture folder: input.json, <provider>_transcript.ndjson and output.json."""
    folder = Path(parent) / ("replay-" + re.sub(r"[^A-Za-z0-9_-]", "", str(found["run"]["id"])))
    folder.mkdir(parents=True, exist_ok=True)
    provider = re.sub(r"[^a-z0-9_-]", "", str(found.get("provider") or "provider").lower()) or "provider"
    meta = {k: found.get(k) for k in ("run", "provider", "truncated", "dropped_bytes", "captured")}
    files = {
        "input.json": json.dumps({**found["input"], "source": meta}, indent=2, ensure_ascii=False) + "\n",
        f"{provider}_transcript.ndjson": found["transcript"].rstrip("\n") + "\n",
        "output.json": json.dumps(found["output"], indent=2, ensure_ascii=False) + "\n",
    }
    if found.get("stderr_tail"):
        files["stderr.log"] = found["stderr_tail"].rstrip("\n") + "\n"
    if found.get("failed_tools"):
        files["failed_tools.json"] = json.dumps(found["failed_tools"], indent=2, ensure_ascii=False) + "\n"
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return [str(folder / name) for name in files]


# ----------------------------------------------------------------------------- the Assistant
@tool(
    "tico_assistant_propose",
    "Assistant only: ask the person you are acting for to confirm something with "
    "side effects that matter: approving a Needs-you item, anything that leaves the team, "
    "spending, changing people, access or settings, archiving or deleting, activating a bot. Give the exact "
    "API operation; it is shown to them as a Confirm / Cancel card and runs only when they click, as them. "
    "Several actions go on ONE card, never one card each: path /api/v2/assistant/batch, body {\"actions\": "
    "[{\"path\": \"/api/v2/tasks/<id>/move\", \"body\": {\"to\": \"closed\", \"why\": \"<reason>\", \"version\": N}}, "
    "{\"path\": \"/api/v2/tasks/<id>/asks/<requirement_id>/answer\", \"body\": {\"value\": \"ok\" | \"no\" | "
    "\"<answer>\", \"note\": \"<their words>\", \"version\": N}}, {\"path\": \"/api/v2/tasks/<id>/comments\", "
    "\"body\": {\"text\": \"<their words>\"}}, ...]} (up to 100); N is the version tico_task_show printed. An OK goes "
    "on a card only when the person's message names it (approve, merge, publish, send, spend); 'clear my queue' never "
    "OKs anything. "
    "The Strategy Owner may propose one owner-confirmed org-chart change as POST /api/v2/seats/batch with body "
    "{\"seats\":[{\"title\":\"...\",\"description\":\"...\",\"reports_to\":\"...\",\"team\":\"...\"}]} . "
    "Use tico_org_models for published recommendations; no seat is added before the owner confirms. "
    "A task action or answer the person clearly asked for needs no card: pass on_behalf_of to that tool instead. "
    "Nothing here runs until they confirm.",
    {
        "summary": _s("One plain sentence saying what will happen if they confirm"),
        "method": {"enum": ["POST", "PUT", "PATCH", "DELETE"], "default": "POST"},
        "path": _s(
            "The API path, e.g. /api/v2/tasks/<id>/move, /api/v2/tasks/<id>/asks/<requirement_id>/answer or "
            "/api/v2/tasks/<id>/comments"
        ),
        "body": {"type": "object", "description": "The JSON body of that request"},
        "for_person": _s(
            "Only the Assistant's own background run: the person (human:<id>) whose Assistant chat gets the card"
        ),
    },
    required=("summary", "path"),
    writes=True,
)
def assistant_propose(api, args):
    sent = {
        "summary": args["summary"],
        "method": args.get("method") or "POST",
        "path": args["path"],
        "body": args.get("body") or {},
    }
    if args.get("for_person"):
        sent["for"] = args["for_person"]
    return api.post("assistant/actions", sent, key=_key(args))


@tool(
    "tico_assistant_morning",
    "The Assistant's one morning message to a person's Assistant chat, from its background "
    "run only (once a company day).",
    {
        "for_person": _s("The person (human:<id>) whose Assistant chat gets it"),
        "body": _s("The message, in Markdown, at most 2000 characters"),
    },
    required=("for_person", "body"),
    writes=True,
)
def assistant_morning(api, args):
    return api.post("assistant/morning", {"for": args["for_person"], "body": args["body"]}, key=_key(args))


@tool(
    "tico_assistant_withdraw",
    "Take back a Confirm card that still waits for the person (its id from "
    "tico_assistant_proposals): it is marked withdrawn and never runs, and the card owner's chat says who withdrew it, "
    "with the reason if you give one. Any teammate may withdraw; only the card's owner can confirm it.",
    {
        "id": _s("The proposal's id"),
        "reason": _s("Why, in one short sentence (optional; the person reads it in their chat)"),
    },
    required=("id",),
    writes=True,
)
def assistant_withdraw(api, args):
    return api.post(f"assistant/actions/{args['id']}/withdraw", {"reason": args.get("reason") or ""}, key=_key(args))


@tool(
    "tico_assistant_proposals",
    "The Confirm cards still waiting for their owner: id, owner, proposer, summary and what "
    "confirming does. Filter by proposer (botops, assistant) and/or owner (a person's id). Use it to find a card's id "
    "to withdraw it.",
    {"proposer": _s("botops or assistant (optional)"), "owner": _s("The card owner's id, e.g. human:ana (optional)")},
)
def assistant_proposals(api, args):
    query = {k: args[k] for k in ("proposer", "owner") if args.get(k)}
    return api.get("assistant/proposals", **query)


@tool(
    "tico_assistant_learn",
    "In a person's own chat: save one thing they taught you about how they like things ranked "
    "and summarised (\"launches matter more than hiring news\"), as one short line in their words, then tell them "
    "what you saved. It is theirs alone and shows on What it has learned, "
    "where they edit or delete it. The Strategy Owner "
    "in the owner's chat saves what the owner teaches it about the company with layer company.",
    {
        "text": _s("One short line, in their words"),
        "layer": {
            "enum": ["personal", "company"],
            "default": "personal",
            "description": "personal (theirs), or company (the Strategy Owner in the owner's chat only)",
        },
    },
    required=("text",),
    writes=True,
)
def assistant_learn(api, args):
    return api.post(
        "assistant/learned", {"layer": args.get("layer") or "personal", "text": args["text"]}, key=_key(args)
    )


@tool(
    "tico_assistant_learned",
    "What a person's chat bot has learned: their own layer, and for the owner the "
    "company layer (vision, priorities, what they taught the Strategy Owner about the company).",
    {},
)
def assistant_learned(api, args):
    return api.get("assistant/learned")


@tool(
    "tico_assistant_shared",
    "The people who chose you to view their Assistant chat, read only: each person's id, name, since when, "
    "your last read and whether what their Assistant learned about them is included. Nobody else's chat is readable.",
    {},
)
def assistant_shared(api, args):
    return api.get("assistant/shared")


@tool(
    "tico_assistant_shared_read",
    "Read one page of a person's Assistant chat that they chose to share with you: their messages, the "
    "Assistant's replies and its cards, newest page first (pass `before` = the reply's next_before for older). Read "
    "only: you cannot post, react or act on a card there. Each read is in the audit log and on their list.",
    {
        "person": _s("The person's id, e.g. human:ana (tico_assistant_shared lists them)"),
        "before": _s("next_before from the previous page, for older messages (optional)"),
    },
    required=("person",),
)
def assistant_shared_read(api, args):
    query = {"before": args["before"]} if args.get("before") else {}
    return api.get("assistant/shared/" + quote(str(args["person"]), safe=":"), **query)


# ----------------------------------------------------------------------------- a person's batch
# What needs a person, frozen, walked one item at a time; responses collected and applied together
# on commit (backend/batch.py). These are a person's tools: a bot calling them is refused.
@tool(
    "tico_brief",
    "Catch the person up in one call, for talking on the go: bots that are down "
    "(`alerts`), who is waiting on them and how many items (`lineup`), how many bots replied to "
    "something they said (`replies_waiting`), the latest things bots said to them (`said`, since "
    "`since`, default 12 hours) and how many bot tasks are stuck. Call it first in a voice "
    "session and again when they ask what's new; pass the previous reply's `now` as `since`.",
    {"since": _s("ISO date-time with timezone; only what bots said after it")},
)
def live_brief(api, args):
    return spoken(api.get("live/brief" + (f"?since={args['since']}" if args.get("since") else "")))


# What a voice assistant reads aloud: every token it reads is a delay before it speaks (so answers stay small). The full text stays one tico_task_show away.
SPOKEN_CLIP = {"answer": 1200, "question": 700, "body": 500, "payload": 500, "note": 200}


def _clip(text, n):
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def spoken(view):
    if not isinstance(view, dict):
        return view
    item = view.get("item")
    if isinstance(item, dict):
        item = {k: v for k, v in item.items() if k not in ("rank", "created_at")}
        clipped = False
        for field, n in SPOKEN_CLIP.items():
            if isinstance(item.get(field), str) and len(item[field]) > n:
                item[field], clipped = _clip(item[field], n), True
        if clipped and str(item.get("key", "")).startswith("task:"):
            item["more"] = "Clipped for speaking; tico_task_show has the full text."
        view = {**view, "item": item}
    if isinstance(view.get("lineup"), list):
        view = {**view, "lineup": [{**g, "top": _clip(g.get("top"), 90)} for g in view["lineup"]]}
    return view


@tool(
    "tico_needs_you_start",
    "Who needs the person, one bot at a time. Call it when they ask who needs them, "
    "what is next, or to go through their list. Starts the batch of the bot that most needs them "
    "(or resumes the batch in progress) and returns `lineup` (every bot waiting on them, most "
    "important first), the count and the first item. Walk it with tico_needs_you_next and "
    "tico_needs_you_respond, read the summary back, tico_needs_you_commit on a clear yes, then offer "
    "the commit's `up_next`. Everything the person says about a bot's items goes to that bot.",
    {
        "bot": _s(
            "Only this bot's items (a slug); omit for the bot that most needs them. The "
            "person's own tasks are not in a batch: tico_task_list lists them"
        ),
        "all": {"type": "boolean", "description": "Every item from every bot in one batch instead."},
        "fresh": {"type": "boolean", "description": "Drop the batch in progress and build a new list."},
    },
    writes=True,
)
def batch_start(api, args):
    if args.get("fresh"):
        open_ = (api.get("batch") or {}).get("batch")
        if open_:
            api.post("batch/" + open_["id"] + "/abandon", {}, key=_key({**args, "operation_id": None}))
    body = {} if args.get("all") else {"bot": args.get("bot") or "next"}
    return spoken(api.post("batch", body, key=_key(args)))


@tool(
    "tico_needs_you_next",
    "The next item of the batch; at the end, the summary to read back before committing.",
    {"batch": _s("The batch id")},
    required=("batch",),
    writes=True,
)
def batch_next(api, args):
    return spoken(api.post("batch/" + args["batch"] + "/next", {}, key=_key(args)))


@tool(
    "tico_needs_you_respond",
    "Record the person's response to the current item (or another, by number). Nothing is applied until commit.",
    {
        "batch": _s("The batch id"),
        "kind": _s(
            "decide | needs_info | instruct | rule | skip | later",
            enum=["decide", "needs_info", "instruct", "rule", "skip", "later"],
        ),
        "text": _s("The person's own words"),
        "decision": _s(
            "With decide: approve | decline | done | close | answer",
            enum=["approve", "decline", "done", "close", "answer"],
        ),
        "item": {"type": "integer", "description": "Another item's number"},
        "until": _s("With later: an ISO date-time"),
    },
    required=("batch", "kind"),
    writes=True,
)
def batch_respond(api, args):
    body = {k: args[k] for k in ("kind", "text", "decision", "item", "until") if args.get(k) not in (None, "")}
    return api.post("batch/" + args["batch"] + "/respond", body, key=_key(args))


@tool(
    "tico_needs_you_commit",
    "Apply every recorded response of the batch. Only after the person confirmed the "
    "summary. Decisions apply as the person; questions, instructions and rules go to the bot each item "
    "came from, whose reply comes back on those items. Returns `up_next`: the next bot to offer.",
    {"batch": _s("The batch id")},
    required=("batch",),
    writes=True,
)
def batch_commit(api, args):
    return api.post("batch/" + args["batch"] + "/commit", {}, key=_key(args))


@tool(
    "tico_needs_you_abandon",
    "Drop the batch in progress without applying anything. Its responses are lost; "
    "the items come back in the next batch.",
    {"batch": _s("The batch id")},
    required=("batch",),
    writes=True,
)
def batch_abandon(api, args):
    return api.post("batch/" + args["batch"] + "/abandon", {}, key=_key(args))


@tool(
    "tico_needs_you_show",
    "What is on the person's Needs you: the total, the number per kind (task: a task of theirs; ask: an OK or a "
    "question with their name; paused: a bot that cannot run until they fix it; mention: an @ of them by a person), "
    "and the items, oldest first, each with its task. Use it to see the pile before closing or stopping anything.",
    {
        "kind": {"type": "string", "enum": ["task", "ask", "paused", "mention"], "description": "Only this kind"},
        "limit": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "The items listed (default 20); the counts are of every item",
        },
    },
)
def needs_you_show(api, args):
    view = api.get("needs-you")
    items = view if isinstance(view, list) else (view or {}).get("items") or []
    by_kind = {}
    for item in items:
        by_kind[item.get("kind")] = by_kind.get(item.get("kind"), 0) + 1
    shown = [item for item in items if not args.get("kind") or item.get("kind") == args["kind"]]
    limit = 20 if args.get("limit") is None else args["limit"]
    return {"total": len(items), "by_kind": by_kind, "items": shown[:limit], "more": max(0, len(shown) - limit)}


@tool(
    "tico_assistant_change_list",
    "What the person's Assistant did for them, newest first (last 30 days by default): "
    "each change's `what` in one line, the `words` of the person it acted on, its `object` (task:, approval:, "
    "automation:, org_rule:, message:), and `undoable` with `why_not` when it cannot be taken back. Use it when the "
    "person asks what was done for them or wants something undone.",
    {
        "days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "How far back (default 30)"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Changes per page (default 50)"},
        "before": _s("The `next` of the page before, to read older changes"),
    },
)
def assistant_change_list(api, args):
    return api.get("assistant/changes", days=args.get("days"), limit=args.get("limit"), before=args.get("before"))


@tool(
    "tico_assistant_change_restore",
    "Undo one change the person's Assistant made for them (its id from "
    "tico_assistant_change_list): a task's status or owner goes back to what it was, an approval goes back to waiting "
    "for the person, an automation goes back to its earlier version, an org rule it added is retired. A message sent "
    "stays sent. Restoring one again changes nothing.",
    {"id": _s("The change's id from tico_assistant_change_list")},
    required=("id",),
    writes=True,
)
def assistant_change_restore(api, args):
    return api.post(f"assistant/changes/{args['id']}/restore", {}, key=_key(args))
