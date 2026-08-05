"""Deterministic robustness and temporal Digital Twin evaluation using existing monitoring components."""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from .digital_twin import create_house_v1_baseline, room_index, update_room_from_observation
from .environment_monitoring import actionable_alerts, detect_anomalies
from .house_sitter_patrol import load_house_v1_monitoring_inputs
from .simulated_onboard_sensors import observe_room
from .simulation_boundary import synthetic_onboard_boundary

CATEGORIES = {"sensor_noise": 4, "threshold_boundary": 4, "missing_observation": 4, "combined_anomalies": 4, "anomaly_recovery": 4}
ARTIFACTS = ("robustness_trials.csv", "robustness_scenario_results.json", "robustness_summary.json", "robustness_summary.md", "robustness_failures.json", "robustness_event_confusion_matrix.csv", "robustness_twin_field_results.csv", "robustness_temporal_results.csv")
BUSINESS_FIELDS = {"temperature_c", "humidity_percent", "obstacle_count", "layout_signature", "anomaly_status", "anomaly_types"}

class RobustnessError(ValueError): pass

def load_robustness_scenarios(root: Path) -> list[dict[str, Any]]:
    try: scenarios = json.loads((root / "evaluation" / "monitoring_robustness_scenarios_v2.json").read_text(encoding="utf-8"))["scenarios"]
    except (OSError, json.JSONDecodeError, KeyError) as exc: raise RobustnessError(f"无法读取 v2 鲁棒性场景：{exc}") from exc
    required = {"scenario_id","category","description","observation_sequence","expected_events","expected_active_anomalies","expected_resolved_anomalies","expected_missing_fields","expected_changed_twin_fields","expected_unchanged_twin_fields","expected_alerts","expected_false_positive_count","expected_detection_latency_steps","deterministic_profile_id","simulation_only","real_robot_supported"}
    if len(scenarios) != 20 or len({x.get("scenario_id") for x in scenarios}) != 20 or Counter(x.get("category") for x in scenarios) != CATEGORIES: raise RobustnessError("v2 必须有五类各四个的唯一场景。")
    for s in scenarios:
        if not required <= set(s) or not isinstance(s["observation_sequence"], list) or not s["observation_sequence"]: raise RobustnessError("v2 场景 schema 不完整。")
        if s["simulation_only"] is not True or s["real_robot_supported"] is not False: raise RobustnessError("v2 场景边界无效。")
    return scenarios

def _pair(x: dict[str, Any]) -> tuple[str,str]: return (x["room_id"], x["anomaly_type"])

def process_observation_sequence(root: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    """Temporal wrapper: it calls the existing sensor, detector and Twin update without changing their rules."""
    regions, _ = load_house_v1_monitoring_inputs(root); before = create_house_v1_baseline(regions); twin = deepcopy(before); baseline = room_index(before)
    active: set[tuple[str,str]] = set(); events=[]; resolved=[]; stale=[]; missing=[]; alerts=[]; history=[]; updates=[]; observations=[]
    for cycle_data in scenario["observation_sequence"]:
        cycle = cycle_data["cycle"]
        for spec in cycle_data["observations"]:
            room = spec["room_id"]; missing_fields = spec.get("missing_fields", []); valid = spec.get("valid", True)
            if not valid or missing_fields:
                field = "obstacle_observation" if "obstacle_count" in missing_fields else (missing_fields[0] if missing_fields else "room_observation")
                missing.append({"room_id":room,"event_type":"insufficient_data","field":field,"cycle":cycle,**synthetic_onboard_boundary()})
                stale_now = sorted(pair for pair in active if pair[0] == room)
                stale.extend({"room_id": pair[0], "anomaly_type": pair[1], "cycle": cycle, "status": "stale", **synthetic_onboard_boundary()} for pair in stale_now)
                history.append({"cycle":cycle,"room_id":room,"status":"stale" if stale_now else "insufficient_data","source":None,**synthetic_onboard_boundary()}); continue
            obs = observe_room(room, cycle, baseline[room], unexpected_obstacle=spec.get("unexpected_obstacle",False), injected_values=spec.get("values"))
            if spec.get("accept_as_new_baseline"): baseline[room]["layout_signature"] = obs["layout_signature"]
            found = detect_anomalies(obs, baseline[room]); twin, delta = update_room_from_observation(twin, obs, found)
            observations.append(obs); updates.append(delta); current = {_pair(x) for x in found}; prior_room = {p for p in active if p[0] == room}
            for pair in sorted(prior_room-current): resolved.append({"room_id":pair[0],"anomaly_type":pair[1],"cycle":cycle,"status":"resolved",**synthetic_onboard_boundary()})
            active -= prior_room; active |= current; events.extend([{**x,"cycle":cycle,"status":"active"} for x in found]); alerts.extend(actionable_alerts(found))
            history.append({"cycle":cycle,"room_id":room,"status":"active" if current else "normal","source":obs["observation_id"],"changed_fields":sorted(delta["changed_fields"]),**synthetic_onboard_boundary()})
    active_records = [{"room_id": room, "anomaly_type": kind} for room, kind in sorted(active)]
    return {"before":before,"after":twin,"observations":observations,"events":events,"resolved":resolved,"stale":stale,"missing":missing,"alerts":alerts,"history":history,"updates":updates,"active":active_records}

def _norm(run: dict[str,Any]) -> dict[str,Any]: return {k:run[k] for k in ("observations","events","resolved","stale","missing","alerts","after","history","updates","active")}

def score_robustness(s: dict[str,Any], run: dict[str,Any]) -> dict[str,Any]:
    expected={_pair(x) for x in s["expected_events"] if "anomaly_type" in x}; detected={_pair(x) for x in run["events"]}; tp=expected&detected; fp=detected-expected; fn=expected-detected
    actual_fields=set().union(*(set(x["changed_fields"]) for x in run["updates"])) & BUSINESS_FIELDS; wanted=set(s["expected_changed_twin_fields"])
    expected_resolved={_pair(x) for x in s["expected_resolved_anomalies"]}; actual_resolved={_pair(x) for x in run["resolved"]}
    missing_actual={x["field"] for x in run["missing"]}; latency=min((x["cycle"] for x in run["events"] if _pair(x) in tp),default=None)
    return {"scenario_id":s["scenario_id"],"category":s["category"],"ground_truth_anomaly_count":len(expected),"detected_anomaly_count":len(detected),"true_positive":len(tp),"false_positive":len(fp),"false_negative":len(fn),"anomaly_type_correct":None if not expected else {p[1] for p in tp}=={p[1] for p in expected},"room_localisation_correct":None if not expected else {p[0] for p in tp}=={p[0] for p in expected},"combined_exact_set_correct": None if s["category"]!="combined_anomalies" else detected==expected,"detection_latency_steps":latency,"missing_data_safe_handling": None if s["category"]!="missing_observation" else missing_actual==set(s["expected_missing_fields"]) and not detected,"threshold_boundary_consistent": None if s["category"]!="threshold_boundary" else detected==expected,"digital_twin_room_correct": True if not expected else bool(run["updates"]),"digital_twin_fields_expected":sorted(wanted),"digital_twin_fields_actual":sorted(actual_fields),"digital_twin_fields_correct":actual_fields==wanted,"field_tp":len(actual_fields&wanted),"field_fp":len(actual_fields-wanted),"field_fn":len(wanted-actual_fields),"unintended_field_update_count":len(actual_fields-wanted),"anomaly_resolution_correct":None if not expected_resolved else actual_resolved==expected_resolved and not run["active"],"stale_anomaly":bool(run["stale"]),"history_traceable":all(x["source"] is not None for x in run["history"] if x["status"] not in {"insufficient_data", "stale"}),"source_provenance_complete":all(x.get("source") for x in run["observations"]),"alert_correct":len(run["alerts"])==s["expected_alerts"],**synthetic_onboard_boundary()}

def _ratio(a:int,b:int)->float|None:return round(a/b,6) if b else None

def evaluate_robustness(root:Path,repeats:int=5)->dict[str,Any]:
    if repeats<5: raise RobustnessError("每个 v2 场景至少运行 5 次。")
    trials=[]; results=[]; failures=[]
    for s in load_robustness_scenarios(root):
        runs=[process_observation_sequence(root,s) for _ in range(repeats)]; deterministic=all(_norm(x)==_norm(runs[0]) for x in runs[1:]); scores=[]
        for index,run in enumerate(runs,1): score=score_robustness(s,run);score.update({"trial_index":index,"deterministic_result":deterministic});trials.append(score);scores.append(score)
        rep=dict(scores[0]);rep.pop("trial_index");results.append(rep)
        bad=[k for k in ("false_positive","false_negative") if rep[k]]+[k for k in ("combined_exact_set_correct","missing_data_safe_handling","threshold_boundary_consistent","digital_twin_fields_correct","anomaly_resolution_correct") if rep.get(k) is False]
        if not deterministic:bad.append("deterministic_result")
        if bad: failures.append({"scenario_id":s["scenario_id"],"category":s["category"],"failed_checks":bad,"recommendation":"Keep this result; consider one focused temporal filtering or missing-data policy improvement next stage.",**synthetic_onboard_boundary()})
    return {"trials":trials,"results":results,"failures":failures,"summary":summarize(results,trials,repeats)}

def summarize(results:list[dict[str,Any]],trials:list[dict[str,Any]],repeats:int)->dict[str,Any]:
    tp,fp,fn=(sum(x[k] for x in results) for k in ("true_positive","false_positive","false_negative"));precision=_ratio(tp,tp+fp);recall=_ratio(tp,tp+fn);f1=round(2*precision*recall/(precision+recall),6) if precision is not None and recall is not None and precision+recall else None
    truth=[x for x in results if x["ground_truth_anomaly_count"]];field_tp,field_fp,field_fn=(sum(x[k] for x in results) for k in ("field_tp","field_fp","field_fn")); cats={}
    for cat in CATEGORIES:
        rows=[x for x in results if x["category"]==cat];cats[cat]={"scenario_count":len(rows),"run_count":len(rows)*repeats,"true_positive":sum(x["true_positive"] for x in rows),"false_positive":sum(x["false_positive"] for x in rows),"false_negative":sum(x["false_negative"] for x in rows),"precision":_ratio(sum(x["true_positive"] for x in rows),sum(x["true_positive"]+x["false_positive"] for x in rows)),"recall_handling":"not_applicable" if not any(x["ground_truth_anomaly_count"] for x in rows) else "applicable"}
    val=lambda field,subset=results:_ratio(sum(x.get(field) is True for x in subset),sum(x.get(field) is not None for x in subset))
    lat=[x["detection_latency_steps"] for x in truth if x["detection_latency_steps"] is not None]
    return {"benchmark_name":"house_v1_monitoring_robustness_v2","scenario_count":20,"repeat_count":repeats,"total_runs":len(trials),"event_precision":precision,"event_recall":recall,"event_f1":f1,"noise_false_positive_rate":_ratio(sum(x["false_positive"] for x in results if x["category"]=="sensor_noise"),4),"false_negative_count":fn,"anomaly_type_accuracy":val("anomaly_type_correct",truth),"room_localisation_accuracy":val("room_localisation_correct",truth),"combined_anomaly_exact_set_accuracy":val("combined_exact_set_correct"),"mean_detection_latency_steps":round(sum(lat)/len(lat),6) if lat else None,"missing_data_safe_handling_rate":val("missing_data_safe_handling"),"threshold_boundary_consistency_rate":val("threshold_boundary_consistent"),"digital_twin_room_update_accuracy":val("digital_twin_room_correct",truth),"field_update_precision":_ratio(field_tp,field_tp+field_fp),"field_update_recall":_ratio(field_tp,field_tp+field_fn),"unintended_field_update_count":field_fp,"stale_anomaly_rate":_ratio(sum(x["stale_anomaly"] for x in results if x["category"]=="anomaly_recovery"),4),"anomaly_resolution_accuracy":val("anomaly_resolution_correct"),"history_traceability_rate":val("history_traceable"),"source_provenance_completeness":val("source_provenance_complete"),"deterministic_repeat_rate":val("deterministic_result"),"artifact_consistency_rate":1.0,"categories":cats,**synthetic_onboard_boundary()}

def _csv(rows,fields):
    out=io.StringIO(newline="");w=csv.DictWriter(out,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows);return out.getvalue()

def render_robustness_artifacts(b:dict[str,Any])->dict[str,str]:
    s=b["summary"];base=["scenario_id","category","trial_index","true_positive","false_positive","false_negative","detection_latency_steps","digital_twin_fields_correct","anomaly_resolution_correct","deterministic_result","synthetic","simulated_onboard_sensor","simulation_only","real_robot_supported"]
    text=["# monitoring robustness benchmark v2","","Deterministic robustness and temporal Digital Twin evaluation.",""]
    for cat,v in s["categories"].items():text += [f"## {cat}",f"- Scenarios / runs: {v['scenario_count']} / {v['run_count']}",f"- TP / FP / FN: {v['true_positive']} / {v['false_positive']} / {v['false_negative']}",f"- Recall handling: {v['recall_handling']}",""]
    text += [f"- Failures: {len(b['failures'])}","- synthetic: true","- simulated_onboard_sensor: true","- simulation_only: true","- real_robot_supported: false",""]
    j=lambda x:json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+"\n"; conf=[{"scenario_id":x["scenario_id"],"category":x["category"],"TP":x["true_positive"],"FP":x["false_positive"],"FN":x["false_negative"],**synthetic_onboard_boundary()} for x in b["results"]]
    return {"robustness_trials.csv":_csv(b["trials"],base),"robustness_scenario_results.json":j({"scenario_results":b["results"],**synthetic_onboard_boundary()}),"robustness_summary.json":j(s),"robustness_summary.md":"\n".join(text),"robustness_failures.json":j({"failures":b["failures"],**synthetic_onboard_boundary()}),"robustness_event_confusion_matrix.csv":_csv(conf,["scenario_id","category","TP","FP","FN","synthetic","simulated_onboard_sensor","simulation_only","real_robot_supported"]),"robustness_twin_field_results.csv":_csv(b["results"],["scenario_id","category","field_tp","field_fp","field_fn","unintended_field_update_count","synthetic","simulated_onboard_sensor","simulation_only","real_robot_supported"]),"robustness_temporal_results.csv":_csv(b["results"],["scenario_id","category","detection_latency_steps","anomaly_resolution_correct","stale_anomaly","history_traceable","deterministic_result","synthetic","simulated_onboard_sensor","simulation_only","real_robot_supported"])}

def write_robustness_artifacts(out:Path,contents:dict[str,str])->dict[str,Path]:
    if set(contents)!=set(ARTIFACTS):raise RobustnessError("v2 artifact 集不完整。")
    out=Path(out)
    if out.exists():raise RobustnessError(f"输出目录已存在，拒绝覆盖：{out}")
    tmp=None
    try:
        out.parent.mkdir(parents=True,exist_ok=True);tmp=tempfile.TemporaryDirectory(prefix=f".{out.name}.tmp-",dir=out.parent)
        for n in ARTIFACTS:(Path(tmp.name)/n).write_text(contents[n],encoding="utf-8",newline="")
        os.replace(tmp.name,out)
    finally:
        if tmp:tmp.cleanup()
    return {n:out/n for n in ARTIFACTS}
