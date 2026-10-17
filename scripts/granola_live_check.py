"""Opt-in live check: does Granola still answer in the shapes Tico's fixtures and parser expect?

    python scripts/granola_live_check.py [--record DIR]

GRANOLA_MCP_TOKEN, an access token for https://mcp.granola.ai/mcp (it lasts about an hour), checks the remote
MCP server Tico's Meetings page uses: initialize, tools/list, list_meetings and get_meetings for up to three
meetings, all read-only. GRANOLA_API_KEY checks the public API the runner importer uses (one page of notes).
Each part is skipped when its credential is not set, so with neither it prints SKIP and exits 0.

It never gates a release: run it by hand or on a schedule, and read a DRIFT as "update the fixtures in
backend/tests/fixtures/granola/ and the parser", not as a broken build.

--record DIR writes DIR/mcp_shapes.json in the fixture format, rebuilt rather than copied: it keeps the shape of
each answer (status, encoding, JSON or event stream, XML-like or JSON notes, which tags and fields carry them, how
many meetings and people) and fills it with made-up ids, names, titles and notes. No value Granola sent is written. An
answer it cannot rebuild is refused and nothing is written. Read the file before committing it all the same.

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


TOOLS = ("list_meetings", "get_meetings", "get_meeting_transcript", "get_account_info")
ARGUMENTS = ("since", "until", "start_date", "end_date", "after", "before", "created_after", "time_range",
             "custom_start", "custom_end", "date_range", "start", "end", "from", "to", "involvement", "captured_by_me",
             "cursor", "limit", "page_size", "meeting_ids", "ids", "note_ids", "meeting_id")
SCHEMA_TYPES = ("object", "array", "string", "integer", "number", "boolean", "null")
FORMATS = ("date", "date-time")
RANGES = ("custom", "last_30_days", "last_7_days", "this_week", "last_week", "this_month", "last_month", "today",
          "yesterday")
SUMMARIES = ("summary_markdown", "summary_text", "ai_summary", "enhanced_notes", "summary")
WRAPPERS = ("meetings_data", "meetings", "notes")
CONTENT_TYPES = ("application/json", "text/event-stream")
ENCODINGS = ("gzip", "br", "deflate", "identity")
RATE_LIMIT_TEXT = "Rate limit exceeded. Please slow down requests."
PLAN_TEXT = "Transcripts are only available to paid Granola tiers"
INTRO = "Synthetic provider introduction:\n"
NOTE = "## Notes\n- Synthetic note %d: budget < 15%% & plan approved"
DEPTH = 8


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

    def meetings(result):
        form, raw = form_of(result)
        try:
            value = GranolaMCP.content(result)
        except GranolaError:
            raise Unrecordable("meetings answer Tico cannot parse") from None
        rows = value.get("meetings") if isinstance(value, dict) else None
        if not isinstance(rows, list) or len(rows) > 100 or not all(isinstance(r, dict) for r in rows):
            raise Unrecordable("meetings answer without a meeting list")
        made = []
        for index, row in enumerate(rows, 1):
            people = row.get("attendees") or row.get("participants") or []
            made.append({"id": synthetic_id(row.get("note_id") or row.get("id") or row.get("meeting_id") or index),
                         "title": f"Meeting {index}", "people": min(len(people) if isinstance(people, list) else 0, 20),
                         "notes": has_notes(row)})
        cursor = "cursor-placeholder" if isinstance(value, dict) and value.get("next_cursor") else None
        if form == "xml_text":
            text = render_xml(raw, made, cursor)
            return {"content": [{"type": "text", "text": text}]}
        summary = next((k for k in SUMMARIES if any(k in r for r in rows)), "summary")
        nested = any(isinstance(r.get(summary), dict) for r in rows)
        people_key = "participants" if any("participants" in r for r in rows) else "attendees"
        listed = [{"id": m["id"], "title": m["title"], "date": "2026-10-01T16:00:00Z",
                   people_key: [{"name": f"Person {p}", "email": f"person{p}@example.com"} for p in range(1, m["people"] + 1)],
                   **({summary: {"markdown": NOTE % i} if nested else NOTE % i} if m["notes"] else {})}
                  for i, m in enumerate(made, 1)]
        body = {"meetings": listed, **({"next_cursor": cursor} if cursor else {})}
        if form == "structured":
            return {"structuredContent": body}
        return {"content": [{"type": "text", "text": json.dumps(body)}]}

    def render_xml(raw, made, cursor):
        start = re.search(r"<(meetings_data|meetings|notes|meeting|note)\b", raw)
        wrapper = start[1] if start and start[1] in WRAPPERS else "meetings"
        element_titles = bool(re.search(r"<title\b", raw))
        quote = "'" if re.search(r"\btitle\s*=\s*'", raw) and not element_titles else '"'
        summary = next((k for k in SUMMARIES if re.search(r"<%s\b" % k, raw)), "summary")
        known = "<known_participants" in raw
        private = bool(re.search(r"<(private_notes|raw_notes|note_taker_notes)\b", raw))
        out = [INTRO] if start and raw[:start.start()].strip() else []
        out.append(f'<{wrapper} count="{len(made)}">')
        for i, m in enumerate(made, 1):
            title = "" if element_titles else f" title={quote}{m['title']}{quote}"
            out.append(f'<meeting id="{m["id"]}"{title} date="Oct 1, 2026 4:00 PM">')
            if element_titles:
                out.append(f"  <title>{m['title']}</title>")
            people = [(f"Person {p}", f"person{p}@example.com") for p in range(1, m["people"] + 1)]
            if people and known:
                out.append("  <known_participants>\n" + "\n".join(f"  {n} from Company <{e}>" for n, e in people)
                           + "\n  </known_participants>")
            elif people:
                out.append("  <attendees>" + "".join(f'<attendee name="{n}" email="{e}"/>' for n, e in people)
                           + "</attendees>")
            if m["notes"]:
                out.append(f"  <{summary}>\n{NOTE % i}\n</{summary}>")
            if private:
                out.append("  <private_notes><summary>Synthetic private note</summary></private_notes>")
            out.append("</meeting>")
        if cursor:
            out.append(f"<next_cursor>{cursor}</next_cursor>")
        out.append(f"</{wrapper}>")
        return "\n".join(out)

    def transcript(result):
        form, raw = form_of(result)
        try:
            GranolaMCP.content(result, allow_text=True)
        except GranolaError:
            raise Unrecordable("transcript Tico cannot parse") from None
        if form == "structured":
            return {"structuredContent": {"transcript": "Person 1: Synthetic line"}}
        if form == "xml_text" and "<transcript" in raw:
            intro = INTRO if raw[:raw.index("<transcript")].strip() else ""
            text = intro + '<transcript meeting_id="%s">[00:00:15] Person 1: Synthetic line</transcript>' % synthetic_id("t")
            return {"content": [{"type": "text", "text": text}]}
        return {"content": [{"type": "text", "text": "Person 1: Synthetic line"}]}

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
                                 "only the shape of Granola's answers is kept."], "exchanges": scrub(exchanges)},
                      indent=2) + "\n"
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "mcp_shapes.json"
    temporary = directory / ".mcp_shapes.json.partial"
    temporary.write_text(made)
    os.replace(temporary, target)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", metavar="DIR", help="write the scrubbed answers to DIR/mcp_shapes.json")
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
