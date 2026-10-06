"""KsefCoordinator, the DataUpdateCoordinator that runs one check per cycle (phase 6).

Wires client → core → storage → notifier; see docs/ARCHITECTURE.md § Data flow.
"""

from __future__ import annotations
