"""Website tools: a bot drives the company's browser for one website tool, a verb at a time (docs/credential-vault.md
"Website logins", integrations/website.md).

Every verb is one call to `POST /api/v2/browser/commands`; the server picks the account and the computer whose Chrome
runs it (the browser host, else the bot's own computer). Nothing here talks to Chrome, a port or a local socket. A
refusal keeps the server's detail unchanged; `tico login` (logins.py) shares it.
"""

import base64
import os
import re
import time
from pathlib import Path

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import _key, _s
from clients.tico import APIError

SITE = _s("The website tool: a site such as app.example.com, a page URL on it, or the tool's name or id")
REF = _s("An element's ref from `tico browser read`, such as e12")
MAX_UPLOAD = 10 * 1024 * 1024  # the route's cap on an upload, before base64
SCROLL = 600  # pixels, about two-thirds of the browser's 900 px window


def command(api, args, verb, more=None, writes=False):
    """Run one verb on the site's browser: the server's answer, or its refusal in plain words."""
    body = {"site": args["site"], "verb": verb, "args": {k: v for k, v in (more or {}).items() if v is not None}}
    return api.post("browser/commands", body, key=_key(args) if writes else None)


def page_line(result):
    """`<title> — <url>`, the page a verb left the browser on."""
    title, url = result.get("title") or "", result.get("url") or ""
    return f"{title} — {url}" if title and url else title or url or "Done."


def _safe(text):
    """A file name piece made from a site or a page's file name: letters, digits, dots and dashes only."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-")[:80] or "file"


def _free(path):
    """`path`, or `name-1.ext`, `name-2.ext`, ... when it is taken: a file is never overwritten."""
    out, n = path, 1
    while out.exists():
        out, n = path.with_name(f"{path.stem}-{n}{path.suffix}"), n + 1
    return out


def _render_page(args, result):
    return result.get("text") or page_line(result)


def _render_path(args, result):
    return result["path"]


@tool(
    "tico_browser_open",
    "Open a page of a website tool in its browser, signed in as your account there (your own account, else the one "
    "shared with you). `url` must be on the tool's sites; the default is its first site's home page. Then read it with "
    "tico_browser_read.",
    {"site": SITE, "url": _s("The page to open, on the tool's sites")},
    required=("site",),
    writes=True,
)
def browser_open(api, args):
    return command(api, args, "open", {"url": args.get("url")}, writes=True)


@tool(
    "tico_browser_read",
    "The page the website tool's browser shows, as text: `<title> — <url>`, then its text in reading order and every "
    "control on its own line with a ref (`[e12] button \"Sign in\"`). Refs last until the next read or page change. "
    "A password box shows no value.",
    {"site": SITE},
    required=("site",),
)
def browser_read(api, args):
    return command(api, args, "read")


@tool(
    "tico_browser_click",
    "Click an element by its ref from tico_browser_read.",
    {"site": SITE, "ref": REF},
    required=("site", "ref"),
    writes=True,
)
def browser_click(api, args):
    return command(api, args, "click", {"ref": args["ref"]}, writes=True)


@tool(
    "tico_browser_type",
    "Type text into a box by its ref, replacing what is there; `submit` presses Enter after. Never a password or a "
    "2FA code: tico_login_fill types those.",
    {"site": SITE, "ref": REF, "text": _s("What to type"), "submit": {"type": "boolean", "description": "Press Enter"}},
    required=("site", "ref", "text"),
    writes=True,
)
def browser_type(api, args):
    more = {"ref": args["ref"], "text": args["text"], "submit": True if args.get("submit") else None}
    return command(api, args, "type", more, writes=True)


@tool(
    "tico_browser_press",
    "Press one key in the browser: Enter, Tab, Escape, Backspace, ArrowDown and the like.",
    {"site": SITE, "key": _s("The key, such as Enter")},
    required=("site", "key"),
    writes=True,
)
def browser_press(api, args):
    return command(api, args, "press", {"key": args["key"]}, writes=True)


@tool(
    "tico_browser_scroll",
    f"Scroll the page by `dy` pixels: down when positive, up when negative ({SCROLL} by default).",
    {"site": SITE, "dy": {"type": "integer", "description": f"Pixels down, negative for up (default {SCROLL})"}},
    required=("site",),
    writes=True,
)
def browser_scroll(api, args):
    dy = args.get("dy")
    return command(api, args, "scroll", {"dy": SCROLL if dy is None else int(dy)}, writes=True)


@tool(
    "tico_browser_back",
    "Go back one page in the browser.",
    {"site": SITE},
    required=("site",),
    writes=True,
)
def browser_back(api, args):
    return command(api, args, "back", writes=True)


@tool(
    "tico_browser_screenshot",
    "A picture of the page the browser shows, saved as a JPEG on this computer; the answer is its path. `out` is the "
    "file (default ./screenshots/<site>-<time>.jpg).",
    {"site": SITE, "out": _s("The JPEG file to write")},
    required=("site",),
    writes=True,  # it writes a file here
    local=True,
)
def browser_screenshot(api, args):
    result = command(api, args, "screenshot")
    image = base64.b64decode(result.get("image") or "")
    if not image:
        raise APIError("no_image", "The browser sent no picture; read the page with tico browser read instead.")
    out = Path(args.get("out") or "screenshots")
    if not args.get("out") or out.is_dir():
        out = _free(out / f"{_safe(args['site'])}-{time.strftime('%Y%m%d-%H%M%S')}.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(image)
    return {"path": os.path.abspath(out), **{k: result[k] for k in ("url", "title", "where") if k in result}}


@tool(
    "tico_browser_download",
    "Save the newest file the browser downloaded (click the site's download link first) to a folder on this computer; "
    "the answer is its path. `out` is the folder (default ./downloads). A file of the same name is never overwritten.",
    {"site": SITE, "out": _s("The folder to save it in")},
    required=("site",),
    writes=True,  # it writes a file here
    local=True,
)
def browser_download(api, args):
    result = command(api, args, "download")
    file = result.get("file") or {}
    if not file.get("data"):
        raise APIError("no_file", "The browser has no finished download; click the download link, then try again.")
    folder = Path(args.get("out") or "downloads")
    folder.mkdir(parents=True, exist_ok=True)
    out = _free(folder / _safe(Path(str(file.get("name") or "download")).name))
    out.write_bytes(base64.b64decode(file["data"]))
    return {"path": os.path.abspath(out), **{k: result[k] for k in ("url", "title", "where") if k in result}}


@tool(
    "tico_browser_upload",
    "Put a file from your checkout into a file box (by its ref) on the page, at most 10 MB.",
    {"site": SITE, "ref": REF, "path": _s("The file, in your checkout")},
    required=("site", "ref", "path"),
    writes=True,
    local=True,
)
def browser_upload(api, args):
    from clients import bot_files as BF

    try:
        # Any kind of file a site takes; a credential-like name, a link or a file outside the checkout is refused.
        path, _ = BF.local_file(BF.checkout_root(), args["path"], extra_types=(Path(args["path"]).suffix.lower(),))
    except BF.Refused as exc:
        raise APIError("bad_file", f"{exc}; upload a file from your checkout.", 422) from None
    if path.stat().st_size > MAX_UPLOAD:
        raise APIError("too_big", f"{path.name} is over 10 MB; upload a smaller file.", 422)
    data = base64.b64encode(path.read_bytes()).decode()
    more = {"ref": args["ref"], "name": path.name, "data": data}
    return command(api, args, "upload", more, writes=True) | {"name": path.name}


@tool(
    "tico_browser_close",
    "Let go of the browser for this site, so another teammate can use the account. The sign-in stays for next time.",
    {"site": SITE},
    required=("site",),
    writes=True,
)
def browser_close(api, args):
    return command(api, args, "close", writes=True)


@tool(
    "tico_browser_needs_you",
    "Ask a person to do one step in this site's browser that only a person may: a code sent by text or email, a "
    "captcha, a new-device check. It is an ask on your current task; their Done wakes you, then read the page again.",
    {"site": SITE, "line": _s("What they should do, in one sentence", maxLength=300)},
    required=("site", "line"),
    writes=True,
)
def browser_needs_you(api, args):
    body = {"site": args["site"], "line": args["line"]}
    return api.post("browser/needs-you", body, key=_key(args))


def _render_upload(args, result):
    return f"Uploaded {result.get('name') or 'the file'}."


def _render_needs_you(args, result):
    return "Asked a person; their Done wakes you. End this turn now."


# `tico browser ...` (clients/cli_spec.py): each leaf's flags come from its tool's schema.
CLI = (
    Group("browser", "a website tool's browser, signed in as your account there"),
    Command(
        "browser open",
        "open a page of a website tool",
        args=(Arg("url", positional=True, nargs="?"),),
        example='tico browser open slack https://app.slack.com/client',
        render=_render_page,
    ),
    Command(
        "browser read",
        "the page as text, with a ref for every control",
        example='tico browser read slack',
        render=_render_page,
    ),
    Command(
        "browser click", "click an element by its ref", example='tico browser click slack e12', render=_render_page
    ),
    Command(
        "browser type",
        "type text into a box by its ref; --submit presses Enter",
        example="tico browser type slack e4 'quarterly report' --submit",
        render=_render_page,
    ),
    Command(
        "browser press",
        "press one key (Enter, Tab, Escape, ...)",
        example='tico browser press slack Enter',
        render=_render_page,
    ),
    Command(
        "browser scroll",
        "scroll the page",
        args=(Arg("dy", metavar="N"),),
        example='tico browser scroll slack --dy 800',
        render=_render_page,
    ),
    Command("browser back", "go back one page", example='tico browser back slack', render=_render_page),
    Command(
        "browser screenshot",
        "save a picture of the page; prints its path",
        args=(Arg("out", metavar="PATH"),),
        example='tico browser screenshot slack --out page.png',
        render=_render_path,
    ),
    Command(
        "browser download",
        "save the newest download; prints its path",
        args=(Arg("out", metavar="DIR"),),
        example='tico browser download slack --out downloads',
        render=_render_path,
    ),
    Command(
        "browser upload",
        "put a file into a file box by its ref",
        example='tico browser upload slack e9 report.pdf',
        render=_render_upload,
    ),
    Command(
        "browser close",
        "let go of the browser for this site",
        example='tico browser close slack',
        render=lambda args, result: "Closed.",
    ),
    Command(
        "browser needs-you",
        "ask a person to do a step only a person may (a texted code, a captcha)",
        example="tico browser needs-you slack 'Type the code Slack texted you'",
        render=_render_needs_you,
    ),
)
