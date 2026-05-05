#!/usr/bin/env python3
"""Compatibility CLI for the modular Verilog-Evolve runner.

The implementation is split across:

- ``runner.py``: orchestration and CLI
- ``versioning.py``: records, scoring adapters, and promotion gates
- ``planning.py``: DR_RTL-style diversity plans
- ``generation.py``: prompt calls and skill retrieval
- ``history.py``: artifacts, histories, and evidence files
"""

from __future__ import annotations

from runner import main


if __name__ == "__main__":
    main()
