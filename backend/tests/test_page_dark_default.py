"""Pages are dark by default (docs/pages.md "Dark by default"): the height script gives a page that colours or paints
neither <html> nor <body> a dark background and light text, unless more of the page's own text would not read on it
(under 3:1) than reads; then it makes the page light instead, so its dark headings and paragraphs never sit dark on
dark. A real browser runs the same cases on RunsOn (PR proof); here a stub document runs the script itself."""

import json
import pathlib
import shutil
import subprocess

import pytest

from backend import visuals as V

SCRIPT = pathlib.Path(V.__file__).with_name("visual-reporter.js")

# Runs the page's own script against a small stub document: argv is the script, then the page as JSON
# ({hash, html, body: {css, kids}}, each kid {tag, css, text, kids, hidden, view, throws}). Colours inherit as in a
# browser; a kid with `view` shows only in that view (card CSS keyed on the data-tico-view the script sets); a style
# the script sets on <html> is read back the way getComputedStyle reports it. Prints <html>'s style and attributes,
# the listeners the script added and how many times it asked for a computed style.
HARNESS = r"""
const fs = require('fs');
const [file, json] = process.argv.slice(2);
const page = JSON.parse(json);
const attrs = {}, listeners = [];
let calls = 0;
const hex = (h) => 'rgb(' + [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)).join(', ') + ')';
const html = {tagName: 'HTML', css: page.html || {}, style: {}, clientWidth: 0, setAttribute: (k, v) => { attrs[k] = v; }};
const make = (spec, parent) => {
  const e = {tagName: (spec.tag || 'div').toUpperCase(), css: spec.css || {}, parentNode: parent, spec};
  const nodes = (spec.text ? [{nodeType: 3, data: spec.text}] : []).concat((spec.kids || []).map((k) => make(k, e)));
  nodes.forEach((n, i) => { n.nextSibling = nodes[i + 1] || null; });
  e.firstChild = nodes[0] || null;
  e.kids = nodes.filter((n) => n.nodeType !== 3);
  e.getClientRects = () => {
    const shown = !spec.hidden && (!spec.view || attrs['data-tico-view'] === spec.view);
    return shown && (parent === html || parent.getClientRects().length) ? [{}] : [];
  };
  e.getElementsByTagName = () => { const all = []; const walk = (x) => x.kids.forEach((k) => { all.push(k); walk(k); }); walk(e); return all; };
  return e;
};
const body = make({tag: 'body', css: page.body.css, kids: page.body.kids}, html);
global.document = {documentElement: html, body};
global.getComputedStyle = (e) => {
  calls++;
  if (e.spec && e.spec.throws) throw new Error('no style');
  if (e === html) {
    const own = html.style.color ? hex(html.style.color) : html.css.color;
    return {color: own || 'rgb(0, 0, 0)', backgroundColor: html.css.backgroundColor || 'rgba(0, 0, 0, 0)',
      backgroundImage: html.css.backgroundImage || 'none'};
  }
  const inherited = () => { const c = calls; const v = getComputedStyle(e.parentNode).color; calls = c; return v; };
  return {
    color: e.css.color || inherited(),
    backgroundColor: e.css.backgroundColor || 'rgba(0, 0, 0, 0)',
    backgroundImage: e.css.backgroundImage || 'none',
  };
};
global.location = {hash: page.hash || ''};
global.window = {};
global.addEventListener = (type) => { listeners.push(type); };
global.parent = {postMessage() {}};
eval(fs.readFileSync(file, 'utf8'));
console.log(JSON.stringify({style: html.style, attrs, listeners, calls}));
"""

INK, MUTED, LIGHT, BLUE = "rgb(26, 26, 26)", "rgb(85, 85, 85)", "rgb(238, 238, 238)", "rgb(59, 130, 246)"
DARK = {"colorScheme": "dark", "backgroundColor": "#191b1a", "color": "#e8eae9"}
LIGHT_PAGE = {"colorScheme": "light", "backgroundColor": "#fff", "color": ""}


def report(**body):
    """The QA report shape: a title, a paragraph and a table, coloured by rules and no background anywhere."""
    return {
        "body": {
            **body,
            "kids": [
                {"tag": "h1", "css": {"color": INK}, "text": "QA: the approval opens on its question"},
                {
                    "tag": "p",
                    "css": {"color": MUTED},
                    "text": "Passed 4 of 4 checks; the new test fails on the old code.",
                },
                {
                    "tag": "table",
                    "kids": [{"tag": "tr", "kids": [{"tag": "td", "text": "AC-3"}, {"tag": "td", "text": "pass"}]}],
                },
                {"tag": "a", "text": "PR #3749"},
                {"tag": "style", "hidden": True, "text": "h1{color:var(--ink,#1a1a1a)}"},
            ],
        }
    }


def run(page, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node runs the page's own script")
    harness = tmp_path / "dark.js"
    harness.write_text(HARNESS)
    done = subprocess.run(
        [node, str(harness), str(SCRIPT), json.dumps(page)], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def style(page, tmp_path):
    return run(page, tmp_path)["style"]


def test_a_page_whose_own_text_colours_do_not_read_on_dark_is_shown_light(tmp_path):
    assert style(report(), tmp_path) == LIGHT_PAGE
    # Its own dark text on <body> and no background: light, with a white background (not left see-through).
    assert style(report(css={"color": "rgb(51, 51, 51)"}), tmp_path) == LIGHT_PAGE


def test_a_plain_page_a_light_palette_and_a_few_accents_stay_dark(tmp_path):
    plain = {"body": {"kids": [{"tag": "h1", "text": "Signups"}, {"tag": "p", "text": "1,204 this week"}]}}
    assert style(plain, tmp_path) == DARK
    # A blue heading (4.7:1 on the dark background) over light labels: everything reads.
    palette = {
        "body": {
            "kids": [
                {"tag": "h1", "css": {"color": BLUE}, "text": "Signups this week"},
                {"tag": "p", "css": {"color": LIGHT}, "text": "Up 12% on last week."},
            ]
        }
    }
    assert style(palette, tmp_path) == DARK
    # Tico's own QA and validation templates published without their tokens: prose in the dark default's colour,
    # a few status accents with their fallbacks, a muted note and a link.
    template = {
        "body": {
            "kids": [
                {"tag": "h1", "text": "QA: the approval opens on its question"},
                {"tag": "p", "text": "This is the QA page for the approval card: 5 of 5 checks pass."},
                {"tag": "span", "css": {"color": "rgb(47, 122, 79)"}, "text": "Pass"},
                {"tag": "span", "css": {"color": "rgb(197, 48, 48)"}, "text": "Fail"},
                {"tag": "span", "css": {"color": "rgb(115, 115, 115)"}, "text": "Not checked yet"},
                {"tag": "span", "css": {"color": MUTED}, "text": "muted"},
                {"tag": "a", "css": {"color": "rgb(47, 95, 208)"}, "text": "Verdict"},
            ]
        }
    }
    assert style(template, tmp_path) == DARK
    # Light text on <body>: the script colours nothing but the background.
    assert style(report(css={"color": LIGHT}), tmp_path) == {"colorScheme": "dark", "backgroundColor": "#191b1a"}


def test_text_on_its_own_background_in_a_link_or_in_a_kit_tag_is_not_counted(tmp_path):
    long = "x" * 200
    kids = [
        {"tag": "p", "text": "x"},
        {
            "tag": "div",
            "css": {"backgroundColor": "rgb(255, 255, 255)"},
            "kids": [{"tag": "p", "css": {"color": INK}, "text": long}],
        },
        {
            "tag": "pre",
            "css": {"backgroundImage": "linear-gradient(#fff, #eee)"},
            "kids": [{"tag": "code", "css": {"color": INK}, "text": long}],
        },
        {"tag": "a", "kids": [{"tag": "code", "css": {"color": INK}, "text": long}]},
        {"tag": "tico-stat", "kids": [{"tag": "span", "css": {"color": INK}, "text": long}]},
    ]
    assert style({"body": {"kids": kids}}, tmp_path) == DARK


def test_a_page_with_its_own_background_colour_or_image_is_left_alone(tmp_path):
    assert style(report(css={"backgroundColor": "rgb(255, 255, 255)"}), tmp_path) == {}
    assert style({**report(), "html": {"backgroundColor": "rgb(17, 17, 17)"}}, tmp_path) == {}
    assert (
        style(report(css={"backgroundImage": "linear-gradient(rgb(255, 255, 255), rgb(238, 238, 238))"}), tmp_path)
        == {}
    )
    assert style({**report(), "html": {"backgroundImage": "linear-gradient(red, blue)"}}, tmp_path) == {}


def test_the_card_view_is_marked_before_the_text_is_read(tmp_path):
    """A dark heading only the full view shows, and light text only the card shows: the card stays dark."""
    page = {
        "hash": "#tico-view=card&tico-size=small",
        "body": {
            "kids": [
                {"tag": "h1", "view": "full", "css": {"color": INK}, "text": "Weekly review: signups and churn"},
                {"tag": "p", "view": "card", "css": {"color": LIGHT}, "text": "1,204"},
            ]
        },
    }
    out = run(page, tmp_path)
    assert out["attrs"] == {"data-tico-view": "card", "data-tico-size": "small"}
    assert out["style"] == DARK


def test_a_colour_it_cannot_read_or_a_failing_style_never_stops_the_rest_of_the_script(tmp_path):
    odd = {
        "body": {
            "kids": [
                {"tag": "p", "css": {"color": "oklch(none none none)"}, "text": "x" * 50},
                {"tag": "p", "css": {"color": "rgb(1e-05, 0, 0)"}, "text": "tiny"},
                {"tag": "p", "css": {"color": "oklch(0.95 0.01 120)"}, "text": "reads well"},
                {"tag": "p", "css": {"color": "lab(10 0 0)"}, "text": "no"},
            ]
        }
    }
    out = run(odd, tmp_path)
    assert out["style"] == DARK  # 6 characters do not read, 10 do; the colour it cannot read is left out
    broken = run({"body": {"kids": [{"tag": "p", "throws": True, "text": "x"}]}}, tmp_path)
    assert broken["attrs"]["data-tico-view"] == "full"
    assert set(broken["listeners"]) == {"hashchange", "load", "resize", "keydown"}


def test_the_text_walk_is_bounded(tmp_path):
    """300 cells under 30 wrappers: one computed style per element, not one per ancestor (Sol measured 18,904)."""
    cells = [{"tag": "td", "css": {"color": INK}, "text": "cell"} for _ in range(300)]
    inner = {"tag": "table", "kids": [{"tag": "tr", "kids": cells}]}
    for _ in range(30):
        inner = {"tag": "div", "kids": [inner]}
    out = run({"body": {"kids": [inner]}}, tmp_path)
    assert out["style"] == LIGHT_PAGE
    assert out["calls"] <= 332 + 5
    # Many elements with no text: it stops after 3,000.
    empty = {
        "body": {"kids": [{"tag": "span"} for _ in range(5000)] + [{"tag": "p", "css": {"color": INK}, "text": "x"}]}
    }
    out = run(empty, tmp_path)
    assert out["style"] == DARK and out["calls"] <= 3000 + 5
