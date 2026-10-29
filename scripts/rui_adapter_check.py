#!/usr/bin/env python
"""Console entry point for the device-adapter conformance kit (thin wrapper).

The implementation lives in :mod:`rui.device.adapter_check` so it can be imported by the
tests; this file is the ``python scripts/rui_adapter_check.py ...`` form for a checkout
that has not been installed yet. ``pip install -e .`` also installs the same command as
``rui-adapter-check`` from ``[project.scripts]``.

    python scripts/rui_adapter_check.py --adapter ../rui-plugin-template
"""
from rui.device.adapter_check import main

if __name__ == "__main__":
    raise SystemExit(main())
