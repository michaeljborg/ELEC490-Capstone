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

from app.nodes import node_interface_ip
from app.nodes import node_interface_class

app = FastAPI()
templates = Jinja2Templates(directory="app/frontend")

# Mount monitoring routes
app.include_router(monitoring_router)

CURRENT_MODEL: str | None = None
CURRENT_BATCH_SIZE: int = 1
NODE_CONCURRENCY: int = 1

# Initialize per-node runtime state
IN_FLIGHT.update({node: 0 for node in NODE_POOL})
LOCKS = {node: asyncio.Lock() for node in NODE_POOL}
DISPATCHER_TASK: asyncio.Task | None = None

NODE_CONCURRENCY = 1
NODE_HEALTHY = {node: True for node in NODE_POOL}

# Per-job websocket registry for streaming
JOB_SOCKETS: dict[str, WebSocket] = {}


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
        "node_healthy": dict(NODE_HEALTHY),
    }

    for ws in list(ACTIVE_SESSIONS):
        try:
            await ws.send_json(status)
        except Exception:
            ACTIVE_SESSIONS.discard(ws)


# =============================
# STARTUP
# =============================

@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_running_loop()

    # Only schedule nodes we can SSH into
    checks = {
        node: loop.run_in_executor(EXECUTOR, _ssh_ok, node)
        for node in NODE_POOL
    }
    results = await asyncio.gather(*checks.values())

    for node, ok in zip(checks.keys(), results):
        NODE_HEALTHY[node] = ok
        if ok:
            for _ in range(NODE_CONCURRENCY):
                AVAILABLE_NODES.put_nowait(node)
        else:
            print(f"[WARN] {node} unreachable via SSH; skipping")

    asyncio.create_task(dispatch_loop())

    # Start monitoring TCP listener
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
# WEBSOCKETS
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


@app.websocket("/ws/job/{job_id}")
async def websocket_job(websocket: WebSocket, job_id: str):
    await websocket.accept()
    JOB_SOCKETS[job_id] = websocket

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        JOB_SOCKETS.pop(job_id, None)


# =============================
# SSH RELAY (NON-STREAMING)
# =============================

def http_relay(node: str, payload) -> str:
    ip = node_interface_ip.NODES[node]

    url = f"http://{ip}:8000/v1/chat/completions"

    if isinstance(payload, list):
        messages = payload
    else:
        messages = [{"role": "user", "content": payload}]

    data = {
        "model": CURRENT_MODEL,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.7,
    }

    r = requests.post(url, json=data, timeout=120)
    r.raise_for_status()

    return r.json()["choices"][0]["message"]["content"]


async def run_on_node(node: str, payload) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, http_relay, node, payload)


# =============================
# SSH RELAY (STREAMING)
# =============================

def ssh_stream_relay(node: str, payload, on_event) -> str:
    """
    Expects remote stream_query(...) to print one JSON event per line.
    Example:
      {"type":"chunk","text":"hello"}
      {"type":"chunk","text":" world"}
      {"type":"done"}
    """
    data = {
        "ip": node_interface_ip.NODES[node],
        "model": CURRENT_MODEL,
    }

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
        f'node_interface_ip.stream_query(**d)"'
    )

    proc = subprocess.Popen(
        ["ssh", node, remote_cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    full_text_parts = []

    try:
        assert proc.stdout is not None

        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"type": "chunk", "text": line}

            if event.get("type") == "chunk":
                text = event.get("text", "")
                if text:
                    full_text_parts.append(text)

            on_event(event)

        ret = proc.wait(timeout=10)

        if ret != 0:
            err = proc.stderr.read().strip() if proc.stderr else "Unknown SSH error"
            raise RuntimeError(err)

        return "".join(full_text_parts)

    finally:
        try:
            proc.kill()
        except Exception:
            pass


async def run_stream_on_node(job_id: str, node: str, payload) -> str:
    loop = asyncio.get_running_loop()

    def worker():
        def emit(event: dict):
            asyncio.run_coroutine_threadsafe(send_job_event(job_id, event), loop)

        return ssh_stream_relay(node, payload, emit)

    return await asyncio.to_thread(worker)


# =============================
# DISPATCH LOOP
# =============================

async def dispatch_loop():
    global WAITING_FOR_NODE

    while True:
        # allow jobs to accumulate briefly (micro-batch window)
        await asyncio.sleep(0.002)

        jobs = []

        # collect multiple queued jobs
        while not JOB_QUEUE.empty():
            jobs.append(await JOB_QUEUE.get())

            # safety cap so bursts don't grow too large
            if len(jobs) >= 32:
                break

        # if nothing accumulated, block for one job
        if not jobs:
            jobs.append(await JOB_QUEUE.get())

        for job_id, payload, fut in jobs:

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
                    NODE_HEALTHY[node] = False
                    if not fut.cancelled():
                        fut.set_exception(e)
                finally:
                    IN_FLIGHT[node] -= 1
                    JOB_QUEUE.task_done()
                    if NODE_HEALTHY.get(node, True):
                        AVAILABLE_NODES.put_nowait(node)
                    await broadcast_status()

            asyncio.create_task(_do())
        async def _do(job_id=job_id, node=node, payload=payload, fut=fut):
            try:
                await send_job_event(job_id, {"type": "start", "node": node})

                # Stream to websocket while also collecting final text
                result = await run_stream_on_node(job_id, node, payload)

                await send_job_event(job_id, {"type": "done", "node": node})

                if not fut.cancelled():
                    fut.set_result((node, result))

            except Exception as e:
                err_text = str(e)

                # Only quarantine for likely node/SSH/network failures
                if any(x in err_text.lower() for x in [
                    "ssh",
                    "connection refused",
                    "connection reset",
                    "timed out",
                    "timeout",
                    "no route to host",
                    "host unreachable",
                    "network is unreachable",
                    "could not resolve hostname",
                ]):
                    NODE_HEALTHY[node] = False

                await send_job_event(job_id, {
                    "type": "error",
                    "node": node,
                    "error": err_text,
                })

                if not fut.cancelled():
                    fut.set_exception(e)

            finally:
                IN_FLIGHT[node] -= 1
                JOB_QUEUE.task_done()

                # Only return healthy nodes to rotation
                if NODE_HEALTHY.get(node, True):
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
        if not NODE_HEALTHY.get(node, True):
            return False

        ip = node_interface_ip.NODES[node]
        url = f"http://{ip}:8000/health"
        r = requests.get(url, timeout=2)
        return r.status_code == 200
    except Exception:
        return False

def _start_vllm_node(node: str, model: str, batch_size: int):
    try:
        node_interface_ip.start(node, model=model, batch_size=batch_size)
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
    global CURRENT_MODEL, CURRENT_BATCH_SIZE, NODE_CONCURRENCY
    data = await request.json()
    model = data.get("model")
    batch_size = int(data.get("batch_size", 1))

    if model not in AVAILABLE_MODELS:
        return {"ok": False, "error": "Invalid model"}

    healthy_nodes = [node for node in NODE_POOL if NODE_HEALTHY.get(node, True)]

    if not healthy_nodes:
        return {"ok": False, "error": "No healthy nodes available"}

    loop = asyncio.get_running_loop()
    results = {}
    errors = {}

    tasks = {
        node: loop.run_in_executor(EXECUTOR, _start_vllm_node, node, model, batch_size)
        for node in healthy_nodes
    }

    completed = await asyncio.gather(*tasks.values())
    started_any = False

    for node, (ok, err) in zip(tasks.keys(), completed):
        results[node] = ok
        if err:
            errors[node] = err
        if ok:
            started_any = True
        else:
            NODE_HEALTHY[node] = False

    for node in NODE_POOL:
        if node not in results:
            results[node] = False
            errors[node] = "Skipped: node unhealthy/unreachable"

    if not started_any:
        return {
            "ok": False,
            "error": "Failed to start on any healthy node",
            "nodes": results,
            "errors": errors,
        }

    CURRENT_MODEL = model
    CURRENT_BATCH_SIZE = batch_size
    NODE_CONCURRENCY = batch_size

    # rebuild node availability queue based on new concurrency
    global AVAILABLE_NODES
    AVAILABLE_NODES = asyncio.Queue()

    for node in healthy_nodes:
        for _ in range(NODE_CONCURRENCY):
            AVAILABLE_NODES.put_nowait(node)

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

    healthy_nodes = [node for node in NODE_POOL if NODE_HEALTHY.get(node, True)]

    tasks = {
        node: loop.run_in_executor(EXECUTOR, _stop_vllm_node, node)
        for node in healthy_nodes
    }

    if tasks:
        completed = await asyncio.gather(*tasks.values())
        for node, (ok, err) in zip(tasks.keys(), completed):
            results[node] = ok
            if err:
                errors[node] = err

    for node in NODE_POOL:
        if node not in results:
            results[node] = False
            errors[node] = "Skipped: node unhealthy/unreachable"

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

    model_active = any(node_status.values())

    return {
        "model": CURRENT_MODEL if model_active else None,
        "nodes": node_status
    }


@app.on_event("shutdown")
async def shutdown_event():
    print("Backend shutting down. Stopping vLLM cluster...")

    loop = asyncio.get_running_loop()

    for node in NODE_POOL:
        if not NODE_HEALTHY.get(node, True):
            continue
        try:
            await loop.run_in_executor(EXECUTOR, _stop_vllm_node, node)
            print(f"Stopped vLLM on {node}")
        except Exception as e:
            print(f"Failed to stop vLLM on {node}: {e}")

    print("Cluster shutdown complete.")


# =============================
# Spam 50
# =============================

@app.post("/spam50")
async def spam50():
    return {
        "ok": True,
        "prompts": SPAM_PROMPTS_50,
    }