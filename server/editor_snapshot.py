"""Verified editor state; never approximate a mod's settled mass from PART.mass."""
import hashlib
import math
from pathlib import Path

from .ksp_confignode import parse


def finite(value, minimum=0):
    if isinstance(value, bool):
        raise ValueError('布尔值不能用作质量或性能')
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError('质量或性能不是有效有限数值')
    return result


def module_fields(module):
    values={v.key:v.value for v in module.values}
    values.update({p.value('name'):p.value('value') for p in module.children_named('FIELD')})
    return values


def verify_sources(source):
    root = Path(source['ksp_root']).resolve()
    records = source.get('files') or []
    if not records:
        raise ValueError('缺少游戏配置来源指纹')
    plugin_paths = {record['path'].replace('\\','/').lower() for record in records if record['path'].lower().endswith('.dll')}
    current_plugins = {str(p.relative_to(root)).replace('\\','/').lower() for p in (root/'GameData').rglob('*.dll')}
    if plugin_paths != current_plugins:
        raise ValueError('Mod 插件集合已变化，请在游戏中重新保存火箭')
    for record in records:
        path = (root / record['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('游戏来源文件缺失或路径越界')
        if hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('游戏配置或 Mod 已变化，请在游戏中重新保存火箭')


def checked_runtime_performance(stage):
    verify_sources(stage['runtime_source'])
    source=stage['runtime_source']
    root=Path(source['ksp_root']).resolve()
    snapshot_path=Path(source['snapshot_path']).resolve()
    if not snapshot_path.is_relative_to(root/'PluginData/C3MassTelemetry/EditorSnapshots'):
        raise ValueError('快照来源路径不合法')
    raw=snapshot_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=source['snapshot_sha256']:
        raise ValueError('编辑器快照已变化')
    node=parse(raw.decode('utf-8-sig'))
    node=node.child('C3_EDITOR_SNAPSHOT') or node
    if node.value('craft_sha256')!=source['craft_sha256']:
        raise ValueError('快照火箭版本不匹配')
    expected_stages,_=automatic_stages({'node':node,'parts':{p.value('craft_id'):p for p in node.children_named('PART')},'source':source})
    expected=next((s for s in expected_stages if s['part_ids']==stage.get('part_ids')),None)
    if expected is None:
        raise ValueError('保存的分级与游戏快照不一致')
    for key in ('wet_mass_t','dry_mass_t'):
        if abs(finite(stage[key])-expected[key])>.0000001:
            raise ValueError('质量已手工改变，与自动识别来源不一致')
    if stage.get('max_ignitions')!=expected['max_ignitions'] or stage.get('multi_ignition')!=expected['multi_ignition']:
        raise ValueError('点火限制与游戏快照不一致')
    if bool(stage.get('ground_ignition_only'))!=expected['ground_ignition_only']:
        raise ValueError('地面点火限制与游戏快照不一致')
    value = stage['runtime_performance']
    for key in ('isp_vac','isp_sl','thrust'):
        if abs(finite(value[key])-expected['runtime_performance'][key])>.0000001:
            raise ValueError('发动机性能与游戏快照不一致')
    return {'isp_vac': finite(value['isp_vac'], .001),
            'isp_sl': finite(value['isp_sl'], .001),
            'thrust': finite(value['thrust'], .001), 'isp_type': 'editor_runtime'}


def read_snapshot(root, craft_content):
    root = Path(root).resolve()
    digest = hashlib.sha256(craft_content).hexdigest()
    path = root / 'PluginData/C3MassTelemetry/EditorSnapshots' / (digest + '.c3snapshot')
    if not path.is_file():
        return None
    if path.stat().st_size > 25*1024*1024:
        raise ValueError('编辑器快照过大')
    raw = path.read_bytes()
    tree = parse(raw.decode('utf-8-sig'))
    node = tree.child('C3_EDITOR_SNAPSHOT') or tree
    if node.value('schema_version') != '1' or node.value('craft_sha256') != digest:
        raise ValueError('编辑器快照与火箭版本不匹配')
    if node.value('infinite_propellant')=='True':
        raise ValueError('无限燃料状态不适用于有限推进剂运力曲线')
    files = [{'path':'GameData/ModuleManager.ConfigCache','sha256':node.value('config_cache_sha256')}]
    files.extend({'path':p.value('path'), 'sha256':p.value('sha256')} for p in node.children_named('PLUGIN_SOURCE'))
    if len(files) < 2:
        raise ValueError('旧快照缺少 Mod 指纹，请重新保存')
    source = {'ksp_root':str(root), 'files':files, 'craft_sha256':digest,
              'snapshot_sha256':hashlib.sha256(raw).hexdigest(), 'snapshot_path':str(path)}
    verify_sources(source)
    parts = node.children_named('PART')
    craft = parse(craft_content.decode('utf-8-sig'))
    craft_ids = {p.value('part').rsplit('_',1)[-1] for p in craft.children_named('PART')}
    by_id = {p.value('craft_id'):p for p in parts}
    if len(by_id) != len(parts) or set(by_id) != craft_ids:
        raise ValueError('编辑器部件实例与 craft 不一致')
    dry = sum(finite(p.value('settled_dry_mass_t')) for p in parts)
    resource = sum(finite(p.value('resource_mass_t')) for p in parts)
    for total,key in [(dry,'editor_dry_mass_t'),(resource,'editor_resource_mass_t'),(dry+resource,'editor_total_mass_t')]:
        expected=finite(node.value(key))
        if abs(total-expected) > max(.00002,expected*5e-7):
            raise ValueError('编辑器部件质量与整箭质量对账失败')
    for part in parts:
        actual=sum(finite(r.value('amount'))*finite(r.value('density_t_per_unit')) for r in part.children_named('RESOURCE'))
        if abs(actual-finite(part.value('resource_mass_t'))) > .00001:
            raise ValueError('编辑器资源质量对账失败')
    return {'node':node,'parts':by_id,'source':source,'dry_mass_t':dry,'resource_mass_t':resource}


def automatic_stages(snapshot):
    """Cut actual decoupler attachment edges; istg only orders engine activation."""
    parts=snapshot['parts']
    excluded={pid for pid,p in parts.items() if any(m.value('name') in ('LaunchClamp','ModuleLaunchClamp') for m in p.children_named('MODULE_STATE'))}
    ids=set(parts)-excluded
    graph={pid:set() for pid in ids}
    cuts=set()
    for pid in ids:
        p=parts[pid]
        parent=p.value('parent_craft_id')
        if parent in ids:
            graph[pid].add(parent);graph[parent].add(pid)
        for mod in p.children_named('MODULE_STATE'):
            name=mod.value('name')
            if name == 'ModuleAnchoredDecoupler':
                raise ValueError('径向分离与并联级尚需独立燃料流分析')
            if name != 'ModuleDecouple':
                continue
            if mod.value('omni_decoupler') == 'True':
                raise ValueError('全向分离节点需要进一步确认')
            for attachment in p.children_named('ATTACHMENT'):
                other=attachment.value('attached_craft_id')
                if attachment.value('node_id')==mod.value('explosive_node_id') and other in ids:
                    cuts.add(frozenset((pid,other)))
    for edge in cuts:
        a,b=tuple(edge)
        graph[a].discard(b);graph[b].discard(a)
    remaining=set(ids);components=[]
    while remaining:
        stack=[min(remaining)];component=set()
        while stack:
            pid=stack.pop()
            if pid in component: continue
            component.add(pid);stack.extend(graph[pid]-component)
        remaining-=component;components.append(component)
    powered=[];passive=[]
    for component in components:
        engines=[(pid,e) for pid in sorted(component) for e in parts[pid].children_named('ENGINE') if e.value('selected')=='True']
        if not engines:
            passive.append(component);continue
        activation={int(parts[pid].value('inverse_stage')) for pid,e in engines}
        if len(activation)!=1:
            raise ValueError('一个连接组件存在不同点火阶段，不能当作独立物理级')
        powered.append({'ids':component,'engines':engines,'activation':activation.pop()})
    powered.sort(key=lambda p:-p['activation'])
    if not powered or len({s['activation'] for s in powered})!=len(powered):
        raise ValueError('无法唯一识别串联动力级')
    # Engine-free head assemblies remain fixed rocket mass through delivery.
    # More general detached passive assemblies require an explicit event model.
    for component in passive:
        neighbours={i for i,s in enumerate(powered) for edge in cuts if edge & component and edge & s['ids']}
        if len(neighbours)!=1:
            raise ValueError('非动力组件不能唯一归属分级')
        powered[neighbours.pop()]['ids'] |= component
    stages=[]
    for s in powered:
        engines=s['engines']; mixtures=[];thrust=0.;vacflow=0.;slflow=0.;ceilings=[];ground_only=False
        groups=[]
        for pid,engine in engines:
            t=finite(engine.value('max_thrust_kN'),.001)*finite(engine.value('thrust_limiter_percent'))/100
            if t<=0:raise ValueError('已启用发动机的推力限制为零')
            iv=finite(engine.value('vacuum_isp_s'),.001);isl=finite(engine.value('sea_level_isp_s'),.001)
            thrust+=t;vacflow+=t/iv;slflow+=t/isl
            mix={p.value('name'):finite(p.value('ratio'),.000000001) for p in engine.children_named('PROPELLANT') if p.value('ignore_for_isp')!='True'}
            if not mix: raise ValueError('发动机缺少质量消耗推进剂')
            if any(p.value('flow_mode') not in ('STACK_PRIORITY_SEARCH','STAGE_PRIORITY_FLOW','STAGE_STACK_FLOW') for p in engine.children_named('PROPELLANT') if p.value('ignore_for_isp')!='True'):
                raise ValueError('当前推进剂跨级流动方式尚未验证')
            mixtures.append(mix)
            mods=parts[pid].children_named('MODULE_STATE')
            rf=[m for m in mods if m.value('name') in ('ModuleEnginesRF','ModuleEngineIgnitor')]
            if rf:
                global_setting=snapshot.get('node').value('rf_limited_ignitions') if snapshot.get('node') else None
                if global_setting not in ('True','False'):
                    raise ValueError('旧快照未记录 RealFuels 点火设置，请重新保存火箭')
                limits=[module_fields(m).get('ignitions') or module_fields(m).get('ignitionsRemained') for m in rf]
                if any(value is None for value in limits): raise ValueError('无法识别发动机点火次数限制')
                if global_setting=='True':
                    if any(int(v)==0 for v in limits):ground_only=True;ceilings.append(1)
                    ceilings.extend(int(v) for v in limits if int(v)>0)
            name=parts[pid].value('name')
            cfg=(name[5:] if name.startswith('SSTU-') else name)+'.cfg'
            groups.append({'cfg':cfg,'count':1,'part_id':pid,'engine_id':engine.value('engine_id')})
        if any(m!=mixtures[0] for m in mixtures):
            raise ValueError('同级发动机使用不同燃料比例，需要燃料流分析')
        resources={}; densities={}
        # Follow crossfeed from every engine within the physical component.
        # Pooling is accepted only when engines have the same reachable tanks.
        reachable_sets=[]
        for engine_id,_ in engines:
            reachable=set(); stack=[engine_id]
            while stack:
                pid=stack.pop()
                if pid in reachable: continue
                reachable.add(pid)
                if parts[pid].value('fuel_cross_feed')=='True':
                    # A non-crossfeed tank can supply its own contents; it
                    # blocks traversal through the tank to further parts.
                    stack.extend(n for n in graph[pid] if n in s['ids'])
            reachable_sets.append(reachable)
        if any(r!=reachable_sets[0] for r in reachable_sets):
            raise ValueError('同级发动机的可用油箱不同，需要逐发动机燃料流分析')
        for pid in s['ids']:
            for r in parts[pid].children_named('RESOURCE'):
                name=r.value('name')
                if pid not in reachable_sets[0] or name not in mixtures[0] or r.value('flow_state')!='True':continue
                resources[name]=resources.get(name,0)+finite(r.value('amount'))
                densities[name]=finite(r.value('density_t_per_unit'))
        mix=mixtures[0]
        units=min(resources.get(name,0)/ratio for name,ratio in mix.items())
        propellant=units*sum(ratio*densities.get(name,0) for name,ratio in mix.items())
        wet=sum(finite(parts[pid].value('current_wet_mass_t')) for pid in s['ids'])
        if propellant<=0 or propellant>=wet:raise ValueError('未识别到有效可消耗推进剂')
        stages.append({'wet_mass_t':wet,'dry_mass_t':wet-propellant,'engine_groups':groups,'engines':groups[0],
                       'multi_ignition':not ceilings or min(ceilings)>1,'max_ignitions':min(ceilings) if ceilings else None,
                       'ground_ignition_only':ground_only,
                       'ignition_limit_source':'runtime module state' if ceilings else 'no ignition-limited engine module present',
                       'parallel':False,'part_ids':sorted(s['ids']),'runtime_source':snapshot['source'],
                       'runtime_performance':{'thrust':thrust,'isp_vac':thrust/vacflow,'isp_sl':thrust/slflow},
                       'tank_states':[{'part_id':pid,'module':m.value('name'),'fields':module_fields(m)} for pid in sorted(s['ids']) for m in parts[pid].children_named('MODULE_STATE') if m.value('name') in ('ModuleFuelTanks','SSTUVolumeContainer','ProceduralPart','ModuleB9PartSwitch')]})
    return stages, {'excluded_nonflight_part_ids':sorted(excluded),'separation_edges':[sorted(e) for e in cuts],
                    'mass_method':'settled editor mass; dry includes nonpropellant and unusable mixture residuals'}
