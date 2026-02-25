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

SPAM_PROMPTS_50 = [
    "Say hello.",
    "List 3 fruits.",
    "Write one short joke.",
    "Give 2 productivity tips.",
    "Name 4 animals.",
    "Describe coffee briefly.",
    "Give one fun fact.",
    "Write a short slogan about speed.",
    "Say something positive.",
    "Write a tiny poem (2 lines).",
    "List 3 colors.",
    "Describe the sky in one sentence.",
    "Give one quick tech tip.",
    "Say something funny (short).",
    "Write one cheerful sentence.",
    "Say good morning.",
    "Say good evening.",
    "List 3 vegetables.",
    "Write a friendly greeting.",
    "Say something encouraging.",
    "Write one line about servers.",
    "Name 5 tools.",
    "Say something calm.",
    "Give one tip about focus.",
    "Write a short compliment.",
    "List 3 cities.",
    "Say something creative.",
    "Write one sentence about teamwork.",
    "Give one tiny idea for a project.",
    "Write a short slogan (<=6 words).",
    "Say thanks in a fun way.",
    "Write a short message to a friend.",
    "Describe rain in 8 words.",
    "Name 3 hobbies.",
    "Say something optimistic.",
    "Write a tiny story (1 sentence).",
    "List 3 drinks.",
    "Say something confident.",
    "Write one line about learning.",
    "Say something nice about today.",
    "Write a short toast (1 sentence).",
    "Say hello again.",
    "Write a short tagline for a cluster.",
    "Give 3 quick tips for sleep.",
    "Say something motivating.",
    "Write a tiny rhyme.",
    "Name 3 animals again.",
    "Say something friendly.",
    "Write a short good-luck message.",
    "Say goodbye.",
    "我草泥马"
]
