"""The card view of a page (docs/pages.md "Widget view"): the page's own card CSS survives publishing, and the height
script marks the page's view from the card frame's fragment, with no change on the server side of the request."""
import json
import pathlib
import shutil
import subprocess

import pytest

from backend import visuals as V

SCRIPT = pathlib.Path(V.__file__).with_name("visual-reporter.js")

# Runs the page's own script against a stub document: argv is the script, then each fragment in turn. The first
# fragment is the one the page loads with; each later one is a hashchange. Prints the attributes after each step.
HARNESS = r"""
const fs = require('fs');
const [file, ...hashes] = process.argv.slice(2);
const attrs = {}, listeners = {}, seen = [];
global.document = {documentElement: {clientWidth: 0, setAttribute: (k, v) => { attrs[k] = v; }}};
global.location = {hash: hashes[0]};
global.window = {};
global.addEventListener = (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); };
global.parent = {postMessage() {}};
eval(fs.readFileSync(file, 'utf8'));
seen.push({...attrs});
for (const hash of hashes.slice(1)) {
  location.hash = hash;
  for (const fn of listeners.hashchange || []) fn();
  seen.push({...attrs});
}
console.log(JSON.stringify(seen));
"""


def test_publishing_keeps_the_card_view_selectors_and_still_drops_scripts_and_handlers():
    page = (b'<!doctype html><html><head><style>.full-only{display:block}'
            b'[data-tico-view=card] .full-only{display:none}[data-tico-view="card"] .card-only{display:block}'
            b'[data-tico-size="small"] h2{font-size:14px}</style><script>alert(1)</script></head>'
            b'<body><h2 class="full-only" onclick="x()">Report</h2></body></html>')
    cleaned, unembedded = V.clean(page, "page", base=False)
    assert b"[data-tico-view=card] .full-only{display:none}" in cleaned
    assert b'[data-tico-view="card"] .card-only{display:block}' in cleaned
    assert b'[data-tico-size="small"] h2{font-size:14px}' in cleaned
    assert b"<script" not in cleaned and b"alert" not in cleaned and b"onclick" not in cleaned
    assert unembedded == []


def test_the_height_script_marks_the_view_from_the_card_fragment_and_follows_hash_changes(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node runs the page's own script")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    hashes = ["", "#tico-view=card&tico-size=small", "#tico-view=card&tico-size=bogus", "#tico-view=card",
              "#tico-view=full&tico-size=large"]
    run = subprocess.run([node, str(harness), str(SCRIPT), *hashes], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [
        {"data-tico-view": "full", "data-tico-size": "full"},      # no fragment: the full page
        {"data-tico-view": "card", "data-tico-size": "small"},
        {"data-tico-view": "card", "data-tico-size": "medium"},    # a size that is not one of the three
        {"data-tico-view": "card", "data-tico-size": "medium"},
        {"data-tico-view": "full", "data-tico-size": "full"},
    ]
