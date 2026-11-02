"""The Company page's template and public releases: what reaches a reader, and that taking something back removes it."""
import hashlib
import json
import os
import re
import shutil
import stat as stat_module
import threading
import base64
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

import backend.company_page as package
from backend import hubdb as H
from backend.company_page import release, render
from backend.config import Settings
from backend.store import Store

AT = datetime(2026, 11, 1, tzinfo=timezone.utc)
PAYLOAD = """<script>alert(1)</script>"'><img src=x onerror=alert(1)>&lt;b&gt;"""


def view_model(**over):
    stat = {"id": "s1", "label": "Revenue", "definition": "Money in.", "chart": "bars", "period": "2026-10",
            "as_of": "2026-11-01", "display": "$118k", "stale": False,
            "series": [{"period": "2026-09", "value": 104, "display": "$104k"},
                       {"period": "2026-10", "value": 118, "display": "$118k"}]}
    update = {"anchor": "2026-10", "title": "October", "author": {"name": "Maya R.", "kind": "person"}, "period": "2026-10",
              "published_at": "2026-11-01T12:00:00Z", "version": 1, "correction_note": None,
              "sections": [{"key": "highlights", "items": [{"body": "Revenue grew.", "ask_id": None}]},
                           {"key": "asks", "items": [{"body": "Intros please.", "ask_id": "ask-1"}]}]}
    view = {"company": {"name": "Acme", "logo_url": "", "mission": "Quotes in seconds.", "badge": "public",
                        "accent": "#1E4FD8", "updated_at": "2026-11-01T12:00:00Z"},
            "audience": {"kind": "public", "name": "Public"}, "stats": [stat], "updates": [update], "next_before": None}
    view.update(over)
    return view


class Doc(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs, self.text, self.scripts, self.styles, self.stack = [], [], [], [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend((tag, k, v) for k, v in attrs)
        if tag in ("script", "style"):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack and self.stack[-1] == "script":
            self.scripts.append(data)
        elif self.stack and self.stack[-1] == "style":
            self.styles.append(data)
        else:
            self.text.append(data)

    def links(self):
        return [v for tag, k, v in self.attrs if tag == "a" and k == "href"]


def every_string_is(value, payload):
    """The view model with every string replaced, keeping only the values that pick a branch."""
    def walk(node, key=None):
        if isinstance(node, dict):
            return {k: walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str) and key not in ("kind", "badge", "chart", "key"):
            return payload
        return node
    return walk(value)


# ------------------------------------------------------------------------------------------------- render
@pytest.mark.parametrize("build", [render.page, render.signed_out])
@pytest.mark.parametrize("kind", ["public", "private"])
def test_every_string_field_is_escaped(build, kind):
    view = every_string_is(view_model(), PAYLOAD)
    view["actions"], view["viewer"], view["csrf"] = True, {"email": PAYLOAD}, PAYLOAD
    view["company"]["url"] = PAYLOAD
    view["audience"]["kind"] = kind
    view["updates"][0]["correction_note"] = PAYLOAD
    doc = Doc(build(view))
    assert doc.tags.count("script") == 1 and doc.scripts == [render._template("page.js")]
    assert "img" not in doc.tags
    assert not [a for _, a, _ in doc.attrs if a.startswith("on") or a in ("style", "srcdoc")]


def test_anchor_and_author_cannot_break_their_attributes():
    view = view_model()
    view["updates"][0].update(anchor='x" onclick="alert(1)', title="T")
    view["updates"][0]["author"] = {"name": PAYLOAD, "kind": "bot"}
    view["actions"] = True
    doc = Doc(render.page(view))
    assert not [a for _, a, _ in doc.attrs if a.startswith("on")]
    assert ("article", "id", "x-onclick-alert-1") in doc.attrs
    assert any(PAYLOAD in t for t in doc.text)      # shown, as words


@pytest.mark.parametrize("text", ["[x](javascript:alert(1))", "[x](JaVaScRiPt:alert(1))", "[x](data:text/html,hi)",
                                  "[x](http://acme.example/)", "[x](//acme.example/)", "[x](/local)",
                                  "[x](https://user:pw@acme.example/)", "[x](https://)", "<a href='https://acme.example'>x</a>",
                                  "![x](https://acme.example/a.png)", "<b>bold</b>", "# heading", "`code`"])
def test_only_https_links_and_the_subset_become_markup(text):
    rendered = render.markdown(text)
    doc = Doc(rendered)
    assert doc.tags == [] and "".join(doc.text) == text


def test_markdown_subset():
    doc = Doc(render.markdown("**b** and *i* [site](https://acme.example/a?x=1&y=2)\n\n- one\n- two\n\n1. first"))
    assert {"strong", "em", "a", "p", "ul", "ol", "li"} <= set(doc.tags)
    assert ("a", "href", "https://acme.example/a?x=1&y=2") in doc.attrs
    assert ("a", "rel", "noopener noreferrer nofollow") in doc.attrs


@pytest.mark.parametrize("accent", ["red", "#12345g", "#1E4FD8;}body{display:none}", "#1E4FD8\n", "", None, 5, "url(x)", "#12345"])
def test_a_bad_accent_takes_the_default(accent):
    view = view_model()
    view["company"]["accent"] = accent
    theme = Doc(render.page(view)).styles[0]
    assert theme.startswith(f":root{{--accent:{render.DEFAULT_ACCENT};")


@pytest.mark.parametrize("logo", ["https://evil.example/x.png", "//evil.example/x.png", "/x.png", "../x.png", "a/../../x.png",
                                  "javascript:alert(1)", "assets/x.svg", "x.png?y", 'x.png"', "data:image/png;base64,AA", None])
def test_a_logo_off_the_release_is_dropped(logo):
    view = view_model()
    view["company"]["logo_url"] = logo
    assert "img" not in Doc(render.page(view)).tags


def test_a_logo_in_the_release_shows():
    view = view_model()
    view["company"]["logo_url"] = "assets/logo-ab12.png"
    assert ("img", "src", "assets/logo-ab12.png") in Doc(render.page(view)).attrs


TEMPLATE_WORDS = set("""stats updates highlights lowlights numbers asks next link show older rss runs on tico updated as of out date
vs in open company public private corrected reply to i can help send sign out p l jan feb mar apr may jun jul aug sep oct nov dec
bot no yet""".split())


def words(*texts):
    return {w.lower() for text in texts for w in re.findall(r"[^\W_]+", text or "")}


def strings_in(node):
    if isinstance(node, dict):
        return [s for v in node.values() for s in strings_in(v)]
    if isinstance(node, list):
        return [s for v in node for s in strings_in(v)]
    return [str(node)] if isinstance(node, (str, int, float)) and not isinstance(node, bool) else []


def test_render_shows_nothing_outside_the_view_model():
    view = view_model()
    view["stats"][0]["cash"] = "SECRETCASH"
    view["stats"][0]["series"][0]["accounts"] = "SECRETACCOUNT"
    view["updates"][0]["source_body"] = "SECRETSOURCE"
    view["updates"][0]["sections"].append({"key": "internal", "items": [{"body": "SECRETSECTION", "ask_id": None}]})
    view["company"]["monthly_burn"] = "SECRETBURN"
    view["secret"] = "SECRETTOP"
    output = render.page(view) + render.feed(view) + render.signed_out(view)
    assert "SECRET" not in output

    clean = view_model()
    doc = Doc(render.page(clean))
    allowed = words(*strings_in(clean)) | TEMPLATE_WORDS | {"a", "mr"}      # the logo letter and the author's initials
    allowed |= {str(int(w)) for w in allowed if w.isdigit()}
    assert words(*doc.text) <= allowed
    labels = [v for _, k, v in doc.attrs if k in ("aria-label", "title", "alt", "content", "value", "placeholder")
              and not (v or "").startswith("default-src") and v not in ("width=device-width,initial-scale=1", "strict-origin-when-cross-origin")]
    assert words(*labels) <= allowed


def test_reader_actions_need_exactly_true():
    for value in (None, False, "true", 1):
        view = view_model(actions=value)
        assert "form" not in Doc(render.page(view)).tags
    doc = Doc(render.page(view_model(actions=True, csrf="tok")))
    actions = [v for tag, k, v in doc.attrs if tag == "form" and k == "action"]
    assert sorted(actions) == sorted([render.REPLY_PATH, render.HELP_PATH])
    assert ("input", "value", "ask-1") in doc.attrs and ("input", "value", "tok") in doc.attrs


def test_only_public_output_can_be_indexed():
    robots = lambda text: ("meta", "name", "robots") in Doc(text).attrs
    assert not robots(render.page(view_model()))
    private = view_model(audience={"kind": "investors", "name": "Investors"})
    private["company"]["badge"] = "private"
    assert robots(render.page(private))
    assert robots(render.signed_out(view_model()))
    unknown = view_model()
    unknown["company"]["badge"] = "something"
    assert robots(render.page(unknown)) and "feed.xml" not in render.page(unknown)
    assert "feed.xml" not in render.page(private)


def test_signed_out_carries_no_content():
    text = render.signed_out(view_model())
    for hidden in ("October", "Revenue", "$118k", "Intros"):
        assert hidden not in text
    assert render.SIGN_IN_PATH in text


def test_policy_covers_exactly_what_the_page_inlines():
    """Under this policy a style attribute or an unhashed block silently breaks the page, with no failing request."""
    text = render.page(view_model(actions=True))
    doc = Doc(text)
    policy = next(v for tag, k, v in doc.attrs if tag == "meta" and k == "content" and v.startswith("default-src"))
    digest = lambda s: base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()
    for block in doc.styles + doc.scripts:
        assert digest(block) in policy
    assert "'unsafe-inline'" not in policy and "default-src 'none'" in policy and "base-uri 'none'" in policy
    assert not [a for _, a, _ in doc.attrs if a == "style"]
    assert not re.search(r"https?://", doc.styles[1])       # nothing loads from the network


def test_older_updates_work_without_a_script():
    def update(n):
        return {**view_model()["updates"][0], "anchor": f"u{n}", "title": f"Title {n}"}
    paged = render.page(view_model(updates=[update(1), update(2)], next_before="2026-10-01T00:00:00Z&x=<1>"))
    doc = Doc(paged)
    assert "?before=2026-10-01T00%3A00%3A00Z%26x%3D%3C1%3E" in doc.links()
    whole = render.page(view_model(updates=[update(n) for n in range(6)]))
    assert whole.count("<details open>") == 1 and whole.index("<details open>") < whole.index('id="u1"')
    assert 'class="more"' in whole and all(f'id="u{n}"' in whole for n in range(6))
    assert "?before=" not in whole


@pytest.mark.parametrize("values", [[0, 0], [5, 5], [-3, 4, None, 9], [-2, -1], [1, None], [None, None], [1e308, -1e308, float("nan"), float("inf")]])
@pytest.mark.parametrize("chart", ["number", "line", "bars"])
def test_charts_take_any_series_and_keep_the_text(chart, values):
    view = view_model()
    view["stats"][0].update(chart=chart, series=[{"period": f"2026-{i + 1:02d}", "value": v, "display": f"D{i}"} for i, v in enumerate(values)])
    text = render.page(view)
    for tag, name, value in Doc(text).attrs:
        if name in ("points", "height", "cx", "cy") and value != "100%":
            assert re.fullmatch(r"[-\d., e+]*", value), (tag, name, value)      # never nan or inf in a coordinate
    assert view["stats"][0]["display"] in text


def test_feed_is_valid_atom_with_anchored_links_and_no_markup_from_outside():
    view = every_string_is(view_model(), PAYLOAD + "\x01")
    view["updates"][0]["anchor"] = "2026-10"
    view["company"]["url"] = "https://acme.example"
    root = ET.fromstring(render.feed(view))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    assert entry.find("a:link", ns).get("href") == "https://acme.example/#2026-10"
    assert entry.find("a:updated", ns).text == "1970-01-01T00:00:00Z"
    assert "<script>" not in entry.find("a:content", ns).text
    assert root.find("a:id", ns).text.startswith("urn:uuid:")
    relative = ET.fromstring(render.feed(view_model()))
    assert relative.find("a:entry", ns).find("a:link", ns).get("href") == "./#2026-10"


# ------------------------------------------------------------------------------------------------- releases
@pytest.fixture
def world(tmp_path, monkeypatch):
    """A real database and a real release folder. Tracks A and B own the projection and the stream: these stand in for
    them and read the same tables, so a change is seen the way the real ones see it."""
    settings = Settings(db_path=tmp_path / "hub.db", company_name="Acme")
    store = Store(settings)
    store.initialize()
    state = SimpleNamespace(store=store, settings=settings, approved=True, stats=[], held=[], root=tmp_path / "company-page-public")

    def stream(c, audience_ids, before=None, limit=10, company_name=""):
        rows = c.execute("SELECT * FROM editions WHERE audience_id=? AND status='published' AND (? IS NULL OR published_at<?) "
                         "ORDER BY published_at DESC LIMIT ?", (audience_ids[0], before, before, limit)).fetchall()
        return [{"anchor": r["id"], "title": r["title"], "author": {"name": r["author"], "kind": "bot"}, "period": r["period_start"],
                 "published_at": r["published_at"], "version": r["version"], "correction_note": r["correction_note"],
                 "edition_source": "SECRETSOURCE", "sections": json.loads(r["body_json"])} for r in rows]

    monkeypatch.setattr(package, "editions", SimpleNamespace(stream=stream), raising=False)
    monkeypatch.setattr(package, "stats", SimpleNamespace(
        board_approved=lambda c, audience: state.approved,
        project=lambda c, audience, at: {"stats": list(state.stats), "held": list(state.held)}), raising=False)
    return state


def open_page(state, transparency="open", enabled=1):
    with state.store.transaction() as c:
        c.execute("INSERT INTO company_page_settings (id, transparency, accent) VALUES ('company',?,'#0F7A5A') "
                  "ON CONFLICT(id) DO UPDATE SET transparency=excluded.transparency", (transparency,))
        c.execute("INSERT OR IGNORE INTO audiences (id, name, kind, enabled, created_at, updated_at) "
                  "VALUES ('pub','Public','public',?,?,?)", (enabled, H.now(), H.now()))
        c.execute("UPDATE audiences SET enabled=?", (enabled,))


def publish(state, edition_id, title, published_at="2026-10-30T00:00:00Z"):
    body = json.dumps([{"key": "highlights", "items": [{"body": f"Body of {title}", "ask_id": None}]}])
    with state.store.transaction() as c:
        c.execute("INSERT INTO editions (id, audience_id, author, period_start, period_end, status, title, body_json, published_at, "
                  "created_at, updated_at) VALUES (?,'pub','Head of Support',?,?,'published',?,?,?,?,?)",
                  (edition_id, edition_id, edition_id, title, body, published_at, H.now(), H.now()))


def stat(display="$118k", **extra):
    return {"id": "s1", "label": "Revenue", "definition": "Money in.", "chart": "number", "period": "2026-10", "as_of": "2026-11-01",
            "display": display, "stale": False, "series": [{"period": "2026-10", "value": 118, "display": display}], **extra}


def test_a_stat_says_as_of_only_when_the_header_does_not_already_say_it():
    def shows(**extra):
        return 'class="as-of"' in render.page(view_model(stats=[stat(**extra)]))
    assert not shows(period="2026-10", as_of="2026-10-31")
    assert not shows(period="Oct 2026", as_of="2026-10-31")
    assert not shows(period="Nov 1, 2026", as_of="2026-11-01")
    assert not shows(period="Q3 2026", as_of="2026-09-30")
    assert not shows(chart="line", period="Nov 1, 2026", as_of="2026-11-01")
    # Something new: a different day, or a stat that has gone out of date.
    assert shows(period="Oct 2026", as_of="2026-11-01")
    assert shows(period="Nov 1, 2026", as_of="2026-10-31")
    assert shows(period="Oct 2026", as_of="2026-10-31", stale=True)
    assert shows(chart="line", period="Nov 1, 2026", as_of="2026-11-01", stale=True)


def test_a_hero_line_runs_edge_to_edge_and_ends_on_the_last_value():
    series = [{"period": p, "value": v, "display": str(v)} for p, v in (("Sep", 1), ("Oct", 2), ("Nov", 3))]
    doc = Doc(render.page(view_model(stats=[stat(chart="line", series=series)])))
    drawn = [v.split() for t, k, v in doc.attrs if t == "polyline" and k == "points"]
    assert drawn[0][0].split(",")[0] == "0" and drawn[0][-1].split(",")[0] == "600"
    assert drawn[1] == [drawn[0][-1], drawn[0][-1]]


def served(state):
    return release.current_dir(state.settings)


def on_disk(state):
    """Every byte under the folder, served or not: taken-back content mustn't sit anywhere in it."""
    return b"".join(p.read_bytes() for p in state.root.rglob("*") if p.is_file() and p.suffix != ".woff2")


def test_nothing_is_public_unless_open_and_enabled(world):
    with world.store.read() as c:
        assert release.public_view(c, AT, world.settings) == {}     # no settings row, no audience
    open_page(world, "closed")
    publish(world, "e1", "First")
    with world.store.read() as c:
        assert release.public_view(c, AT, world.settings) == {}
    open_page(world, "private")
    with world.store.read() as c:
        assert release.public_view(c, AT, world.settings) == {}
    open_page(world, "open", enabled=0)
    with world.store.read() as c:
        assert release.public_view(c, AT, world.settings) == {}
    open_page(world, "open", enabled=1)
    with world.store.read() as c:
        assert release.public_view(c, AT, world.settings)["updates"][0]["title"] == "First"


def test_an_unapproved_board_shows_no_stats_but_updates_stay(world):
    open_page(world)
    publish(world, "e1", "First")
    world.stats = [stat("$777k")]
    world.approved = False
    result = release.rebuild(world.store, "test", at=AT)
    assert not result["empty"]
    assert "$777k" not in (served(world) / "index.html").read_text() and "First" in (served(world) / "index.html").read_text()
    assert json.loads((served(world) / "data.json").read_text())["stats"] == []
    world.approved = "yes"      # only a real True counts
    with world.store.read() as c:
        assert release.public_view(c, AT, world.settings)["stats"] == []
    world.approved = True
    with world.store.read() as c:
        assert release.public_view(c, AT, world.settings)["stats"][0]["display"] == "$777k"


def test_with_nothing_approved_there_is_no_page(world):
    open_page(world)
    world.approved = False
    assert release.rebuild(world.store, "test", at=AT)["empty"]
    assert list(served(world).iterdir()) == []


def test_data_json_is_exactly_the_view_and_extras_never_leave(world):
    open_page(world)
    publish(world, "e1", "First")
    world.stats = [stat(cash=123456, monthly_burn=99, accounts=[{"bank": "SECRETBANK"}])]
    world.held = [{"id": "s9", "label": "SECRETHELD", "reason": "derivation"}]
    release.rebuild(world.store, "test", at=AT)
    data = json.loads((served(world) / "data.json").read_text())
    with world.store.read() as c:
        assert data == release.public_view(c, AT, world.settings)
    assert set(data) == {"company", "audience", "stats", "updates", "next_before"}
    assert set(data["stats"][0]) == {"id", "label", "definition", "chart", "period", "as_of", "display", "stale", "series"}
    assert set(data["updates"][0]) == {"anchor", "title", "author", "period", "published_at", "version", "correction_note", "sections"}
    assert data["next_before"] is None
    blob = on_disk(world)
    for secret in (b"SECRET", b"123456", b"monthly_burn", b"accounts"):
        assert secret not in blob


def test_public_history_walks_every_page_of_the_stream(world, monkeypatch):
    open_page(world)
    monkeypatch.setattr(release, "STREAM_PAGE", 2)
    for n in range(5):
        publish(world, f"e{n}", f"Title {n}", f"2026-10-{n + 10}T00:00:00Z")
    with world.store.read() as c:
        view = release.public_view(c, AT, world.settings)
    assert [u["title"] for u in view["updates"]] == [f"Title {n}" for n in (4, 3, 2, 1, 0)] and view["next_before"] is None


def test_finance_stats_earn_the_open_badge_and_lines_go_in_the_table(world):
    open_page(world)
    with world.store.transaction() as c:
        c.execute("INSERT INTO page_stats (id, source_kind, ref_id, data_class, created_at) VALUES ('s1','kpi','k','customers',?)", (H.now(),))
    world.stats = [stat()]
    with world.store.read() as c:
        assert release.public_view(c, AT, world.settings)["company"]["badge"] == "public"
    with world.store.transaction() as c:
        c.execute("UPDATE page_stats SET source_kind='finance_line'")
    with world.store.read() as c:
        view = release.public_view(c, AT, world.settings)
    assert view["company"]["badge"] == "open" and view["stats"][0]["group"] == "pnl"
    assert "<table>" in render.page(view)


def test_files_are_readable_by_another_container_whatever_the_umask(world):
    open_page(world)
    publish(world, "e1", "First")
    old = os.umask(0o077)
    try:
        result = release.rebuild(world.store, "test", at=AT)
    finally:
        os.umask(old)
    link = world.root / "current"
    assert os.readlink(link) == os.path.join("releases", result["id"])      # relative: the router mounts it elsewhere
    mode = lambda p: stat_module.S_IMODE(os.stat(p).st_mode)
    assert mode(world.root) == mode(world.root / "releases") == mode(link) == 0o755
    for path in link.resolve().rglob("*"):
        assert mode(path) == (0o755 if path.is_dir() else 0o644), path
    assert {"index.html", "data.json", "feed.xml", "assets"} <= {p.name for p in link.iterdir()}
    assert list((link / "assets" / "fonts").glob("*.woff2")) and list((link / "assets" / "fonts").glob("LICENSE-*"))


@pytest.mark.parametrize("through_connection", [False, True])
def test_closed_removes_every_path_and_the_old_folder(world, through_connection):
    open_page(world)
    publish(world, "e1", "Firsttitle")
    world.stats = [stat("$777k")]
    release.rebuild(world.store, "open", at=AT)
    old = (world.root / "current").resolve()
    assert (old / "index.html").exists() and b"Firsttitle" in on_disk(world)
    if through_connection:
        with world.store.transaction() as c:
            c.execute("UPDATE company_page_settings SET transparency='closed'")
            assert release.rebuild(c, "closed", at=AT)["empty"]
            assert not (world.root / "current" / "index.html").exists()     # in the same step, before commit
    else:
        open_page(world, "closed")
        assert release.rebuild(world.store, "closed", at=AT)["empty"]
    current = served(world)
    for name in ("index.html", "data.json", "feed.xml", "assets"):
        assert not (current / name).exists()
    assert current.is_dir() and not old.exists()
    assert [p.name for p in (world.root / "releases").iterdir()] == [current.resolve().name]
    assert b"Firsttitle" not in on_disk(world) and b"$777k" not in on_disk(world)
    with world.store.read() as c:
        reasons = [r["reason"] for r in c.execute("SELECT reason FROM page_releases ORDER BY created_at")]
    assert reasons[-1] == "closed"


def test_retract_leaves_no_trace_in_any_served_file(world):
    open_page(world)
    publish(world, "e1", "Keeper", "2026-10-01T00:00:00Z")
    publish(world, "e2", "Mistaketitle", "2026-10-02T00:00:00Z")
    release.rebuild(world.store, "publish", at=AT)
    old = (world.root / "current").resolve()
    for name in ("index.html", "data.json", "feed.xml"):
        assert "Mistaketitle" in (old / name).read_text()
    with world.store.transaction() as c:
        c.execute("UPDATE editions SET status='retracted' WHERE id='e2'")
        release.rebuild(c, "retract", at=AT)
    for name in ("index.html", "data.json", "feed.xml"):
        text = (served(world) / name).read_text()
        assert "Mistaketitle" not in text and "Keeper" in text
    assert not old.exists() and b"Mistaketitle" not in on_disk(world)


def test_a_take_back_rebuilds_the_folder_the_server_serves_without_the_environment(world, monkeypatch, tmp_path):
    """The folder comes from the server's settings. With the environment not saying so, a take-back must still rebuild
    the folder being served: a take-back that wrote somewhere else would answer 200 and leave the title up."""
    monkeypatch.delenv("TICO_COMPANY_PAGE_DIR", raising=False)
    world.settings.company_page_dir = str(tmp_path / "served")
    world.root = tmp_path / "served"
    open_page(world)
    publish(world, "e1", "Keeper", "2026-10-01T00:00:00Z")
    publish(world, "e2", "Mistaketitle", "2026-10-02T00:00:00Z")
    release.rebuild(world.store, "publish", at=AT)
    with world.store.transaction() as c:
        c.execute("UPDATE editions SET status='retracted' WHERE id='e2'")
        release.rebuild(c, "retract", at=AT)
    for name in ("index.html", "data.json", "feed.xml"):
        text = (served(world) / name).read_text()
        assert "Mistaketitle" not in text and "Keeper" in text


def test_current_is_valid_at_every_step_of_a_swap(world, monkeypatch):
    open_page(world)
    publish(world, "e1", "First")
    release.rebuild(world.store, "first", at=AT)
    seen = []

    def check(moment):
        current = world.root / "current"
        assert current.is_symlink() and current.is_dir(), moment
        assert {"index.html", "data.json", "feed.xml"} <= {p.name for p in current.iterdir()}, moment
        json.loads((current / "data.json").read_text())
        seen.append(moment)

    real = {"replace": os.replace, "rename": os.rename, "symlink": os.symlink, "rmtree": shutil.rmtree}
    for name in ("replace", "rename", "symlink"):
        def wrapped(*args, name=name, **kwargs):
            check(f"before {name}")
            out = real[name](*args, **kwargs)
            check(f"after {name}")
            return out
        monkeypatch.setattr(os, name, wrapped)
    def rmtree(path, *args, **kwargs):
        check("before rmtree")
        real["rmtree"](path, *args, **kwargs)
        check("after rmtree")
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    publish(world, "e2", "Second")
    release.rebuild(world.store, "second", at=AT)
    assert "after replace" in seen and "after rmtree" in seen
    assert "Second" in (world.root / "current" / "index.html").read_text()


def test_a_failed_swap_tells_the_caller_and_leaves_the_old_release_whole(world, monkeypatch):
    open_page(world)
    publish(world, "e1", "First")
    release.rebuild(world.store, "first", at=AT)
    old = (world.root / "current").resolve()
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        release.rebuild(world.store, "second", at=AT)
    assert (world.root / "current").resolve() == old and "First" in (old / "index.html").read_text()
    with world.store.read() as c:
        assert [r["reason"] for r in c.execute("SELECT reason FROM page_releases")] == ["first"]    # no record of a swap that failed


def test_a_read_that_loses_to_a_close_never_reopens_the_page(world, monkeypatch):
    """Rendering holds no lock, so someone can close the page while a rebuild has an open view in hand."""
    open_page(world)
    publish(world, "e1", "Stalecontent")
    prepare, closed = release._prepare, []

    def close_during_render(root, view, logo=None):
        out = prepare(root, view, logo)
        if view and not closed:
            closed.append(True)
            with world.store.transaction() as c:
                c.execute("UPDATE company_page_settings SET transparency='closed'")
                release.rebuild(c, "closed", at=AT)
        return out

    monkeypatch.setattr(release, "_prepare", close_during_render)
    result = release.rebuild(world.store, "daily stats", at=AT)
    assert closed and result["empty"]
    assert not (served(world) / "index.html").exists() and b"Stalecontent" not in on_disk(world)
    assert not list((world.root / "tmp").iterdir())


def test_a_failure_while_rendering_closes_the_page(world, monkeypatch):
    open_page(world)
    publish(world, "e1", "Secretish")
    release.rebuild(world.store, "first", at=AT)
    monkeypatch.setattr(render, "page", lambda view: 1 / 0)
    result = release.rebuild(world.store, "second", at=AT)
    assert result == {"id": result["id"], "empty": True, "failed": True}
    assert not (served(world) / "index.html").exists() and b"Secretish" not in on_disk(world)
    with world.store.transaction() as c:
        assert release.rebuild(c, "third", at=AT)["failed"]     # the same on a connection
    assert not (served(world) / "data.json").exists()


def test_a_connection_outside_a_transaction_commits_its_own_record(world):
    open_page(world)
    publish(world, "e1", "First")
    with world.store.read() as c:
        assert not c.in_transaction
        release.rebuild(c, "direct", at=AT)
        assert not c.in_transaction
    with world.store.read() as c:
        assert c.execute("SELECT count(*) FROM page_releases WHERE reason='direct'").fetchone()[0] == 1


def test_a_connection_never_writes_to_the_install_the_environment_names(world, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("TICO_DB", str(other / "hub.db"))
    open_page(world)
    with world.store.transaction() as c:
        release.rebuild(c, "here", at=AT)
    assert not list(other.iterdir())
    assert served(world) is not None


def test_concurrent_rebuilds_do_not_interleave(world):
    open_page(world)
    publish(world, "seed", "Seed", "2026-01-01T00:00:00Z")
    stop, failures, partial = threading.Event(), [], []

    def rebuilder(n):
        try:
            for i in range(6):
                publish(world, f"e{n}-{i}", f"Title {n} {i}", f"2026-10-{(n % 9) + 10}T00:00:{i:02d}Z")
                release.rebuild(world.store, f"r{n}", at=AT)
        except Exception as exc:    # noqa: BLE001  anything at all fails the test below
            failures.append(exc)

    def reader():
        link = world.root / "current"
        while not stop.is_set():
            if not link.is_symlink():
                continue
            target = link.resolve()
            try:
                names = {p.name for p in target.iterdir()}
            except OSError:
                continue
            if not {"index.html", "data.json", "feed.xml"} <= names and link.resolve() == target:
                partial.append(names)

    watching = threading.Thread(target=reader)
    watching.start()
    workers = [threading.Thread(target=rebuilder, args=(n,)) for n in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    stop.set()
    watching.join()
    assert not failures and not partial
    assert len(list((world.root / "releases").iterdir())) == 1 and not list((world.root / "tmp").iterdir())
    # The last swap holds everything committed, whichever thread made it.
    data = json.loads((served(world) / "data.json").read_text())
    assert len(data["updates"]) == 25


def test_refresh_only_writes_when_the_page_would_change(world):
    assert release.refresh(world.store, AT) is None and served(world) is None      # never public: no folder at all
    open_page(world)
    world.stats = [stat("$118k")]
    publish(world, "e1", "First")
    first = release.refresh(world.store, AT)
    assert first and not first["empty"]
    assert release.refresh(world.store, AT) is None
    assert (world.root / "current").resolve().name == first["id"]
    world.stats = [stat("$120k")]
    second = release.refresh(world.store, AT)
    assert second and "$120k" in (served(world) / "index.html").read_text()


def test_a_failed_daily_read_leaves_the_page_up(world, monkeypatch):
    open_page(world)
    publish(world, "e1", "First")
    release.rebuild(world.store, "first", at=AT)
    monkeypatch.setattr(release, "public_view", lambda *a, **k: 1 / 0)
    assert release.refresh(world.store, AT) is None
    assert (served(world) / "index.html").exists()


def test_a_logo_is_read_through_the_servers_file_reader_with_the_company_places_it_is_set_to(world, monkeypatch):
    """The server's file reader is pointed at the company's own bucket and the places before it (backend/company_storage.py),
    so a logo saved there is found only through that reader, not through a fresh one that knows the server's storage."""
    import backend.blobs as blobs
    open_page(world)
    publish(world, "e1", "First")
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    digest = hashlib.sha256(png).hexdigest()
    with world.store.transaction() as c:
        c.execute("INSERT INTO blobs (id, owner, digest, size, name, content_type, created) VALUES (?,?,?,?,?,?,?)",
                  ("good", "human:o", digest, len(png), "logo", "image/png", H.now()))
        c.execute("UPDATE company_page_settings SET logo_file_id='good'")
    configured = blobs.Blobs(world.settings)
    world.store.blobs = configured
    monkeypatch.setattr(blobs.Blobs, "get", lambda self, d: png if self is configured else None)
    release.rebuild(world.store, "logo", at=AT)
    assert json.loads((served(world) / "data.json").read_text())["company"]["logo_url"].startswith("assets/logo-")


def test_default_and_named_folders(world, tmp_path):
    assert release._root(world.settings) == tmp_path / "company-page-public"
    named = Settings(db_path=tmp_path / "hub.db", company_page_dir=str(tmp_path / "named"))
    assert release._root(named) == tmp_path / "named"
    assert release.current_dir(named) is None


def test_logo_is_copied_when_its_bytes_are_an_image_and_dropped_otherwise(world, monkeypatch):
    open_page(world)
    publish(world, "e1", "First")
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    digest = hashlib.sha256(png).hexdigest()
    with world.store.transaction() as c:
        for blob, content_type in (("good", "image/png"), ("svg", "image/svg+xml")):
            c.execute("INSERT INTO blobs (id, owner, digest, size, name, content_type, created) VALUES (?,?,?,?,?,?,?)",
                      (blob, "human:o", digest, len(png), "logo", content_type, H.now()))
        c.execute("UPDATE company_page_settings SET logo_file_id='good'")
    import backend.blobs as blobs
    monkeypatch.setattr(blobs.Blobs, "get", lambda self, d: png)
    release.rebuild(world.store, "logo", at=AT)
    data = json.loads((served(world) / "data.json").read_text())
    assert re.fullmatch(r"assets/logo-[0-9a-f]{32}\.png", data["company"]["logo_url"])
    assert (served(world) / data["company"]["logo_url"]).read_bytes() == png
    assert data["company"]["logo_url"] in (served(world) / "index.html").read_text()
    assert release.refresh(world.store, AT) is None

    monkeypatch.setattr(blobs.Blobs, "get", lambda self, d: b"<svg onload=alert(1)>")   # a type that lies
    release.rebuild(world.store, "logo", at=AT)
    assert json.loads((served(world) / "data.json").read_text())["company"]["logo_url"] == ""
    assert not list(served(world).glob("assets/logo-*"))

    with world.store.transaction() as c:
        c.execute("UPDATE company_page_settings SET logo_file_id='svg'")
    with world.store.read() as c:
        assert release.public_view(c, AT, world.settings)["company"]["logo_url"] == ""


class References(HTMLParser):
    """Everything a page asks the browser to fetch or link to on its own origin."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.found, self.in_style = set(), False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.in_style = tag == "style"
        attrs = dict(attrs)
        assert not (tag == "script" and "src" in attrs) and not (tag == "link" and attrs.get("rel") == "stylesheet")
        for name in ("src", "href", "poster", "data"):
            value = attrs.get(name)
            if value and tag != "article" and not (tag == "link" and attrs.get("rel") == "canonical") and not value.startswith(("#", "?", "https://")):
                self.found.add(value)

    def handle_data(self, data):
        if self.in_style:
            self.found |= set(re.findall(r"url\(([^)]+)\)", data))


def with_logo(world, monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    with world.store.transaction() as c:
        c.execute("INSERT INTO blobs (id, owner, digest, size, name, content_type, created) VALUES ('good','human:o',?,?,'logo','image/png',?)",
                  (hashlib.sha256(png).hexdigest(), len(png), H.now()))
        c.execute("UPDATE company_page_settings SET logo_file_id='good'")
    import backend.blobs as blobs
    monkeypatch.setattr(blobs.Blobs, "get", lambda self, d: png)


def assert_self_contained(folder):
    wanted = References((folder / "index.html").read_text()).found
    assert any("fonts/" in w for w in wanted) and any("logo-" in w for w in wanted) and "feed.xml" in wanted
    for name in wanted:
        target = (folder / name).resolve()
        assert target.is_file() and target.is_relative_to(folder.resolve()), name


def test_a_release_holds_everything_its_page_refers_to(world, monkeypatch):
    open_page(world)
    publish(world, "e1", "First")
    with_logo(world, monkeypatch)
    release.rebuild(world.store, "first", at=AT)
    assert_self_contained(served(world))


@pytest.mark.parametrize("way_out", ["closed", "retract"])
def test_a_release_with_nothing_public_holds_no_assets(world, monkeypatch, way_out):
    open_page(world)
    publish(world, "e1", "First")
    with_logo(world, monkeypatch)
    release.rebuild(world.store, "first", at=AT)
    old = (world.root / "current").resolve()
    wanted = References((old / "index.html").read_text()).found
    with world.store.transaction() as c:
        c.execute("UPDATE company_page_settings SET transparency='closed'" if way_out == "closed"
                  else "UPDATE editions SET status='retracted'")
        release.rebuild(c, way_out, at=AT)
    assert not old.exists() and list(served(world).iterdir()) == []
    for name in wanted:
        assert not (world.root / "current" / name).exists(), name
    assert not list(world.root.rglob("*.woff2")) and not list(world.root.rglob("logo-*"))


def test_a_retract_that_leaves_a_page_keeps_it_whole_and_drops_the_old_files(world, monkeypatch):
    open_page(world)
    publish(world, "e1", "Keeper", "2026-10-01T00:00:00Z")
    publish(world, "e2", "Gone", "2026-10-02T00:00:00Z")
    with_logo(world, monkeypatch)
    release.rebuild(world.store, "first", at=AT)
    old = (world.root / "current").resolve()
    with world.store.transaction() as c:
        c.execute("UPDATE editions SET status='retracted' WHERE id='e2'")
        release.rebuild(c, "retract", at=AT)
    assert not old.exists()
    assert_self_contained(served(world))


def test_the_router_needs_only_the_folder_it_mounts(world, monkeypatch, tmp_path):
    """On a cell the router mounts one folder read-only, at another path, and sees nothing above it."""
    world.settings.company_page_dir = str(tmp_path / "cell" / "pages" / "acme")
    world.root = tmp_path / "cell" / "pages" / "acme"
    open_page(world)
    publish(world, "e1", "First")
    with_logo(world, monkeypatch)
    old = os.umask(0o077)
    try:
        release.rebuild(world.store, "first", at=AT)
    finally:
        os.umask(old)
    assert not (tmp_path / "company-page-public").exists()
    assert stat_module.S_IMODE(os.stat(tmp_path / "cell").st_mode) == 0o755     # folders it had to make on the way
    for path in world.root.rglob("*"):
        if path.is_symlink():
            assert path.name == "current" and not os.path.isabs(os.readlink(path))
        elif path.is_file() and path.suffix in (".html", ".json", ".xml"):
            assert str(tmp_path) not in path.read_text()
    mounted = tmp_path / "mount" / "elsewhere"
    mounted.parent.mkdir()
    shutil.move(str(tmp_path / "cell" / "pages"), str(mounted))
    assert_self_contained(mounted / "acme" / "current")
