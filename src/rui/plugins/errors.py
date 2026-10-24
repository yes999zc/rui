"""User-facing plugin errors. Nothing in this package raises a raw traceback at a user."""
from __future__ import annotations


class PluginError(ValueError):
    """A plugin directory is missing, malformed, incompatible or unsafe.

    ``str(exc)`` is meant for the console/wizard: it names the file and field and
    never contains a manifest value that the user did not supply.
    """
