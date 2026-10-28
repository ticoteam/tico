"""Scenarios kept one per file: each module's `SCENARIOS` list joins scenarios.SCENARIOS, after the built-in ones.
A case module imports what it needs from evals.factory.scenarios (Scenario, standard, tickets, the checks) and adds
its own setup and checks beside them; add one import line below for it. The imports are literal so the PR checks'
dependency map (scripts/pr_checks.py) sees exactly which files the sim runs."""
SCENARIOS = []
# One line per case, in name order:  from .<name> import SCENARIOS as _<name>; SCENARIOS += _<name>
from .github_read_outage import SCENARIOS as _github_read_outage; SCENARIOS += _github_read_outage
from .qa_waiver_head import SCENARIOS as _qa_waiver_head; SCENARIOS += _qa_waiver_head
from .receipt_wording import SCENARIOS as _receipt_wording; SCENARIOS += _receipt_wording
from .snoozed_ticket import SCENARIOS as _snoozed_ticket; SCENARIOS += _snoozed_ticket
from .verdict_past_open_sibling import SCENARIOS as _verdict_past_open_sibling; SCENARIOS += _verdict_past_open_sibling
from .source_outages import SCENARIOS as _source_outages; SCENARIOS += _source_outages
