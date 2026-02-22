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
    
    # Calculate counts for each node
    node_counts = {node: list(ACTIVE_SESSIONS.values()).count(node) for node in NODE_POOL}
    
    # Send the update to every connected browser
    for websocket in ACTIVE_SESSIONS.keys():
        try:
            await websocket.send_json(node_counts)
        except:
            pass # Handle broken connections gracefully

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Initial assignment for page load
    assigned_index = len(ACTIVE_SESSIONS) % len(NODE_POOL)
    assigned_node = NODE_POOL[assigned_index]

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Cluster Control Panel</title>
      <style>
        body {{ display: flex; flex-direction: column; align-items: center; padding: 40px; font-family: sans-serif; background-color: #f0f2f5; }}
        .container {{ background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 80%; max-width: 800px; }}
        .status-card {{ background: #e7f3ff; padding: 15px; border-radius: 8px; margin-bottom: 20px; border-left: 5px solid #007bff; display: flex; justify-content: space-between; align-items: center; }}
        .occupancy-badge {{ background: #007bff; color: white; padding: 10px 15px; border-radius: 50px; font-weight: bold; font-size: 14px; }}
        .row {{ display: flex; gap: 10px; margin-bottom: 15px; }}
        input[type="text"] {{ flex-grow: 1; padding: 12px; border: 2px solid #ddd; border-radius: 8px; font-size: 16px; }}
        button {{ font-size: 16px; padding: 12px 24px; cursor: pointer; border-radius: 8px; border: none; background-color: #007bff; color: white; }}
        #output {{ width: 100%; height: 350px; border: 1px solid #ccc; padding: 15px; overflow-y: auto; font-family: monospace; background: #2b2b2b; color: #a9b7c6; border-radius: 8px; white-space: pre-wrap; }}
      </style>

      <script>
        const ASSIGNED_NODE = "{assigned_node}";
        
        // Setup WebSocket for Live Occupancy
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${{protocol}}//${{window.location.host}}/ws/occupancy/${{ASSIGNED_NODE}}`);

        ws.onmessage = (event) => {{
            const counts = JSON.parse(event.data);
            const myCount = counts[ASSIGNED_NODE] || 0;
            document.getElementById("occ-count").innerText = myCount + " User(s) on this Node";
        }};

        function appendLine(line) {{
          const box = document.getElementById("output");
          box.textContent += "[" + new Date().toLocaleTimeString() + "] " + line + "\\n";
          box.scrollTop = box.scrollHeight;
        }}

        async function sendQuery() {{
          const prompt = document.getElementById("userPrompt").value;
          if (!prompt) return alert("Please enter a prompt.");
          appendLine("Sending to: " + ASSIGNED_NODE);
          
          try {{
            const res = await fetch("/relay/" + ASSIGNED_NODE, {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify({{ "prompt": prompt }})
            }});
            const data = await res.json();
            appendLine(data.ok ? "RESPONSE: " + data.line : "ERROR: " + data.error);
          }} catch (e) {{ appendLine("FETCH ERROR: " + e.message); }}
        }}
      </script>
    </head>
    <body>
      <div class="container">
        <h2>LLM Cluster Interface</h2>
        <div class="status-card">
            <div><strong>Assigned Node:</strong> <span style="color: #007bff;">{assigned_node.upper()}</span></div>
            <div class="occupancy-badge" id="occ-count">1 User(s) on this Node</div>
        </div>
        <div class="row">
          <input type="text" id="userPrompt" placeholder="Type your prompt here...">
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

# --- Backend Logic (SSH and Relay functions remain identical to before) ---

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