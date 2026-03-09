import subprocess
import base64
import json
import asyncio
import requests
import threading
import uuid
import os

from datetime import datetime
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.nodes import node_interface_ip

# Import ALL config settings
import app.config as cfg

# Import monitoring router + startup hook
from app.backend.monitoring import router as monitoring_router
from app.backend.monitoring import start_metrics_listener
from app.backend.monitoring import _ssh_start_monitor_agent

app = FastAPI()
templates = Jinja2Templates(directory="app/frontend")

# Mount monitoring routes
app.include_router(monitoring_router)

CURRENT_MODEL: str | None = None
CURRENT_BATCH_SIZE: int = 1
NODE_CONCURRENCY: int = 1

SHARED_STATE = {
    "current_model": None,
    "batch_size": 1,
    "nodes": {node: "offline" for node in cfg.NODE_POOL}
}

STATE_LOCK = asyncio.Lock()
CONNECTED_CLIENTS: set[WebSocket] = set()

# Initialize per-node runtime state (depends on cfg.NODE_POOL)
cfg.IN_FLIGHT.update({node: 0 for node in cfg.NODE_POOL})
LOCKS = {node: asyncio.Lock() for node in cfg.NODE_POOL}
DISPATCHER_TASK: asyncio.Task | None = None

NODE_HEALTHY = {node: True for node in cfg.NODE_POOL}

STREAM_QUEUES: dict[str, asyncio.Queue] = {}
STREAM_DONE: dict[str, bool] = {}
JOB_META: dict[str, dict] = {}

# =============================
# STATUS BROADCAST
# =============================

async def broadcast_status():
    async with STATE_LOCK:
        SHARED_STATE["queue_depth"] = cfg.JOB_QUEUE.qsize() + cfg.WAITING_FOR_NODE
        SHARED_STATE["total_users"] = len(CONNECTED_CLIENTS)

    await broadcast_shared_state()


# =============================
# STARTUP
# =============================

@app.on_event("startup")
async def startup_event():
    global DISPATCHER_TASK
    loop = asyncio.get_running_loop()

    # Only schedule nodes we can SSH into
    checks = {node: loop.run_in_executor(cfg.EXECUTOR, _ssh_ok, node) for node in cfg.NODE_POOL}
    results = await asyncio.gather(*checks.values())

    for node, ok in zip(checks.keys(), results):
        NODE_HEALTHY[node] = ok
        if ok:
            for _ in range(NODE_CONCURRENCY):
                cfg.AVAILABLE_NODES.put_nowait(node)
        else:
            print(f"[WARN] {node} unreachable via SSH; skipping")

    DISPATCHER_TASK = asyncio.create_task(dispatch_loop())
    print("[STARTUP] dispatch loop started", DISPATCHER_TASK)

    start_metrics_listener()

    # Start monitoring agents so metrics are always streaming
    tasks = [
        loop.run_in_executor(cfg.EXECUTOR, _ssh_start_monitor_agent, node)
        for node in cfg.NODE_POOL
    ]

    await asyncio.gather(*tasks)
    print("[STARTUP] monitoring agents started on all nodes")


# =============================
# ROOT PAGE
# =============================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "gui.html",
        {
            "request": request,
            "node_pool": cfg.NODE_POOL,
            "node_pool_json": json.dumps(cfg.NODE_POOL),
        },
    )


# =============================
# WEBSOCKET STATUS
# =============================

@app.websocket("/ws/status")
async def websocket_status(ws: WebSocket):
    await ws.accept()
    CONNECTED_CLIENTS.add(ws)
    print(f"[WS] connected, total clients = {len(CONNECTED_CLIENTS)}")

    await broadcast_status()

    try:
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        CONNECTED_CLIENTS.discard(ws)
        print(f"[WS] disconnected, total clients = {len(CONNECTED_CLIENTS)}")
        await broadcast_status()
    except Exception as e:
        CONNECTED_CLIENTS.discard(ws)
        print(f"[WS] error: {e}, total clients = {len(CONNECTED_CLIENTS)}")
        await broadcast_status()

@app.websocket("/ws/stream/{job_id}")
async def websocket_stream(websocket: WebSocket, job_id: str):
    await websocket.accept()

    stream_q = STREAM_QUEUES.get(job_id)
    if stream_q is None:
        await websocket.send_json({"type": "error", "error": "Unknown or non-streaming job_id"})
        await websocket.close()
        return

    try:
        while True:
            item = await stream_q.get()

            await websocket.send_json(item)

            if item["type"] in ("done", "error"):
                break

    except WebSocketDisconnect:
        pass
    finally:
        STREAM_QUEUES.pop(job_id, None)
        STREAM_DONE.pop(job_id, None)
        JOB_META.pop(job_id, None)

_UNSET = object()

async def update_shared_state(model=_UNSET, batch_size=_UNSET, node_updates=None):
    global CURRENT_MODEL, CURRENT_BATCH_SIZE

    async with STATE_LOCK:
        if model is not _UNSET:
            CURRENT_MODEL = model
            SHARED_STATE["current_model"] = model

        if batch_size is not _UNSET:
            CURRENT_BATCH_SIZE = batch_size
            SHARED_STATE["batch_size"] = batch_size

        if node_updates:
            for node, status in node_updates.items():
                SHARED_STATE["nodes"][node] = status

    await broadcast_shared_state()

async def broadcast_shared_state():
    payload = {
        "type": "shared_state",
        "state": SHARED_STATE
    }

    print(f"[WS] broadcasting to {len(CONNECTED_CLIENTS)} clients: {payload}")

    dead = set()
    for ws in CONNECTED_CLIENTS:
        try:
            await ws.send_json(payload)
        except Exception as e:
            print(f"[WS] send failed: {e}")
            dead.add(ws)

    for ws in dead:
        CONNECTED_CLIENTS.discard(ws)

# =============================
# RELAY
# =============================

import time

import time

# # non-streaming response (NO LONGER USED)
# def http_relay(node: str, payload):
#     ip = node_interface_ip.NODES[node]
#     url = f"http://{ip}:8000/v1/chat/completions"

#     if isinstance(payload, list):
#         messages = payload
#     else:
#         messages = [{"role": "user", "content": payload}]

#     data = {
#         "model": CURRENT_MODEL,
#         "messages": messages,
#         "max_tokens": 1024,
#         "temperature": 0.7,
#     }

#     start = time.time()

#     r = requests.post(url, json=data, timeout=120)
#     r.raise_for_status()

#     end = time.time()

#     response = r.json()

#     text = response["choices"][0]["message"]["content"]
#     usage = response.get("usage", {})

#     prompt_tokens = usage.get("prompt_tokens", 0)
#     completion_tokens = usage.get("completion_tokens", 0)
#     total_tokens = usage.get("total_tokens", 0)

#     latency = end - start

#     tokens_per_sec = completion_tokens / latency if latency > 0 else 0

#     return {
#         "text": text,
#         "metrics": {
#             "node": node,
#             "prompt_tokens": prompt_tokens,
#             "completion_tokens": completion_tokens,
#             "total_tokens": total_tokens,
#             "latency": latency,
#             "tokens_per_sec": tokens_per_sec,
#         }
#     }

# streaming relay (IN USE)
def http_relay_stream(node: str, payload, loop: asyncio.AbstractEventLoop, stream_q: asyncio.Queue):
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
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    start = time.time()
    first_token_time = None

    full_text = ""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    try:
        with requests.post(url, json=data, timeout=180, stream=True) as r:
            r.raise_for_status()

            for raw_line in r.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue

                line = raw_line.strip()

                if not line.startswith("data:"):
                    continue

                data_str = line[5:].strip()

                if data_str == "[DONE]":
                    break

                try:
                    evt = json.loads(data_str)
                except Exception:
                    continue

                # =============================
                # Capture usage (comes at end)
                # =============================
                usage = evt.get("usage")

                if usage:
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get("completion_tokens", completion_tokens)
                    total_tokens = usage.get("total_tokens", total_tokens)

                choices = evt.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {})
                chunk = delta.get("content", "")


                if chunk:

                    # TTFT
                    if first_token_time is None:
                        first_token_time = time.time()                    

                    full_text += chunk
                    loop.call_soon_threadsafe(stream_q.put_nowait, {
                        "type": "chunk",
                        "text": chunk,
                    })

        end = time.time()

        latency = end - start

        ttft = (first_token_time - start) if first_token_time else 0
        generation_time = (end - first_token_time) if first_token_time else 0

        tokens_per_sec = (
            completion_tokens / generation_time
            if generation_time > 0 else 0
        )

        final_payload = {
            "text": full_text,
            "metrics": {
                "node": node,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "ttft": ttft,
                "generation_time": generation_time,
                "latency": latency,
                "tokens_per_sec": tokens_per_sec,
            },
        }

        loop.call_soon_threadsafe(stream_q.put_nowait, {
            "type": "done",
            "final": final_payload,
        })

        return final_payload

    except Exception as e:
        loop.call_soon_threadsafe(stream_q.put_nowait, {
            "type": "error",
            "error": str(e),
        })
        raise

async def run_on_node_stream(node: str, payload, stream_q: asyncio.Queue):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(cfg.EXECUTOR, http_relay_stream, node, payload, loop, stream_q)


async def run_on_node(node: str, payload) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(cfg.EXECUTOR, http_relay, node, payload)

def _ssh_ok(node: str) -> bool:
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2", node, "true"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        return proc.returncode == 0
    except Exception:
        return False

# =============================
# DISPATCH LOOP
# =============================

async def dispatch_loop():
    while True:
        await asyncio.sleep(0.002)

        jobs = []

        while not cfg.JOB_QUEUE.empty():
            jobs.append(await cfg.JOB_QUEUE.get())
            if len(jobs) >= 32:
                break

        if not jobs:
            jobs.append(await cfg.JOB_QUEUE.get())

        for job_id, payload, fut in jobs:

            cfg.WAITING_FOR_NODE += 1
            await broadcast_status()

            node = await cfg.AVAILABLE_NODES.get()

            await update_shared_state(node_updates={node: "in-use"})

            cfg.WAITING_FOR_NODE -= 1
            cfg.IN_FLIGHT[node] += 1
            await broadcast_status()

            async def _do(job_id=job_id, node=node, payload=payload, fut=fut):
                try:
                    meta = JOB_META.get(job_id, {})
                    is_stream = meta.get("stream", False)

                    if is_stream:
                        stream_q = STREAM_QUEUES[job_id]
                        result = await run_on_node_stream(node, payload, stream_q)
                    else:
                        result = await run_on_node(node, payload)

                    if not fut.cancelled():
                        fut.set_result((node, result))
                except Exception as e:
                    print(f"[DISPATCH] job failed on {node}: {e}")
                    if not fut.cancelled():
                        fut.set_exception(e)
                finally:
                    cfg.IN_FLIGHT[node] -= 1
                    await update_shared_state(node_updates={node: "free"})
                    cfg.JOB_QUEUE.task_done()
                    if NODE_HEALTHY.get(node, True):
                        cfg.AVAILABLE_NODES.put_nowait(node)
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

    await cfg.JOB_QUEUE.put((job_id, prompt, fut))
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
    stream = bool(data.get("stream", False))

    if not prompt and not messages:
        return {"ok": False, "error": "Empty input"}

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    job_id = data.get("job_id") or "job"

    ahead = cfg.JOB_QUEUE.qsize() + cfg.WAITING_FOR_NODE + sum(cfg.IN_FLIGHT.values())
    payload = messages if messages else prompt

    await cfg.JOB_QUEUE.put((job_id, payload, fut))
    cfg.PENDING[job_id] = fut
    JOB_META[job_id] = {"stream": stream}

    if stream:
        STREAM_QUEUES[job_id] = asyncio.Queue()
        STREAM_DONE[job_id] = False

    await broadcast_status()

    return {"ok": True, "job_id": job_id, "ahead": ahead, "stream": stream}


@app.get("/wait/{job_id}")
async def wait(job_id: str):
    fut = cfg.PENDING.get(job_id)

    if fut is None:
        return {"ok": False, "error": "Unknown job_id"}

    try:
        node, val = await asyncio.wait_for(fut, timeout=180)
        return {
            "ok": True,
            "node": node,
            "line": val["text"],
            "metrics": val["metrics"]
        }
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Timed out waiting in queue/processing"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        cfg.PENDING.pop(job_id, None)


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
    batch_size_raw = data.get("batch_size")
    batch_size = int(batch_size_raw) if batch_size_raw is not None else 1

    if model not in cfg.AVAILABLE_MODELS:
        return {"ok": False, "error": "Invalid model"}

    await update_shared_state(model=model, batch_size=batch_size)

    if model not in cfg.AVAILABLE_MODELS:
        return {"ok": False, "error": "Invalid model"}

    loop = asyncio.get_running_loop()
    results = {}
    errors = {}

    healthy_nodes = [node for node in cfg.NODE_POOL if NODE_HEALTHY.get(node, True)]

    if not healthy_nodes:
        return {"ok": False, "error": "No healthy nodes available"}

    await update_shared_state(
        model=model,
        batch_size=batch_size,
        node_updates={node: "startup" for node in healthy_nodes}
    )

    tasks = {
        node: loop.run_in_executor(cfg.EXECUTOR, _start_vllm_node, node, model, batch_size)
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

    # mark skipped unhealthy nodes explicitly
    for node in cfg.NODE_POOL:
        await update_shared_state(node_updates={node: "startup"})
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
    while True:
        try:
            cfg.AVAILABLE_NODES.get_nowait()
        except asyncio.QueueEmpty:
            break

    for node in healthy_nodes:
        for _ in range(NODE_CONCURRENCY):
            cfg.AVAILABLE_NODES.put_nowait(node)

    await update_shared_state(
        model=model,
        batch_size=batch_size,
        node_updates={node: "free" for node in healthy_nodes}
    )

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

    # Launch all stop operations in parallel
    tasks = {
        node: loop.run_in_executor(cfg.EXECUTOR, _stop_vllm_node, node)
        for node in cfg.NODE_POOL
    }

    completed = await asyncio.gather(*tasks.values())

    node_updates = {}

    for node, (ok, err) in zip(tasks.keys(), completed):
        results[node] = ok
        if err:
            errors[node] = err

        if ok:
            started_any = True
            node_updates[node] = "free"
        else:
            NODE_HEALTHY[node] = False
            node_updates[node] = "offline"

    for node in cfg.NODE_POOL:
        if node not in results:
            results[node] = False
            errors[node] = "Skipped: node unhealthy/unreachable"
            node_updates[node] = "offline"

    CURRENT_MODEL = None

    await update_shared_state(
        model=None,
        node_updates={node: "offline" for node in cfg.NODE_POOL}
    )

    await broadcast_status()

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
        node: loop.run_in_executor(cfg.EXECUTOR, _check_vllm_node, node)
        for node in cfg.NODE_POOL
    }

    results = await asyncio.gather(*tasks.values())
    node_status = dict(zip(tasks.keys(), results))

    # Determine if at least one node is alive
    model_active = any(node_status.values())

    return {
        "model": CURRENT_MODEL if model_active else None,
        "nodes": node_status
    }

@app.on_event("shutdown")
async def shutdown_event():
    print("Backend shutting down. Stopping vLLM cluster...")

    loop = asyncio.get_running_loop()

    for node in cfg.NODE_POOL:
        try:
            await loop.run_in_executor(cfg.EXECUTOR, _stop_vllm_node, node)
            print(f"Stopped vLLM on {node}")
        except Exception as e:
            print(f"Failed to stop vLLM on {node}: {e}")

    print("Cluster shutdown complete.")


# =============================
# SAVE SINGLE NODE METRICS
# =============================

@app.post("/api/save-single-node-metrics")
async def save_single_node_metrics(request: Request):
    try:
        metrics = await request.json()

        output_dir = os.path.join("output", "single-node")
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"single_node_test_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)

        return {
            "ok": True,
            "path": filepath
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }


# =============================
# Spam 50 
# =============================
@app.post("/spam50")
async def spam50():
    loop = asyncio.get_running_loop()

    job_ids = []
    ahead_before = cfg.JOB_QUEUE.qsize() + cfg.WAITING_FOR_NODE + sum(cfg.IN_FLIGHT.values())

    for i, p in enumerate(cfg.SPAM_PROMPTS_50, start=1):
        fut = loop.create_future()
        job_id = f"spam-{uuid.uuid4().hex}"

        cfg.PENDING[job_id] = fut

        # enable streaming
        JOB_META[job_id] = {"stream": True}
        STREAM_QUEUES[job_id] = asyncio.Queue()
        STREAM_DONE[job_id] = False

        await cfg.JOB_QUEUE.put((job_id, p, fut))
        job_ids.append(job_id)

    await broadcast_status()

    return {
        "ok": True,
        "enqueued": 50,
        "ahead_before": ahead_before,
        "job_ids": job_ids,
    }
