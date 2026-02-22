from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi.templating import Jinja2Templates
import subprocess
import time
import base64
import json
import uuid
import node_interface_ip

app = FastAPI()

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
    # Initial assignment logic for new page loads
    assigned_index = len(ACTIVE_SESSIONS) % len(NODE_POOL)
    assigned_node = NODE_POOL[assigned_index]

    # Generate the HTML list for the sidebar
    sidebar_items = "\n".join(
        f'<div class="node-item"><strong>{n.upper()}:</strong> <span id="count-{n}">0</span> Users</div>'
        for n in NODE_POOL
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
    <title>Cluster Control Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
    :root {{
        --bg: #0f172a;
        --panel: rgba(30, 41, 59, 0.6);
        --border: rgba(255,255,255,0.08);
        --accent: #00f5ff;
        --accent2: #7c3aed;
        --text: #e2e8f0;
        --muted: #94a3b8;
    }}

    body {{
        margin: 0;
        padding: 40px;
        font-family: 'Inter', sans-serif;
        background: radial-gradient(circle at top left, #1e293b, #0f172a 60%);
        color: var(--text);
        display: flex;
        gap: 30px;
        justify-content: center;
    }}

    .sidebar {{
        width: 240px;
        background: var(--panel);
        backdrop-filter: blur(14px);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 25px;
    }}

    .node-item {{
        padding: 10px 0;
        border-bottom: 1px solid var(--border);
        font-size: 14px;
    }}

    .main-panel {{
        width: 720px;
        background: var(--panel);
        backdrop-filter: blur(14px);
        border-radius: 18px;
        padding: 35px;
        border: 1px solid var(--border);
    }}

    .status-card {{
        background: linear-gradient(135deg, rgba(0,245,255,0.08), rgba(124,58,237,0.08));
        padding: 20px;
        border-radius: 14px;
        margin-bottom: 25px;
    }}

    .highlight {{
        color: var(--accent);
        font-weight: 600;
    }}

    .row {{
        display: flex;
        gap: 15px;
        margin-bottom: 20px;
    }}

    input[type="text"] {{
        flex-grow: 1;
        padding: 14px;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: #0f172a;
        color: var(--text);
    }}

    button {{
        padding: 14px 28px;
        border-radius: 12px;
        border: none;
        background: linear-gradient(90deg, var(--accent), var(--accent2));
        cursor: pointer;
    }}

    #output {{
        height: 380px;
        overflow-y: auto;
        background: #020617;
        border-radius: 14px;
        padding: 20px;
        font-family: monospace;
        border: 1px solid var(--border);
        white-space: pre-wrap;
        color: #38bdf8;
    }}
    </style>

    <script>
    const ASSIGNED_NODE = "{assigned_node}";
    const NODE_POOL = {json.dumps(NODE_POOL)};

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${{protocol}}//${{window.location.host}}/ws/occupancy/${{ASSIGNED_NODE}}`);

    ws.onmessage = (event) => {{
        const counts = JSON.parse(event.data);

        NODE_POOL.forEach(node => {{
            const element = document.getElementById("count-" + node);
            if (element) element.innerText = counts[node] || 0;
        }});

        const myCount = counts[ASSIGNED_NODE] || 0;
        document.getElementById("my-occ-count").innerText = myCount;
    }};

    function appendLine(line) {{
        const box = document.getElementById("output");
        const time = new Date().toLocaleTimeString();
        box.textContent += "[" + time + "] " + line + "\\n";
        box.scrollTop = box.scrollHeight;
    }}

    async function sendQuery() {{
        const prompt = document.getElementById("userPrompt").value;
        if (!prompt) return alert("Please enter a prompt.");

        appendLine("Dispatching to " + ASSIGNED_NODE + "...");

        try {{
            const res = await fetch("/relay/" + ASSIGNED_NODE, {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ "prompt": prompt }})
            }});

            const data = await res.json();
            appendLine(data.ok ? "RESPONSE: " + data.line : "ERROR: " + data.error);
        }} catch (e) {{
            appendLine("FETCH ERROR: " + e.message);
        }}
    }}
    </script>
    </head>

    <body>

    <div class="sidebar">
        <h3>Cluster Status</h3>
        {sidebar_items}
    </div>

    <div class="main-panel">
        <h2>LLM Cluster Interface</h2>

        <div class="status-card">
            <div><strong>Your Assigned Node:</strong>
                <span class="highlight">{assigned_node.upper()}</span>
            </div>
            <div>
                <strong>Active Users on Your Node:</strong>
                <span id="my-occ-count" class="highlight">1</span>
            </div>
        </div>

        <div class="row">
            <input type="text" id="userPrompt" placeholder="Enter prompt...">
            <button onclick="sendQuery()">Run Query</button>
        </div>

        <div id="output"></div>
    </div>

    </body>
    </html>
    """

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