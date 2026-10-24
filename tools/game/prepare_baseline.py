"""Prepare a fresh isolated game baseline; flight is explicit and runtime-gated."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from server.ksp_confignode import parse


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root, folder, with_fairings=False, ballast_amount=None, flight=False):
    if not re.fullmatch(r'C3Baseline[0-9A-Za-z_-]+', folder):
        raise ValueError('A dedicated C3Baseline save name is required')
    if flight and (not with_fairings or ballast_amount is None):
        raise ValueError('Flight requires the calibrated payload and fairings')
    source = root / 'saves/RocketMaking'
    target = root / 'saves' / folder
    if target.exists():
        raise ValueError('Never overwrite an existing trial')
    baseline = source / 'Ships/VAB/Neutrons I 2630.craft'
    donor = source / 'Ships/VAB/1T_Test.craft'
    original = {str(p.relative_to(root)): sha(p) for p in source.rglob('*') if p.is_file()}
    btext, dtext = baseline.read_text(), donor.read_text()
    b, d = parse(btext), parse(dtext)
    bp, dp = b.children_named('PART'), d.children_named('PART')
    separator = bp[0].value('part')
    donor_sep = next(p for p in dp if p.value('part') == separator)
    payload = dp[:dp.index(donor_sep)]
    expected = ['probeCoreOcto.v2'] + ['batteryPack'] * 8 + ['proceduralTankRealFuels', 'longAntenna']
    if [p.value('part').rsplit('_', 1)[0] for p in payload] != expected:
        raise ValueError('Donor payload changed: inspect before composing')
    shift = [float(a)-float(c) for a,c in zip(bp[0].value('pos').split(','), donor_sep.value('pos').split(','))]
    def block(text, node):
        return '\n'.join(text.splitlines()[node.start_line-1:node.end_line])+'\n'
    blocks=[]
    for i,p in enumerate(payload):
        text=block(dtext,p)
        pos=','.join(format(float(v)+s,'.10g') for v,s in zip(p.value('pos').split(','),shift))
        text=re.sub(r'(?m)^(\s*pos = ).*$',lambda m:m[1]+pos,text)
        if i==0:
            text=re.sub(r'(?m)^(\s*attPos0 = ).*$',lambda m:m[1]+pos,text)
        if ballast_amount is not None and p.value('part').startswith('proceduralTankRealFuels_'):
            if not 0 <= ballast_amount <= 94.2478:
                raise ValueError('Ballast must fit the unchanged donor tank')
            text=re.sub(r'(?m)^(\s*amount = ).*$',lambda m:m[1]+format(ballast_amount,'.15g'),text)
        blocks.append(text)
    # Preserve donor ballast amount initially: runtime measurement defines the
    # delivered assembly mass. A name or static modMass is not a mass measurement.
    baseblocks=[block(btext,p) for p in bp]
    top=next(v for v in donor_sep.values_named('attN') if v.startswith('top,'))
    baseblocks[0]=re.sub(r'(?m)^(\s*attN = )top,.*$',lambda m:m[1]+top,baseblocks[0])
    baseblocks[0]=re.sub(r'(?m)^(\s*attPos0 = ).*$',lambda m:m[1]+donor_sep.value('attPos0'),baseblocks[0])
    if with_fairings:
        # Insert a dedicated fairing stage between clamp release and upper-stage
        # ignition. Sharing the clamps' stage would block launch at low altitude.
        for i, part in enumerate(bp):
            stage=int(part.value('istg'))
            if stage >= 2:
                baseblocks[i]=re.sub(r'(?m)^(\s*istg = ).*$',lambda m:m[1]+str(stage+1),baseblocks[i])
        base=bp[1]
        donor_base=next(p for p in dp if p.value('part')==base.value('part'))
        center=[float(v) for v in base.value('pos').split(',')]
        oldcenter=[float(v) for v in donor_base.value('pos').split(',')]
        sides=[p for p in dp if p.value('part').startswith('KzProcFairingSide1_')]
        if len(sides)!=2: raise ValueError('Expected two donor fairing sides')
        additions=[]
        for side in sides:
            ident=side.value('part')
            oldpos=[float(v) for v in side.value('pos').split(',')]
            local=[(v-c)*(2.5/1.87500012) for v,c in zip(oldpos,oldcenter)]
            pos=','.join(format(c+v,'.10g') for c,v in zip(center,local))
            text=block(dtext,side)
            text=re.sub(r'(?m)^(\s*pos = ).*$',lambda m:m[1]+pos,text)
            text=re.sub(r'(?m)^(\s*attPos0 = ).*$',lambda m:m[1]+','.join(map(str,local)),text)
            # The actual shape/mass is rebuilt in the editor, then saved and
            # inspected on the pad before any flight can be authorized.
            additions.append(text)
            baseblocks[1]=baseblocks[1].replace('\n{\n','\n{\n\tlink = '+ident+'\n',1)
            node=next(v for v in donor_base.values_named('attN') if ident in v)
            node_name=node.split(',',1)[0]
            pattern=r'(?m)^(\s*attN = '+re.escape(node_name)+r',)Null(_.*)$'
            baseblocks[1], count=re.subn(pattern,lambda m:m[1]+ident+m[2],baseblocks[1])
            if count != 1: raise ValueError('Fairing attachment must replace exactly one empty node')
        baseblocks.extend(additions)
    name='C3 Neutrons I 2630 Payload Baseline'
    header='\n'.join(btext.splitlines()[:bp[0].start_line-1])+'\n'
    header=re.sub(r'(?m)^ship = .*$', 'ship = '+name, header)
    header=re.sub(r'(?m)^description = .*$', 'description = Isolated baseline; donor payload mass pending runtime measurement; 220km x 220km at 20deg', header)
    header=re.sub(r'(?m)^vesselType = .*$', 'vesselType = Probe', header)
    craft=header+''.join(blocks+baseblocks)
    parsed=parse(craft)
    ids=[p.value('part') for p in parsed.children_named('PART')]
    if len(ids)!=len(set(ids)) or len(ids)!=(24 if with_fairings else 22):
        raise ValueError('Unexpected or duplicate parts')
    for part in parsed.children_named('PART'):
        node_names=[v.split(',',1)[0] for v in part.values_named('attN')]
        if len(node_names)!=len(set(node_names)):
            raise ValueError('Duplicate attachment node')
    links=[v for p in parsed.children_named('PART') for v in p.values_named('link')]
    if len(links)!=len(ids)-1 or set(links)!=set(ids[1:]):
        raise ValueError('Craft attachment tree is not connected')
    # Flight arming is separate from readiness: the runtime controller must
    # confirm mass, fairing shielding, staging and control before countdown.
    request_dir=root/'PluginData/C3Baseline'
    request=request_dir/'inspect.request'
    if request.exists():
        raise ValueError('An inspection request already exists')
    target.mkdir()
    craft_dir=target/'Ships/VAB';craft_dir.mkdir(parents=True)
    cp=craft_dir/(name+'.craft');cp.write_text(craft,encoding='utf-8')
    save=source/'persistent.sfs'
    save_text=save.read_text(encoding='utf-8-sig')
    save_text,count=re.subn(r'(?m)^(\s*Title = ).*$',lambda m:m[1]+folder+' (SCIENCE_SANDBOX)',save_text,count=1)
    if count != 1: raise ValueError('Save title missing')
    (target/'persistent.sfs').write_text(save_text,encoding='utf-8')
    environment_paths=['GameData/ModuleManager.ConfigCache','GameData/MechJeb2/Plugins/MechJeb2.dll',
                       'GameData/C3MassValidation/Plugins/C3MassValidation.dll']
    environment={p:sha(root/p) for p in environment_paths}
    manifest={'source_files':original,'environment_sha256':environment,'craft_sha256':sha(cp),'payload_part_ids':[p.value('part') for p in payload],
              'target_pe_m':220000,'target_ap_m':220000,'inclination_deg':20,
              'payload_mass_status':'pending_runtime','fairing_status':'pending_editor_rebuild' if with_fairings else 'missing_sides_pad_only',
              'ballast_amount':ballast_amount,
              'source_save_sha256':sha(save)}
    (target/'baseline-inputs.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    changed=[p for p,h in original.items() if sha(root/p)!=h]
    if changed: raise ValueError('Source changed during preparation: '+repr(changed))
    request_dir.mkdir(parents=True,exist_ok=True)
    request.write_text('C3_BASELINE_REQUEST\n{\n folder = '+folder+'\n craft = '+name+'\n mode = '+('flight' if flight else 'inspect')+'\n editor_rebuild = '+str(with_fairings)+'\n craft_sha256 = '+sha(cp)+'\n save_sha256 = '+sha(target/'persistent.sfs')+'\n}\n',encoding='ascii')
    return {'folder':folder,'parts':len(ids),'payload_parts':len(payload),'original_files_unchanged':len(original),'mode':'gated baseline flight' if flight else 'pad inspection only'}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ksp-root',type=Path,required=True)
    ap.add_argument('--folder',required=True)
    ap.add_argument('--flight',action='store_true')
    ap.add_argument('--with-fairings',action='store_true')
    ap.add_argument('--ballast-amount',type=float)
    a=ap.parse_args()
    print(json.dumps(prepare(a.ksp_root,a.folder,a.with_fairings,a.ballast_amount,a.flight),ensure_ascii=False))
