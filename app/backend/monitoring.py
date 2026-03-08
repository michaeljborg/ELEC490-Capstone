import subprocess
import socket
import json
import threading
import asyncio
import os
import time
from collections import deque
from fastapi import APIRouter
from app.config import *

CURRENT_TEST_ID = 0
LOG_DIR = "monitor/logs"

router = APIRouter()

metrics_store: dict[str, deque] = {}
metrics_lock = threading.Lock()
monitoring_agents_started = False

# =============================
# METRICS LISTENER THREAD
# =============================

def start_metrics_listener():
    def _metrics_listener():

        os.makedirs(LOG_DIR, exist_ok=True)

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

                        metrics = json.loads(data.decode("utf-8"))

                        node_name = metrics.get("node_name")

                        if not node_name:
                            continue

                        # attach test ID to metrics
                        metrics["test_id"] = CURRENT_TEST_ID

                        # store in RAM for dashboard
                        with metrics_lock:
                            if node_name not in metrics_store:
                                metrics_store[node_name] = deque(maxlen=METRICS_SAMPLES_CAP)

                            metrics_store[node_name].append(metrics)

                        # write to disk log
                        try:
                            log_path = os.path.join(LOG_DIR, f"{node_name}.json")

                            with open(log_path, "a") as f:
                                f.write(json.dumps(metrics) + "\n")

                        except Exception as e:
                            print(f"[WARN] Failed writing metrics log for {node_name}: {e}")

                except Exception as e:
                    print(f"[WARN] Metrics listener error: {e}")

    t = threading.Thread(target=_metrics_listener, daemon=True)
    t.start()

# =============================
# SSH HELPERS
# =============================

def _ssh_start_monitor_agent(node: str):
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)
    remote_cmd = (
        f"tmux kill-session -t monitor 2>/dev/null || true; "
        f"tmux new-session -d -s monitor "
        f"'{MONITOR_PYTHON} {PATH_TO_SCRIPT}/monitor/monitor_agent.py'"
    )
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", ssh_host, remote_cmd],
        capture_output=True, text=True, timeout=15
    )
    return proc.returncode == 0

def _ssh_stop_monitor_agent(node: str):
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", ssh_host, "tmux kill-session -t monitor 2>/dev/null || true"],
        capture_output=True, text=True, timeout=10
    )
    return proc.returncode == 0

# =============================
# ROUTES
# =============================

@router.post("/api/monitoring/start")
async def monitoring_start():
    global monitoring_agents_started
    global CURRENT_TEST_ID

    loop = asyncio.get_running_loop()

    # Increment test ID for this run
    CURRENT_TEST_ID += 1

    # Ensure logs directory exists
    os.makedirs(LOG_DIR, exist_ok=True)

    # Create test marker entry
    marker = {
        "event": "START_TEST",
        "test_id": CURRENT_TEST_ID,
        "timestamp": time.time()
    }

    # Write marker into each node log file
    for node in NODE_POOL:
        log_path = os.path.join(LOG_DIR, f"{node}.json")
        try:
            with open(log_path, "a") as f:
                f.write("\n")
                f.write(json.dumps(marker) + "\n")
        except Exception as e:
            print(f"[WARN] Failed writing test marker for {node}: {e}")

    # Start monitoring agents on nodes
    tasks = [
        loop.run_in_executor(EXECUTOR, _ssh_start_monitor_agent, node)
        for node in NODE_POOL
    ]

    results = await asyncio.gather(*tasks)

    agents_status = dict(zip(NODE_POOL, results))
    monitoring_agents_started = any(results)

    return {
        "ok": True,
        "agents": agents_status,
        "monitoring_active": monitoring_agents_started,
        "test_id": CURRENT_TEST_ID
    }

@router.post("/api/monitoring/stop")
async def monitoring_stop():
    global monitoring_agents_started
    loop = asyncio.get_running_loop()
    
    tasks = [loop.run_in_executor(EXECUTOR, _ssh_stop_monitor_agent, node) for node in NODE_POOL]
    results = await asyncio.gather(*tasks)
    
    agents_status = dict(zip(NODE_POOL, results))
    monitoring_agents_started = False

    return {
        "ok": True,
        "agents": agents_status,
        "monitoring_active": False
    }

@router.get("/api/metrics")
async def get_metrics():
    with metrics_lock:
        by_node = {}
        for node, deq in metrics_store.items():
            samples = list(deq)
            by_node[node] = {
                "latest": samples[-1] if samples else None,
                "samples": samples
            }

    return {
        "by_node": by_node,
        "monitoring_active": monitoring_agents_started
    }