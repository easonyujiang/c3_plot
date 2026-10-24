import json
import unittest

from server.campaign import (
    CANCELLED,
    COMPLETED,
    FAILED,
    QUEUED,
    RUNNING,
    CampaignQueue,
    CampaignSpec,
)
from server.profile import TrialResult


def spec(max_trials=3):
    return CampaignSpec(
        rocket_profile_id='profile-1',
        rocket_sha256='craft-hash',
        max_trials=max_trials,
        trials=[
            {'label': 'baseline 5t', 'payload_t': 5.0,
             'orbit': {'periapsis_km': 220, 'apoapsis_km': 220, 'inclination_deg': 20}},
            {'label': 'baseline 2.5t', 'payload_t': 2.5,
             'orbit': {'periapsis_km': 220, 'apoapsis_km': 220, 'inclination_deg': 20}},
            {'label': 'polar', 'payload_t': 3.5,
             'orbit': {'periapsis_km': 220, 'apoapsis_km': 220, 'inclination_deg': 97.8}},
        ],
        program={'name': 'test-program'},
        tolerances={'orbit_km': 5},
    )


class CampaignContractTests(unittest.TestCase):
    def test_queue_is_deterministic_and_one_trial_runs_at_a_time(self):
        campaign = CampaignQueue(spec())
        self.assertEqual(campaign.stats()['total'], 3)
        self.assertEqual(campaign.stats()[QUEUED], 3)
        first = campaign.next_queued()
        self.assertEqual(first.spec.payload_t, 5.0)
        campaign.start(first.trial_id)
        self.assertEqual(campaign.stats()[RUNNING], 1)
        with self.assertRaises(ValueError):
            campaign.start(campaign.next_queued().trial_id)
        campaign.finish(first.trial_id, TrialResult(spec_id=first.trial_id,
                                                    status='verified', remaining_dv_m_s=100.0))
        self.assertEqual(campaign.get(first.trial_id).state, COMPLETED)
        second = campaign.next_queued()
        campaign.start(second.trial_id)
        campaign.fail(second.trial_id, 'orbit_tolerance', 'apoapsis low')
        self.assertEqual(campaign.get(second.trial_id).state, FAILED)
        self.assertEqual(campaign.get(second.trial_id).result.failure_code, 'orbit_tolerance')
        third = campaign.next_queued()
        campaign.cancel(third.trial_id)
        self.assertEqual(campaign.get(third.trial_id).state, CANCELLED)
        self.assertEqual(campaign.stats()[COMPLETED], 1)
        self.assertEqual(campaign.stats()[FAILED], 1)
        json.dumps(campaign.snapshot())

    def test_identical_specs_share_a_campaign_id(self):
        self.assertEqual(spec().campaign_id, spec().campaign_id)
        self.assertNotEqual(spec().campaign_id, spec(max_trials=4).campaign_id)

    def test_bad_specs_are_rejected(self):
        with self.assertRaises(ValueError):
            spec(max_trials=2)
        with self.assertRaises(ValueError):
            spec(max_trials=7)
        with self.assertRaises(ValueError):
            CampaignSpec(rocket_profile_id='p', rocket_sha256='s',
                         trials=[{'payload_t': 0, 'orbit': {}}])
        with self.assertRaises(ValueError):
            CampaignSpec(rocket_profile_id='p', rocket_sha256='s', trials=[])

    def test_invalid_state_transitions_are_rejected(self):
        campaign = CampaignQueue(spec())
        record = campaign.next_queued()
        with self.assertRaises(ValueError):
            campaign.finish(record.trial_id, TrialResult(spec_id='x', status='verified'))
        campaign.start(record.trial_id)
        campaign.cancel(record.trial_id)
        self.assertEqual(record.state, CANCELLED)
        self.assertEqual(record.attempts, 1)
        with self.assertRaises(ValueError):
            campaign.cancel(record.trial_id)


if __name__ == '__main__':
    unittest.main()
