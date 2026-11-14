"""One script and one stylesheet from the many files under ui/, concatenated as the page is served.

ui/index.html lists the files: the <link> tags between `<!-- bundle:css:start -->` and
`<!-- bundle:css:end -->`, and the <script> tags between `<!-- bundle:js:start -->` and
`<!-- bundle:js:end -->`. That list, in that order, is the only one. The server concatenates each list
when the page is asked for (again only when a file changed) and serves index.html with each region
replaced by the bundle's tags, whose URLs carry the content hash. The bundle is immutable for that URL, so a
browser fetches it once per release; index.html itself is revalidated on every load.

Each bundle is cut, between files, into parts of at most PART_BYTES (app.bundle.js, app.bundle.2.js, ...), loaded in
order. WebKit's disk cache keeps no response larger than an eighth of its capacity (NetworkResourceLoader,
maximumCacheBufferSize), and the one 4.3 MB script went past that: the desktop app downloaded 1.2 MB on every open
(366-893 ms of its boot). Every part carries the hash of its whole bundle, so the page and the config's `ui_build`
name one build as before.

The scripts the page loads on their own (theme.ts in the head, marked) are named by their content too: the page's tag
gets `?v=<hash of the file>`, and the server keeps that URL for a year (backend/app.py, `versioned`), so an open
needs no round trip to revalidate them. The bundle's first statement marks `tico:bundle-start` for the boot timing
(ui/app/boot-timing.ts).

A file the page lists as TypeScript (`/tico/ui/app/a.ts`) is bundled as its compiled JavaScript (backend/ui_build.py):
when that is missing or older than the source, the bundle is not built (StaleBuild), and nothing older is served.

Each file is bundled as its minified copy (build/ui/min, written by scripts/build-ui.cjs with a map to its source) while
that copy is current, and as it is otherwise (a file edited since the last build). A part with minified files ends by
naming its map (app.bundle.2.js.map): an index map whose sections place each file's own map at that file's line, so
the browser's tools and `python -m backend.ui_bundle --where app.bundle.2.js:1:4567` (a Sentry frame, which keeps only
the file name, line and column) find the source file and line.

Page parts (ui/parts.json). A cold open downloads only the shell: the files a page part names are cut out of the
bundle and served as that part alone, `app.bundle.<part>.js?v=<hash of that part>`, fetched the first time a person
goes to one of its pages (ui/app/parts.ts). Each part is named by its own content, so a release that changed one page's
code changes that part's address only: the shell's `?v=` is the hash of the shell alone. The page lists every part's
address in a JSON block (`<script type="application/json" id="ui-parts">`, no code, so the page's script-src stays
'self') with `build`, the hash of all the app's code and styles, which an open page compares with the config's
`ui_build`. A part's entries are names the shell or another part calls: the shell's first part declares each one
as a stub before any file runs. A page (`pages`) or a call (`calls`) loads the part (with its `deps`, theirs and so
on, in the order ui/parts.json lists the parts), then calls the
real function, which the part's own declaration has put in the stub's place; an idle name (`idle`, a page's stop
helper) does nothing until its part is loaded. A part is never larger than PART_BYTES (refused at build), and an old
part address is a 404: an open tab from before a release reloads to the current one (ui/app/parts.ts).

The shell has a ceiling (SHELL_JS_CEILING): the JavaScript a cold open of Home downloads, gzip as the server sends it
(`cold_js`), may not grow past it. backend/tests/test_ui_shell_size.py fails a pull request that does (`shell_over`
names the size and the largest shell files), and scripts/pr_checks.py runs that test for every change that can move the
number. `python -m backend.ui_bundle --shell-size` prints it.

Set TICO_UI_BUNDLE=off to serve the separate files as index.html lists them (editing the UI).
`python3 -m backend.ui_bundle <ui dir> <out dir>` writes the page and both bundles, for the browser tests.
"""

import gzip
import hashlib
import os
import re
import sys
from pathlib import Path

from . import ui_build

PREFIX = "/tico/ui/"
JS_PATH, CSS_PATH = PREFIX + "app.bundle.js", PREFIX + "app.bundle.css"
# The most JavaScript, in bytes, a cold open of Home may download before the app runs: the shell's parts and the
# standalone scripts, gzip as a browser gets them (`cold_js`). The performance budget is 300 KB (P05b); lower this
# whenever the shell shrinks, never raise it to let a change in: move code a click runs into a page part instead.
SHELL_JS_CEILING = 300 * 1024   # the budget (P05b, 300 KB gzip); lower it whenever the shell shrinks, never raise it
# The gzip level and floor backend/ui_compression.py sends the standalone scripts with (the parts carry their own).
STANDALONE_GZIP_LEVEL, STANDALONE_GZIP_MIN = 3, 1024
# The largest part (decoded): a WebKit cache of 4 MB or more keeps it. The files are cut between, never inside.
PART_BYTES = 512 * 1024
PART = re.compile(re.escape(PREFIX) + r"app\.bundle(?:\.([2-9]|[1-9][0-9]|[a-z][a-z0-9-]*))?\.(js|css)")
# A page part's name (ui/parts.json) and a name it lets others call.
PART_NAME = re.compile(r"[a-z][a-z0-9-]*")
ENTRY_NAME = re.compile(r"[A-Za-z_$][\w$]*")
ENTRY_KINDS = ("pages", "calls", "idle")
IMMUTABLE = "public, max-age=31536000, immutable"
REGION = re.compile(r"[ \t]*<!-- bundle:(js|css):start -->\n(.*?)[ \t]*<!-- bundle:\1:end -->\n?", re.S)
REF = re.compile(r'(?:src|href)="([^"]+)"')
# The scripts outside the bundle, by the URL path the page names them with: the file under ui/ each one serves.
STANDALONE = {"/tico/ui/theme.ts": "theme.ts", "/vendor/marked.min.js": "vendor/marked.min.js"}
# Where the boot timing starts counting the bundle's own run (ui/app/boot-timing.ts).
BUNDLE_MARK = "try { performance.mark('tico:bundle-start'); } catch { /* no User Timing */ }\n"


class BundleError(Exception):
    pass


def enabled():
    return os.environ.get("TICO_UI_BUNDLE", "on").strip().lower() not in ("off", "0", "false", "no")


def _tag(kind, url):
    return f'<script src="{url}"></script>\n' if kind == "js" else f'<link rel="stylesheet" href="{url}">\n'


def part_path(kind, n):
    """The URL path of a bundle's nth part (0 is the first)."""
    return f"{PREFIX}app.bundle.{kind}" if n == 0 else f"{PREFIX}app.bundle.{n + 1}.{kind}"


def is_part(path):
    return bool(PART.fullmatch(path))


def page_part_path(name):
    """The URL path of a page part (ui/parts.json)."""
    return f"{PREFIX}app.bundle.{name}.js"


def read_parts(ui_dir, js_names):
    """ui/parts.json, checked against the files index.html bundles: {part: {"files", "deps", "pages", "calls",
    "idle", "events", "load"}}, empty when there is no such file. `events` are the CSS selectors of controls the shell
    draws whose click or change the part's own document listeners handle: the shell loads the part on the first one."""
    import json

    path = Path(ui_dir) / "parts.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text())
    except ValueError as exc:
        raise BundleError(f"ui/parts.json: {exc}") from None
    parts, owner, entries = {}, {}, {}
    for name, spec in raw.items():
        if name.startswith("//"):
            continue  # a comment
        if not PART_NAME.fullmatch(name) or name.isdigit():
            raise BundleError(f"ui/parts.json: {name!r} is not a part name (a-z, 0-9, -)")
        part = {k: list(spec.get(k, [])) for k in ("files", "deps", "events", *ENTRY_KINDS)}
        if not all(isinstance(e, str) and e.strip() for e in part["events"]):
            raise BundleError(f"ui/parts.json: {name}: events are CSS selectors")
        for f in part["files"]:
            if f not in js_names:
                raise BundleError(f"ui/parts.json: {f} is not a script index.html bundles")
            if f in owner:
                raise BundleError(f"ui/parts.json: {f} is in two parts ({owner[f]}, {name})")
            owner[f] = name
        for kind in ENTRY_KINDS:
            for e in part[kind]:
                if not ENTRY_NAME.fullmatch(e):
                    raise BundleError(f"ui/parts.json: {name}: {e!r} is not a name")
                if e in entries:
                    raise BundleError(f"ui/parts.json: {e} is an entry of two parts ({entries[e]}, {name})")
                entries[e] = name
        parts[name] = part
    for name, part in parts.items():
        for dep in part["deps"]:
            if dep not in parts:
                raise BundleError(f"ui/parts.json: {name} depends on {dep}, which is not a part")
    # What a part needs loaded before its entries run: its deps, theirs, and so on (two parts may need each other), in
    # the order ui/parts.json lists the parts, itself included. Scripts run in that order (ui/app/parts.ts).
    rank = {name: n for n, name in enumerate(parts)}
    for name, part in parts.items():
        need, todo = {name}, [name]
        while todo:
            for dep in parts[todo.pop()]["deps"]:
                if dep not in need:
                    need.add(dep)
                    todo.append(dep)
        part["load"] = sorted(need, key=rank.__getitem__)
    return parts


def stubs(parts):
    """The shell's stand-ins for the page parts' entries (ui/app/parts.ts uiPartCall), declared before any file runs."""
    out = []
    for name, part in parts.items():
        for kind in ENTRY_KINDS:
            for e in part[kind]:
                if kind == "idle":
                    out.append(f"function {e}() {{}}\n")
                else:
                    page = "true" if kind == "pages" else "false"
                    out.append(f'function {e}(...a) {{ return uiPartCall("{name}", "{e}", a, {page}); }}\n')
    called = [e for part in parts.values() for kind in ("pages", "calls") for e in part[kind]]
    if called:
        # So a call that finds a stand-in still there after its part loaded fails, rather than calling itself.
        out.append("const UI_PART_STUBS = new Set([" + ", ".join(called) + "]);\n")
    return "".join(out)


def pack(chunks, limit=None):
    """The indexes of the chunks (each one file's text, in order) packed into parts of at most `limit` bytes
    (PART_BYTES); a file larger than that is a part of its own."""
    limit = PART_BYTES if limit is None else limit
    parts, size = [[]], 0
    for i, chunk in enumerate(chunks):
        if parts[-1] and size + len(chunk) > limit:
            parts.append([])
            size = 0
        parts[-1].append(i)
        size += len(chunk)
    return parts


def split(chunks, limit=None):
    """The chunks packed into parts (`pack`), each part's bytes."""
    return [b"".join(chunks[i] for i in p) for p in pack(chunks, limit)]


def map_path(kind, n):
    """The URL path of the source map of a bundle's nth part."""
    return part_path(kind, n) + ".map"


def _parts(kind, chunks, prefix=lambda n: b"", digests=None, at=None):
    """The parts of one bundle and, per part, its map's sections [(line, map file)]: `chunks` are (text, the lines
    before the file's own code, its map file or None). A part with any section ends by naming its map, `?v=` a hash of
    where each section is and its map's sha256 (`digests`, from the manifest), so the map's URL changes with them and
    is the same on every server built from the same files."""
    digests = digests or {}
    texts = [c[0] for c in chunks]
    parts, sections = [], []
    for n, indexes in enumerate(pack(texts)):
        head = prefix(n)
        line, found = head.count(b"\n"), []
        for i in indexes:
            text, before, path = chunks[i]
            if path is not None:
                found.append((line + before, path))
            line += text.count(b"\n")
        body = head + b"".join(texts[i] for i in indexes)
        if found:
            key = hashlib.sha256(repr([(line, digests.get(path, path.name)) for line, path in found]).encode())
            url = ((at + ".map") if at else map_path(kind, n)).rsplit("/", 1)[1] + "?v=" + key.hexdigest()[:16]
            body += (f"//# sourceMappingURL={url}\n" if kind == "js" else f"/*# sourceMappingURL={url} */\n").encode()
        parts.append(body)
        sections.append(found)
    return parts, sections


def index_map(sections):
    """The index source map of one part: each file's own map placed at the line its code starts on."""
    return (
        b'{"version":3,"sections":['
        + b",".join(
            b'{"offset":{"line":%d,"column":0},"map":%s}' % (line, path.read_bytes().strip()) for line, path in sections
        )
        + b"]}"
    )


class Bundle:
    def __init__(self, page, js_parts, css_parts, versions=None, sections=None, page_parts=None):
        self.page, self.js, self.css = page, b"".join(js_parts), b"".join(css_parts)
        # A page part's URL path -> its bytes (ui/parts.json), in an order their deps allow.
        page_parts = dict(page_parts or {})
        # All the app's code: what a page from before the split, which names only the first URL, needs.
        self.js_whole = self.js + b"".join(page_parts.values())
        # STANDALONE path -> the `?v=` the page names it with.
        self.versions = dict(versions or {})
        # The bundle's URL path -> the part served there.
        self.parts = {
            **{part_path("js", n): b for n, b in enumerate(js_parts)},
            **{part_path("css", n): b for n, b in enumerate(css_parts)},
            **page_parts,
        }
        self.etag = {path: _etag(body) for path, body in self.parts.items()}
        # The shell's paths (every part but the page parts).
        self.shell_paths = {path for path in self.parts if path not in page_parts}
        # A part's map path -> (its `?v=`, its sections); the map itself is read when it is asked for (`source_map`).
        self.sections = {}
        for path, found in (sections or {}).items():
            m = re.search(rb"sourceMappingURL=[^?\s]+\?v=([0-9a-f]{16})", self.parts[path])
            if found and m:
                self.sections[path + ".map"] = (m.group(1).decode(), found)
        self._maps = {}
        # Sent to a browser that accepts it (the server has no other compression); its own strong ETag.
        self.gz = {path: gzip.compress(body, 6, mtime=0) for path, body in self.parts.items()}
        self.gz_etag = {k: v[:-1] + '-gzip"' for k, v in self.etag.items()}
        self.page_etag = _etag(page)
        self.whole_etag = {
            kind: _etag(body)[:-1] + '-whole"' for kind, body in (("js", self.js_whole), ("css", self.css))
        }
        self.whole_gz = {}
        # What the shell's tags carry (`?v=`, the hash of the shell's script, and of the whole stylesheet).
        self.version = {"js": _etag(self.js)[1:17], "css": _etag(self.css)[1:17]}
        # The `?v=` each part is current under: the shell's parts carry the shell's, a page part its own.
        self.part_version = {
            **{path: self.version["js" if path.endswith(".js") else "css"] for path in self.parts},
            **{path: _etag(body)[1:17] for path, body in page_parts.items()},
        }
        # All the app's code and styles: a page that finds a different build in the config is out of date.
        self.build = self.whole_etag["js"][1:17] + "." + self.version["css"]

    def source_map(self, path, v):
        """The map at `path` (app.bundle.2.js.map) as the part names it (`?v=`), or None."""
        if path not in self.sections or self.sections[path][0] != v:
            return None
        if path not in self._maps:
            body = index_map(self.sections[path][1])
            self._maps[path] = (body, gzip.compress(body, 6, mtime=0), _etag(body))
        return self._maps[path]

    def gzip_whole(self, kind):
        if kind not in self.whole_gz:
            self.whole_gz[kind] = gzip.compress(self.js_whole if kind == "js" else self.css, 6, mtime=0)
        return self.whole_gz[kind]


def _etag(data):
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


def build(ui_dir):
    ui_dir = Path(ui_dir)
    html = (ui_dir / "index.html").read_text()
    files = {"js": [], "css": []}
    # The TypeScript sources and the manifest their outputs are checked against: an edit to either rebuilds.
    typed = []
    for m in REGION.finditer(html):
        kind, body = m.group(1), m.group(2)
        for ref in REF.findall(body):
            if not ref.startswith(PREFIX) or ".." in ref.split("/"):
                raise BundleError(f"{ref}: only files under {PREFIX} can be bundled")
            name = ref[len(PREFIX) :]
            path = ui_dir / name
            if not path.is_file():
                raise BundleError(f"{ref}: no such file")
            if kind == "js" and name.endswith(".ts"):
                typed.append(path)
                path = ui_build.compiled(ui_dir, name)  # StaleBuild, not a fallback to the separate files
            files[kind].append((name, path))
    if not files["js"] or not files["css"]:
        raise BundleError("index.html has no bundle regions")
    # Each file as its current minified copy (with its map), or as it is: (name, text, map file or None).
    minified = ui_build.minified_entries(ui_dir)
    sources, digests = {}, {}
    for kind in ("js", "css"):
        sources[kind] = []
        for name, path in files[kind]:
            found = ui_build.minified(ui_dir, name, minified)
            if found:
                text, map_file = found[0].read_text(), found[1]
                digests[map_file] = minified[name]["map"]
            elif name.endswith(".ts"):
                # A compiled file's last line names its source map, which maps the file served alone, not a bundle part.
                text, map_file = ui_build.without_map_line(path.read_bytes()).decode(), None
            else:
                text, map_file = path.read_text(), None
            sources[kind].append((name, text, map_file))
    parts = read_parts(ui_dir, [name for name, _ in files["js"]])
    owner = {f: part for part, spec in parts.items() for f in spec["files"]}
    # One script is one scope for 'use strict': a directive only counts at the top of a script, so the
    # bundle opens with it once, and every file must already be strict.
    js, paged, shell_files = [], {part: [] for part in parts}, []
    for name, text, map_file in sources["js"]:
        # esbuild writes the directive of a compiled or minified file with double quotes.
        if not re.search(r"""^(['"])use strict\1;""", text, re.M):
            raise BundleError(f"{name}: not strict, so it cannot share a script")
        chunk = (f"// file: {name}\n{text.rstrip()}\n\n".encode(), 1, map_file)
        (paged[owner[name]] if name in owner else js).append(chunk)
        if name not in owner:
            shell_files.append((name, chunk[0]))
    css = [(f"/* file: {name} */\n{text.rstrip()}\n\n".encode(), 1, m) for name, text, m in sources["css"]]
    # Each part is a script of its own, so each opens with the directive; the first also marks where the run starts.
    head = BUNDLE_MARK + stubs(parts)
    js_parts, js_sections = _parts(
        "js", js, lambda n: b"'use strict';\n" + (head.encode() if n == 0 else b"") + b"\n", digests
    )
    css_parts, css_sections = _parts("css", css, digests=digests)
    sections = {part_path("js", n): s for n, s in enumerate(js_sections)}
    sections |= {part_path("css", n): s for n, s in enumerate(css_sections)}
    page_parts = {}
    for part, chunks in paged.items():
        if not chunks:
            raise BundleError(f"ui/parts.json: {part} has no files")
        if sum(len(c[0]) for c in chunks) > PART_BYTES:
            raise BundleError(
                f"ui/parts.json: {part} is over {PART_BYTES} bytes, more than WebKit's cache keeps: split it"
            )
        cut, found = _parts("js", chunks, lambda n: b"'use strict';\n\n", digests, page_part_path(part))
        page_parts[page_part_path(part)] = cut[0]
        sections[page_part_path(part)] = found[0]
    whole = Bundle(b"", js_parts, css_parts, page_parts=page_parts)
    version, build_id = whole.version, whole.build
    count = {"js": len(js_parts), "css": len(css_parts)}

    def region(m):
        kind = m.group(1)
        tags = "".join(_tag(kind, f"{part_path(kind, n)}?v={version[kind]}") for n in range(count[kind]))
        if kind == "js" and parts:
            import json

            table = {
                "build": build_id,
                "parts": {
                    part: {
                        "url": f"{page_part_path(part)}?v={whole.part_version[page_part_path(part)]}",
                        "load": spec["load"],
                        **({"events": spec["events"]} if spec["events"] else {}),
                    }
                    for part, spec in parts.items()
                },
            }
            # Data, not code: a JSON block runs nothing, so the page's script-src stays 'self'.
            tags = (
                f'<script type="application/json" id="ui-parts">{json.dumps(table, separators=(",", ":"))}</script>\n'
                + tags
            )
        return tags

    page = REGION.sub(region, html)
    versions, standalone, standalone_bytes = {}, [], {}
    for url, name in STANDALONE.items():
        path, tag = ui_dir / name, f'src="{url}"'
        if tag not in page or not path.is_file():
            continue
        served = path
        if name.endswith(".ts"):  # served as its compiled output, so the hash names those bytes (as for a bundled file)
            typed.append(path)
            served = ui_build.compiled(ui_dir, name)  # StaleBuild, not the source
        standalone_bytes[url] = served.read_bytes()
        versions[url] = hashlib.sha256(standalone_bytes[url]).hexdigest()[:16]
        page = page.replace(tag, f'src="{url}?v={versions[url]}"')
        standalone.append(path)
    listed = [p for kind in files.values() for _, p in kind] + standalone + [ui_dir / "index.html"]
    if typed:
        listed += typed + [ui_build.manifest_path(ui_dir)]
    # A new build of the minified copies rewrites the manifest.
    if minified and ui_build.manifest_path(ui_dir) not in listed:
        listed.append(ui_build.manifest_path(ui_dir))
    if parts:
        listed.append(Path(ui_dir) / "parts.json")
    bundle = Bundle(page.encode(), js_parts, css_parts, versions, sections, page_parts)
    # What `cold_js` and `shell_over` read: each shell file's code as bundled, and each standalone script's bytes.
    bundle.shell_files, bundle.standalone = shell_files, standalone_bytes
    return bundle, listed


def cold_js(bundle):
    """{URL path: bytes on the wire} of the JavaScript a cold open of Home downloads before the app runs, as a browser
    that accepts gzip gets it: the shell's parts (`Bundle.gz`) and the standalone scripts the page names (theme.ts,
    marked), which backend/ui_compression.py compresses. Page parts are left out: none is fetched until its page opens."""
    out = {path: len(bundle.gz[path]) for path in sorted(bundle.shell_paths) if path.endswith(".js")}
    for url, body in bundle.standalone.items():
        if not url.endswith(".css"):
            big = len(body) >= STANDALONE_GZIP_MIN
            out[url] = len(gzip.compress(body, STANDALONE_GZIP_LEVEL, mtime=0)) if big else len(body)
    return out


def shell_over(bundle, ceiling=None):
    """None when a cold open's JavaScript (`cold_js`) is within `ceiling` bytes; otherwise what to tell the change's
    author: the size, the ceiling and the five largest shell files (each gzip on its own, so they add up to more)."""
    ceiling = SHELL_JS_CEILING if ceiling is None else ceiling
    total = sum(cold_js(bundle).values())
    if total <= ceiling:
        return None
    largest = sorted(((len(gzip.compress(text, 6, mtime=0)), name) for name, text in bundle.shell_files), reverse=True)
    files = ", ".join(f"{name} {size / 1024:.1f} KB" for size, name in largest[:5])
    return (
        f"A cold open of Home downloads {total / 1024:.1f} KB of JavaScript (gzip), over its ceiling of "
        f"{ceiling / 1024:.1f} KB (backend/ui_bundle.py SHELL_JS_CEILING). The largest shell files: {files}. Move code "
        "that runs on a click or on another page into a page part (ui/parts.json) instead of raising the ceiling."
    )


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
        """The current Bundle, or None when the files cannot be bundled (the caller serves them as they are).
        A TypeScript file without its current compiled output raises StaleBuild: the old bundle is not kept."""
        if self._bundle is not None and self._signature() == self._stamp:
            return self._bundle
        try:
            self._bundle, self._paths = build(self.ui_dir)
            self._stamp = self._signature()
        except (BundleError, OSError) as exc:
            print(f"ui bundle off: {exc}", file=sys.stderr)
            self._bundle = None
        except ui_build.StaleBuild:
            self._bundle = None
            raise
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
    try:
        bundle = shared(ui_dir).get()
    except ui_build.StaleBuild:
        return ""
    return bundle.build if bundle else ""


def versioned(ui_dir, path, v):
    """Whether `path?v=<v>` names a standalone script as the page served now names it: kept for a year then. Another
    `v` (a page from before an edit) is the file as it is now, revalidated like any other."""
    if not v or path not in STANDALONE or not enabled():
        return False
    try:
        bundle = shared(ui_dir).get()
    except ui_build.StaleBuild:
        return False
    return bool(bundle) and bundle.versions.get(path) == v


_B64 = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}


def _segments(mappings):
    """A source map's mappings, decoded: per generated line, [(column, source, line, column)] (0-based, absolute)."""
    lines, state = [], [0, 0, 0, 0, 0]
    for text in mappings.split(";"):
        row, state[0] = [], 0
        for seg in text.split(","):
            if not seg:
                continue
            values, value, shift = [], 0, 0
            for ch in seg:
                digit = _B64[ch]
                value += (digit & 31) << shift
                if digit & 32:
                    shift += 5
                else:
                    values.append(-(value >> 1) if value & 1 else value >> 1)
                    value, shift = 0, 0
            for i, v in enumerate(values):
                state[i] += v
            if len(values) >= 4:
                row.append(tuple(state[:4]))
        lines.append(row)
    return lines


def where(bundle, frame):
    """A frame of a bundle part (`app.bundle.2.js:1:4567`, 1-based line and column as Sentry and browsers give them) as
    `source:line:column`, or None when no minified file is there."""
    import json

    m = re.fullmatch(r"(?:.*/)?(app\.bundle(?:\.(?:\d+|[a-z][a-z0-9-]*))?\.(?:js|css)):(\d+):(\d+)", frame)
    if not m:
        raise ValueError(f"{frame}: not app.bundle[.N|.part].js:line:column")
    path, line, column = PREFIX + m.group(1), int(m.group(2)) - 1, int(m.group(3)) - 1
    found = [s for s in bundle.sections.get(path + ".map", (None, []))[1] if s[0] <= line]
    if not found:
        return None
    start, map_file = found[-1]
    source_map = json.loads(map_file.read_bytes())
    rows = _segments(source_map["mappings"])
    row = rows[line - start] if line - start < len(rows) else []
    hits = [s for s in row if s[0] <= column]
    if not hits:
        return None
    _, source, src_line, src_column = hits[-1]
    return f"{source_map['sources'][source]}:{src_line + 1}:{src_column + 1}"


if __name__ == "__main__" and sys.argv[1:2] == ["--where"]:
    # A Sentry frame of the bundle this checkout builds now (build it at the release's commit first).
    root = Path(__file__).resolve().parents[1]
    try:
        current, _ = build(root / "ui")
    except ui_build.StaleBuild as exc:
        raise SystemExit(f"ui bundle: {exc}") from None
    for arg in sys.argv[2:]:
        print(f"{arg} -> {where(current, arg) or 'not in a minified file'}")
elif __name__ == "__main__" and sys.argv[1:2] == ["--shell-size"]:
    # The JavaScript a cold open of Home downloads (what bench/first-open.cts measures in a browser), against the ceiling.
    try:
        current, _ = build(Path(__file__).resolve().parents[1] / "ui")
    except ui_build.StaleBuild as exc:
        raise SystemExit(f"ui bundle: {exc}") from None
    sizes = cold_js(current)
    for path, size in sizes.items():
        print(f"{size / 1024:8.1f} KB  {path}")
    print(f"{sum(sizes.values()) / 1024:8.1f} KB  cold open of Home, gzip (ceiling {SHELL_JS_CEILING / 1024:.1f} KB)")
    raise SystemExit(1 if shell_over(current) else 0)
elif __name__ == "__main__":
    ui, out = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        bundle, _ = build(ui)
    except ui_build.StaleBuild as exc:
        raise SystemExit(f"ui bundle: {exc}") from None
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_bytes(bundle.page)
    for path, body in bundle.parts.items():
        (out / path[len(PREFIX) :]).write_bytes(body)
    for path, (_, found) in bundle.sections.items():
        (out / path[len(PREFIX) :]).write_bytes(index_map(found))
