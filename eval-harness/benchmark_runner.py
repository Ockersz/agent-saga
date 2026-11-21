#!/usr/bin/env python3
"""
AgentSaga-Gateway Live End-to-End HTTP Empirical Benchmark Harness
Executes real HTTP workflows against live enterprise-services (:3000) and
agentsaga-gateway (:8080), verifies downstream database state via GET /api/v1/state,
records empirical latency & recovery metrics, and generates IEEE publication figures.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from typing import Dict, Any, Tuple
from urllib.parse import urlparse

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests


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


def get_node_version() -> str:
    try:
        out = subprocess.check_output(["node", "-v"], text=True).strip()
        return out
    except Exception:
        return "unknown"


def reset_services_state(session: requests.Session, services_url: str):
    r = session.post(f"{services_url}/api/v1/reset", timeout=5)
    r.raise_for_status()


def fetch_services_state(session: requests.Session, services_url: str) -> Dict[str, Any]:
    r = session.get(f"{services_url}/api/v1/state", timeout=5)
    r.raise_for_status()
    return r.json()


def run_warmup(session: requests.Session, services_url: str, gateway_url: str, count: int = 10):
    for i in range(count):
        wf_id = str(uuid.uuid4())
        reset_services_state(session, services_url)
        session.post(
            f"{services_url}/api/v1/hr/employees/EMP-00104/status",
            json={"status": "INACTIVE", "reason": "Warmup run"},
            timeout=5,
        )
        reset_services_state(session, services_url)
        r_hr = session.post(
            f"{gateway_url}/proxy/api/v1/hr/employees/EMP-00104/status",
            headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "1", "x-agent-id": "warmup-bot"},
            json={"status": "INACTIVE", "reason": "Warmup run"},
            timeout=5,
        )
        assert r_hr.status_code == 200, f"Warmup HR failed: {r_hr.text}"
        r_pay = session.post(
            f"{gateway_url}/proxy/api/v1/payroll/settlement",
            headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "2", "x-agent-id": "warmup-bot"},
            json={
                "employeeId": "EMP-00104",
                "amount": 1000,
                "currency": "LKR",
                "settlementType": "EQUIPMENT_DEDUCTION",
                "reason": "Warmup deduction",
            },
            timeout=5,
        )
        assert r_pay.status_code == 201, f"Warmup Payroll failed: {r_pay.text}"
        sid = r_pay.json()["settlementId"]
        r_notif = session.post(
            f"{gateway_url}/proxy/api/v1/notifications/clearance-email",
            headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "3", "x-agent-id": "warmup-bot"},
            json={
                "employeeId": "EMP-00104",
                "recipientEmail": "emp104@example.com",
                "settlementId": sid,
                "subject": "Warmup Clearance",
            },
            timeout=5,
        )
        assert r_notif.status_code == 202, f"Warmup Notification failed: {r_notif.text}"


def run_live_benchmark(
    runs: int,
    services_url: str,
    gateway_url: str,
    spawn_local: bool,
    database_url: str = "",
    summary_out: str = "",
    csv_out: str = "",
    skip_figures: bool = False,
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
            raise RuntimeError(
                "Services/Gateway not reachable and --no-spawn was specified."
            )

        print(f"==================================================================")
        print(f"AgentSaga-Gateway Live End-to-End HTTP Benchmark ({runs} workflows)")
        print(f"==================================================================")
        print("Running 10-iteration JIT warmup...")
        run_warmup(session, services_url, gateway_url, count=10)

        num_nominal = int(round(runs * 0.30))
        num_schema = int(round(runs * 0.30))
        num_policy = int(round(runs * 0.10))
        num_partial = runs - (num_nominal + num_schema + num_policy)

        records = []

        # ---------------------------------------------------------------------
        # SUITE 1: Nominal 3-Step Workflows (Latency & Functional Correctness)
        # ---------------------------------------------------------------------
        print(f"[Suite 1/4] Executing {num_nominal} nominal 3-step workflows (Baseline vs. AgentSaga)...")
        for i in range(num_nominal):
            # Baseline Direct
            reset_services_state(session, services_url)
            t0 = time.perf_counter()
            r1 = session.post(
                f"{services_url}/api/v1/hr/employees/EMP-00104/status",
                json={"status": "INACTIVE", "reason": "Resignation processed"},
                timeout=5,
            )
            r2 = session.post(
                f"{services_url}/api/v1/payroll/settlement",
                json={
                    "employeeId": "EMP-00104",
                    "amount": 25000.0,
                    "currency": "LKR",
                    "settlementType": "EQUIPMENT_DEDUCTION",
                    "reason": "Laptop dock return deduction",
                },
                timeout=5,
            )
            sid_base = r2.json()["settlementId"]
            r3 = session.post(
                f"{services_url}/api/v1/notifications/clearance-email",
                json={
                    "employeeId": "EMP-00104",
                    "recipientEmail": "emp104@example.com",
                    "settlementId": sid_base,
                    "subject": "Clearance Notice",
                },
                timeout=5,
            )
            baseline_wf_ms = (time.perf_counter() - t0) * 1000.0
            assert r1.status_code == 200 and r2.status_code == 201 and r3.status_code == 202

            # AgentSaga Proxied
            reset_services_state(session, services_url)
            wf_id = str(uuid.uuid4())
            t0_gw = time.perf_counter()
            g1 = session.post(
                f"{gateway_url}/proxy/api/v1/hr/employees/EMP-00104/status",
                headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "1", "x-agent-id": "hr-bot"},
                json={"status": "INACTIVE", "reason": "Resignation processed"},
                timeout=5,
            )
            g2 = session.post(
                f"{gateway_url}/proxy/api/v1/payroll/settlement",
                headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "2", "x-agent-id": "hr-bot"},
                json={
                    "employeeId": "EMP-00104",
                    "amount": 25000.0,
                    "currency": "LKR",
                    "settlementType": "EQUIPMENT_DEDUCTION",
                    "reason": "Laptop dock return deduction",
                },
                timeout=5,
            )
            sid_gw = g2.json()["settlementId"]
            g3 = session.post(
                f"{gateway_url}/proxy/api/v1/notifications/clearance-email",
                headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "3", "x-agent-id": "hr-bot"},
                json={
                    "employeeId": "EMP-00104",
                    "recipientEmail": "emp104@example.com",
                    "settlementId": sid_gw,
                    "subject": "Clearance Notice",
                },
                timeout=5,
            )
            gateway_wf_ms = (time.perf_counter() - t0_gw) * 1000.0
            assert g1.status_code == 200 and g2.status_code == 201 and g3.status_code == 202

            state = fetch_services_state(session, services_url)
            assert state["employees"]["EMP-00104"]["status"] == "INACTIVE"
            assert state["settlements"][sid_gw]["status"] == "POSTED"
            assert len(state["deliveries"]) == 1

            # Per-step gateway instrumented latencies (averaged over the 3 steps in workflow)
            step_resps = [g1, g2, g3]
            val_ms = float(np.mean([float(r.headers.get("x-agentsaga-validation-ms", "0")) for r in step_resps]))
            pol_ms = float(np.mean([float(r.headers.get("x-agentsaga-policy-ms", "0")) for r in step_resps]))
            gw_overhead_step_ms = float(
                np.mean([float(r.headers.get("x-agentsaga-overhead-ms", "0")) for r in step_resps])
            )
            wall_step_delta_ms = max(0.0, (gateway_wf_ms - baseline_wf_ms) / 3.0)

            records.append(
                {
                    "run_id": f"NOMINAL_{i:04d}",
                    "suite": "nominal",
                    "baseline_intercepted": False,
                    "gateway_intercepted": False,
                    "baseline_policy_blocked": False,
                    "gateway_policy_blocked": False,
                    "baseline_recovered": True,
                    "gateway_recovered": True,
                    "pivot_blocked_after_abort": True,
                    "baseline_wf_ms": baseline_wf_ms,
                    "gateway_wf_ms": gateway_wf_ms,
                    "validation_ms": val_ms,
                    "policy_ms": pol_ms,
                    "validation_plus_policy_ms": val_ms + pol_ms,
                    "gateway_overhead_step_ms": gw_overhead_step_ms,
                    "wall_step_delta_ms": wall_step_delta_ms,
                }
            )

        # ---------------------------------------------------------------------
        # SUITE 2: Schema & Type Hallucinations (RQ1)
        # ---------------------------------------------------------------------
        print(f"[Suite 2/4] Executing {num_schema} schema & type hallucination attacks (RQ1)...")
        malformed_cases = [
            (
                "/api/v1/payroll/settlement",
                {
                    "employeeId": "EMP-00104",
                    "currency": "LKR",
                    "settlementType": "EQUIPMENT_DEDUCTION",
                    "reason": "Missing required amount field",
                },
                "missing_amount",
            ),
            (
                "/api/v1/payroll/settlement",
                {
                    "employeeId": "EMP-00104",
                    "amount": 15000,
                    "currency": "BITCOIN",
                    "settlementType": "EQUIPMENT_DEDUCTION",
                    "reason": "Disallowed enum currency",
                },
                "invalid_enum",
            ),
            (
                "/api/v1/hr/employees/BAD_ID/status",
                {"status": "INACTIVE", "reason": "Invalid employeeId path regex"},
                "path_regex",
            ),
            (
                "/api/v1/payroll/settlement",
                {
                    "employeeId": "EMP-00104",
                    "amount": "5000",
                    "currency": "LKR",
                    "settlementType": "EQUIPMENT_DEDUCTION",
                    "reason": "String-for-number type drift",
                },
                "type_drift",
            ),
        ]

        for i in range(num_schema):
            path_suffix, payload, fault_tag = malformed_cases[i % len(malformed_cases)]

            # Baseline Direct (:3000)
            reset_services_state(session, services_url)
            t0 = time.perf_counter()
            rb = session.post(f"{services_url}{path_suffix}", json=payload, timeout=5)
            baseline_ms = (time.perf_counter() - t0) * 1000.0
            state_b = fetch_services_state(session, services_url)
            baseline_corrupted = (len(state_b["settlements"]) > 0) or ("BAD_ID" in state_b["employees"])
            baseline_intercepted = (rb.status_code == 400) and (not baseline_corrupted)

            # AgentSaga Proxied (:8080)
            reset_services_state(session, services_url)
            wf_id = str(uuid.uuid4())
            t0_gw = time.perf_counter()
            rg = session.post(
                f"{gateway_url}/proxy{path_suffix}",
                headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "1", "x-agent-id": "hr-bot"},
                json=payload,
                timeout=5,
            )
            gateway_ms = (time.perf_counter() - t0_gw) * 1000.0
            state_g = fetch_services_state(session, services_url)
            gateway_clean = (len(state_g["settlements"]) == 0) and ("BAD_ID" not in state_g["employees"])
            assert rg.status_code == 400 and gateway_clean, f"Schema fault {fault_tag} leaked!"

            val_ms = float(rg.headers.get("x-agentsaga-validation-ms", "0"))
            overhead_ms = float(rg.headers.get("x-agentsaga-overhead-ms", str(gateway_ms)))

            records.append(
                {
                    "run_id": f"SCHEMA_{i:04d}",
                    "suite": "schema_hallucination",
                    "baseline_intercepted": baseline_intercepted,
                    "gateway_intercepted": True,
                    "baseline_policy_blocked": False,
                    "gateway_policy_blocked": False,
                    "baseline_recovered": False,
                    "gateway_recovered": True,
                    "pivot_blocked_after_abort": True,
                    "baseline_wf_ms": baseline_ms,
                    "gateway_wf_ms": gateway_ms,
                    "validation_ms": val_ms,
                    "policy_ms": 0.0,
                    "validation_plus_policy_ms": val_ms,
                    "gateway_overhead_step_ms": overhead_ms,
                    "wall_step_delta_ms": max(0.0, gateway_ms - baseline_ms),
                }
            )

        # ---------------------------------------------------------------------
        # SUITE 3: Policy & Monetary Invariant Violations (RQ1b)
        # ---------------------------------------------------------------------
        print(f"[Suite 3/4] Executing {num_policy} policy threshold violation runs (RQ1b)...")
        policy_payload = {
            "employeeId": "EMP-00104",
            "amount": 750000.0,
            "currency": "LKR",
            "settlementType": "SEVERANCE_PAY",
            "reason": "Exceeds 500k autonomous limit without approval token",
        }
        for i in range(num_policy):
            # Baseline Direct
            reset_services_state(session, services_url)
            t0 = time.perf_counter()
            rb = session.post(f"{services_url}/api/v1/payroll/settlement", json=policy_payload, timeout=5)
            baseline_ms = (time.perf_counter() - t0) * 1000.0
            state_b = fetch_services_state(session, services_url)
            baseline_policy_blocked = (rb.status_code == 403) or (len(state_b["settlements"]) == 0)

            # AgentSaga Proxied
            reset_services_state(session, services_url)
            wf_id = str(uuid.uuid4())
            t0_gw = time.perf_counter()
            rg = session.post(
                f"{gateway_url}/proxy/api/v1/payroll/settlement",
                headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "1", "x-agent-id": "hr-bot"},
                json=policy_payload,
                timeout=5,
            )
            gateway_ms = (time.perf_counter() - t0_gw) * 1000.0
            state_g = fetch_services_state(session, services_url)
            assert rg.status_code == 403 and rg.json().get("ruleViolated") == "MONETARY_THRESHOLD"
            assert len(state_g["settlements"]) == 0

            val_ms = float(rg.headers.get("x-agentsaga-validation-ms", "0"))
            pol_ms = float(rg.headers.get("x-agentsaga-policy-ms", "0"))
            overhead_ms = float(rg.headers.get("x-agentsaga-overhead-ms", str(gateway_ms)))

            records.append(
                {
                    "run_id": f"POLICY_{i:04d}",
                    "suite": "policy_violation",
                    "baseline_intercepted": False,
                    "gateway_intercepted": True,
                    "baseline_policy_blocked": baseline_policy_blocked,
                    "gateway_policy_blocked": True,
                    "baseline_recovered": False,
                    "gateway_recovered": True,
                    "pivot_blocked_after_abort": True,
                    "baseline_wf_ms": baseline_ms,
                    "gateway_wf_ms": gateway_ms,
                    "validation_ms": val_ms,
                    "policy_ms": pol_ms,
                    "validation_plus_policy_ms": val_ms + pol_ms,
                    "gateway_overhead_step_ms": overhead_ms,
                    "wall_step_delta_ms": max(0.0, gateway_ms - baseline_ms),
                }
            )

        # ---------------------------------------------------------------------
        # SUITE 4: Mid-Workflow Partial Failures, SAGA Rollback & Pivot Guard (RQ2)
        # ---------------------------------------------------------------------
        print(f"[Suite 4/4] Executing {num_partial} mid-workflow aborts + SAGA rollback & pivot guard (RQ2)...")
        for i in range(num_partial):
            # Baseline Direct (:3000) -> Step 3 returns 504 -> leaves orphan INACTIVE + POSTED
            reset_services_state(session, services_url)
            t0 = time.perf_counter()
            session.post(
                f"{services_url}/api/v1/hr/employees/EMP-00104/status",
                json={"status": "INACTIVE", "reason": "Resignation initiated"},
                timeout=5,
            )
            rb2 = session.post(
                f"{services_url}/api/v1/payroll/settlement",
                json={
                    "employeeId": "EMP-00104",
                    "amount": 18000.0,
                    "currency": "LKR",
                    "settlementType": "EQUIPMENT_DEDUCTION",
                    "reason": "Equipment deduction",
                },
                timeout=5,
            )
            sid_base = rb2.json()["settlementId"]
            rb3 = session.post(
                f"{services_url}/api/v1/notifications/clearance-email",
                headers={"x-simulate-failure": "notification"},
                json={
                    "employeeId": "EMP-00104",
                    "recipientEmail": "emp104@example.com",
                    "settlementId": sid_base,
                    "subject": "Clearance Letter",
                },
                timeout=5,
            )
            baseline_ms = (time.perf_counter() - t0) * 1000.0
            assert rb3.status_code == 504
            state_b = fetch_services_state(session, services_url)
            baseline_recovered = (
                state_b["employees"]["EMP-00104"]["status"] == "ACTIVE"
                and state_b["settlements"][sid_base]["status"] == "REVERSED"
            )

            # AgentSaga Proxied (:8080) -> Step 3 fails 504 -> Gateway executes real SAGA rollbacks
            reset_services_state(session, services_url)
            wf_id = str(uuid.uuid4())
            t0_gw = time.perf_counter()
            g1 = session.post(
                f"{gateway_url}/proxy/api/v1/hr/employees/EMP-00104/status",
                headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "1", "x-agent-id": "hr-bot"},
                json={"status": "INACTIVE", "reason": "Resignation initiated"},
                timeout=5,
            )
            g2 = session.post(
                f"{gateway_url}/proxy/api/v1/payroll/settlement",
                headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "2", "x-agent-id": "hr-bot"},
                json={
                    "employeeId": "EMP-00104",
                    "amount": 18000.0,
                    "currency": "LKR",
                    "settlementType": "EQUIPMENT_DEDUCTION",
                    "reason": "Equipment deduction",
                },
                timeout=5,
            )
            sid_gw = g2.json()["settlementId"]
            g3 = session.post(
                f"{gateway_url}/proxy/api/v1/notifications/clearance-email",
                headers={
                    "x-saga-workflow-id": wf_id,
                    "x-saga-step-index": "3",
                    "x-agent-id": "hr-bot",
                    "x-simulate-failure": "notification",
                },
                json={
                    "employeeId": "EMP-00104",
                    "recipientEmail": "emp104@example.com",
                    "settlementId": sid_gw,
                    "subject": "Clearance Letter",
                },
                timeout=5,
            )
            gateway_ms = (time.perf_counter() - t0_gw) * 1000.0
            assert g1.status_code == 200 and g2.status_code == 201 and g3.status_code == 502
            rollback = g3.json()["sagaRollback"]
            assert rollback["totalCompensated"] == 2 and rollback["failedCompensations"] == 0

            # Verify Pivot Replay / Post-Abort Containment (409 Conflict)
            g_replay = session.post(
                f"{gateway_url}/proxy/api/v1/notifications/clearance-email",
                headers={"x-saga-workflow-id": wf_id, "x-saga-step-index": "3", "x-agent-id": "hr-bot"},
                json={
                    "employeeId": "EMP-00104",
                    "recipientEmail": "emp104@example.com",
                    "settlementId": sid_gw,
                    "subject": "Clearance Letter",
                },
                timeout=5,
            )
            assert g_replay.status_code == 409

            state_g = fetch_services_state(session, services_url)
            gateway_recovered = (
                state_g["employees"]["EMP-00104"]["status"] == "ACTIVE"
                and state_g["settlements"][sid_gw]["status"] == "REVERSED"
                and len(state_g["deliveries"]) == 0
            )
            assert gateway_recovered, f"SAGA rollback state verification failed on run {i}: {state_g}"

            records.append(
                {
                    "run_id": f"ABORT_{i:04d}",
                    "suite": "partial_failure",
                    "baseline_intercepted": False,
                    "gateway_intercepted": False,
                    "baseline_policy_blocked": False,
                    "gateway_policy_blocked": False,
                    "baseline_recovered": baseline_recovered,
                    "gateway_recovered": gateway_recovered,
                    "pivot_blocked_after_abort": g_replay.status_code == 409,
                    "baseline_wf_ms": baseline_ms,
                    "gateway_wf_ms": gateway_ms,
                    "validation_ms": float(g3.headers.get("x-agentsaga-validation-ms", "0")),
                    "policy_ms": float(g3.headers.get("x-agentsaga-policy-ms", "0")),
                    "validation_plus_policy_ms": float(g3.headers.get("x-agentsaga-validation-ms", "0"))
                    + float(g3.headers.get("x-agentsaga-policy-ms", "0")),
                    "gateway_overhead_step_ms": max(0.0, gateway_ms - baseline_ms),
                    "wall_step_delta_ms": max(0.0, gateway_ms - baseline_ms),
                }
            )

        # ---------------------------------------------------------------------
        # Aggregate Metrics & Save Artifacts
        # ---------------------------------------------------------------------
        df = pd.DataFrame(records)
        csv_path = (
            os.path.abspath(csv_out)
            if csv_out
            else os.path.join(repo_root, "eval-harness", "data", "benchmark_metrics.csv")
        )
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        df.to_csv(csv_path, index=False)

        schema_df = df[df["suite"] == "schema_hallucination"]
        policy_df = df[df["suite"] == "policy_violation"]
        abort_df = df[df["suite"] == "partial_failure"]
        nom_df = df[df["suite"] == "nominal"]

        summary = {
            "runs": int(len(df)),
            "journal_mode": "postgresql" if database_url else "in-memory",
            "breakdown": {
                "nominal": int(len(nom_df)),
                "schema_hallucination": int(len(schema_df)),
                "policy_violation": int(len(policy_df)),
                "partial_failure": int(len(abort_df)),
            },
            "environment": {
                "os": f"{platform.system()} {platform.release()}",
                "python": platform.python_version(),
                "node": get_node_version(),
            },
            "rq1_schema_interception_pct": {
                "baseline": round(float(schema_df["baseline_intercepted"].mean() * 100.0), 2),
                "agentsaga": round(float(schema_df["gateway_intercepted"].mean() * 100.0), 2),
            },
            "rq1b_policy_enforcement_pct": {
                "baseline": round(float(policy_df["baseline_policy_blocked"].mean() * 100.0), 2),
                "agentsaga": round(float(policy_df["gateway_policy_blocked"].mean() * 100.0), 2),
            },
            "rq2_state_recovery_pct": {
                "baseline": round(float(abort_df["baseline_recovered"].mean() * 100.0), 2),
                "agentsaga": round(float(abort_df["gateway_recovered"].mean() * 100.0), 2),
                "pivot_blocked_after_abort_pct": round(float(abort_df["pivot_blocked_after_abort"].mean() * 100.0), 2),
            },
            "rq3_latency_ms": {
                "validation_plus_policy_p50": round(float(np.percentile(nom_df["validation_plus_policy_ms"], 50)), 3),
                "validation_plus_policy_p95": round(float(np.percentile(nom_df["validation_plus_policy_ms"], 95)), 3),
                "validation_plus_policy_p99": round(float(np.percentile(nom_df["validation_plus_policy_ms"], 99)), 3),
                "gateway_overhead_p50": round(float(np.percentile(nom_df["gateway_overhead_step_ms"], 50)), 3),
                "gateway_overhead_p95": round(float(np.percentile(nom_df["gateway_overhead_step_ms"], 95)), 3),
                "gateway_overhead_p99": round(float(np.percentile(nom_df["gateway_overhead_step_ms"], 99)), 3),
                "wall_delta_step_p50": round(float(np.percentile(nom_df["wall_step_delta_ms"], 50)), 3),
                "wall_delta_step_p95": round(float(np.percentile(nom_df["wall_step_delta_ms"], 95)), 3),
                "wall_delta_step_p99": round(float(np.percentile(nom_df["wall_step_delta_ms"], 99)), 3),
                "baseline_3step_wf_p50": round(float(np.percentile(nom_df["baseline_wf_ms"], 50)), 3),
                "agentsaga_3step_wf_p50": round(float(np.percentile(nom_df["gateway_wf_ms"], 50)), 3),
                "saga_rollback_wf_p50": round(float(np.percentile(abort_df["gateway_wf_ms"], 50)), 3),
                "saga_rollback_wf_p95": round(float(np.percentile(abort_df["gateway_wf_ms"], 95)), 3),
            },
        }

        summary_path = (
            os.path.abspath(summary_out)
            if summary_out
            else os.path.join(repo_root, "eval-harness", "data", "benchmark_summary.json")
        )
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        print("\n------------------------------------------------------------------")
        print(f"LIVE EMPIRICAL EVALUATION SUMMARY (N = {len(df)} HTTP Workflows)")
        print("------------------------------------------------------------------")
        print(f"Environment: {summary['environment']['os']} | Node {summary['environment']['node']} | Python {summary['environment']['python']}")
        print(f"Journal Mode: {summary['journal_mode']}")
        print(f"RQ1  Schema Defect Interception:   Baseline {summary['rq1_schema_interception_pct']['baseline']:.1f}% | AgentSaga {summary['rq1_schema_interception_pct']['agentsaga']:.1f}%")
        print(f"RQ1b Policy Threshold Enforcement: Baseline {summary['rq1b_policy_enforcement_pct']['baseline']:.1f}% | AgentSaga {summary['rq1b_policy_enforcement_pct']['agentsaga']:.1f}%")
        print(f"RQ2  SAGA State Consistency Recov: Baseline {summary['rq2_state_recovery_pct']['baseline']:.1f}% | AgentSaga {summary['rq2_state_recovery_pct']['agentsaga']:.1f}% (Pivot Guard {summary['rq2_state_recovery_pct']['pivot_blocked_after_abort_pct']:.1f}%)")
        lat = summary["rq3_latency_ms"]
        print(f"RQ3  Validation + Policy Gate:     P50 {lat['validation_plus_policy_p50']:.2f}ms | P95 {lat['validation_plus_policy_p95']:.2f}ms | P99 {lat['validation_plus_policy_p99']:.2f}ms")
        print(f"RQ3  In-Gateway Mediation/Step:    P50 {lat['gateway_overhead_p50']:.2f}ms | P95 {lat['gateway_overhead_p95']:.2f}ms | P99 {lat['gateway_overhead_p99']:.2f}ms")
        print(f"RQ3  Proxy Hop Delta (incl. TCP):  P50 {lat['wall_delta_step_p50']:.2f}ms | P95 {lat['wall_delta_step_p95']:.2f}ms | P99 {lat['wall_delta_step_p99']:.2f}ms")
        print(f"RQ3  3-Step Workflow Wall-Clock:   Baseline P50 {lat['baseline_3step_wf_p50']:.2f}ms vs AgentSaga P50 {lat['agentsaga_3step_wf_p50']:.2f}ms")
        print(f"RQ3  Live SAGA Rollback Workflow:  P50 {lat['saga_rollback_wf_p50']:.2f}ms | P95 {lat['saga_rollback_wf_p95']:.2f}ms")
        print("------------------------------------------------------------------\n")

        if not skip_figures:
            generate_figures(repo_root, summary, nom_df)
        print("Live benchmark completed successfully!")

    finally:
        for proc in (gw_proc, svc_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


def generate_figures(repo_root: str, summary: Dict[str, Any], nom_df: pd.DataFrame):
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")

    # -------------------------------------------------------------------------
    # Figure 1: Safety, Policy, and State Recovery Comparison
    # -------------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(6.8, 4.2), dpi=300)
    categories = [
        "Schema Interception\n(RQ1)",
        "Policy Enforcement\n(RQ1b)",
        "SAGA State Recovery\n(RQ2)",
    ]
    baseline_scores = [
        summary["rq1_schema_interception_pct"]["baseline"],
        summary["rq1b_policy_enforcement_pct"]["baseline"],
        summary["rq2_state_recovery_pct"]["baseline"],
    ]
    gateway_scores = [
        summary["rq1_schema_interception_pct"]["agentsaga"],
        summary["rq1b_policy_enforcement_pct"]["agentsaga"],
        summary["rq2_state_recovery_pct"]["agentsaga"],
    ]

    x = np.arange(len(categories))
    width = 0.34

    rects1 = ax.bar(x - width / 2, baseline_scores, width, label="Baseline Agent (Direct HTTP)", color="#95a5a6", edgecolor="#7f8c8d")
    rects2 = ax.bar(x + width / 2, gateway_scores, width, label="AgentSaga Gateway", color="#2471A3", edgecolor="#1B4F72")

    ax.set_ylabel("Success Rate (%)", fontsize=10.5, fontweight="bold")
    ax.set_title("Live Contract Safety, Policy Gate & SAGA Recovery", fontsize=11.5, fontweight="bold", pad=10)
    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=9.5, fontweight="bold")
    ax.set_ylim(0, 124)
    ax.legend(loc="upper left", frameon=True, facecolor="white", framealpha=0.95, fontsize=9)

    for rect in rects1:
        h = rect.get_height()
        ax.annotate(
            f"{h:.1f}%",
            xy=(rect.get_x() + rect.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8.8,
            color="#444444",
        )
    for rect in rects2:
        h = rect.get_height()
        ax.annotate(
            f"{h:.1f}%",
            xy=(rect.get_x() + rect.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9.0,
            fontweight="bold",
            color="#1B4F72",
        )

    plt.tight_layout()
    fig1_path = os.path.join(repo_root, "paper", "figures", "fig1_safety_and_recovery.png")
    fig.savefig(fig1_path, dpi=300)
    plt.close(fig)
    print(f"  ✓ Saved {fig1_path}")

    # -------------------------------------------------------------------------
    # Figure 2: Real Measured Latency CDF
    # -------------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(6.8, 4.2), dpi=300)
    sorted_val = np.sort(nom_df["validation_plus_policy_ms"].values)
    sorted_gw = np.sort(nom_df["gateway_overhead_step_ms"].values)
    sorted_wall = np.sort(nom_df["wall_step_delta_ms"].values)
    yvals = np.arange(1, len(sorted_val) + 1) / float(len(sorted_val))

    val_p50 = summary["rq3_latency_ms"]["validation_plus_policy_p50"]
    val_p95 = summary["rq3_latency_ms"]["validation_plus_policy_p95"]
    gw_p50 = summary["rq3_latency_ms"]["gateway_overhead_p50"]
    gw_p95 = summary["rq3_latency_ms"]["gateway_overhead_p95"]
    wall_p50 = summary["rq3_latency_ms"]["wall_delta_step_p50"]
    wall_p95 = summary["rq3_latency_ms"]["wall_delta_step_p95"]
    wall_p99 = summary["rq3_latency_ms"]["wall_delta_step_p99"]

    ax.plot(sorted_val, yvals, label=f"Schema + Policy Gate (P50={val_p50:.2f}ms, P95={val_p95:.2f}ms)", color="#1E8449", lw=2.1)
    ax.plot(sorted_gw, yvals, label=f"In-Gateway Mediation (P50={gw_p50:.2f}ms, P95={gw_p95:.2f}ms)", color="#2471A3", lw=2.1, ls="-.")
    ax.plot(sorted_wall, yvals, label=f"End-to-End Proxy Hop (P50={wall_p50:.2f}ms, P95={wall_p95:.2f}ms)", color="#D35400", lw=2.2, ls="--")

    ax.axvline(x=gw_p95, color="#2471A3", ls=":", lw=1.3, alpha=0.8)
    ax.axvline(x=wall_p95, color="#D35400", ls=":", lw=1.3, alpha=0.8)

    xlim_max = max(1.0, float(np.ceil(max(wall_p95 * 1.35, wall_p99 * 1.15) * 2) / 2))
    ax.set_xlim(0, xlim_max)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("Measured Latency Overhead per Step (ms)", fontsize=10.5, fontweight="bold")
    ax.set_ylabel("Cumulative Probability (CDF)", fontsize=10.5, fontweight="bold")
    ax.set_title("Live Measured Gateway Latency Overhead CDF (RQ3)", fontsize=11.5, fontweight="bold", pad=10)
    ax.legend(loc="lower right", frameon=True, facecolor="white", framealpha=0.95, fontsize=8.2)

    plt.tight_layout()
    fig2_path = os.path.join(repo_root, "paper", "figures", "fig2_latency_cdf.png")
    fig.savefig(fig2_path, dpi=300)
    plt.close(fig)
    print(f"  ✓ Saved {fig2_path}")


def main():
    parser = argparse.ArgumentParser(description="AgentSaga Live End-to-End HTTP Benchmark")
    parser.add_argument("--runs", type=int, default=1000, help="Total number of workflows to execute")
    parser.add_argument("--services-url", default="http://127.0.0.1:3000", help="Base URL for enterprise services")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080", help="Base URL for AgentSaga gateway")
    parser.add_argument("--spawn-local", action="store_true", default=True, help="Spawn local Node processes if ports are down")
    parser.add_argument("--no-spawn", dest="spawn_local", action="store_false", help="Fail instead of spawning local processes")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""), help="Optional PostgreSQL connection string for durable SAGA journal mode")
    parser.add_argument("--summary-out", default="", help="Optional custom output path for summary JSON")
    parser.add_argument("--csv-out", default="", help="Optional custom output path for metrics CSV")
    parser.add_argument("--skip-figures", action="store_true", help="Skip overwriting paper/figures PNGs")
    args = parser.parse_args()

    run_live_benchmark(
        args.runs,
        args.services_url,
        args.gateway_url,
        args.spawn_local,
        database_url=args.database_url,
        summary_out=args.summary_out,
        csv_out=args.csv_out,
        skip_figures=args.skip_figures,
    )


if __name__ == "__main__":
    main()
