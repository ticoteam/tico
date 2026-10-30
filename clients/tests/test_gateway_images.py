"""`hub gateway image` and the `hub_gateway_image` tool (clients/hubtools.py): the references are read and checked on this
computer before anything is sent, an existing file is never overwritten, the images are saved beside each other with a
sidecar that says how each was made (prompt, category, model, cost, reference names and sha256), and the client sends the
Gateway's shape (clients/gateway.py `image`). The Gateway is mocked: nothing is sent anywhere."""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from clients import gateway as G
from clients import hubcli, hubtools, remotecli

PNG = b"\x89PNG\r\n\x1a\n" + b"reference bytes"
MADE = b"\x89PNG\r\n\x1a\n" + b"made"


@pytest.fixture
def sent(monkeypatch):
    """The calls the CLI makes to the server, with a fake answer of `count` images at $0.05."""
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, path, body, key=None):
            calls.append((path, body))
            return {"model": "tico-images-1.0", "category": body["category"],
                    "images": [{"mime": "image/png", "b64": base64.b64encode(MADE + bytes([n])).decode()}
                               for n in range(body["count"])],
                    "usage": {"cost_usd": 0.05}}

    monkeypatch.setattr(remotecli, "Client", FakeClient)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    return calls


def run(argv):
    return remotecli.run(hubcli.parser().parse_args(argv))


def test_images_are_saved_beside_each_other_with_a_sidecar_for_each(sent, tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG)
    out = tmp_path / "shots" / "frame.png"
    result = run(["gateway", "image", "a kettle", "--category", "character", "--ref", str(ref), "--count", "2",
                  "--out", str(out)])
    assert [Path(p).name for p in result["saved"]] == ["frame.png", "frame-2.png"]
    assert out.read_bytes() == MADE + b"\x00" and (out.parent / "frame-2.png").read_bytes() == MADE + b"\x01"
    (path, body), = sent
    assert path == "gateway/images" and body["category"] == "character" and body["count"] == 2
    assert body["images"] == ["data:image/png;base64," + base64.b64encode(PNG).decode()]
    assert "out" not in body and "refs" not in body
    sidecar = json.loads((out.parent / "frame.png.json").read_text())
    assert sidecar["prompt"] == "a kettle" and sidecar["category"] == "character" and sidecar["cost_usd"] == 0.05
    assert sidecar["model"] == "tico-images-1.0" and sidecar["index"] == 1 and sidecar["count"] == 2
    assert sidecar["refs"] == [{"name": "ref.png", "sha256": hashlib.sha256(PNG).hexdigest()}]
    assert json.loads((out.parent / "frame-2.png.json").read_text())["index"] == 2


def test_a_reference_that_is_not_an_image_or_too_big_is_refused_before_anything_is_sent(sent, tmp_path):
    gif = tmp_path / "anim.gif"
    gif.write_bytes(b"GIF89a")
    with pytest.raises(ValueError, match="PNG, JPEG or WebP"):
        run(["gateway", "image", "x", "--category", "photo", "--ref", str(gif), "--out", str(tmp_path / "a.png")])
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * hubtools.IMAGE_REF_BYTES)
    with pytest.raises(ValueError, match="2 MiB"):
        run(["gateway", "image", "x", "--category", "photo", "--ref", str(big), "--out", str(tmp_path / "b.png")])
    assert sent == []


def test_an_existing_file_is_never_overwritten_and_nothing_is_sent(sent, tmp_path):
    out = tmp_path / "frame.png"
    out.write_bytes(b"keep me")
    with pytest.raises(ValueError, match="exist"):
        run(["gateway", "image", "x", "--category", "photo", "--out", str(out)])
    assert out.read_bytes() == b"keep me" and sent == []


def test_the_output_names_and_the_image_types_are_read_from_the_bytes_and_the_name():
    assert [p.name for p in hubtools.image_outputs("shots/a.png", 3)] == ["a.png", "a-2.png", "a-3.png"]
    assert [p.name for p in hubtools.image_outputs("shots/a", 2)] == ["a.png", "a-2.png"]
    assert hubtools.image_type(PNG) == "image/png" and hubtools.image_type(b"\xff\xd8\xff\xe0x") == "image/jpeg"
    assert hubtools.image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp" and hubtools.image_type(b"GIF89a") is None


def test_the_client_sends_the_gateways_shape_and_keeps_only_the_images(monkeypatch):
    sent = {}

    def post(url, body, headers, timeout, opener):
        sent.update(url=url, body=body, headers=headers, timeout=timeout)
        return 200, {"model": "tico-images-1.0", "category": "photo", "images": [
            {"mime": "image/png", "b64": "AAAA"}, {"mime": "image/png"}, "junk"],
            "usage": {"cost_usd": 0.07}, "provider": "openai"}, ""
    monkeypatch.setattr(G, "_post", post)
    status, answer, code = G.image("https://gateway.example.test/v1/gateway", "tgw-key", "a fox", "photo", [],
                                   "1920x1080", 2, {"X-Tico-Bot": "ops"})
    assert (status, code) == (200, "")
    assert sent["url"] == "https://gateway.example.test/v1/gateway/images" and sent["timeout"] == 130
    assert sent["body"] == {"prompt": "a fox", "category": "photo", "count": 2, "size": "1920x1080"}
    assert sent["headers"] == {"Authorization": "Bearer tgw-key", "X-Tico-Bot": "ops"}
    assert answer == {"model": "tico-images-1.0", "category": "photo", "images": [{"mime": "image/png", "b64": "AAAA"}],
                      "usage": {"cost_usd": 0.07}}
    monkeypatch.setattr(G, "_post", lambda *args: (404, {}, "not_found"))
    assert G.image("https://gateway.example.test/v1/gateway", "k", "x", "storyboard") == (404, {}, "not_found")
