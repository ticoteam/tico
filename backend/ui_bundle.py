"""One script and one stylesheet from the many files under ui/, with no build step.

ui/index.html lists the files: the <link> tags between `<!-- bundle:css:start -->` and
`<!-- bundle:css:end -->`, and the <script> tags between `<!-- bundle:js:start -->` and
`<!-- bundle:js:end -->`. That list, in that order, is the only one. The server concatenates each list
when the page is asked for (again only when a file changed) and serves index.html with each region
replaced by a single tag whose URL carries the content hash. The bundle is immutable for that URL, so a
browser fetches it once per release; index.html itself is revalidated on every load.

The scripts the page loads on their own (theme.js in the head, marked) are named by their content too: the page's tag
gets `?v=<hash of the file>`, and the server keeps that URL for a year (backend/app.py, `versioned`), so an open
needs no round trip to revalidate them. The bundle's first statement marks `tico:bundle-start` for the boot timing
(ui/app/boot-timing.js).

Each file goes in smaller (`slim_js`, `slim_css`): without its comment lines, blank lines and indentation, about a
fifth of the UI's size, gzipped or not. Nothing on a line with code changes, nor anything inside a string or template
literal; a file that does not read cleanly to its end goes in as it is. TICO_UI_SLIM=off bundles the files unchanged
(the bundle's line numbers then match the files').

Set TICO_UI_BUNDLE=off to serve the separate files as index.html lists them (editing the UI).
`python3 -m backend.ui_bundle <ui dir> <out dir>` writes the page and both bundles, for the browser tests.
"""
import gzip
import hashlib
import os
import re
import sys
from pathlib import Path

PREFIX = "/tico/ui/"
JS_PATH, CSS_PATH = PREFIX + "app.bundle.js", PREFIX + "app.bundle.css"
PATHS = (JS_PATH, CSS_PATH)
IMMUTABLE = "public, max-age=31536000, immutable"
REGION = re.compile(r"[ \t]*<!-- bundle:(js|css):start -->\n(.*?)[ \t]*<!-- bundle:\1:end -->\n?", re.S)
REF = re.compile(r'(?:src|href)="([^"]+)"')
# The scripts outside the bundle, by the URL path the page names them with: the file under ui/ each one serves.
STANDALONE = {"/tico/ui/theme.js": "theme.js", "/vendor/marked.min.js": "vendor/marked.min.js"}
# Where the boot timing starts counting the bundle's own run (ui/app/boot-timing.js).
BUNDLE_MARK = "try { performance.mark('tico:bundle-start'); } catch { /* no User Timing */ }\n"


class BundleError(Exception):
    pass


def enabled():
    return os.environ.get("TICO_UI_BUNDLE", "on").strip().lower() not in ("off", "0", "false", "no")


def slim_enabled():
    return os.environ.get("TICO_UI_SLIM", "on").strip().lower() not in ("off", "0", "false", "no")


# A `/` after one of these (or at the start) begins a regular expression; after anything else it divides.
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do", "else",
                "yield", "await"}
_KEEP = ("/*!", "@license", "@preserve")


def slim_js(text):
    """`text` without its comment lines, blank lines and leading indentation. Only whole lines go: a line holding code
    is kept as it is, comment and all, and so is every line inside a string or a template literal (its text and
    indentation are the string's). Returns `text` unchanged when it does not read to its end in plain code (an
    unclosed template or comment, or a regular expression read as division)."""
    out, i, n = [], 0, len(text)
    # The open templates: for each, how many `{` deep its current `${...}` is (None while in the template's text).
    stack = []
    last = ""            # the last character of code that was not space, or the last word
    line_start = True
    while i < n:
        in_text = bool(stack) and stack[-1] is None
        if line_start and not in_text:
            j = i
            while j < n and text[j] in " \t":
                j += 1
            end = text.find("\n", j)
            end = n if end < 0 else end
            if text.startswith("//", j) or j == end:
                i = end + 1             # a comment line or a blank one: gone, with its newline
                continue
            if text.startswith("/*", j) and not any(k in text[j:text.find("*/", j) + 2] for k in _KEEP):
                close = text.find("*/", j + 2)
                if close < 0:
                    return text
                after = text.find("\n", close)
                after = n if after < 0 else after
                if not text[close + 2:after].strip():
                    i = after + 1       # a comment of its own lines: gone
                    continue
            i, line_start = j, False    # the indentation goes; the line itself is kept as it is
            continue
        line_start = False
        c = text[i]
        if in_text:
            if c == "\\":
                out.append(text[i:i + 2]); i += 2; continue
            if c == "`":
                stack.pop(); last = "`"
            elif c == "$" and text.startswith("${", i):
                stack[-1] = 0; out.append("${"); i += 2; last = "{"; continue
            elif c == "\n":
                out.append(c); i += 1; continue    # a template's own line: kept whole, indentation and all
            out.append(c); i += 1; continue
        if c == "\n":
            out.append(c); i += 1; line_start = True; continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\n":
                    return text
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1]); i = j + 1; last = c; continue
        if c == "`":
            stack.append(None); out.append(c); i += 1; continue
        if c == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            out.append(text[i:end]); i = end; continue
        if c == "/" and text.startswith("/*", i):
            close = text.find("*/", i + 2)
            if close < 0:
                return text
            out.append(text[i:close + 2]); i = close + 2; continue
        if c == "/" and (not last or last in _REGEX_AFTER or last in _REGEX_WORDS):
            j, cls = i + 1, False
            while j < n and (cls or text[j] != "/"):
                if text[j] == "\n":
                    return text
                if text[j] == "\\":
                    j += 1
                elif text[j] == "[":
                    cls = True
                elif text[j] == "]":
                    cls = False
                j += 1
            out.append(text[i:j + 1]); i = j + 1; last = "/re"; continue
        if stack and c == "{":
            stack[-1] += 1
        elif stack and c == "}":
            if stack[-1] == 0:
                stack[-1] = None; out.append(c); i += 1; last = "}"; continue
            stack[-1] -= 1
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            out.append(text[i:j]); last = text[i:j]; i = j; continue
        if c in "+-" and last == c:
            last = c + c        # `i++ / n` divides
        elif c not in " \t\r":
            last = c
        out.append(c); i += 1
    return "".join(out) if not stack else text


def slim_css(text):
    """`text` without its comments (outside strings), blank lines and indentation; unchanged if a comment or a string
    never closes."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            if j >= n or text[j] != c:
                return text
            out.append(text[i:j + 1]); i = j + 1; continue
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            if close < 0:
                return text
            body = text[i:close + 2]
            # A comment parts what is on either side of it, as a space does.
            out.append(body if body.startswith("/* file: ") or any(k in body for k in _KEEP) else " ")
            i = close + 2; continue
        out.append(c); i += 1
    return "".join(line.strip() + "\n" for line in "".join(out).splitlines() if line.strip())


def _tag(kind, url):
    return (f'<script src="{url}"></script>\n' if kind == "js" else f'<link rel="stylesheet" href="{url}">\n')


class Bundle:
    def __init__(self, page, js, css, versions=None):
        self.page, self.js, self.css = page, js, css
        # STANDALONE path -> the `?v=` the page names it with.
        self.versions = dict(versions or {})
        self.etag = {JS_PATH: _etag(js), CSS_PATH: _etag(css)}
        # Sent to a browser that accepts it (the server has no other compression); its own strong ETag.
        self.gz = {JS_PATH: gzip.compress(js, 6, mtime=0), CSS_PATH: gzip.compress(css, 6, mtime=0)}
        self.gz_etag = {k: v[:-1] + '-gzip"' for k, v in self.etag.items()}
        self.page_etag = _etag(page)
        # What the page's own tags carry (`?v=`), joined: a page that finds a different one in the config is out of date.
        self.build = self.etag[JS_PATH][1:17] + "." + self.etag[CSS_PATH][1:17]


def _etag(data):
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


def build(ui_dir):
    ui_dir = Path(ui_dir)
    html = (ui_dir / "index.html").read_text()
    files = {"js": [], "css": []}
    for m in REGION.finditer(html):
        kind, body = m.group(1), m.group(2)
        for ref in REF.findall(body):
            if not ref.startswith(PREFIX) or ".." in ref.split("/"):
                raise BundleError(f"{ref}: only files under {PREFIX} can be bundled")
            path = ui_dir / ref[len(PREFIX):]
            if not path.is_file():
                raise BundleError(f"{ref}: no such file")
            files[kind].append((ref[len(PREFIX):], path))
    if not files["js"] or not files["css"]:
        raise BundleError("index.html has no bundle regions")
    # One script is one scope for 'use strict': a directive only counts at the top of a script, so the
    # bundle opens with it once, and every file must already be strict.
    js = ["'use strict';\n" + BUNDLE_MARK]
    for name, path in files["js"]:
        text = path.read_text()
        if not re.search(r"^'use strict';", text, re.M):
            raise BundleError(f"{name}: not strict, so it cannot share a script")
        if slim_enabled():
            text = slim_js(text)
        js.append(f"// file: {name}\n{text.rstrip()}\n")
    css = [f"/* file: {name} */\n{(slim_css if slim_enabled() else str)(path.read_text()).rstrip()}\n"
           for name, path in files["css"]]
    js_b, css_b = "\n".join(js).encode(), "\n".join(css).encode()
    urls = {"js": f"{JS_PATH}?v={_etag(js_b)[1:17]}", "css": f"{CSS_PATH}?v={_etag(css_b)[1:17]}"}
    page = REGION.sub(lambda m: _tag(m.group(1), urls[m.group(1)]), html)
    versions, standalone = {}, []
    for url, name in STANDALONE.items():
        path, tag = ui_dir / name, f'src="{url}"'
        if tag not in page or not path.is_file():
            continue
        versions[url] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        page = page.replace(tag, f'src="{url}?v={versions[url]}"')
        standalone.append(path)
    listed = [p for kind in files.values() for _, p in kind] + standalone + [ui_dir / "index.html"]
    return Bundle(page.encode(), js_b, css_b, versions), listed


class UiBundle:
    """Builds lazily and rebuilds only when index.html or a listed file changed."""

    def __init__(self, ui_dir):
        self.ui_dir, self._bundle, self._stamp, self._paths = Path(ui_dir), None, None, []

    def _signature(self):
        out = []
        for p in self._paths:
            try:
                st = p.stat()
                out.append((st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return out

    def get(self):
        """The current Bundle, or None when the files cannot be bundled (the caller serves them as they are)."""
        if self._bundle is not None and self._signature() == self._stamp:
            return self._bundle
        try:
            self._bundle, self._paths = build(self.ui_dir)
            self._stamp = self._signature()
        except (BundleError, OSError) as exc:
            print(f"ui bundle off: {exc}", file=sys.stderr)
            self._bundle = None
        return self._bundle


_SHARED = {}


def shared(ui_dir):
    """The one UiBundle for a ui directory, for the page route and for `build_id` alike."""
    key = str(ui_dir)
    if key not in _SHARED:
        _SHARED[key] = UiBundle(ui_dir)
    return _SHARED[key]


def build_id(ui_dir):
    """The build the server serves now (the config carries it so an open page can tell it is out of date);
    empty when the UI is served as separate files."""
    if not enabled():
        return ""
    bundle = shared(ui_dir).get()
    return bundle.build if bundle else ""


def versioned(ui_dir, path, v):
    """Whether `path?v=<v>` names a standalone script as the page served now names it: kept for a year then. Another
    `v` (a page from before an edit) is the file as it is now, revalidated like any other."""
    if not v or path not in STANDALONE or not enabled():
        return False
    bundle = shared(ui_dir).get()
    return bool(bundle) and bundle.versions.get(path) == v


if __name__ == "__main__":
    ui, out = Path(sys.argv[1]), Path(sys.argv[2])
    bundle, _ = build(ui)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_bytes(bundle.page)
    (out / "app.bundle.js").write_bytes(bundle.js)
    (out / "app.bundle.css").write_bytes(bundle.css)
