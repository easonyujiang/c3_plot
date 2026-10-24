"""Build a versioned RocketProfile JSON from a VAB craft and editor snapshot.

This is Phase A: it does not compute capacity or write the database.  It reuses
the read-only import/evidence pipeline and emits the profile consumed by later
simulation and calibration layers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.paths import ROOT
from server.profile import build_rocket_profile


def _loadmeta(path: Path):
    sibling = path.with_suffix('.loadmeta')
    if not sibling.is_file():
        return None
    from server.import_service import _loadmeta_metadata
    return _loadmeta_metadata(sibling.read_text(encoding='utf-8-sig'))


def build_draft(craft_path: Path, ksp_root: Path):
    from server.vab_parser import parse_craft
    from server.ksp_install_index import build_ksp_index
    from server.ksp_part_matcher import enrich_craft_draft
    from server.ksp_runtime_fingerprints import ksp_runtime_provenance
    from server.ksp_mass_evidence import extract_part_mass_evidence
    from server.ksp_craft_mass_reconciliation import reconcile_craft_mass
    from server.editor_snapshot import read_snapshot, automatic_stages

    raw = craft_path.read_bytes()
    draft = parse_craft(raw.decode('utf-8-sig'), craft_path.name)
    index = build_ksp_index(ksp_root)
    if index.status != 'ok':
        raise ValueError(f'KSP index unavailable: {index.status}')
    draft = enrich_craft_draft(draft, index)
    provenance = ksp_runtime_provenance(ksp_root)
    draft['ksp_runtime_provenance'] = provenance
    try:
        draft['mass_evidence'] = extract_part_mass_evidence(
            draft, index, installation_match=draft.get('installation_match'),
            provenance=provenance)
    except Exception as exc:  # evidence must not hide the profile
        draft['mass_evidence'] = None
        draft.setdefault('structured_warnings', []).append({
            'code': 'mass_evidence_failed', 'severity': 'warning',
            'message': f'逐部件质量证据提取失败：{exc}',
        })
    loadmeta = _loadmeta(craft_path)
    try:
        draft['mass_reconciliation'] = reconcile_craft_mass(
            draft, draft.get('mass_evidence'), loadmeta,
            loadmeta_filename=craft_path.with_suffix('.loadmeta').name)
    except Exception as exc:
        draft['mass_reconciliation'] = None
        draft.setdefault('structured_warnings', []).append({
            'code': 'mass_reconciliation_failed', 'severity': 'warning',
            'message': f'整箭质量对账异常：{exc}',
        })
    try:
        snapshot = read_snapshot(ksp_root, raw)
    except (ValueError, OSError, KeyError) as exc:
        draft['automatic_identification'] = {
            'status': 'snapshot_invalid',
            'message': str(exc),
        }
        return draft
    if snapshot is not None:
        stages, topology = automatic_stages(snapshot)
        draft['stages'] = stages
        stage_by_part = {part_id: stage_index
                         for stage_index, stage in enumerate(stages)
                         for part_id in stage.get('part_ids') or []}
        editor_parts = []
        for part_id, node in snapshot['parts'].items():
            name = node.value('name')
            editor_parts.append({
                'part_id': part_id,
                'name': name,
                'stage_index': stage_by_part.get(part_id),
                'role': 'fairing' if str(name).startswith('KzProcFairingSide1') else 'unknown',
                'dry_mass_t': node.value('settled_dry_mass_t'),
                'resource_mass_t': node.value('resource_mass_t'),
                'wet_mass_t': node.value('current_wet_mass_t'),
                'shielded': node.value('shielded_from_airstream') == 'True',
            })
        draft['editor_parts'] = editor_parts
        draft['automatic_identification'] = {
            'status': 'verified_editor_snapshot',
            'source': snapshot['source'],
            'topology': topology,
            'editor_dry_mass_t': snapshot['dry_mass_t'],
            'editor_resource_mass_t': snapshot['resource_mass_t'],
        }
        draft['requires_mass_confirmation'] = False
    else:
        draft['automatic_identification'] = {
            'status': 'snapshot_missing',
            'message': '未找到编辑器快照；质量参数只能标记为未确认',
        }
    return draft


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--craft', type=Path, required=True, help='Path to a .craft file')
    parser.add_argument('--ksp-root', type=Path, help='KSP install root; defaults to C3_MASS_KSP_ROOT')
    parser.add_argument('--output', type=Path, help='Output JSON; defaults to output/profiles/<stem>.json')
    parser.add_argument('--payload-parts', help='Comma-separated part names to mark as payload')
    args = parser.parse_args()
    craft = args.craft.resolve()
    if not craft.is_file():
        parser.error(f'craft not found: {craft}')
    if args.ksp_root:
        ksp_root = args.ksp_root.resolve()
    else:
        from c3_mass.game_data import ksp_root as configured_root
        ksp_root = configured_root()
    draft = build_draft(craft, ksp_root)
    if args.payload_parts:
        names = {name.strip() for name in args.payload_parts.split(',') if name.strip()}
        draft['payload_part_ids'] = [part['part_id'] for part in draft.get('editor_parts') or []
                                     if part.get('name') in names]
        if not draft['payload_part_ids']:
            parser.error('no editor part matched --payload-parts')
    profile = build_rocket_profile(draft)
    output = args.output or (ROOT / 'output' / 'profiles' / (craft.stem + '.json'))
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(profile.to_json() + '\n', encoding='utf-8')
    print(json.dumps({
        'profile_id': profile.profile_id,
        'name': profile.name,
        'stages': len(profile.stages),
        'parameters': len(profile.parameters),
        'craft_sha256': profile.source.get('craft_sha256'),
        'snapshot': profile.automatic_identification.get('status'),
        'output': str(output),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
