"""RaPToR-Lite: simulation-first verified robot task primitives."""

from .capability_registry import CapabilityRegistry
from .executor import MockExecutor
from .models import TaskSpec
from .verifier import verify_task

__all__ = ("CapabilityRegistry", "MockExecutor", "TaskSpec", "verify_task")
