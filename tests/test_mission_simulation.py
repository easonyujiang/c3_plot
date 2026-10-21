import json
import unittest

from c3_mass.mission_simulation import (
    BurnRequirement,
    BurnResult,
    DataQuality,
    FailureCode,
    IgnitionKind,
    MissionSimulationResult,
    StageState,
    to_dict,
)


def _burn(burn_id='b1', order=1, target_dv_m_s=100.0, ignition_kind=IgnitionKind.FIRST,
          success=True, failure_code=FailureCode.NONE, failure_reason_zh='',
          data_quality=DataQuality.EXACT, **kwargs):
    return BurnResult(
        burn_id=burn_id, order=order, target_dv_m_s=target_dv_m_s,
        ignition_kind=ignition_kind, success=success, failure_code=failure_code,
        failure_reason_zh=failure_reason_zh, data_quality=data_quality,
        **kwargs,
    )


class BurnRequirementTests(unittest.TestCase):
    def test_burn_requirement_has_no_solver_result_fields(self):
        fields = set(BurnRequirement.__dataclass_fields__)
        for forbidden in ('is_first_ignition', 'restart_allowed', 'success',
                          'actual_dv_m_s', 'propellant_consumed_t', 'stage_index'):
            self.assertNotIn(forbidden, fields)
        req = BurnRequirement(
            burn_id='b1', order=1, target_dv_m_s=2451.0, source='mission_model')
        d = req.to_dict()
        for forbidden in ('is_first_ignition', 'restart_allowed', 'success',
                          'actual_dv_m_s', 'propellant_consumed_t'):
            self.assertNotIn(forbidden, d)
        self.assertEqual(d['target_dv_m_s'], 2451.0)

    def test_from_dict_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            BurnRequirement.from_dict({
                'burn_id': 'b', 'order': 1, 'target_dv_m_s': 1.0, 'source': 'm',
                'is_first_ignition': True,
            })


class SerializationTests(unittest.TestCase):
    def test_all_structures_convert_to_json(self):
        req = BurnRequirement(burn_id='b1', order=1, target_dv_m_s=2451.0, source='mission_model')
        stage = StageState(
            stage_index=1, wet_mass_t=19.467, dry_mass_t=0.899,
            propellant_remaining_t=18.568, isp_vac_s=300.0, multi_ignition=True)
        burn = _burn(burn_id='b2', order=2, target_dv_m_s=2451.0,
                     actual_dv_m_s=2450.0, stage_index=1, mass_before_t=20.0,
                     mass_after_t=12.0, propellant_consumed_t=8.0,
                     propellant_remaining_t=10.0,
                     data_quality=DataQuality.ESTIMATED)
        mission = MissionSimulationResult(
            rocket_ref='Neutron I 2630', payload_t=1.0, success=True,
            failure_code=FailureCode.NONE, failure_reason_zh='',
            data_quality=DataQuality.ESTIMATED,
            burns=[burn], warnings=['w'], metadata={'k': 'v'})
        for obj in (req, stage, burn, mission):
            d = to_dict(obj)
            self.assertIsInstance(d, dict)
            parsed = json.loads(json.dumps(d, ensure_ascii=False))
            self.assertIsInstance(parsed, dict)
            self.assertEqual(obj.to_dict(), d)

    def test_enums_serialize_to_stable_lowercase_strings(self):
        for code in FailureCode:
            self.assertEqual(code.value, code.value.lower())
        for quality in DataQuality:
            self.assertEqual(quality.value, quality.value.lower())
        for kind in IgnitionKind:
            self.assertEqual(kind.value, kind.value.lower())
        burn = _burn(ignition_kind=IgnitionKind.RESTART, stage_index=1, success=False,
                     failure_code=FailureCode.PROPELLANT_INSUFFICIENT,
                     failure_reason_zh='推进剂不足',
                     data_quality=DataQuality.INSUFFICIENT)
        d = burn.to_dict()
        self.assertEqual(d['ignition_kind'], 'restart')
        self.assertEqual(d['failure_code'], 'propellant_insufficient')
        self.assertEqual(d['data_quality'], 'insufficient')
        self.assertNotIsInstance(d['ignition_kind'], IgnitionKind)

    def test_burn_result_distinguishes_ignition_kinds(self):
        for kind, expected in (
                (IgnitionKind.FIRST, 'first'),
                (IgnitionKind.RESTART, 'restart'),
                (IgnitionKind.UNASSIGNED, 'unassigned')):
            kwargs = {'stage_index': 1} if kind is not IgnitionKind.UNASSIGNED else {}
            burn = _burn(ignition_kind=kind, **kwargs)
            self.assertEqual(burn.to_dict()['ignition_kind'], expected)

    def test_failure_code_and_chinese_reason_coexist(self):
        burn = _burn(ignition_kind=IgnitionKind.RESTART, stage_index=1, success=False,
                     failure_code=FailureCode.RESTART_UNSUPPORTED,
                     failure_reason_zh='上面级不支持多次点火')
        d = burn.to_dict()
        self.assertEqual(d['failure_code'], 'restart_unsupported')
        self.assertEqual(d['failure_reason_zh'], '上面级不支持多次点火')
        self.assertFalse(d['success'])

    def test_nested_mission_result_serializes(self):
        mission = MissionSimulationResult(
            rocket_ref='R1', payload_t=1.5, success=False,
            failure_code=FailureCode.PROPELLANT_INSUFFICIENT,
            failure_reason_zh='推进剂不足',
            data_quality=DataQuality.ESTIMATED,
            burns=[
                _burn(burn_id='park', order=1, target_dv_m_s=0.0,
                      ignition_kind=IgnitionKind.UNASSIGNED),
                _burn(burn_id='depart', order=2, target_dv_m_s=2451.0,
                      ignition_kind=IgnitionKind.FIRST, stage_index=1,
                      actual_dv_m_s=2000.0, success=False,
                      failure_code=FailureCode.PROPELLANT_INSUFFICIENT,
                      failure_reason_zh='推进剂不足',
                      data_quality=DataQuality.ESTIMATED),
            ],
            warnings=['w1', 'w2'],
            metadata={'mods': ['SSTU'], 'nested': {'a': [1, 2]}},
        )
        d = mission.to_dict()
        self.assertEqual(len(d['burns']), 2)
        self.assertIsInstance(d['burns'][0], dict)
        self.assertIsInstance(d['burns'][1]['ignition_kind'], str)
        self.assertEqual(d['failure_code'], 'propellant_insufficient')
        self.assertEqual(d['warnings'], ['w1', 'w2'])
        self.assertEqual(d['metadata']['mods'], ['SSTU'])
        s = json.dumps(d, ensure_ascii=False)
        self.assertIn('"failure_code": "propellant_insufficient"', s)
        self.assertIn('"ignition_kind": "first"', s)
        self.assertIn('推进剂不足', s)

    def test_round_trip_through_from_dict(self):
        mission = MissionSimulationResult(
            rocket_ref='R1', payload_t=1.5, success=False,
            failure_code=FailureCode.PROPELLANT_INSUFFICIENT,
            failure_reason_zh='推进剂不足',
            data_quality=DataQuality.ESTIMATED,
            burns=[_burn(burn_id='depart', order=2, target_dv_m_s=2451.0,
                         ignition_kind=IgnitionKind.RESTART, stage_index=1)],
            warnings=['w'],
            metadata={'k': 'v'},
        )
        restored = MissionSimulationResult.from_dict(json.loads(json.dumps(mission.to_dict())))
        self.assertEqual(restored.to_dict(), mission.to_dict())
        self.assertIsInstance(restored.burns[0], BurnResult)


class ImmutabilityTests(unittest.TestCase):
    def test_mutating_serialized_output_does_not_mutate_source(self):
        md = {'engine': 'Merlin-1B', 'count': 3}
        req = BurnRequirement(
            burn_id='b1', order=1, target_dv_m_s=100.0, source='mission_model', metadata=md)
        d = req.to_dict()
        d['metadata']['engine'] = 'MUTATED'
        d['metadata']['extra'] = 1
        self.assertEqual(req.metadata['engine'], 'Merlin-1B')
        self.assertNotIn('extra', req.metadata)

        md['engine'] = 'MUTATED-2'
        self.assertEqual(req.metadata['engine'], 'Merlin-1B')

        mission = MissionSimulationResult(
            rocket_ref='R', payload_t=1.0, success=True,
            failure_code=FailureCode.NONE, failure_reason_zh='',
            data_quality=DataQuality.EXACT,
            burns=[], warnings=['w'], metadata={'nested': {'a': [1, 2]}})
        d2 = mission.to_dict()
        d2['metadata']['nested']['a'].append(99)
        d2['warnings'].append('mutated')
        self.assertEqual(mission.metadata['nested']['a'], [1, 2])
        self.assertEqual(mission.warnings, ['w'])

    def test_caller_supplied_metadata_is_copied_on_construction(self):
        md = {'a': {'b': [1, 2]}}
        stage = StageState(
            stage_index=0, wet_mass_t=10.0, dry_mass_t=2.0,
            propellant_remaining_t=8.0, metadata=md)
        md['a']['b'].append(99)
        md['c'] = 1
        self.assertEqual(stage.metadata, {'a': {'b': [1, 2]}})


class ValidationTests(unittest.TestCase):
    def test_negative_mass_rejected(self):
        with self.assertRaises(ValueError):
            StageState(stage_index=0, wet_mass_t=-5.0, dry_mass_t=1.0,
                       propellant_remaining_t=0.0)
        with self.assertRaises(ValueError):
            BurnResult(burn_id='b', order=1, target_dv_m_s=100.0,
                       propellant_consumed_t=-1.0,
                       ignition_kind=IgnitionKind.FIRST, success=True,
                       failure_code=FailureCode.NONE, failure_reason_zh='',
                       data_quality=DataQuality.EXACT)

    def test_dry_mass_above_wet_mass_rejected(self):
        with self.assertRaises(ValueError):
            StageState(stage_index=0, wet_mass_t=1.0, dry_mass_t=2.0,
                       propellant_remaining_t=0.0)

    def test_propellant_beyond_capacity_rejected(self):
        with self.assertRaises(ValueError):
            StageState(stage_index=0, wet_mass_t=10.0, dry_mass_t=2.0,
                       propellant_remaining_t=9.0)

    def test_negative_dv_rejected(self):
        with self.assertRaises(ValueError):
            BurnRequirement(burn_id='b', order=1, target_dv_m_s=-1.0, source='m')
        with self.assertRaises(ValueError):
            _burn(target_dv_m_s=-1.0)
        with self.assertRaises(ValueError):
            _burn(actual_dv_m_s=-2.0)

    def test_illegal_order_rejected(self):
        with self.assertRaises(ValueError):
            BurnRequirement(burn_id='b', order=0, target_dv_m_s=1.0, source='m')
        with self.assertRaises(ValueError):
            _burn(order=-1)

    def test_other_illegal_values_rejected(self):
        with self.assertRaises(ValueError):
            BurnRequirement(burn_id='', order=1, target_dv_m_s=1.0, source='m')
        with self.assertRaises(ValueError):
            BurnRequirement(burn_id='b', order=1, target_dv_m_s=1.0, source='m',
                            metadata=[1, 2])
        with self.assertRaises(ValueError):
            MissionSimulationResult(rocket_ref='R', payload_t=-1.0, success=True,
                                    failure_code=FailureCode.NONE,
                                    failure_reason_zh='',
                                    data_quality=DataQuality.EXACT)
        with self.assertRaises(ValueError):
            MissionSimulationResult(rocket_ref='', payload_t=1.0, success=True,
                                    failure_code=FailureCode.NONE,
                                    failure_reason_zh='',
                                    data_quality=DataQuality.EXACT)
        with self.assertRaises(ValueError):
            _burn(ignition_kind='bogus')
        with self.assertRaises(ValueError):
            _burn(failure_code='bogus')
        with self.assertRaises(ValueError):
            MissionSimulationResult(rocket_ref='R', payload_t=1.0, success=True,
                                    failure_code=FailureCode.NONE,
                                    failure_reason_zh='',
                                    data_quality=DataQuality.EXACT,
                                    burns=['not-a-burn'])


class MetadataJsonTests(unittest.TestCase):
    def test_metadata_rejects_plain_object_at_construction(self):
        with self.assertRaises(ValueError) as ctx:
            BurnRequirement(burn_id='b', order=1, target_dv_m_s=1.0,
                            source='mission_model', metadata={'bad': object()})
        self.assertIn('metadata.bad is not JSON-compatible', str(ctx.exception))

    def test_metadata_nested_object_reports_field_path(self):
        with self.assertRaises(ValueError) as ctx:
            BurnRequirement(burn_id='b', order=1, target_dv_m_s=1.0,
                            source='mission_model',
                            metadata={'engine': {'value': object()}})
        self.assertIn('metadata.engine.value is not JSON-compatible',
                      str(ctx.exception))

    def test_metadata_list_element_object_reports_path(self):
        with self.assertRaises(ValueError) as ctx:
            BurnRequirement(burn_id='b', order=1, target_dv_m_s=1.0,
                            source='mission_model',
                            metadata={'parts': [1, object()]})
        self.assertIn('metadata.parts[1] is not JSON-compatible', str(ctx.exception))

    def test_metadata_rejects_set_and_bytes(self):
        for bad in ({1, 2}, b'bytes'):
            with self.assertRaises(ValueError) as ctx:
                BurnRequirement(burn_id='b', order=1, target_dv_m_s=1.0,
                                source='mission_model', metadata={'bad': bad})
            self.assertIn('metadata.bad is not JSON-compatible', str(ctx.exception))

    def test_metadata_rejects_non_finite_floats(self):
        for bad in (float('nan'), float('inf'), float('-inf')):
            with self.assertRaises(ValueError) as ctx:
                BurnRequirement(burn_id='b', order=1, target_dv_m_s=1.0,
                                source='mission_model', metadata={'x': bad})
            self.assertIn('metadata.x must be a finite number', str(ctx.exception))

    def test_metadata_rejects_non_string_keys(self):
        with self.assertRaises(ValueError) as ctx:
            BurnRequirement(burn_id='b', order=1, target_dv_m_s=1.0,
                            source='mission_model', metadata={1: 'x'})
        self.assertIn('metadata keys must be strings', str(ctx.exception))

    def test_metadata_accepts_json_types_and_normalizes_tuples(self):
        req = BurnRequirement(
            burn_id='b', order=1, target_dv_m_s=1.0, source='mission_model',
            metadata={
                'none': None, 'flag': True, 'text': 's', 'n': 3,
                'f': 1.25, 'lst': [1, 'a'], 'tup': (1, (2, 3)), 'd': {'k': 'v'},
            })
        self.assertEqual(req.metadata['tup'], [1, [2, 3]])
        self.assertIsInstance(req.metadata['tup'], list)
        json.dumps(req.to_dict(), ensure_ascii=False)

    def test_to_dict_rejects_object_assigned_after_construction(self):
        req = BurnRequirement(burn_id='b', order=1, target_dv_m_s=1.0,
                              source='mission_model', metadata={'ok': 1})
        req.metadata['bad'] = object()
        with self.assertRaises(ValueError) as ctx:
            req.to_dict()
        self.assertIn('metadata.bad is not JSON-compatible', str(ctx.exception))

    def test_to_dict_rejects_object_in_nested_structure(self):
        mission = MissionSimulationResult(
            rocket_ref='R', payload_t=1.0, success=True,
            failure_code=FailureCode.NONE, failure_reason_zh='',
            data_quality=DataQuality.EXACT,
            burns=[_burn(burn_id='park', order=1, target_dv_m_s=0.0,
                         ignition_kind=IgnitionKind.UNASSIGNED)],
            metadata={})
        mission.burns[0].metadata['x'] = object()
        with self.assertRaises(ValueError) as ctx:
            mission.to_dict()
        self.assertIn('burns[0].metadata.x is not JSON-compatible', str(ctx.exception))


class BurnResultConsistencyTests(unittest.TestCase):
    def test_success_requires_none_failure_code(self):
        with self.assertRaises(ValueError):
            _burn(stage_index=1, success=True,
                  failure_code=FailureCode.PROPELLANT_INSUFFICIENT,
                  failure_reason_zh='推进剂不足')

    def test_failure_requires_non_none_code_and_reason(self):
        with self.assertRaises(ValueError):
            _burn(stage_index=1, success=False, failure_code=FailureCode.NONE,
                  failure_reason_zh='')
        with self.assertRaises(ValueError):
            _burn(stage_index=1, success=False,
                  failure_code=FailureCode.PROPELLANT_INSUFFICIENT,
                  failure_reason_zh='   ')

    def test_first_and_restart_require_stage_index(self):
        with self.assertRaises(ValueError):
            _burn(ignition_kind=IgnitionKind.FIRST)
        with self.assertRaises(ValueError):
            _burn(ignition_kind=IgnitionKind.RESTART)

    def test_unassigned_requires_no_stage_index(self):
        with self.assertRaises(ValueError):
            _burn(ignition_kind=IgnitionKind.UNASSIGNED, stage_index=1)

    def test_calibration_covered_burn_is_legal(self):
        burn = _burn(ignition_kind=IgnitionKind.UNASSIGNED, success=True,
                     failure_code=FailureCode.NONE)
        d = burn.to_dict()
        self.assertIsNone(d['stage_index'])
        self.assertTrue(d['success'])
        self.assertEqual(d['failure_code'], 'none')

    def test_successful_first_ignition_is_legal(self):
        _burn(ignition_kind=IgnitionKind.FIRST, stage_index=1, success=True,
              failure_code=FailureCode.NONE)

    def test_successful_restart_is_legal(self):
        _burn(ignition_kind=IgnitionKind.RESTART, stage_index=1, success=True,
              failure_code=FailureCode.NONE)

    def test_unassigned_failure_is_legal(self):
        burn = _burn(ignition_kind=IgnitionKind.UNASSIGNED, success=False,
                     failure_code=FailureCode.NO_STAGE_ASSIGNED,
                     failure_reason_zh='未分配到执行级')
        self.assertIsNone(burn.to_dict()['stage_index'])

    def test_mass_after_beyond_mass_before_rejected(self):
        with self.assertRaises(ValueError):
            _burn(stage_index=1, mass_before_t=10.0, mass_after_t=12.0)

    def test_actual_dv_over_target_is_allowed(self):
        burn = _burn(stage_index=1, target_dv_m_s=2451.0, actual_dv_m_s=3000.0,
                     success=True, failure_code=FailureCode.NONE)
        self.assertGreater(burn.to_dict()['actual_dv_m_s'],
                           burn.to_dict()['target_dv_m_s'])


class MissionResultConsistencyTests(unittest.TestCase):
    def test_mission_success_requires_none_failure_code(self):
        with self.assertRaises(ValueError):
            MissionSimulationResult(rocket_ref='R', payload_t=1.0, success=True,
                                    failure_code=FailureCode.DATA_INSUFFICIENT,
                                    failure_reason_zh='数据不足',
                                    data_quality=DataQuality.ESTIMATED)

    def test_mission_failure_requires_code_and_reason(self):
        with self.assertRaises(ValueError):
            MissionSimulationResult(rocket_ref='R', payload_t=1.0, success=False,
                                    failure_code=FailureCode.NONE,
                                    failure_reason_zh='',
                                    data_quality=DataQuality.ESTIMATED)
        with self.assertRaises(ValueError):
            MissionSimulationResult(rocket_ref='R', payload_t=1.0, success=False,
                                    failure_code=FailureCode.DATA_INSUFFICIENT,
                                    failure_reason_zh='',
                                    data_quality=DataQuality.ESTIMATED)

    def test_mission_does_not_auto_derive_from_burns(self):
        mission = MissionSimulationResult(
            rocket_ref='R', payload_t=1.0, success=True,
            failure_code=FailureCode.NONE, failure_reason_zh='',
            data_quality=DataQuality.EXACT,
            burns=[_burn(burn_id='b', order=1, target_dv_m_s=0.0,
                         ignition_kind=IgnitionKind.UNASSIGNED, success=False,
                         failure_code=FailureCode.PROPELLANT_INSUFFICIENT,
                         failure_reason_zh='推进剂不足')])
        d = mission.to_dict()
        self.assertTrue(d['success'])
        self.assertEqual(d['failure_code'], 'none')
        self.assertFalse(d['burns'][0]['success'])


class StageStateIspVacTests(unittest.TestCase):
    def test_isp_vac_none_accepted(self):
        stage = StageState(stage_index=0, wet_mass_t=10.0, dry_mass_t=2.0,
                           propellant_remaining_t=8.0, isp_vac_s=None)
        self.assertIsNone(stage.to_dict()['isp_vac_s'])

    def test_isp_vac_zero_rejected(self):
        with self.assertRaises(ValueError):
            StageState(stage_index=0, wet_mass_t=10.0, dry_mass_t=2.0,
                       propellant_remaining_t=8.0, isp_vac_s=0.0)

    def test_isp_vac_negative_rejected(self):
        with self.assertRaises(ValueError):
            StageState(stage_index=0, wet_mass_t=10.0, dry_mass_t=2.0,
                       propellant_remaining_t=8.0, isp_vac_s=-300.0)

    def test_isp_vac_non_finite_rejected(self):
        for bad in (float('nan'), float('inf'), float('-inf')):
            with self.assertRaises(ValueError):
                StageState(stage_index=0, wet_mass_t=10.0, dry_mass_t=2.0,
                           propellant_remaining_t=8.0, isp_vac_s=bad)

    def test_isp_vac_positive_accepted(self):
        stage = StageState(stage_index=0, wet_mass_t=10.0, dry_mass_t=2.0,
                           propellant_remaining_t=8.0, isp_vac_s=300.0)
        self.assertEqual(stage.to_dict()['isp_vac_s'], 300.0)


if __name__ == '__main__':
    unittest.main()
