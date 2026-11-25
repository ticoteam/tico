"""Tags, metadata and "made from" on files (backend/file_details.py): writers change them, readers read them, and a
file a viewer may not read never shows through a tag filter, a search or a parent link."""

from backend.tests.helpers.api import headers, post
from backend.tests.helpers.automation_cli import Client
from backend.tests.helpers.company import bot
from clients import cli_spec, hubcli, hubtools


def page(api, token, name, task, **fields):
    r = api.post(
        "/api/v2/pages",
        json={"name": name, "text": "<p>" + name + " orbitalnote</p>", "task": task, **fields},
        headers=headers(token),
    )
    assert r.status_code == 200, r.text
    return r.json()["page"]["file_id"]


def tasks(api):
    secret = post(api, "tasks", {"owner": "ops", "title": "Secret", "body": "Work", "private": True}, "ben-test")
    public = post(api, "tasks", {"owner": "ops", "title": "Public", "body": "Work"})
    return secret["id"], public["id"]


def tag(api, fid, token, **change):
    return api.post(f"/api/v2/files/{fid}/tags", json=change, headers=headers(token))


def info(api, fid, token):
    r = api.get(f"/api/v2/files/{fid}/info", headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["file"]


def test_tags_attach_with_write_rights_and_filter_only_readable_files(api):
    ops = bot(api, "ops")
    secret, public = tasks(api)
    hidden, shown = page(api, ops, "hidden.html", secret), page(api, ops, "shown.html", public)
    post(api, "tags", {"key": "client:acme", "label": "Acme"})
    post(api, "tags", {"key": "launch-kit", "is_template": True})

    outsider = bot(api, "cpo")  # reads the public page, is no party to its task, so may not write it
    assert tag(api, shown, outsider, add=["client:acme"]).status_code == 403
    assert tag(api, shown, ops, add=["client:nobody"]).status_code == 404  # only existing tags
    assert tag(api, shown, ops, add=["launch-kit"]).status_code == 422  # a template is not a tag on anything
    for fid in (hidden, shown):
        r = tag(api, fid, ops, add=["Client:Acme"])
        assert r.status_code == 200, r.text
        assert [t["key"] for t in r.json()["tags"]] == ["client:acme"]

    for token, expected in (("cara-test", [shown]), ("ben-test", [shown, hidden])):
        listed = api.get("/api/v2/files", params={"tag": "client:acme"}, headers=headers(token)).json()["files"]
        assert sorted(f["id"] for f in listed) == sorted(expected), token
        for params in ({"tag": "client:acme"}, {"tag": "client:acme", "q": "orbitalnote"}):
            found = api.get("/api/v2/files/search", params=params, headers=headers(token)).json()["results"]
            assert sorted(f["id"] for f in found) == sorted(expected), (token, params)
    cara = api.get("/api/v2/files/search", params={"tag": "client:acme"}, headers=headers("cara-test"))
    assert hidden not in cara.text and "hidden.html" not in cara.text
    assert api.get("/api/v2/files", params={"tag": "nothing"}, headers=headers("cara-test")).json()["files"] == []

    assert info(api, shown, outsider)["may_edit"] is False
    assert info(api, shown, ops)["may_edit"] is True
    assert tag(api, shown, ops, remove=["client:acme"]).json()["tags"] == []
    activity = api.get(f"/api/v2/files/{shown}/activity", headers=headers(outsider)).text
    assert '"tagged"' in activity and "client:acme" not in activity  # a tag's key is for its readers alone
    listed = api.get("/api/v2/files", params={"tag": "client:acme"}, headers=headers("cara-test")).json()["files"]
    assert listed == []


def test_metadata_is_one_bounded_object_writers_change_and_versions_keep(api):
    ops = bot(api, "ops")
    _, public = tasks(api)
    fid = page(api, ops, "creative.html", public)
    url = f"/api/v2/files/{fid}/metadata"
    r = api.patch(url, json={"set": {"ads": {"meta": "238"}, "angle": "price"}}, headers=headers(ops))
    assert r.status_code == 200, r.text
    outsider = bot(api, "cpo")
    assert api.patch(url, json={"set": {"angle": "x"}}, headers=headers(outsider)).status_code == 403
    assert api.get(url, headers=headers("cara-test")).json()["metadata"] == {"ads": {"meta": "238"}, "angle": "price"}
    too_big = api.patch(url, json={"set": {"blob": "x" * 17000}}, headers=headers(ops))
    assert too_big.status_code == 422, too_big.text
    nan = {**headers(ops), "content-type": "application/json"}
    assert api.patch(url, content='{"set":{"score":NaN}}', headers=nan).status_code == 422  # never stored
    page(api, ops, "creative.html", public, page=fid, text="<p>v2</p>")  # a new version keeps it
    assert info(api, fid, ops)["metadata"] == {"ads": {"meta": "238"}, "angle": "price"}
    r = api.patch(url, json={"set": {"angle": None}, "remove": ["missing"]}, headers=headers(ops))
    assert r.json()["metadata"] == {"ads": {"meta": "238"}}
    for value in (True, 1):  # true and 1 are two values, each saved
        assert api.patch(url, json={"set": {"live": value}}, headers=headers(ops)).status_code == 200
        assert repr(api.get(url, headers=headers(ops)).json()["metadata"]["live"]) == repr(value)
    activity = api.get(f"/api/v2/files/{fid}/activity", headers=headers(ops)).json()["activity"]
    assert [a["action"] for a in activity].count("metadata_changed") == 4


def test_made_from_needs_a_readable_parent_never_loops_and_purge_clears_it(api):
    ops = bot(api, "ops")
    secret, public = tasks(api)
    source = page(api, ops, "source.html", secret)
    child = page(api, ops, "child.html", public, **{"from": source})
    assert info(api, child, ops)["parent"] == {"id": source, "title": "source.html"}
    assert info(api, child, "cara-test")["parent"] is None  # the parent is on a task cara may not read
    looped = api.post(
        "/api/v2/pages",
        json={"name": "source.html", "text": "<p>x</p>", "page": source, "from": child},
        headers=headers(ops),
    )
    assert looped.status_code == 422, looped.text
    missing = api.post(
        "/api/v2/pages",
        json={"name": "other.html", "text": "<p>x</p>", "task": public, "from": "file-nope"},
        headers=headers(ops),
    )
    assert missing.status_code == 404, missing.text
    sql = api.post(
        "/api/v2/sql", json={"sql": "SELECT * FROM bot_files WHERE id='" + child + "'"}, headers=headers("cara-test")
    )
    assert sql.status_code == 200 and source not in sql.text, sql.text  # nor as a bare id through SQL

    post(api, "tags", {"key": "creative"})
    assert tag(api, source, ops, add=["creative"]).status_code == 200
    assert api.delete(f"/api/v2/files/{source}", headers=headers()).status_code == 200
    assert info(api, child, ops)["parent"] is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM file_tags WHERE file_id=?", (source,)).fetchone()[0] == 0
        assert c.execute("SELECT parent_id FROM bot_files WHERE id=?", (child,)).fetchone()[0] is None


def test_an_agent_does_all_three_from_its_command_line(api):
    ops = bot(api, "ops")
    _, public = tasks(api)
    source = page(api, ops, "brief.html", public)
    post(api, "tags", {"key": "client:acme"})
    client = Client(api, ops)

    def tico(*argv):
        args = hubcli.parser().parse_args(list(argv))
        return cli_spec.of(args).call(client, args)

    assert tico("file", "tag", source, "add", "client:acme")["tags"][0]["key"] == "client:acme"
    meta = tico("file", "meta", "set", source, "angle=price", "--json", '{"ads": {"meta": 238}, "angle": "x"}')
    assert meta["metadata"] == {"ads": {"meta": 238}, "angle": "price"}  # a pair wins over the same key in --json
    assert tico("file", "meta", "rm", source, "angle")["metadata"] == {"ads": {"meta": 238}}
    made = hubtools.BY_NAME["tico_file_publish"]["fn"](
        client, {"name": "variant.md", "text": "A variant", "task": public, "from": source}
    )
    shown = tico("file", "show", made["file"]["id"])["file"]
    assert shown["parent"] == {"id": source, "title": "brief.html"} and shown["may_edit"] is True
    found = tico("file", "search", "--tag", "client:acme")["results"]
    assert [f["id"] for f in found] == [source]
