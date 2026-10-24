import json
import unittest

from server.profile import (
    CapacityPoint,
    Encyclopedia,
    TrialResult,
    TrialSpec,
    build_rocket_profile,
)


def draft():
    return {
        'rocket': {'name': 'Test Rocket'},
        'stages': [{
            'wet_mass_t': 10.0,
            'dry_mass_t': 2.0,
            'runtime_performance': {'thrust': 100.0, 'isp_vac': 300.0, 'isp_sl': 250.0},
            'engine_groups': [{'cfg': 'engine.cfg', 'count': 1,
                               'part_id': 'p1', 'engine_id': 'main'}],
            'tank_states': [{'part_id': 'p1', 'module': 'ModuleFuelTanks',
                             'fields': {'type': 'default', 'volume': '100'}}],
            'runtime_source': {
                'ksp_root': '/tmp/ksp',
                'files': [{'path': 'GameData/ModuleManager.ConfigCache', 'sha256': 'cache'}],
            },
            'part_ids': ['p1', 'p2', 'p3'],
            'multi_ignition': True,
            'max_ignitions': 2,
        }],
        'editor_parts': [
            {'part_id': 'p1', 'name': 'engineTank', 'stage_index': 0, 'role': 'stage',
             'dry_mass_t': 2.0, 'resource_mass_t': 8.0, 'wet_mass_t': 10.0,
             'shielded': False},
            {'part_id': 'p2', 'name': 'payloadBox', 'stage_index': 0, 'role': 'unknown',
             'dry_mass_t': 0.2, 'resource_mass_t': 0.8, 'wet_mass_t': 1.0,
             'shielded': False},
            {'part_id': 'p3', 'name': 'KzProcFairingSide1', 'stage_index': 0, 'role': 'fairing',
             'dry_mass_t': 0.2, 'resource_mass_t': 0.0, 'wet_mass_t': 0.2,
             'shielded': False},
        ],
        'payload_part_ids': ['p2'],
        'source': {
            'filename': 'Test.craft',
            'format': 'ksp-craft',
            'part_count': 1,
            'root_info': {'version': '1.12.5', 'mod_versions': 'A=1|B=2'},
        },
        'automatic_identification': {
            'status': 'verified_editor_snapshot',
            'source': {
                'ksp_root': '/tmp/ksp',
                'files': [
                    {'path': 'GameData/ModuleManager.ConfigCache', 'sha256': 'cache'},
                    {'path': 'GameData/MechJeb2/Plugins/MechJeb2.dll', 'sha256': 'mechjeb'},
                ],
                'craft_sha256': 'craft-hash',
                'snapshot_sha256': 'snapshot-hash',
            },
            'topology': {'mass_method': 'test'},
            'editor_dry_mass_t': 2.0,
            'editor_resource_mass_t': 8.0,
        },
        'mass_reconciliation': {'reconciliation_status': 'reconciled'},
        'structured_warnings': [],
    }


class ProfileContractTests(unittest.TestCase):
    def test_profile_keeps_stage_parameters_and_provenance(self):
        profile = build_rocket_profile(draft())
        self.assertEqual(profile.name, 'Test Rocket')
        self.assertEqual(profile.source['craft_sha256'], 'craft-hash')
        self.assertEqual(profile.environment.config_cache_sha256, 'cache')
        self.assertEqual(profile.environment.mechjeb_sha256, 'mechjeb')
        self.assertEqual(profile.environment.mod_versions, {'A': '1', 'B': '2'})
        self.assertEqual(len(profile.stages), 1)
        stage = profile.stages[0]
        self.assertAlmostEqual(stage.propellant_t, 8.0)
        self.assertEqual(stage.thrust_kN, 100.0)
        self.assertEqual(stage.confidence, 'exact')
        self.assertEqual(stage.source, 'runtime_editor_snapshot')
        self.assertEqual(stage.source_sha256, 'cache')
        keys = {entry.key: entry for entry in profile.parameters}
        self.assertEqual(keys['stage[0].wet_mass_t'].value, 10.0)
        self.assertEqual(keys['stage[0].wet_mass_t'].confidence, 'exact')
        self.assertEqual(keys['stage[0].engine[0].cfg'].value, 'engine.cfg')
        self.assertEqual(keys['stage[0].tank[0].volume'].value, 100.0)
        self.assertEqual(keys['environment.config_cache_sha256'].value, 'cache')
        self.assertEqual(len(profile.parts), 3)
        self.assertEqual(profile.payload['status'], 'declared')
        self.assertAlmostEqual(profile.payload['wet_mass_t'], 1.0)
        self.assertAlmostEqual(profile.fairings['wet_mass_t'], 0.2)
        self.assertTrue(profile.stages[0].payload_included)
        self.assertTrue(profile.stages[0].fairing_included)
        self.assertIn('part[p2].wet_mass_t', keys)
        self.assertEqual(profile.calibration, [])
        self.assertEqual(profile.capacity, [])
        json.dumps(profile.to_dict())

    def test_missing_snapshot_is_explicit_not_estimated(self):
        data = draft()
        data['automatic_identification'] = {'status': 'snapshot_missing',
                                             'message': 'no snapshot'}
        data['stages'] = []
        data['editor_parts'] = []
        data['payload_part_ids'] = []
        profile = build_rocket_profile(data)
        self.assertEqual(profile.automatic_identification['status'], 'snapshot_missing')
        self.assertEqual(profile.stages, [])
        self.assertFalse([entry for entry in profile.parameters
                          if entry.key.startswith('stage[')])
        self.assertTrue(any(entry.key == 'environment.craft_sha256'
                            for entry in profile.parameters))
        json.dumps(profile.to_dict())

    def test_trial_and_capacity_contracts_are_serializable(self):
        spec = TrialSpec(rocket_sha256='craft-hash', payload_t=5.0,
                         orbit={'apoapsis_km': 220, 'periapsis_km': 220,
                                'inclination_deg': 20})
        result = TrialResult(spec_id='t1', status='verified', remaining_dv_m_s=1200)
        point = CapacityPoint(orbit=spec.orbit, payload_t=5.0, status='verified',
                              estimate_t=5.0, confidence='measured')
        book = Encyclopedia(profile=build_rocket_profile(draft()).to_dict(),
                            capacity=[point], trials=[result])
        payload = book.to_dict()
        self.assertEqual(payload['capacity'][0]['status'], 'verified')
        self.assertEqual(payload['trials'][0]['remaining_dv_m_s'], 1200)
        json.dumps(payload)


if __name__ == '__main__':
    unittest.main()
