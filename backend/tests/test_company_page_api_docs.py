"""The Company page's routes are in the v2 document, and the rule flag that reaches its editions is in the CLI.

Acme is the fictional company; ana-test is its owner (the base fixtures)."""
from backend.openapi_v2 import spec
from backend.tests.test_api import api  # noqa: F401  (fixture)

PREFIXES = ("/api/v2/company-page", "/api/v2/audiences", "/api/v2/editions", "/api/v2/readers", "/api/v2/reader-messages")


def test_every_company_page_route_the_server_serves_is_in_the_v2_document(api):  # noqa: F811
    # A route added without its document row would be missing from the API reference and from BotOps' list of calls.
    served = {(method.lower(), route.path) for route in api.app.routes if getattr(route, "path", "").startswith(PREFIXES)
              for method in getattr(route, "methods", ()) if method not in ("HEAD", "OPTIONS")}
    documented = {(method, path) for path, ops in spec(api.app)["paths"].items() for method in ops
                  if path.startswith(PREFIXES)}
    assert served and served == documented, (sorted(served - documented), sorted(documented - served))


def test_the_logo_and_the_preview_say_they_send_image_bytes_and_html_not_json(api):  # noqa: F811
    """A client generating code from the document would otherwise send the logo as JSON and read the preview as JSON."""
    ops = spec(api.app)["paths"]
    images = {"image/png", "image/jpeg", "image/webp"}
    assert set(ops["/api/v2/company-page/logo"]["post"]["requestBody"]["content"]) == images
    assert set(ops["/api/v2/company-page/logo"]["get"]["responses"]["200"]["content"]) == images
    assert set(ops["/api/v2/company-page/preview"]["get"]["responses"]["200"]["content"]) == {"text/html"}


def test_the_cli_sends_an_outside_editions_who_for_a_rule_an_owner_adds_or_edits(monkeypatch):
    """`hub rules add --outside-editions` is the Who the Company page's editions read; it must reach the API as that key."""
    from clients import hubcli, remotecli
    sent = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, path, **query):
            return {"actor": "human:ana-test", "kind": "human"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            sent.append((method, path, body))
            return {"rule": {}}

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body)

        def patch(self, path, body=None, key=None):
            return self.call("PATCH", path, body)
    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")

    def run(*argv):
        remotecli.run(hubcli.parser().parse_args(list(argv)))
        return sent[-1]
    method, path, body = run("rules", "add", "--kind", "internal", "--text", "Say the price once.", "--outside-editions")
    assert (method, path) == ("POST", "rules")
    assert body["who"] == {"outside_editions": True}
    method, path, body = run("rules", "edit", "rule-1", "--version", "2", "--outside-editions")
    assert body["who"] == {"outside_editions": True}
    assert "outside_editions" not in run("rules", "add", "--kind", "internal", "--text", "Teammates only.")[2].get("who", {})
