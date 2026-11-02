"""The Beta features: the one list the app's Beta pill, Help, the manual and support diagnostics read.

A Beta feature works today and may still change. The web app gets this list in `/api/v2/config` (`beta`) and draws
the pill only for an id here (ui/app/state.js `betaPill`). Each `docs` entry names a manual page and the heading the
feature lives under (None for the page's title); that heading is followed by the marker line `MARKER` opens, and no
other heading is (backend/tests/test_beta.py). `on` says whether an install has the feature turned on or in use, for
the support bundle (backend/diagnostics.py); None where the server cannot tell.
"""
import sqlite3
from collections import namedtuple

# `bots` marks the bots that are this feature, for the pill on a bot's page and template card: {"templates": [...],
# "harnesses": [...]}.
Feature = namedtuple("Feature", "id name docs on bots", defaults=(None,))

# The line under a Beta heading in docs/*.md.
MARKER = "> **Beta**"


def _rows(c, sql, args=()):
    return bool(c.execute(sql, args).fetchone())


def _bot_harness(c, harnesses):
    marks = ",".join("?" * len(harnesses))
    return _rows(c, "SELECT 1 FROM bot_config b JOIN bots ON bots.slug=b.bot WHERE bots.state!='archived' AND "
                    f"json_extract(b.config,'$.harness') IN ({marks}) LIMIT 1", tuple(harnesses))


def _external_harnesses():
    from .agents import PAIRABLE
    from .external_sync import HARNESSES
    return PAIRABLE + HARNESSES


FEATURES = (
    # A WSL computer reports itself as Linux, so the server cannot tell one is connected.
    Feature("windows", "Windows computers through WSL 2", (("install.md", "Windows PC (WSL 2)"),), None),
    Feature("chat_goals", "Chat goals", (("chat-goals.md", None),),
            lambda c, s, shell: _rows(c, "SELECT 1 FROM chat_goals LIMIT 1")),
    Feature("message_bots", "Message bots: mail and calendar",
            (("messaging.md", None), ("mail.md", None), ("mail-service.md", None)),
            lambda c, s, shell: _rows(c, "SELECT 1 FROM mail_mailboxes LIMIT 1"), {"templates": ["inbox"]}),
    Feature("slack_gateway", "Slack gateway", (("slack-gateway.md", None),),
            lambda c, s, shell: bool(s.slack_gateway_enabled)),
    Feature("listening", "Listening", (("listening.md", None),),
            lambda c, s, shell: _rows(c, "SELECT 1 FROM bots WHERE slug='listening' AND state='active'"),
            {"templates": ["listening"]}),
    Feature("live_meetings", "Live Meetings API and Recorder", (("live-meetings-api.md", None),),
            lambda c, s, shell: _rows(c, "SELECT 1 FROM live_meetings LIMIT 1")),
    Feature("computer_screen", "Computer screen (View and Control)",
            (("install.md", "Screen"), ("permissions.md", "A computer's screen")),
            lambda c, s, shell: _rows(c, "SELECT 1 FROM runners WHERE revoked_at IS NULL AND screen!='off' LIMIT 1")),
    Feature("external_agents", "External agents: Hermes, OpenClaw, Grok Bot, Dots, Muse sync",
            (("hermes-agents.md", None), ("openclaw-agents.md", None), ("external-agent-sync.md", None)),
            lambda c, s, shell: _bot_harness(c, _external_harnesses()), {"harnesses": list(_external_harnesses())}),
    Feature("replay_performance", "Behaviour replay and the computer Performance page",
            (("releasing.md", "Behaviour replay"), ("permissions.md", "A computer's performance")),
            lambda c, s, shell: _rows(c, "SELECT 1 FROM runners WHERE revoked_at IS NULL AND performance='on' LIMIT 1")),
    # Beta until the Mac app is signed.
    Feature("mac_app", "The Mac desktop app", (("desktop.md", None),),
            lambda c, s, shell: bool(shell and shell.get("mac"))),
)
IDS = tuple(f.id for f in FEATURES)


def anchor(heading):
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces as hyphens."""
    return "".join(ch for ch in heading.lower() if ch.isalnum() or ch in " -_").replace(" ", "-")


def listing():
    """The list for clients: id, name, its manual pages (`page.md#heading`) and the bots that are the feature."""
    return [{"id": f.id, "name": f.name, **(f.bots or {}),
             "docs": [page + ("#" + anchor(heading) if heading else "") for page, heading in f.docs]} for f in FEATURES]


def turned_on(c, settings, shell=None):
    """The ids of the Beta features this install has on or in use. A check that cannot read its table leaves its
    feature out rather than failing the support bundle."""
    out = []
    for f in FEATURES:
        if f.on is None:
            continue
        try:
            if f.on(c, settings, shell):
                out.append(f.id)
        except sqlite3.Error:
            continue
    return out
