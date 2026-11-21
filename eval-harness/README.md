# Evaluation & Fault-Injection Harness (`eval-harness/`)

This directory contains the reproducible live HTTP benchmark suites and fault-injection drivers used to evaluate **AgentSaga-Gateway** against direct baseline microservice calls.

---

## Prerequisites

1. **Build the Node.js binaries (`services` and `gateway`):**
   ```bash
   cd gateway && npm ci && npm run build
   cd ../services && npm ci && npm run build
   cd ..
   ```
2. **Install Python dependencies (Python 3.10+):**
   ```bash
   pip install -r eval-harness/requirements.txt
   ```
3. *(Optional for PostgreSQL 16 mode)* Start `agentsaga-postgres` on port `5433`:
   ```bash
   docker compose up -d --wait postgres
   ```

---

## Benchmark Suites

### 1. Live End-to-End HTTP Benchmark (`benchmark_runner.py`)
Executes four live workflow suites over loopback TCP HTTP (after a 10-iteration V8 JIT warm-up) and verifies downstream database state directly via `GET /api/v1/state`:
- **Nominal (30%):** 3-step `HR -> Payroll -> Notification` flows comparing Baseline vs. AgentSaga step and workflow latency.
- **Schema Hallucinations (`RQ1`, 30%):** Missing required fields (`amount`), invalid enums (`BITCOIN`), invalid path regexes (`BAD_ID`), and string-for-number type drift (`"5000"`).
- **Policy Threshold Violations (`RQ1b`, 10%):** Schema-valid payouts exceeding `POLICY_MAX_AMOUNT` (`500000`) without an `x-agentsaga-approval-token`.
- **Mid-Workflow Aborts & Pivot Replay (`RQ2`, 30%):** Injected HTTP `504` timeout on Step 3 after Steps 1 and 2 commit, asserting full inverse SAGA compensation and subsequent `409 Conflict` pivot replay rejection.

**Run in In-Memory WAL Mode (`N = 1,000`):**
```bash
python eval-harness/benchmark_runner.py --runs 1000 --services-url http://127.0.0.1:3010 --gateway-url http://127.0.0.1:8090 --skip-figures
```

**Run in Durable PostgreSQL 16 ACID Mode (`N = 1,000`):**
```bash
python eval-harness/benchmark_runner.py \
  --runs 1000 \
  --services-url http://127.0.0.1:3010 \
  --gateway-url http://127.0.0.1:8090 \
  --database-url postgres://saga_admin:saga_secure_password@127.0.0.1:5433/agentsaga_journal \
  --summary-out eval-harness/data/benchmark_postgres_summary.json \
  --csv-out eval-harness/data/benchmark_postgres_metrics.csv \
  --skip-figures
```

| CLI Flag | Default | Purpose |
| :--- | :--- | :--- |
| `--runs` | `1000` | Total workflows distributed across Suites 1–4. |
| `--services-url` | `http://127.0.0.1:3000` | Target mock enterprise services base URL (port parsed when spawning locally). |
| `--gateway-url` | `http://127.0.0.1:8080` | Target AgentSaga gateway base URL (port parsed when spawning locally). |
| `--spawn-local` / `--no-spawn` | `--spawn-local` | Automatically spawn local built Node.js processes if targets are down. |
| `--database-url` | `$DATABASE_URL` | PostgreSQL 16 connection URI; omit for in-memory WAL mode. |
| `--summary-out` | `eval-harness/data/benchmark_summary.json` | Output path for aggregated percentile & recovery JSON summary. |
| `--csv-out` | `eval-harness/data/benchmark_metrics.csv` | Output path for per-run CSV telemetry. |
| `--skip-figures` | `false` | Skip PNG chart generation. |

---

### 2. Multi-Agent Concurrency & Isolation Stress Test (`concurrency_benchmark.py`)
Spawns concurrent worker threads across `C in [1, 10, 25, 50]` executing mixed nominal and `504`-aborted 3-step workflows across distinct entities (`EMP-10000`..`EMP-10199`) and verifies `100.0%` cross-workflow state isolation via `GET /api/v1/state`:

```bash
python eval-harness/concurrency_benchmark.py \
  --workflows-per-tier 200 \
  --services-url http://127.0.0.1:3010 \
  --gateway-url http://127.0.0.1:8090
```

Supports `--workflows-per-tier`, `--services-url`, `--gateway-url`, `--spawn-local` / `--no-spawn`, and `--database-url`. Writes summary telemetry to [data/concurrency_summary.json](data/concurrency_summary.json).

---

### 3. Optional Live Gemini Agent Evaluator (`live_llm_agent_eval.py`)
Prompts the Gemini REST API (`gemini-2.5-flash` by default) to generate multi-step HTTP tool calls for nominal, schema-tricky, and policy-violating prompts and routes them through `/proxy`:

```bash
export GEMINI_API_KEY="your-api-key"
python eval-harness/live_llm_agent_eval.py --model gemini-2.5-flash
```
Exits gracefully with instructions if `GEMINI_API_KEY` is unset.

---

## Saved Empirical Datasets (`eval-harness/data/`)
- [benchmark_summary.json](data/benchmark_summary.json) & [benchmark_metrics.csv](data/benchmark_metrics.csv): 1,000-workflow In-Memory WAL run.
- [benchmark_postgres_summary.json](data/benchmark_postgres_summary.json) & [benchmark_postgres_metrics.csv](data/benchmark_postgres_metrics.csv): 1,000-workflow PostgreSQL 16 ACID run (`310 COMMITTED`, `300 COMPENSATED`, `1,830` SQL step rows verified).
- [concurrency_summary.json](data/concurrency_summary.json): 800-workflow (`200` per tier `C in {1, 10, 25, 50}`) concurrency & isolation run.
