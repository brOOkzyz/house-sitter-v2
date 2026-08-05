"""One shared, strict boundary marker for synthetic onboard-monitoring records."""
from __future__ import annotations


def synthetic_onboard_boundary() -> dict[str, bool]:
    """Return fresh JSON booleans so every derived record has the same boundary."""
    return {
        "synthetic": True,
        "simulated_onboard_sensor": True,
        "simulation_only": True,
        "real_robot_supported": False,
    }
