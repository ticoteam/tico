#!/usr/bin/env python3
"""Rebuild the Inter subset the UI sets its text in (ui/vendor/fonts/inter-variable.woff2).

Takes InterVariable.woff2 from the pinned Inter release, limits its weight axis to the 400-700 the styles use
(the optical-size axis stays: titles get Inter's display cut), keeps Latin, Latin Extended and the punctuation,
arrows and symbols the UI writes, and the layout features it needs (kerning, ligatures, contextual alternates,
tabular figures, case-sensitive forms). Needs fontTools with brotli (`pip install fonttools brotli`).

    python3 scripts/build-text-font.py                 # download the release and rebuild
    python3 scripts/build-text-font.py --zip Inter.zip # from a release zip already on disk
    python3 scripts/build-text-font.py --stamp         # only put the font's hash in its URLs
    python3 scripts/build-text-font.py --check         # exit 1 if a URL does not carry the font's hash

Every URL of the font carries `?v=<hash of the font>`: the server keeps a versioned font for a year (backend/app.py),
so a new subset must arrive under a new URL.
"""
import hashlib
import io
import re
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
FONT = UI / "vendor" / "fonts" / "inter-variable.woff2"
RELEASE = "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip"
RELEASE_SHA256 = "9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e"
MEMBER = "web/InterVariable.woff2"
WEIGHTS = (400, 700)
# Latin, Latin-1, Latin Extended-A/B and Additional, spacing and combining marks; then what the UI writes: general
# punctuation, currency, letterlike symbols, arrows, a few operators and technical keys (⌘ ⏎), shapes, check marks.
UNICODES = ("U+0000-024F,U+0259,U+02B0-02FF,U+0300-036F,U+03B8,U+1E00-1EFF,U+2000-206F,U+20A0-20CF,U+2100-2122,"
            "U+2190-21FF,U+2212,U+2215,U+2260-2265,U+22EF,U+2303,U+2318,U+23CE,U+25A0-25FF,U+2713,U+2715,U+2717,"
            "U+FB01-FB02,U+FEFF,U+FFFD")
FEATURES = "kern,liga,calt,ccmp,locl,mark,mkmk,rvrn,tnum,case"

# The files that name the font's URL: the page's preload and the @font-face rule.
FONT_URL_FILES = [UI / "index.html", UI / "styles" / "tokens.css"]
FONT_URL = re.compile(r"(?<![\w/])/vendor/fonts/inter-variable\.woff2(?:\?v=[0-9a-f]*)?")


def font_url():
    return "/vendor/fonts/inter-variable.woff2?v=" + hashlib.sha256(FONT.read_bytes()).hexdigest()[:12]


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


def release_zip(path):
    data = Path(path).read_bytes() if path else urllib.request.urlopen(RELEASE, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != RELEASE_SHA256:
        sys.exit("the Inter release does not match its pinned sha256")
    return data


def build(zip_path):
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    with zipfile.ZipFile(io.BytesIO(release_zip(zip_path))) as z:
        font = TTFont(io.BytesIO(z.read(MEMBER)))
    font = instancer.instantiateVariableFont(font, {"wght": WEIGHTS})
    font.recalcTimestamp = False        # the release's own date: the same input builds the same bytes
    with tempfile.TemporaryDirectory() as tmp:
        limited = Path(tmp) / "limited.ttf"
        font.save(limited)
        subset.main([str(limited), "--unicodes=" + UNICODES, "--layout-features=" + FEATURES,
                     "--flavor=woff2", "--output-file=" + str(FONT)])


def main(argv):
    if "--check" in argv:
        stale = stale_font_urls()
        if stale:
            sys.exit("the Inter URL is not this font's in: " + ", ".join(stale)
                     + "\nrun python3 scripts/build-text-font.py --stamp")
        return
    if "--stamp" not in argv:
        build(argv[argv.index("--zip") + 1] if "--zip" in argv else None)
        print(f"{FONT.relative_to(ROOT)}: {FONT.stat().st_size // 1024} KB")
    stamp_font_urls()
    print(font_url())


if __name__ == "__main__":
    main(sys.argv[1:])
