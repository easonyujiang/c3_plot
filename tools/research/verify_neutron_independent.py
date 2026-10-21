"""Independent formula check; imports no project computation or solver code.

NASA rocket equation: https://www1.grc.nasa.gov/beginners-guide-to-aeronautics/ideal-rocket-equation/
MIT vis-viva / Hohmann: https://ocw.mit.edu/courses/16-851-satellite-engineering-fall-2003/e9e1eb82a6e428358fb07c1ee0fd2c3c_ps2_cg_solution.pdf
Three-point empirical loss interpolation is the project's assumption, not a
NASA/MIT flight-capacity model. Agreement verifies arithmetic, not flights.
"""
import argparse
import json
import math
import re
import urllib.request
from pathlib import Path

from tools.paths import ROOT
MU=3.986004418e14
R=6371000.0
G0=9.80665
SID_DAY=86164.1

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='http://127.0.0.1:8765')
    parser.add_argument('--output',type=Path,default=ROOT/'output/independent_check')
    args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    def api(path,body=None):
        request=urllib.request.Request(args.base+path,data=json.dumps(body).encode() if body is not None else None,
                                      headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)
    cfg=api('/api/rockets/neutron_i_2630'); stored=api('/api/data/neutron_i_2630')
    stages=[]
    for stage in cfg['stages']:
        # Independently read vacuum Isp from the local engine's atmosphereCurve.
        text=(ROOT/'engines'/stage['engines']['cfg']).read_text(encoding='utf-8')
        block=re.search(r'atmosphereCurve\s*\{([^}]+)',text).group(1)
        isp=float(re.search(r'key\s*=\s*0(?:\.0+)?\s+([\d.]+)',block).group(1))
        stages.append((float(stage['wet_mass_t']),float(stage['dry_mass_t']),isp))
    assert [s[2] for s in stages]==[s['isp_vac'] for s in stored['stages']]
    latitude=19.81
    rotation=2*math.pi*(R+92.7)/SID_DAY
    ref_rotation=rotation*math.cos(math.radians(latitude))
    def park(h):return math.sqrt(MU/(R+h*1000))-ref_rotation
    def ideal(payload):
        above=payload;dv=0.0
        for wet,dry,isp in reversed(stages):
            dv+=G0*isp*math.log((wet+above)/(dry+above));above+=wet
        return dv
    anchors=[]
    for point in cfg['calibration']:
        m=point['payload_t'];h=point['orbit_alt_km'];remaining=point['dv_rem_m_s']
        anchors.append({'payload_t':m,'altitude_km':h,'stored_remaining_dv':remaining,
                        'ideal_vacuum_dv':ideal(m),'inferred_loss':ideal(m)-park(h)-remaining,
                        'remaining_normalized_220':remaining+park(h)-park(220)})
    def loss(payload):
        result=0.0
        for i,p in enumerate(anchors):
            factor=1.0
            for j,q in enumerate(anchors):
                if i!=j:factor*=(payload-q['payload_t'])/(p['payload_t']-q['payload_t'])
            result+=p['inferred_loss']*factor
        return max(0.0,result)
    def remaining(payload):return ideal(payload)-park(220)-loss(payload)
    def capacity(required):
        lo,hi=0.0,100.0
        if remaining(lo)<required:return 0.0
        for _ in range(90):
            middle=(lo+hi)/2
            if remaining(middle)>=required:lo=middle
            else:hi=middle
        return (lo+hi)/2
    r0=R+220000; rp=R+400000;ra=R+40000000
    a_transfer=(r0+ra)/2;a_final=(rp+ra)/2
    velocity=lambda radius,a:math.sqrt(MU*(2/radius-1/a))
    departure=velocity(r0,a_transfer)-math.sqrt(MU/r0)
    arrival=velocity(ra,a_final)-velocity(ra,a_transfer)
    inclinations=[]
    for inc in (0.0,latitude,89.0,180.0):
        reachable=min(max(inc,latitude),180-latitude)
        change=math.radians(abs(inc-reachable))
        at_apo=math.sqrt(velocity(ra,a_transfer)**2+velocity(ra,a_final)**2-
                         2*velocity(ra,a_transfer)*velocity(ra,a_final)*math.cos(change))
        direction=max(0.0,ref_rotation-rotation*math.cos(math.radians(reachable)))
        required=departure+at_apo+direction
        equivalent=((math.sqrt(MU/r0)+required)**2-2*MU/r0)/1e6
        response=api('/api/mission-query',{'orbit_type':'elliptical','periapsis_km':400,'apoapsis_km':40000,
                   'inclination_deg':inc,'parking_alt_km':220,'launch_lat':latitude,'payload_t':.7})
        candidate=next(p for p in response['results'] if p['slug']=='neutron_i_2630')
        assert abs(required-response['mission']['required_dv'])<.051
        assert abs(equivalent-response['mission']['c3_effective'])<.00051
        inclinations.append({'inclination_deg':inc,'departure_dv':departure,'arrival_dv':at_apo,
                             'launch_direction_dv':direction,'plane_change_deg':math.degrees(change),
                             'required_dv':required,'physical_c3':-MU/a_final/1e6,'equivalent_c3':equivalent,
                             'independent_capacity_t':capacity(required),'software_capacity_t':candidate['verified_capacity'],
                             'recommendation_eligible':candidate['recommendation_eligible']})
    mission=next(p for p in inclinations if p['inclination_deg']==89)
    assert abs(mission['independent_capacity_t']-mission['software_capacity_t'])<.001
    # Model sensitivity: independent interpolation of remaining upper-stage
    # fuel between the two nearest benchmark payloads. This is not an error bar.
    low,high=sorted(anchors,key=lambda p:p['payload_t'])[:2]
    dry=stages[-1][1];ve=G0*stages[-1][2]
    fuel=lambda p:(dry+p['payload_t'])*math.expm1(p['remaining_normalized_220']/ve)
    f0,f1=fuel(low),fuel(high)
    def fuel_linear(m):return f0+(f1-f0)*(m-low['payload_t'])/(high['payload_t']-low['payload_t'])
    lo,hi=low['payload_t'],high['payload_t']
    for _ in range(90):
        mid=(lo+hi)/2
        if ve*math.log1p(fuel_linear(mid)/(dry+mid))>=mission['required_dv']:lo=mid
        else:hi=mid
    alternate=(lo+hi)/2
    for p in anchors:p['reconstructed_remaining_dv']=ideal(p['payload_t'])-park(p['altitude_km'])-loss(p['payload_t'])
    curve_errors=[]
    for p in stored['c3_mass_curve']:
        dv=max(0.0,math.sqrt(max(0.0,2*MU/r0+p['c3']*1e6))-math.sqrt(MU/r0))
        curve_errors.append(abs(capacity(dv)-p['payload']))
    report={'provenance':stored['source_summary'],'formulas':{'rocket':'dv = g0 * Isp * ln(m_initial / m_final)',
             'orbit':'v = sqrt(mu*(2/r - 1/a))','energy':'C3 = -mu/a; equivalent_C3 = (v_parking + required_dv)^2 - 2*mu/r_parking'},
            'model_scope':'independent arithmetic reproduction using historical inputs; no new flight validation',
            'stages':stages,'anchors':anchors,'inclination_checks':inclinations,
            'alternative_upper_fuel_linear_capacity_t':alternate,'alternative_difference_kg':(alternate-mission['independent_capacity_t'])*1000,
            'maximum_curve_difference_kg':max(curve_errors)*1000}
    (out/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'89_degree_case':mission,'alternate_capacity_t':alternate,'max_curve_difference_kg':max(curve_errors)*1000},ensure_ascii=False))

if __name__=='__main__':main()
