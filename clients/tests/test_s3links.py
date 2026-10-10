"""s3:// URIs in what a bot writes become view URLs before sending (clients/s3links.py), for the CLI and the MCP tools."""
import logging

import pytest

from clients import hubcli, hubtools, remotecli, s3links

VIEW = "https://d1234example.cloudfront.net"
MAPPING = {"acme-files": VIEW}


def test_the_setting_maps_buckets_to_https_bases_and_ignores_bad_entries_with_a_log_line(caplog):
    with caplog.at_level(logging.WARNING, logger="tico.config"):
        parsed = s3links.parse(" acme-files=https://d1234example.cloudfront.net/ , media.acme=https://cdn.acme.example/files,"
                               "Bad_Bucket=https://x.example, plain-http=http://x.example, creds=https://u:p@x.example,"
                               "query-base=https://x.example/?a=1, acme-files=https://other.example, no-base, ,ab=https://x.example")
    assert parsed == {"acme-files": VIEW, "media.acme": "https://cdn.acme.example/files"}
    ignored = [r.getMessage() for r in caplog.records]
    assert len(ignored) == 7 and all(m.startswith("TICO_S3_VIEW_URLS: ignored") for m in ignored)
    assert not any("u:p" in m for m in ignored), "a base is never logged: it may carry credentials"
    assert s3links.parse("") == {} and s3links.parse(parsed) == parsed


def test_a_key_is_encoded_segment_by_segment_under_the_base_and_its_prefix():
    mapping = s3links.parse("media.acme=https://cdn.acme.example/files")
    assert s3links.view_url("media.acme", "a b/c#1?(x).md", mapping) == "https://cdn.acme.example/files/a%20b/c%231%3F%28x%29.md"
    assert s3links.view_url("other", "k.md", mapping) == ""


def test_mapped_uris_are_rewritten_outside_code_and_unmapped_ones_are_reported():
    text = ("Drafts: s3://acme-files/email/deliverables/u-1/2026-09-09-launch.md, and s3://other-bucket/x/y.md.\n"
            "[hero](s3://acme-files/img/hero.png) `s3://acme-files/in-code.md`\n```\ns3://acme-files/fenced.md\n```\n")
    out, rewritten, unmapped = s3links.rewrite(text, MAPPING)
    assert out == ("Drafts: " + VIEW + "/email/deliverables/u-1/2026-09-09-launch.md, and s3://other-bucket/x/y.md.\n"
                   "[hero](" + VIEW + "/img/hero.png) `s3://acme-files/in-code.md`\n```\ns3://acme-files/fenced.md\n```\n")
    assert [r["from"] for r in rewritten] == ["s3://acme-files/email/deliverables/u-1/2026-09-09-launch.md",
                                              "s3://acme-files/img/hero.png"]
    assert unmapped == ["s3://other-bucket/x/y.md"]


class Api:
    """The server: `config` carries the mapping; each write is recorded and echoed."""
    def __init__(self, mapping=MAPPING):
        self.mapping, self.calls = mapping, []

    def get(self, path, **query):
        self.calls.append(("GET", path))
        if path == "config":
            return {"s3_view_urls": self.mapping}
        if path == "me":
            return {"actor": "bot:finance", "role": "bot"}
        if path.startswith("tasks/"):
            return {"task": {"version": 3}}
        raise AssertionError(path)

    def post(self, path, body=None, key=None):
        self.calls.append(("POST", path, body))
        return {"id": "x1", "sent": body}


def test_the_tools_rewrite_text_before_sending_and_say_so():
    api = Api()
    made = hubtools.BY_NAME["hub_task_create"]["fn"](api, {"owner": "human:ana", "title": "Review the drafts",
                                                          "body": "Draft: s3://acme-files/d/launch.md"})
    assert made["sent"]["body"] == "Draft: " + VIEW + "/d/launch.md"
    assert made["s3_links"]["rewritten"] == [{"from": "s3://acme-files/d/launch.md", "to": VIEW + "/d/launch.md"}]
    assert "rewritten" in made["s3_links"]["note"]
    said = hubtools.BY_NAME["hub_message_send"]["fn"](api, {"to": "human:ana", "text": "See s3://other-bucket/k.md"})
    assert said["sent"]["text"] == "See s3://other-bucket/k.md"
    assert said["s3_links"]["unmapped"] == ["s3://other-bucket/k.md"]
    assert "hub file import s3://" in said["s3_links"]["hint"]
    commented = hubtools.BY_NAME["hub_task_comment"]["fn"](api, {"id": "t1", "text": "`s3://acme-files/a.md` s3://acme-files/b.md"})
    assert commented["sent"]["text"] == "`s3://acme-files/a.md` " + VIEW + "/b.md"
    updated = hubtools.BY_NAME["hub_task_update"]["fn"](api, {"id": "t1", "status": "done", "note": "s3://acme-files/r.pdf"})
    assert updated["sent"]["note"] == VIEW + "/r.pdf"
    chat = hubtools.BY_NAME["hub_chat_send"]["fn"](api, {"to": "finance", "text": "s3://acme-files/c.png"})
    assert chat["sent"]["text"] == VIEW + "/c.png"
    # Text without a bucket URI costs no config read and comes back as it always did.
    before = len(api.calls)
    plain = hubtools.BY_NAME["hub_task_comment"]["fn"](api, {"id": "t1", "text": "Done."})
    assert "s3_links" not in plain and ("GET", "config") not in api.calls[before:]


def test_a_server_without_the_mapping_leaves_text_alone_with_the_hint():
    api = Api(mapping=None)
    made = hubtools.BY_NAME["hub_task_create"]["fn"](api, {"owner": "human:ana", "title": "T", "body": "s3://acme-files/d.md"})
    assert made["sent"]["body"] == "s3://acme-files/d.md" and made["s3_links"]["unmapped"] == ["s3://acme-files/d.md"]


@pytest.mark.parametrize("argv, field", [
    (["task", "create", "--owner", "ana", "--title", "Review", "--body", "s3://acme-files/d.md and s3://other-bucket/e.md"], "body"),
    (["task", "comment", "t1", "s3://acme-files/d.md and s3://other-bucket/e.md"], "text"),
    (["task", "update", "t1", "--status", "done", "--note", "s3://acme-files/d.md and s3://other-bucket/e.md"], "note"),
])
def test_the_cli_rewrites_before_sending(monkeypatch, argv, field):
    api = Api()
    monkeypatch.setattr(remotecli, "Client", lambda *a, **k: api)
    monkeypatch.setenv("HUB_API_URL", "http://hub.invalid")
    monkeypatch.delenv("HUB_OPERATION_ID", raising=False)
    result = remotecli.run(hubcli.parser().parse_args(argv))
    sent = [c for c in api.calls if c[0] == "POST"][-1][2]
    assert sent[field] == VIEW + "/d.md and s3://other-bucket/e.md"
    assert result["s3_links"]["unmapped"] == ["s3://other-bucket/e.md"] and result["s3_links"]["rewritten"]
