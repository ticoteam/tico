"""The company file engine behind tico.team/setup: the draft, its edit actions, and tico.company.yaml (format 0.2).

No model and no transport: functions over the draft dict the page keeps. Whatever serves the page's
`POST /api/onboarding/turn` gives the model `SPEC["actions"]` as its tools and runs each tool call through a `Turn`:

    turn = Turn(request["draft"])            # DraftError when the draft breaks its shape
    result = turn.apply(name, tool_input)    # {"ok": True, "said": ...} or {"ok": False, "refused": ...}
    draft = turn.finish()                    # what goes back to the page; `undo` holds the plan before the turn
    turn.keys_removed                        # a key-shaped string was cut: tell the visitor not to paste keys

Standard library only, except that `from_yaml` needs PyYAML. Self-contained so it can be copied as is: Tico HQ
(ticoteam/tico-services) serves the setup page from a copy taken from a published Tico release
(`scripts/build_catalog_json.py --out DIR`), with the actions, catalogue and company models for the page's browser planner.
"""
from .actions import Refusal, Turn, apply_action, undo
from .draft import LIMITS, MODELS, ROLES, SEATS, SPEC, DraftError, blank
from .keys import scrub, scrub_all
from .yamlfile import FORMAT, from_yaml, to_yaml

__all__ = ["DraftError", "FORMAT", "LIMITS", "MODELS", "ROLES", "Refusal", "SEATS", "SPEC", "Turn", "apply_action", "blank",
           "from_yaml", "scrub", "scrub_all", "to_yaml", "undo", "validate"]


def validate(draft):
    """A valid copy of a draft, key-shaped strings removed, or DraftError naming the field that is wrong."""
    from .draft import check

    return check(scrub_all(draft)[0])
