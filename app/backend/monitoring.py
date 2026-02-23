import subprocess
import socket
import json
import threading
import asyncio
from collections import deque
from fastapi import APIRouter
from app.config import *

router = APIRouter()

metrics_store: dict[str, deque] = {}
metrics_lock = threading.Lock()
monitoring_agents_started = False


# =============================
# METRICS LISTENER THREAD
# =============================

def start_metrics_listener():
    def _metrics_listener():
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

                        with metrics_lock:
                            if node_name not in metrics_store:
                                metrics_store[node_name] = deque(maxlen=METRICS_SAMPLES_CAP)
                            metrics_store[node_name].append(metrics)

                        try:
                            METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
                            log_file = METRICS_LOG_DIR / f"{node_name}.jsonl"
                            with open(log_file, "a") as f:
                                f.write(json.dumps(metrics) + "\n")
                        except Exception:
                            pass

                except Exception:
                    pass

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
        capture_output=True,
        text=True,
        timeout=15,
    )

    return proc.returncode == 0


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


# =============================
# ROUTES
# =============================

@router.post("/api/monitoring/start")
async def monitoring_start():
    global monitoring_agents_started

    loop = asyncio.get_running_loop()
    results = {}

    for node in NODE_POOL:
        ok = await loop.run_in_executor(EXECUTOR, _ssh_start_monitor_agent, node)
        results[node] = ok

    monitoring_agents_started = any(results.values())

    return {
        "ok": True,
        "agents": results,
        "monitoring_active": monitoring_agents_started
    }


@router.post("/api/monitoring/stop")
async def monitoring_stop():
    global monitoring_agents_started

    loop = asyncio.get_running_loop()
    results = {}

    for node in NODE_POOL:
        ok = await loop.run_in_executor(EXECUTOR, _ssh_stop_monitor_agent, node)
        results[node] = ok

    monitoring_agents_started = False

    return {
        "ok": True,
        "agents": results,
        "monitoring_active": False
    }


@router.get("/api/metrics")
async def get_metrics():
    with metrics_lock:
        by_node = {
            node: {
                "latest": list(deq)[-1] if deq else None,
                "samples": list(deq)
            }
            for node, deq in metrics_store.items()
        }

    return {
        "by_node": by_node,
        "monitoring_active": monitoring_agents_started
    }