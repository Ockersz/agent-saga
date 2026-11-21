#!/usr/bin/env python3
"""
AgentSaga-Gateway Concurrent Multi-Agent Load & Workflow Isolation Benchmark
Spawns N concurrent autonomous agent workers across C in [1, 10, 25, 50]
executing mixed nominal and fault-injected 3-step workflows simultaneously,
verifies 100% cross-workflow state isolation via GET /api/v1/state, and
generates paper/figures/fig3_concurrency_scaling.png.
"""

import argparse
import concurrent.futures
import json
import os
import platform
import subprocess
import threading
import time
import uuid
from typing import Dict, Any, List, Tuple
from urllib.parse import urlparse

import matplotlib.pyplot as plt
import numpy as np
import requests
from requests.adapters import HTTPAdapter


_thread_local = threading.local()


def get_thread_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        sess = requests.Session()
        adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64)
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        _thread_local.session = sess
    return _thread_local.session


def check_url_ready(session: requests.Session, url: str, timeout_s: float = 0.5) -> bool:
    try:
        r = session.get(url, timeout=timeout_s)
        if r.status_code != 200:
            return False
        data = r.json()
        if url.endswith("/api/v1/state"):
            return isinstance(data, dict) and "employees" in data
        if url.endswith("/health"):
            return isinstance(data, dict) and data.get("service") == "agentsaga-gateway"
        return True
    except (requests.RequestException, ValueError):
        return False


def wait_until_ready(session: requests.Session, url: str, name: str, timeout_s: float = 20.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if check_url_ready(session, url):
            return
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {name} at {url}")


def start_local_processes(
    repo_root: str,
    services_url: str = "http://127.0.0.1:3000",
    gateway_url: str = "http://127.0.0.1:8080",
    database_url: str = "",
) -> Tuple[subprocess.Popen, subprocess.Popen]:
    services_entry = os.path.join(repo_root, "services", "dist", "server.js")
    gateway_entry = os.path.join(repo_root, "gateway", "dist", "main.js")
    contracts_dir = os.path.join(repo_root, "contracts")

    if not os.path.exists(services_entry) or not os.path.exists(gateway_entry):
        raise FileNotFoundError(
            "Compiled artifacts missing. Run `npm run build` in both `services/` and `gateway/` first."
        )

    services_port = urlparse(services_url).port or 3000
    gateway_port = urlparse(gateway_url).port or 8080

    env_services = os.environ.copy()
    env_services["PORT"] = str(services_port)
    env_services["BENCHMARK_QUIET"] = "1"

    env_gateway = os.environ.copy()
    env_gateway["PORT"] = str(gateway_port)
    env_gateway["UPSTREAM_SERVICES_URL"] = services_url.rstrip("/")
    env_gateway["CONTRACTS_DIR"] = contracts_dir
    env_gateway["POLICY_MAX_AMOUNT"] = "500000"
    env_gateway["BENCHMARK_QUIET"] = "1"
    if database_url:
        env_gateway["DATABASE_URL"] = database_url
    else:
        env_gateway.pop("DATABASE_URL", None)

    svc_proc = subprocess.Popen(
        ["node", services_entry],
        cwd=os.path.join(repo_root, "services"),
        env=env_services,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    gw_proc = subprocess.Popen(
        ["node", gateway_entry],
        cwd=os.path.join(repo_root, "gateway"),
        env=env_gateway,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return svc_proc, gw_proc


def execute_single_workflow(
    idx: int, gateway_url: str, should_abort: bool
) -> Dict[str, Any]:
    session = get_thread_session()
    wf_id = str(uuid.uuid4())
    emp_id = f"EMP-{10000 + idx:05d}"
    step_latencies_ms: List[float] = []

    # Step 1: HR Status Update
    t0 = time.perf_counter()
    r1 = session.post(
        f"{gateway_url}/proxy/api/v1/hr/employees/{emp_id}/status",
        headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "1", "x-agent-id": f"agent-{idx % 50}"},
        json={"status": "INACTIVE", "reason": "Concurrent offboarding flow"},
        timeout=10,
    )
    step_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
    assert r1.status_code == 200, f"Step 1 failed for {emp_id}: {r1.status_code} {r1.text}"

    # Step 2: Payroll Settlement
    t0 = time.perf_counter()
    r2 = session.post(
        f"{gateway_url}/proxy/api/v1/payroll/settlement",
        headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "2", "x-agent-id": f"agent-{idx % 50}"},
        json={
            "employeeId": emp_id,
            "amount": 12500.0,
            "currency": "LKR",
            "settlementType": "EQUIPMENT_DEDUCTION",
            "reason": "Concurrent deduction verification",
        },
        timeout=10,
    )
    step_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
    assert r2.status_code == 201, f"Step 2 failed for {emp_id}: {r2.status_code} {r2.text}"
    settlement_id = r2.json()["settlementId"]

    # Step 3: Pivot Notification (or injected 504 abort)
    h3 = {"x-saga-workflow-id": wf_id, "x-saga-step-index": "3", "x-agent-id": f"agent-{idx % 50}"}
    if should_abort:
        h3["x-simulate-failure"] = "notification"

    t0 = time.perf_counter()
    r3 = session.post(
        f"{gateway_url}/proxy/api/v1/notifications/clearance-email",
        headers=h3,
        json={
            "employeeId": emp_id,
            "recipientEmail": f"{emp_id.lower()}@example.com",
            "settlementId": settlement_id,
            "subject": "Clearance Notice",
        },
        timeout=10,
    )
    step_latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    if should_abort:
        assert r3.status_code == 502, f"Expected 502 rollback for {emp_id}, got {r3.status_code}"
        rollback = r3.json()["sagaRollback"]
        assert rollback["totalCompensated"] == 2 and rollback["failedCompensations"] == 0
    else:
        assert r3.status_code == 202, f"Expected 202 pivot commit for {emp_id}, got {r3.status_code}"

    return {
        "idx": idx,
        "emp_id": emp_id,
        "settlement_id": settlement_id,
        "should_abort": should_abort,
        "step_latencies_ms": step_latencies_ms,
    }


def run_concurrency_benchmark(
    workflows_per_tier: int,
    services_url: str,
    gateway_url: str,
    spawn_local: bool,
    database_url: str = "",
):
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.makedirs(os.path.join(repo_root, "paper", "figures"), exist_ok=True)
    os.makedirs(os.path.join(repo_root, "eval-harness", "data"), exist_ok=True)

    session = requests.Session()
    svc_proc = None
    gw_proc = None

    try:
        already_up = check_url_ready(session, f"{services_url}/api/v1/state") and check_url_ready(
            session, f"{gateway_url}/health"
        )
        if spawn_local and not already_up:
            print(f"Starting local Enterprise Services ({services_url}) & AgentSaga Gateway ({gateway_url})...")
            svc_proc, gw_proc = start_local_processes(
                repo_root,
                services_url=services_url,
                gateway_url=gateway_url,
                database_url=database_url,
            )
            wait_until_ready(session, f"{services_url}/api/v1/state", "Enterprise Services")
            wait_until_ready(session, f"{gateway_url}/health", "AgentSaga Gateway")
        elif not already_up:
            raise RuntimeError("Services/Gateway not reachable and --no-spawn was specified.")

        print("Running 10-iteration warmup...")
        for w in range(10):
            execute_single_workflow(9000 + w, gateway_url, should_abort=(w % 2 == 1))
        session.post(f"{services_url}/api/v1/reset", timeout=5).raise_for_status()

        concurrency_tiers = [1, 10, 25, 50]
        tier_results: List[Dict[str, Any]] = []

        print(f"==================================================================")
        print(f"AgentSaga Multi-Agent Concurrency Benchmark ({workflows_per_tier} flows/tier)")
        print(f"==================================================================")

        for workers in concurrency_tiers:
            session.post(f"{services_url}/api/v1/reset", timeout=5).raise_for_status()

            t_tier_start = time.perf_counter()
            outcomes: List[Dict[str, Any]] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(execute_single_workflow, idx, gateway_url, (idx % 2 == 1))
                    for idx in range(workflows_per_tier)
                ]
                for fut in concurrent.futures.as_completed(futures):
                    outcomes.append(fut.result())

            tier_duration_s = time.perf_counter() - t_tier_start

            # Verify 100% cross-workflow database state isolation
            state = session.get(f"{services_url}/api/v1/state", timeout=10).json()
            deliveries_by_emp: Dict[str, List[Dict[str, Any]]] = {}
            for d in state["deliveries"].values():
                deliveries_by_emp.setdefault(d["employeeId"], []).append(d)

            isolated_ok = 0
            for item in outcomes:
                emp = state["employees"].get(item["emp_id"])
                stl = state["settlements"].get(item["settlement_id"])
                dels = deliveries_by_emp.get(item["emp_id"], [])

                if item["should_abort"]:
                    ok = (
                        emp is not None
                        and emp["status"] == "ACTIVE"
                        and stl is not None
                        and stl["status"] == "REVERSED"
                        and len(dels) == 0
                    )
                else:
                    ok = (
                        emp is not None
                        and emp["status"] == "INACTIVE"
                        and stl is not None
                        and stl["status"] == "POSTED"
                        and len(dels) == 1
                        and dels[0]["status"] == "SENT"
                    )
                if ok:
                    isolated_ok += 1

            isolation_pct = round(100.0 * isolated_ok / len(outcomes), 2)
            assert isolated_ok == len(outcomes), f"Isolation mismatch at C={workers}: {isolated_ok}/{len(outcomes)}"

            all_step_lats = [lat for o in outcomes for lat in o["step_latencies_ms"]]
            total_client_reqs = workflows_per_tier * 3
            throughput_rps = total_client_reqs / tier_duration_s
            workflows_per_sec = workflows_per_tier / tier_duration_s

            row = {
                "concurrency": workers,
                "workflows": workflows_per_tier,
                "client_requests": total_client_reqs,
                "duration_s": round(tier_duration_s, 3),
                "throughput_rps": round(throughput_rps, 1),
                "workflows_per_sec": round(workflows_per_sec, 1),
                "step_latency_p50_ms": round(float(np.percentile(all_step_lats, 50)), 2),
                "step_latency_p95_ms": round(float(np.percentile(all_step_lats, 95)), 2),
                "step_latency_p99_ms": round(float(np.percentile(all_step_lats, 99)), 2),
                "isolation_verified_pct": isolation_pct,
            }
            tier_results.append(row)
            print(
                f"  C={workers:2d} workers | {row['throughput_rps']:7.1f} req/s ({row['workflows_per_sec']:5.1f} wf/s) "
                f"| Step P50={row['step_latency_p50_ms']:5.2f}ms P95={row['step_latency_p95_ms']:5.2f}ms "
                f"| Isolation={row['isolation_verified_pct']:.1f}%"
            )

        summary = {
            "workflows_per_tier": workflows_per_tier,
            "total_workflows": workflows_per_tier * len(concurrency_tiers),
            "journal_mode": "postgresql" if database_url else "in-memory",
            "environment": {
                "os": f"{platform.system()} {platform.release()}",
                "python": platform.python_version(),
            },
            "tiers": tier_results,
        }

        out_json = os.path.join(repo_root, "eval-harness", "data", "concurrency_summary.json")
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"\nSaved {out_json}")

        generate_concurrency_chart(repo_root, tier_results)
    finally:
        for proc in (gw_proc, svc_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


def generate_concurrency_chart(repo_root: str, tiers: List[Dict[str, Any]]):
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")

    concurrencies = [str(t["concurrency"]) for t in tiers]
    throughputs = [t["throughput_rps"] for t in tiers]
    p95_lats = [t["step_latency_p95_ms"] for t in tiers]

    x = np.arange(len(concurrencies))
    fig, ax1 = plt.subplots(figsize=(6.8, 4.3), dpi=300)

    bars = ax1.bar(x, throughputs, width=0.45, color="#2471A3", edgecolor="#1B4F72", label="Throughput (req/s)", zorder=3)
    ax1.set_xlabel("Concurrent Autonomous Agents (C)", fontsize=10.5, fontweight="bold")
    ax1.set_ylabel("Gateway Throughput (req/s)", fontsize=10.5, fontweight="bold", color="#1B4F72")
    ax1.tick_params(axis="y", labelcolor="#1B4F72")
    ax1.set_xticks(x)
    ax1.set_xticklabels([f"C = {c}" for c in concurrencies], fontsize=9.5, fontweight="bold")
    max_tp = max(throughputs)
    ax1.set_ylim(0, max_tp * 1.35)

    for rect in bars:
        h = rect.get_height()
        ax1.annotate(
            f"{h:.0f}",
            xy=(rect.get_x() + rect.get_width() / 2, h),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8.8,
            fontweight="bold",
            color="#1B4F72",
        )

    ax2 = ax1.twinx()
    ax2.grid(False)
    line = ax2.plot(x, p95_lats, color="#D35400", marker="o", lw=2.3, ms=7, label="Per-Step P95 Latency (ms)", zorder=5)
    ax2.set_ylabel("Per-Step P95 Latency (ms)", fontsize=10.5, fontweight="bold", color="#D35400")
    ax2.tick_params(axis="y", labelcolor="#D35400")
    max_lat = max(p95_lats)
    ax2.set_ylim(0, max_lat * 1.95)

    for xi, yi in zip(x, p95_lats):
        ax2.annotate(
            f"{yi:.1f}ms",
            xy=(xi, yi),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8.5,
            fontweight="bold",
            color="#A04000",
            bbox=dict(boxstyle="round,pad=0.14", fc="white", ec="none", alpha=0.85),
        )

    ax1.set_title("Multi-Agent Throughput & P95 Latency Scaling (100% Isolation)", fontsize=11.0, fontweight="bold", pad=12)

    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper left", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.8)

    plt.tight_layout()
    out_fig = os.path.join(repo_root, "paper", "figures", "fig3_concurrency_scaling.png")
    fig.savefig(out_fig, dpi=300)
    plt.close(fig)
    print(f"Saved {out_fig}")


def main():
    parser = argparse.ArgumentParser(description="AgentSaga Concurrent Multi-Agent Load & Isolation Benchmark")
    parser.add_argument("--workflows-per-tier", type=int, default=200, help="Workflows to run per concurrency tier")
    parser.add_argument("--services-url", default="http://127.0.0.1:3000", help="Base URL for enterprise services")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080", help="Base URL for AgentSaga gateway")
    parser.add_argument("--spawn-local", action="store_true", default=True, help="Spawn local Node processes if ports are down")
    parser.add_argument("--no-spawn", dest="spawn_local", action="store_false", help="Fail instead of spawning local processes")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""), help="Optional PostgreSQL connection string for durable SAGA journal mode")
    args = parser.parse_args()

    run_concurrency_benchmark(
        args.workflows_per_tier,
        args.services_url,
        args.gateway_url,
        args.spawn_local,
        database_url=args.database_url,
    )


if __name__ == "__main__":
    main()
