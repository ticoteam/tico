"""The Outline's list of prompts reaches past the loaded pages, so it keeps a page's privacy."""

from backend.tests.test_api import api, get, post  # noqa: F401


def test_outline_lists_the_readers_prompts_and_no_one_elses(api):
    first = post(api, "chat/ops", {"text": "Check the release notes"}, "ben-test")
    post(api, "chat/ops", {"text": "Draft   the launch\nemail " + "x" * 300}, "ben-test")
    cid = first["conversation_id"]
    prompts = get(api, f"conversations/{cid}/outline", "ben-test")["prompts"]
    assert [p["id"] for p in prompts][0] == first["id"]
    assert prompts[0]["text"] == "Check the release notes"
    assert prompts[1]["text"].startswith("Draft the launch email x") and len(prompts[1]["text"]) == 140
    # Ben's private chat: the owner reads neither its messages nor its outline.
    assert api.get(f"/api/v2/conversations/{cid}/messages", headers={"Authorization": "Bearer ana-test"}).status_code in (403, 404)
    assert api.get(f"/api/v2/conversations/{cid}/outline", headers={"Authorization": "Bearer ana-test"}).status_code in (403, 404)
