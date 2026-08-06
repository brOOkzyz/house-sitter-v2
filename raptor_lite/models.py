"""Strict JSON models for the small RaPToR-Lite task core."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ParameterSpec(StrictModel):
    name: str
    type: str
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    allowed_values: list[Any] | None = None
    default: Any | None = None
    unit: str | None = None


class CapabilitySpec(StrictModel):
    name: str
    description: str
    parameters: list[ParameterSpec] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    timeout_seconds: float
    safety_constraints: list[str] = Field(default_factory=list)
    execution_adapter: str
    simulation_supported: bool
    physical_robot_supported: bool


class TaskStep(StrictModel):
    step_id: str
    skill: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = None
    on_failure: str = "abort"


class TaskSpec(StrictModel):
    task_id: str
    name: str
    description: str
    robot_profile: str
    steps: list[TaskStep]
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationIssue(StrictModel):
    issue_code: str
    severity: str
    step_id: str | None = None
    field: str | None = None
    message: str
    suggested_fix: str


class VerificationReport(StrictModel):
    approved: bool
    issues: list[VerificationIssue] = Field(default_factory=list)
    resolved_capabilities: list[str] = Field(default_factory=list)
    safety_summary: list[str] = Field(default_factory=list)


class PlanningResult(StrictModel):
    original_text: str
    normalized_text: str
    detected_intent: str | None = None
    extracted_rooms: list[str] = Field(default_factory=list)
    extracted_checks: list[str] = Field(default_factory=list)
    candidate_task: TaskSpec | None = None
    confidence: float = 0.0
    match_basis: list[str] = Field(default_factory=list)
    automatically_added_steps: list[str] = Field(default_factory=list)
    automatic_addition_reasons: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    unsupported_elements: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    status: Literal["planned", "needs_clarification", "unsupported", "invalid"]


class ExecutionStepResult(StrictModel):
    step_id: str
    skill: str
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None


class ExecutionResult(StrictModel):
    success: bool
    step_results: list[ExecutionStepResult] = Field(default_factory=list)
    first_failure: str | None = None
    start_timestamp: str | None = None
    end_timestamp: str | None = None


class ExecutionTrace(StrictModel):
    timestamp: str
    event: str
    step_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
