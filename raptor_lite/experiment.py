"""Reproducible, simulation-only experiment runner; the interactive demo does not use it."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import random
import subprocess
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from .artifacts import write_planning_run
from .capability_registry import CapabilityRegistry
from .executor import BackendExecutor
from .house2d import House2DBackend
from .models import StrictModel
from .phase57 import resource_decision
from .planner import OfflineHouseSitterPlanner, normalized_task
from .robot_feedback import build_feedback, feedback_markdown
from .scenario import plan_scenario, verify_scenario
from .verifier import verify_task


INSTRUMENTATION_VERSION = "raptor-lite-experiment-v1"


def canonical_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class ScenarioVariant(StrictModel):
    name: str
    text: str
    weight: float = Field(default=1.0, gt=0)


class SensorExperimentConfig(StrictModel):
    noise_bound: float = Field(default=0.0, ge=0)
    dropout_probability: float = Field(default=0.0, ge=0, le=1)


class ResourceExperimentConfig(StrictModel):
    initial_battery: float | None = Field(default=None, ge=0, le=100)
    battery_per_door: float = Field(default=4.0, gt=0)
    inspection_battery_cost: float = Field(default=0.2, ge=0)
    safe_return_margin: float = Field(default=2.0, ge=0)


class ThresholdExperimentConfig(StrictModel):
    temperature_max: float = Field(default=28.0)
    humidity_max: float = Field(default=70.0)


class AblationConfig(StrictModel):
    disable_capability_grounding: bool = False
    disable_verifier: bool = False
    disable_route_optimization: bool = False
    disable_resource_policy: bool = False

    def requested(self) -> list[str]:
        return [name for name, enabled in self.model_dump().items() if enabled]


class ExperimentConfig(StrictModel):
    schema_version: Literal["raptor-lite-experiment-v1"] = INSTRUMENTATION_VERSION
    experiment_mode: bool = False
    seed: int = Field(ge=0, lt=2**31)
    task_text: str = Field(min_length=1, max_length=4096)
    scenario_distribution: list[ScenarioVariant] = Field(min_length=1)
    sensor: SensorExperimentConfig = Field(default_factory=SensorExperimentConfig)
    resource: ResourceExperimentConfig = Field(default_factory=ResourceExperimentConfig)
    thresholds: ThresholdExperimentConfig = Field(default_factory=ThresholdExperimentConfig)
    ablations: AblationConfig = Field(default_factory=AblationConfig)
    output_path: str = "artifacts/raptor_lite/experiments"

    @model_validator(mode="after")
    def _require_experiment_mode_for_ablation(self) -> "ExperimentConfig":
        if self.ablations.requested() and not self.experiment_mode:
            raise ValueError("Ablation switches require experiment_mode=true.")
        return self

    def config_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

    def select_scenario(self) -> ScenarioVariant:
        population = self.scenario_distribution
        return random.Random(self.seed).choices(population, weights=[item.weight for item in population], k=1)[0]


def load_experiment_config(path: Path) -> ExperimentConfig:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return ExperimentConfig.model_validate(document)


def _git_commit() -> str:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _effective_scenario(plan: dict[str, Any], task: Any, config: ExperimentConfig) -> dict[str, Any]:
    """Add deterministic sensor dropout events through ScenarioSpec, never the detector."""
    effective = deepcopy(plan)
    events = effective["candidate_scenario"]["events"]
    rng = random.Random(f"{config.seed}:sensor-dropout")
    existing = {(item["room"], item["event_type"]) for item in events}
    for index, room in enumerate(task.metadata.get("optimized_visit_order", []), 1):
        if rng.random() < config.sensor.dropout_probability and (room, "observation_dropout") not in existing:
            events.append({"event_id": f"experiment-dropout-{index:03d}", "room": room, "event_type": "observation_dropout", "parameters": {"checkpoint": room}, "start_stage": "scenario_start", "persistence": "until_reset", "visual_representation": {"icon": "sensor_unknown", "label": "configured observation dropout"}, "simulation_only": True})
    effective["extracted_events"] = deepcopy(events)
    return effective


class ExperimentRunner:
    """One explicit experiment path. Ablated safety gates are non-executing baselines."""

    def __init__(self, profile_path: Path):
        self.profile_path = Path(profile_path)
        self.registry = CapabilityRegistry.from_yaml(self.profile_path)
        self.planner = OfflineHouseSitterPlanner(self.registry)

    def run(self, config: ExperimentConfig) -> dict[str, Any]:
        scenario_variant = config.select_scenario()
        planning = self.planner.plan(config.task_text, optimize_route=not config.ablations.disable_route_optimization)
        task = planning.candidate_task
        scenario_plan = plan_scenario(scenario_variant.text, config.seed)
        if task is not None and scenario_plan.get("status") == "planned":
            scenario_plan = _effective_scenario(scenario_plan, task, config)
        scenario_report = verify_scenario(scenario_plan)
        verifier = verify_task(task, self.registry) if task is not None else None
        policy: dict[str, Any] | None = None
        backend: House2DBackend | None = None
        result = None
        trace: list[Any] = []
        ablated_gates = [name for name in ("capability_grounding", "verifier", "resource_policy") if getattr(config.ablations, f"disable_{name}")]
        guard = "standard_verified_execution"
        if task is not None and scenario_report["approved"]:
            backend = House2DBackend(seed=config.seed, initial_battery=config.resource.initial_battery, sensor_noise_bound=config.sensor.noise_bound, scenario={**scenario_plan["candidate_scenario"], "validation_status": "approved"}, battery_per_door=config.resource.battery_per_door, inspection_battery_cost=config.resource.inspection_battery_cost, temperature_max=config.thresholds.temperature_max, humidity_max=config.thresholds.humidity_max)
            backend.initialize(task)
            policy = resource_decision(task, {**backend.current_robot_state(), "activity": "idle"}, battery_per_door=config.resource.battery_per_door, inspection_battery_cost=config.resource.inspection_battery_cost, safety_margin=config.resource.safe_return_margin)
        if ablated_gates:
            # Safe experimental baseline: record counterfactual decisions but never bypass a safety gate.
            guard = "ablation_non_executing_baseline"
        elif task is None or verifier is None or not scenario_report["approved"] or not verifier.approved or policy is None or policy["decision"] != "APPROVE":
            guard = "admission_denied"
        else:
            assert backend is not None
            result, trace = BackendExecutor(backend).run(task, verifier, self.registry)
        bundle = backend.artifact_bundle() if backend is not None else {}
        feedback = build_feedback(task.metadata.get("optimized_visit_order", planning.extracted_rooms), trace, bundle, execution_success=bool(result and result.success)) if task is not None and result is not None else None
        output = write_planning_run(Path(config.output_path), planning, self.registry.as_json(), verifier, result, trace, backend, scenario_input=scenario_variant.text, scenario_plan=scenario_plan, scenario_report=scenario_report, robot_feedback=feedback, robot_feedback_markdown=feedback_markdown(feedback) if feedback else None, resource_policy=policy)
        execution = result.model_dump(mode="json") if result is not None else {"executed": False, "reason": guard}
        manifest = {"schema_version": INSTRUMENTATION_VERSION, "run_id": output.name, "timestamp": datetime.now(UTC).isoformat(), "git_commit": _git_commit(), "instrumentation_version": INSTRUMENTATION_VERSION, "instrumentation_version_hash": canonical_hash(INSTRUMENTATION_VERSION), "config": config.model_dump(mode="json"), "config_hash": config.config_hash(), "seed": config.seed, "inputs": {"task_text": config.task_text, "scenario_text": scenario_variant.text, "scenario_variant": scenario_variant.model_dump(mode="json")}, "task_spec": task.model_dump(mode="json") if task is not None else None, "capability_profile": self.registry.as_json(), "capability_profile_hash": canonical_hash(self.registry.as_json()), "planning": planning.model_dump(mode="json"), "scenario": {"plan": scenario_plan, "verification": scenario_report}, "verifier_decision": verifier.model_dump(mode="json") if verifier is not None else None, "resource_policy_decision": policy, "ablations": {"settings": config.ablations.model_dump(mode="json"), "requested": config.ablations.requested(), "execution_guard": guard}, "ground_truth": bundle.get("scenario_ground_truth"), "observations": bundle.get("sensor_observations", []), "route": bundle.get("route_trace", []), "detections": bundle.get("detected_anomalies", []), "twin": {"before": bundle.get("digital_twin_before"), "after": bundle.get("digital_twin_after"), "diff": bundle.get("digital_twin_updates", [])}, "final_outcome": execution, "simulation_only": True, "physical_robot_validated": False}
        reproducible = {key: manifest[key] for key in ("instrumentation_version", "config_hash", "seed", "inputs", "task_spec", "capability_profile_hash", "planning", "scenario", "verifier_decision", "resource_policy_decision", "ablations", "ground_truth", "observations", "route", "detections", "twin")}
        manifest["reproducibility_hash"] = canonical_hash(reproducible)
        (output / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"artifact_directory": str(output), "manifest": manifest}
