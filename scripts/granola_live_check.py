"""Opt-in live check: does Granola still answer in the shapes Tico's fixtures and parser expect?

    python scripts/granola_live_check.py [--record DIR]

GRANOLA_MCP_TOKEN, an access token for https://mcp.granola.ai/mcp (it lasts about an hour), checks the remote
MCP server Tico's Meetings page uses: initialize, tools/list, list_meetings and get_meetings for up to three
meetings, all read-only. GRANOLA_API_KEY checks the public API the runner importer uses (one page of notes).
Each part is skipped when its credential is not set, so with neither it prints SKIP and exits 0.

It never gates a release: run it by hand or on a schedule, and read a DRIFT as "update the fixtures in
backend/tests/fixtures/granola/ and the parser", not as a broken build.

--record DIR writes the answers it saw to DIR/mcp_shapes.json in the fixture format, scrubbed: ids, titles,
names, emails and notes become placeholders and no token or cookie is written. Read the file before committing it.

Exit status: 0 when the shapes match or the check is skipped, 1 on drift, 2 when Granola cannot be reached or
refuses the credential.
"""
import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.granola_mcp import MCP, GranolaError, GranolaMCP  # noqa: E402

PUBLIC_API = "https://public-api.granola.ai/v1"
KEPT_HEADERS = ("content-type", "content-encoding", "mcp-session-id", "retry-after")
SECRETS = ("access_token", "refresh_token", "device_code", "id_token", "client_id", "id")
PERSONAL = ("name", "title", "summary", "summary_markdown", "summary_text", "notes", "private_notes", "transcript",
            "speaker", "company", "email")
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
    exchanges = json.loads(Path(path).read_text())["exchanges"]
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


def live_mcp(token, timeout=45):
    import httpx
    exchanges, session = {}, {}
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        def rpc(name, method, params=None, notification=False):
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
            if response.status_code == 429:
                name = "rate_limit_http_429"
            elif message.get("error") and GranolaMCP.rate_limited(message["error"]):
                name = "rate_limit_rpc_error"
            elif result.get("isError") and GranolaMCP.rate_limited(result):
                name = "rate_limit_tool_error"
            exchanges[name] = exchange
            return result

        rpc("initialize", "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                         "clientInfo": {"name": "tico-live-check", "version": "1"}})
        rpc("initialized", "notifications/initialized", notification=True)
        tools = {t.get("name"): t for t in rpc("tools_list", "tools/list").get("tools", []) if isinstance(t, dict)}
        if "list_meetings" not in tools:
            return exchanges
        schema = (tools["list_meetings"].get("inputSchema") or {}).get("properties", {})
        ranges = schema.get("time_range", {}).get("enum", [])
        listed = rpc("list_meetings", "tools/call", {"name": "list_meetings", "arguments": {
            "time_range": "last_30_days"} if "last_30_days" in ranges else {}})
        try:
            ids = [row["id"] for row in GranolaMCP.content(listed).get("meetings", []) if row.get("id")][:3]
        except (GranolaError, AttributeError, TypeError):
            ids = []
        if ids and "get_meetings" in tools:
            rpc("get_meetings", "tools/call", {"name": "get_meetings", "arguments": {"meeting_ids": ids}})
    return exchanges


def live_public_api(key, timeout=30):
    import httpx
    response = httpx.get(PUBLIC_API + "/notes", params={"page_size": 1}, timeout=timeout,
                         headers={"Authorization": "Bearer " + key})
    if response.status_code in (401, 403):
        raise PermissionError(f"public API: Granola refused the key (HTTP {response.status_code})")
    if response.status_code == 429:
        return []    # throttled: nothing to say about the shape
    try:
        page = response.json()
    except ValueError:
        return [f"public API notes: HTTP {response.status_code} without JSON"]
    if not isinstance(page, dict) or not isinstance(page.get("notes"), list):
        return ["public API notes: no notes list"]
    problems = [] if "hasMore" in page else ["public API notes: no hasMore"]
    if any(not re.fullmatch(r"not_[A-Za-z0-9]{14}", str(row.get("id", ""))) for row in page["notes"]):
        problems.append("public API notes: a note id no longer looks like not_ + 14 characters")
    return problems


# ---- scrub ----

def scrub(exchanges):
    """Placeholders for everything personal; structure, tags, attribute names and provider wording are kept."""
    ids, people = {}, {}

    def placeholder_id(match):
        return ids.setdefault(match[0], "00000000-0000-4000-8000-%012d" % (len(ids) + 1))

    def person(match):
        return people.setdefault(match[0].lower(), "person%d@example.com" % (len(people) + 1))

    def text(value):
        value = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", placeholder_id, value)
        value = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", person, value)
        value = re.sub(r'\btitle="[^"]*"', 'title="Meeting"', value)
        value = re.sub(r"<(summary|notes|private_notes|transcript)([^>]*)>.*?</\1>",
                       lambda m: f"<{m[1]}{m[2]}>Scrubbed {m[1]}</{m[1]}>", value, flags=re.S)
        value = re.sub(r"(<known_participants>).*?(</known_participants>)",
                       lambda m: m[1] + "\n  Person (note creator) from Company <person@example.com>\n  " + m[2],
                       value, flags=re.S)
        return value

    def walk(value, key=None, meetings=True):
        if isinstance(value, dict):
            return {k: walk(v, k, meetings) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, None, meetings) for v in value]
        if isinstance(value, str):
            if key in SECRETS:
                return f"<{key}>"
            if key and key.lower() == "mcp-session-id":
                return "session-placeholder"
            if meetings and key in PERSONAL:
                return f"Scrubbed {key}"
            return text(value)
        return value

    # Tool and server descriptions name no one; everywhere else a name, title or note is a person's.
    return {name: walk(exchange, meetings=name not in ("initialize", "tools_list")) for name, exchange in exchanges.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", metavar="DIR", help="write the scrubbed answers to DIR/mcp_shapes.json")
    args = parser.parse_args(argv)
    token, key = os.environ.get("GRANOLA_MCP_TOKEN", "").strip(), os.environ.get("GRANOLA_API_KEY", "").strip()
    if not token and not key:
        print("SKIP: set GRANOLA_MCP_TOKEN and/or GRANOLA_API_KEY to check Granola's live answers")
        return 0
    problems = []
    try:
        if token:
            exchanges = live_mcp(token)
            for name, exchange in exchanges.items():
                problems.extend(shape_problems(name, exchange))
            for name in ("initialize", "tools_list", "list_meetings"):
                if name not in exchanges:
                    problems.append(f"{name}: no answer recorded")
            if args.record:
                Path(args.record).mkdir(parents=True, exist_ok=True)
                out = Path(args.record) / "mcp_shapes.json"
                out.write_text(json.dumps({"about": ["Recorded by scripts/granola_live_check.py --record; scrubbed."],
                                           "exchanges": scrub(exchanges)}, indent=2) + "\n")
                print(f"recorded {len(exchanges)} answers to {out}; read it before committing")
        else:
            print("SKIP MCP: GRANOLA_MCP_TOKEN is not set")
        if key:
            problems.extend(live_public_api(key))
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
    print("OK: Granola's answers match the fixtures' shapes" if not problems else f"{len(problems)} shape change(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
