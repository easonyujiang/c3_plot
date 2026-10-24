import math
import unittest

from c3_mass.ascent_simulation import (
    AscentSettings,
    PitchProgram,
    Planet,
    StageSpec,
    simulate_ascent,
    total_ideal_dv,
)


G0 = 9.80665


def vacuum_planet():
    return Planet(name='Vacuum', mu=0.0, radius=6371000.0,
                  atmosphere_height=0.0, density0=0.0, scale_height=1.0)


def horizontal():
    return PitchProgram(vertical_altitude_m=0, turn_rate_deg_per_km=0,
                        max_pitch_deg=0, min_pitch_deg=0)


class AscentSimulationTests(unittest.TestCase):
    def test_single_stage_matches_rocket_equation(self):
        stage = StageSpec(dry_mass_t=2.0, propellant_t=8.0, thrust_kN=100.0, isp_s=300.0)
        result = simulate_ascent(
            [stage], 1.0, planet=vacuum_planet(), pitch=horizontal(),
            settings=AscentSettings(dt_s=0.01, max_time_s=1000.0))
        expected = stage.isp_s * G0 * math.log(11.0 / 3.0)
        self.assertAlmostEqual(result.final.speed_m_s, expected, delta=1.0)
        self.assertAlmostEqual(result.final_mass_t, 1.0, places=9)
        self.assertEqual(result.stopped_reason, 'all_stages_burned')

    def test_two_stage_total_dv_and_mass_drop(self):
        stages = [
            StageSpec(dry_mass_t=3.0, propellant_t=7.0, thrust_kN=120.0, isp_s=300.0),
            StageSpec(dry_mass_t=1.0, propellant_t=4.0, thrust_kN=30.0, isp_s=320.0),
        ]
        result = simulate_ascent(
            stages, 1.0, planet=vacuum_planet(), pitch=horizontal(),
            settings=AscentSettings(dt_s=0.01, max_time_s=1000.0))
        expected = (stages[0].isp_s * G0 * math.log(16.0 / 9.0)
                    + stages[1].isp_s * G0 * math.log(6.0 / 2.0))
        self.assertAlmostEqual(result.final.speed_m_s, expected, delta=1.5)
        self.assertAlmostEqual(result.final_mass_t, 1.0, places=9)
        self.assertEqual(len(result.stage_events), 2)
        self.assertAlmostEqual(result.stage_events[0].mass_before_t, 9.0, places=6)
        self.assertAlmostEqual(result.stage_events[0].mass_after_t, 6.0, places=6)

    def test_ideal_dv_matches_simulation_and_order(self):
        stages = [
            StageSpec(dry_mass_t=3.0, propellant_t=7.0, thrust_kN=120.0, isp_s=300.0),
            StageSpec(dry_mass_t=1.0, propellant_t=4.0, thrust_kN=30.0, isp_s=320.0),
        ]
        expected = total_ideal_dv(stages, 1.0)
        self.assertAlmostEqual(expected, 300.0 * G0 * math.log(16.0 / 9.0)
                               + 320.0 * G0 * math.log(6.0 / 2.0), places=6)

    def test_drag_reduces_speed_and_records_q(self):
        stage = StageSpec(dry_mass_t=2.0, propellant_t=8.0, thrust_kN=300.0,
                          isp_s=300.0, area_m2=10.0, drag_coefficient=0.5)
        vacuum = simulate_ascent(
            [stage], 1.0, planet=vacuum_planet(), pitch=horizontal(),
            settings=AscentSettings(dt_s=0.02, max_time_s=200.0))
        atmosphere = Planet(name='Air', mu=0.0, radius=6371000.0,
                            atmosphere_height=100000.0, density0=1.225, scale_height=8500.0)
        air = simulate_ascent(
            [stage], 1.0, planet=atmosphere, pitch=horizontal(),
            settings=AscentSettings(dt_s=0.02, max_time_s=200.0))
        self.assertLess(air.final.speed_m_s, vacuum.final.speed_m_s)
        self.assertGreater(air.max_dynamic_pressure_Pa, 0.0)
        self.assertGreater(air.max_acceleration_m_s2, 0.0)

    def test_time_step_convergence(self):
        stage = StageSpec(dry_mass_t=2.0, propellant_t=8.0, thrust_kN=100.0, isp_s=300.0)
        coarse = simulate_ascent([stage], 1.0, planet=vacuum_planet(), pitch=horizontal(),
                                 settings=AscentSettings(dt_s=0.02, max_time_s=1000.0))
        fine = simulate_ascent([stage], 1.0, planet=vacuum_planet(), pitch=horizontal(),
                               settings=AscentSettings(dt_s=0.01, max_time_s=1000.0))
        self.assertLess(abs(coarse.final.speed_m_s - fine.final.speed_m_s), 1.0)

    def test_invalid_stage_is_rejected(self):
        with self.assertRaises(ValueError):
            StageSpec(dry_mass_t=-1.0, propellant_t=1.0, thrust_kN=10.0, isp_s=300.0)
        with self.assertRaises(ValueError):
            StageSpec(dry_mass_t=1.0, propellant_t=1.0, thrust_kN=10.0, isp_s=0.0)


if __name__ == '__main__':
    unittest.main()
