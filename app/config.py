import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import threading

# ==========================================================
# CONFIG
# ==========================================================

PATH_TO_SCRIPT = "/home/cluster/ELEC490-Capstone"
MONITOR_PYTHON = "/home/cluster/vllm-venv/bin/python"
NODE_POOL = ["node2", "node3", "node4", "node5"]

# Monitoring
MONITOR_PORT = 5000
METRICS_SAMPLES_CAP = 60
METRICS_LOG_DIR = Path(PATH_TO_SCRIPT) / "monitor" / "log"
MONITOR_SSH_HOSTS = {}

# Node concurrency
NODE_CONCURRENCY = 1


# ==========================================================
# RUNTIME STATE
# ==========================================================

# Job tracking
PENDING: dict[str, asyncio.Future] = {}
WAITING_FOR_NODE = 0
JOB_QUEUE: asyncio.Queue = asyncio.Queue()
AVAILABLE_NODES: asyncio.Queue = asyncio.Queue()

# Will be initialized after import (depends on NODE_POOL)
IN_FLIGHT: dict[str, int] = {}
ACTIVE_SESSIONS = set()

# Execution
EXECUTOR = ThreadPoolExecutor(max_workers=32)

# Monitoring state
metrics_store: dict[str, deque] = {}
metrics_lock = threading.Lock()
monitoring_agents_started = False
