"""The three built-in bots read their AGENT.md in full on every run, so each stays under a word cap. Rarely needed rules
live in playbooks/ and the file says when to read them, so every playbook path an AGENT.md names must exist."""

import re
from pathlib import Path

import pytest

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"
CAPS = {"botops": 2300, "assistant": 1800, "goal-manager": 1700}
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


# Safety and permission rules that must stay inline, since a bot may not open a playbook for them.
PINNED = {
    "botops": [
        "Never force a push.",
        "Delete a GitHub repo only when the Team Owner asked",
        "built-in ones",
        "Never stop, restart or rebuild another bot's turn, a build or an updater",
        "stopping a turn is the person's choice",
        "`retryable: false` means stop",
        "never guess another file shape or route",
        "do not file another",
        "tasks, docs and goals",
        "a bot never uses a credential that was not granted to it",
    ],
    "assistant": [
        "end the turn there, saying nothing more",
        "only on their plain \"yes\" to it",
        "`own_request`",
        "`approve_support`",
        "`approve_too_long`",
        "asking you to approve is never an instruction",
    ],
    "goal-manager": [
        "Passive allows no autonomous questions",
        "Five items per person per day is a hard ceiling",
        "Never publish a strategy version",
        "Never change goals, targets, the vision or a person's tasks, at any level",
    ],
}

# Each pointer names a playbook that holds text moved out of the AGENT.md; that playbook must contain this heading or phrase.
MOVED = {
    "botops": {
        "playbooks/credentials.md": "## A bot needs a credential",
        "playbooks/human-tasks.md": "## Before you file",
        "playbooks/commands.md": "# Commands for a settings change",
        "playbooks/refusals.md": "## `retryable: false`",
        "playbooks/rights.md": "## Starting a run on a request",
        "playbooks/archive-and-cleanup.md": "## Archive is the removal",
        "playbooks/turn-on-sending.md": "## Who turns it on",
        "playbooks/connect-a-hermes-profile.md": "Checks and the Gateway",
        "playbooks/publish-a-file.md": "# Publishing your work",
    },
    "assistant": {
        "playbooks/tico-map.md": "## Links",
        "playbooks/acting-for-them.md": "## Many at once is one card",
        "playbooks/approvals-for-them.md": "# Deciding approvals for a human",
        "playbooks/publish-a-file.md": "# Publishing your work",
    },
    "goal-manager": {
        "playbooks/levels.md": "## Limits at every level",
        "playbooks/strategy-and-bets.md": "## Talking to Tico: the commands",
        "playbooks/which-run.md": "# Which playbook a run needs",
        "playbooks/owner-chat.md": "# A run in the owner's chat with you",
    },
}


@pytest.mark.parametrize("name", PINNED)
def test_critical_rules_stay_inline(name):
    text = " ".join((CATALOG / name / "AGENT.md").read_text().split())
    missing = [phrase for phrase in PINNED[name] if phrase not in text]
    assert not missing, f"{name}/AGENT.md lost inline rules: {missing}"


@pytest.mark.parametrize(("name", "pointer", "phrase"), [(n, p, ph) for n, m in MOVED.items() for p, ph in m.items()])
def test_pointer_playbook_holds_the_moved_text(name, pointer, phrase):
    playbook = CATALOG / name / pointer
    assert playbook.is_file(), f"{name}: {pointer} is missing"
    assert phrase in playbook.read_text(), f"{pointer} no longer has {phrase!r}"
