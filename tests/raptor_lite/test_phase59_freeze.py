from __future__ import annotations

from pathlib import Path

from raptor_lite.experiment import load_experiment_config


ROOT = Path(__file__).resolve().parents[2]


def test_pilot_entry_is_loadable_and_keeps_all_ablations_disabled():
    config = load_experiment_config(ROOT / "configs/raptor_lite/pilot_experiment.yaml")
    assert config.experiment_mode and not config.ablations.requested()
    assert config.output_path == "results/raptor_lite/pilot"
    assert (ROOT / config.output_path / ".gitkeep").is_file()
    assert "No pilot or Phase 6 result was run" in (ROOT / "docs/raptor_lite/phase59_pre_experiment_freeze.md").read_text()
