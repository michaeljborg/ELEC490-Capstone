# api/queue.py

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
import asyncio

from core.config import (
    JOB_QUEUE,
    PENDING,
    IN_FLIGHT,
    WAITING_FOR_NODE,
    ACTIVE_SESSIONS,
    NODE_POOL,
)

from core.dispatcher import broadcast_status

router = APIRouter()


# ----------------------------
# WebSocket Status Endpoint
# ----------------------------
@router.websocket("/ws/status")
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


# ----------------------------
# Relay (direct enqueue + wait)
# ----------------------------
@router.post("/relay")
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


# ----------------------------
# Enqueue (FIFO)
# ----------------------------
@router.post("/enqueue")
async def enqueue(request: Request):
    global WAITING_FOR_NODE

    data = await request.json()
    prompt = (data.get("prompt") or "").strip()

    if not prompt:
        return {"ok": False, "error": "Empty prompt"}

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    job_id = data.get("job_id") or "job"

    # Calculate how many are ahead
    ahead = JOB_QUEUE.qsize() + WAITING_FOR_NODE + sum(IN_FLIGHT.values())

    await JOB_QUEUE.put((job_id, prompt, fut))
    PENDING[job_id] = fut

    await broadcast_status()

    return {"ok": True, "job_id": job_id, "ahead": ahead}


# ----------------------------
# Wait for Job Completion
# ----------------------------
@router.get("/wait/{job_id}")
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


# ----------------------------
# Spam 50 Prompts
# ----------------------------
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


@router.post("/spam50")
async def spam50():
    global WAITING_FOR_NODE

    loop = asyncio.get_running_loop()

    job_ids = []
    ahead_before = JOB_QUEUE.qsize() + WAITING_FOR_NODE + sum(IN_FLIGHT.values())

    for i, prompt in enumerate(SPAM_PROMPTS_50, start=1):
        fut = loop.create_future()
        job_id = f"spam-{i}-{int(loop.time()*1000)}"

        PENDING[job_id] = fut
        await JOB_QUEUE.put((job_id, prompt, fut))
        job_ids.append(job_id)

    await broadcast_status()

    return {
        "ok": True,
        "enqueued": 50,
        "ahead_before": ahead_before,
        "job_ids": job_ids,
    }