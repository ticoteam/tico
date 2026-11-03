"""Meeting importers that run on an enrolled computer and file through the hub's import API."""

REGISTRY = {"granola": ("granola", "Granola"),
            "zoom": ("zoom", "Zoom"), "google-meet": ("google_meet", "GoogleMeet")}


def importer_class(source):
    import importlib
    module, name = REGISTRY[source]
    return getattr(importlib.import_module("." + module, __name__), name)
