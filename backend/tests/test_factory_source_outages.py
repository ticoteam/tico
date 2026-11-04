"""Small factory boundary scenarios also run in hosted PR checks; the full factory explores more seeds."""
import pytest

from evals.factory.cases.source_outages import SCENARIOS
from evals.factory.sim import Run


@pytest.mark.parametrize('scenario', SCENARIOS, ids=lambda scenario: scenario.name)
def test_source_interruption_factory_scenario(scenario):
    run = Run(scenario, seed=1).go()
    for label, _, check in scenario.checks:
        passed, detail = check(run)
        assert passed, f'{label}: {detail}'
