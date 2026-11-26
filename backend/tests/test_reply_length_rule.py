"""One reply length for every bot, the Assistant included: the answer first, about 90 words unless the person asked for
more (spec "Bot replies a person reads at a glance", AC-2). The other bots get it from runner-rs/src/prompt.rs, whose
own tests check it; the Assistant's replies follow its rulebook."""

import re
from pathlib import Path

RULEBOOK = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "assistant" / "AGENTS.md"


def replies_section():
    text = RULEBOOK.read_text()
    m = re.search(r"^## Replies\n(.*?)(?=^## )", text, re.S | re.M)
    assert m, "the Assistant's rulebook has no Replies section"
    return " ".join(m.group(1).split())


def test_the_assistant_replies_in_about_90_words_and_an_ask_for_more_wins():
    replies = replies_section()
    assert "Answer first, about 90 words at most unless they ask for more" in replies, replies
    # One number only: no second word count beside it.
    assert re.findall(r"(\d+) words", replies) == ["90"], replies


def test_an_ask_for_every_item_lifts_the_five_item_list_limit():
    replies = replies_section()
    assert re.search(r"five at most unless they ask for (all|every)", replies), replies
