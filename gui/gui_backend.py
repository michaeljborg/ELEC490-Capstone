from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import subprocess
import base64
import json
import node_interface_ip
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory=".")
PENDING: dict[str, asyncio.Future] = {}


# ---- CONFIG ----
PATH_TO_SCRIPT = "/home/cluster/ELEC490-Capstone"
NODE_POOL = ["node2", "node3", "node4"]
# ----------------

WAITING_FOR_NODE = 0

EXECUTOR = ThreadPoolExecutor(max_workers=32)
LOCKS = {node: asyncio.Lock() for node in NODE_POOL}

# All connected websockets (no longer “assigned” to a node)
ACTIVE_SESSIONS = set()

# Global FIFO job queue: each item is (job_id, prompt, future)
JOB_QUEUE: asyncio.Queue = asyncio.Queue()

# Node availability tokens (free nodes live here)
AVAILABLE_NODES: asyncio.Queue = asyncio.Queue()

IN_FLIGHT = {node: 0 for node in NODE_POOL}

DISPATCHER_TASK: asyncio.Task | None = None


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

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # No assigned_node anymore
    return templates.TemplateResponse(
        "gui.html",
        {
            "request": request,
            "node_pool": NODE_POOL,
            "node_pool_json": json.dumps(NODE_POOL),
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
        f'cd "{PATH_TO_SCRIPT}" && '
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
    ahead = JOB_QUEUE.qsize() + WAITING_FOR_NODE

    await JOB_QUEUE.put((job_id, prompt, fut))
    await broadcast_status()

    node, val = await fut
    try:
        return {"ok": True, "node": node, "line": val}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Timed out waiting in queue/processing"}
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

    # Optional: how many were already waiting (simple snapshot)
    ahead_before = JOB_QUEUE.qsize()

    for i, p in enumerate(SPAM_PROMPTS_50, start=1):
        fut = loop.create_future()  # we won't await it; it's fine
        job_id = f"spam-{i}"
        prompt = f"SPAMTEST {i}/50\n{p}"
        await JOB_QUEUE.put((job_id, prompt, fut))

    await broadcast_status()
    return {"ok": True, "enqueued": 50, "ahead_before": ahead_before}