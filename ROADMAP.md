# Project Roadmap: AgentSaga-Gateway (ASG)

This document outlines the development lifecycle, feature releases, and experimental benchmarking roadmap for the **AgentSaga-Gateway** open-source project.

---

## Release Milestones

### Phase 1: Core Engine & Contract Verification (v0.1.0-alpha)
- [x] **Architecture Specification:** Complete zero-trust proxy design and OpenAPI extension definitions (`ARCHITECTURE.md`).
- [x] **Contract Schema Specifications:** Multi-service reference specifications with `x-agentsaga-*` vendor extensions.
- [x] **Dynamic Contract Validator:** Strict in-memory OpenAPI 3.1 JSON Schema validator (path parameters + request bodies, `coerceTypes: false`) intercepting agent tool requests at the network edge (`Ajv` + `ajv-formats`).
- [x] **Reverse Proxy Core:** High-throughput keep-alive HTTP proxy engine routing validated traffic to target microservice pods.
- [x] **Compensating Rollback Coordinator:** Automated backward-recovery engine resolving dynamic JSONPath bindings (`$.response.body.*`, `$.request.path.*`) and dispatching inverse APIs in reverse topological order upon failure.
- [x] **Policy & Invariant Gate:** Dynamic risk classifier enforcing agent allowlists, autonomous monetary ceilings, and critical-risk approval tokens (`PolicyEvaluatorService`).

### Phase 2: Distributed SAGA Orchestration & Sandboxing (v0.2.0-beta)
- [x] **Durable Transaction Journal:** Relational PostgreSQL / in-memory schema tracking multi-step agent workflow execution (`saga_workflows`, `saga_step_journal`).
- [x] **Reference Enterprise Testbed:** Containerized Docker Compose environment featuring mock enterprise microservices (HR, Payroll, Notifications) with state inspection APIs.
- [x] **Pivot & Retriable State Machine:** Explicit Pivot Invariant enforcement blocking terminal irreversible actions (`pivot`) and post-abort replays (`409 Conflict`).
- [ ] **Ephemeral Shadow Sandbox:** Dry-run execution router for high-risk mutative operations prior to production mesh commitment.

### Phase 3: Live Benchmarking, Empirical Evaluation & Research (v0.3.0)
- [x] **Automated Live HTTP Test Harness:** End-to-end Python evaluation framework executing 1,000 real HTTP workflows against live Gateway & Services processes (`eval-harness/benchmark_runner.py`).
- [x] **Live Fault Injection Suite:**
  - Schema & type hallucinations (missing fields, enum drift, path regex violations, string-for-number type drift).
  - Policy threshold violations (unauthorized monetary limits).
  - Downstream HTTP 504 gateway timeout aborts + post-abort pivot replay verification.
- [x] **Live Empirical Metrics Collection (N = 1,000 + 800 Concurrent):**
  - Schema defect interception: 100.0% blocked at edge (`400 Bad Request`, 0 DB mutations).
  - Policy threshold enforcement: 100.0% blocked at gate (`403 Forbidden`, 0 DB mutations).
  - Distributed state recovery: 100.0% restored via SAGA rollback + 100.0% post-abort pivot blocking.
  - Runtime latency overhead: Schema + Policy Gate `P50 = 0.03ms` / `P95 = 0.04ms`; End-to-End Proxy Hop Delta `P50 = 1.80ms` / `P95 = 2.42ms`.
  - Concurrency scaling (`C = 1..50`, `eval-harness/concurrency_benchmark.py`): Peak `708.1 req/s` (`C = 10`), `100.0%` cross-workflow state isolation up to `C = 50`.
- [x] **PostgreSQL 16 CI Integration & Live LLM Evaluator:** CI runs live PostgreSQL 16 durable SAGA journal benchmarks on every push (`.github/workflows/ci.yml`), plus optional live Gemini evaluator (`eval-harness/live_llm_agent_eval.py`).
- [x] **Publication Figures Generated:** `paper/figures/fig1_safety_and_recovery.png`, `paper/figures/fig2_latency_cdf.png`, `paper/figures/fig3_concurrency_scaling.png`, `paper/figures/fig_system_architecture.png`, `paper/figures/fig_process_lifecycle.png`.
- [x] **Camera-Ready Research Manuscript Draft:** Complete IEEE conference manuscript with live empirical benchmarks, concurrency scaling, declarative contract listing, formal SAGA mediation algorithm, and `\DOUBLEBLIND` toggle (`paper/main.tex`).

### Phase 4: Production Hardening & Ecosystem Integrations (v1.0.0-GA)
- [ ] **MCP (Model Context Protocol) Bridge:** Native adapter exposing verified microservice tool registries to Anthropic MCP clients.
- [ ] **LangGraph / CrewAI Middleware:** Client SDK plugins facilitating seamless integration with popular agent frameworks.
- [ ] **Helm Chart & Kubernetes Operator:** Production-ready deployment manifests for cloud-native Kubernetes clusters.
- [ ] **Open-Source Documentation & Developer Portal:** Complete guides, interactive tutorials, and benchmark replication scripts.
