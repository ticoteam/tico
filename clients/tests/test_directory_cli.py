"""`tico contacts` as a bot runs it: each command's argv becomes the request its route takes (path, query or body), and `--text`
prints a short answer without changing what is asked. The routes themselves are in backend/tests/test_directory_tools.py (Contacts was
the Directory; the files keep their names).
"""

import pytest

from clients import hubcli, remotecli


class Recorder:
    """Stands in for the API client: answers each call with a canned body and keeps the request it was given."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def get(self, path, /, **query):
        self.calls.append(("GET", path, {k: v for k, v in query.items() if v is not None}, None))
        return self.answers.get(("GET", path), {})

    def post(self, path, body=None, key=None):
        self.calls.append(("POST", path, None, body))
        return self.answers.get(("POST", path), {})


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    fake = Recorder({})
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: fake)
    return fake


def run(argv):
    return remotecli.run(hubcli.parser().parse_args(argv))


@pytest.mark.parametrize(
    "argv, call",
    [
        (["contacts", "self"], ("GET", "entities/self", {}, None)),
        (["contacts", "types"], ("GET", "entities/types", {}, None)),
        (["contacts", "describe", "person"], ("GET", "entities/types/person", {}, None)),
        (
            ["contacts", "find", "organization", "--name", "acme", "--link", "customer_of", "--limit", "5"],
            ("GET", "entities/find/organization", {"name": "acme", "link": "customer_of", "limit": 5}, None),
        ),
        (["contacts", "get", "jane@example.test"], ("GET", "entities/card", {"ref": "jane@example.test"}, None)),
        (
            ["contacts", "rules", "acme.example", "--as", "librarian", "--action", "email"],
            ("GET", "entities/rules", {"ref": "acme.example", "as_bot": "librarian", "action": "email"}, None),
        ),
        (
            ["contacts", "history", "jane@example.test", "--attribute", "title", "--limit", "10"],
            ("GET", "entities/history", {"ref": "jane@example.test", "attribute": "title", "limit": 10}, None),
        ),
        (
            [
                "contacts",
                "upsert",
                "person",
                "--match",
                "email=jane@example.test",
                "--set",
                "title=CFO",
                "--name",
                "Jane Ortiz",
            ],
            (
                "POST",
                "entities",
                None,
                {
                    "type": "person",
                    "match": {"email": "jane@example.test"},
                    "values": {"title": "CFO"},
                    "name": "Jane Ortiz",
                },
            ),
        ),
        (
            [
                "contacts",
                "upsert",
                "person",
                "--match",
                "email=jane@example.test",
                "--set",
                "title=a=b",
                "--confidence",
                "0.7",
            ],
            (
                "POST",
                "entities",
                None,
                {
                    "type": "person",
                    "match": {"email": "jane@example.test"},
                    "values": {"title": "a=b"},
                    "confidence": 0.7,
                },
            ),
        ),
        (
            ["contacts", "link", "organization/acme", "customer_of", "company/self", "--from", "2025-03-01"],
            (
                "POST",
                "entities/links",
                None,
                {
                    "from_ref": "organization/acme",
                    "link_key": "customer_of",
                    "to_ref": "company/self",
                    "valid_from_on": "2025-03-01",
                },
            ),
        ),
        (["contacts", "unlink", "lnk_1"], ("POST", "entities/unlink", None, {"link_id": "lnk_1"})),
        (
            ["contacts", "merge", "organization/acme", "organization/acme-old"],
            ("POST", "entities/merge", None, {"keep": "organization/acme", "drop": "organization/acme-old"}),
        ),
        (
            ["contacts", "unmerge", "organization/acme-old"],
            ("POST", "entities/unmerge", None, {"drop": "organization/acme-old"}),
        ),
        (
            ["contacts", "note", "jane@example.test", "Prefers phone", "--audience", "me"],
            ("POST", "entities/notes", None, {"ref": "jane@example.test", "body": "Prefers phone", "audience": "me"}),
        ),
        (
            [
                "contacts",
                "attribute",
                "add",
                "organization",
                "crm_stage",
                "--name",
                "CRM stage",
                "--value-type",
                "select",
                "--description",
                "Where the deal stands",
                "--option",
                "won=Won",
                "--option",
                "lost=Lost",
            ],
            (
                "POST",
                "entities/types/organization/attributes",
                None,
                {
                    "key": "crm_stage",
                    "name": "CRM stage",
                    "description": "Where the deal stands",
                    "value_type": "select",
                    "options": [{"key": "won", "label": "Won"}, {"key": "lost", "label": "Lost"}],
                },
            ),
        ),
        (
            ["contacts", "type", "add", "vendor", "--name", "Vendor", "--description", "A company we buy from"],
            (
                "POST",
                "entities/types",
                None,
                {"key": "vendor", "name": "Vendor", "description": "A company we buy from"},
            ),
        ),
        (
            [
                "contacts",
                "link-type",
                "add",
                "advises",
                "--name",
                "advises",
                "--inverse",
                "advised by",
                "--description",
                "Counsel to",
                "--symmetric",
            ],
            (
                "POST",
                "entities/link-types",
                None,
                {
                    "key": "advises",
                    "name": "advises",
                    "inverse_name": "advised by",
                    "description": "Counsel to",
                    "symmetric": True,
                },
            ),
        ),
    ],
)
def test_each_contacts_command_sends_its_route_its_query_and_its_body(recorder, argv, call):
    run(argv)
    method, path, query, body = call
    assert len(recorder.calls) == 1
    got_method, got_path, got_query, got_body = recorder.calls[0]
    assert (got_method, got_path) == (method, path)
    if query is not None:
        assert got_query == query  # exact: an unset flag is not sent at all
    if body is not None:
        # The fields the command gave are all there; a switch it did not set may be sent as false, which is its default.
        assert {k: got_body.get(k) for k in body} == body
    # The flags a command reads are not sent along: the tools take their own fields, and --text is for the terminal.
    assert "as_text" not in (got_query or {})
    assert "cmd" not in (got_body or {}) and "fn" not in (got_body or {})


def test_text_is_for_the_terminal_and_the_tool_is_asked_the_same_question(recorder):
    recorder.answers[("GET", "entities/card")] = {
        "ambiguous": False,
        "id": "person/jane-ortiz",
        "name": "Jane Ortiz",
        "type": "person",
        "values": {"title": {"value": "CFO", "source_type": "hub", "updated_at": "2026-11-07T10:00:00Z"}},
        "links": [{"link_key": "works_at", "other_name": "Acme Lights", "other_type": "organization"}],
        "refs": [],
        "notes": [{"audience": "me", "author_key": "human:ana", "body": "Prefers phone"}],
    }
    run(["contacts", "get", "jane@example.test", "--text"])
    assert recorder.calls[0][2] == {"ref": "jane@example.test"}  # the same question as without --text


def test_text_prints_a_card_and_a_list_and_says_when_a_reference_is_ambiguous():
    card = {
        "ambiguous": False,
        "id": "person/jane-ortiz",
        "name": "Jane Ortiz",
        "type": "person",
        "values": {"title": {"value": "CFO", "source_type": "hub", "updated_at": "2026-11-07T10:00:00Z"}},
        "links": [{"link_key": "works_at", "other_name": "Acme Lights", "other_type": "organization"}],
        "refs": [],
        "notes": [],
    }
    text = remotecli.contacts_text("contacts get", card)
    assert text.splitlines()[0] == "Jane Ortiz (person) person/jane-ortiz"
    assert "  title: CFO (from hub, 2026-11-07)" in text
    assert "  works_at: Acme Lights (organization)" in text

    listing = {
        "rows": [{"id": "organization/acme", "name": "Acme", "status": "active", "links_to_us": ["customer_of"]}],
        "next": "abc",
    }
    assert remotecli.contacts_text("contacts find", listing).splitlines() == [
        "organization/acme  Acme  active  [customer_of]",
        "more: --after abc",
    ]

    ambiguous = {
        "ambiguous": True,
        "ref": "shared.example",
        "matches": [{"id": "a", "name": "A", "status": "active"}, {"id": "b", "name": "B", "status": "active"}],
    }
    text = remotecli.contacts_text("contacts get", ambiguous)
    assert text.startswith("shared.example matches 2 entities; name one by id:")


def test_tico_contacts_runs_and_the_old_directory_command_is_unknown(recorder, capsys):
    """Directory was renamed Contacts with no alias: `tico contacts` runs, `tico directory` is an unknown command."""
    run(["contacts", "types"])
    assert recorder.calls == [("GET", "entities/types", {}, None)]
    with pytest.raises(SystemExit) as refused:
        hubcli.parser().parse_args(["directory", "types"])
    assert refused.value.code == 2
    assert "invalid choice: 'directory'" in capsys.readouterr().err
    from clients import hubtools

    names = {t["name"] for t in hubtools.TOOLS}
    assert "tico_contacts_find" in names and not any(n.startswith("tico_directory") for n in names)
