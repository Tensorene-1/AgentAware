# AgentAware: Adaptive Resource Management for AI Agent Workloads on Windows

AgentAware is a Windows-based project that explores adaptive resource management for AI agent workloads. It monitors system resource usage, classifies workload conditions, and applies resource management policies to improve responsiveness and resource utilization.

## Features

- **Resource Monitoring:** Tracks system resource usage and workload conditions.
- **Workload Classification:** Uses a machine learning classifier to identify workload states.
- **Adaptive Policy Engine:** Determines suitable resource management actions based on workload conditions.
- **Resource Enforcement:** Integrates Python control logic with a C++ enforcement component.
- **Simulation:** Evaluates adaptive resource management under different workload conditions.
- **Performance Evaluation:** Generates charts and JSON results to analyze system behavior.

## Tech Stack

- Python
- C++
- scikit-learn
- NumPy
- Matplotlib
- psutil
- CMake

## Project Structure

```text
AgentAware/
├── cpp/
│   ├── CMakeLists.txt
│   ├── enforcement.cpp
│   └── enforcement.h
├── python/
│   ├── classifier.py
│   ├── controller.py
│   ├── enforcement_interface.py
│   ├── monitor.py
│   ├── policy_engine.py
│   └── simulate.py
├── results/
│   ├── evaluation_charts.png
│   ├── evaluation_results.json
│   ├── sensitivity.json
│   └── sensitivity_chart.png
└── README.md
```

## How It Works

1. **Monitor:** Collects system resource and workload information.
2. **Classify:** Identifies workload conditions using the trained classifier.
3. **Decide:** The policy engine selects an appropriate resource management action.
4. **Enforce:** The controller communicates with the enforcement component to apply the selected action.
5. **Evaluate:** Simulation results are collected and visualized to assess performance.

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/Tensorene-1/AgentAware.git
cd AgentAware
```

### 2. Install Python Dependencies

Ensure Python is installed, then install the required packages:

```bash
pip install numpy pandas matplotlib scikit-learn psutil
```

### 3. Run the Simulation

From the project root, run:

```bash
python python/simulate.py
```

The simulation may generate evaluation results and charts, depending on the implementation.

## Results

The `results/` directory contains evaluation data and visualizations used to analyze workload classification, resource management behavior, and sensitivity to workload changes.

## Platform

Designed for Windows environments, with Python-based monitoring and control logic and a C++ enforcement component.

## Future Improvements

- Improve workload classification accuracy.
- Refine adaptive resource allocation policies.
- Expand workload scenarios and evaluation metrics.
- Integrate more advanced AI agent workload scheduling strategies.

## Author

Developed as a project exploring adaptive resource management for AI agent workloads.
