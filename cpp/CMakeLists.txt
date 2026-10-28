# CMakeLists.txt -- builds agentaware_enforce.dll from enforcement.cpp
#
# Usage (from an x64 Native Tools Command Prompt / any CMake+MSVC setup):
#   cmake -B build -A x64
#   cmake --build build --config Release
#
# Produces build/Release/agentaware_enforce.dll, loaded by the Python
# control loop via enforcement_interface.WindowsEnforcementBackend.

cmake_minimum_required(VERSION 3.20)
project(AgentAwareEnforcement LANGUAGES CXX)

if(NOT WIN32)
    message(WARNING
        "enforcement.cpp targets the Win32/NT API (Job Objects, "
        "NtSetInformationProcess) and can only be built on Windows. "
        "On other platforms, use enforcement_interface.SimulatedEnforcementBackend.")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(agentaware_enforce SHARED
    enforcement.cpp
    enforcement.h
)

target_compile_definitions(agentaware_enforce PRIVATE AGENTAWARE_EXPORTS)

if(WIN32)
    target_link_libraries(agentaware_enforce PRIVATE ntdll)
endif()

set_target_properties(agentaware_enforce PROPERTIES
    OUTPUT_NAME "agentaware_enforce"
)
