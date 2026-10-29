"""The three built-in bots read their AGENT.md in full on every run, so each stays under a word cap. Rarely needed rules
live in playbooks/ and the file says when to read them, so every playbook path an AGENT.md names must exist."""

import re
from pathlib import Path

import pytest

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"
CAPS = {"botops": 1897, "assistant": 1461, "goal-manager": 1484}
PLAYBOOK = re.compile(r"playbooks/[\w.-]+\.md")


@pytest.mark.parametrize("name", CAPS)
def test_builtin_agent_stays_under_its_word_cap(name):
    words = len((CATALOG / name / "AGENT.md").read_text().split())
    assert words <= CAPS[name], f"{name}/AGENT.md is {words} words; move rarely needed rules to playbooks/ (cap {CAPS[name]})"


@pytest.mark.parametrize("name", CAPS)
def test_every_playbook_its_agent_names_exists(name):
    folder = CATALOG / name
    named = set(PLAYBOOK.findall((folder / "AGENT.md").read_text()))
    assert named, f"{name}/AGENT.md points at no playbook"
    missing = sorted(path for path in named if not (folder / path).is_file())
    assert not missing, missing
