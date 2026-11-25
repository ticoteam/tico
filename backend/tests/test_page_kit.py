"""The page kit (backend/visuals.py KIT): a page that uses a kit tag is served with the kit it was published with,
after the height reporter; a page without one is served as before; a later kit never changes a published version."""

import pytest

from backend import visuals as V
from backend.tests.helpers.api import headers
from backend.tests.helpers.files import publish, turn
from backend.tests.helpers.visuals import VISUALS

KIT_PAGE = '<tico-stat label="Signups" value="1,204" trend="+12%"></tico-stat><p>plan</p>'
PLAIN = "<p>no kit here</p><tico-stat-other>a page's own tag</tico-stat-other>"


@pytest.fixture(autouse=True)
def full(api, monkeypatch):
    monkeypatch.setattr(api.app.state.store.settings, "visual_origin", VISUALS)


def served(api, fid, version):
    token = V.mint(V.key_for(api.app, api.app.state.store.settings), "human:ben", fid, version)
    r = api.get(VISUALS + "/visuals/" + token)
    assert r.status_code == 200, r.text
    return r.content


def reporter(data):
    return data.encode() + b"<script>" + V.REPORTER.encode() + b"</script>"


def test_a_page_with_a_kit_tag_runs_the_kit_it_was_published_with_and_others_are_served_as_written(api, monkeypatch):
    _, attempt = turn(api)
    made = publish(api, attempt, name="signups.html", text=KIT_PAGE)
    assert made.status_code == 200, made.text
    fid = made.json()["file"]["id"]
    plain = publish(api, attempt, name="plain.html", text=PLAIN).json()["file"]["id"]
    with api.app.state.store.read() as c:
        kits = dict(c.execute("SELECT file_id, page_kit FROM bot_file_versions WHERE file_id IN (?,?)", (fid, plain)))
    assert kits == {fid: V.KIT, plain: V.KIT}
    assert served(api, fid, 1) == reporter(KIT_PAGE) + b"<script>" + V.KITS[1] + b"</script>"
    assert served(api, plain, 1) == reporter(PLAIN), "no kit tag: the page as before"
    info = api.get(f"/api/v2/files/{fid}/info", headers=headers("ben-test")).json()["file"]
    assert api.get(info["visual_src"]).content.endswith(V.KITS[1] + b"</script>")

    # A new kit is released: the version already published keeps kit 1, the next one is pinned to kit 2.
    monkeypatch.setattr(V, "KIT", 2)
    monkeypatch.setattr(V, "KITS", {**V.KITS, 2: b"/* kit 2 */"})
    again = publish(api, attempt, name="signups.html", text=KIT_PAGE + "<p>week 2</p>")
    assert again.status_code == 200, again.text
    assert again.json()["file"]["id"] == fid
    assert served(api, fid, 1).endswith(b"<script>" + V.KITS[1] + b"</script>")
    assert served(api, fid, 2) == reporter(KIT_PAGE + "<p>week 2</p>") + b"<script>/* kit 2 */</script>"


def test_a_page_from_before_kits_is_served_without_one_even_with_a_kit_tag(api):
    _, attempt = turn(api)
    fid = publish(api, attempt, name="old.html", text=KIT_PAGE).json()["file"]["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_file_versions SET page_kit=NULL WHERE file_id=?", (fid,))
    assert served(api, fid, 1) == reporter(KIT_PAGE)
