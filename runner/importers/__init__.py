"""`import runner.importers`: runner_support/importers/ (runner/__init__.py). A package of its own, so
`runner.importers.<module>` loads the forwarder beside this file and is the same module as runner_support's."""
from runner_support import importers as _real


def __getattr__(name):
    return getattr(_real, name)
