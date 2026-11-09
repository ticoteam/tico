#!/usr/bin/env python3
"""Mechanical checks for a brand-book artifact. The coach runs this; it never judges taste.

  audit.py contrast '#FFFFFF' '#F4B942'     one WCAG ratio (paste this output, never a number worked out by eye)
  audit.py markdown artifact.json          the book as Markdown, for people to read
  audit.py check artifact.json [transcript.json]
                                            every check below, one line per finding; exit 1 if any

Checks: never-claim catch phrases and blocked words anywhere in the book's own copy; wrong name forms; contrast
numbers in the Look section recomputed from the hexes; word counts on labelled lines; the rule pack's size and shape;
items still tagged default. Quoted bad copy is exempt: the claims, words and names tables themselves, customer
words, the voice "Not like" column, off-brand examples, and lines that start with Never/Block/Warn/Avoid.

With the transcript: every quote in a `by: founder` item is in a founder turn word for word, every "founder's call"
cites the turn where the founder made it ("founder's call, turn 47"), and every line labelled [customer words] is a
real customer-words row.

In Tico the same checks run on the server (brand/check) from structured fields; this script reads the prototype's
table format.
"""

import json
import re
import sys

EXEMPT_SECTIONS = {"claims", "words", "naming", "customer_words", "governance"}
EXEMPT_LINE = re.compile(r"^\s*(never|block|warn|avoid|not like)\b", re.I)
EXEMPT_FIELD = re.compile(r"break|alternative|perception|options heard", re.I)
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
NAMED = {"white": "#FFFFFF", "black": "#000000"}


def lum(hexcode):
    def ch(v):
        v = v / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    h = hexcode.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ratio(fg, bg):
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def text_of(cell):
    return cell.get("text", "") if isinstance(cell, dict) else str(cell)


def copy_lines(art):
    """(where, text) for every line of the book's own copy that the claim and word checks should read."""
    yield "headline", art.get("headline", "")
    yield "summary", art.get("summary", "")
    for sec in art.get("sections", []):
        sid, d = sec["id"], sec.get("data", {})
        if sid in EXEMPT_SECTIONS:
            continue
        for i, it in enumerate(d.get("items", [])):
            if isinstance(it, dict) and EXEMPT_FIELD.search(it.get("label", "")):
                continue
            t = it.get("value", it.get("text", "")) if isinstance(it, dict) else str(it)
            yield f"{sid}[{i}]", t
        cols = [c.lower() for c in d.get("columns", [])]
        for r, row in enumerate(d.get("rows", [])):
            cells = [text_of(c) for c in row]
            if sid == "examples" and any(c.strip().lower() == "off" for c in cells):
                continue
            for c, t in enumerate(cells):
                col = cols[c] if c < len(cols) else ""
                if col in ("not like", "never", "by") or col.startswith("source"):
                    continue
                yield f"{sid} row {r + 1} {col}".strip(), t
    for p in art.get("proposed_changes", []):
        yield "proposed_changes", text_of(p.get("text", "")) if isinstance(p, dict) else str(p)


def section(art, sid):
    return next((s for s in art.get("sections", []) if s["id"] == sid), None)


def phrases(cell, sep=r";"):
    """Catch phrases are split on ';' (a phrase may hold a comma); word lists on ';' or ','."""
    return [p.strip().strip("'\"") for p in re.split(sep, text_of(cell)) if p.strip()]


def loose(p):
    """A phrase matches with any spacing or hyphen between its words and any case."""
    words = [re.escape(w) for w in re.split(r"[\s-]+", p) if w]
    return re.compile(r"(?<!\w)" + r"[\s-]+".join(words) + r"(?!\w)", re.I)


def check(art):
    out = []
    # Sentence by sentence, so "Never claim: waterproof." inside a longer rule is exempt but the rest is read.
    lines = [
        (w, s)
        for w, t in copy_lines(art)
        for s in re.split(r"(?<=[.!?])\s+", t or "")
        if s and not EXEMPT_LINE.match(s)
    ]

    # Never-claims: the Catch column holds the phrases that give the claim away.
    claims = section(art, "claims")
    if claims:
        cols = [c.lower() for c in claims["data"].get("columns", [])]
        if "catch" not in cols:
            out.append("claims: no Catch column, so never-claims can't be checked; add the phrases that give each away")
        else:
            ci = cols.index("catch")
            for row in claims["data"]["rows"]:
                if not text_of(row[0]).lower().startswith("never"):
                    continue
                for p in phrases(row[ci]):
                    rx = loose(p)
                    for w, t in lines:
                        if rx.search(t):
                            out.append(f"never-claim '{p}' in {w}: {t[:110]}")

    # Claim IDs: every [A3] cited anywhere must be an allowed claim; every message's proof must cite one.
    allowed = set()
    if claims:
        for row in claims["data"].get("rows", []):
            first = text_of(row[0])
            if first.lower().startswith("allowed"):
                allowed.update(re.findall(r"\bA\d+\b", first))
    for w, t in copy_lines(art):
        for ref in re.findall(r"\[([^\]]*\bA\d+\b[^\]]*)\]", t or ""):
            for cid in re.findall(r"\bA\d+\b", ref):
                if cid not in allowed:
                    out.append(f"claim {cid} cited in {w} is not an allowed claim")
    msgs = section(art, "messages")
    if msgs:
        cols = [c.lower() for c in msgs["data"].get("columns", [])]
        if "proof" in cols:
            pi = cols.index("proof")
            for r, row in enumerate(msgs["data"].get("rows", [])):
                cell = row[pi] if pi < len(row) else ""
                tag = cell.get("tag") if isinstance(cell, dict) else None
                stated_none = "now" in " ".join(text_of(c) for c in row[:2]).lower() and text_of(
                    cell
                ).lower().startswith("none")
                if not re.search(r"\bA\d+\b", text_of(cell)) and tag not in ("assumption", "gap") and not stated_none:
                    out.append(f"messages row {r + 1}: proof cites no allowed claim ID and isn't tagged assumption")

    # Customer words: count real quotes (a source, not tagged assumption).
    cw = section(art, "customer_words")
    if cw:
        cols = [c.lower() for c in cw["data"].get("columns", [])]
        si = cols.index("source") if "source" in cols else None
        real = 0
        for row in cw["data"].get("rows", []):
            q = row[0]
            if isinstance(q, dict) and q.get("tag") == "assumption":
                continue
            if si is None or text_of(row[si]).strip():
                real += 1
        if real < 5:
            out.append(f"customer words: {real} sourced verbatim quotes, the bar is 5")

    # Blocked words.
    words = section(art, "words")
    if words:
        for row in words["data"].get("rows", []):
            cells = [text_of(c) for c in row]
            if cells[0].lower() == "avoid" and cells[-1].lower() == "block":
                for p in phrases(cells[1], r"[;,]"):
                    rx = loose(p)
                    for w, t in lines:
                        if rx.search(t):
                            out.append(f"blocked word '{p}' in {w}: {t[:110]}")

    # Wrong name forms (case-sensitive; right forms are removed first so 'Starter' doesn't hit 'Home Starter').
    naming = section(art, "naming")
    if naming:
        rights, wrongs = [], []
        for row in naming["data"].get("rows", []):
            cells = [text_of(c) for c in row]
            rights.append(re.sub(r"\s*\[.*?\]|\s*\(.*?\)", "", cells[1]).strip())
            for wf in re.split(r";", cells[-1]):
                wf = re.sub(r"\[.*?\]|\(.*?\)", "", wf).replace(" alone", "").strip()
                if wf and wf.lower() not in ("none", "none seen"):
                    wrongs.append(wf)
        rights.sort(key=len, reverse=True)
        for w, t in lines:
            bare = t
            for r in rights:
                bare = bare.replace(r, " ")
            for wf in wrongs:
                if re.search(r"(?<!\w)" + re.escape(wf) + r"(?!\w)", bare):
                    out.append(f"wrong name '{wf}' in {w}: {t[:110]}")

    # Contrast: recompute every 'A on B ... N.NN:1' in Look from the colour rows.
    look = section(art, "visual")
    if look:
        names = dict(NAMED)
        rows = [[text_of(c) for c in r] for r in look["data"].get("rows", [])]
        for cells in rows:
            m = HEX.search(cells[1]) if len(cells) > 1 else None
            if cells[0].lower().startswith("colour") and m:
                names[cells[1][: m.start()].strip().lower()] = m.group(0)
        checked = 0
        for cells in rows:
            if not cells[0].lower().startswith("contrast"):
                continue
            pairs = [p.strip() for p in cells[1].split("/")]
            nums = re.findall(r"(\d+(?:\.\d+)?):1(?!\s*needed)", cells[2])
            nums = [n for n in nums if n not in ("4.5", "3")]
            for pair, said in zip(pairs, nums, strict=False):
                pm = re.match(r"(?:the )?(.+?) on (.+?)(?: \(|$)", pair, re.I)
                if not pm:
                    continue
                fg, bg = (names.get(x.strip().lower()) for x in pm.groups())
                if not fg or not bg:
                    out.append(f"contrast: can't find hexes for '{pair}'")
                    continue
                real = ratio(fg, bg)
                checked += 1
                if abs(real - float(said)) > 0.02:
                    out.append(f"contrast: '{pair}' says {said}:1, the hexes give {real:.2f}:1")
        print(f"(checked {checked} contrast numbers)", file=sys.stderr)

    # Word counts on labelled lines: '(10 words)' or 'counted: 25'.
    for sec in art.get("sections", []):
        for it in sec.get("data", {}).get("items", []):
            if not isinstance(it, dict):
                continue
            m = re.search(r"(\d+) words|counted: (\d+)", it.get("label", ""))
            if m:
                want = int(m.group(1) or m.group(2))
                body = re.split(r"\s\(by:|\sOptions heard", it.get("value", ""))[0]
                body = re.sub(r"\s*\[[^\]]*\]", "", body)  # sources and claim IDs aren't words
                got = len(re.findall(r"[\w$][\w$,.'’-]*", body))
                if got != want:
                    out.append(f"word count: '{it['label']}' is {got} words, not {want}")

    # Sample pieces: on-brand pieces stay under 80 words (sources and claim IDs don't count).
    ex = section(art, "examples")
    if ex:
        cols = [c.lower() for c in ex["data"].get("columns", [])]
        ti = cols.index("text") if "text" in cols else 1
        for r, row in enumerate(ex["data"].get("rows", [])):
            cells = [text_of(c) for c in row]
            if any(c.strip().lower() == "off" for c in cells):
                continue
            n = len(re.findall(r"[\w$][\w$,.'’-]*", re.sub(r"\s*\[[^\]]*\]", "", cells[ti])))
            if n > 80:
                out.append(f"examples row {r + 1}: {n} words, the limit is 80")

    # Rule pack: at most 15 lines, no word lists (Tico checks blocked words and names itself).
    pack = section(art, "rule_pack")
    if pack:
        items = [text_of(i) for i in pack["data"].get("items", [])]
        if len(items) > 12:
            out.append(f"rule pack: {len(items)} rules, the limit is 12")
        for i, t in enumerate(items):
            if t.count(",") >= 5:
                out.append(f"rule pack line {i + 1} reads like a word list; Tico checks those, keep rules")

    # Defaults left.
    n = sum(1 for _, t in copy_lines(art) if re.search(r"\(by: default\)|^default$", t))
    if n:
        out.append(f"{n} items still tagged default; read them back before finishing")
    return out


def norm(t):
    """Case, punctuation and spacing don't count; words do."""
    return " ".join(re.findall(r"[\w$]+", t.replace("’", "'").lower()))


def check_transcript(art, tr):
    """The book may only say the founder said or chose something the transcript shows."""
    out = []
    turns = tr.get("turns", [])
    said = norm(" ".join(t["text"] for t in turns if t.get("who") == "person"))
    cw = section(art, "customer_words")
    quotes = [norm(text_of(r[0])) for r in cw["data"].get("rows", [])] if cw else []
    extra = [(f"{sec['id']} {k}", sec[k]) for sec in art.get("sections", []) for k in ("open", "note") if sec.get(k)]
    extra.append(("resume", art.get("resume") or ""))
    for w, t in list(copy_lines(art)) + extra:
        t = t or ""
        if "by: founder" in t:
            for q in re.findall(r'"([^"]{12,})"', t):
                if norm(q) not in said:
                    out.append(f"{w}: quoted as the founder's but in no founder turn: \"{q[:70]}\"")
        for m in re.finditer(r"founder's call", t, re.I):
            tm = re.search(r"turn (\d+)", t[m.start() : m.start() + 80])
            if not tm:
                out.append(f"{w}: founder's call cites no turn")
                continue
            n = int(tm.group(1))
            if not (1 <= n <= len(turns)) or turns[n - 1].get("who") != "person":
                out.append(f"{w}: founder's call cites turn {n}, which isn't a founder turn")
        for q in re.findall(r'"([^"]{8,})"\s*\[customer words\]', t, re.I):
            if not any(norm(q) in c or c in norm(q) for c in quotes):
                out.append(f"{w}: labelled customer words but no customer-words row has it: \"{q[:70]}\"")
    return out


def cell(c):
    t = text_of(c).replace("|", "\\|").replace("\n", " ")
    return f"{t} ({c['tag']})" if isinstance(c, dict) and c.get("tag") else t


def markdown(art):
    """The book for people: headline, then each section in template order, open notes kept."""
    out = [f"# {art.get('artifact', 'Brand book')}: {art.get('company', '')}".rstrip(": "), ""]
    if art.get("headline"):
        out += [f"**{art['headline']}**", ""]
    if art.get("summary"):
        out += [art["summary"], ""]
    for sec in art.get("sections", []):
        d = sec.get("data", {})
        out += [f"## {sec.get('title', sec['id'])}", ""]
        if sec.get("open"):
            out += [f"> Open: {sec['open']}", ""]
        if "columns" in d:
            out.append("| " + " | ".join(d["columns"]) + " |")
            out.append("|" + "---|" * len(d["columns"]))
            out += ["| " + " | ".join(cell(c) for c in row) + " |" for row in d.get("rows", [])]
        elif sec.get("block") == "meters":
            out += [
                f"- **{i.get('label', '')}**: {i.get('score', '')}/{d.get('max', 10)}. {i.get('text', '')}"
                for i in d.get("items", [])
            ]
        elif sec.get("block") == "fields":
            out += [f"- **{i.get('label', '')}**: {i.get('value', '')}" for i in d.get("items", [])]
        else:
            out += [f"- {cell(i)}" for i in d.get("items", [])]
        if sec.get("note"):
            out += ["", f"_{sec['note']}_"]
        out.append("")
    for key, title in (("decisions", "Decisions"), ("assumptions", "Assumptions")):
        if art.get(key):
            out += [f"## {title}", ""] + [f"- {text_of(x)}" for x in art[key]] + [""]
    if art.get("next_steps"):
        out += ["## Next steps", ""]
        out += [
            f"- {x.get('text', '')} ({x.get('owner', '')})" if isinstance(x, dict) else f"- {x}"
            for x in art["next_steps"]
        ]
    return "\n".join(out).rstrip() + "\n"


def main(argv):
    if len(argv) == 4 and argv[1] == "contrast":
        r = ratio(argv[2], argv[3])
        verdict = "pass text" if r >= 4.5 else "large text and marks only" if r >= 3 else "fail"
        print(f"{argv[2]} on {argv[3]}: {r:.2f}:1 {verdict}")
        return 0
    if len(argv) == 3 and argv[1] == "markdown":
        sys.stdout.write(markdown(json.load(open(argv[2]))))
        return 0
    if len(argv) in (3, 4) and argv[1] == "check":
        art = json.load(open(argv[2]))
        findings = check(art)
        if len(argv) == 4:
            findings += check_transcript(art, json.load(open(argv[3])))
        for f in findings:
            print(f)
        print(f"{len(findings)} findings")
        return 1 if findings else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
