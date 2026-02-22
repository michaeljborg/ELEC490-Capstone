# core/config.py

import asyncio
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from pathlib import Path

# ---- CONFIG ----
PATH_TO_SCRIPT = "/home/cluster/ELEC490-Capstone"
MONITOR_PYTHON = "/home/cluster/vllm-venv/bin/python"
NODE_POOL = ["node2", "node3", "node4", "node5"]

MONITOR_PORT = 5000
METRICS_SAMPLES_CAP = 60
METRICS_LOG_DIR = Path(PATH_TO_SCRIPT) / "monitor" / "log"

NODE_CONCURRENCY = 1
MONITOR_SSH_HOSTS = {}

# ---- STATE ----
EXECUTOR = ThreadPoolExecutor(max_workers=32)

PENDING: dict[str, asyncio.Future] = {}
JOB_QUEUE: asyncio.Queue = asyncio.Queue()
AVAILABLE_NODES: asyncio.Queue = asyncio.Queue()

IN_FLIGHT = {node: 0 for node in NODE_POOL}
WAITING_FOR_NODE = 0

ACTIVE_SESSIONS = set()

_metrics_store: dict[str, deque] = {}
_metrics_lock = asyncio.Lock()
_monitoring_agents_started = False