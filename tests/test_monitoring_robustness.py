"""Contract tests for monitoring robustness benchmark v2."""
from __future__ import annotations
import csv
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from house_sitter_core.house_sitter_patrol import run_house_sitter_patrol
from house_sitter_core.monitoring_robustness_evaluation import ARTIFACTS, evaluate_robustness, load_robustness_scenarios, process_observation_sequence, render_robustness_artifacts, write_robustness_artifacts
ROOT=Path(__file__).resolve().parents[1]
def test_twenty_unique_scenarios_have_five_categories_and_temporal_schema():
    ss=load_robustness_scenarios(ROOT);assert len(ss)==20 and len({s['scenario_id'] for s in ss})==20
    assert {s['category'] for s in ss}=={'sensor_noise','threshold_boundary','missing_observation','combined_anomalies','anomaly_recovery'}
    assert all(s['observation_sequence'] and s['deterministic_profile_id'] for s in ss)
    assert all(len(s['observation_sequence'])>=3 for s in ss if s['category']=='anomaly_recovery')
def test_fixed_profiles_repeat_and_v1_kitchen_result_is_unchanged():
    s=next(x for x in load_robustness_scenarios(ROOT) if x['scenario_id']=='combined_temperature_humidity');assert process_observation_sequence(ROOT,s)==process_observation_sequence(ROOT,s)
    v1=run_house_sitter_patrol(ROOT,'kitchen_unexpected_obstacle');assert [(x['room_id'],x['anomaly_type']) for x in v1['anomalies']]==[('kitchen','unexpected_obstacle')]
def test_missing_data_is_insufficient_not_normal_and_does_not_clear_anomaly_without_observation():
    s=next(x for x in load_robustness_scenarios(ROOT) if x['scenario_id']=='missing_temperature');r=process_observation_sequence(ROOT,s)
    assert r['missing'][0]['field']=='temperature_c' and not r['events'] and r['after']==r['before']
def test_threshold_and_combined_sets_and_recovery_states_are_scored():
    b=evaluate_robustness(ROOT,5);rows={x['scenario_id']:x for x in b['results']}
    assert rows['boundary_temperature_upper_exact']['detected_anomaly_count']==0 and rows['boundary_temperature_upper_exceeded']['true_positive']==1
    assert rows['combined_obstacle_layout']['combined_exact_set_correct'] is True
    assert rows['recovery_temperature']['anomaly_resolution_correct'] is True and rows['recovery_temperature']['stale_anomaly'] is False
    stale=deepcopy(next(x for x in load_robustness_scenarios(ROOT) if x['scenario_id']=='recovery_temperature'))
    stale['observation_sequence'][2]['observations'][0]={'room_id':'kitchen','missing_fields':['temperature_c']}
    temporal=process_observation_sequence(ROOT,stale)
    assert temporal['stale'][0]['status']=='stale' and temporal['active']
def test_metrics_preserve_the_real_noise_failure_and_not_applicable_handling():
    s=evaluate_robustness(ROOT,5)['summary'];assert s['noise_false_positive_rate']==0.25 and s['event_recall']==1.0
    assert s['categories']['sensor_noise']['recall_handling']=='not_applicable' and s['field_update_precision']==0.925 and s['field_update_recall']==1.0
def test_artifacts_are_complete_and_consistent():
    b=evaluate_robustness(ROOT,5)
    with tempfile.TemporaryDirectory() as d:
        p=write_robustness_artifacts(Path(d)/'out',render_robustness_artifacts(b));assert set(p)==set(ARTIFACTS)
        assert len(list(csv.DictReader(p['robustness_trials.csv'].open())))==100
        assert json.loads(p['robustness_summary.json'].read_text())['total_runs']==100
        assert len(json.loads(p['robustness_failures.json'].read_text())['failures'])==1
def test_v2_does_not_call_ros_gazebo_network_or_llm():
    text='\n'.join((ROOT/x).read_text() for x in ('house_sitter_core/monitoring_robustness_evaluation.py','scripts/evaluate_monitoring_robustness.py')).casefold()
    assert all(x not in text for x in ('import subprocess','ros2','gazebo','nav2','import requests','import urllib','openai','import llm'))
