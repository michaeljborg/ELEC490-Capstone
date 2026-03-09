import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import threading
import json
import requests

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

# Models
AVAILABLE_MODELS = [
    "Qwen/Qwen2.5-1.5B-Instruct",
    "Qwen/Qwen2.5-7B-Instruct-AWQ",
    "hugging-quants/Meta-Llama-3.1-8B-Instruct-AWQ-INT4",
    "meta-llama/Llama-3.2-3B-Instruct",
    "google/gemma-2-2b-it"
]

# ==========================================================
# BENCHMARK DATASET
# ==========================================================
BENCHMARK_PROMPTS = []
PROMPTS_FILE = Path(PATH_TO_SCRIPT) / "benchmark_prompts.json"

def load_benchmark_prompts():
    global BENCHMARK_PROMPTS
    if not PROMPTS_FILE.exists():
        print("Downloading benchmark dataset (Alpaca)...")
        url = "https://raw.githubusercontent.com/tatsu-lab/stanford_alpaca/main/alpaca_data.json"
        try:
            r = requests.get(url, timeout=30)
            data = r.json()
            BENCHMARK_PROMPTS = [item["instruction"] for item in data]
            with open(PROMPTS_FILE, "w") as f:
                json.dump(BENCHMARK_PROMPTS, f)
        except Exception as e:
            print(f"[WARN] Failed to load dataset: {e}")
            BENCHMARK_PROMPTS = ["Fallback test prompt"] * 100 
    else:
        with open(PROMPTS_FILE, "r") as f:
            BENCHMARK_PROMPTS = json.load(f)

load_benchmark_prompts()
