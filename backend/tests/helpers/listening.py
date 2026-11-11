"""Helpers that other tests share, moved out of test_listening.py so they import no test file."""

from backend import listening as L
from backend.store import H
from backend.tests.helpers.api import setup_attempt
from backend import bot_settings as BS


RECEIVERS = (
    "social-media-manager",
    "librarian",
    "content-social",
    "influencer",
    "sales-operations-manager",
    "doc-updater",
    "recruiting",
)


# The destinations a company writes in registry/listening.yaml; the hub ships none.
DESTINATIONS = """
destinations:
  market:   {category: market, threshold: 0.70, receiver: bot:librarian, what: a concrete fact about a company in the market}
  content:  {category: content, threshold: 0.75, receiver: content-social, readers: [bot:doc-updater], what: a question a post could answer}
  creators: {category: creator, threshold: 0.75, receiver: bot:influencer, what: a creator the company may want to reach}
  partners: {category: partner, threshold: 0.75, receiver: bot:recruiting, what: a consultancy or trainer, a partner lead}
  leads:    {category: lead, threshold: 0.75, receiver: bot:sales-operations-manager, unless: {category: vendor_pitch, threshold: 0.70}, what: a team lead who might use the product}
"""


def _bots(api):
    (api.app.state.store.settings.registry_dir / L.LISTENING_FILE).write_text(DESTINATIONS)
    with api.app.state.store.transaction() as c:
        for slug in RECEIVERS:
            if not H.bot(c, slug):
                c.execute(
                    "INSERT INTO bots (id, slug, display_name, effort, state, created_at) VALUES (?,?,?,?,?,?)",
                    (slug, slug, slug, "low", "active", H.now()),
                )
            if not c.execute("SELECT 1 FROM bot_config WHERE bot=?", (slug,)).fetchone():
                c.execute(
                    "INSERT INTO bot_config (bot, team, owner_id, description, reports_to, repo) VALUES (?,?,?,?,?,?)",
                    (slug, "marketing", "ana", slug, "cmo", "emp-" + slug),
                )
                BS.write(c, slug, {"runtime": "fake", "model": "fake", "host": "keeper"})


def _token(api, slug):
    return setup_attempt(api, slug)[2]["token"]


def _post(native_id, text, author="@host1"):
    return {
        "native_id": native_id,
        "url": f"https://x.com/{author.strip('@')}/status/{native_id}",
        "author": author,
        "author_url": f"https://x.com/{author.strip('@')}",
        "content": text,
        "published_at": "2026-09-23T10:00:00Z",
    }


class FakeJudge:
    """Scores by keyword so a test says which categories a post should clear."""

    def __init__(self):
        self.calls = []

    def __call__(self, state, questions, label=None):
        self.calls.append((state, label))
        text = state["content"].lower()
        answers = {qid: {"type": "noul", "noul": 0.05} for qid in questions}
        for qid in questions:
            if f"[{qid}]" in text:
                answers[qid]["noul"] = 0.9
        return {"answers": answers, "ms": 1, "model": "fake-judge", "usage": {}}
