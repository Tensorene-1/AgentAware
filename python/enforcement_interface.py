"""
enforcement_interface.py -- abstract enforcement contract.

On Windows, AgentAware's controller talks to the native enforcement
module (cpp/enforcement.cpp, exposed as agentaware_enforce.dll) through
this same interface via ctypes. Decoupling the interface from the backend
lets the Python-side control loop (monitor -> classifier -> policy
engine) be developed and evaluated on any platform, while the actual
Job-Object / priority-class calls stay in native code where the Win32 API
lives.

Two backends are provided:
  - WindowsEnforcementBackend: thin ctypes wrapper calling into the
    compiled agentaware_enforce.dll (Windows only).
  - SimulatedEnforcementBackend: records the policy that *would* have
    been applied and models its effect on a synthetic workload, used for
    cross-platform evaluation (see simulate.py).
"""

from __future__ import annotations

import ctypes
import platform
import dataclasses
from typing import List

from policy_engine import ResourcePolicy


class EnforcementBackend:
    def apply(self, pid: int, policy: ResourcePolicy) -> bool:
        raise NotImplementedError


class WindowsEnforcementBackend(EnforcementBackend):
    """ctypes bridge to agentaware_enforce.dll (built from cpp/enforcement.cpp)."""

    def __init__(self, dll_path: str = "agentaware_enforce.dll"):
        if platform.system() != "Windows":
            raise RuntimeError("WindowsEnforcementBackend requires Windows")
        self.dll = ctypes.WinDLL(dll_path)
        self.dll.AgentAware_ApplyPolicy.argtypes = [
            ctypes.c_uint32,  # pid
            ctypes.c_char_p,  # priority class name
            ctypes.c_char_p,  # io priority name
            ctypes.c_int32,   # cpu rate limit percent
            ctypes.c_int32,   # affinity core count
            ctypes.c_int32,   # working set hint MB
        ]
        self.dll.AgentAware_ApplyPolicy.restype = ctypes.c_bool

    def apply(self, pid: int, policy: ResourcePolicy) -> bool:
        return bool(self.dll.AgentAware_ApplyPolicy(
            pid,
            policy.priority_class.value.encode(),
            policy.io_priority.value.encode(),
            policy.cpu_rate_limit_percent,
            policy.affinity_core_count,
            policy.working_set_hint_mb,
        ))


@dataclasses.dataclass
class AppliedPolicyRecord:
    timestamp: float
    pid: int
    policy_name: str
    cpu_rate_limit_percent: int


class SimulatedEnforcementBackend(EnforcementBackend):
    """Records applied policies for evaluation without touching the OS."""

    def __init__(self):
        self.history: List[AppliedPolicyRecord] = []

    def apply(self, pid: int, policy: ResourcePolicy) -> bool:
        import time
        self.history.append(AppliedPolicyRecord(
            timestamp=time.time(),
            pid=pid,
            policy_name=policy.name,
            cpu_rate_limit_percent=policy.cpu_rate_limit_percent,
        ))
        return True
