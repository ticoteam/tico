"""The Company page's backend seams: what the mission discloses, the edge's route allowlist, and the switch's side effects.
Acme is the fictional company. Each test stands for a way something could leave the install that the owner did not allow."""
import json
import re
from datetime import datetime, timezone

from backend import company
from backend import hubdb as H
from backend.auth import Identity
from backend.company_page import audiences as A, disclosure as D, editions as E, release, render, settings as S, stats as ST
from backend.company_page.api import preview_view
from backend.tests.test_company_page_core import numbers, post_income, setup, shown
from backend.tests.test_company_page_editions import plain_sections
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_company_page_editions import page  # noqa: F401
from backend.tests.test_finance_fixtures import OWNER, books, finance_template, store  # noqa: F401

VISION = "Every landlord runs on Acme."


def _settings(store):  # noqa: F811
    return store.settings


def _page_text(view):
    return json.dumps(view) + render.page(view) + render.feed(view) + render.signed_out(view)


def test_a_hidden_mission_is_on_no_page_no_feed_and_no_data(store):  # noqa: F811
    """The company profile holds the vision; the audience's mission rule decides whether it leaves. Hidden by default."""
    with store.transaction() as c:
        A.seed(c)
        company.update(c, OWNER, vision=VISION)
        investors = {"id": "investors", "kind": "investors", "name": "Investors"}
        view = release.view_model(c, investors, [], [], None, "private", _settings(store))
        assert view["company"]["mission"] == ""
        assert VISION not in _page_text(view)
        # The signed-out door has no audience, so no mission either.
        door = release.view_model(c, {"kind": "private", "name": ""}, [], [], None, "private", _settings(store))
        assert door["company"]["mission"] == "" and VISION not in _page_text(door)
        # Shown to this audience only: the other audience still gets none.
        D.set_rule(c, OWNER, "investors", "mission", mode="show")
        shown = release.view_model(c, investors, [], [], None, "private", _settings(store))
        assert shown["company"]["mission"] == VISION and VISION in _page_text(shown)
        board = {"id": "board", "kind": "board", "name": "Board"}
        assert release.view_model(c, board, [], [], None, "private", _settings(store))["company"]["mission"] == ""


def test_going_open_turns_public_on_in_the_same_step(store):  # noqa: F811
    """Open is a public page: the Public audience is on the moment the company goes Open, with nothing else to switch."""
    with store.transaction() as c:
        A.seed(c)
        S.set_transparency(c, OWNER, "private")
        assert c.execute("SELECT enabled FROM audiences WHERE kind='public'").fetchone()[0] == 0
        S.set_transparency(c, OWNER, "open")
        assert c.execute("SELECT enabled FROM audiences WHERE kind='public'").fetchone()[0] == 1
        # Going back doesn't have to switch it off; a page is served only while the company is Open.
        S.set_transparency(c, OWNER, "closed")
        assert release.public_view(c, datetime.now(timezone.utc)) == {}


def test_a_tightening_says_how_many_published_editions_it_leaves_up(page):  # noqa: F811
    """Published editions stay when a rule tightens: they're dated statements a person approved. The response says so
    instead of changing the page in silence. A loosening doesn't carry the count."""
    page.published()
    tighter = page.api.put("/api/v2/audiences/board/disclosure", headers=headers(),
                           json={"rules": [{"data_class": "kpi", "mode": "hidden"}]})
    assert tighter.status_code == 200 and tighter.json()["published_editions"] == 1
    looser = page.api.put("/api/v2/audiences/board/disclosure", headers=headers(),
                          json={"rules": [{"data_class": "kpi", "mode": "show"}]})
    assert looser.status_code == 200 and "published_editions" not in looser.json()
    assert page.rows("SELECT status FROM editions WHERE status='published'") != []


def test_a_held_label_is_not_in_the_draft_and_its_word_is_not_allowed_in_the_text(store):  # noqa: F811
    """A held stat's label goes nowhere near the model: the draft's facts leave it out, so the leak check doesn't allow
    the word either, and a draft that names it is flagged."""
    setup(store, shown("cash_runway"))
    with store.transaction() as c:
        numbers(c, ("Northwind", "cash_runway", "months"), ("Harbor", "cash_runway", "$"))
        facts = ST.facts(c, "investors", "2026-10-01", "2026-10-31")
    assert "held" not in facts and facts["stats"] == []
    prompt = E.draft_input(facts, [], "2026-10-01", "2026-10-31", {})
    assert "Northwind" not in prompt and "Harbor" not in prompt and "12,000" not in prompt
    found = E.leak_findings("Update", plain_sections("Northwind was 12,000 in October."), facts, "2026-10-01", "2026-10-31")
    assert found["numbers"] == ["12,000"] and found["names"] == ["Northwind"]


def test_a_public_board_yes_puts_the_public_page_up_when_it_is_recorded(page):  # noqa: F811
    """The Public page shows what the board approved, so the yes rebuilds it at once instead of at the next day's refresh.
    A Private board's yes leaves the Public page alone."""
    with page.store.transaction() as c:
        A.seed(c)
        S.set_transparency(c, OWNER, "open")
        D.set_rule(c, "human:ana", "public", "kpi", mode="show")
    assert get(page.api, "company-page")["public_live"] is False     # open, but nothing approved to show yet
    page.decide(post(page.api, "audiences/board/stats/approval", {})["approval"])
    assert "board approved" not in page.rebuilds
    page.decide(post(page.api, "audiences/public/stats/approval", {})["approval"])
    assert page.rebuilds.count("board approved") == 1
    served = release._served(page.store.settings)
    assert [s["label"] for s in served["stats"]] == ["Revenue"]
    assert get(page.api, "company-page")["public_live"] is True


def test_the_pnl_keeps_the_series_order_and_the_last_three_periods_across_a_year_end():
    """Period labels are short ("Oct"), so sorting them puts Aug after Jul and Oct in the middle. The table reads the
    series' own order, and the last three of it are the ones shown."""
    def line(label, periods):
        return {"label": label, "group": "pnl", "series": [{"period": p, "display": p.upper()} for p in periods]}
    months = ["Aug", "Sep", "Oct", "Nov"]
    html = render._pnl([line("Revenue", months), line("Spend", months[1:])])
    assert re.findall(r'<th scope="col">([^<]+)</th>', html) == ["Sep", "Oct", "Nov"]
    assert "AUG" not in html
    across = render._pnl([line("Revenue", ["Nov", "Dec", "Jan"])])
    assert re.findall(r'<th scope="col">([^<]+)</th>', across) == ["Nov", "Dec", "Jan"]


def test_a_public_update_names_the_company_as_the_board_does(page, monkeypatch):  # noqa: F811
    """The public page and the board read one source the same way: the company's own name, not a fallback."""
    from backend.company_page import editions as E
    seen = []
    real = E.stream

    def spy(c, audience_ids, **kw):
        seen.append(kw.get("company_name"))
        return real(c, audience_ids, **kw)
    monkeypatch.setattr(E, "stream", spy)
    with page.store.transaction() as c:
        release._whole_stream(c, "board", "Acme")
    assert seen and seen[0] == "Acme"


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
WEBP = b"RIFF\0\0\0\0WEBP" + b"\0" * 64


def test_the_preview_is_the_page_s_own_view_so_pnl_lines_and_the_logo_show_as_they_do_there(books, monkeypatch):  # noqa: F811
    """The owner's preview is built the way the page is: a P&L line sits in the table, and the logo is there (inline, as the
    preview can load nothing from a release folder)."""
    setup(books, shown("revenue"))
    with books.transaction() as c:
        line = post_income(c, "4100", 70_000_00)
        ST.create_stat(c, OWNER, "finance_line", line, label="Subscriptions")
        D.set_rule(c, OWNER, "investors", "pnl_line", line, mode="show")
        c.execute("INSERT INTO blobs (id, owner, digest, size, name, content_type, created) VALUES ('logo-p',?,?,?,?,?,?)",
                  (OWNER, "ab" * 32, len(PNG), S.LOGO_NAME + ".png", "image/png", H.now()))
        S.update(c, OWNER, logo_file_id="logo-p")
        monkeypatch.setattr(release, "_logo_bytes", lambda c_read, settings, view: PNG)
        view = preview_view(c, books.settings, A.get(c, "investors"), "2026-10-15")
    assert [s["group"] for s in view["stats"] if s["label"] == "Subscriptions"] == ["pnl"]
    assert view["company"]["logo_url"].startswith("data:image/png;base64,")
    assert 'src="data:image/png;base64,' in render.page(view, inline_logo=True)
    assert 'src="data:' not in render.page(view)     # a release page never carries an inline logo


def upload(api, data, token="ana-test", expected=200):
    r = api.post("/api/v2/company-page/logo", content=data, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def test_a_logo_is_a_picture_by_its_bytes_and_only_the_page_s_own_upload_can_be_the_logo(api):
    """The logo goes out on the public page. Its type comes from the bytes, an SVG is refused, and a picture from the
    library can't be made the logo by naming its id."""
    for data in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", b"RIFF\0\0\0\0WAVE" + b"\0" * 64,
                 b"not an image at all"):
        upload(api, data, expected=422)
    upload(api, b"\x89PNG\r\n\x1a\n" + b"0" * (release.MAX_LOGO_BYTES), expected=413)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO blobs (id, owner, digest, size, name, content_type, created) "
                  "VALUES ('tour','human:ana','d0',10,'tour.png','image/png',?)", (H.now(),))
    r = api.patch("/api/v2/company-page", headers=headers(), json={"logo_file_id": "tour"})
    assert r.status_code == 422 and "uploaded to the Company page" in r.text

    first = upload(api, PNG)
    assert first["logo_file_id"] and first["public_live"] is False
    shown = api.get("/api/v2/company-page/logo", headers=headers())
    assert shown.status_code == 200 and shown.content == PNG and shown.headers["content-type"] == "image/png"
    assert shown.headers["x-content-type-options"] == "nosniff"
    replaced = upload(api, JPEG)
    assert replaced["logo_file_id"] != first["logo_file_id"]
    assert api.get("/api/v2/company-page/logo", headers=headers()).headers["content-type"] == "image/jpeg"
    upload(api, WEBP)
    with api.app.state.store.read() as c:
        names = [r[0] for r in c.execute("SELECT name FROM blobs WHERE name LIKE 'company-page-logo%'")]
    assert len(names) == 3 and all(n.startswith(S.LOGO_NAME) for n in names)

    gone = api.delete("/api/v2/company-page/logo", headers=headers())
    assert gone.status_code == 200 and gone.json()["logo_file_id"] is None
    assert api.get("/api/v2/company-page/logo", headers=headers()).status_code == 404


def test_only_an_owner_or_admin_uploads_the_logo_and_a_bot_is_refused(api):
    """A bot never changes what leaves the install, and a member who is not an owner or admin doesn't either."""
    api.app.state.auth.settings.test_identities["ops-test"] = Identity("bot:ops", "bot", agent="test")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,runtime,created) VALUES('ops','ops','fake',?)", (H.now(),))
        c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES('ops','test','x-ops',?,'human:ana')",
                  (H.now(),))
    upload(api, PNG, token="ops-test", expected=403)
    upload(api, PNG, token="cara-test", expected=403)
    assert api.get("/api/v2/company-page/logo", headers=headers("cara-test")).status_code == 403
    assert api.get("/api/v2/company-page", headers=headers()).json()["logo_file_id"] is None
