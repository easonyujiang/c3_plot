import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.game.audit_baseline import audit


def _inspection(payload_t, *, payload_parts=0, shielded=True):
    parts = ''.join(
        'PART\n{\n payload = True\n mass_t = %s\n}\n' % (payload_t / payload_parts if payload_parts else 0)
        for _ in range(payload_parts))
    return ('C3_BASELINE_INSPECTION\n{\n'
            ' craft_sha256 = synthetic\n'
            ' controllable = True\n'
            ' payload_mass_t = %s\n'
            ' target_payload_t = %s\n'
            ' payload_shielded = %s\n'
            '%s}\n') % (payload_t, payload_t, shielded, parts)


class BaselineAuditTests(unittest.TestCase):
    def test_target_payload_above_one_tonne_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = 'C3BaselineSynthetic'
            save = root / 'saves' / folder
            save.mkdir(parents=True)
            data = root / 'PluginData' / 'C3Baseline'
            data.mkdir(parents=True)
            source = root / 'GameData' / 'ModuleManager.ConfigCache'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'cache')
            digest = hashlib.sha256(b'cache').hexdigest()
            (save / 'baseline-inputs.json').write_text(json.dumps({
                'source_files': {'GameData/ModuleManager.ConfigCache': digest},
                'environment_sha256': {},
                'target_pe_m': 220000,
                'target_ap_m': 220000,
                'inclination_deg': 20,
                'payload_t': 2.5,
            }), encoding='utf-8')
            (data / (folder + '.c3inspection')).write_text(
                _inspection(2.5, payload_parts=11), encoding='utf-8')
            (data / (folder + '_configured.c3inspection')).write_text(
                _inspection(2.5, payload_parts=11), encoding='utf-8')
            (data / (folder + '_final.c3inspection')).write_text(
                _inspection(2.5, payload_parts=11, shielded=False), encoding='utf-8')
            with (data / (folder + '_flight.csv')).open('w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream)
                writer.writerow(['ut_s', 'elapsed_s', 'stage', 'altitude_m', 'periapsis_m',
                                 'apoapsis_m', 'inclination_deg', 'mass_t', 'throttle',
                                 'thrust_kN', 'electric_charge', 'status'])
                writer.writerow([0, 0, 1, 1000, 1000, 1000, 20, 100, 1, 100, 800, '上升'])
                writer.writerow([20, 20, 1, 219000, 219000, 220000, 20, 5.85, 0, 0, 800, '关闭'])
                writer.writerow([50, 50, 1, 220000, 219000, 220000, 20, 5.85, 0, 0, 800, '关闭'])
            result = audit(root, folder)
            self.assertEqual(result['status'], 'target_verified')
            self.assertEqual(result['target_payload_t'], 2.5)
            self.assertTrue(result['payload_mass_matches_target'])
            self.assertGreaterEqual(result['orbit_hold_s'], 30)


if __name__ == '__main__':
    unittest.main()
