import csv
import tempfile
import unittest
from pathlib import Path

from tools.analyze_telemetry import analyze


class TelemetryAuditTests(unittest.TestCase):
    def write_session(self, directory, *, energy_error=0, mass_error=0):
        path = Path(directory) / 'synthetic.csv'
        row = dict(ut_s=10, body='Earth', situation='ORBITING', stage=1, mass_t=5,
                   altitude_m=220000, periapsis_m=220000, apoapsis_m=220000,
                   orbit_speed_m_s=7800, c3_km2_s2=(7800**2 - 2*3.986e14/6591000)/1e6 + energy_error,
                   thrust_kN=0, inclination_deg=20)
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=row)
            writer.writeheader()
            writer.writerow(row)
        (Path(directory) / 'synthetic_10_000.cfg').write_text('''C3_RUNTIME_SNAPSHOT
{
schema_version = 2
body = Earth
body_radius_m = 6371000
body_mu_m3_s2 = 398600000000000
vessel_mass_t = 5
ut_s = 10
PART
{
runtime_part_mass_t = %s
}
}
''' % (5 + mass_error), encoding='utf-8')
        return path

    def test_independent_mass_energy_audit_does_not_promote_to_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            result = analyze(self.write_session(directory))
            self.assertEqual(result['status'], 'runtime_invariants_passed')
            self.assertFalse(result['calibration_updated'])
            self.assertEqual(len(result['reference_orbit_coast_candidates']), 1)

    def test_mass_or_energy_corruption_fails(self):
        for errors in [{'energy_error': 0.01}, {'mass_error': 0.02}]:
            with self.subTest(errors=errors), tempfile.TemporaryDirectory() as directory:
                result = analyze(self.write_session(directory, **errors))
                self.assertEqual(result['status'], 'runtime_invariants_failed')

    def test_real_config_node_save_omits_outer_node_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_session(directory)
            snapshot = path.with_name('synthetic_10_000.cfg')
            lines = snapshot.read_text(encoding='utf-8').splitlines()
            snapshot.write_text('\n'.join(lines[2:-1]), encoding='utf-8')
            self.assertEqual(analyze(path)['status'], 'runtime_invariants_passed')

    def test_missing_snapshot_is_insufficient_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_session(directory)
            path.with_name('synthetic_10_000.cfg').unlink()
            with self.assertRaisesRegex(ValueError, 'snapshots missing'):
                analyze(path)


if __name__ == '__main__':
    unittest.main()
