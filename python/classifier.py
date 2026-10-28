"""
classifier.py -- AgentAware workload classifier.

Converts a sliding window of telemetry Samples into a discrete workload
label. The reference implementation is deliberately rule-based: it is
transparent, cheap to run every sampling interval, and easy to validate
against a labeled baseline trace before any learned model is considered.

Workload classes
-----------------
COMPUTE   -- sustained high CPU, low I/O (model inference, local reasoning,
             heavy in-process computation)
IO_BURST  -- low/moderate CPU, high disk I/O (file reads/writes, large
             context loads, checkpointing)
NET_WAIT  -- low CPU, low disk I/O, process count/thread count elevated but
             mostly blocked (waiting on an external API / tool call)
IDLE      -- low CPU, low I/O, little activity (agent is "thinking" between
             steps or waiting on the user)
BURSTY    -- high variance across the window; alternating spikes that do not
             settle into one of the above (typical of an agent rapidly
             interleaving reasoning and tool calls)
"""

from __future__ import annotations

import statistics
import dataclasses
from collections import deque
from enum import Enum
from typing import Deque, List

from monitor import Sample


class WorkloadClass(str, Enum):
    COMPUTE = "COMPUTE"
    IO_BURST = "IO_BURST"
    NET_WAIT = "NET_WAIT"
    IDLE = "IDLE"
    BURSTY = "BURSTY"


@dataclasses.dataclass
class ClassifierThresholds:
    cpu_high: float = 60.0          
    cpu_low: float = 10.0
    io_high_bytes_s: float = 5_000_000  
    cpu_cv_bursty: float = 0.6      
    window_size: int = 3            


class WorkloadClassifier:
    def __init__(self, thresholds: ClassifierThresholds = ClassifierThresholds()):
        self.t = thresholds
        self.window: Deque[Sample] = deque(maxlen=self.t.window_size)

    def push(self, sample: Sample) -> WorkloadClass:
        self.window.append(sample)
        return self.classify()

    def classify(self) -> WorkloadClass:
        if not self.window:
            return WorkloadClass.IDLE

        cpu_vals = [s.cpu_percent for s in self.window]
        io_vals = [s.io_read_bytes_s + s.io_write_bytes_s for s in self.window]

        cpu_mean = statistics.mean(cpu_vals)
        io_mean = statistics.mean(io_vals)

        if len(cpu_vals) >= 3 and cpu_mean > 1e-6:
            cpu_cv = (statistics.pstdev(cpu_vals) / cpu_mean)
        else:
            cpu_cv = 0.0

        
        if cpu_cv > self.t.cpu_cv_bursty and cpu_mean > self.t.cpu_low:
            return WorkloadClass.BURSTY

        if cpu_mean >= self.t.cpu_high and io_mean < self.t.io_high_bytes_s:
            return WorkloadClass.COMPUTE

        if io_mean >= self.t.io_high_bytes_s:
            return WorkloadClass.IO_BURST

        if cpu_mean < self.t.cpu_low and io_mean < self.t.io_high_bytes_s:
           
            avg_threads = statistics.mean(s.num_threads for s in self.window)
            if avg_threads > 4:
                return WorkloadClass.NET_WAIT
            return WorkloadClass.IDLE

        return WorkloadClass.COMPUTE  
