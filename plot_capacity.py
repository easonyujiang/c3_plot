"""One command: installed-engine evidence -> offline C3/payload plot.

Writes generated artifacts only, never initializes SQLite or changes YAML.
"""
import argparse
import csv
import hashlib
import html
import json
from pathlib import Path

from compute_c3_mass import compute_rocket, ideal_dv_to_orbit, MU, R_EARTH, H_PARK, v_orbit
from installed_engine_audit import installed_engine_resolver
from server.validation import validate_rocket
from server.yaml_codec import load_config

ROOT = Path(__file__).resolve().parent


def export_curve(config_path, output, ksp_root):
    raw = config_path.read_bytes()
    cfg = load_config(raw.decode('utf-8-sig'))
    valid, errors = validate_rocket(cfg)
    if not valid:
        raise ValueError('; '.join(errors))
    resolver, engines = installed_engine_resolver(cfg, ksp_root)
    data = compute_rocket(cfg, engine_resolver=resolver)
    measured = []
    for point in cfg['calibration']:
        dv = point['dv_rem_m_s'] + ideal_dv_to_orbit(point['orbit_alt_km']) - ideal_dv_to_orbit(H_PARK)
        c3 = ((v_orbit + dv) ** 2 - 2 * MU / (R_EARTH + H_PARK * 1000)) / 1e6
        measured.append({'payload_t': point['payload_t'], 'remaining_dv_reference_m_s': dv,
                         'c3_reference': c3, 'observed_orbit_alt_km': point['orbit_alt_km']})
    support = data['calibration_support']
    low, high = support['measured_payload_min_t'], support['measured_payload_max_t']
    traces = []
    for name, supported, color, dash in [('模型曲线 · 已有校准载荷范围内', True, '#2563eb', 'solid'),
                                         ('模型外推 · 需要飞行验证', False, '#d97706', 'dash')]:
        x, y = [], []
        for pt in data['c3_mass_curve']:
            inside = low <= pt['payload'] <= high
            x.append(pt['c3'] if inside == supported else None)
            y.append(pt['payload'] if inside == supported else None)
        traces.append({'type': 'scatter', 'mode': 'lines', 'name': name, 'x': x, 'y': y,
                       'line': {'color': color, 'dash': dash, 'width': 3}, 'connectgaps': False})
    traces.append({'type': 'scatter', 'mode': 'markers', 'name': '已有校准点（原始记录未回填）',
                   'x': [p['c3_reference'] for p in measured], 'y': [p['payload_t'] for p in measured],
                   'marker': {'size': 10, 'color': '#111827'}})
    evidence = {'config_path': str(config_path.resolve()), 'config_sha256': hashlib.sha256(raw).hexdigest(),
                'installed_engines': engines, 'calibration': support, 'measured_points': measured,
                'limitations': ['三点二次拟合没有独立验证或统计误差条',
                                '质量、发动机数量和飞行校准由现有配置提供，并非此次游戏运行时实测',
                                '采用理想燃烧及经验上升损失；未仿真 FAR 气动力、有限推力或级寿命']}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [('curve.json', data), ('evidence.json', evidence)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'curve.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['c3_km2_s2', 'payload_t', 'within_measured_payload_range'])
        for point in data['c3_mass_curve']:
            writer.writerow([point['c3'], point['payload'], low <= point['payload'] <= high])
    layout = {'title': {'text': cfg['rocket']['name'] + ' · C3–载荷曲线'},
              'xaxis': {'title': {'text': 'C3 (km²/s²)'}}, 'yaxis': {'title': {'text': '载荷 (t)'}, 'rangemode': 'tozero'},
              'template': 'plotly_white', 'legend': {'orientation': 'h', 'y': -0.2},
              'margin': {'b': 110}, 'hovermode': 'closest'}
    # Embedded local Plotly makes the deliverable work without network access.
    plotly = (ROOT / 'static/plotly.min.js').read_text(encoding='utf-8')
    safe = lambda value: json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')
    document = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>C3–载荷曲线</title><style>body{font:16px system-ui;margin:24px;color:#172554}'
                '#curve{width:100%;min-width:0;height:70vh;min-height:460px;overflow:hidden}p{max-width:1000px;line-height:1.7}</style>'
                '<h1>' + html.escape(cfg['rocket']['name']) + '</h1>'
                f'<p>参考：220 km、19.81°；已有校准载荷范围 {low:g}–{high:g} t。'
                '蓝线为已有校准载荷范围内的模型推算，橙色虚线为外推。黑点为现有校准点，原始飞行记录尚未回填；'
                '不同原始轨道高度已按同一经验模型换算。三点拟合不构成独立精度验证。</p>'
                '<div id="curve"></div><script>' + plotly + '</script><script>Plotly.newPlot("curve",'
                + safe(traces) + ',' + safe(layout) + ',{responsive:true,displaylogo:false});</script>'
                '<p>发动机参数取自指定游戏安装的最终补丁配置；火箭质量、数量和校准来自现有 YAML。'
                '完整指纹与模型范围见同目录 evidence.json；图表可缩放并导出 PNG。</p>')
    (output / 'C3_mass.html').write_text(document, encoding='utf-8')
    return {'plot': str((output / 'C3_mass.html').resolve()), 'calibration': support,
            'engine_source_sha256': engines['source']['sha256']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rocket', type=Path)
    parser.add_argument('--ksp-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export_curve(args.rocket, args.output, args.ksp_root), ensure_ascii=False, indent=2))
