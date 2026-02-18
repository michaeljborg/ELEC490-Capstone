from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import subprocess
import time
import base64
import json
import uuid
import node_interface_ip
from fastapi.templating import Jinja2Templates


app = FastAPI()

templates = Jinja2Templates(directory=".")

# ---- CONFIG ----
PATH_TO_SCRIPT = "/home/cluster/ELEC490-Capstone"
NODE_POOL = ["node2", "node3", "node4", "node5", "node6"]
# ----------------

EXECUTOR = ThreadPoolExecutor(max_workers=32)
LOCKS = {node: asyncio.Lock() for node in NODE_POOL}

# Tracks { websocket: assigned_node }
ACTIVE_SESSIONS = {}

async def broadcast_counts():
    """Sends the current occupancy counts to all connected users."""
    if not ACTIVE_SESSIONS:
        return
    
    # Calculate counts for every node in the pool
    node_counts = {node: list(ACTIVE_SESSIONS.values()).count(node) for node in NODE_POOL}
    
    # Push the full cluster status to every connected browser
    for websocket in ACTIVE_SESSIONS.keys():
        try:
            await websocket.send_json(node_counts)
        except:
            pass 

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    assigned_index = len(ACTIVE_SESSIONS) % len(NODE_POOL)
    assigned_node = NODE_POOL[assigned_index]

    # Instead of return f"""...""", we return the template file
    return templates.TemplateResponse("gui.html", {
            "request": request,
            "assigned_node": assigned_node,
            "node_pool": NODE_POOL,
            "node_pool_json": json.dumps(NODE_POOL)
        })

@app.websocket("/ws/occupancy/{node}")
async def websocket_endpoint(websocket: WebSocket, node: str):
    await websocket.accept()
    ACTIVE_SESSIONS[websocket] = node
    await broadcast_counts() # Update everyone that a new user joined
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except WebSocketDisconnect:
        del ACTIVE_SESSIONS[websocket]
        await broadcast_counts() # Update everyone that a user left

def ssh_relay(node: str, prompt: str) -> str:
    payload = {"ip": node_interface_ip.NODES[node], "model": "Qwen/Qwen2.5-1.5B-Instruct", "prompt": prompt}
    b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    remote_cmd = f'cd "{PATH_TO_SCRIPT}" && python3 -c "import base64, json, node_interface_ip; d=json.loads(base64.b64decode(\'{b64}\').decode(\'utf-8\')); print(node_interface_ip.query(**d))"'
    proc = subprocess.run(["ssh", node, remote_cmd], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0: raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()

@app.post("/relay/{node}")
async def relay(node: str, request: Request):
    data = await request.json()
    async with LOCKS[node]:
        loop = asyncio.get_running_loop()
        try:
            val = await loop.run_in_executor(EXECUTOR, ssh_relay, node, data.get("prompt", ""))
            return {"ok": True, "line": val}
        except Exception as e: return {"ok": False, "error": str(e)}