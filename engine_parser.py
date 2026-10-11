"""
KSP .cfg Engine Parser
======================
Extracts engine parameters from KSP .cfg files.
Handles ModuleEnginesFX, ModuleEngines, atmosphereCurve.

Returns: thrust, vacuum Isp, sea-level Isp, and full Isp curve.
Stage masses already include engine mass, so engine mass is NOT added separately.
"""
import re, os, json

def _find_all_blocks(text, block_name):
    results = []
    pattern = rf'{re.escape(block_name)}\s*\{{'
    pos = 0
    while True:
        m = re.search(pattern, text[pos:], re.IGNORECASE)
        if not m: break
        start = pos + m.end() - 1
        depth, i = 1, start + 1
        while i < len(text) and depth > 0:
            if text[i] == '{': depth += 1
            elif text[i] == '}': depth -= 1
            i += 1
        results.append(text[start+1:i-1])
        pos = i
    return results

def _find_value(text, key, default=None):
    m = re.search(rf'{re.escape(key)}\s*=\s*([^\n\r]+)', text)
    return m.group(1).strip() if m else default

def _find_key_entries(text):
    """
    Parse atmosphereCurve key entries.
    Format: key = pressure isp [tangent_in] [tangent_out]
    Returns list of (pressure, isp, tangent_in, tangent_out)
    """
    entries = []
    for m in re.finditer(
        r'key\s*=\s*([0-9.e+\-]+)\s+([0-9.e+\-]+)'
        r'(?:\s+([0-9.e+\-]+))?(?:\s+([0-9.e+\-]+))?',
        text
    ):
        p  = float(m.group(1))
        isp = float(m.group(2))
        t1 = float(m.group(3)) if m.group(3) else None
        t2 = float(m.group(4)) if m.group(4) else None
        entries.append(dict(pressure=p, isp=isp, tangent_in=t1, tangent_out=t2))
    return entries

def extract_engine_params(cfg_filename):
    """
    Extract engine parameters from a KSP .cfg file.

    Returns dict:
        thrust_kN:  max thrust (kN)
        isp_vac_s:  vacuum Isp (s)
        isp_sl_s:   sea-level Isp (s) — None if engine can't operate at sea level
        isp_curve:  full atmosphereCurve as list of {pressure, isp, tangent_in, tangent_out}
        isp_type:   'vacuum_only' | 'linear' | 'multi_segment' | 'altitude_compensating'
        engine_name: display name from cfg title field
    """
    engines_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'engines')
    filepath = os.path.join(engines_dir, os.path.basename(cfg_filename))

    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()

    # Engine display name — clean autoLOC localization strings
    title_raw = _find_value(text, 'title', '')
    if not title_raw:
        title_raw = os.path.basename(cfg_filename).replace('.cfg', '')
    # Handle #autoLOC_500460 //#autoLOC_500460 = S1 SRB-KD25k "Kickback" Solid Fuel Booster
    m = re.search(r'=\s*(.+)$', title_raw)
    if m:
        title = m.group(1).strip()
    else:
        # Handle SSTU - SC-ENG - Merlin-1B -> Merlin-1B
        title = title_raw.replace('SSTU - SC-ENG - ', '').replace('SSTU - SC-ENG-', '')
    # Trim quotes and extra whitespace
    title = title.strip().strip('"')

    thrust = 0.0
    isp_vac = 0.0
    isp_sl = None
    isp_curve = []

    # Find engine MODULE — prefer ModuleEnginesFX, fall back to ModuleEngines
    eng_module = None
    for mod_block in _find_all_blocks(text, 'MODULE'):
        name_val = _find_value(mod_block, 'name', '')
        if 'ModuleEnginesFX' in name_val:
            eng_module = mod_block
            break
    if eng_module is None:
        for mod_block in _find_all_blocks(text, 'MODULE'):
            name_val = _find_value(mod_block, 'name', '')
            if 'ModuleEngines' in name_val:
                eng_module = mod_block
                break

    if eng_module:
        thrust = float(_find_value(eng_module, 'maxThrust', 0))

        ac_blocks = _find_all_blocks(eng_module, 'atmosphereCurve')
        ac_text = ac_blocks[0] if ac_blocks else None
        if ac_text:
            keys = _find_key_entries(ac_text)
            keys.sort(key=lambda x: x['pressure'])
            isp_curve = keys

            if keys:
                # Vacuum Isp: first key (pressure = 0)
                isp_vac = keys[0]['isp']

                # Sea-level Isp: find key at pressure = 1.0, or highest pressure
                sl_key = None
                for k in keys:
                    if abs(k['pressure'] - 1.0) < 0.02:
                        sl_key = k
                        break
                if sl_key:
                    isp_sl = sl_key['isp']
                elif len(keys) > 1:
                    # If no key at exactly 1 atm, check if engine even works at sea level
                    # 3-key engines with sharp cutoff near 0.07 atm are vacuum-only
                    last = keys[-1]
                    if last['pressure'] > 0.5 and last['isp'] > 10:
                        isp_sl = last['isp']

    # Classify Isp curve type
    n = len(isp_curve)
    if n <= 1:
        isp_type = 'vacuum_only'
    elif n == 2:
        isp_type = 'linear'
    elif n == 3 and isp_curve[1]['pressure'] < 0.1 and isp_curve[2]['pressure'] < 0.1:
        isp_type = 'vacuum_only'  # sharp cutoff = vacuum engine
    elif n >= 4:
        isp_type = 'altitude_compensating'
    else:
        isp_type = 'multi_segment'

    return dict(
        thrust_kN=thrust,
        isp_vac_s=isp_vac,
        isp_sl_s=isp_sl,
        isp_curve=isp_curve,
        isp_type=isp_type,
        engine_name=title,
    )


if __name__ == '__main__':
    import sys
    for f in sys.argv[1:] or ['SC-ENG-Merlin-1B.cfg', 'liquidEngineAerospike.cfg']:
        print(f"\n=== {f} ===")
        params = extract_engine_params(f)
        for k, v in params.items():
            if k == 'isp_curve':
                print(f"  {k}: ({len(v)} points)")
                for pt in v:
                    print(f"    p={pt['pressure']:.4f}  isp={pt['isp']:.0f}  t_in={pt['tangent_in']}  t_out={pt['tangent_out']}")
            else:
                print(f"  {k}: {v}")
