"""Synthetic lease model, not a production authorization or browser adapter."""

from dataclasses import dataclass


@dataclass
class Lease:
    epoch: int
    expires_at: float
    active: bool = True


class Guard:
    def __init__(self, lease, clock):
        self.lease, self.clock = lease, clock
        self.results, self.uncertain, self.inflight = [], False, False

    def valid(self, epoch):
        return (self.lease.active and epoch == self.lease.epoch and
                self.clock() < self.lease.expires_at)

    def dispatch(self, epoch, effect):
        if not self.valid(epoch):
            raise RuntimeError('JOB_FENCED')
        self.inflight = True
        try:
            return effect()
        finally:
            if not self.valid(epoch):
                self.uncertain = True
            self.inflight = False

    def publish(self, epoch, result):
        if not self.valid(epoch) or self.uncertain:
            return False
        self.results.append(result)
        return True

    def revoke(self):
        if self.inflight:
            self.uncertain = True
        self.lease.active = False


class FixtureBridge:
    def __init__(self, guard, epoch):
        self.guard, self.epoch, self.fallback_attempts = guard, epoch, 0

    def claim_profile(self, profile_id=None):
        return {'profile_id': 'fixture-profile', 'runner_id': 'fixture-runner'}

    def get_browser_visibility(self, profile_id):
        return True

    def release_profile(self, profile_id, runner_id):
        return self.guard.valid(self.epoch)

    def record_profile_output(self, profile_id, runner_id, receipt, payment, link):
        return self.guard.publish(self.epoch, {'kind': 'fixture-output'})

    def mark_profile_unusable(self, profile_id, error_text):
        return self.guard.publish(self.epoch, {'kind': 'fixture-unusable'})

    def heartbeat_profile(self, profile_id, runner_id):
        return self.guard.valid(self.epoch)

    def mark_profile_success(self, title, national, imei, link=''):
        self.fallback_attempts += 1
        return self.guard.publish(self.epoch, {'kind': 'fixture-success'})
