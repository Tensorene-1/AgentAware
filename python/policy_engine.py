"""
policy_engine.py -- AgentAware policy engine.

Maps a WorkloadClass onto a concrete ResourcePolicy: a small, serializable
description of scheduling priority, CPU affinity/rate limits, I/O
priority, and memory working-set hints. The policy engine is intentionally
decoupled from enforcement -- it only *decides*; the enforcement layer
(cpp/enforcement.cpp) is what actually calls into the Windows API.

The mapping table is external (a plain dict here, JSON/YAML in a full
deployment) so policies can be re-tuned without touching classifier or
monitor code.
"""

from __future__ import annotations

import dataclasses
from enum import Enum

from classifier import WorkloadClass


class PriorityClass(str, Enum):
    IDLE = "IDLE_PRIORITY_CLASS"
    BELOW_NORMAL = "BELOW_NORMAL_PRIORITY_CLASS"
    NORMAL = "NORMAL_PRIORITY_CLASS"
    ABOVE_NORMAL = "ABOVE_NORMAL_PRIORITY_CLASS"
    HIGH = "HIGH_PRIORITY_CLASS"


class IoPriority(str, Enum):
    VERY_LOW = "IoPriorityVeryLow"
    LOW = "IoPriorityLow"
    NORMAL = "IoPriorityNormal"
    HIGH = "IoPriorityHigh"


@dataclasses.dataclass(frozen=True)
class ResourcePolicy:
    name: str
    priority_class: PriorityClass
    io_priority: IoPriority
    cpu_rate_limit_percent: int      # JOBOBJECT_CPU_RATE_CONTROL cap, 0 = uncapped
    affinity_core_count: int         # cores to pin the job to, 0 = all available
    working_set_hint_mb: int         # soft working-set ceiling, 0 = OS default


# The core mapping: workload class -> resource policy.
POLICY_TABLE = {
    WorkloadClass.COMPUTE: ResourcePolicy(
        name="compute-boost",
        priority_class=PriorityClass.ABOVE_NORMAL,
        io_priority=IoPriority.NORMAL,
        cpu_rate_limit_percent=0,       # let it use available cores
        affinity_core_count=0,
        working_set_hint_mb=0,
    ),
    WorkloadClass.IO_BURST: ResourcePolicy(
        name="io-priority-boost",
        priority_class=PriorityClass.NORMAL,
        io_priority=IoPriority.HIGH,
        cpu_rate_limit_percent=40,      # I/O-bound: cap CPU to free cycles
        affinity_core_count=0,
        working_set_hint_mb=0,
    ),
    WorkloadClass.NET_WAIT: ResourcePolicy(
        name="background-wait",
        priority_class=PriorityClass.BELOW_NORMAL,
        io_priority=IoPriority.LOW,
        cpu_rate_limit_percent=15,      # mostly blocked; don't reserve cycles
        affinity_core_count=0,
        working_set_hint_mb=0,
    ),
    WorkloadClass.IDLE: ResourcePolicy(
        name="yield-to-foreground",
        priority_class=PriorityClass.IDLE,
        io_priority=IoPriority.VERY_LOW,
        cpu_rate_limit_percent=5,
        affinity_core_count=1,
        working_set_hint_mb=256,        # trim working set while idle
    ),
    WorkloadClass.BURSTY: ResourcePolicy(
        name="balanced-responsive",
        priority_class=PriorityClass.NORMAL,
        io_priority=IoPriority.NORMAL,
        cpu_rate_limit_percent=70,      # headroom for spikes, capped for fairness
        affinity_core_count=0,
        working_set_hint_mb=0,
    ),
}


class PolicyEngine:
    def __init__(self, table=None):
        self.table = table or POLICY_TABLE
        self._current: ResourcePolicy | None = None

    def decide(self, workload: WorkloadClass) -> ResourcePolicy:
        policy = self.table[workload]
        self._current = policy
        return policy

    @property
    def current(self) -> ResourcePolicy | None:
        return self._current
