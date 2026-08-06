"""Task JSON parsing boundary."""
from __future__ import annotations

import json
from pathlib import Path

from .models import TaskSpec


def load_task(path: Path) -> TaskSpec:
    return TaskSpec.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
