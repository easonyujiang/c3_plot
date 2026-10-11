import json
import math
import unittest

from mission_simulation import (
    BurnRequirement,
    DataQuality,
    FailureCode,
    IgnitionKind,
    StageState,
)
from mission_solver import (
    MASS_TOLERANCE_T,
    STANDARD_GRAVITY_M_S2,
    available_delta_v_m_s,
    execute_burn,
    required_propellant_t,
)


def _stage(propellant=20.0, isp=300.0, multi=False, count=0, attached=True,
           parallel=False, dry=5.0, wet=25.0, index=1):
    return StageState(
        stage_index=index,
        name='upper',
        wet_mass_t=wet,
        dry_mass_t=dry,
        propellant_remaining_t=propellant,
        isp_vac_s=isp,
        multi_ignition=multi,
        ignition_count=count,
        attached=attached,
        parallel=parallel,
        metadata={'role': 'upper'},
    )


def _req(target=400.0, burn_id='b1', source='mission_model'):
    return BurnRequirement(burn_id=burn_id, order=1, target_dv_m_s=target,
                           source=source)


class AvailableDeltaVTests(unittest.TestCase):
    def test_available_delta_v_known_value(self):
        stage = _stage(propellant=20.0, isp=300.0)
        expected = 300.0 * 9.80665 * math.log(100.0 / (100.0 - 20.0))
        self.assertAlmostEqual(available_delta_v_m_s(stage, 100.0), expected,
                               places=9)
        # hand-computed constant: 300 * 9.80665 * ln(1.25) ~= 656.487 m/s
        self.assertAlmostEqual(available_delta_v_m_s(stage, 100.0), 656.487,
                               delta=0.05)

    def test_available_delta_v_zero_propellant_is_zero(self):
        stage = _stage(propellant=0.0)
        self.assertEqual(available_delta_v_m_s(stage, 100.0), 0.0)

    def test_available_rejects_invalid_stage_inputs(self):
        with self.assertRaises(ValueError):
            available_delta_v_m_s(_stage(attached=False), 100.0)
        with self.assertRaises(ValueError):
            available_delta_v_m_s(_stage(parallel=True), 100.0)
        with self.assertRaises(ValueError):
            available_delta_v_m_s(_stage(isp=None), 100.0)
        with self.assertRaises(ValueError):
            available_delta_v_m_s(_stage(isp=0.0), 100.0)
        with self.assertRaises(ValueError):
            available_delta_v_m_s(_stage(), 0.0)
        with self.assertRaises(ValueError):
            available_delta_v_m_s(_stage(), float('nan'))
        with self.assertRaises(ValueError):
            available_delta_v_m_s(_stage(), 24.0)  # dry 5 + prop 20 = 25

    def test_tiny_propellant_yields_positive_dv_via_log1p(self):
        stage = _stage(propellant=1e-15, isp=300.0)
        dv = available_delta_v_m_s(stage, 100.0)
        expected = 300.0 * 9.80665 * (-math.log1p(-1e-15 / 100.0))
        self.assertAlmostEqual(dv, expected, delta=abs(expected) * 1e-9 + 1e-30)
        self.assertGreater(dv, 0.0)

    def test_regular_ratio_results_unchanged(self):
        stage = _stage(propellant=20.0, isp=300.0)
        old_style = 300.0 * 9.80665 * math.log(100.0 / 80.0)
        self.assertAlmostEqual(available_delta_v_m_s(stage, 100.0), old_style,
                               places=12)

    def test_invalid_propellant_fraction_rejected(self):
        stage = _stage(propellant=20.0, isp=300.0)
        stage.propellant_remaining_t = -1.0  # bypass constructor validation
        with self.assertRaises(ValueError):
            available_delta_v_m_s(stage, 100.0)
        stage.propellant_remaining_t = 150.0
        with self.assertRaises(ValueError):
            available_delta_v_m_s(stage, 100.0)


class RequiredPropellantTests(unittest.TestCase):
    def test_required_propellant_known_value(self):
        stage = _stage(propellant=20.0, isp=300.0)
        expected = 100.0 * (1.0 - math.exp(-500.0 / (300.0 * 9.80665)))
        self.assertAlmostEqual(required_propellant_t(stage, 100.0, 500.0),
                               expected, places=9)
        # hand-computed constant ~= 15.629 t
        self.assertAlmostEqual(required_propellant_t(stage, 100.0, 500.0),
                               15.629, delta=0.01)

    def test_formulas_are_inverses(self):
        stage = _stage(propellant=20.0, isp=300.0)
        dv = available_delta_v_m_s(stage, 100.0)
        prop = required_propellant_t(stage, 100.0, dv)
        self.assertAlmostEqual(prop, 20.0, delta=1e-6)

        stage2 = _stage(propellant=10.0, isp=250.0)
        prop2 = required_propellant_t(stage2, 50.0, 500.0)
        full = _stage(propellant=prop2, isp=250.0)
        dv2 = available_delta_v_m_s(full, 50.0)
        self.assertAlmostEqual(dv2, 500.0, delta=1e-3)

    def test_required_rejects_zero_and_negative_target(self):
        stage = _stage()
        for bad in (0.0, -1.0, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                required_propellant_t(stage, 100.0, bad)

    def test_required_rejects_invalid_stage_inputs(self):
        with self.assertRaises(ValueError):
            required_propellant_t(_stage(parallel=True), 100.0, 300.0)
        with self.assertRaises(ValueError):
            required_propellant_t(_stage(isp=None), 100.0, 300.0)

    def test_tiny_targets_yield_positive_propellant_via_expm1(self):
        stage = _stage(propellant=20.0, isp=300.0)
        previous = 0.0
        for target in (1e-14, 1e-12, 1e-10, 1e-6):
            required = required_propellant_t(stage, 100.0, target)
            expected = 100.0 * (-math.expm1(-target / (300.0 * 9.80665)))
            self.assertAlmostEqual(required, expected,
                                   delta=abs(expected) * 1e-9 + 1e-30)
            self.assertGreater(required, 0.0)
            self.assertGreater(required, previous)
            previous = required

    def test_regular_dv_results_unchanged(self):
        stage = _stage(propellant=20.0, isp=300.0)
        old_style = 100.0 * (1.0 - math.exp(-400.0 / (300.0 * 9.80665)))
        self.assertAlmostEqual(required_propellant_t(stage, 100.0, 400.0),
                               old_style, places=12)

    def test_tiny_targets_inverse_round_trip(self):
        stage = _stage(propellant=20.0, isp=300.0)
        dvs = []
        for target in (1e-14, 1e-12, 1e-10, 1e-6):
            prop = required_propellant_t(stage, 100.0, target)
            self.assertGreater(prop, 0.0)
            full = _stage(propellant=prop, isp=300.0)
            dv = available_delta_v_m_s(full, 100.0)
            self.assertAlmostEqual(dv, target,
                                   delta=abs(target) * 1e-6 + 1e-16)
            dvs.append(dv)
        self.assertEqual(dvs, sorted(dvs))


class FirstIgnitionTests(unittest.TestCase):
    def test_first_ignition_success(self):
        stage = _stage(propellant=20.0, isp=300.0, count=0, multi=False)
        new_stage, burn = execute_burn(stage, _req(400.0), 100.0)
        self.assertTrue(burn.success)
        self.assertEqual(burn.ignition_kind, IgnitionKind.FIRST)
        self.assertEqual(burn.failure_code, FailureCode.NONE)
        self.assertEqual(burn.stage_index, 1)
        self.assertAlmostEqual(burn.actual_dv_m_s, 400.0, places=6)
        expected_consumed = 100.0 * (1.0 - math.exp(-400.0 / (300.0 * 9.80665)))
        self.assertAlmostEqual(burn.propellant_consumed_t, expected_consumed,
                               places=9)
        self.assertAlmostEqual(burn.mass_before_t, 100.0, places=9)
        self.assertAlmostEqual(burn.mass_after_t, 100.0 - expected_consumed,
                               places=9)
        self.assertEqual(new_stage.ignition_count, 1)
        self.assertAlmostEqual(new_stage.propellant_remaining_t,
                               20.0 - expected_consumed, places=9)
        self.assertEqual(new_stage.stage_index, 1)
        self.assertEqual(new_stage.multi_ignition, False)
        # first ignition does not require multi_ignition=True
        self.assertEqual(burn.data_quality, DataQuality.EXACT)

    def test_second_burn_is_restart_and_does_not_reuse_propellant(self):
        stage = _stage(propellant=20.0, isp=300.0, multi=True)
        s1, b1 = execute_burn(stage, _req(400.0), 100.0)
        m2 = b1.mass_after_t
        s2, b2 = execute_burn(s1, _req(200.0, burn_id='b2'), m2)
        self.assertTrue(b2.success)
        self.assertEqual(b2.ignition_kind, IgnitionKind.RESTART)
        self.assertEqual(s2.ignition_count, 2)
        expected2 = m2 * (1.0 - math.exp(-200.0 / (300.0 * 9.80665)))
        self.assertAlmostEqual(b2.propellant_consumed_t, expected2, places=9)
        self.assertAlmostEqual(
            s2.propellant_remaining_t,
            20.0 - b1.propellant_consumed_t - b2.propellant_consumed_t,
            places=9)
        self.assertAlmostEqual(
            s2.propellant_remaining_t,
            s1.propellant_remaining_t - expected2, places=9)
        self.assertAlmostEqual(b2.mass_after_t, m2 - expected2, places=9)
        self.assertGreater(s2.propellant_remaining_t, 0.0)


class FailurePathTests(unittest.TestCase):
    def test_restart_unsupported_returns_failure_and_unchanged_state(self):
        stage = _stage(propellant=10.0, isp=300.0, multi=False, count=1)
        new_stage, burn = execute_burn(stage, _req(300.0), 50.0)
        self.assertFalse(burn.success)
        self.assertEqual(burn.failure_code, FailureCode.RESTART_UNSUPPORTED)
        self.assertTrue(burn.failure_reason_zh)
        self.assertEqual(burn.actual_dv_m_s, 0.0)
        self.assertEqual(burn.propellant_consumed_t, 0.0)
        self.assertEqual(burn.mass_before_t, 50.0)
        self.assertEqual(burn.mass_after_t, 50.0)
        self.assertEqual(burn.ignition_kind, IgnitionKind.RESTART)
        self.assertEqual(burn.stage_index, 1)
        self.assertEqual(new_stage.ignition_count, 1)
        self.assertEqual(new_stage.propellant_remaining_t, 10.0)
        self.assertEqual(new_stage.to_dict(), stage.to_dict())

    def test_propellant_insufficient_burns_all_and_reports_actual_dv(self):
        stage = _stage(propellant=5.0, isp=300.0)
        new_stage, burn = execute_burn(stage, _req(800.0), 100.0)
        self.assertFalse(burn.success)
        self.assertEqual(burn.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertIn('推进剂', burn.failure_reason_zh)
        expected_actual = 300.0 * 9.80665 * math.log(100.0 / 95.0)
        self.assertAlmostEqual(burn.actual_dv_m_s, expected_actual, places=9)
        self.assertLess(burn.actual_dv_m_s, 800.0)
        self.assertEqual(burn.propellant_consumed_t, 5.0)
        self.assertEqual(new_stage.propellant_remaining_t, 0.0)
        self.assertEqual(new_stage.ignition_count, 1)
        self.assertEqual(burn.ignition_kind, IgnitionKind.FIRST)
        self.assertEqual(burn.stage_index, 1)
        self.assertAlmostEqual(burn.mass_after_t, 95.0, places=9)

    def test_isp_missing_returns_data_insufficient_and_unchanged_state(self):
        stage = _stage(isp=None)
        new_stage, burn = execute_burn(stage, _req(300.0), 100.0,
                                       data_quality=DataQuality.ESTIMATED)
        self.assertEqual(burn.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertTrue(burn.failure_reason_zh)
        self.assertEqual(burn.data_quality, DataQuality.INSUFFICIENT)
        self.assertEqual(burn.ignition_kind, IgnitionKind.FIRST)
        self.assertEqual(burn.stage_index, 1)
        self.assertEqual(burn.actual_dv_m_s, 0.0)
        self.assertEqual(burn.propellant_consumed_t, 0.0)
        self.assertEqual(new_stage.ignition_count, 0)
        self.assertEqual(new_stage.propellant_remaining_t, 20.0)
        self.assertIsNone(new_stage.isp_vac_s)

    def test_detached_stage_returns_no_stage_assigned(self):
        stage = _stage(attached=False)
        new_stage, burn = execute_burn(stage, _req(300.0), 100.0)
        self.assertEqual(burn.failure_code, FailureCode.NO_STAGE_ASSIGNED)
        self.assertEqual(burn.ignition_kind, IgnitionKind.UNASSIGNED)
        self.assertIsNone(burn.stage_index)
        self.assertEqual(burn.actual_dv_m_s, 0.0)
        self.assertEqual(burn.propellant_consumed_t, 0.0)
        self.assertTrue(burn.failure_reason_zh)
        self.assertEqual(new_stage.attached, False)
        self.assertEqual(new_stage.propellant_remaining_t, 20.0)

    def test_parallel_stage_is_not_burned_as_series(self):
        stage = _stage(parallel=True)
        new_stage, burn = execute_burn(stage, _req(300.0), 100.0)
        self.assertEqual(burn.failure_code, FailureCode.DATA_INSUFFICIENT)
        self.assertIn('并联燃烧需要组合求解', burn.failure_reason_zh)
        self.assertEqual(burn.ignition_kind, IgnitionKind.UNASSIGNED)
        self.assertIsNone(burn.stage_index)
        self.assertEqual(burn.data_quality, DataQuality.INSUFFICIENT)
        self.assertEqual(burn.actual_dv_m_s, 0.0)
        self.assertEqual(burn.propellant_consumed_t, 0.0)
        self.assertEqual(new_stage.propellant_remaining_t, 20.0)


class InputValidationTests(unittest.TestCase):
    def test_zero_target_dv_rejected(self):
        with self.assertRaises(ValueError):
            execute_burn(_stage(), _req(0.0), 100.0)

    def test_non_stage_rejected_with_value_error_not_attribute_error(self):
        req = _req(300.0)
        with self.assertRaises(ValueError) as ctx:
            execute_burn({}, req, 100.0)
        self.assertIn('StageState', str(ctx.exception))
        with self.assertRaises(ValueError):
            execute_burn(None, req, 100.0)
        with self.assertRaises(ValueError):
            execute_burn('not-a-stage', req, 100.0)

    def test_calibration_source_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            execute_burn(_stage(), _req(300.0, source='rocket_calibration'),
                         100.0)
        self.assertIn('rocket_calibration', str(ctx.exception))

    def test_vehicle_mass_too_small_rejected(self):
        stage = _stage(propellant=20.0, dry=5.0)  # min mass = 25 t
        with self.assertRaises(ValueError):
            execute_burn(stage, _req(300.0), 24.0)
        with self.assertRaises(ValueError):
            execute_burn(stage, _req(300.0), 0.0)
        with self.assertRaises(ValueError):
            execute_burn(stage, _req(300.0), float('nan'))

    def test_requirement_must_be_burn_requirement(self):
        with self.assertRaises(ValueError):
            execute_burn(_stage(), {'burn_id': 'b', 'order': 1,
                                    'target_dv_m_s': 300.0, 'source': 'm'},
                         100.0)

    def test_negative_vehicle_mass_rejected(self):
        with self.assertRaises(ValueError):
            execute_burn(_stage(), _req(300.0), -50.0)


class ImmutabilityTests(unittest.TestCase):
    def test_inputs_are_not_modified(self):
        stage = _stage(propellant=20.0, isp=300.0, multi=True)
        req = _req(400.0)
        req.metadata['extra'] = [1, 2]
        before_stage = stage.to_dict()
        before_req = req.to_dict()
        execute_burn(stage, req, 100.0)          # success path
        execute_burn(stage, _req(99999.0), 100.0)  # insufficient path
        execute_burn(_stage(multi=False, count=1), _req(300.0), 100.0)
        self.assertEqual(stage.to_dict(), before_stage)
        self.assertEqual(req.to_dict(), before_req)

    def test_returned_stage_is_a_new_instance(self):
        cases = [
            (_stage(), _req(400.0)),                       # success
            (_stage(propellant=5.0), _req(8000.0)),        # insufficient
            (_stage(multi=False, count=1), _req(300.0)),   # restart unsupported
            (_stage(isp=None), _req(300.0)),               # data insufficient
            (_stage(attached=False), _req(300.0)),         # detached
            (_stage(parallel=True), _req(300.0)),          # parallel
        ]
        for stage, req in cases:
            new_stage, _ = execute_burn(stage, req, 100.0)
            self.assertIsNot(new_stage, stage)

    def test_returned_metadata_is_not_shared(self):
        stage = _stage()
        new_stage, _ = execute_burn(stage, _req(400.0), 100.0)
        new_stage.metadata['role'] = 'mutated'
        self.assertEqual(stage.metadata['role'], 'upper')


class SerializationTests(unittest.TestCase):
    def test_results_are_json_dumpable(self):
        cases = [
            (_stage(), _req(400.0)),
            (_stage(propellant=5.0), _req(8000.0)),
            (_stage(multi=False, count=1), _req(300.0)),
            (_stage(isp=None), _req(300.0)),
            (_stage(attached=False), _req(300.0)),
            (_stage(parallel=True), _req(300.0)),
        ]
        for stage, req in cases:
            new_stage, burn = execute_burn(stage, req, 100.0)
            json.dumps(new_stage.to_dict(), ensure_ascii=False)
            json.dumps(burn.to_dict(), ensure_ascii=False)


class FloatEdgeCaseTests(unittest.TestCase):
    def test_huge_dv_exhausts_all_propellant(self):
        stage = _stage(propellant=20.0, isp=300.0)
        new_stage, burn = execute_burn(stage, _req(1_000_000.0), 100.0)
        self.assertFalse(burn.success)
        self.assertEqual(burn.failure_code, FailureCode.PROPELLANT_INSUFFICIENT)
        self.assertEqual(new_stage.propellant_remaining_t, 0.0)
        self.assertEqual(burn.propellant_consumed_t, 20.0)
        expected = 300.0 * 9.80665 * math.log(100.0 / 80.0)
        self.assertAlmostEqual(burn.actual_dv_m_s, expected, places=9)

    def test_tiny_dv_is_success_with_negligible_consumption(self):
        stage = _stage(propellant=20.0, isp=300.0)
        new_stage, burn = execute_burn(stage, _req(1e-6), 100.0)
        self.assertTrue(burn.success)
        self.assertAlmostEqual(burn.actual_dv_m_s, 1e-6, places=9)
        expected_consumed = 100.0 * (1.0 - math.exp(-1e-6 / (300.0 * 9.80665)))
        self.assertAlmostEqual(new_stage.propellant_remaining_t,
                               20.0 - expected_consumed, places=9)
        self.assertGreater(new_stage.propellant_remaining_t, 19.999)

    def test_boundary_required_equals_remaining(self):
        stage = _stage(propellant=10.0, isp=300.0)
        boundary_target = 300.0 * 9.80665 * math.log(100.0 / 90.0)
        new_stage, burn = execute_burn(stage, _req(boundary_target), 100.0)
        self.assertTrue(burn.success)
        self.assertEqual(burn.actual_dv_m_s, boundary_target)
        self.assertGreaterEqual(new_stage.propellant_remaining_t, 0.0)
        self.assertLessEqual(new_stage.propellant_remaining_t, MASS_TOLERANCE_T)
        self.assertAlmostEqual(burn.mass_after_t, 90.0, places=6)

    def test_tiny_target_burn_is_successful_and_finite(self):
        stage = _stage(propellant=20.0, isp=300.0)
        new_stage, burn = execute_burn(stage, _req(1e-14), 100.0)
        self.assertTrue(burn.success)
        self.assertEqual(burn.actual_dv_m_s, 1e-14)
        self.assertGreater(burn.propellant_consumed_t, 0.0)
        self.assertTrue(math.isfinite(burn.propellant_consumed_t))
        self.assertTrue(math.isfinite(burn.mass_after_t))
        self.assertTrue(math.isfinite(burn.mass_before_t))
        self.assertTrue(math.isfinite(new_stage.propellant_remaining_t))
        self.assertEqual(new_stage.ignition_count, 1)
        expected = 100.0 * (-math.expm1(-1e-14 / (300.0 * 9.80665)))
        self.assertAlmostEqual(burn.propellant_consumed_t, expected,
                               delta=abs(expected) * 1e-6 + 1e-30)
        self.assertLessEqual(burn.mass_after_t, burn.mass_before_t)
        self.assertGreaterEqual(burn.mass_after_t, 0.0)
        self.assertGreaterEqual(new_stage.propellant_remaining_t, 0.0)
        # IEEE-754 cannot always represent 100.0 - 3.4e-16 as a different
        # float; the state reduction must still be exactly consistent with
        # the consumed value, and no observable mass difference is faked.
        self.assertEqual(new_stage.propellant_remaining_t,
                         20.0 - burn.propellant_consumed_t)

        # At 1e-10 m/s the reduction is observable at float precision.
        stage2 = _stage(propellant=20.0, isp=300.0)
        s2, b2 = execute_burn(stage2, _req(1e-10), 100.0)
        self.assertTrue(b2.success)
        self.assertGreater(b2.propellant_consumed_t, 0.0)
        self.assertLess(s2.propellant_remaining_t, 20.0)
        expected2 = 100.0 * (-math.expm1(-1e-10 / (300.0 * 9.80665)))
        self.assertAlmostEqual(b2.propellant_consumed_t, expected2,
                               delta=abs(expected2) * 1e-6 + 1e-30)

    def test_tolerance_boundary_conserves_mass(self):
        # required = remaining + 0.75 * MASS_TOLERANCE_T: still a success by
        # tolerance, but must not consume more than the stage actually has.
        stage = _stage(propellant=10.0, isp=300.0)
        over = MASS_TOLERANCE_T * 0.75
        target = -300.0 * 9.80665 * math.log(1.0 - (10.0 + over) / 100.0)
        required = 100.0 * (1.0 - math.exp(-target / (300.0 * 9.80665)))
        self.assertGreater(required, 10.0)
        self.assertLess(required, 10.0 + MASS_TOLERANCE_T)
        new_stage, burn = execute_burn(stage, _req(target), 100.0)
        self.assertTrue(burn.success)
        self.assertEqual(burn.actual_dv_m_s, target)
        self.assertLessEqual(burn.propellant_consumed_t, 10.0)
        self.assertEqual(burn.propellant_consumed_t, 10.0)
        self.assertEqual(new_stage.propellant_remaining_t, 0.0)
        self.assertGreaterEqual(new_stage.propellant_remaining_t, 0.0)
        self.assertAlmostEqual(100.0 - burn.mass_after_t,
                               burn.propellant_consumed_t, places=12)
        self.assertAlmostEqual(burn.mass_after_t, 90.0, places=12)


class DataQualityTests(unittest.TestCase):
    def test_data_quality_passthrough_on_success(self):
        _, burn = execute_burn(_stage(), _req(400.0), 100.0,
                               data_quality=DataQuality.ESTIMATED)
        self.assertEqual(burn.data_quality, DataQuality.ESTIMATED)

    def test_data_quality_passthrough_on_propellant_failure(self):
        _, burn = execute_burn(_stage(propellant=5.0), _req(8000.0), 100.0,
                               data_quality=DataQuality.ESTIMATED)
        self.assertEqual(burn.data_quality, DataQuality.ESTIMATED)

    def test_data_quality_forced_insufficient_for_missing_data(self):
        _, burn = execute_burn(_stage(isp=None), _req(400.0), 100.0,
                               data_quality=DataQuality.EXACT)
        self.assertEqual(burn.data_quality, DataQuality.INSUFFICIENT)
        _, burn2 = execute_burn(_stage(parallel=True), _req(400.0), 100.0,
                                data_quality=DataQuality.EXACT)
        self.assertEqual(burn2.data_quality, DataQuality.INSUFFICIENT)


if __name__ == '__main__':
    unittest.main()
