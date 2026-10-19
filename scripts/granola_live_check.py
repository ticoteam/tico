"""Opt-in live check: does Granola still answer in the shapes Tico's fixtures and parser expect?

    python scripts/granola_live_check.py [--record DIR]

GRANOLA_MCP_TOKEN, an access token for https://mcp.granola.ai/mcp (it lasts about an hour), checks the remote
MCP server Tico's Meetings page uses: initialize, tools/list, list_meetings and get_meetings for up to three
meetings, all read-only. GRANOLA_API_KEY checks the public API the runner importer uses (one page of notes).
Each part is skipped when its credential is not set, so with neither it prints SKIP and exits 0.

It never gates a release: run it by hand or on a schedule, and read a DRIFT as "update the fixtures in
backend/tests/fixtures/granola/ and the parser", not as a broken build.

--record DIR writes DIR/mcp_shapes.json in the fixture format, rebuilt rather than copied. Kept: each answer's
status, encoding and JSON or event-stream form; the protocol version, error codes, Retry-After seconds and schema limits;
tool and argument names Tico knows; and, row by row, the form of every meeting: XML-like or JSON (structured or as
text), which id, title, date, summary and link fields it has and whether each is a tag, an attribute (and its quote) or
a key, each attendee's form, the known_participants line forms and which private sections it has. Everything else is
made up: ids, names, emails, titles, dates, notes, cursors, descriptions and error wording.

It is a projection, not a full capture. Dropped: content blocks after the first, unknown tags, attributes and keys
(with anything nested in them), text after the notes, events after the 20th, tools after the 50th and people after the
20th in a meeting. A private section's text is replaced. An answer it cannot rebuild, or whose rebuild Tico would read
differently, is refused and nothing is written. Read the file before committing it all the same.

A probe Granola rate-limits is waited out once (its Retry-After, at most a minute); if it is throttled again, that
probe is reported INCOMPLETE and the run never says OK. The throttle's own shape is still checked.

Exit status: 0 when every probe's shape matches or the check is skipped, 1 on drift, 2 when part of the check could
not run (unreachable, credential refused, rate-limited twice, no meetings to read).
"""
import argparse
import json
import os
import re
import sys
import time
import uuid
from html import escape, unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.granola_mcp import MCP, GranolaError, GranolaMCP  # noqa: E402

PUBLIC_API = "https://public-api.granola.ai/v1"
KEPT_HEADERS = ("content-type", "content-encoding", "mcp-session-id", "retry-after")
RETRY_WAIT, RETRY_WAIT_MAX = 20, 60    # one wait-and-retry for a throttled probe, never longer than this
TRANSPORT = None                       # tests replace the network; None is the real one
SLEEP = time.sleep
RATE_LIMITS = ("rate_limit_http_429", "rate_limit_rpc_error", "rate_limit_tool_error")


def decoded(exchange):
    """The JSON-RPC message of an exchange, whether it came as JSON or as an event stream."""
    if "events" in exchange:
        return exchange["events"][0] if exchange["events"] else None
    return exchange.get("json")


def meetings_of(result, problems, name):
    try:
        value = GranolaMCP.content(result)
    except GranolaError:
        problems.append(f"{name}: Tico's parser reads no meetings from the result")
        return []
    rows = value.get("meetings") if isinstance(value, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) and r.get("id") for r in rows):
        problems.append(f"{name}: the parsed result has no list of meetings with ids")
        return []
    return rows


def shape_problems(name, exchange):
    """What in one recorded exchange no longer matches what Tico reads. An empty list means it matches."""
    problems = []
    status, headers = exchange.get("status"), {k.lower(): v for k, v in exchange.get("headers", {}).items()}
    if name in AUTH_EXCHANGES:
        body = exchange.get("json")
        if status >= 400 or not isinstance(body, dict):
            return [f"{name}: HTTP {status} without a JSON object"]
        missing = [key for key in AUTH_EXCHANGES[name] if not body.get(key)]
        return [f"{name}: missing {', '.join(missing)}"] if missing else []
    if name == "initialized":
        return [] if status in (200, 202, 204) else [f"{name}: HTTP {status}"]
    if name in RATE_LIMITS:
        message = decoded(exchange)
        if name == "rate_limit_http_429" and status != 429:
            problems.append(f"{name}: HTTP {status}, not 429")
        if not GranolaMCP.rate_limited(message):
            problems.append(f"{name}: Tico does not read it as a rate limit")
        if "retry-after" in headers and not re.fullmatch(r"\d+(\.\d+)?|\w{3}, .+ GMT", headers["retry-after"]):
            problems.append(f"{name}: Retry-After is neither seconds nor an HTTP date")
        return problems
    message = decoded(exchange)
    if status != 200 or not isinstance(message, dict) or not isinstance(message.get("result"), dict):
        return [f"{name}: HTTP {status} without a JSON-RPC result"]
    result = message["result"]
    if name == "initialize":
        if not isinstance(result.get("protocolVersion"), str):
            problems.append(f"{name}: no protocolVersion")
        if "mcp-session-id" not in headers:
            problems.append(f"{name}: no Mcp-Session-Id header")
    elif name == "tools_list":
        tools = {t.get("name"): t for t in result.get("tools", []) if isinstance(t, dict)}
        for tool in ("list_meetings", "get_meetings"):
            if tool not in tools:
                problems.append(f"{name}: no {tool} tool")
        ids = ((tools.get("get_meetings") or {}).get("inputSchema") or {}).get("properties", {})
        if tools.get("get_meetings") and not any(key in ids for key in ("meeting_ids", "ids", "note_ids")):
            problems.append(f"{name}: get_meetings takes no meeting_ids")
    elif name in ("list_meetings", "get_meetings", "get_meetings_event_stream"):
        rows = meetings_of(result, problems, name)
        if name != "list_meetings" and rows and not any(GranolaMCP.item(row).notes for row in rows):
            problems.append(f"{name}: no meeting has shared notes Tico would import")
    elif name == "transcript":
        try:
            value = GranolaMCP.content(result, allow_text=True)
        except GranolaError:
            value = None
        if not isinstance(value, dict) or not (value.get("transcript") or value.get("turns")):
            problems.append(f"{name}: no transcript text")
    elif name == "transcript_free_plan":
        if not result.get("isError"):
            problems.append(f"{name}: a free-plan refusal is no longer an isError result")
    return problems


AUTH_EXCHANGES = {"register": ("client_id",), "device_authorization": ("device_code", "user_code", "verification_uri"),
                  "token": ("access_token", "expires_in"), "authorization_server": ("token_endpoint",)}


def check_fixture(path):
    """Every problem in a fixture file, as 'exchange: problem' lines."""
    return check_fixture_exchanges(json.loads(Path(path).read_text())["exchanges"])


def check_fixture_exchanges(exchanges):
    return [problem for name, exchange in exchanges.items() for problem in shape_problems(name, exchange)]


# ---- live ----

def recorded(response):
    out = {"status": response.status_code,
           "headers": {k: v for k, v in response.headers.items() if k.lower() in KEPT_HEADERS}}
    if "text/event-stream" in response.headers.get("content-type", ""):
        events = []
        for event in response.text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            try:
                events.append(json.loads(data)) if data else None
            except ValueError:
                pass
        out["events"] = events
    elif response.content:
        try:
            out["json"] = response.json()
        except ValueError:
            out["text"] = response.text[:2000]
    return out


def retry_wait(response):
    try:
        return min(max(float(response.headers.get("Retry-After", RETRY_WAIT)), 0), RETRY_WAIT_MAX)
    except ValueError:
        return RETRY_WAIT


def live_mcp(token, timeout=45):
    """The answers seen, and the probes a rate limit kept from answering (their shapes were not checked)."""
    import httpx
    exchanges, session, incomplete = {}, {}, []
    with httpx.Client(timeout=timeout, follow_redirects=False, transport=TRANSPORT) as client:
        def rpc(name, method, params=None, notification=False):
            for attempt in range(2):
                headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream",
                           "MCP-Protocol-Version": "2025-03-26"}
                if session.get("id"):
                    headers["Mcp-Session-Id"] = session["id"]
                body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
                if not notification:
                    body["id"] = uuid.uuid4().hex
                response = client.post(MCP, headers=headers, json=body)
                if response.status_code in (401, 403):
                    raise PermissionError(f"{name}: Granola refused the token (HTTP {response.status_code})")
                session["id"] = response.headers.get("Mcp-Session-Id") or session.get("id")
                exchange = recorded(response)
                message = decoded(exchange)
                message = message if isinstance(message, dict) else {}
                result = message.get("result") if isinstance(message.get("result"), dict) else {}
                throttle = ("rate_limit_http_429" if response.status_code == 429 else
                            "rate_limit_rpc_error" if message.get("error") and GranolaMCP.rate_limited(message["error"]) else
                            "rate_limit_tool_error" if result.get("isError") and GranolaMCP.rate_limited(result) else None)
                if throttle is None:
                    exchanges[name] = exchange
                    return result
                exchanges[throttle] = exchange    # the throttle's own shape is checked too
                if attempt == 0:
                    SLEEP(retry_wait(response))
            incomplete.append(f"{name}: rate-limited twice, its answer was not checked")
            return None

        if rpc("initialize", "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                            "clientInfo": {"name": "tico-live-check", "version": "1"}}) is None:
            return exchanges, incomplete
        rpc("initialized", "notifications/initialized", notification=True)
        listed_tools = rpc("tools_list", "tools/list")
        if listed_tools is None:
            return exchanges, incomplete
        tools = {t.get("name"): t for t in listed_tools.get("tools", []) if isinstance(t, dict)}
        if "list_meetings" not in tools:
            return exchanges, incomplete
        schema = (tools["list_meetings"].get("inputSchema") or {}).get("properties", {})
        ranges = schema.get("time_range", {}).get("enum", [])
        listed = rpc("list_meetings", "tools/call", {"name": "list_meetings", "arguments": {
            "time_range": "last_30_days"} if "last_30_days" in ranges else {}})
        if listed is None:
            return exchanges, incomplete
        try:
            ids = [row["id"] for row in GranolaMCP.content(listed).get("meetings", []) if row.get("id")][:3]
            if not ids:
                incomplete.append("get_meetings: no meetings in the last 30 days, so no notes answer was checked")
        except (GranolaError, AttributeError, TypeError):
            ids = []    # the list's own shape check reports this as drift
        if ids and "get_meetings" in tools:
            rpc("get_meetings", "tools/call", {"name": "get_meetings", "arguments": {"meeting_ids": ids}})
    return exchanges, incomplete


def live_public_api(key, timeout=30):
    """Shape problems and incomplete probes for one page of the public API's notes."""
    import httpx
    with httpx.Client(timeout=timeout, transport=TRANSPORT) as client:
        for attempt in range(2):
            response = client.get(PUBLIC_API + "/notes", params={"page_size": 1},
                                  headers={"Authorization": "Bearer " + key})
            if response.status_code != 429:
                break
            if attempt == 0:
                SLEEP(retry_wait(response))
        else:
            return [], ["public API notes: rate-limited twice, its answer was not checked"]
    if response.status_code in (401, 403):
        raise PermissionError(f"public API: Granola refused the key (HTTP {response.status_code})")
    try:
        page = response.json()
    except ValueError:
        return [f"public API notes: HTTP {response.status_code} without JSON"], []
    if not isinstance(page, dict) or not isinstance(page.get("notes"), list):
        return ["public API notes: no notes list"], []
    problems = [] if "hasMore" in page else ["public API notes: no hasMore"]
    if any(not re.fullmatch(r"not_[A-Za-z0-9]{14}", str(row.get("id", ""))) for row in page["notes"]):
        problems.append("public API notes: a note id no longer looks like not_ + 14 characters")
    return problems, []


# ---- scrub ----
# A recording is rebuilt, not filtered: every value in it is either a fixed protocol literal from the lists below, a
# count or limit, or made-up wording. Nothing Granola sent is copied, so a field nobody listed cannot carry a person's
# text into a fixture. A shape that cannot be rebuilt this way is refused before anything is written.

class Unrecordable(Exception):
    """A fixed category name; never the provider's text."""


class WriteFailed(Exception):
    """The recording could not be saved: the error's kind and the staging file left behind, if it could not be removed."""


TOOLS = ("list_meetings", "get_meetings", "get_meeting_transcript", "get_account_info")
ARGUMENTS = ("since", "until", "start_date", "end_date", "after", "before", "created_after", "time_range",
             "custom_start", "custom_end", "date_range", "start", "end", "from", "to", "involvement", "captured_by_me",
             "cursor", "limit", "page_size", "meeting_ids", "ids", "note_ids", "meeting_id")
SCHEMA_TYPES = ("object", "array", "string", "integer", "number", "boolean", "null")
FORMATS = ("date", "date-time")
RANGES = ("custom", "last_30_days", "last_7_days", "this_week", "last_week", "this_month", "last_month", "today",
          "yesterday")
SUMMARIES = ("summary_markdown", "summary_text", "ai_summary", "enhanced_notes", "summary")
PRIVATE = ("private_notes", "raw_notes", "note_taker_notes")
ID_KEYS = ("id", "meeting_id", "note_id")
DATES = ("date", "created_at", "start_time")
ROW_KEYS = ID_KEYS + ("title",) + DATES + SUMMARIES + ("web_url",)
CONTENT_TYPES = ("application/json", "text/event-stream")
ENCODINGS = ("gzip", "br", "deflate", "identity")
RATE_LIMIT_TEXT = "Rate limit exceeded. Please slow down requests."
PLAN_TEXT = "Transcripts are only available to paid Granola tiers"
INTRO = "Synthetic provider introduction:\n"
NOTE = "## Notes\n- Synthetic note %d: budget < 15%% & plan approved"
DEPTH = 8
PEOPLE = 20


def scrub(exchanges):
    """The recording rebuilt from made-up values, keeping the shape Tico reads; raises Unrecordable."""
    ids = {}

    def synthetic_id(value):
        return ids.setdefault(str(value), "00000000-0000-4000-8000-%012d" % (len(ids) + 1))

    def schema(value, depth=0):
        if depth > DEPTH or not isinstance(value, dict):
            raise Unrecordable("tool schema too deep or not an object")
        out = {}
        kind = value.get("type")
        if kind is not None:
            kinds = kind if isinstance(kind, list) else [kind]
            out["type"] = kind if all(k in SCHEMA_TYPES for k in kinds) else "string"
        if isinstance(value.get("format"), str) and value["format"] in FORMATS:
            out["format"] = value["format"]
        names = {}
        if isinstance(value.get("properties"), dict):
            for index, (key, spec) in enumerate(value["properties"].items()):
                names[key] = key if key in ARGUMENTS else f"param_{index + 1}"
            out["properties"] = {names[k]: schema(v, depth + 1) for k, v in value["properties"].items()}
        if isinstance(value.get("required"), list):
            out["required"] = [names.get(k, "param_required") for k in value["required"] if isinstance(k, str)]
        if isinstance(value.get("enum"), list):
            out["enum"] = [v if v in RANGES else f"option_{i + 1}" for i, v in enumerate(value["enum"])]
        if isinstance(value.get("items"), dict):
            out["items"] = schema(value["items"], depth + 1)
        for key in ("minItems", "maxItems"):
            if isinstance(value.get(key), int) and not isinstance(value[key], bool) and 0 <= value[key] <= 10_000:
                out[key] = value[key]
        if isinstance(value.get("additionalProperties"), bool):
            out["additionalProperties"] = value["additionalProperties"]
        return out

    def tool_error(name, result):
        if GranolaMCP.rate_limited(result):
            text = RATE_LIMIT_TEXT
        elif name == "transcript" and re.search(r"paid|upgrade|business|enterprise", encode_text(result), re.I):
            text = PLAN_TEXT
        else:
            text = "Synthetic tool error."
        return {"isError": True, "content": [{"type": "text", "text": text}]}

    def has_notes(row):
        try:
            return GranolaMCP.item(row) is not None
        except GranolaError:      # a row without an id: Tico skips it, and so does the rebuilt one
            return False

    def form_of(result):
        if isinstance(result.get("structuredContent"), (dict, list)):
            return "structured", None
        blocks = [b for b in result.get("content", []) if isinstance(b, dict) and b.get("type") == "text"]
        if not blocks:
            raise Unrecordable("result without text or structured content")
        raw = blocks[0].get("text", "")
        if not isinstance(raw, str):
            raise Unrecordable("text block that is not text")
        try:
            json.loads(raw)
            return "json_text", raw
        except ValueError:
            return "xml_text", raw

    def date_like(value):
        if not isinstance(value, str) or not value:
            return value if value in (None, "") else "2026-10-01T16:00:00Z"
        return "2026-10-01T16:00:00Z" if re.match(r"\d{4}-\d{2}-\d{2}", value) else "Oct 1, 2026 4:00 PM"

    def note_of(value, index):
        if value is None or isinstance(value, str):
            return NOTE % index if value else value
        if isinstance(value, dict):
            return {k: note_of(v if isinstance(v, str) or v is None else "x", index)
                    for k, v in value.items() if k in ("markdown", "text")}
        raise Unrecordable("summary the recorder does not know")

    def projection(rows):
        """What Tico reads from a meeting list, with made-up values set aside: the rebuild must not change it."""
        return [(sorted(k for k in ROW_KEYS if row.get(k) is not None), has_notes(row),
                 [(bool(a.get("name")), bool(a.get("email"))) if isinstance(a, dict) else str
                  for a in (row.get("attendees") or row.get("participants") or [])[:PEOPLE]]) for row in rows]

    def meetings(result):
        form, raw = form_of(result)
        try:
            value = GranolaMCP.content(result)
        except GranolaError:
            raise Unrecordable("meetings answer Tico cannot parse") from None
        rows = value.get("meetings") if isinstance(value, dict) else None
        if not isinstance(rows, list) or len(rows) > 100 or not all(isinstance(r, dict) for r in rows):
            raise Unrecordable("meetings answer without a meeting list")
        cursor = "cursor-placeholder" if value.get("next_cursor") else None
        if form == "xml_text":
            made = {"content": [{"type": "text", "text": render_xml(raw, len(rows), cursor)}]}
        else:
            body = {"meetings": [json_row(i, row) for i, row in enumerate(rows, 1)],
                    **({"next_cursor": cursor} if cursor else {})}
            made = {"structuredContent": body} if form == "structured" else {
                "content": [{"type": "text", "text": json.dumps(body)}]}
        if projection(GranolaMCP.content(made)["meetings"]) != projection(rows):
            raise Unrecordable("meetings answer the rebuild would change")
        return made

    def json_row(index, row):
        out = {}
        for key, value in row.items():
            if key in ID_KEYS:
                out[key] = synthetic_id(value) if value not in (None, "") else value
            elif key == "title":
                out[key] = f"Meeting {index}" if value is not None else None
            elif key in DATES:
                out[key] = date_like(value)
            elif key in SUMMARIES:
                out[key] = note_of(value, index)
            elif key == "notes" and isinstance(value, dict):
                out[key] = {k: note_of(v, index) for k, v in value.items() if k in ("summary", "ai_summary")}
            elif key == "web_url":
                out[key] = "https://notes.granola.ai/d/synthetic" if value else value
            elif key in ("attendees", "participants") and isinstance(value, list):
                out[key] = [f"Person {p}" if isinstance(a, str) else
                            {k: f"Person {p}" if k == "name" else f"person{p}@example.com"
                             for k in ("name", "email") if isinstance(a, dict) and a.get(k)}
                            for p, a in enumerate(value[:PEOPLE], 1) if isinstance(a, (str, dict))]
        return out

    def children(body):
        """The direct child tags of a row, read as Tico's parser reads them: (name, attributes, inner or None)."""
        out, position = [], 0
        while match := re.search(r"<([\w:-]+)\b([^>]*)>", body[position:]):
            start = position + match.end()
            if match[0].rstrip().endswith("/>"):
                out.append((match[1], match[2].rstrip().rstrip("/"), None))
                position = start
                continue
            close = re.search(r"</" + re.escape(match[1]) + r"\s*>", body[start:])
            if close is None:
                break
            out.append((match[1], match[2], body[start:start + close.start()]))
            position = start + close.end()
        return out

    def attrs_of(text):
        return [(m[1], m[2], unescape(m[3])) for m in re.finditer(r'''([\w:-]+)\s*=\s*(["'])(.*?)\2''', text, re.S)]

    def made_up(key, value, index):
        if key in ID_KEYS:
            return synthetic_id(value) if value else ""
        if key == "title":
            return f"Meeting {index}"
        if key in DATES:
            return date_like(value)
        if key in SUMMARIES:
            return NOTE % index if value.strip() else ""
        return "https://notes.granola.ai/d/synthetic" if value.strip() else ""

    def render_attendees(inner, people):
        out = []
        for match in re.finditer(r"<attendee\b([^>]*?)(?:/\s*>|>(.*?)</attendee\s*>)", inner or "", re.S):
            if len(people) >= PEOPLE:
                break
            p = len(people) + 1
            people.append(p)
            values = {"name": f"Person {p}", "email": f"person{p}@example.com"}
            attrs = [(k, q, values[k] if v else "") for k, q, v in attrs_of(match[1]) if k in values]
            nested = [(k, values[k] if (v or "").strip() else "") for k, _, v in children(match[2] or "")
                      if k in values and k not in [a[0] for a in attrs]]
            head = "".join(f" {k}={q}{v}{q}" for k, q, v in attrs)
            if match[2] is None:
                out.append(f"<attendee{head}/>")
            elif nested or any(k == "name" and v for k, _, v in attrs) or not re.sub(r"<[^>]*>", "", match[2]).strip():
                out.append(f"<attendee{head}>" + "".join(f"<{k}>{v}</{k}>" for k, v in nested) + "</attendee>")
            else:
                out.append(f"<attendee{head}>{values['name']}</attendee>")
        return "<attendees>" + "".join(out) + "</attendees>"

    def render_known(inner, people):
        out = []
        for line in (inner or "").splitlines():
            if not line.strip() or len(people) >= PEOPLE:
                continue
            p = len(people) + 1
            people.append(p)
            line = unescape(line).strip()
            email = re.search(r"<([^<>\s]+@[^<>\s]+)>", line)
            name = re.sub(r"\s*\(note creator\)", "", line[:email.start()] if email else line, flags=re.I).strip()
            named = bool(re.split(r"(?:^|\s+)from\s+", name, maxsplit=1, flags=re.I)[0].strip())
            made = " ".join(part for part in (
                f"Person {p}" if named else "", "(note creator)" if re.search(r"\(note creator\)", line, re.I) else "",
                "from Company" if re.search(r"(?:^|\s)from\s", line, re.I) else "",
                f"<person{p}@example.com>" if email else "") if part)
            out.append("  " + made)
        return "<known_participants>\n" + "\n".join(out) + "\n  </known_participants>"

    def render_xml(raw, count, cursor):
        """Each row in the form it came in, filled with made-up values; Tico's own row split decides the rows."""
        start = re.search(r"<(meetings_data|meetings|notes|meeting|note|transcript)\b", raw)
        body = raw[start.start():]
        scan = re.sub(r"<(%s)\b[^>]*>.*?</\1\s*>" % "|".join(SUMMARIES + PRIVATE), lambda m: " " * len(m[0]), body,
                      flags=re.S)
        nodes = list(re.finditer(r'''<(meeting|note)\b((?:[^<>"']|"[^"]*"|'[^']*')*?)(/?)>''', scan, re.S))
        if len(nodes) != count:
            raise Unrecordable("meetings answer whose rows the recorder cannot find")
        out = [INTRO.rstrip()] if raw[:start.start()].strip() else []
        wrapper = re.match(r"<(meetings_data|meetings|notes)\b([^>]*)>", body)
        if wrapper:
            counted = any(k == "count" for k, _, _ in attrs_of(wrapper[2]))
            out.append(f"<{wrapper[1]}" + (f' count="{count}"' if counted else "") + ">")
        for index, node in enumerate(nodes, 1):
            end = nodes[index].start() if index < len(nodes) else len(body)
            close = re.search(r"</" + node[1] + r"\s*>", scan[node.end():end])
            if not node[3] and close is None:
                raise Unrecordable("meeting without its closing tag")
            head = "".join(f" {k}={q}{escape(made_up(k, v, index), quote=True)}{q}"
                           for k, q, v in attrs_of(node[2]) if k in ROW_KEYS)
            if node[3]:
                out.append(f"<{node[1]}{head}/>")
                continue
            people, parts, seen = [], [], set()
            for name, _, inner in children(body[node.end():node.end() + close.start()]):
                inner = inner or ""
                if name in ("attendees", "known_participants") and name in seen:
                    continue      # Tico reads only the first of each
                seen.add(name)
                if name in ROW_KEYS:
                    parts.append(f"  <{name}>{made_up(name, inner, index)}</{name}>")
                elif name == "attendees":
                    parts.append("  " + render_attendees(inner, people))
                elif name == "known_participants":
                    parts.append("  " + render_known(inner, people))
                elif name in PRIVATE:
                    nested = re.search(r"<(%s)\b" % "|".join(SUMMARIES), inner)
                    text = f"<{nested[1]}>Synthetic private note</{nested[1]}>" if nested else "Synthetic private note"
                    parts.append(f"  <{name}>{text}</{name}>")
            out.append(f"<{node[1]}{head}>\n" + "".join(p + "\n" for p in parts) + f"</{node[1]}>")
        if cursor:
            out.append(f"<next_cursor>{cursor}</next_cursor>")
        if wrapper:
            out.append(f"</{wrapper[1]}>")
        return "\n".join(out)

    def transcript(result):
        form, raw = form_of(result)
        try:
            value = GranolaMCP.content(result, allow_text=True)
        except GranolaError:
            raise Unrecordable("transcript Tico cannot parse") from None
        line = "Person 1: Synthetic line"
        if form == "structured":
            return {"structuredContent": {"transcript": line}}
        if form == "json_text":
            if not isinstance(value, dict) or not isinstance(value.get("transcript"), str):
                raise Unrecordable("transcript form the recorder does not know")
            return {"content": [{"type": "text", "text": json.dumps({"transcript": line})}]}
        if "<transcript" in raw:
            intro = INTRO if raw[:raw.index("<transcript")].strip() else ""
            text = intro + '<transcript meeting_id="%s">[00:00:15] %s</transcript>' % (synthetic_id("t"), line)
            return {"content": [{"type": "text", "text": text}]}
        return {"content": [{"type": "text", "text": line}]}

    def result_of(name, result):
        if not isinstance(result, dict):
            raise Unrecordable("result that is not an object")
        if result.get("isError"):
            return tool_error(name, result)
        if name == "initialize":
            version = result.get("protocolVersion")
            if not isinstance(version, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
                raise Unrecordable("initialize without a protocol version")
            return {"protocolVersion": version, "capabilities": {"tools": {}},
                    "serverInfo": {"name": "granola", "version": "0"}}
        if name == "tools_list":
            tools = [t for t in result.get("tools", []) if isinstance(t, dict)]
            return {"tools": [{"name": t.get("name") if t.get("name") in TOOLS else f"other_tool_{i + 1}",
                               "description": "Synthetic description.", "inputSchema": schema(t.get("inputSchema") or {})}
                              for i, t in enumerate(tools[:50])]}
        if name in ("list_meetings", "get_meetings", "get_meetings_event_stream"):
            return meetings(result)
        if name == "transcript":
            return transcript(result)
        raise Unrecordable("an answer the recorder does not know")

    def message_of(name, message):
        if not isinstance(message, dict):
            raise Unrecordable("message that is not an object")
        if "jsonrpc" not in message:
            if name == "rate_limit_http_429":
                return {"error": RATE_LIMIT_TEXT}
            raise Unrecordable("answer that is not JSON-RPC")
        out = {"jsonrpc": "2.0"}
        if "id" in message:
            out["id"] = "<request id>"
        if "error" in message:
            code = message["error"].get("code") if isinstance(message["error"], dict) else None
            out["error"] = {"code": code if isinstance(code, int) and not isinstance(code, bool) and -32768 <= code <= 599
                            else -32000,
                            "message": RATE_LIMIT_TEXT if GranolaMCP.rate_limited(message["error"]) else "Synthetic error."}
        elif "result" in message:
            out["result"] = result_of(name, message["result"])
        return out

    def headers_of(headers):
        out = {}
        for key, value in headers.items():
            key = key.lower()
            if key == "content-type":
                kind = next((t for t in CONTENT_TYPES if t in str(value).lower()), None)
                if kind:
                    out[key] = kind
            elif key == "content-encoding" and str(value).lower() in ENCODINGS:
                out[key] = str(value).lower()
            elif key == "mcp-session-id":
                out[key] = "session-placeholder"
            elif key == "retry-after":
                out[key] = str(value) if re.fullmatch(r"\d{1,5}", str(value)) else "5"
        return out

    out = {}
    for name, exchange in exchanges.items():
        status = exchange.get("status")
        if not isinstance(status, int) or not 100 <= status <= 599:
            raise Unrecordable("answer without an HTTP status")
        made = {"status": status, "headers": headers_of(exchange.get("headers") or {})}
        if "events" in exchange:
            made["events"] = [message_of(name, e) for e in exchange["events"][:20]]
        elif "json" in exchange:
            made["json"] = message_of(name, exchange["json"])
        elif "text" in exchange:
            raise Unrecordable("answer that is not JSON")
        out[name] = made
    return out


def encode_text(value):
    return json.dumps(value, default=str)


def write_recording(directory, exchanges):
    """Rebuild first, then replace the file in one step: a refused recording writes nothing."""
    made = json.dumps({"about": ["Recorded by scripts/granola_live_check.py --record and rebuilt from made-up values:",
                                 "each meeting keeps its form; the projection's limits are in the script's help."],
                       "exchanges": scrub(exchanges)}, indent=2) + "\n"
    directory = Path(directory)
    target = directory / "mcp_shapes.json"
    temporary = directory / ".mcp_shapes.json.partial"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary.write_text(made)
        os.replace(temporary, target)
    except BaseException as exc:
        try:
            temporary.unlink(missing_ok=True)
            left = None
        except OSError:
            left = temporary.name
        if isinstance(exc, OSError):
            raise WriteFailed(type(exc).__name__, left) from None
        raise
    return target


def main(argv=None):
    title, _usage, *details = __doc__.split("\n\n")
    parser = argparse.ArgumentParser(description=title, epilog="\n\n".join(details),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--record", metavar="DIR",
                        help="write answers rebuilt from made-up values to DIR/mcp_shapes.json (what it keeps and drops: below)")
    args = parser.parse_args(argv)
    token, key = os.environ.get("GRANOLA_MCP_TOKEN", "").strip(), os.environ.get("GRANOLA_API_KEY", "").strip()
    if not token and not key:
        print("SKIP: set GRANOLA_MCP_TOKEN and/or GRANOLA_API_KEY to check Granola's live answers")
        return 0
    problems, incomplete = [], []
    try:
        if token:
            exchanges, unchecked = live_mcp(token)
            incomplete.extend(unchecked)
            for name, exchange in exchanges.items():
                problems.extend(shape_problems(name, exchange))
            for name in ("initialize", "tools_list", "list_meetings"):
                if name not in exchanges and not any(item.startswith(name + ":") for item in unchecked):
                    problems.append(f"{name}: no answer recorded")
            if args.record:
                try:
                    out = write_recording(args.record, exchanges)
                    print(f"recorded {len(exchanges)} answers, rebuilt from made-up values, to {out}")
                except Unrecordable as exc:
                    print(f"CANNOT RECORD: {exc}; nothing was written")
                    return 2
                except WriteFailed as exc:
                    kind, left = exc.args
                    print(f"CANNOT RECORD: could not save the file ({kind}); "
                          + (f"remove {left} by hand, the old file is unchanged" if left else "nothing was written"))
                    return 2
                except Exception as exc:  # a recorder fault is never reported as Granola's
                    print(f"CANNOT RECORD: recorder error ({type(exc).__name__}); nothing was written")
                    return 2
        else:
            print("SKIP MCP: GRANOLA_MCP_TOKEN is not set")
        if key:
            found, unchecked = live_public_api(key)
            problems.extend(found)
            incomplete.extend(unchecked)
        else:
            print("SKIP public API: GRANOLA_API_KEY is not set")
    except PermissionError as exc:
        print(f"CANNOT CHECK: {exc}")
        return 2
    except Exception as exc:  # network errors: say which kind, never the provider's text
        print(f"CANNOT CHECK: Granola unreachable ({type(exc).__name__})")
        return 2
    for problem in problems:
        print("DRIFT " + problem)
    for item in incomplete:
        print("INCOMPLETE " + item)
    if problems:
        print(f"{len(problems)} shape change(s)")
        return 1
    if incomplete:
        print("CANNOT CHECK: part of the check did not finish")
        return 2
    print("OK: Granola's answers match the fixtures' shapes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
