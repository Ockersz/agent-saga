# AgentSaga-Gateway (ASG)

[![CI](https://github.com/Ockersz/agent-saga/actions/workflows/ci.yml/badge.svg)](https://github.com/Ockersz/agent-saga/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![OpenAPI 3.1](https://img.shields.io/badge/OpenAPI-3.1-green.svg)](https://spec.openapis.org/oas/v3.1.0)
[![Architecture: Zero--Trust](https://img.shields.io/badge/Architecture-Zero--Trust-orange.svg)](#architecture)

**AgentSaga-Gateway** is an open-source, decoupled reverse-proxy and transaction coordinator that enforces **dynamic contract verification** (OpenAPI 3.1) and **SAGA-based compensating rollbacks** for autonomous AI agents executing actions across distributed microservices.

---

## 🎯 The Problem

When autonomous AI agents (powered by frontier LLMs) execute multi-step plans across microservices:
1. **Schema & Semantic Drift:** Agents hallucinate parameters, miss required fields, and introduce type errors that crash downstream services.
2. **Partial Failures & Orphan State:** If an agent successfully reserves an inventory item in Service A, but fails on billing in Service B, standard agent frameworks throw an in-memory exception and crash—leaving databases in an inconsistent, non-recoverable state.
3. **Absence of Zero-Trust Network Mediation:** Existing agent guardrails run client-side in the agent's Python process. In enterprise architectures, downstream microservices cannot trust client-side validation; validation and policy checks must occur at the **network boundary**.

---

## 💡 The Solution: How AgentSaga-Gateway Works

AgentSaga-Gateway sits as a non-blocking reverse proxy between autonomous AI agents and enterprise microservice backends:

```
[ AI Agent Runtime ]
        │
        │ HTTP / Tool Call
        ▼
[ AgentSaga-Gateway ]
   ├── 1. Dynamic OpenAPI Contract Validator (< 5ms)
   ├── 2. Policy & Invariant Gate (Risk Level Evaluation)
   ├── 3. Ephemeral Sandbox Router (Conditional Shadow Dry-Run)
   └── 4. Distributed SAGA State Machine (PostgreSQL Journal)
        │
        ├── (All Valid) ───────────────► [ Downstream Enterprise Microservices ]
        │
        └── (Downstream 500 / Abort) ──► [ SAGA Compensator (Inverse Rollback APIs) ]
```

### Key Architectural Capabilities
* **Dynamic OpenAPI 3.1 Contract Shield:** Validates inbound agent payloads in real time against strict JSON Schema specifications at the network edge before traffic touches downstream databases.
* **Declarative SAGA Extensions (`x-agentsaga-*`):** Annotates standard OpenAPI contracts with compensation paths, risk classifications, and action types (`compensatable`, `pivot`, `retriable`).
* **Automated Backward Recovery:** If an agent workflow aborts or encounters a downstream failure at step $N$, the gateway automatically fires inverse compensating API calls in reverse topological order.
* **Pivot Transaction Isolation:** Enforces strict execution ordering so irreversible side-effects (e.g., physical email dispatch or wire transfers) only execute after all prior compensatable operations have succeeded.

---

## 📁 Repository Structure

```
├── ARCHITECTURE.md          # Complete Architectural & Data Flow Specification
├── ROADMAP.md               # Engineering Release Milestones & Benchmarking Plan
├── contracts/               # Reference OpenAPI 3.1 Specs with x-agentsaga Extensions
│   ├── hr-service.yaml
│   ├── payroll-service.yaml
│   └── notification-service.yaml
├── gateway/                 # Core Gateway Engine (Reverse Proxy & SAGA Journal)
├── services/                # Reference Enterprise Microservices & Docker Compose Testbed
├── eval-harness/            # Python-based Benchmark & Fault Injection Suite
└── paper/                   # Academic Research Manuscript (IEEE double-column format)
```

---

## 🚀 Quickstart & Documentation

* Read the detailed technical blueprint: [ARCHITECTURE.md](ARCHITECTURE.md)
* View the upcoming development milestones: [ROADMAP.md](ROADMAP.md)
* Inspect reference contract definitions: [contracts/](contracts/)

---

## 📄 License

Licensed under the Apache License, Version 2.0.
