"""The names a bot turn is given (`TICO_TOKEN`, `TICO_BOT`, ...). The runner sets the `TICO_` name and, during the rename
overlap, the older `HUB_` name with the same value (runner-rs/src/env.rs). A reader takes `TICO_` first and falls back to
`HUB_`, so a turn started by an older runner, or a script written against the old names, still works."""

import os


def lookup(environ, name, default=None):
    """The value of a `HUB_` name read as its `TICO_` twin first: `lookup(os.environ, "HUB_BOT")` takes TICO_BOT, else
    HUB_BOT, else `default`."""
    twin = "TICO_" + name[len("HUB_"):] if name.startswith("HUB_") else name
    for key in (twin, name):
        if environ.get(key):
            return environ[key]
    return environ.get(name, default)


def require(environ, name):
    """`lookup` for a name that must be set: KeyError when neither is, as `environ[name]` is."""
    value = lookup(environ, name)
    if value is None:
        raise KeyError(name)
    return value


def get(name, default=None):
    """`lookup` on the process environment."""
    return lookup(os.environ, name, default)
