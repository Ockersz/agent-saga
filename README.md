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

## 🎬 Visual Scenarios: AgentSaga in Action

### Scenario 1: Intercepting Schema Drift at the Network Edge (Zero-Trust Shield)
When an autonomous agent generates a malformed payload (missing mandatory fields or hallucinating enum values), AgentSaga-Gateway intercepts and rejects it at the perimeter in **< 2ms**—preventing downstream database corruption.

```mermaid
sequenceDiagram
    autonumber
    actor Agent as Autonomous AI Agent
    participant GW as AgentSaga Gateway
    participant Payroll as Payroll Microservice (DB)

    Agent->>GW: POST /api/v1/payroll/settlement
    Note over GW: Intercepted at Network Edge
    Note over GW: Contract Violation Detected:<br/>Missing amount, Invalid currency
    GW-->>Agent: HTTP 400 Bad Request (Violations Array)
    Note over Payroll: Downstream Protected: 0 calls forwarded, 0 DB writes
```

---

### Scenario 2: Mid-Workflow Crash & Automated SAGA Rollback (The Undo Button)
In an enterprise multi-step workflow (e.g., offboarding: HR $\rightarrow$ Payroll $\rightarrow$ Notification), if the final step crashes with a `504 Gateway Timeout`, standard agent frameworks abort and leave orphan records. AgentSaga-Gateway **automatically triggers reverse-order SAGA compensation** to restore total database consistency.

```mermaid
sequenceDiagram
    autonumber
    actor Agent as Autonomous AI Agent
    participant GW as AgentSaga Gateway
    participant HR as HR Microservice
    participant Payroll as Payroll Microservice
    participant Email as Notification Microservice

    Note over Agent,Email: Phase 1 - Forward Execution
    Agent->>GW: Step 1 - POST /hr/employees/EMP-00104/status
    GW->>HR: Forward - Set status to INACTIVE
    HR-->>GW: 200 OK (Status Updated)
    Note over GW: Journaled Step 1 (Compensatable)
    GW-->>Agent: 200 OK

    Agent->>GW: Step 2 - POST /payroll/settlement
    GW->>Payroll: Forward - Record deduction
    Payroll-->>GW: 201 Created (settlementId: uuid-88)
    Note over GW: Journaled Step 2 (Compensatable)
    GW-->>Agent: 201 Created

    Agent->>GW: Step 3 - POST /notifications/clearance-email
    GW->>Email: Forward - Dispatch email
    Email-->>GW: 504 Gateway Timeout (SMTP Relay Dead)
    Note over GW: Failure Detected - Activating SAGA Rollback

    Note over Agent,Email: Phase 2 - Automated Backward Recovery (Reverse Order)
    GW->>Payroll: Inverse Step 2 - POST /settlement/uuid-88/reversal
    Payroll-->>GW: 200 OK (Settlement REVERSED)

    GW->>HR: Inverse Step 1 - POST /hr/employees/EMP-00104/status
    HR-->>GW: 200 OK (Status restored to ACTIVE)

    GW-->>Agent: 502 Bad Gateway - SAGA Compensation Complete
```

---

### Scenario 3: The Pivot Transaction Pattern (Irreversible Actions)
Certain actions cannot be programmatically rolled back (e.g., customer emails, physical warehouse dispatch, irreversible wire transfers). AgentSaga enforces execution barriers using the `pivot` action type:

| Action Classification | OpenAPI Extension | Behavior & Safety Invariant |
| :--- | :--- | :--- |
| **Compensatable** | `x-agentsaga-action-type: compensatable` | Executed during forward phase. Paired with explicit `x-agentsaga-compensating-action` inverse endpoint. |
| **Pivot** | `x-agentsaga-action-type: pivot` | **Irreversible terminal action.** Can **only** execute after all preceding compensatable operations have succeeded and committed. If an earlier step fails, the pivot never fires. |
| **Retriable** | `x-agentsaga-action-type: retriable` | Guaranteed idempotent endpoint executed with exponential backoff. |

---

## 📁 Repository Structure

```
├── .env.example                 # Gateway, Policy & Docker Port Configuration
├── .github/workflows/ci.yml     # CI Build, Jest Suite & Live PostgreSQL 16 Benchmark
├── ARCHITECTURE.md              # Complete Architectural & Data Flow Specification
├── LICENSE                      # Apache License 2.0
├── ROADMAP.md                   # Engineering Release Milestones & Benchmarking Plan
├── docker-compose.yml           # Orchestrates PostgreSQL 16, Services & Gateway
├── contracts/                   # Reference OpenAPI 3.1 Specs with x-agentsaga Extensions
│   ├── hr-service.yaml
│   ├── payroll-service.yaml
│   └── notification-service.yaml
├── gateway/                     # Core Gateway Engine (TypeScript / NestJS + Ajv + SAGA)
├── services/                    # Reference Enterprise Microservices (HR, Payroll, Notification)
└── eval-harness/                # Python Live HTTP, Concurrency & Gemini Evals
    └── README.md                # Full Reproducibility & CLI Flag Guide
```

---

## 🚀 Quickstart & Empirical Verification

### 1. Run with Docker Compose (PostgreSQL 16 + Mock Services + Gateway)
Host ports default to non-conflicting bindings (`POSTGRES_HOST_PORT=5433`, `SERVICES_HOST_PORT=3010`, `GATEWAY_HOST_PORT=8090`; see [.env.example](.env.example)):

```bash
docker compose up -d --build
curl http://127.0.0.1:8090/health
```

### 2. Interactive Walkthrough (`curl`)
After starting the Docker Compose stack above (`gateway` on `:8090`, `enterprise-services` on `:3010`), exercise all five runtime behaviors directly:

```bash
# Reset downstream microservice state
curl -s -X POST http://127.0.0.1:3010/api/v1/reset

# A) Nominal Step 1 (HR) & Step 2 (Payroll)
WF_ID="11111111-2222-3333-4444-555555555555"
curl -s -X POST http://127.0.0.1:8090/proxy/api/v1/hr/employees/EMP-00104/status \
  -H "Content-Type: application/json" -H "x-saga-workflow-id: $WF_ID" -H "x-saga-step-index: 1" -H "x-agent-id: hr-bot" \
  -d '{"status":"INACTIVE","reason":"Employee resignation approved"}'

# B) 400 Bad Request — OpenAPI schema & strict type enforcement at network edge (0 DB writes)
curl -i -X POST http://127.0.0.1:8090/proxy/api/v1/payroll/settlement \
  -H "Content-Type: application/json" -H "x-saga-workflow-id: wf-bad-schema" -H "x-agent-id: hr-bot" \
  -d '{"employeeId":"EMP-00104","amount":"5000","currency":"LKR","settlementType":"EQUIPMENT_DEDUCTION","reason":"Type drift"}'

# C) 403 Forbidden — Policy threshold gate (amount > 500000 without x-agentsaga-approval-token)
curl -i -X POST http://127.0.0.1:8090/proxy/api/v1/payroll/settlement \
  -H "Content-Type: application/json" -H "x-saga-workflow-id: wf-policy-block" -H "x-agent-id: hr-bot" \
  -d '{"employeeId":"EMP-00104","amount":750000,"currency":"LKR","settlementType":"SEVERANCE_PAY","reason":"Executive payout"}'

# D) 502 Bad Gateway — Simulate Step 3 timeout to trigger automatic reverse-order SAGA rollback
SETTLEMENT_ID=$(curl -s -X POST http://127.0.0.1:8090/proxy/api/v1/payroll/settlement \
  -H "Content-Type: application/json" -H "x-saga-workflow-id: $WF_ID" -H "x-saga-step-index: 2" -H "x-agent-id: hr-bot" \
  -d '{"employeeId":"EMP-00104","amount":25000,"currency":"LKR","settlementType":"EQUIPMENT_DEDUCTION","reason":"Laptop dock return"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['settlementId'])")

curl -i -X POST http://127.0.0.1:8090/proxy/api/v1/notifications/clearance-email \
  -H "Content-Type: application/json" -H "x-saga-workflow-id: $WF_ID" -H "x-saga-step-index: 3" -H "x-agent-id: hr-bot" \
  -H "x-simulate-failure: notification" \
  -d "{\"employeeId\":\"EMP-00104\",\"recipientEmail\":\"emp104@example.com\",\"settlementId\":\"$SETTLEMENT_ID\",\"subject\":\"Clearance\"}"

# Verify downstream DB rolled back (EMP-00104 restored to ACTIVE, settlement REVERSED)
curl -s http://127.0.0.1:3010/api/v1/state

# E) 409 Conflict — Subsequent pivot replay against the aborted workflow is blocked
curl -i -X POST http://127.0.0.1:8090/proxy/api/v1/notifications/clearance-email \
  -H "Content-Type: application/json" -H "x-saga-workflow-id: $WF_ID" -H "x-saga-step-index: 3" -H "x-agent-id: hr-bot" \
  -d "{\"employeeId\":\"EMP-00104\",\"recipientEmail\":\"emp104@example.com\",\"settlementId\":\"$SETTLEMENT_ID\",\"subject\":\"Clearance\"}"
```

### 3. Protocol Headers & Telemetry Reference

#### Inbound Control Headers
| Header | Direction | Purpose |
| :--- | :--- | :--- |
| `x-saga-workflow-id` | Request | Correlates multi-step tool invocations to a single SAGA workflow journal entry. |
| `x-saga-step-index` | Request | 1-based execution step index used to enforce pivot ordering invariants. |
| `x-agent-id` | Request | Caller identity audited by `PolicyEvaluatorService` (`untrusted-*` and `blocked-*` prefixes are rejected with `403`). |
| `x-agentsaga-approval-token` | Request | Authorization override permitting payouts above `POLICY_MAX_AMOUNT` (`500000`) or `critical` risk endpoints. |
| `x-simulate-failure` | Request (Testbed) | Injects downstream faults (`payroll` -> HTTP `500`, `notification` -> HTTP `504`) in the mock microservices. |

#### Outbound Telemetry Headers
| Header | Description |
| :--- | :--- |
| `x-agentsaga-validation-ms` | Compiled Ajv path & JSON body validation time in milliseconds. |
| `x-agentsaga-policy-ms` | Dynamic policy gate evaluation latency in milliseconds. |
| `x-agentsaga-overhead-ms` | Total in-gateway mediation latency (validation + policy + SAGA journal I/O). |
| `x-agentsaga-downstream-ms` | Upstream microservice HTTP round-trip latency in milliseconds. |
| `x-agentsaga-latency-ms` | End-to-end gateway turnaround latency in milliseconds. |

### 4. Local Build, Tests & Live Benchmarks
See [eval-harness/README.md](eval-harness/README.md) for all CLI flags and benchmark datasets.

```bash
# Build & test gateway (Ajv validation, Policy gate, SAGA orchestration)
cd gateway && npm ci && npm run build && npm test

# Build enterprise mock services
cd ../services && npm ci && npm run build && cd ..

# Run 1,000-workflow benchmark (in-memory WAL mode)
python eval-harness/benchmark_runner.py --runs 1000 --services-url http://127.0.0.1:3010 --gateway-url http://127.0.0.1:8090 --skip-figures

# Run 1,000-workflow benchmark (PostgreSQL 16 durable ACID journal mode)
python eval-harness/benchmark_runner.py --runs 1000 --services-url http://127.0.0.1:3010 --gateway-url http://127.0.0.1:8090 --database-url postgres://saga_admin:saga_secure_password@127.0.0.1:5433/agentsaga_journal --summary-out eval-harness/data/benchmark_postgres_summary.json --skip-figures

# Run 800-workflow multi-agent concurrency benchmark (C = 1, 10, 25, 50)
python eval-harness/concurrency_benchmark.py --workflows-per-tier 200 --services-url http://127.0.0.1:3010 --gateway-url http://127.0.0.1:8090
```

### 5. Live Empirical Results Summary
| Metric | Baseline (Direct) | AgentSaga (Mem WAL) | AgentSaga (PostgreSQL 16) |
| :--- | :---: | :---: | :---: |
| Schema Interception (`RQ1`, `N=300`) | 0.0% | **100.0%** | **100.0%** |
| Policy Enforcement (`RQ1b`, `N=100`) | 0.0% | **100.0%** | **100.0%** |
| SAGA State Recovery (`RQ2`, `N=300`) | 0.0% | **100.0%** | **100.0%** |
| Post-Abort Pivot Blocking (`409`) | 0.0% | **100.0%** | **100.0%** |
| Validation + Policy Gate (`P50/P95`) | N/A | 0.03 / 0.04 ms | 0.03 / 0.04 ms |
| Proxy Hop Delta (`P50/P95`) | N/A | 1.80 / 2.42 ms | 13.45 / 15.72 ms |
| Full SAGA Rollback (`P50/P95`) | N/A | 10.91 / 12.43 ms | 60.93 / 75.50 ms |

---

## 📚 Additional Documentation

* Read the detailed technical blueprint: [ARCHITECTURE.md](ARCHITECTURE.md)
* Reproduce all benchmarks & view datasets: [eval-harness/README.md](eval-harness/README.md)
* View the upcoming development milestones: [ROADMAP.md](ROADMAP.md)
* Inspect reference contract definitions: [contracts/](contracts/)

---

## 📄 License

Licensed under the Apache License, Version 2.0 ([LICENSE](LICENSE)).
