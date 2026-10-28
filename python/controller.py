"""
controller.py -- AgentAware control loop.

Ties together monitoring, classification, policy decision, and
enforcement into the closed loop described in the architecture:

    agent process -> monitor -> classifier -> policy engine -> enforcement
                         ^                                          |
                         +---------------- feedback -----------------+

Each iteration samples the target process tree, classifies the current
workload, decides a policy, and applies it only when the workload class
has changed since the last iteration (to avoid needless syscalls /
re-priority churn on every tick).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from monitor import ProcessTreeMonitor, Sample
from classifier import WorkloadClassifier, WorkloadClass
from policy_engine import PolicyEngine, ResourcePolicy
from enforcement_interface import EnforcementBackend


@dataclass
class LoopEvent:
    sample: Sample
    workload: WorkloadClass
    policy: ResourcePolicy
    policy_changed: bool


class AgentAwareController:
    def __init__(
        self,
        root_pid: int,
        backend: EnforcementBackend,
        sample_interval_s: float = 0.5,
    ):
        self.monitor = ProcessTreeMonitor(root_pid, sample_interval_s)
        self.classifier = WorkloadClassifier()
        self.policy_engine = PolicyEngine()
        self.backend = backend
        self.root_pid = root_pid
        self._last_workload: Optional[WorkloadClass] = None

    def tick(self, sample: Sample) -> LoopEvent:
        workload = self.classifier.push(sample)
        policy = self.policy_engine.decide(workload)
        changed = workload != self._last_workload
        if changed:
            self.backend.apply(self.root_pid, policy)
            self._last_workload = workload
        return LoopEvent(sample=sample, workload=workload, policy=policy, policy_changed=changed)

    def run(self, max_iterations: Optional[int] = None):
        """Live-runs the loop against the real process tree (Windows/psutil)."""
        n = 0
        for sample in self.monitor.stream():
            event = self.tick(sample)
            yield event
            n += 1
            if max_iterations is not None and n >= max_iterations:
                break
