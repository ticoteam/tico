import io

from PIL import Image

from backend.tests.test_api import api, headers, setup_attempt  # noqa: F401


def image(kind="PNG", colour="red"):
    out = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(out, kind)
    return out.getvalue()


def test_icon_owner_rights_public_png_cache_and_replacement(api):
    assert api.get("/api/v2/team/icon").status_code == 404
    for token in ("ben-test", "cara-test"):
        assert api.post("/api/v2/team/icon", content=image(), headers=headers(token)).status_code == 403
    _, _, attempt = setup_attempt(api, "coo")
    assert api.post("/api/v2/team/icon", content=image(), headers=headers(attempt["token"])).status_code == 403
    assert api.post("/api/v2/team/icon", content=image(), headers=headers()).json()["url"] == "/api/v2/team/icon"
    first = api.get("/api/v2/team/icon")
    assert first.headers["content-type"] == "image/png"
    assert first.headers["cache-control"] == "public, max-age=300"
    assert api.get("/api/v2/team/icon", headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    for kind in ("JPEG", "WEBP"):
        assert api.post("/api/v2/team/icon", content=image(kind, "blue"), headers=headers()).status_code == 200
    assert api.get("/api/v2/team/icon").headers["etag"] != first.headers["etag"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM blobs WHERE name='team-icon.png'").fetchone()[0] == 1
    assert len([p for p in api.app.state.blobs.directory.rglob("*") if p.is_file()]) == 1


def test_icon_strips_metadata_makes_square_and_weak_etag_matches(api):
    from PIL.PngImagePlugin import PngInfo
    source = io.BytesIO()
    metadata = PngInfo()
    metadata.add_text("private", "acme internal")
    Image.new("RGB", (300, 100), "red").save(source, "PNG", pnginfo=metadata)
    assert api.post("/api/v2/team/icon", content=source.getvalue(), headers=headers()).status_code == 200
    response = api.get("/api/v2/team/icon")
    assert b"acme internal" not in response.content
    with Image.open(io.BytesIO(response.content)) as result:
        assert result.size == (300, 300) and not result.info
        assert result.getpixel((0, 0))[3] == 0
    assert api.get("/api/v2/team/icon", headers={"If-None-Match": '"other", W/' + response.headers["etag"]}).status_code == 304
