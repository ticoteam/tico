"""A doc's web address is checked before it is stored (backend/docs.py clean_url): a bad one is a 422, never a 500."""

import pytest

from backend.docs import clean_url
from backend.store import Problem


@pytest.mark.parametrize("raw", ["https://[", "[", "https://]x"])
def test_an_address_the_url_parser_rejects_is_a_validation_error(raw):
    with pytest.raises(Problem) as exc:
        clean_url(raw)
    assert exc.value.status == 422


def test_a_plain_address_is_still_accepted():
    assert clean_url("example.com/page") == "https://example.com/page"
