#!/usr/bin/env python3
"""The company's one Slack connector for bots. It holds no Slack token: Tico does the Slack calls.

  connectors/slack.py doctor
  connectors/slack.py channels
  connectors/slack.py history --channel '#release_notes' --since yesterday --threads --format md
  connectors/slack.py post --channel '#agents' --text 'shipped 3 docs'
  connectors/slack.py dm --to someone@example.com --text 'the pricing doc is live'
  connectors/slack.py file --id F0123ABCD

Every call goes to Tico as this run's bot (HUB_API_URL and HUB_TOKEN), and Tico's Slack gateway, which holds the one
Slack token, reads and posts for it (backend/slack_bots.py). With the gateway off, Tico says what to start.

Tico checks every call, not this script: the bot's `tools:` entry for slack (`read` to read, `post` to post or DM, an
optional `channels:` list and `dms: false`), onboarding, the Slack channel list (Settings > Tools > Slack: readers and
posting), Slack Connect channels (refused), 4,000 characters at most, a DM only to a person at the company who may sign
in, the org's rules on what is sent, and screening on what is read. A post or DM shows in Tico too: a post in the bot's
room, a DM in its chat with the person.

Files people share show as `[file F...] name` lines in `history` and in a message routed from Slack. `file --id`
saves one into this user's own slack-files-<uid> folder under the temp folder (made 0700), named <id>-<name>, never
replacing a file.

Exit codes: 0 ok, 1 failure (Tico or Slack unreachable, the gateway off), 2 refused.
"""

import argparse, json, os, re, stat, sys, tempfile, time, unicodedata
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HUB = Path(__file__).resolve().parent.parent  # .../tico
MAX_FILE_BYTES = 50 * 1024 * 1024
FILES_DIR = Path(tempfile.gettempdir()) / (f"slack-files-{os.getuid()}" if hasattr(os, "getuid") else "slack-files")
MAX_NAME_BYTES = 200  # file systems refuse a name over 255 bytes
DEFAULT_TZ = "America/Los_Angeles"
SENT_WAIT = 20  # seconds a post or DM is followed until Slack has it
REFUSALS = {400, 403, 404, 409, 413, 422}
JSON_ERRORS = False  # set by --json / --format json


class Failure(Exception):
    def __init__(self, msg, hint=""):
        super().__init__(msg)
        self.msg, self.hint = msg, hint


class Refused(Failure):
    pass


# ---------------------------------------------------------------- Tico


def client(timeout=30):
    url, token = os.environ.get("HUB_API_URL"), os.environ.get("HUB_TOKEN")
    if not url or not token:
        raise Failure(
            "This run has no Tico credential (HUB_API_URL and HUB_TOKEN).",
            "Bots reach Slack through Tico: run this inside a bot's turn.",
        )
    if str(HUB) not in sys.path:
        sys.path.insert(0, str(HUB))
    from clients.tico import Client

    return Client(url, token, timeout=timeout, retries=1)


def failed(code, detail, status):
    kind = Refused if status in REFUSALS else Failure
    return kind(detail or code, "Try again shortly." if status in (429, 502, 504) else "")


def hub(method, path, body=None, query=None, timeout=30):
    """One call to Tico as this run; Tico's refusal becomes Refused (exit 2), anything else Failure (exit 1)."""
    from clients.tico import APIError

    api = client(timeout)
    try:
        return api.call(method, path, body, query=query)
    except APIError as e:
        raise failed(e.code, e.detail, e.status)


def hub_file(fid):
    """(bytes, file id, name, mimetype, screening line) of a shared Slack file, from Tico."""
    from clients.tico import http_failure

    api = client(150)
    request = urllib.request.Request(
        api.url + "/api/v2/slack/files/" + urllib.parse.quote(fid, safe=""),
        headers={"Authorization": "Bearer " + api.bearer(), "User-Agent": "tico-slack-connector/2"},
    )
    try:
        with api.opener.open(request, timeout=150) as response:
            raw, headers = response.read(MAX_FILE_BYTES + 1), response.headers
    except urllib.error.HTTPError as exc:
        with exc:
            e = http_failure(exc, "")
        raise failed(e.code, e.detail, e.status)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise Failure(f"Tico could not be reached: {type(exc).__name__}", "Try again shortly.")
    if len(raw) > MAX_FILE_BYTES:
        raise Refused(f"File {fid} is over the {MAX_FILE_BYTES} byte limit.")
    unq = lambda name: urllib.parse.unquote(headers.get(name) or "")
    return (
        raw,
        unq("X-Slack-File-Id") or fid,
        unq("X-Slack-File-Name"),
        unq("X-Slack-File-Type"),
        unq("X-Tico-Screening"),
    )


def until_sent(mid):
    """Follow a post or DM until Slack has it (or it was stopped), up to SENT_WAIT seconds."""
    state = {"state": "queued"}
    for _ in range(SENT_WAIT):
        state = hub("GET", f"slack/posts/{mid}")
        if state.get("state") not in ("queued", "ready", "sending", "rate_limited"):
            break
        time.sleep(1)
    if state.get("state") in ("failed", "canceled", "uncertain"):
        why = f"{state['state']}: {state.get('error') or 'no reason given'}"
        kind = Refused if state["state"] == "canceled" else Failure
        raise kind(f"Slack did not get message {mid} ({why}).", "It is still in Tico; the bot's room shows it.")
    return state


def held(answer):
    """A write the org's rules held: refused, with the hold's words."""
    if isinstance(answer, dict) and answer.get("held"):
        hold = answer["held"] if isinstance(answer["held"], dict) else {}
        raise Refused(
            "The org's rules held this, so nothing was sent: " + str(hold.get("reason") or hold.get("text") or ""),
            "A person can Send anyway from the hold, or put the draft on the task instead.",
        )


# ---------------------------------------------------------------- time (pure)


def zone(name):
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:
        raise Failure(f"unknown timezone {name!r}", "Use an IANA name like America/Los_Angeles.")


REL_RE = re.compile(r"^(\d+)\s*(m|min|mins|minutes|h|hr|hrs|hours|d|day|days|w|week|weeks)$")
REL_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def day_start(d, tz):
    """Midnight of a date in tz, DST-safe."""
    return datetime(d.year, d.month, d.day, tzinfo=tz)


def parse_when(text, tz, now):
    """'now' | '24h' | '90m' | 'today' | 'yesterday' | ISO date or datetime -> aware datetime."""
    s = (text or "").strip().lower()
    if not s:
        raise Failure("empty time value")
    if s == "now":
        return now
    if s == "today":
        return day_start(now.date(), tz)
    if s == "yesterday":
        return day_start(now.date() - timedelta(days=1), tz)
    m = REL_RE.match(s)
    if m:
        return now - timedelta(seconds=int(m.group(1)) * REL_UNITS[m.group(2)[0]])
    try:
        dt = datetime.fromisoformat((text or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise Failure(
            f"cannot read time {text!r}",
            "Use an ISO date/time (2026-09-01, 2026-09-01T09:00), a relative window "
            "(24h, 90m, 7d), 'today', or 'yesterday'.",
        )
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def time_range(since, until, tz_name=DEFAULT_TZ, now=None):
    """(start, end) as aware datetimes. 'yesterday' with no --until is the whole previous day."""
    tz = zone(tz_name)
    now = now.astimezone(tz) if now else datetime.now(tz)
    since = (since or "24h").strip()
    start = parse_when(since, tz, now)
    if until:
        end = parse_when(until, tz, now)
        if until.strip().lower() in ("yesterday", "today"):
            end = day_start(end.date() + timedelta(days=1), tz)  # inclusive of that whole day
    elif since.lower() == "yesterday":
        end = day_start(start.date() + timedelta(days=1), tz)
    else:
        end = now
    if end <= start:
        raise Failure("the time range is empty", "--until must be after --since.")
    return start, end


# ---------------------------------------------------------------- text (pure)


def _indent(text, pad):
    lines = (text or "").splitlines() or ["(no text)"]
    return [pad + ln if ln.strip() else pad.rstrip() for ln in lines]


def stamp(ts, tz):
    return datetime.fromtimestamp(float(ts or 0), tz).strftime("%Y-%m-%d %H:%M")


def render_md(sections, tz_name=DEFAULT_TZ):
    """sections: [{id, name, start, end, warning, messages:[{time, author, text, permalink, replies:[...]}]}]"""
    out = ["# Slack history", ""]
    for s in sections:
        title = f"#{s['name']} ({s['id']})" if s.get("name") else s["id"]
        msgs = s.get("messages") or []
        replies = sum(len(m.get("replies") or []) for m in msgs)
        out.append(f"## {title}")
        out.append(
            f"{s.get('start', '')} to {s.get('end', '')} ({tz_name}) - {len(msgs)} messages, {replies} thread replies"
        )
        out.append("")
        if s.get("warning"):
            out += [s["warning"], ""]
        if not msgs:
            out += ["Nothing in this range.", ""]
            continue
        for m in msgs:
            out.append(f"- {m.get('time', '')} {m.get('author', 'unknown')} - {m.get('permalink', '')}")
            out += _indent(m.get("text", ""), "  ")
            for r in m.get("replies") or []:
                out.append(f"  - {r.get('time', '')} {r.get('author', 'unknown')} (reply) - {r.get('permalink', '')}")
                out += _indent(r.get("text", ""), "    ")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def one_line(text):
    """One printable line: whitespace runs become one space; control and format characters (NUL, bidi
    overrides) are dropped."""
    text = "".join(c for c in str(text or "") if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf"))
    return " ".join(text.split())


def trim_bytes(text, limit):
    return text.encode()[: max(limit, 0)].decode(errors="ignore")


def file_name(fid, name):
    """`<file id>-<name>`: the sharer picks the name, so it never names an existing file on its own.
    At most MAX_NAME_BYTES, keeping a short extension."""
    name = one_line(Path(str(name or "").replace("\\", "/")).name)
    fid = re.sub(r"[^A-Za-z0-9]", "", str(fid or ""))[:32] or "F"
    if name in ("", ".", ".."):
        return fid
    full = f"{fid}-{name}"
    if len(full.encode()) <= MAX_NAME_BYTES:
        return full
    stem, suffix = os.path.splitext(full)
    if len(suffix.encode()) > 16:
        stem, suffix = full, ""
    return trim_bytes(stem, MAX_NAME_BYTES - len(suffix.encode())).rstrip(" .") + suffix


def files_dir(path):
    """The default download folder, made 0700 if missing; refused unless it is this user's own real folder."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as e:
        raise Failure(f"Could not create {path}: {e.strerror or e}", "Pass --out a folder you own.")
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
        raise Refused(
            f"{path} is not a folder this user owns; nothing was saved.", "Remove it, or pass --out a folder you own."
        )
    if hasattr(os, "getuid") and stat.S_IMODE(st.st_mode) & 0o077:
        os.chmod(path, 0o700)
    return path


def save_new(path, raw, folder):
    """Write a new file and never replace or follow one: O_EXCL|O_NOFOLLOW; in a folder, -2, -3 on a clash."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    stem, suffix = path.stem, path.suffix
    for n in range(1, 100 if folder else 2):
        target = path if n == 1 else path.with_name(f"{stem}-{n}{suffix}")
        try:
            fd = os.open(target, flags, 0o600)
        except FileExistsError:
            continue
        except (OSError, ValueError) as e:
            why = getattr(e, "strerror", None) or e
            raise Failure(
                f"Could not save {one_line(target)}: {why}; nothing was saved.",
                "Pass --out a shorter or plainer file name.",
            )
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return target
    raise Refused(f"{path} already exists; nothing was saved.", "Pass --out a new file name or a folder.")


# ---------------------------------------------------------------- commands


def emit(args, row, text):
    print(json.dumps({"ok": True, **row}, indent=2) if args.json else text)
    return 0


def cmd_doctor(args):
    me = hub("GET", "me")
    listed = hub("GET", "slack/channels")
    state = listed.get("gateway") or "off"
    rows = listed.get("channels") or []
    emit(
        args,
        {"actor": me.get("actor"), "gateway": state, "channels": len(rows)},
        f"as        {me.get('actor')}\ngateway   {state}\nchannels  {len(rows)} on the list (connectors/slack.py channels)",
    )
    if state != "connected":
        raise Failure(
            f"Tico's Slack gateway is {state}, so bots cannot reach Slack.",
            "Start it: TICO_SLACK_GATEWAY_ENABLED=1 and the slack profile in COMPOSE_PROFILES, with the tokens in "
            "Tools > Slack (docs/slack-gateway.md).",
        )
    return 0


def cmd_channels(args):
    rows = hub("GET", "slack/channels").get("channels") or []
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        readers = ", ".join(r.get("readers") or []) or "-"
        print(f"{r.get('channel', ''):<32} post {'on' if r.get('post') else 'off':<4} readers {readers}")
        if r.get("note"):
            print(f"  {one_line(r['note'])}")
    print(f"{len(rows)} channels on the list (Settings > Tools > Slack).")
    return 0


def cmd_history(args):
    refs = [r.strip() for chunk in args.channel for r in str(chunk).split(",") if r.strip()]
    tz = zone(args.tz)
    start, end = time_range(args.since, args.until, args.tz)
    sections = []
    for ref in refs:
        query = {"channel": ref, "oldest": f"{start.timestamp():.6f}", "latest": f"{end.timestamp():.6f}"}
        if args.threads:
            query["threads"] = "true"
        answer = hub("GET", "slack/history", query=query, timeout=90)
        for m in answer.get("messages") or []:
            m["time"] = stamp(m.get("ts"), tz)
            for r in m.get("replies") or []:
                r["time"] = stamp(r.get("ts"), tz)
        sections.append(
            {
                "id": answer.get("id") or ref,
                "name": answer.get("name") or "",
                "start": start.strftime("%Y-%m-%d %H:%M"),
                "end": end.strftime("%Y-%m-%d %H:%M"),
                "messages": answer.get("messages") or [],
                **({"warning": answer["warning"]} if answer.get("warning") else {}),
            }
        )
    if args.format == "json":
        print(json.dumps({"ok": True, "tz": args.tz, "channels": sections}, indent=2))
    else:
        print(render_md(sections, args.tz), end="")
    return 0


def text_of(args):
    return sys.stdin.read() if args.text == "-" else args.text


def cmd_post(args):
    body = {"channel": args.channel, "text": text_of(args), **({"thread_ts": args.thread} if args.thread else {})}
    answer = hub("POST", "slack/posts", body)
    held(answer)
    state = until_sent(answer["message_id"])
    where = f"#{answer.get('name') or answer.get('channel')}"
    done = f"posted to {where}: {state['permalink']}" if state.get("permalink") else f"queued for {where}"
    return emit(args, {**answer, **state}, done)


def cmd_dm(args):
    to = [r.strip() for chunk in args.to for r in str(chunk).split(",") if r.strip()]
    if len(to) != 1:
        raise Refused("A DM goes to one person.", "Send one DM per person, or post in a listed channel.")
    answer = hub("POST", "slack/dms", {"to": to[0], "text": text_of(args)})
    held(answer)
    state = until_sent(answer["message_id"])
    done = f"sent a DM to {answer.get('to')}: {state['permalink']}" if state.get("permalink") else "queued"
    return emit(args, {**answer, **state}, done)


def cmd_file(args):
    raw, fid, name, mimetype, warning = hub_file(args.id.strip())
    out = Path(args.out).expanduser() if args.out else FILES_DIR
    folder = not args.out or out.is_dir() or str(args.out).endswith(("/", os.sep))
    if folder:
        out = out / file_name(fid, name)
    if args.out:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as e:
            raise Failure(f"Could not create {one_line(out.parent)}: {getattr(e, 'strerror', None) or e}")
    else:
        files_dir(FILES_DIR)
    out = save_new(out, raw, folder)
    row = {"id": fid, "name": name, "mimetype": mimetype, "bytes": len(raw), "path": str(out)}
    if warning:
        row["warning"] = warning
    saved = (
        f"saved {one_line(name or fid)} ({one_line(mimetype) or 'unknown type'}, {len(raw)} bytes) to {one_line(out)}"
    )
    return emit(args, row, (warning + "\n" if warning else "") + saved)


# ---------------------------------------------------------------- cli


def build_parser():
    p = argparse.ArgumentParser(
        prog="slack.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--json", action="store_true", help="machine-readable output and errors")
        # Older instructions name the bot; Tico knows it from the run's token, so this is accepted and not used.
        sp.add_argument("--as", dest="as_slug", default=None, help=argparse.SUPPRESS)
        return sp

    common(sub.add_parser("doctor", help="is Tico's Slack gateway up, and who am I")).set_defaults(func=cmd_doctor)
    common(sub.add_parser("channels", help="the Slack channel list bots follow")).set_defaults(func=cmd_channels)

    h = common(sub.add_parser("history", help="read a channel over a time range"))
    h.add_argument(
        "--channel", action="append", required=True, help="id or #name; repeat or comma-separate for several"
    )
    h.add_argument("--since", default="24h", help="ISO time, 24h/7d/90m, today, or yesterday")
    h.add_argument("--until", default=None, help="ISO time; defaults to now")
    h.add_argument("--tz", default=DEFAULT_TZ, help=f"IANA timezone (default {DEFAULT_TZ})")
    h.add_argument("--threads", action="store_true", help="also fetch every thread's replies")
    h.add_argument("--format", choices=["md", "json"], default="md")
    h.set_defaults(func=cmd_history)

    o = common(sub.add_parser("post", help="post in a listed channel"))
    o.add_argument("--channel", required=True, help="id or #name on the Slack channel list")
    o.add_argument("--text", required=True, help="message text, or - to read stdin")
    o.add_argument("--thread", default=None, help="parent ts, to reply in a thread")
    o.set_defaults(func=cmd_post)

    m = common(sub.add_parser("dm", help="DM a person at the company"))
    m.add_argument("--to", action="append", required=True, help="work email, Slack @handle or U-id")
    m.add_argument("--text", required=True, help="message text, or - to read stdin")
    m.set_defaults(func=cmd_dm)

    f = common(sub.add_parser("file", help="download a file someone shared (a screenshot, a log)"))
    f.add_argument("--id", required=True, help="the file id, F..., from a `[file F...]` line")
    f.add_argument(
        "--out",
        default=None,
        help=f"a new file path, or a folder to save it in as <id>-<name> (default {FILES_DIR}); "
        "an existing file is never replaced",
    )
    f.set_defaults(func=cmd_file)
    return p


def main(argv=None):
    global JSON_ERRORS
    args = build_parser().parse_args(argv)
    JSON_ERRORS = bool(getattr(args, "json", False) or getattr(args, "format", "") == "json")
    try:
        return args.func(args)
    except Refused as e:
        report(e, "refused")
        return 2
    except Failure as e:
        report(e, "failed")
        return 1


def report(e, kind):
    if JSON_ERRORS:
        print(json.dumps({"ok": False, "kind": kind, "error": e.msg, "hint": e.hint}, indent=2))
    else:
        print(f"{kind}: {e.msg}", file=sys.stderr)
        if e.hint:
            print(f"  {e.hint}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
