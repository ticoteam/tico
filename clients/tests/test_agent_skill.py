"""skills/who-needs-me/SKILL.md carries the same text the MCP server sends as its instructions."""

from pathlib import Path

from clients.agent_skill import WHO_NEEDS_ME

SKILL = Path(__file__).resolve().parents[2] / "skills" / "who-needs-me" / "SKILL.md"


def test_the_skill_file_is_the_constant_under_its_front_matter():
    text = SKILL.read_text()
    assert text.startswith("---\n")
    body = text.split("\n---\n\n", 1)[1]
    assert body == WHO_NEEDS_ME
