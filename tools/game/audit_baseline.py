"""Read live isolated baseline evidence and print results; no database or file writes."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from server.ksp_confignode import parse


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def snapshot(path):
    if not path.exists():
        return None
    tree = parse(path.read_text(encoding='utf-8-sig'))
    node = tree.child('C3_BASELINE_INSPECTION') or tree
    result = dict(node.all_values())
    parts = node.children_named('PART')
    payload = [p for p in parts if p.value('payload') == 'True']
    result.update(parts=len(parts), payload_parts=len(payload),
                  payload_part_sum_t=sum(float(p.value('mass_t')) for p in payload),
                  shielded_payload_parts=sum(p.value('shielded') == 'True' for p in payload))
    return result


def audit(root, folder):
    if not folder.startswith('C3Baseline') or Path(folder).name != folder:
        raise ValueError('Require an isolated baseline folder')
    save = root / 'saves' / folder
    manifest = json.loads((save/'baseline-inputs.json').read_text())
    data = root/'PluginData/C3Baseline'
    changed = [p for p,h in manifest['source_files'].items() if digest(root/p) != h]
    env_changed = [p for p,h in manifest.get('environment_sha256',{}).items() if digest(root/p) != h]
    initial = snapshot(data/(folder+'.c3inspection'))
    configured = snapshot(data/(folder+'_configured.c3inspection'))
    final = snapshot(data/(folder+'_final.c3inspection'))
    target_payload = None
    for state in (initial, configured, final):
        value = (state or {}).get('target_payload_t')
        if value not in (None, ''):
            target_payload = float(value)
            break
    if target_payload is None:
        target_payload = float(manifest.get('payload_t', 1.0))
    result = {'folder':folder, 'source_files_checked':len(manifest['source_files']),
              'source_files_changed':changed, 'environment_files_changed':env_changed,
              'target_payload_t':target_payload,
              'initial':initial, 'configured':configured, 'final':final,
              'status':'no_flight_evidence'}
    path = data/(folder+'_flight.csv')
    if not path.exists():
        return result
    with path.open(encoding='utf-8-sig',newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        result['status']='flight_log_empty'
        return result
    numbers = ['ut_s','elapsed_s','altitude_m','periapsis_m','apoapsis_m','inclination_deg',
               'mass_t','throttle','thrust_kN','electric_charge']
    for row in rows:
        for key in numbers:
            row[key]=float(row[key])
            if not math.isfinite(row[key]): raise ValueError('Non-finite '+key)
        row['stage']=int(row['stage'])
    if any(b['ut_s']<=a['ut_s'] for a,b in zip(rows,rows[1:])):
        raise ValueError('Non-increasing telemetry time')
    stable_start = None
    for row in rows:
        reached = (abs(row['periapsis_m']-manifest['target_pe_m'])<=5000 and
                   abs(row['apoapsis_m']-manifest['target_ap_m'])<=5000 and
                   abs(row['inclination_deg']-manifest['inclination_deg'])<=0.2 and row['thrust_kN']<0.01)
        stable_start = row['ut_s'] if reached and stable_start is None else stable_start if reached else None
    hold = 0 if stable_start is None else rows[-1]['ut_s']-stable_start
    stage_events=[]
    for row in rows:
        if not stage_events or row['stage']!=stage_events[-1]['stage']:
            stage_events.append({k:row[k] for k in ['elapsed_s','stage','altitude_m','mass_t']})
    payload_mass = float(final['payload_mass_t']) if final else None
    payload_matches = bool(payload_mass is not None and abs(payload_mass-target_payload)<=0.001)
    intact = bool(final and final['payload_parts']==11 and payload_matches
                  and final['controllable']=='True')
    result.update(status='target_verified' if hold>=30 and intact and not changed and not env_changed else 'target_not_verified',
                  samples=len(rows),last_sample=rows[-1],orbit_hold_s=hold,
                  payload_mass_t=payload_mass,payload_mass_matches_target=payload_matches,
                  peak_altitude_m=max(r['altitude_m'] for r in rows),
                  min_electric_charge=min(r['electric_charge'] for r in rows),stage_events=stage_events)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ksp-root',type=Path,required=True)
    parser.add_argument('--folder',required=True)
    args=parser.parse_args()
    print(json.dumps(audit(args.ksp_root,args.folder),ensure_ascii=False,indent=2))
