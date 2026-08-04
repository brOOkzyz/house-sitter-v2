#!/usr/bin/env python3
"""Preview a deterministic simulation skill plan without executing steps."""

from __future__ import annotations

import sys

from run_simulation_skill import main


if __name__ == "__main__":
    arguments = list(sys.argv[1:])
    if "--preview-only" not in arguments:
        arguments.append("--preview-only")
    raise SystemExit(main(arguments))
