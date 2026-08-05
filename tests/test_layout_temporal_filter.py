from pathlib import Path
from house_sitter_core.layout_change_filter import TwoObservationLayoutFilter
from house_sitter_core.temporal_filter_comparison import compare,process_with_filter,render,write,ARTIFACTS
from house_sitter_core.monitoring_robustness_evaluation import load_robustness_scenarios,process_observation_sequence
import tempfile,json
ROOT=Path(__file__).resolve().parents[1]
def test_candidates_confirm_clear_and_do_not_cross_rooms():
 f=TwoObservationLayoutFilter();assert not f.observe('a','x','b',1)['confirmed'];assert f.observe('a','x','b',2)['confirmed'];assert f.observe('a','b','b',3)['event']=='candidate_cleared';assert not f.observe('a','x','b',4)['confirmed'];assert not f.observe('b','x','b',5)['confirmed']
def test_transient_noise_is_suppressed_but_persistent_layout_is_confirmed():
 ss={x['scenario_id']:x for x in load_robustness_scenarios(ROOT)};raw=process_observation_sequence(ROOT,ss['noise_layout_transient']);filtered=process_with_filter(ROOT,ss['noise_layout_transient']);assert raw['events'] and not filtered['events'];assert not any('layout_signature' in x['changed_fields'] for x in filtered['updates'])
 persistent=process_with_filter(ROOT,ss['recovery_layout_new_baseline']);assert [(x['room_id'],x['anomaly_type']) for x in persistent['events']]==[('bedroom','layout_change')];assert persistent['events'][0]['cycle']==3
def test_other_combined_anomalies_and_recovery_remain_available():
 ss={x['scenario_id']:x for x in load_robustness_scenarios(ROOT)};r=process_with_filter(ROOT,ss['combined_temperature_humidity']);assert {x['anomaly_type'] for x in r['events']}=={'temperature_out_of_range','humidity_out_of_range'}
 assert process_with_filter(ROOT,ss['recovery_temperature'])['resolved']
def test_paired_comparison_uses_same_observations_and_writes_artifacts():
 c=compare(ROOT,5);assert len(c['trials'])==200;assert c['summary']['two_observation_confirmation']['noise_false_positive_rate']==0.0
 with tempfile.TemporaryDirectory() as d:p=write(Path(d)/'out',render(c));assert set(p)==set(ARTIFACTS);assert len(json.loads(p['temporal_filter_paired_results.json'].read_text())['paired_results'])==20
def test_no_ros_network_or_llm_calls():
 text='\n'.join((ROOT/x).read_text() for x in ('house_sitter_core/layout_change_filter.py','house_sitter_core/temporal_filter_comparison.py','scripts/compare_layout_temporal_filter.py')).casefold();assert all(x not in text for x in ('import subprocess','ros2','gazebo','nav2','import requests','import urllib','openai','import llm'))
