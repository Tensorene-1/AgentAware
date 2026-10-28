"""
simulate.py -- synthetic evaluation harness for AgentAware.

There is no live Windows deployment available in this development
environment, so this harness evaluates the monitor -> classifier ->
policy pipeline against SYNTHETIC agent workload traces rather than a
real agent. Every trace is generated from a known ground-truth phase
schedule (e.g. "idle for 3s, then compute for 6s, then io_burst for 2s"),
which lets us report two honest, reproducible things:

  1. Classifier accuracy: how often WorkloadClassifier's label matches
     the ground-truth phase that generated the sample.
  2. A queueing-model estimate of task completion time and CPU-headroom
     impact under AgentAware's adaptive policy vs. a static baseline
     (fixed NORMAL priority, no I/O prioritization, no CPU capping) --
     using the CPU rate limits / priority weights from policy_engine.py
     as the model's parameters.

This is explicitly a controlled simulation, not a field measurement on
real agent workloads or real Windows scheduling -- the paper reports it
as preliminary evidence that the classifier and policy logic behave as
intended, pending deployment on a live Windows testbed.
"""

from __future__ import annotations

import json
import random
import statistics
import dataclasses
from typing import List, Tuple

from monitor import Sample
from classifier import WorkloadClassifier, WorkloadClass, ClassifierThresholds
from policy_engine import PolicyEngine, POLICY_TABLE

random.seed(7)  # reproducibility


# --- Ground-truth phase profiles ------------------------------------------
# (cpu_percent_mean, cpu_percent_std, io_bytes_s_mean, io_bytes_s_std, threads)
PHASE_PROFILES = {
    WorkloadClass.COMPUTE:  (78.0, 6.0,      50_000,     20_000, 6),
    WorkloadClass.IO_BURST: (25.0, 8.0,   9_000_000,  2_000_000, 5),
    WorkloadClass.NET_WAIT: (6.0,  3.0,      30_000,     15_000, 9),
    WorkloadClass.IDLE:     (2.0,  1.5,      5_000,       3_000, 2),
    WorkloadClass.BURSTY:   (55.0, 35.0,  1_500_000,  1_400_000, 7),
}


@dataclasses.dataclass
class LabeledSample:
    sample: Sample
    ground_truth: WorkloadClass


def generate_trace(phase_schedule: List[Tuple[WorkloadClass, int]],
                    dt: float = 0.5) -> List[LabeledSample]:
    """phase_schedule: list of (WorkloadClass, num_samples) in sequence."""
    trace = []
    t = 0.0
    for phase, n in phase_schedule:
        cpu_mu, cpu_sd, io_mu, io_sd, threads = PHASE_PROFILES[phase]
        for _ in range(n):
            cpu = max(0.0, random.gauss(cpu_mu, cpu_sd))
            io = max(0.0, random.gauss(io_mu, io_sd))
            s = Sample(
                timestamp=t,
                cpu_percent=cpu,
                memory_rss_mb=200 + cpu * 1.5,
                io_read_bytes_s=io * 0.6,
                io_write_bytes_s=io * 0.4,
                num_processes=1 + (threads // 4),
                num_threads=threads,
                task_elapsed_s=t,
            )
            trace.append(LabeledSample(sample=s, ground_truth=phase))
            t += dt
    return trace


def build_agent_session_trace() -> List[LabeledSample]:
    """A representative agent session: plan (compute) -> read context (io)
    -> call a tool over the network (net_wait) -> interleaved tool-use
    (bursty) -> idle between user turns -> final compute pass."""
    schedule = [
        (WorkloadClass.COMPUTE, 12),
        (WorkloadClass.IO_BURST, 8),
        (WorkloadClass.NET_WAIT, 14),
        (WorkloadClass.BURSTY, 16),
        (WorkloadClass.IDLE, 10),
        (WorkloadClass.COMPUTE, 10),
    ]
    return generate_trace(schedule)


# --- Classifier accuracy ---------------------------------------------------

def evaluate_classifier_accuracy(trace: List[LabeledSample]) -> dict:
    clf = WorkloadClassifier(ClassifierThresholds())
    correct = 0
    confusion = {}
    predictions = []
    for ls in trace:
        pred = clf.push(ls.sample)
        predictions.append(pred)
        confusion.setdefault(ls.ground_truth.value, {}).setdefault(pred.value, 0)
        confusion[ls.ground_truth.value][pred.value] += 1
        if pred == ls.ground_truth:
            correct += 1
    return {
        "accuracy": correct / len(trace),
        "n_samples": len(trace),
        "confusion_matrix": confusion,
    }


# --- Completion-time / responsiveness model --------------------------------
#
# Simple analytic model: each phase's *effective* processing rate scales
# with the CPU share the policy grants it (relative to a 100%-available
# core), and I/O-bound phases additionally scale with I/O priority.
# Baseline = always NORMAL_PRIORITY_CLASS, no CPU cap, no IO priority
# boost (Windows default scheduling for a foreground process).
# AgentAware = the policy this workload class actually receives from
# POLICY_TABLE.

IO_PRIORITY_SPEEDUP = {
    "IoPriorityVeryLow": 0.6,
    "IoPriorityLow": 0.85,
    "IoPriorityNormal": 1.0,
    "IoPriorityHigh": 1.3,
}

BASELINE_CPU_SHARE = 1.0        # uncapped, normal priority
BASELINE_IO_SPEEDUP = 1.0       # normal I/O priority


def effective_rate(policy, phase: WorkloadClass) -> float:
    cpu_share = 1.0 if policy.cpu_rate_limit_percent == 0 else policy.cpu_rate_limit_percent / 100.0
    io_speedup = IO_PRIORITY_SPEEDUP[policy.io_priority.value]
    if phase in (WorkloadClass.IO_BURST,):
        return io_speedup                       # I/O-bound: I/O priority dominates
    if phase in (WorkloadClass.NET_WAIT, WorkloadClass.IDLE):
        return 1.0                               # blocked/idle: rate limits don't matter
    return cpu_share                             # compute/bursty: CPU share dominates


def simulate_completion_time(trace: List[LabeledSample], dt: float = 0.5) -> dict:
    engine = PolicyEngine()
    clf = WorkloadClassifier(ClassifierThresholds())

    baseline_time = 0.0
    agentaware_time = 0.0
    other_app_penalty_baseline = 0.0   # cycles stolen from other apps
    other_app_penalty_agentaware = 0.0
    policy_switches = 0
    last_workload = None

    for ls in trace:
        pred = clf.push(ls.sample)
        policy = engine.decide(pred)
        if pred != last_workload:
            policy_switches += 1
            last_workload = pred

        base_rate = effective_rate(
            type("P", (), {
                "cpu_rate_limit_percent": 0,
                "io_priority": type("E", (), {"value": "IoPriorityNormal"})(),
            })(), ls.ground_truth)
        aa_rate = effective_rate(policy, ls.ground_truth)

        # Work "done" per interval is proportional to rate; time to finish
        # a fixed unit of work per phase sample is inversely proportional.
        baseline_time += dt / max(base_rate, 0.05)
        agentaware_time += dt / max(aa_rate, 0.05)

        # Foreground-app responsiveness penalty: how much CPU the agent
        # holds when NOT compute-bound (i.e. cycles it doesn't need but
        # would still contend for under fixed NORMAL priority).
        if ls.ground_truth in (WorkloadClass.NET_WAIT, WorkloadClass.IDLE, WorkloadClass.IO_BURST):
            other_app_penalty_baseline += dt * 1.0   # holds normal priority regardless
            other_app_penalty_agentaware += dt * (policy.cpu_rate_limit_percent / 100.0 if policy.cpu_rate_limit_percent else 1.0)

    return {
        "baseline_task_time_s": round(baseline_time, 2),
        "agentaware_task_time_s": round(agentaware_time, 2),
        "task_time_improvement_pct": round(
            100.0 * (baseline_time - agentaware_time) / baseline_time, 2),
        "policy_switches": policy_switches,
        "other_app_contention_baseline": round(other_app_penalty_baseline, 2),
        "other_app_contention_agentaware": round(other_app_penalty_agentaware, 2),
        "responsiveness_improvement_pct": round(
            100.0 * (other_app_penalty_baseline - other_app_penalty_agentaware)
            / other_app_penalty_baseline, 2) if other_app_penalty_baseline else 0.0,
    }


def run_full_evaluation(n_sessions: int = 30) -> dict:
    acc_results = []
    time_results = []
    for _ in range(n_sessions):
        trace = build_agent_session_trace()
        acc_results.append(evaluate_classifier_accuracy(trace))
        time_results.append(simulate_completion_time(trace))

    return {
        "n_sessions": n_sessions,
        "samples_per_session": len(build_agent_session_trace()),
        "classifier_accuracy_mean": round(statistics.mean(r["accuracy"] for r in acc_results), 4),
        "classifier_accuracy_stdev": round(statistics.pstdev(r["accuracy"] for r in acc_results), 4),
        "task_time_improvement_pct_mean": round(statistics.mean(r["task_time_improvement_pct"] for r in time_results), 2),
        "task_time_improvement_pct_stdev": round(statistics.pstdev(r["task_time_improvement_pct"] for r in time_results), 2),
        "responsiveness_improvement_pct_mean": round(statistics.mean(r["responsiveness_improvement_pct"] for r in time_results), 2),
        "avg_policy_switches_per_session": round(statistics.mean(r["policy_switches"] for r in time_results), 2),
        "sample_confusion_matrix": acc_results[0]["confusion_matrix"],
        "sample_time_result": time_results[0],
    }


if __name__ == "__main__":
    results = run_full_evaluation(n_sessions=30)
    print(json.dumps(results, indent=2))
    with open("../results/evaluation_results.json", "w") as f:
        json.dump(results, f, indent=2)
