import subprocess
import base64
import json
import asyncio
import requests

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.nodes import node_interface_ip

# Import ALL config settings
from app.config import *
from app.config import SPAM_PROMPTS_50
from app.config import AVAILABLE_MODELS

# Import monitoring router + startup hook
from app.backend.monitoring import router as monitoring_router
from app.backend.monitoring import start_metrics_listener

app = FastAPI()
templates = Jinja2Templates(directory="app/frontend")

# Mount monitoring routes
app.include_router(monitoring_router)

CURRENT_MODEL: str | None = None

# Initialize per-node runtime state (depends on NODE_POOL)
IN_FLIGHT.update({node: 0 for node in NODE_POOL})
LOCKS = {node: asyncio.Lock() for node in NODE_POOL}
DISPATCHER_TASK: asyncio.Task | None = None


# =============================
# STATUS BROADCAST
# =============================

async def broadcast_status():
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
            ACTIVE_SESSIONS.discard(ws)


NODE_CONCURRENCY = 1


# =============================
# STARTUP
# =============================

@app.on_event("startup")
async def startup_event():
    # Seed node availability queue
    for node in NODE_POOL:
        for _ in range(NODE_CONCURRENCY):
            AVAILABLE_NODES.put_nowait(node)

    # Start dispatch loop
    asyncio.create_task(dispatch_loop())

    # Start monitoring TCP listener (now lives in monitoring_backend)
    start_metrics_listener()


# =============================
# ROOT PAGE
# =============================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "gui.html",
        {
            "request": request,
            "node_pool": NODE_POOL,
            "node_pool_json": json.dumps(NODE_POOL),
        },
    )


# =============================
# WEBSOCKET STATUS
# =============================

@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    await websocket.accept()
    ACTIVE_SESSIONS.add(websocket)
    await broadcast_status()

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ACTIVE_SESSIONS.discard(websocket)
        await broadcast_status()


# =============================
# SSH RELAY
# =============================

def ssh_relay(node: str, payload) -> str:
    data = {
        "ip": node_interface_ip.NODES[node],
        "model": CURRENT_MODEL,
    }

    # detect payload type
    if isinstance(payload, list):
        data["messages"] = payload
    else:
        data["prompt"] = payload

    b64 = base64.b64encode(json.dumps(data).encode()).decode()

    remote_cmd = (
        f'cd "{PATH_TO_SCRIPT}" && '
        f'PYTHONPATH="{PATH_TO_SCRIPT}" '
        f'python3 -c "import base64, json; '
        f'from app.nodes import node_interface_ip; '
        f'd=json.loads(base64.b64decode(\'{b64}\').decode()); '
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


async def run_on_node(node: str, payload) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, ssh_relay, node, payload)

# =============================
# DISPATCH LOOP
# =============================

async def dispatch_loop():
    global WAITING_FOR_NODE

    while True:
        job_id, payload, fut = await JOB_QUEUE.get()

        WAITING_FOR_NODE += 1
        await broadcast_status()

        node = await AVAILABLE_NODES.get()

        WAITING_FOR_NODE -= 1
        IN_FLIGHT[node] += 1
        await broadcast_status()

        async def _do(job_id=job_id, node=node, payload=payload, fut=fut):
            try:
                result = await run_on_node(node, payload)
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


# =============================
# QUEUE ENDPOINTS
# =============================

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

    if CURRENT_MODEL is None:
        return {"ok": False, "error": "No model loaded"}

    data = await request.json()

    prompt = (data.get("prompt") or "").strip()
    messages = data.get("messages")

    if not prompt and not messages:
        return {"ok": False, "error": "Empty input"}

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    job_id = data.get("job_id") or "job"

    ahead = JOB_QUEUE.qsize() + WAITING_FOR_NODE + sum(IN_FLIGHT.values())

    payload = messages if messages else prompt

    await JOB_QUEUE.put((job_id, payload, fut))
    PENDING[job_id] = fut

    await broadcast_status()

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


# =============================
# vLLM CONTROL
# =============================

def _check_vllm_node(node: str):
    try:
        ip = node_interface_ip.NODES[node]
        url = f"http://{ip}:8000/health"
        r = requests.get(url, timeout=2)
        return r.status_code == 200
    except Exception as e:
        return False

def _start_vllm_node(node: str, model: str):
    try:
        node_interface_ip.start(node, model=model)
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
async def start_vllm_cluster(request: Request):
    global CURRENT_MODEL

    data = await request.json()
    model = data.get("model")

    if model not in AVAILABLE_MODELS:
        return {"ok": False, "error": "Invalid model"}

    loop = asyncio.get_running_loop()
    results = {}
    errors = {}

    tasks = {
        node: loop.run_in_executor(EXECUTOR, _start_vllm_node, node, model)
        for node in NODE_POOL
    }

    completed = await asyncio.gather(*tasks.values())

    all_ok = True

    for node, (ok, err) in zip(tasks.keys(), completed):
        results[node] = ok
        if err:
            errors[node] = err
        if not ok:
            all_ok = False

    if not all_ok:
        return {
            "ok": False,
            "error": "Failed to start all nodes",
            "nodes": results,
            "errors": errors,
        }

    # Only set after successful startup
    CURRENT_MODEL = model

    return {
        "ok": True,
        "model": CURRENT_MODEL,
        "nodes": results,
        "errors": errors,
    }


@app.post("/api/vllm/stop")
async def stop_vllm_cluster():
    global CURRENT_MODEL

    loop = asyncio.get_running_loop()
    results = {}
    errors = {}

    for node in NODE_POOL:
        ok, err = await loop.run_in_executor(EXECUTOR, _stop_vllm_node, node)
        results[node] = ok
        if err:
            errors[node] = err

    CURRENT_MODEL = None

    return {
        "ok": True,
        "model": CURRENT_MODEL,
        "nodes": results,
        "errors": errors,
    }

@app.get("/api/vllm/status")
async def vllm_status():
    loop = asyncio.get_running_loop()

    tasks = {
        node: loop.run_in_executor(EXECUTOR, _check_vllm_node, node)
        for node in NODE_POOL
    }

    results = await asyncio.gather(*tasks.values())
    node_status = dict(zip(tasks.keys(), results))

    # Determine if at least one node is alive
    model_active = any(node_status.values())

    return {
        "model": CURRENT_MODEL if model_active else None,
        "nodes": node_status
    }

# =============================
# Spam 50 
# =============================
@app.post("/spam50")
async def spam50():
    loop = asyncio.get_running_loop()

    job_ids = []
    ahead_before = JOB_QUEUE.qsize() + WAITING_FOR_NODE + sum(IN_FLIGHT.values())

    for i, p in enumerate(SPAM_PROMPTS_50, start=1):
        fut = loop.create_future()
        job_id = f"spam-{i}-{int(loop.time()*1000)}"

        PENDING[job_id] = fut
        await JOB_QUEUE.put((job_id, p, fut))
        job_ids.append(job_id)

    await broadcast_status()

    return {
        "ok": True,
        "enqueued": 50,
        "ahead_before": ahead_before,
        "job_ids": job_ids,
    }
