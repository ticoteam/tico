"""Independent checks for caller authority and uncertain external creation through MCP."""

from backend.tests.helpers.github_app import api, gh, auth, connect  # noqa: F401


def mcp(api, args, token="owner-test"):
    return api.post(
        "/api/v2/mcp",
        headers=auth(token),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "hub_repo_product_create", "arguments": args},
        },
    )


def test_non_owner_cannot_invoke_hidden_product_tool_or_contact_github(api, gh):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}
    before = len(gh.calls)
    args = {"name": "tico-recorder", "operation_id": "review-non-owner", "confirm_repository": "Acme/tico-recorder"}
    for token in ("person-test", "bot-test", "botops-test", "runner-test"):
        response = mcp(api, args, token)
        if response.status_code == 200:
            result = response.json()["result"]
            assert result["isError"] and result["structuredContent"]["error"] == "forbidden"
        else:
            assert response.status_code in (401, 403)
        assert len(gh.calls) == before, "a forbidden direct call must not even fetch a preview"
    assert not gh.created_repositories


def test_mcp_uncertain_creation_preserves_original_target_and_never_creates_again(api, gh):
    connect(api, administration="true")
    gh.permissions = {"administration": "write", "metadata": "read"}
    args = {"name": "tico-recorder", "operation_id": "review-uncertain-mcp", "confirm_repository": "Acme/tico-recorder"}
    gh.lose_next_product_create_response = True
    first = mcp(api, args).json()["result"]
    assert first["isError"] and first["structuredContent"]["error"] == "github_create_outcome_unknown"
    changed = mcp(api, {**args, "name": "another-product", "confirm_repository": "Acme/another-product"}).json()[
        "result"
    ]
    assert changed["isError"] and changed["structuredContent"]["error"] == "idempotency_conflict"
    same = mcp(api, args).json()["result"]
    assert same["isError"] and same["structuredContent"]["error"] == "github_create_outcome_unknown"
    assert gh.created_repositories == {"Acme/tico-recorder"}
    assert len(gh.of("/orgs/Acme/repos")) == 1
