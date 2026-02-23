import subprocess
import base64
import json
import node_interface_ip
import asyncio
import socket
import threading

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Import ALL config settings
from config import *

app = FastAPI()
templates = Jinja2Templates(directory=".")

# Initialize per-node runtime state (depends on NODE_POOL)
IN_FLIGHT.update({node: 0 for node in NODE_POOL})
LOCKS = {node: asyncio.Lock() for node in NODE_POOL}
DISPATCHER_TASK: asyncio.Task | None = None


def _metrics_listener():
    """Run in background thread: accept TCP connections on MONITOR_PORT, store + log metrics."""
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
            except OSError:
                break
            except Exception:
                pass


async def broadcast_status():
    """Push queue + per-node in-flight + user count to everyone."""
    if not ACTIVE_SESSIONS:
        return

    status = {
        "queue_depth": JOB_QUEUE.qsize(),
        "waiting_for_node": WAITING_FOR_NODE,
        "in_flight": dict(IN_FLIGHT),
        "total_users": len(ACTIVE_SESSIONS),
    }


    for ws in list(ACTIVE_SESSIONS):
        try:
            await ws.send_json(status)
        except:
            # Drop dead sockets quietly
            try:
                ACTIVE_SESSIONS.remove(ws)
            except KeyError:
                pass


NODE_CONCURRENCY = 1  # set >1 if you want multiple concurrent prompts per node

@app.on_event("startup")
async def startup_event():
    # Seed the free-node token queue
    for node in NODE_POOL:
        for _ in range(NODE_CONCURRENCY):
            AVAILABLE_NODES.put_nowait(node)

    asyncio.create_task(dispatch_loop())

    # Start metrics listener so we can receive from agents when they are started
    t = threading.Thread(target=_metrics_listener, daemon=True)
    t.start()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "gui.html",
        {
            "request": request,
            "node_pool": NODE_POOL,
            "node_pool_json": json.dumps(NODE_POOL),
            "METRICS_SAMPLES_CAP": METRICS_SAMPLES_CAP,
        },
    )


@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    await websocket.accept()
    ACTIVE_SESSIONS.add(websocket)
    await broadcast_status()
    try:
        while True:
            await websocket.receive_text()  # keepalive
    except WebSocketDisconnect:
        ACTIVE_SESSIONS.discard(websocket)
        await broadcast_status()


def ssh_relay(node: str, prompt: str) -> str:
    payload = {
        "ip": node_interface_ip.NODES[node],
        "model": "Qwen/Qwen2.5-1.5B-Instruct",
        "prompt": prompt,
    }
    b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    remote_cmd = (
        f'cd "{PATH_TO_SCRIPT}/gui" && '
        f'python3 -c "import base64, json, node_interface_ip; '
        f'd=json.loads(base64.b64decode(\'{b64}\').decode(\'utf-8\')); '
        f'print(node_interface_ip.query(**d))"'
    )

    proc = subprocess.run(
        ["ssh", node, remote_cmd],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


async def run_on_node(node: str, prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, ssh_relay, node, prompt)


async def dispatch_loop():
    global WAITING_FOR_NODE
    while True:
        job_id, prompt, fut = await JOB_QUEUE.get()  # oldest job

        WAITING_FOR_NODE += 1
        await broadcast_status()

        node = await AVAILABLE_NODES.get()  # waits until some node is free

        WAITING_FOR_NODE -= 1
        IN_FLIGHT[node] += 1
        await broadcast_status()

        async def _do(job_id=job_id, node=node, prompt=prompt, fut=fut):
            try:
                result = await run_on_node(node, prompt)
                if not fut.cancelled():
                    fut.set_result((node, result))
            except Exception as e:
                if not fut.cancelled():
                    fut.set_exception(e)
            finally:
                IN_FLIGHT[node] -= 1
                JOB_QUEUE.task_done()
                AVAILABLE_NODES.put_nowait(node)
                await broadcast_status()

        asyncio.create_task(_do())

@app.post("/relay")
async def relay(request: Request):
    data = await request.json()
    prompt = (data.get("prompt") or "").strip()
    job_id = data.get("job_id") or f"relay-{int(asyncio.get_running_loop().time())}"
    
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    await JOB_QUEUE.put((job_id, prompt, fut))
    await broadcast_status()

    try:
        node, val = await asyncio.wait_for(fut, timeout=180)
        return {"ok": True, "node": node, "line": val}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/enqueue")
async def enqueue(request: Request):
    data = await request.json()
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "Empty prompt"}

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    job_id = data.get("job_id") or "job"

    # How many prompts already waiting (not counting in-flight)
    ahead = JOB_QUEUE.qsize() + WAITING_FOR_NODE + sum(IN_FLIGHT.values())

    await JOB_QUEUE.put((job_id, prompt, fut))
    await broadcast_status()

    # We return immediately; client will then "await" completion via /wait/{job_id} OR long-poll
    # To keep it simple, store future by job_id:
    PENDING[job_id] = fut

    return {"ok": True, "job_id": job_id, "ahead": ahead}

@app.get("/wait/{job_id}")
async def wait(job_id: str):
    fut = PENDING.get(job_id)
    if fut is None:
        return {"ok": False, "error": "Unknown job_id"}

    try:
        node, val = await asyncio.wait_for(fut, timeout=180)
        return {"ok": True, "node": node, "line": val}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Timed out waiting in queue/processing"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        PENDING.pop(job_id, None)


# ---- Monitoring API ----
def _ssh_start_monitor_agent(node: str) -> bool:
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)
    py = MONITOR_PYTHON.strip()

    remote_cmd = (
        f"tmux kill-session -t monitor 2>/dev/null || true; "
        f"tmux new-session -d -s monitor "
        f"'{py} {PATH_TO_SCRIPT}/monitor/monitor_agent.py'"
    )

    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", ssh_host, remote_cmd],
        capture_output=True,
        text=True,
        timeout=15,
    )

    if proc.returncode != 0:
        print("STDERR:", proc.stderr)

    return proc.returncode == 0


def _check_agent_running(node: str) -> bool:
    """Return True if monitor_agent.py process is running on the node."""
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=3", ssh_host, "pgrep -f 'monitor/monitor_agent.py'"],
        capture_output=True,
        text=True,
        timeout=8,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _ssh_stop_monitor_agent(node: str) -> bool:
    """Stop monitor_agent on one node. Returns True on success."""
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", ssh_host, "tmux kill-session -t monitor 2>/dev/null || true"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode == 0


@app.post("/api/monitoring/start")
async def monitoring_start():
    """Start monitor_agent on all nodes via SSH + tmux."""
    global monitoring_agents_started
    loop = asyncio.get_running_loop()
    results = {}
    errors = {}
    for node in NODE_POOL:
        try:
            ok = await loop.run_in_executor(EXECUTOR, _ssh_start_monitor_agent, node)
            err = None
            results[node] = ok
            if err:
                errors[node] = err
        except Exception as e:
            results[node] = False
            errors[node] = str(e)
    monitoring_agents_started = any(results.values())
    return {"ok": True, "agents": results, "agent_errors": errors, "monitoring_active": monitoring_agents_started}


@app.get("/api/monitoring/agent-status")
async def monitoring_agent_status():
    """Which nodes currently have the monitor agent process running (after Start)."""
    loop = asyncio.get_running_loop()
    status = {}
    for node in NODE_POOL:
        try:
            status[node] = await loop.run_in_executor(EXECUTOR, _check_agent_running, node)
        except Exception:
            status[node] = False
    return {"nodes": status}


def _diagnose_node(node: str) -> str:
    """Run the agent on the node for 2s and return stdout+stderr (to see why it exits)."""
    ssh_host = MONITOR_SSH_HOSTS.get(node, node)
    remote_cmd = (
        f'bash -lc \'cd "{PATH_TO_SCRIPT}" && timeout 2 python3 monitor/monitor_agent.py 2>&1\' || true'
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


@app.get("/api/monitoring/diagnose")
async def monitoring_diagnose(node: str):
    """Run the agent briefly on the given node and return its output (traceback, etc.)."""
    if node not in NODE_POOL:
        return {"ok": False, "error": "unknown node"}
    loop = asyncio.get_running_loop()
    output = await loop.run_in_executor(EXECUTOR, _diagnose_node, node)
    return {"ok": True, "node": node, "output": output}


@app.post("/api/monitoring/stop")
async def monitoring_stop():
    """Stop monitor_agent on all nodes."""
    global monitoring_agents_started
    loop = asyncio.get_running_loop()
    results = {}
    for node in NODE_POOL:
        try:
            ok = await loop.run_in_executor(EXECUTOR, _ssh_stop_monitor_agent, node)
            results[node] = ok
        except Exception:
            results[node] = False
    monitoring_agents_started = False
    return {"ok": True, "agents": results, "monitoring_active": False}


@app.get("/api/metrics")
async def get_metrics():
    """Latest metrics and last N samples per node for charts; log is written to file."""
    with metrics_lock:
        by_node = {}
        for node, deq in metrics_store.items():
            samples = list(deq)
            by_node[node] = {
                "latest": samples[-1] if samples else None,
                "samples": samples,
            }
    return {
        "by_node": by_node,
        "monitoring_active": monitoring_agents_started,
        "log_path": str(METRICS_LOG_DIR),
    }


@app.get("/api/monitoring/status")
async def monitoring_status():
    return {"monitoring_active": monitoring_agents_started}

def _start_vllm_node(node: str):
    try:
        node_interface_ip.start(node)
        node_interface_ip.wait_for_ready(node, timeout=120)
        return True, None
    except Exception as e:
        return False, str(e)

def _stop_vllm_node(node: str):
    try:
        remote_cmd = "tmux kill-session -t vllm 2>/dev/null || true"
        proc = subprocess.run(
            ["ssh", node, remote_cmd],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode == 0, None
    except Exception as e:
        return False, str(e)

@app.post("/api/vllm/start")
async def start_vllm_cluster():
    loop = asyncio.get_running_loop()
    results = {}
    errors = {}

    for node in NODE_POOL:
        try:
            ok, err = await loop.run_in_executor(
                EXECUTOR,
                _start_vllm_node,
                node
            )
            results[node] = ok
            if err:
                errors[node] = err
        except Exception as e:
            results[node] = False
            errors[node] = str(e)

    return {
        "ok": True,
        "nodes": results,
        "errors": errors
    }

@app.post("/api/vllm/stop")
async def stop_vllm_cluster():
    loop = asyncio.get_running_loop()
    results = {}
    errors = {}

    for node in NODE_POOL:
        try:
            ok, err = await loop.run_in_executor(
                EXECUTOR,
                _stop_vllm_node,
                node
            )
            results[node] = ok
            if err:
                errors[node] = err
        except Exception as e:
            results[node] = False
            errors[node] = str(e)

    return {
        "ok": True,
        "nodes": results,
        "errors": errors
    }

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
]

@app.post("/spam50")
async def spam50():
    loop = asyncio.get_running_loop()

    job_ids = []
    ahead_before = JOB_QUEUE.qsize() + WAITING_FOR_NODE + sum(IN_FLIGHT.values())

    for i, p in enumerate(SPAM_PROMPTS_50, start=1):
        fut = loop.create_future()
        job_id = f"spam-{i}-{int(loop.time()*1000)}"  # avoid collisions
        prompt = p

        PENDING[job_id] = fut
        await JOB_QUEUE.put((job_id, prompt, fut))
        job_ids.append(job_id)

    await broadcast_status()
    return {"ok": True, "enqueued": 50, "ahead_before": ahead_before, "job_ids": job_ids}
