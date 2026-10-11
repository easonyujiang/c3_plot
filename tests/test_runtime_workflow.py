import hashlib
import tempfile
import unittest
from pathlib import Path

from mission_simulation import StageState, BurnRequirement, FailureCode
from mission_solver import execute_burn
from server.editor_snapshot import checked_runtime_performance, verify_sources


class RuntimeWorkflowTests(unittest.TestCase):
    def test_ground_only_engine_cannot_start_as_an_upper_stage(self):
        stage=StageState(stage_index=1,wet_mass_t=10,dry_mass_t=1,
                         propellant_remaining_t=9,isp_vac_s=320,
                         metadata={'max_ignitions':1,'ground_ignition_only':True})
        _,burn=execute_burn(stage,BurnRequirement(burn_id='upper',order=1,target_dv_m_s=100,source='ascent_replay'),11)
        self.assertFalse(burn.success)
        self.assertEqual(burn.failure_code,FailureCode.RESTART_UNSUPPORTED)

    def test_three_ignition_engine_rejects_fourth_without_consuming_fuel(self):
        stage=StageState(stage_index=0,wet_mass_t=10,dry_mass_t=1,
                         propellant_remaining_t=9,isp_vac_s=320,multi_ignition=True,
                         metadata={'max_ignitions':3})
        mass=11
        for i in range(3):
            stage,burn=execute_burn(stage,BurnRequirement(burn_id=str(i),order=i+1,target_dv_m_s=100,source='mission_model'),mass)
            self.assertTrue(burn.success)
            mass=burn.mass_after_t
        before=stage.propellant_remaining_t
        after,burn=execute_burn(stage,BurnRequirement(burn_id='fourth',order=3,target_dv_m_s=100,source='mission_model'),mass)
        self.assertFalse(burn.success)
        self.assertEqual(burn.failure_code,FailureCode.RESTART_UNSUPPORTED)
        self.assertEqual(after.propellant_remaining_t,before)
        self.assertEqual(after.ignition_count,3)

    def test_ascent_ignition_counts_against_restart_limit(self):
        stage=StageState(stage_index=1,wet_mass_t=10,dry_mass_t=1,
                         propellant_remaining_t=8,isp_vac_s=320,multi_ignition=True,
                         ignition_count=1,metadata={'max_ignitions':2})
        after,burn=execute_burn(stage,BurnRequirement(burn_id='departure',order=1,target_dv_m_s=100,source='mission_model'),10)
        self.assertTrue(burn.success)
        self.assertFalse(after.ignition_available)

    def test_runtime_engine_source_survives_local_cfg_absence_and_detects_mod_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game=root/'GameData';game.mkdir()
            files=[]
            for name in ('ModuleManager.ConfigCache','mod.dll'):
                p=game/name;p.write_bytes(name.encode())
                files.append({'path':'GameData/'+name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
            stage={'runtime_performance':{'thrust':213,'isp_vac':300,'isp_sl':10},
                   'runtime_source':{'ksp_root':str(root),'files':files}}
            verify_sources(stage['runtime_source'])
            (game/'mod.dll').write_bytes(b'changed')
            with self.assertRaises(ValueError):verify_sources(stage['runtime_source'])

    def test_new_plugin_invalidates_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game=root/'GameData';game.mkdir()
            cache=game/'ModuleManager.ConfigCache';cache.write_bytes(b'cache')
            source={'ksp_root':str(root),'files':[{'path':'GameData/ModuleManager.ConfigCache','sha256':hashlib.sha256(b'cache').hexdigest()}]}
            verify_sources(source)
            (game/'new.dll').write_bytes(b'new')
            with self.assertRaises(ValueError):verify_sources(source)
