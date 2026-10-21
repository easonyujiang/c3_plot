"""Read-only inventory of existing flight snapshots; no mass assumptions."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from tools.paths import ROOT
from server.ksp_confignode import parse, CraftParseError


def subtree(text, name):
    """Extract one balanced line-delimited node; preserve its contents exactly.

    Existing saves contain scenario nodes with multiword names unsupported by
    the strict craft parser. Only the independent FLIGHTSTATE subtree is read.
    """
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == name]
    if len(starts) != 1:
        raise ValueError('Expected exactly one ' + name)
    start = starts[0]
    depth, opened = 0, False
    for i in range(start + 1, len(lines)):
        token = lines[i].strip()
        if token == '{':
            depth += 1
            opened = True
        elif token == '}':
            depth -= 1
            if opened and depth == 0:
                return ''.join(lines[start:i + 1])
    raise ValueError('Unbalanced ' + name)


def inventory(folder):
    result = []
    for path in sorted(folder.glob('*.sfs')):
        raw = path.read_bytes()
        text = raw.decode('utf-8-sig')
        try:
            flight = parse(subtree(text, 'FLIGHTSTATE')).child('FLIGHTSTATE')
        except CraftParseError as error:
            raise ValueError(str(path) + ': ' + repr(error)) from error
        record = {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest(),
                  'ut_s': flight.value('UT') if flight else None, 'vessels': [],
                  'scope': 'FLIGHTSTATE only; scenario modules not interpreted'}
        for vessel in flight.children_named('VESSEL') if flight else []:
            orbit = vessel.child('ORBIT')
            entry = {'name': vessel.value('name'), 'id': vessel.value('pid'),
                     'situation': vessel.value('sit'), 'landed': vessel.value('landed'),
                     'orbit': dict(orbit.all_values()) if orbit else None, 'parts': []}
            for part in vessel.children_named('PART'):
                engines = [dict(module.all_values()) for module in part.children_named('MODULE')
                           if module.value('name') in ('SSTUModularEngineCluster', 'ModuleEngineConfigs',
                                                       'ModuleEngines', 'ModuleEnginesFX', 'ModuleEnginesRF')]
                entry['parts'].append({'name': part.value('name'), 'inverse_stage': part.value('istg'),
                                      'saved_mass': part.value('mass'), 'resources':
                                      [dict(r.all_values()) for r in part.children_named('RESOURCE')],
                                      'engines': engines})
            record['vessels'].append(entry)
        result.append(record)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.folder)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    for save in result:
        print(Path(save['path']).name, [(v['name'], v['situation'], len(v['parts'])) for v in save['vessels']])
