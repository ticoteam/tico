"""Helpers that other tests share, moved out of test_plan_bucket.py so they import no test file."""

import pytest
from backend import company_file as C


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """A whole 300-seat chart: far over the env's 8 KB and over what one env string may hold once it grows."""
    from backend.tests.helpers.company_file import chart

    draft = chart(300)
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    path = tmp_path_factory.mktemp("plan") / "tico.company.yaml"
    path.write_text(C.to_yaml(draft))
    return path
