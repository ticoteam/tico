#!/usr/bin/env python3
"""Build the text font the UI sets its text in (ui/vendor/fonts/atkinson-hyperlegible-next-plain-zero.woff2).

The source is the Latin subset of Atkinson Hyperlegible Next beside it (ui/vendor/fonts/atkinson-hyperlegible-next.woff2,
SIL OFL 1.1). scripts/build_plain_zero.py adds a plain zero, the zero without its slash, on the ss09 feature. The result is
a modified font, so it carries its own family name.

    python3 scripts/build-text-font.py                 # rebuild the font from its source, then stamp its URLs
    python3 scripts/build-text-font.py --stamp         # only put the font's hash in its URLs
    python3 scripts/build-text-font.py --check         # exit 1 if a URL does not carry the font's hash

Needs fontTools with brotli (`pip install fonttools brotli`).

Every URL of the font carries `?v=<hash of the font>`: the server keeps a versioned font for a year (backend/app.py),
so a new build must arrive under a new URL.
"""

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
SOURCE = UI / "vendor" / "fonts" / "atkinson-hyperlegible-next.woff2"
FONT = UI / "vendor" / "fonts" / "atkinson-hyperlegible-next-plain-zero.woff2"
PLAIN_ZERO = Path(__file__).resolve().parent / "build_plain_zero.py"

# The files that name the font's URL: the page's preload and the @font-face rule.
FONT_URL_FILES = [UI / "index.html", UI / "styles" / "tokens.css"]
FONT_URL = re.compile(r"(?<![\w/])/vendor/fonts/atkinson-hyperlegible-next-plain-zero\.woff2(?:\?v=[0-9a-f]*)?")


def font_url():
    return "/vendor/fonts/atkinson-hyperlegible-next-plain-zero.woff2?v=" + hashlib.sha256(FONT.read_bytes()).hexdigest()[:12]


def stale_font_urls():
    want = font_url()
    stale = []
    for f in FONT_URL_FILES:
        found = [m.group(0) for m in FONT_URL.finditer(f.read_text(encoding="utf-8"))]
        if not found or any(u != want for u in found):
            stale.append(str(f.relative_to(ROOT)))
    return stale


def stamp_font_urls():
    want = font_url()
    for f in FONT_URL_FILES:
        f.write_text(FONT_URL.sub(want, f.read_text(encoding="utf-8")), encoding="utf-8")


def build():
    subprocess.run([sys.executable, str(PLAIN_ZERO), str(SOURCE), str(FONT), "--attach"], check=True)


def main(argv):
    if "--check" in argv:
        stale = stale_font_urls()
        if stale:
            sys.exit(
                "the text font's URL is not this font's in: "
                + ", ".join(stale)
                + "\nrun python3 scripts/build-text-font.py --stamp"
            )
        return
    if "--stamp" not in argv:
        build()
        print(f"{FONT.relative_to(ROOT)}: {FONT.stat().st_size // 1024} KB")
    stamp_font_urls()
    print(font_url())


if __name__ == "__main__":
    main(sys.argv[1:])
