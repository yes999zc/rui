#!/usr/bin/env python3
"""Image entry point for the container health probe.

The rules live in :mod:`rui.healthcheck` (installed as the ``rui-healthcheck`` console
script); this file is what the Dockerfile copies to ``/usr/local/bin/rui-healthcheck``.
"""
from __future__ import annotations

import sys

from rui.healthcheck import evaluate, main

__all__ = ["evaluate", "main"]

if __name__ == "__main__":
    sys.exit(main())
