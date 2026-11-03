"""A bot turn's names: TICO_ first, HUB_ as the fallback for a run that only has the old name."""

import pytest

from clients import runenv


def test_the_tico_name_wins_and_the_hub_name_is_the_fallback():
    assert runenv.lookup({"TICO_BOT": "ops", "HUB_BOT": "old"}, "HUB_BOT") == "ops"
    assert runenv.lookup({"HUB_BOT": "old"}, "HUB_BOT") == "old"
    assert runenv.lookup({"TICO_TOKEN": "", "HUB_TOKEN": "t"}, "HUB_TOKEN") == "t"   # an empty twin is no value
    assert runenv.lookup({}, "HUB_BOT", "fallback") == "fallback"
    assert runenv.lookup({"HUB_OPERATION_ID": ""}, "HUB_OPERATION_ID") == ""          # the old name's own empty value


def test_require_raises_when_neither_name_is_set():
    assert runenv.require({"TICO_API_URL": "https://t.example"}, "HUB_API_URL") == "https://t.example"
    with pytest.raises(KeyError):
        runenv.require({}, "HUB_API_URL")
