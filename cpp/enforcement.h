// enforcement.h -- AgentAware native enforcement layer (Windows).
//
// Exposes a small C ABI so the Python control loop (controller.py) can
// call into this module via ctypes, and so it can equally be linked
// directly into a C++/C# host application. All real interaction with the
// Windows scheduler and I/O subsystem happens behind this boundary.

#pragma once

#ifdef AGENTAWARE_EXPORTS
#define AGENTAWARE_API __declspec(dllexport)
#else
#define AGENTAWARE_API __declspec(dllimport)
#endif

#include <cstdint>

extern "C" {

// Wraps the target process (and, transitively, every child process it
// spawns) in a Windows Job Object so resource limits apply to the whole
// agent process tree, not just the root PID. Must be called once before
// AgentAware_ApplyPolicy for a given pid. Returns true on success.
AGENTAWARE_API bool AgentAware_AttachToJob(uint32_t pid);

// Applies a resource policy to the process tree previously attached with
// AgentAware_AttachToJob. Safe to call repeatedly as the workload
// classification changes; only the deltas that actually change job/
// priority settings incur syscalls.
//
//   priorityClass   : one of "IDLE_PRIORITY_CLASS", "BELOW_NORMAL_PRIORITY_CLASS",
//                     "NORMAL_PRIORITY_CLASS", "ABOVE_NORMAL_PRIORITY_CLASS",
//                     "HIGH_PRIORITY_CLASS"
//   ioPriority      : one of "IoPriorityVeryLow", "IoPriorityLow",
//                     "IoPriorityNormal", "IoPriorityHigh"
//   cpuRateLimitPct : 0 = uncapped, else JOBOBJECT_CPU_RATE_CONTROL hard cap
//   affinityCores   : 0 = all available cores, else pin to first N cores
//   workingSetHintMb: 0 = OS default, else soft working-set ceiling (MB)
AGENTAWARE_API bool AgentAware_ApplyPolicy(
    uint32_t pid,
    const char* priorityClass,
    const char* ioPriority,
    int32_t cpuRateLimitPct,
    int32_t affinityCores,
    int32_t workingSetHintMb);

// Releases the Job Object handle and restores default OS scheduling for
// the process tree. Call when the agent task completes.
AGENTAWARE_API bool AgentAware_Detach(uint32_t pid);

} // extern "C"
