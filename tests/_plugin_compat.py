"""Shared rui_compat range for the plugin manifests the test suite writes.

The v0.4.0 baseline broke 26 tests because four fixtures hard-coded a ceiling
(">=0.2.0 <0.4.0") that the running build no longer satisfied. A fixture only
needs a manifest this build accepts, so derive the ceiling from
rui.__version__ instead of copying a release number into the test.

Keep FIXTURE_COMPAT_FLOOR pinned at the v0 line's floor: the fixtures model
plugins written against the v0 contract, which starts at 0.2.0. Only the ceiling
follows the running build.
"""
from __future__ import annotations

import rui
from rui.plugins import parse_range, parse_version

FIXTURE_COMPAT_FLOOR = "0.2.0"


def fixture_compat_range() -> str:
    """Return a rui_compat range that accepts the running build.

    The ceiling is the minor after the running build (0.7.0 -> <0.8.0), matching
    the shipped v0/v1 matrices. A future bump therefore keeps the fixtures valid
    without widening the range to something meaningless like <999.
    """
    version = parse_version(rui.__version__)
    ceiling = f"{version.major}.{version.minor + 1}.0"
    compat = f">={FIXTURE_COMPAT_FLOOR} <{ceiling}"
    if not parse_range(compat).matches(rui.__version__):
        raise AssertionError(
            f"derived rui_compat {compat!r} does not accept the running build "
            f"{rui.__version__}; fixture_compat_range() is stale"
        )
    return compat
