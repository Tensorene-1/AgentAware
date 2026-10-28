"""
monitor.py -- AgentAware telemetry / monitoring layer.

Collects per-interval resource metrics for a target process (and its
children, since agent frameworks routinely spawn subprocesses for tool
calls, code execution, or browser automation).

On Windows this module is designed to be backed by ETW (Event Tracing for
Windows) and PDH (Performance Data Helper) counters, or by Job Object
accounting information (QueryInformationJobObject /
JOBOBJECT_BASIC_ACCOUNTING_INFORMATION) when the agent tree is already
wrapped in a Job Object by the enforcement layer. For portability during
development and evaluation, this reference implementation uses the
cross-platform `psutil` library, which exposes an equivalent metric set
(CPU%, RSS, I/O counters, thread/handle count) via the same call shape
that the Windows-native backend would use. Swapping the backend is a
matter of implementing the same `Sample` producer interface.
"""

from __future__ import annotations

import time
import dataclasses
from typing import Optional, List

import psutil


@dataclasses.dataclass
class Sample:
    """A single telemetry snapshot for a process tree at time t."""

    timestamp: float
    cpu_percent: float          # aggregate CPU utilization across the tree
    memory_rss_mb: float        # aggregate resident set size
    io_read_bytes_s: float      # disk read rate since last sample
    io_write_bytes_s: float     # disk write rate since last sample
    num_processes: int          # size of the process tree (agent + children)
    num_threads: int
    task_elapsed_s: float       # wall-clock time since task start


class ProcessTreeMonitor:
    """Samples resource usage for a root process and all of its descendants.

    This mirrors what the Windows implementation would do with a Job
    Object: the job groups the agent and every subprocess it spawns, and
    QueryInformationJobObject gives the same aggregate counters (CPU
    time, I/O bytes, active process count) in a single call instead of
    walking the process tree by hand.
    """

    def __init__(self, root_pid: int, sample_interval_s: float = 0.5):
        self.root_pid = root_pid
        self.sample_interval_s = sample_interval_s
        self._task_start = time.time()
        self._last_io = None
        self._last_sample_time = None

    def _tree_pids(self) -> List[psutil.Process]:
        try:
            root = psutil.Process(self.root_pid)
        except psutil.NoSuchProcess:
            return []
        procs = [root]
        try:
            procs.extend(root.children(recursive=True))
        except psutil.NoSuchProcess:
            pass
        return [p for p in procs if p.is_running()]

    def sample(self) -> Optional[Sample]:
        procs = self._tree_pids()
        if not procs:
            return None

        now = time.time()
        cpu_total = 0.0
        mem_total = 0
        threads_total = 0
        io_read = 0
        io_write = 0

        for p in procs:
            try:
                cpu_total += p.cpu_percent(interval=None)
                mem_total += p.memory_info().rss
                threads_total += p.num_threads()
                io = p.io_counters()
                io_read += io.read_bytes
                io_write += io.write_bytes
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        io_read_rate = io_write_rate = 0.0
        if self._last_io is not None and self._last_sample_time is not None:
            dt = max(now - self._last_sample_time, 1e-6)
            io_read_rate = max((io_read - self._last_io[0]) / dt, 0.0)
            io_write_rate = max((io_write - self._last_io[1]) / dt, 0.0)

        self._last_io = (io_read, io_write)
        self._last_sample_time = now

        return Sample(
            timestamp=now,
            cpu_percent=cpu_total,
            memory_rss_mb=mem_total / (1024 * 1024),
            io_read_bytes_s=io_read_rate,
            io_write_bytes_s=io_write_rate,
            num_processes=len(procs),
            num_threads=threads_total,
            task_elapsed_s=now - self._task_start,
        )

    def stream(self):
        """Generator yielding a Sample roughly every sample_interval_s."""
        while True:
            s = self.sample()
            if s is not None:
                yield s
            time.sleep(self.sample_interval_s)
