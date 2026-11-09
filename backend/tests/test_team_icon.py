import io

from PIL import Image

from backend.tests.helpers.api import headers, setup_attempt  # noqa: F401
from backend.tests.helpers.team_icon import image


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
    assert (
        api.get("/api/v2/team/icon", headers={"If-None-Match": '"other", W/' + response.headers["etag"]}).status_code
        == 304
    )


def test_apple_touch_icon_is_opaque_180_square_with_the_logo_fitted_and_padded():
    from backend import team_icon

    logo = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    logo.paste((255, 0, 0, 255), (10, 10, 30, 30))
    source = io.BytesIO()
    logo.save(source, "PNG")
    with Image.open(io.BytesIO(team_icon.touch_png(source.getvalue()))) as icon:
        assert icon.format == "PNG" and icon.size == (180, 180) and icon.mode == "RGB"
        assert icon.getpixel((0, 0)) == (255, 255, 255)  # the padding is white, not the transparent black of the logo
        assert icon.getpixel((20, 20)) == (255, 255, 255)  # inside the fitted box, where the logo is transparent
        red = icon.getpixel((90, 90))  # the logo's own colour, in the middle
        assert red[0] > 240 and red[1] < 15 and red[2] < 15
