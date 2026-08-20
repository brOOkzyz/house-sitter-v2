from __future__ import annotations

from pathlib import Path

from raptor_lite.experiment import load_experiment_config


ROOT = Path(__file__).resolve().parents[2]


def test_pilot_entry_is_loadable_and_keeps_all_ablations_disabled():
    config = load_experiment_config(ROOT / "configs/raptor_lite/pilot_experiment.yaml")
    assert config.experiment_mode and not config.ablations.requested()
    assert config.output_path == "results/raptor_lite/pilot"
    public_boundary = (ROOT / "docs/reproducibility.md").read_text()
    assert "Pilot and intermediate generated records remain excluded" in public_boundary


def test_phase511_refreeze_declares_experiment_and_deployment_boundaries():
    report = (ROOT / "README.md").read_text()
    assert "House2D" in report and "experimental backend" in report
    assert "Create3ROS2Backend" in report and "integration path" in report
    assert "Physical robot validation was not performed" in report
