// enforcement.cpp -- AgentAware native enforcement layer (Windows).
//
// Implements resource-policy enforcement using three Win32/NT
// mechanisms, chosen because together they cover CPU scheduling, CPU
// rate limiting, disk I/O priority, memory footprint, and core
// placement for an entire agent process tree:
//
//   1. Job Objects (CreateJobObject / AssignProcessToJobObject /
//      SetInformationJobObject) -- group the agent and every subprocess
//      it spawns so limits apply to the whole tree at once, and to cap
//      CPU usage via JOBOBJECT_CPU_RATE_CONTROL_INFORMATION.
//   2. SetPriorityClass -- Windows scheduling priority for the process.
//   3. NtSetInformationProcess(ProcessIoPriority) -- per-process I/O
//      priority, undocumented but stable since Vista and used by
//      Windows itself for background task throttling.
//
// Build: cl /LD /DAGENTAWARE_EXPORTS enforcement.cpp /Fe:agentaware_enforce.dll
// (or via CMake -- see CMakeLists.txt)

#include "enforcement.h"

#include <windows.h>
#include <winternl.h>
#include <string>
#include <unordered_map>
#include <mutex>

#pragma comment(lib, "ntdll.lib")

namespace {

// NtSetInformationProcess / ProcessIoPriority are documented informally
// (MSDN "Process and Thread Priority") but not exposed in winternl.h;
// declare the pieces we need locally.
constexpr int ProcessIoPriority = 33;

typedef NTSTATUS(NTAPI* NtSetInformationProcess_t)(
    HANDLE ProcessHandle,
    ULONG ProcessInformationClass,
    PVOID ProcessInformation,
    ULONG ProcessInformationLength);

enum IoPriorityHint {
    IoPriorityVeryLow = 0,
    IoPriorityLow = 1,
    IoPriorityNormal = 2,
    IoPriorityHigh = 3,
};

struct JobHandle {
    HANDLE hJob = nullptr;
    HANDLE hProcess = nullptr;
};

std::mutex g_mutex;
std::unordered_map<uint32_t, JobHandle> g_jobs;

DWORD PriorityClassFromString(const std::string& name) {
    if (name == "IDLE_PRIORITY_CLASS") return IDLE_PRIORITY_CLASS;
    if (name == "BELOW_NORMAL_PRIORITY_CLASS") return BELOW_NORMAL_PRIORITY_CLASS;
    if (name == "NORMAL_PRIORITY_CLASS") return NORMAL_PRIORITY_CLASS;
    if (name == "ABOVE_NORMAL_PRIORITY_CLASS") return ABOVE_NORMAL_PRIORITY_CLASS;
    if (name == "HIGH_PRIORITY_CLASS") return HIGH_PRIORITY_CLASS;
    return NORMAL_PRIORITY_CLASS;
}

IoPriorityHint IoPriorityFromString(const std::string& name) {
    if (name == "IoPriorityVeryLow") return IoPriorityVeryLow;
    if (name == "IoPriorityLow") return IoPriorityLow;
    if (name == "IoPriorityHigh") return IoPriorityHigh;
    return IoPriorityNormal;
}

bool SetIoPriority(HANDLE hProcess, IoPriorityHint hint) {
    static auto pNtSetInformationProcess =
        reinterpret_cast<NtSetInformationProcess_t>(
            GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "NtSetInformationProcess"));
    if (!pNtSetInformationProcess) return false;

    ULONG value = static_cast<ULONG>(hint);
    NTSTATUS status = pNtSetInformationProcess(
        hProcess, ProcessIoPriority, &value, sizeof(value));
    return status >= 0; // NT_SUCCESS
}

bool SetCpuRateLimit(HANDLE hJob, int32_t percent) {
    JOBOBJECT_CPU_RATE_CONTROL_INFORMATION cpuInfo = {};
    if (percent <= 0) {
        // Explicitly disable rate control -- uncapped.
        cpuInfo.ControlFlags = JOB_OBJECT_CPU_RATE_CONTROL_NOTIFY;
        return SetInformationJobObject(
            hJob, JobObjectCpuRateControlInformation, &cpuInfo, sizeof(cpuInfo)) != 0;
    }
    // CpuRate is expressed in units of 1/10000 of total CPU; e.g. 40% -> 400000.
    cpuInfo.ControlFlags = JOB_OBJECT_CPU_RATE_CONTROL_ENABLE |
                            JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP;
    cpuInfo.CpuRate = static_cast<DWORD>(percent) * 100; // percent * 10000 / 100
    return SetInformationJobObject(
        hJob, JobObjectCpuRateControlInformation, &cpuInfo, sizeof(cpuInfo)) != 0;
}

bool SetAffinity(HANDLE hJob, int32_t coreCount) {
    if (coreCount <= 0) return true; // 0 = leave default (all cores)

    SYSTEM_INFO sysInfo;
    GetSystemInfo(&sysInfo);
    DWORD_PTR mask = 0;
    int usable = min<int>(coreCount, static_cast<int>(sysInfo.dwNumberOfProcessors));
    for (int i = 0; i < usable; ++i) mask |= (static_cast<DWORD_PTR>(1) << i);

    JOBOBJECT_BASIC_LIMIT_INFORMATION basicInfo = {};
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION extInfo = {};
    extInfo.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_AFFINITY;
    extInfo.BasicLimitInformation.Affinity = mask;
    return SetInformationJobObject(
        hJob, JobObjectExtendedLimitInformation, &extInfo, sizeof(extInfo)) != 0;
}

bool SetWorkingSetHint(HANDLE hJob, int32_t hintMb) {
    if (hintMb <= 0) return true; // 0 = OS default

    JOBOBJECT_EXTENDED_LIMIT_INFORMATION extInfo = {};
    extInfo.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_WORKINGSET;
    SIZE_T bytes = static_cast<SIZE_T>(hintMb) * 1024 * 1024;
    extInfo.BasicLimitInformation.MinimumWorkingSetSize = bytes / 2;
    extInfo.BasicLimitInformation.MaximumWorkingSetSize = bytes;
    return SetInformationJobObject(
        hJob, JobObjectExtendedLimitInformation, &extInfo, sizeof(extInfo)) != 0;
}

} // namespace

extern "C" {

AGENTAWARE_API bool AgentAware_AttachToJob(uint32_t pid) {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_jobs.count(pid)) return true; // already attached

    HANDLE hProcess = OpenProcess(
        PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SET_INFORMATION |
        PROCESS_QUERY_INFORMATION,
        FALSE, pid);
    if (!hProcess) return false;

    HANDLE hJob = CreateJobObjectW(nullptr, nullptr);
    if (!hJob) {
        CloseHandle(hProcess);
        return false;
    }

    // JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK / default settings allow child
    // processes spawned by tool calls to remain in the same job so
    // aggregate limits apply to the whole agent tree.
    if (!AssignProcessToJobObject(hJob, hProcess)) {
        CloseHandle(hJob);
        CloseHandle(hProcess);
        return false;
    }

    g_jobs[pid] = JobHandle{hJob, hProcess};
    return true;
}

AGENTAWARE_API bool AgentAware_ApplyPolicy(
    uint32_t pid,
    const char* priorityClass,
    const char* ioPriority,
    int32_t cpuRateLimitPct,
    int32_t affinityCores,
    int32_t workingSetHintMb) {

    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_jobs.find(pid);
    if (it == g_jobs.end()) return false; // must AttachToJob first

    JobHandle& jh = it->second;
    bool ok = true;

    ok &= SetPriorityClass(jh.hProcess, PriorityClassFromString(priorityClass)) != 0;
    ok &= SetIoPriority(jh.hProcess, IoPriorityFromString(ioPriority));
    ok &= SetCpuRateLimit(jh.hJob, cpuRateLimitPct);
    ok &= SetAffinity(jh.hJob, affinityCores);
    ok &= SetWorkingSetHint(jh.hJob, workingSetHintMb);

    return ok;
}

AGENTAWARE_API bool AgentAware_Detach(uint32_t pid) {
    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_jobs.find(pid);
    if (it == g_jobs.end()) return false;

    // Restore default scheduling before releasing the job.
    SetPriorityClass(it->second.hProcess, NORMAL_PRIORITY_CLASS);
    CloseHandle(it->second.hJob);
    CloseHandle(it->second.hProcess);
    g_jobs.erase(it);
    return true;
}

} // extern "C"
