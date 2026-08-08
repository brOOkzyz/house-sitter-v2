"""Static capability-profile loading; Phase 1 never introspects ROS."""
from __future__ import annotations

from pathlib import Path

import yaml

from .models import CapabilitySpec


class CapabilityRegistry:
    def __init__(self, capabilities: list[CapabilitySpec], available_capabilities: list[str] | None = None):
        self.capabilities = {item.name: item for item in capabilities}
        self.available_capabilities = set(available_capabilities or self.capabilities)

    @classmethod
    def from_yaml(cls, path: Path) -> "CapabilityRegistry":
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls([CapabilitySpec.model_validate(item) for item in document.get("capabilities", [])], document.get("available_capabilities"))

    def get(self, name: str) -> CapabilitySpec | None:
        return self.capabilities.get(name)

    def as_json(self) -> dict[str, object]:
        return {"capabilities": [item.model_dump(mode="json") for item in self.capabilities.values()], "available_capabilities": sorted(self.available_capabilities)}

    def explore(self, query: str = "") -> dict[str, object]:
        """Describe this profile from its declared capabilities, never UI constants."""
        terms = {word for word in query.casefold().replace("_", " ").split() if word}
        entries = []
        for capability in self.capabilities.values():
            text = " ".join((capability.name, capability.description, *capability.safety_constraints)).replace("_", " ").casefold()
            if terms and not terms <= set(text.split()) and not terms & set(text.split()):
                continue
            entries.append({"name": capability.name, "description": capability.description, "parameters": [item.model_dump(mode="json") for item in capability.parameters], "required_capabilities": capability.required_capabilities, "safety_constraints": capability.safety_constraints, "simulation_supported": capability.simulation_supported, "physical_robot_supported": capability.physical_robot_supported, "execution_adapter": capability.execution_adapter})
        limitations = sorted({constraint for item in self.capabilities.values() for constraint in item.safety_constraints})
        natural = [f"Can {item['name'].replace('_', ' ')}: {item['description']}" for item in entries]
        if any(not item.physical_robot_supported for item in self.capabilities.values()):
            natural.append("Cannot control a physical robot: this profile is simulation-only.")
        return {"query": query, "available_capabilities": sorted(self.available_capabilities), "capabilities": entries, "limitations": limitations, "natural_language": natural}
