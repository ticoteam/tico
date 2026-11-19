"""What the runner's Python side keeps of the GitHub App token path: the hub's token answer is held briefly
(hub_token), and a token the hub refuses is named in plain words (token_problem)."""

from datetime import datetime, timedelta, timezone

import pytest

from runner_support import git_credentials as G


class Hub:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def post(self, path, body):
        self.calls.append(body)
        return self.answer


def test_hub_token_holds_an_answer_while_it_has_margin_then_asks_the_hub_again():
    body = {"bot": "alpha", "purpose": "git"}
    far = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    hub = Hub({"configured": True, "token": "ghs_long", "expires_at": far})
    assert G.hub_token(hub, body)["token"] == "ghs_long"
    assert G.hub_token(hub, body)["token"] == "ghs_long"
    assert hub.calls == [body]  # held: the second ask was answered from the held copy
    near = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    short = Hub({"configured": True, "token": "ghs_short", "expires_at": near})
    G.hub_token(short, body)
    G.hub_token(short, body)
    assert short.calls == [body, body]  # less than the git margin left: never held


@pytest.mark.parametrize(
    'error,wanted',
    [
        (
            ('github_repo_not_accessible', 'acme/web is not reachable; check the GitHub App installation', 409),
            "Tico could not get a GitHub token for acme/web: the GitHub App is not installed on it or cannot reach it",
        ),
        (
            ('forbidden', "That bot's repository is not in the connected organization (acme)", 403),
            'Tico could not get a GitHub token for acme/web: it is outside the connected GitHub organization',
        ),
        (
            ('github_repo_missing', 'The repository acme/web does not exist yet on GitHub.', 409),
            'Tico could not get a GitHub token for acme/web: The repository acme/web does not exist yet on GitHub.',
        ),
    ],
)
def test_token_problem_names_the_hubs_cause_in_plain_words(error, wanted):
    from clients.tico import APIError

    assert G.token_problem(APIError(*error), 'acme/web').startswith(wanted)
    assert 'the server did not answer (TimeoutError)' in G.token_problem(TimeoutError(), 'acme/web')
    secret = 'ghs_' + 'Q' * 36
    assert secret not in G.token_problem(APIError('x', f'bad {secret}', 500), 'acme/web')
