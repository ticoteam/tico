#!/usr/bin/env python3
"""Build, check and render one page from a template in this skill (SKILL.md says when to use which).

    python3 page.py build  DRAFT.html --out PAGE.html        real screenshots in, red numbered arrows drawn on them
    python3 page.py check  PAGE.html --kind KIND              the page's rules; exit 1 names each one it breaks (a
                                                              long spec or a long bullet is a warning that does not fail)
    python3 page.py render FILE_ID --kind KIND [--out DIR]    the published page, checked again and drawn at 390 and 1280 px
    python3 page.py kit    PAGE.html                          hints on its page kit tags (any page, a widget too); never fails

KIND is bug-spec, screen-spec, change-spec, validation, review, qa or release. Standard library only.
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

KINDS = ("bug-spec", "screen-spec", "change-spec", "validation", "review", "qa", "release")
# The sections each kind must have, by id. Every page is a title, one line saying what it is, the contents, then its
# sections, each an H2 (SKILL.md, "The eight rules"). Other sections are the template's too, kept when they say something.
SECTIONS = {
    "bug-spec": ("report", "cause", "solution", "criteria", "scope"),
    "screen-spec": ("pictures", "criteria", "scope"),
    "change-spec": ("behaviour", "criteria", "scope"),
    "validation": ("criteria", "version", "not-checked"),
    "review": ("findings", "checked", "head", "not-checked"),
    "qa": ("strip", "plan", "proof", "how", "risk", "findings", "head", "reviews", "not-checked"),
    "release": ("changelog", "confidence", "plan", "risks", "tasks"),
}
MAX_RISKS = 3
TILES = 4  # a QA page's verdict strip: criteria, checks, review findings, risk
FOLD_LINES = 10  # output longer than this sits in a <details>, never on the face of the page
MAX_BYTES = 8 * 1024 * 1024
DESC_WORDS = 40  # the line under the title: what the page is, in one plain sentence
PARAGRAPH_WORDS = 50  # longer is a blob: bullets, one line each
BULLET_WORDS = 30  # a longer bullet is a warning: one line each
# A spec may run long when clarity needs it, so its length is a warning (Nov 18); every other page's is a rule.
SPECS = ("bug-spec", "screen-spec", "change-spec")
PAGE_WORDS = {
    "bug-spec": 1200,
    "screen-spec": 1200,
    "change-spec": 1000,
    "validation": 1000,
    "review": 1800,
    "qa": 1800,
    "release": 1500,
}
# What only the team reads: a changelog entry is for customers.
INTERNAL = re.compile(r"(?<![\w&])#\d+\b|\b(PR|pull request|commit|refactor(ed|ing)?|migration)\b", re.I)
# A page never hands the checking to its reader.
ASKS_TO_TEST = re.compile(
    r"\b(please (check|test|look|verify|confirm)|can you (check|test|look)|could you (check|test|look)"
    r"|go and (check|test)|send (me |us )?a screenshot|test it on your)\b",
    re.I,
)
FILLER = re.compile(
    r"\b(seamless(ly)?|leverag(e|es|ing)|robust|delightful|cutting[- ]edge|best[- ]in[- ]class)\b", re.I
)
DESC = re.compile(r"</h1>\s*<p\b[^>]*\bclass=\"[^\"]*\bdesc\b[^\"]*\"[^>]*>(.*?)</p>", re.S | re.I)
TOC = re.compile(r"<nav\b[^>]*\bclass=\"[^\"]*\btoc\b[^\"]*\"[^>]*>(.*?)</nav>", re.S | re.I)
H2_SECTION = re.compile(r"<section\b[^>]*\bid=\"([^\"]+)\"[^>]*>\s*<h2\b", re.I)
FORM = re.compile(r"<(form|input|select|textarea|button)\b", re.I)
PICTURE = re.compile(r"data:image/(png|jpeg|webp);base64,", re.I)
FIGURE = re.compile(r"<figure\b(?:[^>\"]|\"[^\"]*\")*>(.*?)</figure>", re.S | re.I)
# Attribute values may hold ">" (an arrow is written "1: 600,120 -> 420,500"), so a tag runs to the first ">" outside quotes.
ATTRS = r"((?:[^>\"]|\"[^\"]*\")*?)"
SHOT = re.compile(r"<figure\b" + ATTRS + r"\bdata-shot=\"([^\"]+)\"" + ATTRS + r">(.*?)</figure>", re.S | re.I)
ARROW = re.compile(r"(\d+)\s*:\s*(\d+)\s*,\s*(\d+)\s*->\s*(\d+)\s*,\s*(\d+)")
STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.S | re.I)
STYLE_ATTR = re.compile(r"""\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
# A colour or background declaration, and a literal colour in its value (#hex, rgb(), hsl(), a colour name). page.py's own
# red arrows are fill and stroke, which are not here, so they never count.
COLOUR_DECL = re.compile(
    r"(?:^|[^\w-])(?:color|background(?:-color|-image)?|border(?:-(?:top|right|bottom|left))?(?:-color)?|outline-color)"
    r"\s*:([^;{}]*)",
    re.I,
)
LITERAL_COLOUR = re.compile(
    r"#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?)\("
    r"|(?:^|[^\w-])(?:white|black|gray|grey|silver|red|green|blue|yellow|orange|purple|pink|brown|navy|teal|maroon"
    r"|olive|lime|aqua|fuchsia|cyan|magenta)\b",
    re.I,
)
# The page kit's tags and their attributes (SKILL.md, "The page kit"). A slip is a hint, never a failure: the page still
# reads without the kit. Tico's live blocks are tags of their own, filled in by Tico, not the kit.
KIT_TAGS = {
    "tico-stat": ("label", "value", "sub", "trend"),
    "tico-chart": ("kind", "data", "label"),
    "tico-table": (),
    "tico-list": (),
    "tico-badge": ("tone",),
}
KIT_VALUES = {("tico-chart", "kind"): ("bars", "line"), ("tico-badge", "tone"): ("warn", "strong")}
LIVE_TAGS = ("tico-tasks", "tico-task", "tico-files", "tico-kpi")
ANY_ATTR = re.compile(r"^(id|class|style|title|hidden|lang|dir|role|slot|data-[\w-]+|aria-[\w-]+)$")
KIT_TAG = re.compile(r"<(tico-[\w-]+)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.I)
TAG_ATTR = re.compile(r"""([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")
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


def own_css(text):
    """The CSS the page carries itself: its <style> blocks and its style= attributes."""
    return "\n".join(STYLE_BLOCK.findall(text)) + "\n" + "\n".join(a or b for a, b in STYLE_ATTR.findall(text))


def own_colours(css):
    """The literal colours the CSS writes in a colour or background declaration. A token (var(), its fallback included)
    and a picture's url() are not the page's own colours, so they do not count."""
    css = re.sub(r"url\((?:\"[^\"]*\"|'[^']*'|[^)\"'])*\)", "", css, flags=re.I)
    css = re.sub(r"var\([^()]*\)", "", css, flags=re.I)
    return [m.group(0) for m in COLOUR_DECL.finditer(css) if LITERAL_COLOUR.search(m.group(1))]


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
    out += shape_problems(text)
    for name in SECTIONS[kind]:
        if not re.search(rf"\bid=\"{name}\"", text):
            out.append(f'no section id="{name}"')
    # A chart (<figure class="chart">) draws numbers; every other picture is a real screenshot of the app.
    figures = [f for f in FIGURE.finditer(text) if not re.match(r"<figure\b[^>]*\bclass=\"[^\"]*\bchart\b", f.group(0))]
    for i, fig in enumerate(figures, 1):
        if not PICTURE.search(fig.group(1)):
            out.append(f"picture {i} is not a screenshot: every picture is the real app, never a drawing")
    if kind == "screen-spec" and len(figures) < 2:
        out.append("a screen spec shows before and after: at least two screenshots")
    if re.search(r"class=\"[^\"]*\bmock", text, re.I):
        out.append("a drawn mock-up: show the real app instead")
    css = own_css(text)
    if own_colours(css) and not re.search(r"prefers-color-scheme", css, re.I):
        out.append(
            "colours are light-only: use the template's tokens (var(--ink)...) or add dark values under prefers-color-scheme"
        )
    if kind == "change-spec":
        behaviour = section(text, "behaviour")
        if behaviour and len(re.findall(r"<pre\b", behaviour, re.I)) < 2:
            out.append("a change spec shows the behaviour before and after: two <pre> blocks")
    if kind == "bug-spec":
        solution = section(text, "solution") or ""
        pairs = len(re.findall(r"<figure\b", solution, re.I)), len(re.findall(r"<pre\b", solution, re.I))
        if max(pairs) < 2:
            out.append("a bug spec shows the solution before and after: two screenshots, or two <pre> blocks")
    if kind == "qa":
        out += qa_problems(text)
    if kind == "release":
        risks = len(re.findall(r"<li\b", section(text, "risks") or "", re.I))
        if risks > MAX_RISKS:
            out.append(f"{risks} risks; name the {MAX_RISKS} biggest")
        changelog = section(text, "changelog") or ""
        changelog = html.unescape(re.sub(r"<(style|svg)\b.*?</\1>|<[^>]+>", " ", changelog, flags=re.S | re.I))
        for m in INTERNAL.finditer(changelog):
            out.append(f"'{m.group(0)}' in the changelog: it is written for customers, in their words")
    plain = " ".join(words(text))
    for m in ASKS_TO_TEST.finditer(plain):
        out.append(f"'{m.group(0)}': the page brings the proof; it never asks the reader to check")
    for m in FILLER.finditer(plain):
        out.append(f"'{m.group(0)}': say what it does instead")
    if kind not in SPECS and len(words(text)) > PAGE_WORDS[kind]:
        out.append(
            f"{len(words(text))} words; a {kind} page keeps under {PAGE_WORDS[kind]}: cut what the reader does not need"
        )
    return out


def shape_problems(text):
    """The shape every page has: one H1, one plain line under it saying what the page is, contents linking each H2,
    headings in order, bullets over blobs, and nothing to fill in."""
    out = []
    h1 = len(re.findall(r"<h1\b", text, re.I))
    if h1 != 1:
        out.append(f"{h1} H1 headings: a page has one, its title")
    desc = DESC.search(text)
    if not desc:
        out.append('no line under the title: <p class="desc">This is a spec to ...</p>, right after the H1')
    else:
        said = " ".join(words(desc.group(1)))
        if not re.match(r"This\b", said):
            out.append(
                'the line under the title says what the page is, starting "This is": "This is the QA page for ..."'
            )
        if len(words(desc.group(1))) > DESC_WORDS:
            out.append(f"the line under the title has {len(words(desc.group(1)))} words; at most {DESC_WORDS}")
    linked = H2_SECTION.findall(text)
    if len(re.findall(r"<h2\b", text, re.I)) > len(linked):
        out.append('an H2 that does not open a <section id="...">: the contents cannot link it')
    toc = TOC.search(text)
    if not toc:
        out.append('no contents: <nav class="toc"> after the line under the title, linking every H2')
    else:
        hrefs = re.findall(r"href=\"#([^\"]+)\"", toc.group(1))
        for name in linked:
            if name not in hrefs:
                out.append(f'the contents do not link "#{name}"')
        for name in hrefs:
            if name not in linked:
                out.append(f'the contents link "#{name}", which is not an H2 section on the page')
    levels = [int(n) for n in re.findall(r"<h([1-6])\b", text, re.I)]
    if levels and levels[0] != 1:
        out.append(f"the page opens with an H{levels[0]}: the title (H1) comes first")
    for i in range(1, len(levels)):
        before, after = levels[i - 1], levels[i]
        if after > before + 1:
            out.append(f"an H{after} right after an H{before}: headings go H1, H2, H3 in order")
            break
    for block in re.findall(r"<p\b[^>]*>(.*?)</p>", text, re.S | re.I):
        said = words(block)
        if len(said) > PARAGRAPH_WORDS:
            out.append(
                f'a paragraph of {len(said)} words ("{" ".join(said[:6])} ..."): split it into bullets, one line each'
            )
    if FORM.search(text):
        out.append("a form or a button: the page asks for nothing; what to OK is plain from what the page is")
    return out


def warnings_in(text, kind):
    """What the page may keep but should look at again: a spec over its length, which clarity can need, and a bullet
    longer than a line."""
    out = []
    count = len(words(text))
    if kind in SPECS and count > PAGE_WORDS[kind]:
        out.append(
            f"{count} words; a {kind} page is usually under {PAGE_WORDS[kind]}: cut every word clarity does not need"
        )
    for item in re.findall(r"<li\b[^>]*>(.*?)</li>", text, re.S | re.I):
        said = words(item)
        if len(said) > BULLET_WORDS:
            out.append(f'a bullet of {len(said)} words ("{" ".join(said[:6])} ..."): one line each, or two bullets')
    return out


def qa_problems(text):
    """A QA page's own parts: the verdict strip, the diagram of how it works with the files changed, risk and
    rollback, and long output folded away. Each missing part is named, so the page is fixed before Jev reads it."""
    out = []
    strip = section(text, "strip")
    if strip is not None:
        tiles = len(re.findall(r"class=\"[^\"]*\btile\b", strip))
        if tiles != TILES:
            out.append(
                f"the verdict strip has {tiles} tiles; it has {TILES}: criteria passed, checks at the head, open "
                "review findings, risk"
            )
    how = section(text, "how")
    if how is not None:
        rows = re.findall(
            r"<tr\b.*?(?:</tr>|(?=<tr\b)|$)", re.sub(r"<thead\b.*?</thead>", "", how, flags=re.S | re.I), re.S | re.I
        )
        if not rows:
            out.append("how it works has no table of the files changed, with their lines and a total")
        files = [row for row in rows if not re.search(r">\s*(?:<b>\s*)?total\b", row, re.I)]
        changed = re.search(r"class=\"[^\"]*\bbox\b[^\"]*\bchanged\b", how)
        boxes = len(re.findall(r"class=\"[^\"]*\bbox\b", how))
        if not changed and len(files) > 1:
            out.append(
                "how it works has no diagram: the parts the change touches as boxes, the changed ones "
                'class="box changed" (only a one-file change may name its file instead)'
            )
        elif changed and boxes < 2:
            out.append("how it works shows one box: draw the parts around the change too, for context")
    risk = section(text, "risk")
    if risk is not None:
        # Each under a heading of its own (the section's own "Risk and rollback" names both and proves neither).
        parts = re.split(r"(?=<h3\b)", risk, flags=re.I)
        risk_part = next((p for p in parts if re.match(r"<h3\b[^>]*>\s*risk\b", p, re.I)), None)
        rollback = next((p for p in parts if re.match(r"<h3\b[^>]*>\s*rollback\b", p, re.I)), None)
        if not risk_part:
            out.append('risk and rollback has no "Risk: low | medium | high" heading over what could go wrong')
        if not rollback:
            out.append('risk and rollback has no "Rollback" heading over how to undo it')
        elif not re.search(r"\bdata\b|\bmigration\b", " ".join(words(rollback)), re.I):
            out.append("the rollback does not say whether stored data changes")
    folded = re.sub(r"<details\b.*?</details>", "", text, flags=re.S | re.I)
    for i, block in enumerate(re.findall(r"<pre\b[^>]*>(.*?)</pre>", folded, re.S | re.I), 1):
        lines = len(block.strip().splitlines())
        if lines > FOLD_LINES:
            out.append(
                f"output {i} on the face of the page is {lines} lines: fold anything over {FOLD_LINES} into "
                "<details><summary>…</summary><pre>…</pre></details>"
            )
    return out


def section(text, name):
    """The HTML of <section id="name"> up to its own end (sections inside it are not counted), or None."""
    start = re.search(rf"<section\b[^>]*\bid=\"{name}\"[^>]*>", text)
    if not start:
        return None
    depth, at = 1, start.end()
    for tag in re.finditer(r"<(/?)section\b[^>]*>", text[at:], re.I):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return text[at : at + tag.start()]
    return text[at:]


def kit_hints(text):
    """What the page kit would not draw as the page means: an unknown kit tag, an attribute or value it does not take,
    or a chart with no rows."""
    out = []
    for m in KIT_TAG.finditer(text):
        tag, attrs = m.group(1).lower(), m.group(2)
        if tag in LIVE_TAGS:
            continue
        if tag not in KIT_TAGS:
            out.append(f"<{tag}> is not a kit tag; the kit has " + ", ".join(f"<{t}>" for t in KIT_TAGS))
            continue
        given = {a.lower(): b or c or d or "" for a, b, c, d in TAG_ATTR.findall(attrs.rstrip("/"))}
        for name, value in given.items():
            if name not in KIT_TAGS[tag] and not ANY_ATTR.match(name):
                takes = ", ".join(KIT_TAGS[tag]) or "no attributes of its own"
                out.append(f'<{tag}> has no "{name}" attribute; it takes {takes}')
            allowed = KIT_VALUES.get((tag, name))
            if allowed and value not in allowed:
                out.append(f'<{tag} {name}="{value}">: {name} is ' + " or ".join(allowed))
        inside = re.split(r"</tico-chart", text[m.end() :], maxsplit=1, flags=re.I)[0]
        if tag == "tico-chart" and "data" not in given and not re.search(r"<li\b", inside, re.I):
            out.append('<tico-chart> has no rows: data="Label 10, Label 20" or a list of "Label 10" items inside it')
    return out


def kit(path):
    hints = kit_hints(Path(path).read_text())
    for line in hints:
        print("kit: hint:", line)
    print("kit: ok" if not hints else f"kit: {len(hints)} hint(s); the page still reads without the kit")


def check(path, kind):
    text = Path(path).read_text()
    found = problems_in(text, kind)
    for line in warnings_in(text, kind):
        print("check: warning:", line)
    for line in kit_hints(text):
        print("check: hint:", line)
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
    read = subprocess.run(["tico", "file", "read", file_id], capture_output=True, text=True, check=False)
    if read.returncode:
        sys.exit("render: tico file read failed: " + read.stderr.strip())
    text = json.loads(read.stdout)["text"]
    out = Path(out or tempfile.mkdtemp(prefix="page-render-")).resolve()  # as_uri() needs an absolute path
    out.mkdir(parents=True, exist_ok=True)
    page = out / "published.html"
    page.write_text(text)
    found = problems_in(text, kind)
    for line in warnings_in(text, kind):
        print("published page: warning:", line)
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
                check=False,
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
    k = sub.add_parser("kit")
    k.add_argument("page")
    a = p.parse_args()
    if a.cmd == "build":
        build(a.draft, a.out)
    elif a.cmd == "check":
        check(a.page, a.kind)
    elif a.cmd == "kit":
        kit(a.page)
    else:
        render(a.file_id, a.kind, a.out)


if __name__ == "__main__":
    main()
