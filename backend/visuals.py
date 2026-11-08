"""Visuals in replies (Dev 245, Visual replies 1/4): what may show inline, the page clean-up done when a visual is
attached, and the signed, cookie-free address a visual is shown from.

A visual is a normal versioned file on its task or conversation. A message stores the file, the version it was posted
with and a title (backend/app.py attach_visuals). Readers get a fresh `src` on /visuals/<token>: the token names the
reader, the file, the version and an expiry, and every request on it (each range request too) checks again that the
reader can still open the file. Nothing on that route reads or sets the app's cookies."""
import base64
import errno
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import stat
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .live_blocks import LIVE_ATTRS, fill as fill_live, has_live

# What may show inline, by stored type; anything else, or bigger, is a normal file chip.
KINDS = {"image/png": "image", "image/jpeg": "image", "image/gif": "image", "image/webp": "image",
         "image/svg+xml": "svg", "text/html": "page",
         "video/mp4": "video", "video/webm": "video", "video/quicktime": "video", "video/x-m4v": "video",
         "audio/mpeg": "audio", "audio/mp4": "audio", "audio/x-m4a": "audio", "audio/wav": "audio",
         "audio/x-wav": "audio", "audio/wave": "audio", "audio/vnd.wave": "audio", "audio/ogg": "audio",
         "audio/aac": "audio"}
# Python 3.15's mimetypes names .wav audio/vnd.wave; browsers know audio/wav. A type the server picks is stored as the
# common name, and versions already stored under the other still show inline (KINDS above).
ALIASES = {"audio/vnd.wave": "audio/wav"}
MB = 1024 * 1024
# A page or SVG is read whole by the viewer (ui/app/viewer.js caps it at 10 MB); video and audio stream by range.
LIMITS = {"image": 25 * MB, "svg": 10 * MB, "page": 10 * MB, "video": 2048 * MB, "audio": 2048 * MB}
MAGIC = {"image/png": (b"\x89PNG\r\n\x1a\n",), "image/jpeg": (b"\xff\xd8\xff",), "image/gif": (b"GIF87a", b"GIF89a")}
TITLE_MAX = 200
LIFE = 2 * 3600          # lead, Oct 19: two hours, every request re-checked


def stored_type(mime):
    """The type to store for a type the server guessed from a file name: the common name for an alias, else as is."""
    return ALIASES.get(mime, mime) if mime else mime


def kind_of(mime, size, head=b""):
    """The inline kind for a file version, or "file" (a chip). `head` is the first bytes, when known: a picture whose
    bytes are not that picture is a chip, never a card."""
    mime = (mime or "").split(";", 1)[0].strip().lower()
    kind = KINDS.get(mime)
    if not kind or size is None or size > LIMITS[kind]:
        return "file"
    if head and mime in MAGIC and not head.startswith(MAGIC[mime]):
        return "file"
    if head and mime == "image/webp" and not (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "file"
    return kind


# ---------------------------------------------------------------------------------------------- page clean-up
# Attributes that make a browser fetch something. A value that is not a data: URI (or an in-page #fragment) is
# removed and reported. Links (<a href>) are clicks, not loads: they stay and open in a new tab.
LOADS = {"src", "srcset", "poster", "background", "data", "action", "formaction", "codebase", "cite", "longdesc",
         "manifest", "ping", "lowsrc", "dynsrc", "archive", "imagesrcset"}
DROPPED_TAGS = {"script", "iframe", "frame", "frameset", "object", "embed", "applet", "base", "noscript"}
# Of those, the ones whose whole subtree goes too. embed, frame and base are void: they have no end tag, so dropping
# one drops only that tag and the page after it stays.
SKIPPED_SUBTREES = DROPPED_TAGS - {"embed", "frame", "base"}
CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.I | re.S)
CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?(['"]?)([^'")\s;]+)\1\s*\)?[^;]*;?""", re.I)
ATTR = re.compile(r"""(\s+)([^\s=/>"']+)(?:(\s*=\s*)("[^"]*"|'[^']*'|[^\s>"']+))?""")


def loads_nothing(value):
    value = html.unescape(value or "").strip()
    return not value or value.startswith("#") or value.lower().startswith("data:")


class _Cleaner(HTMLParser):
    def __init__(self, base_target):
        super().__init__(convert_charrefs=False)
        # `skip`: the dropped containers still open, innermost last; content is dropped while any is open.
        self.out, self.unembedded, self.skip, self.style = [], [], [], 0
        self.base_target = base_target
        self.based = False
        self.case = {}                                      # SVG is XML: end tags keep their start tag's case

    def note(self, value):
        value = html.unescape(value).strip()
        if value and value not in self.unembedded:
            self.unembedded.append(value)

    def css(self, text):
        def imported(match):
            self.note(match.group(2))
            return ""
        def url(match):
            if loads_nothing(match.group(2)):
                return match.group(0)
            self.note(match.group(2))
            return "url()"
        return CSS_URL.sub(url, CSS_IMPORT.sub(imported, text))

    def srcset(self, value):
        """The candidates of a srcset that load nothing (HTML's own parsing: a URL runs to whitespace, so a data: URI
        keeps its comma; descriptors run to the next comma)."""
        text, at, kept = html.unescape(value), 0, []
        while at < len(text):
            while at < len(text) and (text[at].isspace() or text[at] == ","):
                at += 1
            start = at
            while at < len(text) and not text[at].isspace():
                at += 1
            url = text[start:at]
            descriptor = ""
            if url.endswith(","):
                url = url.rstrip(",")
            else:
                end = text.find(",", at)
                end = len(text) if end < 0 else end
                descriptor, at = text[at:end].strip(), end
            if not url:
                continue
            if loads_nothing(url):
                kept.append((url + " " + descriptor).strip())
            else:
                self.note(url)
        return ", ".join(kept)

    def tag(self, raw, name, closing):
        lowered = name.lower()
        if lowered in DROPPED_TAGS:
            if lowered == "base":
                for _, key, _, value in ATTR.findall(raw[len(name) + 1:]):
                    if key.lower() == "href" and value and not loads_nothing(value.strip("\"'")):
                        self.note(value.strip("\"'"))
            elif not closing and lowered in SKIPPED_SUBTREES:
                self.skip.append(lowered)
            return
        if lowered == "meta" and re.search(r"http-equiv\s*=\s*['\"]?\s*refresh", raw, re.I):
            return
        if lowered in LIVE_ATTRS:
            # A live block keeps only its own attributes; the server fills it in for each viewer when it is shown.
            body = raw[len(name) + 1:].rstrip()
            end = "/>" if body.endswith("/>") else ">"
            kept = "".join(f"{space}{key}{eq}{value}" for space, key, eq, value in ATTR.findall(body[:-len(end)])
                           if key.lower() in LIVE_ATTRS[lowered])
            self.out.append("<" + name + kept + end)
            return
        body = raw[len(name) + 1:].rstrip()
        end = "/>" if body.endswith("/>") else ">"
        body = body[:-len(end)]
        attrs, rel_link = [], lowered == "link"
        rel = ""
        for space, key, eq, value in ATTR.findall(body):
            if key.lower() == "rel" and value:
                rel = html.unescape(value.strip("\"'")).lower()
        for space, key, eq, value in ATTR.findall(body):
            k, bare = key.lower(), html.unescape(value[1:-1] if value[:1] in "\"'" and len(value) > 1 else value)
            local = k.rsplit(":", 1)[-1]
            if k.startswith("on"):
                continue                                    # event handlers never run here
            if lowered == "a" and local in ("target", "rel"):
                continue                                    # set below
            if local == "href" and lowered != "a":
                if not loads_nothing(bare):
                    self.note(bare)
                    continue
            elif local in ("srcset", "imagesrcset"):
                cleaned = self.srcset(bare)
                if not cleaned:
                    continue
                attrs.append(f'{space}{key}="{html.escape(cleaned, quote=True)}"')
                continue
            elif local in LOADS:
                if not loads_nothing(bare):
                    self.note(bare)
                    continue
            elif local == "style":
                cleaned = self.css(bare)
                if cleaned != bare:
                    attrs.append(f'{space}{key}="{html.escape(cleaned, quote=True)}"')
                    continue
            attrs.append(space + key + (eq + value if eq else ""))
        if rel_link and any(word in rel.split() for word in ("dns-prefetch", "preconnect", "prefetch", "prerender")):
            return
        if lowered == "a":
            attrs.append(' target="_blank" rel="noopener noreferrer"')
        self.case[lowered] = name
        self.out.append("<" + name + "".join(attrs) + end)
        if lowered == "head" and self.base_target and not self.based:
            self.out.append('<base target="_blank">')
            self.based = True
        if lowered == "style" and end == ">":
            self.style += 1

    def raw_name(self):
        text = self.get_starttag_text() or ""
        match = re.match(r"<([^\s/>]+)", text)
        return text, match.group(1) if match else ""

    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag in SKIPPED_SUBTREES:
                self.skip.append(tag)
            return
        text, name = self.raw_name()
        self.tag(text, name, False)

    def handle_startendtag(self, tag, attrs):
        if self.skip:
            return
        text, name = self.raw_name()
        self.tag(text, name, True)

    def handle_endtag(self, tag):
        if tag in DROPPED_TAGS:
            if tag in self.skip:                            # closes it and anything left open inside it
                del self.skip[len(self.skip) - 1 - self.skip[::-1].index(tag):]
            return
        if self.skip:
            return
        if tag == "style" and self.style:
            self.style -= 1
        self.out.append(f"</{self.case.get(tag, tag)}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(self.css(data) if self.style else data)

    def handle_entityref(self, name):
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip:
            self.out.append(f"&#{name};")

    def handle_comment(self, data):
        pass                                                # conditional comments can carry loads

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        if not self.skip:
            self.out.append(f"<?{data}>")

    def unknown_decl(self, data):
        if not self.skip:
            self.out.append(f"<![{data}]>")


def clean(data, kind, base=True):
    """(bytes, unembedded): a page or SVG with every reference that would load from somewhere else removed, and the
    list of those references, in order. Embedded data: URIs stay. Scripts, frames and event handlers are dropped (v1
    is static). A page's links open in a new tab, and it gets <base target="_blank">."""
    text = data.decode("utf-8", errors="replace")
    cleaner = _Cleaner(base_target=base and kind == "page")
    cleaner.feed(text)
    cleaner.close()
    out = "".join(cleaner.out)
    if base and kind == "page" and not cleaner.based:
        out = '<base target="_blank">' + out
    return out.encode("utf-8"), cleaner.unembedded


def loading_references(data):
    """Every reference in a page or SVG that a browser would fetch: empty for a clean visual (spec test 3)."""
    return clean(data, "svg")[1]


# ---------------------------------------------------------------------------------------------- reporter
# The one script a visual page runs (lead, Oct 19): it tells the frame around it how tall the page is. Its hash is
# the only script the page's content policy allows, so the page's own scripts and inline handlers stay dead. A frame
# laid out at zero width reports an inflated height the app cannot shrink back from, so it sends nothing until the
# page has a width and reports again on every resize (ui/tests/visual-reporter.cjs runs the same file). It reports
# the content's own height when the content fits the frame, not scrollHeight, which never goes below the frame: a
# page measured once too tall (wrapped at a passing narrow width) could otherwise never come back down.
REPORTER = Path(__file__).with_name("visual-reporter.js").read_text().strip()
REPORTER_HASH = "sha256-" + base64.b64encode(hashlib.sha256(REPORTER.encode()).digest()).decode()


def with_reporter(data):
    return data + b"<script>" + REPORTER.encode() + b"</script>"


def policy(kind, ancestors):
    frames = "frame-ancestors " + (" ".join(ancestors) or "'none'")
    if kind == "page":
        return ("default-src 'none'; script-src '" + REPORTER_HASH + "'; img-src data:; font-src data:; "
                "media-src data:; style-src 'unsafe-inline' data:; connect-src 'none'; form-action 'none'; "
                "base-uri 'none'; " + frames + "; sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox")
    if kind == "svg":
        return ("default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline' data:; "
                + frames + "; sandbox")
    return "default-src 'none'; " + frames + "; sandbox"


# ---------------------------------------------------------------------------------------------- tokens
SECRET_FILE = "visual-secret"


def secret(settings):
    """The key that signs visual addresses: TICO_VISUAL_SECRET, else a file beside the database made on first use."""
    given = os.environ.get("TICO_VISUAL_SECRET", "").strip()
    if given:
        if len(given) < 32:
            raise RuntimeError("TICO_VISUAL_SECRET must be at least 32 characters")
        return given.encode()
    path = settings.db_path.parent / SECRET_FILE
    if not path.exists():
        # Written whole, then linked into place: a request that arrives meanwhile never reads a half-made file, and
        # of two first requests the second link loses and both use the one secret. A filesystem that cannot
        # hard-link falls back to an exclusive create, which can still show an empty file for a moment.
        value = secrets.token_hex(32)
        draft = path.with_name(f".{SECRET_FILE}-{secrets.token_hex(8)}")
        fd = os.open(draft, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(value)
            try:
                os.link(draft, path)
            except FileExistsError:
                pass
            except OSError as exc:
                if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
                    raise
                try:
                    out = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(out, "w") as handle:
                        handle.write(value)
        finally:
            draft.unlink()
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(str(path) + " must be readable only by its owner (chmod 600)")
    value = path.read_text().strip()
    if len(value) < 32:
        raise RuntimeError(str(path) + " is too short; delete it to generate a new one")
    return value.encode()


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(key, actor, fid, version, part="", now=None):
    """A signed address for one reader, file version and part ("" or "poster"). Tokens are minted per hour bucket and
    live two hours, so the same reader gets the same address while more than half its life remains."""
    now = int(now if now is not None else time.time())
    expires = (now // (LIFE // 2) + 2) * (LIFE // 2)
    body = _b64(json.dumps([actor, fid, int(version), part, expires], separators=(",", ":")).encode())
    return body + "." + _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def read(key, token, now=None):
    """(actor, file id, version, part) from a token this server signed that has not expired, else None."""
    try:
        body, mac = token.split(".", 1)
        if not hmac.compare_digest(_unb64(mac), hmac.new(key, body.encode(), hashlib.sha256).digest()):
            return None
        actor, fid, version, part, expires = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if int(now if now is not None else time.time()) >= int(expires):
        return None
    return str(actor), str(fid), int(version), str(part)


def ancestors(settings):
    """The frame-ancestors sources of a visual's content policy: the visual address itself, the app's own address and
    its CORS origins (where the app's pages that frame it are). On a loopback address the other loopback spelling
    too: a local install opened at localhost instead of 127.0.0.1 (or the desktop app pointed at it) is the same app."""
    own = origin_of(settings.public_url)
    twin = {"127.0.0.1": "localhost", "localhost": "127.0.0.1"}.get(hostname(own))
    if twin:
        parts = urlsplit(own)
        twin = f"{parts.scheme}://{twin}" + (f":{parts.port}" if parts.port else "")
    return list(dict.fromkeys(["'self'", own, *([twin] if twin else []), *settings.cors_origins]))


def origin_of(url):
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def hostname(url):
    """The lowercase hostname of `url`, or "" when it has none. So https://Tico.example:443/ and http://tico.example:8443
    share one hostname: whatever the scheme or port, a browser sends that host's cookies to both."""
    return (urlsplit(url or "").hostname or "").lower()


def check_origin(settings):
    """A separate visual origin must really be separate: on the app's own hostname, in any scheme or with any port,
    visuals would sit next to its cookies, and a request there could not be told from one to the app. Such a setup
    stops the server; leaving TICO_VISUAL_ORIGIN unset gives the fallback instead. A value that is not a URL at all (a
    stray bracket, a port that is not a number) counts as unset, so visuals take the fallback (mode): like a missing
    setting, it never stops the server."""
    value = getattr(settings, "visual_origin", "")
    try:
        urlsplit(value or "").port
        visual = origin_of(value)
    except ValueError:
        logging.getLogger("tico.visuals").warning(
            "TICO_VISUAL_ORIGIN is not a valid URL; visuals show pictures, video and audio only until it is fixed")
        return ""
    if visual and hostname(visual) == hostname(settings.public_url):
        raise ValueError("TICO_VISUAL_ORIGIN must differ from the app's own hostname (TICO_PUBLIC_URL), whatever the scheme or port")
    return visual


def mode(settings):
    """How visuals show, decided from the setup alone (Visuals always on; there is no switch): "full" with a separate
    visual address, where every inline kind is served from that address as in Visual replies; "media" without one, the
    fallback, where pictures, video and audio are served from the app's own address as media only and pages and SVG are
    cards that open the file viewer. A page or SVG is never served from the app's own address: not on a loopback
    install, and TICO_VISUAL_SAME_ORIGIN, the opt-in that once did it, is no longer read."""
    return "full" if getattr(settings, "visual_origin", "") else "media"


def shown(settings, c):
    """What Settings and the clients are told (config `visuals`): "full" with a visual address; "hosted" without one
    while Tico file hosting takes new files, so new pages and SVG show inline from the company's files address
    (tico_storage.visual_ready); "media" otherwise (pages and SVG are Open cards)."""
    if mode(settings) == "full":
        return "full"
    from .tico_storage import visual_address
    return "hosted" if visual_address(c)[1] else "media"


def src_for(app, settings, c, who, fid, version):
    """A fresh `src` for one version of a file, as `for_reader` gives a message's visual (same reader check, same
    address rules), or None: a type that doesn't show inline, a file `who` may not read, or a page or SVG this install
    can't show inline (an Open card). A page held in Tico file hosting gets a `src` on the app's own route, which
    checks the reader on every load and sends them on to a short link on the company's files address (`serve`)."""
    v = c.execute("SELECT mime, size FROM bot_file_versions WHERE file_id=? AND version=?", (fid, int(version))).fetchone()
    kind = kind_of(v["mime"], v["size"]) if v else "file"
    if kind not in INLINE:
        return None
    message = {"refs": {"attachments": [{"file_id": fid, "version": int(version), "kind": kind}]}}
    return for_reader(app, settings, c, who, [message])[0]["refs"]["attachments"][0].get("src")


# What the fallback serves from the app's own address: kinds that a browser only ever shows as media.
MEDIA = ("image", "video", "audio")


# ---------------------------------------------------------------------------------------------- attach
def parse(item):
    """(file id, version, title) from an attachment given as "<file id>@<version>" or {ref, title}."""
    ref, title = (item, "") if isinstance(item, str) else (item.ref, item.title or "")
    fid, number = ref.rsplit("@", 1)
    return fid, int(number), " ".join(str(title).split())[:TITLE_MAX]


def _head(blobs, digest):
    try:
        return b"".join(blobs.open_range(digest, 0, 15))
    except Exception:          # a copy still on its way to storage: judged by its type alone
        return b""


def attach(c, who, files, blobs, items, *, task_id=None, conversation_id=None):
    """The stored attachments for a message, in the order given, and what each page left unembedded.

    Each is a version of a file on that task (task_review.comment_file's rule) or in that conversation. A page or SVG
    whose bytes would still load something from elsewhere gets a cleaned new version, and that version is the one
    posted. Over a size limit or of another type it is attached as a plain file (kind "file"), never a visual."""
    from .counsel import no_attachments
    from .store import H, Problem
    from .task_review import comment_file
    no_attachments(who, items)
    out, report = [], []
    for item in items:
        fid, number, title = parse(item)
        if task_id:
            fid, version = comment_file(c, files.auth, files, who, task_id, fid, number)
        else:
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
            if not row or row["scope"] != "conversation:" + conversation_id or row["archived"] \
                    or not files.visible(c, who, row, {}):
                raise Problem("not_found", "File version not found in this conversation", 404)
            version = c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=?",
                                (fid, number)).fetchone()
            if not version:
                raise Problem("not_found", "File version not found in this conversation", 404)
            version = dict(version)
        digest = version.get("digest")
        mime = (version.get("mime") or "").split(";", 1)[0].strip().lower()
        kind = kind_of(mime, version.get("size"), _head(blobs, digest) if digest else b"")
        if not c.execute("SELECT 1 FROM bot_files WHERE id=?", (fid,)).fetchone():
            kind = "file"           # a legacy task attachment (task_assets) is not a versioned file: it stays a chip
        unembedded = []
        if kind in ("page", "svg"):
            data = blobs.get(digest)
            cleaned, unembedded = clean(data, kind, base=False)     # <base> is added where it is shown
            if cleaned != data:
                version = _clean_version(c, who, files, blobs, fid, version, cleaned, mime)
                kind = kind_of(mime, version["size"]) if len(cleaned) <= LIMITS[kind] else "file"
        number = int(version["version"])
        out.append({"id": fid, "file_id": fid, "version": number, "ref": f"{fid}@{number}",
                    "title": title or version["name"], "kind": kind, "name": version["name"],
                    "size": version["size"], "content_type": version.get("mime") or mime,
                    "url": f"/api/v2/files/{fid}?v={number}", **({"unembedded": unembedded} if unembedded else {})})
        report.append({"ref": f"{fid}@{number}", "kind": kind, "unembedded": unembedded})
        H.event(c, who.actor, "message.visual", task_id or conversation_id, {"file": fid, "version": number, "kind": kind})
    return out, report


def _clean_version(c, who, files, blobs, fid, version, cleaned, mime):
    from .store import H
    from .blobs import register
    digest = blobs.put(cleaned, mime)
    item = register(c, who, digest, len(cleaned), version["name"], mime)
    number = c.execute("SELECT current_version FROM bot_files WHERE id=?", (fid,)).fetchone()[0] + 1
    files.insert_task_version(c, fid, number, item["id"], digest, len(cleaned), version["name"], mime, who.actor, H.now())
    c.execute("UPDATE bot_files SET current_version=?,last_activity_at=? WHERE id=?", (number, H.now(), fid))
    files.activity(c, fid, who.actor, "modified", version=number, digest=digest,
                   detail={"reason": "visual: references that would load from elsewhere removed"})
    return dict(c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=?", (fid, number)).fetchone())


# ---------------------------------------------------------------------------------------------- read
INLINE = ("image", "svg", "page", "video", "audio")


def decorate(c, who, files, key, base, messages, full=True, hosted=None):
    """Each visual on these messages, as this reader sees it now: the file's current version, whether it was removed,
    its size in pixels, and fresh addresses on the visual route (the picture and, for a video, its poster). `base` is
    the address the route is on: the visual address, or "" for the app's own (the fallback, `full` False). In the
    fallback a page or SVG gets no `src`: it is a card with `open` true, its title and, when the file has one, a still
    preview (`preview_url`, a picture), and Open shows it in the file viewer. The exception is one `hosted(version)`
    says shows from Tico file hosting's address (tico_storage.visual_ready): it gets a `src` on the app's own route,
    which checks the reader and sends them there (`serve`)."""
    cache, rows, served = {}, {}, {}
    # The visual route rebuilds a bot reader without its run (serve, token_reader), so a file this run sees but that
    # bare bot does not, a chat's file among them, gets no address: a file chip, not a link that always 404s.
    # A token names one reader: the caller itself, even when it acts for someone else (task_privacy.actor is then a pair).
    route = token_reader(c, files.auth, who.actor) if who.role == "bot" else who
    # Counsel sees each file a message carries as its plain ref, never an address or what the file is: what it reads is
    # the one version its run was asked to read (backend/counsel.py run_read), through its own tool.
    from .counsel import is_counsel
    plain = is_counsel(who)
    for message in messages:
        items = ((message or {}).get("refs") or {}).get("attachments") or []
        if not any(isinstance(a, dict) and a.get("file_id") for a in items):
            continue
        fresh = []
        for a in items:
            if not isinstance(a, dict) or not a.get("file_id"):
                fresh.append(a)
                continue
            if plain:
                fresh.append({k: a[k] for k in ("id", "file_id", "version", "ref", "kind") if k in a})
                continue
            fid, number = a["file_id"], int(a.get("version") or 0)
            if fid not in rows:
                rows[fid] = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
            row = rows[fid]
            hidden = bool(row) and not row["archived"] and not files.visible(c, who, row, cache)
            if a.get("kind") not in INLINE and not hidden:
                fresh.append(a)                  # a file kind with no address here: shown as stored, when it may be seen
                continue
            removed = not row or bool(row["archived"]) or hidden
            v = None if removed else c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=?",
                                               (fid, number)).fetchone()
            # A file this reader may not open says nothing of itself: not its name, title, type or size, whatever its
            # kind. A message can show a file from elsewhere (a review question's, backend/file_reviews.py) to a reader
            # of its task.
            shown = {k: a[k] for k in ("id", "file_id", "version", "ref", "kind") if k in a} if hidden else a
            view = {**shown, "removed": removed or v is None,
                    "current_version": None if removed else row["current_version"]}
            if v is not None:
                actor = who.actor
                here = base if route is who or (route and files.visible(c, route, row, served)) else None
                inline = full or a["kind"] in MEDIA or bool(hosted and here is not None and hosted(v))
                view["src"] = here + "/visuals/" + mint(key, actor, fid, number) if inline and here is not None else None
                if not inline:
                    view["open"] = True
                    if v["thumb_blob_id"] and here is not None:
                        view["preview_url"] = here + "/visuals/" + mint(key, actor, fid, number, "preview")
                for field in ("width", "height", "duration_ms"):
                    if v[field] is not None:
                        view[field] = v[field]
                if a["kind"] == "video" and v["poster_blob_id"] and here is not None:
                    view["poster_url"] = here + "/visuals/" + mint(key, actor, fid, number, "poster")
            fresh.append(view)
        message["refs"] = {**message["refs"], "attachments": fresh}
    return messages


def elsewhere(base, message, task=None):
    """The one line a message with visuals carries where it leaves Tico (Slack today; an email or push outlet later),
    as {"count", "label", "url"}, or None when it has none. Nothing visual is rendered there (Visual replies, choice 4).
    The link is a query, not a #fragment, because some clients drop fragments (ui/app/state.js turns it into the
    route): a message on a task (`task`, as hubdb.message_task_id finds it) opens the task, a plain chat reply that
    bot's chat in the room the reply is in, each at the message. It names ids only; whether the reader may open them
    is checked when they do."""
    refs = (message or {}).get("refs") or {}
    count = sum(1 for a in refs.get("attachments") or [] if isinstance(a, dict) and a.get("kind") in INLINE)
    if not count:
        return None
    where = {"task": task} if task else {}
    sender = str(message.get("sender_id") or "")
    if not where and sender.startswith("bot:"):
        # A bot has more than one chat with a person (each Slack thread is a room of its own): name the room.
        where = {"bot": sender[4:], "conversation": message.get("conversation_id") or ""}
    url = (base or "") + "/" + ("?" + urlencode({**where, "message": message["id"]}) if where else "")
    return {"count": count, "label": f"{count} visual{'' if count == 1 else 's'} - view in Tico", "url": url}


def key_for(app, settings):
    """The signing key, read once per server."""
    if not getattr(app.state, "visual_key", None):
        app.state.visual_key = secret(settings)
    return app.state.visual_key


def for_reader(app, settings, c, who, messages):
    """decorate() for any path that hands messages to a reader: the conversation page and snapshot, a single message,
    task comments, the task page, the assistant room and the live event stream all go through here."""
    from .tico_storage import visual_ready
    full, blobs, frames = mode(settings) == "full", app.state.blobs, ancestors(settings)

    def hosted(v):
        return visual_ready(blobs, v["digest"], v["size"], (v["mime"] or "").split(";", 1)[0].strip().lower(), frames)
    return decorate(c, who, app.state.files, key_for(app, settings), settings.visual_origin if full else "", messages,
                    full, hosted)


# ---------------------------------------------------------------------------------------------- serve
def on_host(request, origin):
    """Whether this request came in on `origin`'s host (the Host header, its default port dropped)."""
    try:
        want, got = urlsplit(origin or ""), urlsplit("//" + request.headers.get("host", ""))
        default = 443 if want.scheme == "https" else 80
        return bool(want.hostname) and (got.hostname, got.port or default) == (want.hostname, want.port or default)
    except ValueError:                      # a malformed Host or origin is never the visual address
        return False


def token_reader(c, auth, actor):
    """The reader the visual route checks a token's actor as: a person as they are now, a bot as itself without any
    run (a token outlives the turn it was minted in), or None when neither exists."""
    from .auth import Identity
    from .store import H, Problem
    if not isinstance(actor, str) or actor == H.bot_actor(H.COUNSEL):
        return None             # Counsel reads no visual: its grant is its run's, which a token does not carry
    try:
        if actor.startswith("human:"):
            return auth.identity_for_actor(c, actor)
        return Identity(actor, "bot") if actor.startswith("bot:") and H.bot(c, H.actor_id(actor)) else None
    except Problem:
        return None


def serve(request, store, auth, files, blobs, key, ancestors, token, full=True, visual_origin=""):
    """GET /visuals/<token>: one visual for the reader the token names, checked again on every request. The route
    answers on every host the server does, the app's own included, so a page or SVG is served only to a request that
    came in on the visual address (`visual_origin`, in full mode); anywhere else, the app's own address and the whole
    fallback (`full` False), the route serves media only. A poster or preview that is not a picture is refused.
    A page or SVG held in Tico file hosting's bucket is never served from here, in either mode: once the reader is
    checked, they are sent (302) to a four-minute link to its cleaned copy on the company's address
    (tico_storage.visual_link)."""
    from fastapi.responses import Response
    from .file_delivery import serve as deliver
    gone = Response(status_code=404, headers=_headers("default-src 'none'; sandbox", "attachment"))
    found = read(key, token)
    if not found:
        return gone
    actor, fid, number, part = found
    with store.read() as c:
        who = token_reader(c, auth, actor)
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        if not who or not row or row["archived"] or row["locator"] != "tico_blob" or not files.visible(c, who, row, {}):
            return gone
        v = c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=?", (fid, number)).fetchone()
        if not v:
            return gone
        mime = (v["mime"] or "").split(";", 1)[0].strip().lower()
        kind = kind_of(mime, v["size"])
        if part in ("poster", "preview"):
            # A video's poster, or the still preview of a page or SVG shown as a card: always a picture.
            blob_id = v["poster_blob_id"] if part == "poster" else v["thumb_blob_id"]
            still = c.execute("SELECT * FROM blobs WHERE id=?", (blob_id,)).fetchone() if blob_id else None
            if not still or (kind != "video" if part == "poster" else kind not in ("page", "svg")):
                return gone
            blob = dict(still)
            mime = (blob.get("content_type") or "").split(";", 1)[0].strip().lower()
            kind = KINDS.get(mime)
            if kind != "image":
                return gone
            blob["content_type"] = mime
        elif kind not in INLINE:
            return gone
        else:
            blob = {"digest": v["digest"], "size": v["size"], "name": v["name"], "content_type": mime}
    if kind in ("page", "svg") and not part:
        from .tico_storage import visual_link
        # A page with live blocks never has a hosted copy (tico_storage.make_visual), so it is filled in below.
        link = visual_link(blobs, blob["digest"], blob["size"], mime, ancestors)
        if link:
            return Response(status_code=302, headers={"Location": link, "Cache-Control": "private, no-store",
                                                      "Referrer-Policy": "no-referrer"})
        if not (full and on_host(request, visual_origin)):
            return gone
    rule = policy(kind, ancestors)
    if kind in ("page", "svg"):
        data = blobs.get(blob["digest"])
        if kind == "page" and not part and has_live(text := data.decode("utf-8", errors="replace")):
            # Filled in for this reader alone, with what they may see now.
            with store.read() as c:
                data = fill_live(text, c, who, auth, files).encode("utf-8")
        data, _ = clean(data, kind)
        if kind == "page":
            data = with_reporter(data)
        response = Response(data if request.method != "HEAD" else b"", media_type=mime,
                            headers={**_headers(rule), "Content-Length": str(len(data))})
        return response
    response = deliver(request, blobs, blob, True)
    response.headers.update(_headers(rule, disposition(mime)))
    return response


def disposition(mime):
    """Inline only for a picture, video or audio type a browser shows as media; anything else is a download."""
    return "inline" if KINDS.get(mime) in MEDIA else "attachment"


def _headers(rule, how="inline"):
    return {"Content-Security-Policy": rule, "Cross-Origin-Resource-Policy": "cross-origin",
            "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-cache", "Content-Disposition": how}
