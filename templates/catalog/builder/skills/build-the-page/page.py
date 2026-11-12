#!/usr/bin/env python3
"""Build, check and render one page from a template in this skill (SKILL.md says when to use which).

    python3 page.py build  DRAFT.html --out PAGE.html        real screenshots in, red numbered arrows drawn on them
    python3 page.py check  PAGE.html --kind KIND              the page's rules; exit 1 names each one it breaks
    python3 page.py render FILE_ID --kind KIND [--out DIR]    the published page, checked again and drawn at 390 and 1280 px

KIND is screen-spec, change-spec, validation or review. Standard library only.
"""

import argparse
import base64
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

KINDS = ("screen-spec", "change-spec", "validation", "review")
# The sections each kind must have, by id. Details come after the pictures, inside <section id="details">.
SECTIONS = {
    "screen-spec": ("pictures", "details", "goal", "scope", "criteria"),
    "change-spec": ("behaviour", "details", "goal", "scope", "criteria"),
    "validation": ("criteria", "details", "version", "not-checked"),
    "review": ("checked", "details", "head", "findings", "not-checked"),
}
MAX_BYTES = 8 * 1024 * 1024
LEAD_WORDS = 70
PAGE_WORDS = {"screen-spec": 1200, "change-spec": 1000, "validation": 1000, "review": 1800}
# A page never hands the checking to its reader.
ASKS_TO_TEST = re.compile(
    r"\b(please (check|test|look|verify|confirm)|can you (check|test|look)|could you (check|test|look)"
    r"|go and (check|test)|send (me |us )?a screenshot|test it on your)\b",
    re.I,
)
FILLER = re.compile(
    r"\b(seamless(ly)?|leverag(e|es|ing)|robust|delightful|cutting[- ]edge|best[- ]in[- ]class)\b", re.I
)
PICTURE = re.compile(r"data:image/(png|jpeg|webp);base64,", re.I)
FIGURE = re.compile(r"<figure\b(?:[^>\"]|\"[^\"]*\")*>(.*?)</figure>", re.S | re.I)
# Attribute values may hold ">" (an arrow is written "1: 600,120 -> 420,500"), so a tag runs to the first ">" outside quotes.
ATTRS = r"((?:[^>\"]|\"[^\"]*\")*?)"
SHOT = re.compile(r"<figure\b" + ATTRS + r"\bdata-shot=\"([^\"]+)\"" + ATTRS + r">(.*?)</figure>", re.S | re.I)
ARROW = re.compile(r"(\d+)\s*:\s*(\d+)\s*,\s*(\d+)\s*->\s*(\d+)\s*,\s*(\d+)")
CHROMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def size_of(data):
    """(width, height) of a PNG or JPEG, read from its header."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker, length = data[i + 1], struct.unpack(">H", data[i + 2 : i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return w, h
            i += 2 + length
    raise ValueError("not a PNG or JPEG")


def figure(n, path, data, arrows, shown, caption):
    """One screenshot as an inline SVG, with its arrows in the picture's own pixels so they stay on their target."""
    w, h = size_of(data)
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    # Sized for the reader: 3 px lines and a 14 px circle at the width the picture is shown, or at 700 px for a wide
    # picture, which a phone shrinks to a third.
    scale = w / min(shown, 700)
    stroke, r = round(3 * scale), round(14 * scale)
    parts = [
        f'<figure class="shot"><svg viewBox="0 0 {w} {h}" width="{shown}" role="img" '
        f'aria-label="{html.escape(Path(path).name)}">',
        f'<defs><marker id="arrow{n}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" '
        f'orient="auto"><path d="M0 0L10 5L0 10z" style="fill:var(--fail,#c53030)"/></marker></defs>',
        f'<image href="data:{mime};base64,{base64.b64encode(data).decode()}" width="{w}" height="{h}"/>',
    ]
    for num, x1, y1, x2, y2 in arrows:
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" marker-end="url(#arrow{n})" '
            f'style="stroke:var(--fail,#c53030);stroke-width:{stroke}"/>'
            f'<circle cx="{x1}" cy="{y1}" r="{r}" style="fill:var(--fail,#c53030)"/>'
            f'<text x="{x1}" y="{y1}" dy="0.35em" text-anchor="middle" '
            f'style="fill:#fff;font:700 {round(r * 1.2)}px var(--sans,system-ui,sans-serif)">{num}</text>'
        )
    parts.append("</svg>" + caption + "</figure>")
    return "".join(parts)


def build(src, out):
    text = Path(src).read_text()
    base = Path(src).resolve().parent
    problems = []
    count = 0

    def one(match):
        nonlocal count
        attrs, path, rest, inner = match.groups()
        count += 1
        file = (base / path).resolve()
        if not file.is_file():
            problems.append(f"{path}: no such screenshot")
            return match.group(0)
        data = file.read_bytes()
        spec = re.search(r'data-arrows="([^"]*)"', attrs + rest)
        arrows = [tuple(int(v) for v in a) for a in ARROW.findall(spec.group(1))] if spec else []
        if spec and "{{" in spec.group(1):
            problems.append(f"{path}: its arrows are still a placeholder")
        shown = re.search(r'data-width="(\d+)"', attrs + rest)
        try:
            w, _ = size_of(data)
        except ValueError:
            problems.append(f"{path}: not a PNG or JPEG")
            return match.group(0)
        width = int(shown.group(1)) if shown else (390 if "-390" in path else min(w, 1280))
        return figure(count, path, data, arrows, width, inner)

    built = SHOT.sub(one, text)
    if problems:
        sys.exit("build: " + "; ".join(problems))
    Path(out).write_text(built)
    print(json.dumps({"page": str(out), "pictures": count, "bytes": len(built.encode())}))


def words(fragment):
    fragment = re.sub(r"<(style|pre|code|svg)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    return re.findall(r"[\w'’-]+", html.unescape(re.sub(r"<[^>]+>", " ", fragment)))


def problems_in(text, kind):
    out = []
    low = text.lower()
    if len(text.encode()) > MAX_BYTES:
        out.append(
            f"the page is {len(text.encode()) // 1024} KB; the limit is 8 MB: crop the pictures or save them as JPEG"
        )
    if "<script" in low:
        out.append("a script: pages run none")
    if re.search(r"\bsrc=\"(?!data:)|<link\b|@import|url\((?!\)|['\"]?(data:|#))", text, re.I):
        out.append("something loaded from outside the page: pictures go in as data: URIs (page.py build)")
    if "{{" in text or re.search(r"\bTODO\b", text):
        out.append("a placeholder is still in the page ({{...}} or TODO)")
    if 'data-shot="' in text:
        out.append("a screenshot was not built in: run page.py build first")
    lead = re.search(r"<(\w+)\b[^>]*\bid=\"lead\"[^>]*>(.*?)</\1>", text, re.S | re.I)
    if not lead:
        out.append('no opening: the page starts with <div id="lead">, what it is and what to decide')
    else:
        lines = len(re.findall(r"<(p|li)\b", lead.group(2), re.I)) or 1
        if lines > 3:
            out.append(f"the opening has {lines} lines; at most 3")
        if len(words(lead.group(2))) > LEAD_WORDS:
            out.append(f"the opening has {len(words(lead.group(2)))} words; at most {LEAD_WORDS}")
        if not re.search(r"\bdecide\b", lead.group(2), re.I):
            out.append("the opening does not say what to decide (a line starting 'Decide:')")
        if kind == "change-spec" and not (
            re.search(r"\bcause\b", lead.group(2), re.I) and re.search(r"\bfix\b", lead.group(2), re.I)
        ):
            out.append("a change spec opens with its cause and its fix")
        first = min((m.start() for m in re.finditer(r"<(figure|section)\b", text, re.I)), default=len(text))
        if lead.start() > first:
            out.append("the opening is not the first thing on the page")
    for section in SECTIONS[kind]:
        if not re.search(rf"\bid=\"{section}\"", text):
            out.append(f'no section id="{section}"')
    details = text.find('id="details"')
    figures = list(FIGURE.finditer(text))
    for i, fig in enumerate(figures, 1):
        if not PICTURE.search(fig.group(1)):
            out.append(f"picture {i} is not a screenshot: every picture is the real app, never a drawing")
        if details != -1 and fig.start() > details:
            out.append(f"picture {i} is in the details: pictures come before the details")
    if kind == "screen-spec" and len(figures) < 2:
        out.append("a screen spec shows before and after: at least two screenshots")
    if re.search(r"class=\"[^\"]*\bmock", text, re.I):
        out.append("a drawn mock-up: show the real app instead")
    if kind == "change-spec":
        behaviour = re.search(r"id=\"behaviour\".*?</section>", text, re.S)
        if behaviour and len(re.findall(r"<pre\b", behaviour.group(0), re.I)) < 2:
            out.append("a change spec shows the behaviour before and after: two <pre> blocks")
    plain = " ".join(words(text))
    for m in ASKS_TO_TEST.finditer(plain):
        out.append(f"'{m.group(0)}': the page brings the proof; it never asks the reader to check")
    for m in FILLER.finditer(plain):
        out.append(f"'{m.group(0)}': say what it does instead")
    if len(words(text)) > PAGE_WORDS[kind]:
        out.append(f"{len(words(text))} words; a {kind} page keeps under {PAGE_WORDS[kind]}: cut the details")
    return out


def check(path, kind):
    found = problems_in(Path(path).read_text(), kind)
    for line in found:
        print("check:", line)
    if found:
        sys.exit(1)
    print("check: ok")


def chrome():
    for name in ([os.environ["TICO_CHROME"]] if os.environ.get("TICO_CHROME") else []) + list(CHROMES):
        found = name if os.path.exists(name) else shutil.which(name)
        if found:
            return found
    return None


def render(file_id, kind, out):
    read = subprocess.run(["tico", "file", "read", file_id], capture_output=True, text=True)
    if read.returncode:
        sys.exit("render: tico file read failed: " + read.stderr.strip())
    text = json.loads(read.stdout)["text"]
    out = Path(out or tempfile.mkdtemp(prefix="page-render-"))
    out.mkdir(parents=True, exist_ok=True)
    page = out / "published.html"
    page.write_text(text)
    found = problems_in(text, kind)
    for line in found:
        print("published page:", line)
    browser = chrome()
    shots = []
    if browser:
        for width, height in ((390, 3000), (1280, 2400)):
            shot = out / f"published-{width}.png"
            subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    f"--window-size={width},{height}",
                    f"--screenshot={shot}",
                    page.as_uri(),
                ],
                capture_output=True,
                timeout=90,
            )
            if shot.is_file() and shot.stat().st_size > 2000:
                shots.append(str(shot))
            else:
                found.append(f"it did not draw at {width} px")
                print(f"published page: it did not draw at {width} px")
    else:
        print("published page: not drawn, no Chrome on this computer (set TICO_CHROME)")
    print(json.dumps({"html": str(page), "look_at": shots, "problems": len(found)}))
    if found or not browser:
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("draft")
    b.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("page")
    c.add_argument("--kind", choices=KINDS, required=True)
    r = sub.add_parser("render")
    r.add_argument("file_id")
    r.add_argument("--kind", choices=KINDS, required=True)
    r.add_argument("--out")
    a = p.parse_args()
    if a.cmd == "build":
        build(a.draft, a.out)
    elif a.cmd == "check":
        check(a.page, a.kind)
    else:
        render(a.file_id, a.kind, a.out)


if __name__ == "__main__":
    main()
