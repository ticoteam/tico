"""GitHub's installation token formats reach a bot's git unchanged (offline, a fake GitHub). Since Oct 2026 GitHub mints
stateless tokens (`ghs_<app id>_<JWT>`, about 520 characters with dots and dashes); the old ones were `ghs_` and 36
letters and digits. The end-to-end check against the real GitHub is the release check
runner_support/tests/test_github_git.py; this pins the hub's half of the path on every PR: the token GitHub answers is
the token the runner is handed, for the turn and for each git call."""

import pytest

from backend.tests.helpers.github_app import api, connect, gh, runner_token, stateless_token, turn_token  # noqa: F401


@pytest.mark.parametrize("fmt", ["stateless", "legacy"])
def test_the_minted_token_is_handed_over_exactly_as_github_issued_it(api, gh, fmt):
    issued = []
    legacy = gh.new_token

    def mint():
        issued.append(stateless_token() if fmt == "stateless" else legacy())
        return issued[-1]

    gh.new_token = mint
    connect(api)
    runner_token(api, "cpo")
    for purpose in ("turn", "git"):
        r = turn_token(api, "cpo", purpose)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["token"] == issued[-1], (purpose, len(data["token"]), len(issued[-1]))
        assert [group["token"] for group in data["tokens"]] == [issued[-1]], data["tokens"]
    if fmt == "stateless":
        token = issued[-1]
        assert len(token) > 400 and token.count(".") == 2 and token.startswith("ghs_4242_"), "not the new format"


def test_a_captured_stream_masks_both_token_formats():
    from backend import stream_capture

    token = stateless_token()
    masked = stream_capture.mask(f"remote: {token} and ghs_{'a1' * 18} refused")
    assert masked == f"remote: {stream_capture.MASK} and {stream_capture.MASK} refused", masked
