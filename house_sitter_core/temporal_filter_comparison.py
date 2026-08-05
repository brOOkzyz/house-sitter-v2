"""Paired pre-filtering versus two-observation layout-confirmation evaluation."""
from __future__ import annotations
import csv,json,os,tempfile,io
from copy import deepcopy
from pathlib import Path
from typing import Any
from .digital_twin import create_house_v1_baseline,room_index,update_room_from_observation
from .environment_monitoring import actionable_alerts,detect_anomalies
from .house_sitter_patrol import load_house_v1_monitoring_inputs
from .layout_change_filter import TwoObservationLayoutFilter
from .monitoring_robustness_evaluation import load_robustness_scenarios,process_observation_sequence,score_robustness,summarize
from .simulated_onboard_sensors import observe_room
from .simulation_boundary import synthetic_onboard_boundary
ARTIFACTS=("temporal_filter_trials.csv","temporal_filter_paired_results.json","temporal_filter_summary.json","temporal_filter_summary.md","temporal_filter_failures.json","temporal_filter_metric_comparison.csv")
class TemporalFilterError(ValueError):pass
def process_with_filter(root:Path,s:dict[str,Any])->dict[str,Any]:
    regions,_=load_house_v1_monitoring_inputs(root);before=create_house_v1_baseline(regions);twin=deepcopy(before);base=room_index(before);filt=TwoObservationLayoutFilter();active=set();events=[];resolved=[];stale=[];missing=[];alerts=[];history=[];updates=[];observations=[];audit=[]
    for cd in s['observation_sequence']:
        cycle=cd['cycle']
        for spec in cd['observations']:
            room=spec['room_id'];miss=spec.get('missing_fields',[])
            if not spec.get('valid',True) or miss:
                field='obstacle_observation' if 'obstacle_count' in miss else (miss[0] if miss else 'room_observation');missing.append({'room_id':room,'event_type':'insufficient_data','field':field,'cycle':cycle,**synthetic_onboard_boundary()});stale_now=[p for p in active if p[0]==room];stale += [{'room_id':a,'anomaly_type':b,'cycle':cycle,'status':'stale',**synthetic_onboard_boundary()} for a,b in stale_now];history.append({'cycle':cycle,'room_id':room,'status':'stale' if stale_now else 'insufficient_data','source':None,**synthetic_onboard_boundary()});continue
            raw=observe_room(room,cycle,base[room],unexpected_obstacle=spec.get('unexpected_obstacle',False),injected_values=spec.get('values'));observations.append(raw);decision=filt.observe(room,raw['layout_signature'],base[room]['layout_signature'],cycle);audit.append({'room_id':room,'cycle':cycle,**decision,**synthetic_onboard_boundary()});candidate=deepcopy(raw)
            if not decision['confirmed']: candidate['layout_signature']=base[room]['layout_signature']
            found=detect_anomalies(candidate,base[room]);twin,delta=update_room_from_observation(twin,candidate,found);updates.append(delta);current={(x['room_id'],x['anomaly_type']) for x in found};prior={p for p in active if p[0]==room};resolved += [{'room_id':a,'anomaly_type':b,'cycle':cycle,'status':'resolved',**synthetic_onboard_boundary()} for a,b in sorted(prior-current)];active-=prior;active|=current;events += [{**x,'cycle':cycle,'status':'active'} for x in found];alerts+=actionable_alerts(found);history.append({'cycle':cycle,'room_id':room,'status':'active' if current else 'normal','source':raw['observation_id'],'changed_fields':sorted(delta['changed_fields']),**synthetic_onboard_boundary()})
            if spec.get('accept_as_new_baseline') and decision['confirmed']:base[room]['layout_signature']=raw['layout_signature']
    return {'before':before,'after':twin,'observations':observations,'events':events,'resolved':resolved,'stale':stale,'missing':missing,'alerts':alerts,'history':history,'updates':updates,'active':[{'room_id':a,'anomaly_type':b} for a,b in sorted(active)],'audit_events':audit}
def _metrics(results,trials,repeats):
    s=summarize(results,trials,repeats);layout=[x for x in results if any(e.get('anomaly_type')=='layout_change' for e in next(z for z in load_robustness_scenarios(Path.cwd()) if z['scenario_id']==x['scenario_id'])['expected_events'])];tp=sum(x['true_positive'] for x in layout);fp=sum(x['false_positive'] for x in layout);fn=sum(x['false_negative'] for x in layout);ratio=lambda a,b:round(a/b,6) if b else None;s.update({'layout_change_precision':ratio(tp,tp+fp),'layout_change_recall':ratio(tp,tp+fn),'mean_layout_detection_latency':round(sum(x['detection_latency_steps'] for x in layout if x['detection_latency_steps'] is not None)/len([x for x in layout if x['detection_latency_steps'] is not None]),6) if any(x['detection_latency_steps'] is not None for x in layout) else None});return s
def compare(root:Path,repeats:int=5)->dict:
    if repeats<5:raise TemporalFilterError('每种策略至少 5 次。')
    trials=[];paired=[];failures=[]
    for s in load_robustness_scenarios(root):
        norms={'none':[],'two_observation_confirmation':[]};scores={'none':[],'two_observation_confirmation':[]}
        for i in range(1,repeats+1):
            runs={'none':process_observation_sequence(root,s),'two_observation_confirmation':process_with_filter(root,s)}
            if runs['none']['observations']!=runs['two_observation_confirmation']['observations']:raise TemporalFilterError('配对策略观测序列不一致。')
            for policy,run in runs.items():
                score=score_robustness(s,run);score.update({'repeat_index':i,'policy':policy,'observation_profile_id':s['deterministic_profile_id'],'detected_events':[(e['room_id'],e['anomaly_type']) for e in run['events']],'digital_twin_changes':run['updates']});trials.append(score);scores[policy].append(score);norms[policy].append({k:run[k] for k in ('observations','events','resolved','stale','alerts','after','updates')})
        item={'scenario_id':s['scenario_id'],'category':s['category'],**synthetic_onboard_boundary()}
        for p in scores:
            det=all(x==norms[p][0] for x in norms[p][1:]);scores[p][0]['deterministic_result']=det;item[p]={k:v for k,v in scores[p][0].items() if k not in {'repeat_index','policy','digital_twin_changes'}};item[p]['deterministic_result']=det
        paired.append(item)
    for p in ('none','two_observation_confirmation'):
        res=[x[p] for x in paired];tr=[x for x in trials if x['policy']==p]
        for x in res:x['deterministic_result']=x.get('deterministic_result',True)
    summaries={p:_metrics([x[p] for x in paired],[x for x in trials if x['policy']==p],repeats) for p in ('none','two_observation_confirmation')}
    for x in paired:
        filtered=x['two_observation_confirmation']; bad=[key for key in ('false_negative','false_positive') if filtered[key]]
        bad += [key for key in ('combined_exact_set_correct','anomaly_resolution_correct','digital_twin_fields_correct') if filtered.get(key) is False]
        if bad:failures.append({'scenario_id':x['scenario_id'],'failed_checks':bad,'pre_filtering':x['none']['detected_events'],'filtered':filtered['detected_events'],**synthetic_onboard_boundary()})
    a,b=summaries['none'],summaries['two_observation_confirmation'];delta={k:round((b[k] or 0)-(a[k] or 0),6) for k in ('event_precision','event_recall','noise_false_positive_rate','field_update_precision','field_update_recall','mean_layout_detection_latency','unintended_field_update_count')};return {'trials':trials,'paired':paired,'summary':{'pre_filtering':a,'two_observation_confirmation':b,'deltas':delta,**synthetic_onboard_boundary()},'failures':failures}
def _csv(rows,fields):out=io.StringIO(newline='');w=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows);return out.getvalue()
def render(c):
    j=lambda x:json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+'\n';s=c['summary'];rows=[{'policy':p,**v} for p,v in s.items() if isinstance(v,dict) and p!='deltas'];text='\n'.join(['# temporal filter comparison','',f"- pre-filtering Precision/Recall/F1: {s['pre_filtering']['event_precision']} / {s['pre_filtering']['event_recall']} / {s['pre_filtering']['event_f1']}",f"- filtered Precision/Recall/F1: {s['two_observation_confirmation']['event_precision']} / {s['two_observation_confirmation']['event_recall']} / {s['two_observation_confirmation']['event_f1']}",f"- deltas: {s['deltas']}",''])
    return {'temporal_filter_trials.csv':_csv(c['trials'],['scenario_id','repeat_index','policy','observation_profile_id','true_positive','false_positive','false_negative','detected_events','digital_twin_changes','synthetic','simulated_onboard_sensor','simulation_only','real_robot_supported']),'temporal_filter_paired_results.json':j({'paired_results':c['paired'],**synthetic_onboard_boundary()}),'temporal_filter_summary.json':j(s),'temporal_filter_summary.md':text,'temporal_filter_failures.json':j({'failures':c['failures'],**synthetic_onboard_boundary()}),'temporal_filter_metric_comparison.csv':_csv(rows,['policy','event_precision','event_recall','event_f1','noise_false_positive_rate','layout_change_precision','layout_change_recall','field_update_precision','field_update_recall','unintended_field_update_count','mean_layout_detection_latency','synthetic','simulated_onboard_sensor','simulation_only','real_robot_supported'])}
def write(out:Path,contents:dict):
    if set(contents)!=set(ARTIFACTS):raise TemporalFilterError('artifact 集不完整。')
    out=Path(out)
    if out.exists():raise TemporalFilterError('输出目录已存在。')
    tmp=None
    try:
        out.parent.mkdir(parents=True,exist_ok=True);tmp=tempfile.TemporaryDirectory(prefix=f'.{out.name}.tmp-',dir=out.parent)
        for n in ARTIFACTS:(Path(tmp.name)/n).write_text(contents[n],encoding='utf-8')
        os.replace(tmp.name,out)
    finally:
        if tmp:tmp.cleanup()
    return {n:out/n for n in ARTIFACTS}
