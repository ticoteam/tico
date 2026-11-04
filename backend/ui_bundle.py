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
        js.append(f"// file: {name}\n{text.rstrip()}\n")
    css = [f"/* file: {name} */\n{path.read_text().rstrip()}\n" for name, path in files["css"]]
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
