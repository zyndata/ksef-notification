"""The pure decision core (phase 4): no I/O, no Home Assistant imports, no clock reads.

`now` is always a parameter. tests/test_purity.py enforces it.
"""

from __future__ import annotations
