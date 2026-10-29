#!/usr/bin/env python3
"""Rebuild the Material Symbols Outlined subset the UI ships.

Scans ui/index.html, ui/*.js, ui/app/*.js and ui/styles/*.css for icon names, adds the `icon:` of every catalog card
(templates/catalog/*/card.yaml) and group (templates/groups.yaml), writes them to
ui/vendor/fonts/icons.txt and downloads a woff2 that holds just those glyphs from Google Fonts. Run it
after adding an icon or a template:

    python3 scripts/build-icon-font.py          # rescan and download
    python3 scripts/build-icon-font.py --stamp  # only put the font's hash in its URLs
    python3 scripts/build-icon-font.py --check  # rescan only; exit 1 if icons.txt or the font's URL is stale

The font is a ligature font: an icon missing from it shows as its raw name. The axes match the
CSS in ui/styles/ (FILL 0, wght 200, GRAD 0, opsz 24).

Every URL of the font carries `?v=<hash of the font>`, rewritten here with each new font. A browser, a desktop app's web
view or a proxy that kept the old subset would otherwise serve it under the same URL after an update, and an icon the
update added (Automations' `bolt`) would read as its name.
"""
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
FONT_DIR = UI / "vendor" / "fonts"
MANIFEST = FONT_DIR / "icons.txt"
FONT = FONT_DIR / "material-symbols-outlined.woff2"
AXES = "opsz,wght,FILL,GRAD@24,200,0,0"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

NAME = r"[a-z][a-z0-9_]*"
# Classes whose text content is an icon name (each is styled with the icon font in ui/styles/).
ICON_CLASSES = ("nav-icon", "mobile-nav-icon", "int-key", "agent-mark", "person-mail-link", "material-symbols-outlined",
                "ob-ms")
PATTERNS = [
    # 1. An element with an icon class whose only content is the name: <span class="nav-icon">check_circle</span>
    re.compile(r'class="[^"]*\b(?:%s)\b[^"]*"[^>]*>\s*(%s)\s*<' % ("|".join(ICON_CLASSES), NAME)),
    # 2. Names inside an icon map: const TASK_VIEW_ICONS = {list: 'view_list', ...}
    ("map", re.compile(r"\b\w*_ICONS\s*=\s*\{([^}]*)\}")),
    # 3. Fallbacks: MAP[key] || 'more_horiz'  and  ...querySelector('.nav-icon')?.textContent || 'article'
    re.compile(r"_ICONS\[[^\]]*\]\s*\|\|\s*'(%s)'" % NAME),
    re.compile(r"\.nav-icon'\)\?\.textContent\s*\|\|\s*'(%s)'" % NAME),
    # 4. A name picked into an `icon` variable: const icon = done ? 'a' : 'b';
    ("icon-var", re.compile(r"\bconst icon\s*=\s*([^;\n]*);")),
]


TEMPLATES = ROOT / "templates"
# `icon: name` on its own line in a card or in templates/groups.yaml.
YAML_ICON = re.compile(r"^\s*(?:-\s+)?icon:\s*['\"]?(%s)['\"]?\s*(?:#.*)?$" % NAME, re.M)


def template_icons():
    """The icons the catalog and the departments name, which the UI shows next to each bot and department."""
    files = sorted(TEMPLATES.glob("catalog/*/card.yaml")) + [TEMPLATES / "groups.yaml"]
    found = set()
    for path in files:
        if path.is_file():
            found.update(YAML_ICON.findall(path.read_text(encoding="utf-8")))
    return found


def sources():
    files = [UI / "index.html"] + sorted(UI.glob("*.js")) + sorted(UI.glob("app/*.js")) + sorted(UI.glob("styles/*.css"))
    return [f for f in files if f.is_file()]


def used_icons():
    """Every icon name the UI renders through the icon font, sorted."""
    found = set()
    for path in sources():
        text = path.read_text(encoding="utf-8")
        for pat in PATTERNS:
            if isinstance(pat, tuple):
                kind, rx = pat
                for m in rx.finditer(text):
                    # A map value is quoted; in a ternary only the branches are icons, not the tested value.
                    rx = r"'(%s)'" if kind == "map" else r"[?:]\s*'(%s)'"
                    found.update(re.findall(rx % NAME, m.group(1)))
            else:
                found.update(pat.findall(text))
    return sorted(found | template_icons())


def css_url(icons):
    return ("https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:%s&icon_names=%s&display=block"
            % (AXES, ",".join(icons)))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


# The files that name the font's URL: the page's preload, the @font-face rule and the Overview's own FontFace.
FONT_URL_FILES = [UI / "index.html", UI / "styles" / "tokens.css", UI / "overview" / "building.js"]
FONT_URL = re.compile(r"(?<![\w/])/vendor/fonts/material-symbols-outlined\.woff2(?:\?v=[0-9a-f]*)?")


def font_version():
    return hashlib.sha256(FONT.read_bytes()).hexdigest()[:12]


def font_url():
    return "/vendor/fonts/material-symbols-outlined.woff2?v=" + font_version()


def stale_font_urls():
    """The files whose font URL is not this font's, by path relative to the repo."""
    want = font_url()
    return [str(f.relative_to(ROOT)) for f in FONT_URL_FILES
            if any(m.group(0) != want for m in FONT_URL.finditer(f.read_text(encoding="utf-8")))
            or not FONT_URL.search(f.read_text(encoding="utf-8"))]


def stamp_font_urls():
    want = font_url()
    for f in FONT_URL_FILES:
        text = f.read_text(encoding="utf-8")
        f.write_text(FONT_URL.sub(want, text), encoding="utf-8")


def read_manifest():
    if not MANIFEST.is_file():
        return []
    return [l.strip() for l in MANIFEST.read_text().splitlines() if l.strip()]


def main(argv):
    icons = used_icons()
    if "--check" in argv:
        if read_manifest() != icons:
            print("icons.txt is stale; run scripts/build-icon-font.py", file=sys.stderr)
            return 1
        if stale_font_urls():
            print("the font's URL is stale in %s; run scripts/build-icon-font.py --stamp" % ", ".join(stale_font_urls()), file=sys.stderr)
            return 1
        return 0
    if "--stamp" in argv:           # the URLs only, for the font as it is (no download)
        stamp_font_urls()
        return 0
    MANIFEST.write_text("\n".join(icons) + "\n")
    css = fetch(css_url(icons)).decode()
    urls = re.findall(r"url\((https://[^)]+)\)\s*format\('woff2'\)", css)
    if len(urls) != 1:
        sys.exit("expected one woff2 url in the Google Fonts CSS, got:\n" + css)
    FONT.write_bytes(fetch(urls[0]))
    stamp_font_urls()
    print("%d icons, %d bytes -> %s" % (len(icons), FONT.stat().st_size, FONT.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
