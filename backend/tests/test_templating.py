"""Templates (backend/templating.py): `{{path}}` lookups and nothing else; the event catalog's names."""
import json

from backend import event_catalog as C
from backend.templating import CUT, render, render_json


def test_a_path_is_looked_up_and_a_missing_one_is_empty():
    values = {"data": {"repo": {"name": "acme/app"}, "items": [{"id": 7}, {"id": 8}], "ok": True, "n": 3, "none": None},
              "type": "push"}
    assert render("{{type}} on {{ data.repo.name }}: {{data.items.1.id}} {{data.ok}} {{data.n}}", values) \
        == "push on acme/app: 8 true 3"
    assert render("[{{data.nope}}][{{data.items.9}}][{{data.items.x}}][{{data.repo.name.deeper}}][{{none}}]"
                  "[{{data.none}}]", values) == "[][][][][][]"
    assert render("{{data.items}}", values) == '[{"id":7},{"id":8}]'


def test_nothing_is_evaluated_and_a_value_is_never_read_as_a_template():
    values = {"x": "{{y}}", "y": "secret", "name": "{{ __import__('os').system('id') }}"}
    assert render("{{x}} {{name}}", values) == "{{y}} {{ __import__('os').system('id') }}"
    assert render("{{x|upper}} {{ 1+1 }} {% if x %}", values) == "{{x|upper}} {{ 1+1 }} {% if x %}"
    assert render("{{__class__.__mro__}}", {"a": 1}) == ""


def test_output_is_capped_with_a_note_and_json_keeps_its_shape():
    long = {"body": "x" * 500}
    out = render("{{body}}", long, cap=100)
    assert len(out) == 100 and out.endswith(CUT)
    data = render_json({"{{kept}}": "{{body}}", "list": ["a {{body}}", 5, None], "n": 1}, long, cap=300)
    assert len(json.dumps(data, ensure_ascii=False, separators=(",", ":"))) <= 300 and list(data) == ["{{kept}}", "list", "n"]
    assert data["n"] == 1 and data["list"][1:] == [5, None] and CUT in data["list"][0] + data["{{kept}}"]
    assert render_json("{{body}}", long, cap=50).endswith(CUT) and render_json(None, long) is None


def test_the_catalog_names_aliases_and_pass_throughs():
    assert C.canonical("release_published") == "release.published" and C.canonical("recording.ready") == "meeting.ready"
    assert C.canonical("task.done") == "task.done" and C.canonical("webhook.shop") == "webhook.shop"
    assert C.canonical("custom.order-paid") == "custom.order-paid" and C.canonical("nope") is None
    assert C.canonical("finance.budget.cutoff") == "finance.budget.cutoff"
    assert C.canonical("Task.Done") is None and C.canonical(None) is None
    assert C.matches("task.*", "task.done") and C.matches("*", "bot.held") and not C.matches("task.*", "approval.requested")
    assert C.matches("task.done", "task.done") and not C.matches("task.done", "task.done2")
    groups = {e["group"] for e in C.listing()}
    assert groups == {"task", "approval", "needs_you", "bot", "release", "meeting", "strategy", "market", "inbound",
                      "health", "webhook", "tico", "finance", "custom", "org_rule", "rules", "brand", "gtm", "growth"}
    assert all(e["example"] and e["description"] for e in C.listing()) and C.listing("task")[0]["name"] == "task.created"
