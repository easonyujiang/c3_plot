import json
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

from c3_mass.compute_c3_mass import compute_rocket
from c3_mass.mission_capacity_search import MissionCapacityResult
from c3_mass.mission_simulation import DataQuality, FailureCode
from server.mission_analysis import RocketMissionAnalysisResult
from server.yaml_codec import load_config

import server.routes_data as routes_data


def _make_app():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes_data.bp)
    return app


def _mission_dict():
    return {
        'required_dv': 1000.0,
        'post_parking_ignitions': 1,
        'orbit_type': 'circular',
        'arrival_strategy': 'combined',
        'burn_sequence': [
            {'order': 1, 'name': 'park', 'source': 'rocket_calibration',
             'dv_m_s': 0.0},
            {'order': 2, 'name': 'departure', 'source': 'mission_model',
             'dv_m_s': 1000.0},
        ],
    }


def _quick(rocket, slug, **over):
    item = {
        'rocket': rocket, 'slug': slug, 'capacity': 1.0,
        'requested_payload': None, 'margin': None, 'utilization': None,
        'dv_feasible': True, 'supports_restart': True,
        'restart_required': False, 'feasible': True, 'reason': '',
    }
    item.update(over)
    return item


def _mk_capacity(slug, status='bounded', max_payload=0.502):
    return MissionCapacityResult(
        rocket_ref=slug, success=(status == 'bounded'),
        capacity_status=status,
        max_payload_t=max_payload,
        feasible_lower_bound_t=(max_payload - 0.0004
                                if max_payload is not None else None),
        infeasible_upper_bound_t=(max_payload + 0.0004
                                  if max_payload is not None else None),
        payload_tolerance_t=0.001,
        failure_code=(FailureCode.NONE if status == 'bounded'
                      else FailureCode.DATA_INSUFFICIENT),
        failure_reason_zh='' if status == 'bounded' else 'no capacity',
        data_quality=DataQuality.ESTIMATED,
        best_feasible_evaluation=None, first_infeasible_evaluation=None,
        evaluations=[], warnings=[],
        metadata={
            'evaluation_count': 20, 'cache_hit_count': 0,
            'calibration_payload_min_t': 0.05,
            'calibration_payload_max_t': 4.6,
            'monotonicity_samples': 17, 'monotonicity_checked': True,
            'boundary_width_t': 0.0008,
            'search_method': 'grid_guard_then_bisection',
            'whole_burn_per_stage': True,
        })


def _mk_analysis(rocket, slug, status, capacity_status='bounded',
                 capacity_max=0.502, reason='', phase=None):
    capacity = (_mk_capacity(slug, status=capacity_status,
                             max_payload=capacity_max)
                if capacity_status is not None else None)
    return RocketMissionAnalysisResult(
        rocket=rocket, slug=slug,
        verification_status=status,
        verified_feasible=(True if status == 'verified_feasible'
                           else (False if status == 'verified_infeasible'
                                 else None)),
        recommendation_eligible=(status == 'verified_feasible'),
        data_quality=DataQuality.ESTIMATED,
        failure_code=(FailureCode.NONE if status == 'verified_feasible'
                      else (FailureCode.PROPELLANT_INSUFFICIENT
                            if status == 'verified_infeasible'
                            else FailureCode.DATA_INSUFFICIENT)),
        failure_reason_zh=reason, failure_phase=phase,
        normalization=None, burn_conversion=None,
        capacity_search=capacity, requested_payload_evaluation=None,
        warnings=[], metadata={'payload_requested': False,
                               'capacity_status': capacity_status})


def _patch_fleet(quick_results, analyses=None, cfg=None, data=None):
    """Patch data access so the route sees the given quick estimates and
    analysis results.  Returns the compute_orbit_mission mock."""
    def analysis_side_effect(rocket, slug, config, computed_data, mission,
                             payload_t=None, data_quality=DataQuality.EXACT,
                             payload_tolerance_t=0.001,
                             monotonicity_samples=17, max_evaluations=128,
                             max_search_nodes=10000):
        if analyses is None:
            return _mk_analysis(rocket, slug, 'data_insufficient',
                                capacity_status='data_insufficient')
        if slug in analyses:
            return analyses[slug]
        return _mk_analysis(rocket, slug, 'data_insufficient',
                            capacity_status='data_insufficient')

    patches = [
        mock.patch('server.routes_data.models.find_rockets_for_orbit',
                   return_value=quick_results),
        mock.patch('server.routes_data.models.get_rocket_config',
                   return_value=cfg if cfg is not None else {'stages': []}),
        mock.patch('server.routes_data.models.get_computed_data',
                   return_value=data if data is not None else {'dv_curve': []}),
        mock.patch('server.routes_data.analyze_rocket_for_mission',
                   side_effect=analysis_side_effect),
    ]
    for patch in patches:
        patch.start()
    # NOTE: patch.start() returns the created MOCK in Python 3.13, not the
    # patch object; callers must stop the PATCH objects themselves.
    return patches


class MissionQueryApiTests(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.client = self.app.test_client()

    def _post(self, body=None, raw=None):
        if raw is not None:
            return self.client.post('/api/mission-query',
                                    data=raw,
                                    content_type='application/json')
        return self.client.post('/api/mission-query', json=body or {})

    def _post_mission(self, quick_results, analyses=None, body=None,
                      cfg=None, data=None):
        started = _patch_fleet(quick_results, analyses, cfg, data)
        try:
            with mock.patch('server.routes_data.compute_orbit_mission',
                            return_value=_mission_dict()) as orbit_mock:
                response = self._post(body or {})
                return response, orbit_mock
        finally:
            for patch in started:
                patch.stop()

    def test_old_fields_preserved_and_new_fields_added(self):
        quick = [_quick('Rocket A', 'a', requested_payload=0.3, margin=0.2)]
        response, _ = self._post_mission(
            quick, analyses={'a': _mk_analysis('Rocket A', 'a',
                                               'verified_feasible',
                                               capacity_max=0.6)},
            body={'payload_t': 0.3})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        for key in ('mission', 'payload_t', 'count', 'feasible_count',
                    'recommended', 'results', 'detailed_analysis'):
            self.assertIn(key, payload)
        entry = payload['results'][0]
        for key in ('rocket', 'slug', 'capacity', 'requested_payload',
                    'margin', 'utilization', 'dv_feasible',
                    'supports_restart', 'restart_required', 'feasible',
                    'reason', 'quick_estimate', 'detailed_validation',
                    'verification_status', 'verified_feasible',
                    'verified_capacity', 'recommendation_eligible',
                    'detailed_reason'):
            self.assertIn(key, entry)
        self.assertEqual(entry['feasible'], True)
        self.assertEqual(entry['quick_estimate']['feasible'], True)
        self.assertEqual(entry['verification_status'], 'verified_feasible')
        self.assertEqual(entry['verified_capacity'], 0.6)
        self.assertEqual(entry['recommendation_eligible'], True)
        self.assertEqual(entry['detailed_reason'], '')
        self.assertEqual(payload['payload_t'], 0.3)
        self.assertEqual(payload['detailed_analysis']
                         ['verified_feasible_count'], 1)

    def test_recommended_only_from_detailed_validation(self):
        quick = [
            _quick('Rocket A', 'a', feasible=True, capacity=2.0),
            _quick('Rocket B', 'b', feasible=False, capacity=0.1),
        ]
        analyses = {
            'a': _mk_analysis('Rocket A', 'a', 'verified_infeasible',
                              capacity_status='bounded', capacity_max=1.5,
                              reason='详细验证不可行', phase='stage_planning'),
            'b': _mk_analysis('Rocket B', 'b', 'verified_feasible',
                              capacity_max=2.2),
        }
        response, _ = self._post_mission(
            quick, analyses=analyses, body={'payload_t': 0.5})
        payload = response.get_json()
        # quick estimate says A is feasible, but detailed says not -> B wins
        self.assertEqual(payload['recommended']['slug'], 'b')
        self.assertEqual(payload['feasible_count'], 1)  # legacy count of A
        self.assertEqual(payload['detailed_analysis']
                         ['verified_feasible_count'], 1)
        self.assertEqual(payload['detailed_analysis']
                         ['verified_infeasible_count'], 1)

    def test_quick_feasible_but_detailed_infeasible_not_recommended(self):
        quick = [_quick('Rocket A', 'a', feasible=True)]
        response, _ = self._post_mission(
            quick, analyses={'a': _mk_analysis(
                'Rocket A', 'a', 'verified_infeasible',
                capacity_status='bounded', capacity_max=0.2,
                reason='任务规划失败')},
            body={'payload_t': 0.5})
        payload = response.get_json()
        self.assertIsNone(payload['recommended'])
        self.assertEqual(payload['feasible_count'], 1)

    def test_single_rocket_data_insufficient_isolated(self):
        quick = [
            _quick('Rocket A', 'a', feasible=True),
            _quick('Rocket B', 'b', feasible=True),
        ]
        analyses = {
            'a': _mk_analysis('Rocket A', 'a', 'data_insufficient',
                              capacity_status='data_insufficient',
                              reason='校准曲线不可信'),
            'b': _mk_analysis('Rocket B', 'b', 'verified_feasible',
                              capacity_max=1.2),
        }
        response, _ = self._post_mission(quick, analyses=analyses)
        payload = response.get_json()
        self.assertEqual(len(payload['results']), 2)
        entry_a = payload['results'][0]
        self.assertEqual(entry_a['verification_status'], 'data_insufficient')
        self.assertFalse(entry_a['recommendation_eligible'])
        self.assertEqual(entry_a['verified_feasible'], None)
        self.assertEqual(payload['recommended']['slug'], 'b')
        self.assertEqual(payload['detailed_analysis']
                         ['data_insufficient_count'], 1)

    def test_all_unavailable_recommended_null(self):
        quick = [_quick('Rocket A', 'a', feasible=True)]
        response, _ = self._post_mission(
            quick, analyses={'a': _mk_analysis(
                'Rocket A', 'a', 'data_insufficient',
                capacity_status='evaluation_limit')})
        payload = response.get_json()
        self.assertIsNone(payload['recommended'])

    def test_no_payload_query(self):
        quick = [
            _quick('Rocket A', 'a', capacity=0.3),
            _quick('Rocket B', 'b', capacity=1.0),
        ]
        analyses = {
            'a': _mk_analysis('Rocket A', 'a', 'verified_feasible',
                              capacity_max=0.3),
            'b': _mk_analysis('Rocket B', 'b', 'verified_feasible',
                              capacity_max=1.0),
        }
        response, _ = self._post_mission(quick, analyses=analyses)
        payload = response.get_json()
        self.assertIsNone(payload['payload_t'])
        # no payload -> highest verified capacity is recommended
        self.assertEqual(payload['recommended']['slug'], 'b')
        self.assertEqual(payload['recommended']['verified_capacity'], 1.0)

    def test_arrival_strategy_passed_to_orbit_calculation(self):
        quick = [_quick('Rocket A', 'a')]
        for strategy in ('combined', 'separate'):
            with self.subTest(strategy=strategy):
                started = _patch_fleet(quick, analyses={})
                try:
                    with mock.patch(
                            'server.routes_data.compute_orbit_mission',
                            return_value=_mission_dict()) as orbit_mock:
                        self._post({'arrival_strategy': strategy})
                        kwargs = orbit_mock.call_args.kwargs
                        self.assertEqual(kwargs['arrival_strategy'], strategy)
                finally:
                    for patch in started:
                        patch.stop()

    def test_include_audit_off_by_default_on_when_requested(self):
        quick = [_quick('Rocket A', 'a')]
        analyses = {'a': _mk_analysis('Rocket A', 'a', 'verified_feasible',
                                      capacity_max=0.6)}
        response, _ = self._post_mission(quick, analyses=analyses, body={})
        validation = response.get_json()['results'][0]['detailed_validation']
        self.assertNotIn('audit', validation)
        response2, _ = self._post_mission(
            quick, analyses=analyses, body={'include_audit': True})
        validation2 = response2.get_json()['results'][0][
            'detailed_validation']
        self.assertIn('audit', validation2)
        self.assertTrue(response2.get_json()['detailed_analysis']
                        ['include_audit'])

    def test_compact_response_has_no_search_trajectory(self):
        quick = [_quick('Rocket A', 'a')]
        analyses = {'a': _mk_analysis('Rocket A', 'a', 'verified_feasible',
                                      capacity_max=0.6)}
        response, _ = self._post_mission(quick, analyses=analyses)
        text = response.get_data(as_text=True)
        self.assertNotIn('evaluations', text)
        self.assertNotIn('"audit"', text)

    def test_invalid_json_and_bodies_return_400(self):
        cases = [
            self._post(raw='not json'),
            self._post(raw=''),
            self._post(raw='[1, 2]'),
            self._post(raw='"hello"'),
        ]
        for response in cases:
            self.assertEqual(response.status_code, 400)
            payload = response.get_json()
            self.assertIn('error', payload)

    def test_strict_number_rejection(self):
        bad_bodies = [
            {'payload_t': True},
            {'payload_t': False},
            {'payload_t': float('nan')},
            {'payload_t': float('inf')},
            {'payload_t': float('-inf')},
            {'payload_t': 'abc'},
            {'inclination_deg': 181},
            {'inclination_deg': -1},
            {'launch_lat': 91},
            {'target_altitude_km': 79},
            {'parking_alt_km': 79},
            {'payload_t': -1},
            {'orbit_type': 'circular_extra'},
            {'arrival_strategy': 'mixed'},
            {'include_audit': 'true'},
            {'max_search_nodes': 100},
            {'initial_samples': 5},
            {'unknown_param': 1},
        ]
        for body in bad_bodies:
            with self.subTest(body=body):
                response = self._post(body)
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.get_json())

    def test_runtime_error_not_wrapped_as_data_insufficient(self):
        quick = [_quick('Rocket A', 'a')]
        started = _patch_fleet(quick, analyses=None)
        try:
            with mock.patch('server.routes_data.compute_orbit_mission',
                            return_value=_mission_dict()):
                with mock.patch(
                        'server.routes_data.analyze_rocket_for_mission',
                        side_effect=RuntimeError('mass conservation violated')):
                    with self.assertRaises(RuntimeError):
                        self._post({})
        finally:
            for patch in started:
                patch.stop()


@unittest.skip('历史固定快照已按清零要求移除；真实游戏数据由 test_live_game_data 按需验证')
class NeutronRealServiceIntegrationTests(unittest.TestCase):
    """Real analysis service, patched data access layer, no database."""

    @classmethod
    def setUpClass(cls):
        cls.config = load_config(
            Path('rockets/neutron_i_2630.yaml').read_text(encoding='utf-8'))
        cls.computed = compute_rocket(cls.config)
        cls.app = _make_app()
        cls.client = cls.app.test_client()

    def _query(self, payload_t=None, include_audit=False):
        body = {'target_altitude_km': 35786, 'inclination_deg': 180,
                'arrival_strategy': 'combined'}
        if payload_t is not None:
            body['payload_t'] = payload_t
        if include_audit:
            body['include_audit'] = True
        with mock.patch(
                'server.routes_data.models.find_rockets_for_orbit',
                return_value=[_quick('Neutron I 2630', 'neutron_i_2630',
                                     feasible=False, reason='旧快速估算')]):
            with mock.patch('server.routes_data.models.get_rocket_config',
                            return_value=self.config):
                with mock.patch('server.routes_data.models.get_computed_data',
                                return_value=self.computed):
                    return self.client.post('/api/mission-query', json=body)

    def test_no_payload_bounded_retrograde_capacity_about_0_134(self):
        response = self._query()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        entry = payload['results'][0]
        self.assertEqual(entry['verification_status'], 'verified_feasible')
        self.assertTrue(entry['verified_feasible'])
        self.assertFalse(entry['recommendation_eligible'])
        self.assertAlmostEqual(entry['verified_capacity'], 0.134, delta=0.002)
        self.assertEqual(entry['detailed_validation']['capacity']['status'],
                         'bounded')
        self.assertLessEqual(
            entry['detailed_validation']['capacity']['boundary_width_t'],
            0.001)
        self.assertIsNone(payload['recommended'])
        self.assertIsNone(payload['payload_t'])

    def test_payload_0_1_verified_feasible(self):
        response = self._query(payload_t=0.1)
        payload = response.get_json()
        entry = payload['results'][0]
        self.assertEqual(entry['verification_status'], 'verified_feasible')
        self.assertTrue(entry['verified_feasible'])
        requested = entry['detailed_validation']['requested_payload']
        self.assertTrue(requested['feasible'])
        self.assertTrue(requested['plan_verified'])
        timeline = entry['detailed_validation']['burn_timeline']
        self.assertEqual([t['ignition_kind'] for t in timeline[1:]],
                         ['restart', 'restart'])
        self.assertIsNone(payload['recommended'])

    def test_payload_2_5_verified_infeasible(self):
        response = self._query(payload_t=2.5)
        payload = response.get_json()
        entry = payload['results'][0]
        self.assertEqual(entry['verification_status'], 'verified_infeasible')
        self.assertFalse(entry['verified_feasible'])
        self.assertFalse(entry['recommendation_eligible'])
        self.assertIn('2450.7', entry['detailed_reason'])
        requested = entry['detailed_validation']['requested_payload']
        self.assertFalse(requested['feasible'])
        self.assertEqual(requested['failure_phase'], 'stage_planning')
        # no forged executing stages in the timeline
        for burn in entry['detailed_validation']['burn_timeline']:
            self.assertIsNone(burn['stage_index'])
        self.assertIsNone(payload['recommended'])
        # The independent search includes the launch-direction correction.
        self.assertAlmostEqual(entry['verified_capacity'], 0.134, delta=0.002)

    def test_include_audit_attaches_full_audit(self):
        response = self._query(payload_t=0.2, include_audit=True)
        payload = response.get_json()
        audit = payload['results'][0]['detailed_validation']['audit']
        self.assertIn('normalization', audit)
        self.assertIn('burn_conversion', audit)
        self.assertIn('capacity_search', audit)
        self.assertIn('requested_payload_evaluation', audit)


class UnavailableAnalysisTests(unittest.TestCase):
    def test_missing_config_and_data_reasons_distinguished(self):
        quick = [
            _quick('Rocket A', 'a', feasible=True),
            _quick('Rocket B', 'b', feasible=True),
            _quick('Rocket C', 'c', feasible=True),
        ]
        analyses = {
            'b': _mk_analysis('Rocket B', 'b', 'verified_feasible',
                              capacity_max=1.0),
            'c': _mk_analysis('Rocket C', 'c', 'verified_feasible',
                              capacity_max=1.0),
        }
        with mock.patch(
                'server.routes_data.models.find_rockets_for_orbit',
                return_value=quick):
            with mock.patch(
                    'server.routes_data.models.get_rocket_config',
                    side_effect=lambda slug: None if slug != 'b' else {
                        'stages': []}):
                with mock.patch(
                        'server.routes_data.models.get_computed_data',
                        side_effect=lambda slug: None if slug != 'a' else {
                            'dv_curve': []}):
                    with mock.patch(
                            'server.routes_data.analyze_rocket_for_mission',
                            side_effect=lambda rocket, slug, **kw:
                            analyses[slug]):
                        response = _make_app().test_client().post(
                            '/api/mission-query', json={'payload_t': 0.5})
        payload = response.get_json()
        by_slug = {e['slug']: e for e in payload['results']}
        # Rocket A: only computed data present -> config missing
        self.assertIn('缺少火箭配置', by_slug['a']['detailed_reason'])
        # Rocket B: only config present -> computed data missing
        self.assertIn('缺少计算结果', by_slug['b']['detailed_reason'])
        # Rocket C: both missing -> combined reason
        self.assertIn('缺少火箭配置与计算结果',
                      by_slug['c']['detailed_reason'])

    def test_unavailable_metadata_payload_requested(self):
        quick = [_quick('Rocket A', 'a', feasible=True)]
        for body, expected in (({}, False), ({'payload_t': 0.5}, True)):
            request_body = dict(body)
            request_body['include_audit'] = True
            with mock.patch(
                    'server.routes_data.models.find_rockets_for_orbit',
                    return_value=quick):
                with mock.patch(
                        'server.routes_data.models.get_rocket_config',
                        return_value=None):
                    with mock.patch(
                            'server.routes_data.models.get_computed_data',
                            return_value=None):
                        response = _make_app().test_client().post(
                            '/api/mission-query', json=request_body)
            entry = response.get_json()['results'][0]
            validation = entry['detailed_validation']
            self.assertEqual(validation['verification_status'],
                             'data_insufficient')
            self.assertEqual(
                validation['audit']['metadata']['payload_requested'], expected)

    def test_unavailable_helper_metadata(self):
        from server.mission_analysis import unavailable_rocket_analysis
        result = unavailable_rocket_analysis('R', 's', reason='x',
                                             payload_requested=True)
        self.assertTrue(result.metadata['payload_requested'])
        result2 = unavailable_rocket_analysis('R', 's', reason='x')
        self.assertFalse(result2.metadata['payload_requested'])
        self.assertEqual(result2.to_dict()['metadata']['payload_requested'],
                         False)


if __name__ == '__main__':
    unittest.main()
