# api/monitoring.py

from fastapi import APIRouter
import asyncio
import socket
import subprocess
import json
import threading
from pathlib import Path

from core.config import (
    NODE_POOL,
    MONITOR_PORT,
    METRICS_SAMPLES_CAP,
    METRICS_LOG_DIR,
    MONITOR_PYTHON,
    MONITOR_SSH_HOSTS,
    EXECUTOR,
)

router = APIRouter(prefix="/api/monitoring")

# ---- Monitoring State ----
_metrics_store = {}
_metrics_lock = threading.Lock()
_monitoring_agents_started = False


# -------------------------------------------------
# TCP Metrics Listener
# -------------------------------------------------
def _metrics_listener():
    """Background thread: accepts TCP metrics from agents."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", MONITOR_PORT))
        s.listen()

        while True:
            try:
                conn, _ = s.accept()
                with conn:
                    data = conn.recv(4096)
                    if not data:
                        continue

                    try:
                        metrics = json.loads(data.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue

                    node_name = metrics.get("node_name")
                    if not node_name:
                        continue

                    with _metrics_lock:
                        if node_name not in _metrics_store:
                            from collections import deque
                            _metrics_store[node_name] = deque(maxlen=METRICS_SAMPLES_CAP)
                        _metrics_store[node_name].append(metrics)

                    # Append to disk
                    try:
                        METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
                        log_file = METRICS_LOG_DIR / f"{node_name}.jsonl"
                        with open(log_file, "a") as f:
                            f.write(json.dumps(metrics) + "\n")
                    except Exception:
                        pass

            except OSError:
                break
            except Exception:
                pass


# -------------------------------------------------
# Agent Control Helpers
# -------------------------------------------------
def _ssh_start_monitor_agent(node: str):
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)
    py = MONITOR_PYTHON.strip()

    remote_cmd = (
        f"tmux kill-session -t monitor 2>/dev/null || true; "
        f"tmux new-session -d -s monitor "
        f"'{py} /home/cluster/ELEC490-Capstone/monitor/monitor_agent.py'"
    )

    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", ssh_host, remote_cmd],
        capture_output=True,
        text=True,
        timeout=15,
    )

    return proc.returncode == 0, proc.stderr.strip()


def _ssh_stop_monitor_agent(node: str):
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)

    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", ssh_host,
         "tmux kill-session -t monitor 2>/dev/null || true"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    return proc.returncode == 0


def _check_agent_running(node: str):
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)

    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=3", ssh_host,
         "pgrep -f 'monitor/monitor_agent.py'"],
        capture_output=True,
        text=True,
        timeout=8,
    )

    return proc.returncode == 0 and bool(proc.stdout.strip())


def _diagnose_node(node: str):
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)

    remote_cmd = (
        f'bash -lc \'cd "/home/cluster/ELEC490-Capstone" '
        f'&& timeout 2 python3 monitor/monitor_agent.py 2>&1\' || true'
    )

    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", ssh_host, remote_cmd],
        capture_output=True,
        text=True,
        timeout=15,
    )

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()

    return (out + "\n" + err).strip() or "(no output)"


# -------------------------------------------------
# API Endpoints
# -------------------------------------------------
@router.post("/start")
async def monitoring_start():
    global _monitoring_agents_started

    loop = asyncio.get_running_loop()
    results = {}
    errors = {}

    for node in NODE_POOL:
        ok, err = await loop.run_in_executor(
            EXECUTOR, _ssh_start_monitor_agent, node
        )
        results[node] = ok
        if err:
            errors[node] = err

    _monitoring_agents_started = any(results.values())

    return {
        "ok": True,
        "agents": results,
        "agent_errors": errors,
        "monitoring_active": _monitoring_agents_started,
    }


@router.post("/stop")
async def monitoring_stop():
    global _monitoring_agents_started

    loop = asyncio.get_running_loop()
    results = {}

    for node in NODE_POOL:
        ok = await loop.run_in_executor(
            EXECUTOR, _ssh_stop_monitor_agent, node
        )
        results[node] = ok

    _monitoring_agents_started = False

    return {
        "ok": True,
        "agents": results,
        "monitoring_active": False,
    }


@router.get("/agent-status")
async def monitoring_agent_status():
    loop = asyncio.get_running_loop()
    status = {}

    for node in NODE_POOL:
        status[node] = await loop.run_in_executor(
            EXECUTOR, _check_agent_running, node
        )

    return {"nodes": status}


@router.get("/diagnose")
async def monitoring_diagnose(node: str):
    if node not in NODE_POOL:
        return {"ok": False, "error": "unknown node"}

    loop = asyncio.get_running_loop()
    output = await loop.run_in_executor(
        EXECUTOR, _diagnose_node, node
    )

    return {"ok": True, "node": node, "output": output}


@router.get("/status")
async def monitoring_status():
    return {"monitoring_active": _monitoring_agents_started}


# -------------------------------------------------
# Metrics Endpoint (separate route)
# -------------------------------------------------
@router.get("/metrics")
async def get_metrics():
    with _metrics_lock:
        by_node = {}
        for node, deq in _metrics_store.items():
            samples = list(deq)
            by_node[node] = {
                "latest": samples[-1] if samples else None,
                "samples": samples,
            }

    return {
        "by_node": by_node,
        "monitoring_active": _monitoring_agents_started,
        "log_path": str(METRICS_LOG_DIR),
    }


# -------------------------------------------------
# Background Thread Starter (call from main.py)
# -------------------------------------------------
def start_metrics_listener():
    t = threading.Thread(target=_metrics_listener, daemon=True)
    t.start()