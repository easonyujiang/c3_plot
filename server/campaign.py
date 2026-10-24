"""Deterministic campaign/trial queue contracts (Phase C), pure Python.

No KSP, Flask, SQLite, threads or file writes.  The campaign engine will later
drive this queue; keeping the state machine pure makes it testable and lets the
same specification drive a dry run or a real flight.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any

from .profile import TrialResult, TrialSpec


QUEUED = 'queued'
RUNNING = 'running'
COMPLETED = 'completed'
FAILED = 'failed'
CANCELLED = 'cancelled'
TERMINAL = (COMPLETED, FAILED, CANCELLED)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


@dataclass
class CampaignSpec:
    rocket_profile_id: str
    rocket_sha256: str
    trials: list[dict]
    campaign_id: str = ''
    program: dict = field(default_factory=dict)
    tolerances: dict = field(default_factory=dict)
    max_trials: int = 6
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.rocket_profile_id or not self.rocket_sha256:
            raise ValueError('rocket_profile_id and rocket_sha256 are required')
        if not isinstance(self.trials, list) or not self.trials:
            raise ValueError('at least one trial is required')
        if not isinstance(self.max_trials, int) or isinstance(self.max_trials, bool) or self.max_trials < 1:
            raise ValueError('max_trials must be a positive integer')
        if self.max_trials > 6:
            raise ValueError('max_trials must be <= 6')
        if len(self.trials) > self.max_trials:
            raise ValueError('trial count exceeds max_trials')
        for index, trial in enumerate(self.trials):
            if not isinstance(trial, dict):
                raise ValueError(f'trial {index} must be a mapping')
            if 'orbit' not in trial or not isinstance(trial['orbit'], dict):
                raise ValueError(f'trial {index} needs an orbit mapping')
            payload = trial.get('payload_t')
            if not isinstance(payload, (int, float)) or isinstance(payload, bool) or payload <= 0:
                raise ValueError(f'trial {index} needs a positive payload_t')
        if not self.campaign_id:
            self.campaign_id = _stable_hash({
                'profile': self.rocket_profile_id,
                'rocket_sha256': self.rocket_sha256,
                'trials': self.trials,
                'program': self.program,
                'tolerances': self.tolerances,
                'max_trials': self.max_trials,
            })[:16]

    def trial_specs(self) -> list[TrialSpec]:
        return [TrialSpec(
            rocket_sha256=self.rocket_sha256,
            payload_t=float(trial['payload_t']),
            orbit=dict(trial['orbit']),
            program=dict(self.program),
            tolerances=dict(self.tolerances),
            budget={'max_trials': self.max_trials},
        ) for trial in self.trials]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrialRecord:
    trial_id: str
    spec: TrialSpec
    state: str = QUEUED
    attempts: int = 0
    result: TrialResult | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            'trial_id': self.trial_id,
            'spec': self.spec.to_dict(),
            'state': self.state,
            'attempts': self.attempts,
            'result': self.result.to_dict() if self.result is not None else None,
            'note': self.note,
        }


class CampaignQueue:
    """Deterministic trial state machine. One running trial at a time."""

    def __init__(self, spec: CampaignSpec):
        if not isinstance(spec, CampaignSpec):
            raise TypeError('spec must be a CampaignSpec')
        self.spec = spec
        self._records = []
        for index, trial_spec in enumerate(spec.trial_specs(), start=1):
            self._records.append(TrialRecord(
                trial_id=f'{spec.campaign_id}-{index:04d}', spec=trial_spec))
        self._by_id = {record.trial_id: record for record in self._records}

    @property
    def records(self) -> list[TrialRecord]:
        return list(self._records)

    def get(self, trial_id: str) -> TrialRecord:
        try:
            return self._by_id[trial_id]
        except KeyError:
            raise KeyError(f'unknown trial: {trial_id}')

    def next_queued(self) -> TrialRecord | None:
        return next((record for record in self._records if record.state == QUEUED), None)

    def running(self) -> TrialRecord | None:
        return next((record for record in self._records if record.state == RUNNING), None)

    def start(self, trial_id: str) -> TrialRecord:
        record = self.get(trial_id)
        if record.state != QUEUED:
            raise ValueError(f'trial {trial_id} is {record.state}, not queued')
        if self.running() is not None:
            raise ValueError('another trial is already running')
        record.state = RUNNING
        record.attempts += 1
        return record

    def finish(self, trial_id: str, result: TrialResult) -> TrialRecord:
        if not isinstance(result, TrialResult):
            raise TypeError('result must be a TrialResult')
        record = self.get(trial_id)
        if record.state != RUNNING:
            raise ValueError(f'trial {trial_id} is {record.state}, not running')
        record.result = result
        record.state = COMPLETED if result.status == 'verified' else FAILED
        return record

    def fail(self, trial_id: str, failure_code: str, note: str | None = None) -> TrialRecord:
        record = self.get(trial_id)
        if record.state != RUNNING:
            raise ValueError(f'trial {trial_id} is {record.state}, not running')
        record.result = TrialResult(spec_id=trial_id, status='failed',
                                    failure_code=failure_code, note=note)
        record.state = FAILED
        record.note = note
        return record

    def cancel(self, trial_id: str) -> TrialRecord:
        record = self.get(trial_id)
        if record.state not in (QUEUED, RUNNING):
            raise ValueError(f'trial {trial_id} is already {record.state}')
        record.state = CANCELLED
        return record

    def stats(self) -> dict:
        counts = {state: 0 for state in (QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED)}
        for record in self._records:
            counts[record.state] += 1
        return {'campaign_id': self.spec.campaign_id,
                'total': len(self._records), **counts}

    def snapshot(self) -> dict:
        return {'spec': self.spec.to_dict(),
                'stats': self.stats(),
                'trials': [record.to_dict() for record in self._records]}
