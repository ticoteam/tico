"""The factory scenario list is the same whichever module a process imports first.

evals/factory/scenarios.py joins the case modules at its end, and each case module imports its helpers from
scenarios.py, so importing a case module first used to leave the list short. This package module loads the list first,
so the cycle always starts in the same place."""

from evals.factory import scenarios  # noqa: F401  (imported for its side effect: the scenario list is complete after it)
