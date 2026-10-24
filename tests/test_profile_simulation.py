import math
import unittest

from c3_mass.ascent_simulation import AscentSettings, PitchProgram, Planet
from c3_mass.profile_simulation import simulate_profile, stage_specs_from_profile


def profile(payload_status='declared'):
    return {
        'profile_id': 'test-profile',
        'name': 'Profile Test',
        'payload': {
            'status': payload_status,
            'wet_mass_t': 1.0,
            'dry_mass_t': 0.2,
        },
        'fairings': {'wet_mass_t': 0.1},
        'stages': [{
            'wet_mass_t': 10.0,
            'dry_mass_t': 2.0,
            'thrust_kN': 100.0,
            'isp_vac_s': 300.0,
            'isp_sl_s': 250.0,
            'payload_included': True if payload_status == 'declared' else None,
        }],
    }


class ProfileSimulationTests(unittest.TestCase):
    def test_declared_payload_is_removed_from_stage_mass(self):
        parsed = stage_specs_from_profile(profile())
        stage = parsed['stages'][0]
        self.assertAlmostEqual(parsed['payload_t'], 1.0)
        self.assertAlmostEqual(stage.dry_mass_t, 1.8)
        self.assertAlmostEqual(stage.propellant_t, 7.2)
        self.assertTrue(any('fairing' in warning for warning in parsed['warnings']))

    def test_t0_and_t1_use_the_same_net_masses(self):
        result = simulate_profile(
            profile(),
            planet=Planet(mu=0.0, radius=6371000.0, atmosphere_height=0.0, density0=0.0),
            pitch=PitchProgram(vertical_altitude_m=0, turn_rate_deg_per_km=0,
                               max_pitch_deg=0, min_pitch_deg=0),
            settings=AscentSettings(dt_s=0.01, max_time_s=1000.0),
        )
        expected = 300.0 * 9.80665 * math.log(10.0 / 2.8)
        self.assertAlmostEqual(result['ideal_dv_m_s'], expected, places=6)
        self.assertAlmostEqual(result['simulation']['final']['speed_m_s'], expected, delta=1.0)
        self.assertAlmostEqual(result['simulation']['final_mass_t'], 1.0, places=9)

    def test_unknown_payload_is_explicit_and_not_subtracted(self):
        parsed = stage_specs_from_profile(profile('unknown'))
        self.assertEqual(parsed['payload_t'], 0.0)
        self.assertAlmostEqual(parsed['stages'][0].dry_mass_t, 2.0)
        self.assertTrue(any('not declared' in warning for warning in parsed['warnings']))


if __name__ == '__main__':
    unittest.main()
