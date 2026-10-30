"""POST /api/v2/gateway/images (backend/app.py): the route forwards the prompt, category, references, size and count to the
Gateway's image maker with the company's key, answers the Gateway's shape, maps its refusals to plain problems, and never
reaches the Gateway for a refused request. The Gateway is faked: no call leaves the test."""
from backend.tests.test_api import api, headers  # noqa: F401
from clients import gateway as G


def connect(api):
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url = "gw-synthetic", "https://hq.example/v1/gateway/decisions"


def test_the_images_route_forwards_the_request_and_answers_the_gateways_shape(api, monkeypatch):
    connect(api)
    calls = []

    def image(base, key, prompt, category, images, size, count, header):
        calls.append((base, key, prompt, category, list(images), size, count))
        return 200, {"model": "tico-images-1.0", "category": category, "images": [{"mime": "image/png", "b64": "AAAA"}],
                     "usage": {"cost_usd": 0.05}}, ""
    monkeypatch.setattr(G, "image", image)
    result = api.post("/api/v2/gateway/images", json={"prompt": "a kettle", "category": "photo", "size": "1920x1080",
                                                      "count": 2}, headers=headers("ana-test"))
    assert result.status_code == 200, result.text
    assert result.json() == {"model": "tico-images-1.0", "category": "photo",
                             "images": [{"mime": "image/png", "b64": "AAAA"}], "usage": {"cost_usd": 0.05}}
    assert calls[0][1:] == ("gw-synthetic", "a kettle", "photo", [], "1920x1080", 2)
    api.post("/api/v2/gateway/images", json={"prompt": "x", "category": "storyboard"}, headers=headers("ana-test"))
    assert calls[1][3:] == ("storyboard", [], None, 1)


def test_the_images_route_checks_its_input_and_needs_a_connected_gateway(api, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A refused request must not reach the Gateway")
    monkeypatch.setattr(G, "image", forbidden)
    for body in ({"prompt": "x"}, {"prompt": "", "category": "photo"}, {"prompt": "x", "category": "video"},
                 {"prompt": "x", "category": "photo", "count": 0}, {"prompt": "x", "category": "photo", "count": 5}):
        assert api.post("/api/v2/gateway/images", json=body, headers=headers("ana-test")).status_code == 422, body
    refused = api.post("/api/v2/gateway/images", json={"prompt": "x", "category": "photo"}, headers=headers("ana-test"))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "gateway_not_connected"


def test_the_gateways_refusals_come_back_as_plain_problems(api, monkeypatch):
    connect(api)
    for status, code, want_status, want in ((404, "404", 503, "gateway_image_unavailable"),
                                            (422, "invalid", 422, "gateway_image_failed"),
                                            (429, "cap_reached", 503, "gateway_image_failed"),
                                            (0, "unreachable", 503, "gateway_image_failed")):
        monkeypatch.setattr(G, "image", lambda *args, status=status, code=code: (status, {}, code))
        refused = api.post("/api/v2/gateway/images", json={"prompt": "x", "category": "graphic"}, headers=headers("ana-test"))
        assert (refused.status_code, refused.json()["error"]["code"]) == (want_status, want), refused.text
