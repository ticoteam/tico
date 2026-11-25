"""Pages are dark by default (docs/pages.md "Dark by default"): the height script gives a page that colours neither
<html> nor <body> a dark background and light text, unless the page's own text colours are for a light page; then it
makes the page light instead, so its dark headings and paragraphs never sit on the dark background."""

import json
import pathlib
import shutil
import subprocess

import pytest

from backend import visuals as V

SCRIPT = pathlib.Path(V.__file__).with_name("visual-reporter.js")

# Runs the page's own script against a small stub document: argv is the script, then the page as JSON
# ({html, body: {css, kids}}, each kid {tag, css, text, kids, hidden}). Colours inherit as in a browser; a style the
# script sets on <html> is read back the way getComputedStyle reports it. Prints <html>'s style afterwards.
HARNESS = r"""
const fs = require('fs');
const [file, json] = process.argv.slice(2);
const page = JSON.parse(json);
const hex = (h) => 'rgb(' + [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)).join(', ') + ')';
const make = (spec, parent) => {
  const e = {tagName: (spec.tag || 'div').toUpperCase(), css: spec.css || {}, parentNode: parent, hidden: spec.hidden};
  const nodes = (spec.text ? [{nodeType: 3, data: spec.text}] : []).concat((spec.kids || []).map((k) => make(k, e)));
  nodes.forEach((n, i) => { n.nextSibling = nodes[i + 1] || null; });
  e.firstChild = nodes[0] || null;
  e.kids = nodes.filter((n) => n.nodeType !== 3);
  e.getClientRects = () => (e.hidden || (parent && parent.getClientRects && !parent.getClientRects().length) ? [] : [{}]);
  e.getElementsByTagName = () => { const all = []; const walk = (x) => x.kids.forEach((k) => { all.push(k); walk(k); }); walk(e); return all; };
  return e;
};
const html = {tagName: 'HTML', css: page.html || {}, style: {}, clientWidth: 0, setAttribute() {}};
const body = make({tag: 'body', css: page.body.css, kids: page.body.kids}, html);
html.kids = [body];
global.document = {documentElement: html, body};
global.getComputedStyle = (e) => {
  if (e === html) {
    const own = html.style.color ? hex(html.style.color) : html.css.color;
    return {color: own || 'rgb(0, 0, 0)', backgroundColor: html.css.backgroundColor || 'rgba(0, 0, 0, 0)', backgroundImage: 'none'};
  }
  return {
    color: e.css.color || getComputedStyle(e.parentNode).color,
    backgroundColor: e.css.backgroundColor || 'rgba(0, 0, 0, 0)',
    backgroundImage: e.css.backgroundImage || 'none',
  };
};
global.location = {hash: ''};
global.window = {};
global.addEventListener = () => {};
global.parent = {postMessage() {}};
eval(fs.readFileSync(file, 'utf8'));
console.log(JSON.stringify(html.style));
"""

INK, MUTED, LIGHT, BLUE = "rgb(26, 26, 26)", "rgb(85, 85, 85)", "rgb(238, 238, 238)", "rgb(59, 130, 246)"
DARK = {"colorScheme": "dark", "background": "#191b1a", "color": "#e8eae9"}


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


def test_a_page_whose_own_text_colours_are_for_a_light_page_is_shown_light_not_dark_on_dark(tmp_path):
    assert run(report(), tmp_path) == {"colorScheme": "light", "background": "#fff", "color": ""}
    # Only the headings coloured: still a light page's colours, and dark headings would vanish on the dark background.
    headings = {
        "body": {"kids": [{"tag": "h2", "css": {"color": INK}, "text": "Results"}, {"tag": "p", "text": "x" * 500}]}
    }
    assert run(headings, tmp_path)["background"] == "#fff"


def test_a_plain_page_and_a_page_with_light_text_stay_dark(tmp_path):
    plain = {
        "body": {
            "kids": [
                {"tag": "h1", "text": "Signups"},
                {"tag": "p", "text": "1,204 this week"},
                {"tag": "a", "text": "more"},
            ]
        }
    }
    assert run(plain, tmp_path) == DARK
    # A dark palette set on its text and not on <body>, with a blue accent darker than the cut: still dark.
    palette = {
        "body": {
            "kids": [
                {"tag": "h1", "css": {"color": LIGHT}, "text": "Signups this week"},
                {"tag": "p", "css": {"color": LIGHT}, "text": "Up 12% on last week, mostly from the new landing page."},
                {"tag": "span", "css": {"color": BLUE}, "text": "+12%"},
            ]
        }
    }
    assert run(palette, tmp_path) == DARK
    # Light text on <body>: the script colours nothing but the background, as before.
    assert run(report(css={"color": LIGHT}), tmp_path) == {"colorScheme": "dark", "background": "#191b1a"}


def test_dark_text_on_its_own_light_box_or_a_page_with_its_own_background_is_left_alone(tmp_path):
    boxed = {
        "body": {
            "kids": [
                {"tag": "p", "text": "x" * 200},
                {
                    "tag": "div",
                    "css": {"backgroundColor": "rgb(255, 255, 255)"},
                    "kids": [{"tag": "p", "css": {"color": INK}, "text": "on a card"}],
                },
                {
                    "tag": "pre",
                    "css": {"backgroundImage": "linear-gradient(#fff, #eee)"},
                    "kids": [{"tag": "code", "css": {"color": INK}, "text": "x = 1"}],
                },
            ]
        }
    }
    assert run(boxed, tmp_path) == DARK
    assert run(report(css={"backgroundColor": "rgb(255, 255, 255)"}), tmp_path) == {}
    assert run({**report(), "html": {"backgroundColor": "rgb(17, 17, 17)"}}, tmp_path) == {}
