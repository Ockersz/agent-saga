# Architecture Specification: AgentSaga-Gateway (ASG)

> **Decoupled Zero-Trust Runtime Verification and Transactional SAGA Mediation for Autonomous AI Agents in Distributed Microservices**


---

## 1. Problem Statement & Motivation

As autonomous AI agents (powered by frontier reasoning models such as Gemini, Claude, and GPT-o-series) transition from conversational interfaces to **autonomous multi-agent planners**, they increasingly invoke distributed enterprise microservices via REST, gRPC, and tool-use protocols (e.g., Anthropic MCP).

However, modern enterprise microservices operate under strict requirements that non-deterministic LLMs routinely violate:
1. **Schema & Semantic Drift:** LLMs produce subtle payload hallucinations (e.g., mistyped enum values, stringified numbers, missing non-null fields, unauthorized extra attributes) that bypass client-side checks and cause downstream service crashes.
2. **Distributed State Corruption (The Partial-Failure Problem):** Autonomous multi-step agent plans span multiple independent microservices (e.g., Step 1: *Inventory Reservation*, Step 2: *Courier Booking*, Step 3: *Billing Settlement*). When Step 3 fails or times out, standard agent frameworks (LangGraph, CrewAI, AutoGen) terminate with an in-memory exception, leaving Step 1 and Step 2 committed in external databases with **zero automated compensation or rollback**.
3. **Absence of Zero-Trust Network Mediation:** Existing agent guardrails (e.g., Guardrails AI, Pydantic) execute client-side inside the agent's host memory. In an enterprise Zero-Trust security model, downstream microservices cannot trust client-side validation; validation and policy enforcement must occur at an **independent network boundary (API Gateway / Sidecar Proxy)**.

---

## 2. High-Level System Architecture

```
                       ┌────────────────────────────────────────────────────────┐
                       │                   AI AGENT RUNTIME                     │
                       │  (LangGraph / AutoGen / Python / NestJS Agent Pod)     │
                       └───────────────────────────┬────────────────────────────┘
                                                   │
                                                   │ 1. Tool Call (HTTP / JSON-RPC)
                                                   ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   AGENTSAGA GATEWAY (REVERSE PROXY)                                    │
│                                                                                                        │
│   ┌───────────────────────────┐      ┌───────────────────────────┐     ┌───────────────────────────┐   │
│   │   1. Contract Validator   │ ───> │  2. Dynamic Policy Engine │ ──> │   3. SAGA State Machine   │   │
│   │    (OpenAPI 3.1 Schema)   │      │    (Risk & Invariant Check│     │   (Distributed Journal)   │   │
│   └─────────────┬─────────────┘      └───────────────────────────┘     └─────────────┬─────────────┘   │
│                 │ (Fails Validation)                                                 │                 │
│                 ▼                                                                    │                 │
│         [Immediate 400 Block]                                                        │ Forward Call    │
└──────────────────────────────────────────────────────────────────────────────────────┼─────────────────┘
                                                                                       │
                         ┌─────────────────────────────────────────────────────────────┴─────────────────┐
                         │                                                                               │
                         ▼                                                                               ▼
     ┌───────────────────────────────────────┐                               ┌───────────────────────────────────────┐
     │          SHADOW SANDBOX               │                               │        PRODUCTION MICROSERVICES       │
     │ (Ephemeral Mock DB / Dry-Run Engine)  │                               │                                       │
     │ (Triggered if x-risk-level: critical) │                               │  • HR & Biometric Service             │
     └───────────────────────────────────────┘                               │  • Payroll & Finance Service          │
                                                                             │  • Notifications / Logistics          │
                                                                             └───────────────────┬───────────────────┘
                                                                                                 │
                                                                         [Downstream Error / 500]│
                                                                                                 ▼
                                                                             ┌───────────────────────────────────────┐
                                                                             │         COMPENSATING SAGA RUNNER      │
                                                                             │  Executes x-compensating-action APIs  │
                                                                             │  in reverse topological order.        │
                                                                             └───────────────────────────────────────┘
```

---

## 3. OpenAPI 3.1 Specification Extensions (`x-agentsaga-*`)

We extend standard OpenAPI 3.1 / JSON Schema contracts with vendor-neutral extensions that inform the Gateway how to govern agent calls:

```yaml
openapi: 3.1.0
info:
  title: Enterprise Payroll & Settlement Service
  version: 1.0.0
paths:
  /api/v1/payroll/settlement:
    post:
      summary: Execute employee settlement deduction
      operationId: executeSettlement
      # Custom AgentSaga Extensions:
      x-agentsaga-action-type: compensatable  # Options: compensatable | pivot | retriable
      x-agentsaga-risk-level: medium          # Options: low | medium | high | critical
      x-agentsaga-compensating-action:
        path: /api/v1/payroll/settlement/{transactionId}/reversal
        method: POST
        mapping:
          transactionId: "$.response.body.transactionId"
          reason: "Agent workflow aborted by SAGA orchestrator"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [employeeId, amount, currency, settlementReason]
              properties:
                employeeId:
                  type: string
                  pattern: "^EMP-[0-9]{5}$"
                amount:
                  type: number
                  minimum: 0.01
                  maximum: 500000.00
                currency:
                  type: string
                  enum: [LKR, USD, EUR, GBP]
                settlementReason:
                  type: string
                  minLength: 5
```

### Action Types Defined:
1. **`compensatable`**: Action can be mechanically undone via an inverse API call (e.g., Hold $\rightarrow$ Release, Reserve $\rightarrow$ Cancel).
2. **`pivot`**: The point of no return (e.g., physical email dispatch, un-reversible wire transfer). Must execute *last*. If a pivot succeeds, subsequent steps must be strictly *retriable*.
3. **`retriable`**: Guaranteed to succeed eventually via idempotent retries with exponential backoff.

---

## 4. SAGA Journal & Database Schema (PostgreSQL)

The Gateway maintains a persistent, append-only distributed transaction journal in PostgreSQL:

```sql
CREATE TYPE saga_status AS ENUM (
    'STARTED', 
    'IN_PROGRESS', 
    'COMPENSATING', 
    'COMPENSATED', 
    'COMMITTED', 
    'FAILED'
);

CREATE TYPE step_status AS ENUM (
    'PENDING', 
    'VALIDATED', 
    'EXECUTED', 
    'COMPENSATION_PENDING', 
    'COMPENSATED', 
    'FAILED'
);

-- Master Workflow Table
CREATE TABLE saga_workflows (
    workflow_id VARCHAR(128) PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    status saga_status NOT NULL DEFAULT 'STARTED',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Per-Step Transaction Journal
CREATE TABLE saga_step_journal (
    step_id VARCHAR(128) PRIMARY KEY,
    workflow_id VARCHAR(128) NOT NULL REFERENCES saga_workflows(workflow_id) ON DELETE CASCADE,
    step_index INT NOT NULL,
    target_service VARCHAR(64) NOT NULL,
    target_path VARCHAR(255) NOT NULL,
    http_method VARCHAR(10) NOT NULL,
    action_type VARCHAR(20) NOT NULL, -- compensatable, pivot, retriable
    risk_level VARCHAR(20) NOT NULL,
    
    -- Request / Response payloads for deterministic rollback
    forward_request_body JSONB NOT NULL,
    forward_response_body JSONB,
    forward_status_code INT,
    
    -- Compensation Metadata extracted from OpenAPI spec
    compensating_path VARCHAR(255),
    compensating_method VARCHAR(10),
    compensating_payload JSONB,
    
    status step_status NOT NULL DEFAULT 'PENDING',
    execution_latency_ms INT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_saga_workflow ON saga_step_journal(workflow_id, step_index);
```

---

## 5. Empirical Benchmark & Verification Plan

To validate system reliability and performance under production enterprise conditions, the evaluation framework assesses three core Research Questions ($RQ$s):

* **$RQ_1$ (Contract Safety & Defect Prevention):**  
  What percentage of hallucinated schemas, type mismatches, and policy violations are intercepted before downstream database state corruption?  
  *Baseline comparison:* Raw Agent tool-calling (LangGraph / OpenAI Agents SDK) vs. Gateway.
* **$RQ_2$ (Distributed State Consistency & Recovery):**  
  Under simulated mid-workflow failures (network drops, 500 server errors, timeouts on step $N$ of $M$), what is the automated recovery rate of the SAGA compensation engine compared to baseline agent frameworks?
* **$RQ_3$ (Runtime Latency & Throughput Overhead):**  
  What is the amortized latency overhead (in milliseconds) introduced by JSON Schema validation, OPA policy evaluation, and SAGA journal logging under load (100–1,000 req/sec)?

---

## 6. Directory Layout

```
agent-saga/
├── ARCHITECTURE.md                  # Master System Specification (This Document)
├── LICENSE                          # Apache License 2.0
├── README.md                        # Project Overview, Walkthrough & Reproducibility
├── ROADMAP.md                       # Engineering Release Milestones & Benchmarking Plan
├── .env.example                     # Gateway, Policy & Docker Port Configuration
├── docker-compose.yml               # Orchestrates PostgreSQL 16, Services, and Gateway
├── .github/workflows/ci.yml         # CI Build, Jest Tests & PostgreSQL 16 Benchmark
│
├── contracts/                       # OpenAPI 3.1 Specs with x-agentsaga-* extensions
│   ├── hr-service.yaml
│   ├── payroll-service.yaml
│   └── notification-service.yaml
│
├── gateway/                         # Core Gateway (TypeScript / NestJS)
│   ├── src/
│   │   ├── contracts/               # OpenAPI 3.1 contract loader & Ajv compiler
│   │   ├── validator/               # Path & JSON body contract validation engine
│   │   ├── policy/                  # Dynamic risk, allowlist & threshold evaluator
│   │   ├── saga/                    # SAGA journal recorder & inverse compensator
│   │   ├── proxy/                   # Reverse proxy controller & pivot invariant gate
│   │   └── database/                # PostgreSQL 16 journal DDL (schema.sql)
│   ├── test/                        # Jest integration & orchestration test suites
│   ├── package.json
│   ├── tsconfig.json
│   └── Dockerfile
│
├── services/                        # Mock Enterprise Microservices (Express / TypeScript)
│   ├── src/
│   │   └── server.ts                # HR, Payroll, Notification & state inspection APIs
│   ├── package.json
│   ├── tsconfig.json
│   └── Dockerfile
│
└── eval-harness/                    # Live HTTP & Concurrency Benchmark Suite
    ├── README.md                    # Benchmark Replication Guide
    ├── benchmark_runner.py          # 1,000-workflow E2E safety, recovery & latency suite
    ├── concurrency_benchmark.py     # 800-workflow multi-agent stress & isolation suite
    ├── live_llm_agent_eval.py       # Optional live Gemini REST agent evaluator
    ├── data/                        # Empirical JSON summaries & CSV run logs
    └── requirements.txt
```
