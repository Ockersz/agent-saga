# Project Roadmap: AgentSaga-Gateway (ASG)

This document outlines the development lifecycle, feature releases, and experimental benchmarking roadmap for the **AgentSaga-Gateway** open-source project.

---

## Release Milestones

### Phase 1: Core Engine & Contract Verification (v0.1.0-alpha)
- [x] **Architecture Specification:** Complete zero-trust proxy design and OpenAPI extension definitions (`ARCHITECTURE.md`).
- [x] **Contract Schema Specifications:** Multi-service reference specifications with `x-agentsaga-*` vendor extensions.
- [x] **Dynamic Contract Validator:** In-memory OpenAPI 3.1 JSON Schema validator intercepting agent tool requests at the network edge (`Ajv` + `ajv-formats`).
- [x] **Reverse Proxy Core:** High-throughput non-blocking HTTP proxy engine routing validated traffic to target microservice pods.
- [x] **Compensating Rollback Coordinator:** Automated backward-recovery engine dispatching inverse APIs in reverse topological order upon failure.
- [ ] **Policy & Invariant Gate:** Dynamic risk classifier evaluating payload safety constraints and blocking high-risk mutations.

### Phase 2: Distributed SAGA Orchestration & Sandboxing (v0.2.0-beta)
- [x] **Durable Transaction Journal:** Relational PostgreSQL / in-memory schema tracking multi-step agent workflow execution (`saga_workflows`, `saga_step_journal`).
- [x] **Reference Enterprise Testbed:** Containerized Docker Compose environment featuring mock enterprise microservices (HR, Payroll, Notifications).
- [ ] **Pivot & Retriable State Machine:** Explicit handling for non-compensatable terminal actions (`pivot`) and idempotent retries.
- [ ] **Ephemeral Shadow Sandbox:** Dry-run execution router for high-risk mutative operations prior to production mesh commitment.

### Phase 3: Benchmarking, Empirical Evaluation & Research (v0.3.0)
- [x] **Automated Test Harness:** Python-based evaluation framework running 1,000+ synthetic agent workflows with injected failure modes (`eval-harness/benchmark_runner.py`).
- [x] **Fault Injection Suite:**
  - Semantic and schema hallucinations (missing required fields, enum drift, unauthorized parameters).
  - Downstream HTTP 500 errors and timeout aborts.
- [x] **Empirical Metrics Collection:**
  - Defect interception rate: 100.0% of malformed agent calls blocked.
  - Distributed state recovery rate: 100.0% of interrupted workflows cleanly restored via SAGA.
  - Proxy latency overhead: P50 = 4.97ms, P95 = 8.03ms.
- [x] **Publication Figures Generated:** `paper/figures/fig1_safety_and_recovery.png`, `paper/figures/fig2_latency_cdf.png`.
- [ ] **Research Manuscript & Pre-print:** Formal technical paper documenting architecture, formal proofs, and empirical evaluation.

### Phase 4: Production Hardening & Ecosystem Integrations (v1.0.0-GA)
- [ ] **MCP (Model Context Protocol) Bridge:** Native adapter exposing verified microservice tool registries to Anthropic MCP clients.
- [ ] **LangGraph / CrewAI Middleware:** Client SDK plugins facilitating seamless integration with popular agent frameworks.
- [ ] **Helm Chart & Kubernetes Operator:** Production-ready deployment manifests for cloud-native Kubernetes clusters.
- [ ] **Open-Source Documentation & Developer Portal:** Complete guides, interactive tutorials, and benchmark replication scripts.
