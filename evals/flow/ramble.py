"""The ramble cases (ramble_cases.json) and how a split of one is checked. `hard` is what must hold whatever the model
answered, because the server enforces it (backend/flow_questions.py `read` and `fallback`): known sections only, never
the summary or the vision, mostly the owner's own words, within the page's limits and counts, and the whole text for
the section asked when nothing was split. `soft` is how good the split is, which only a real model can be scored on:
the sections it should fill, the phrases it should keep, the words it should leave out. `split` runs a model's answer
through the server's own filter, as the route does, for the live eval (evals/flow/run.py)."""

import json
import re
from pathlib import Path

from backend import flow_questions as FQ
from backend import strategy as S

HERE = Path(__file__).parent
CASES = HERE / "ramble_cases.json"
WHYS = ("no_model", "spend_cap", "limit", "failed")
NEVER = ("summary", "vision")
WORD = re.compile(r"[\w'’$%-]+", re.UNICODE)
OWN_SHARE = 0.8  # a line's words that must be the owner's (the server's OWN_WORDS)


def load(path=CASES):
    return json.loads(Path(path).read_text())["cases"]


def _words(text):
    return [w.strip("-'’").lower() for w in WORD.findall(text or "") if w.strip("-'’")]


def own_share(line, text):
    """The share of `line`'s words that are in `text`: an independent count, not the server's own."""
    mine, own = _words(line), set(_words(text))
    return sum(w in own for w in mine) / len(mine) if mine else 0.0


def limit(section):
    return S.STATEMENT_MAX if section == FQ.BET else S.SECTION_MAX


def hard(case, result):
    """Why `result` (the route's answer) breaks what the server promises for `case`, as a list (empty when it holds)."""
    text, asked, out = case["text"], case["asked"], []
    if set(result) != {"lines", "split", "why"}:
        return [f"answer has {sorted(result)}"]
    lines = result["lines"]
    if not isinstance(lines, list) or not lines:
        return ["no lines: the owner's answer would be lost"]
    for line in lines:
        section, words = line.get("section"), str(line.get("text") or "")
        if section in NEVER or section not in FQ.KEYS:
            out.append(f"a line for {section!r}")
        if not words.strip():
            out.append(f"an empty line for {section}")
        if section in FQ.KEYS and len(words) > limit(section):
            out.append(f"a {section} line of {len(words)} characters")
    if result["split"] is True:
        if result["why"] is not None:
            out.append("split with a why")
        if len(lines) > FQ.LINES_MAX:
            out.append(f"{len(lines)} lines")
        if sum(x.get("section") == FQ.BET for x in lines) > FQ.BETS_MAX:
            out.append("more bets than BETS_MAX")
        for line in lines:
            if own_share(str(line.get("text") or ""), text) < OWN_SHARE:
                out.append(f"not the owner's words: {line.get('text')!r}")
            if set(line) - {"section", "text"}:
                out.append(f"a split line carries {sorted(set(line) - {'section', 'text'})}")
    elif result["split"] is False:
        if result["why"] not in WHYS:
            out.append(f"why {result['why']!r}")
        if lines != FQ.whole(text, asked):
            out.append("not split, but not the whole text for the section asked")
        if [x["section"] for x in lines] != [asked]:
            out.append("the whole text under another section")
    else:
        out.append(f"split is {result['split']!r}")
    return out


def _has(lines, phrase):
    return any(phrase.lower() in str(x.get("text") or "").lower() for x in lines)


def _said(lines, word):
    pattern = re.compile(r"(?<![\w'’-])" + re.escape(word.lower()) + r"(?![\w'’-])")
    return any(pattern.search(str(x.get("text") or "").lower()) for x in lines)


def soft(case, result, raw=None):
    """How good the split is: {sections, keep, drop, usable, bets, passed}. `raw` is the model's own answer, before the
    server's filter, when there was one (to count the bets it gave, which the server would cap)."""
    expect, lines = case["expect"], result.get("lines") or []
    split = result.get("split") is True
    got = {x.get("section") for x in lines} if split else set()
    want = set(expect["sections"])
    sections = len(want & got) / len(want) if want else (1.0 if not split else 0.0)
    keep = [p for p in expect["keep"] if not (split and _has(lines, p))]
    drop = [w for w in expect["drop"] if split and _said(lines, w)]
    usable = split if expect.get("usable", True) else not split
    given = (raw or {}).get("lines") if isinstance(raw, dict) else None
    bets = sum(isinstance(x, dict) and x.get("section") == FQ.BET for x in given) if isinstance(given, list) else 0
    bets_ok = bets <= int(expect.get("bets_max", FQ.BETS_MAX))
    passed = usable and sections >= 0.75 and not keep and not drop and bets_ok
    return {"sections": sections, "missed": keep, "said": drop, "usable": usable, "bets_ok": bets_ok, "passed": passed}


def split(answer, case):
    """A model's `answer` through the server's own filter, as POST /flow/ramble gives it back."""
    lines = FQ.read(answer, case["text"])
    return {"lines": lines, "split": True, "why": None} if lines else FQ.fallback(case["text"], case["asked"], "failed")


def careful(case):
    """The careful model's answer for `case`."""
    return {"lines": [dict(x) for x in case["careful"]]}
