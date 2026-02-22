from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import subprocess
import time
import base64
import json
import node_interface_ip

app = FastAPI()

# ---- CONFIG ----
PATH_TO_SCRIPT = "/home/cluster/ELEC490-Capstone"
# Available worker nodes in the pool
NODE_POOL = ["node2", "node3", "node4", "node5", "node6"]
# ----------------

EXECUTOR = ThreadPoolExecutor(max_workers=32)
LOCKS = {node: asyncio.Lock() for node in NODE_POOL}

# This dictionary will store: { "User_IP": "Assigned_Node" }
USER_ASSIGNMENTS = {}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user_ip = request.client.host
    
    # Automatically assign a node if this is a new user
    if user_ip not in USER_ASSIGNMENTS:
        # Calculate which node to give them (simple round-robin or sequence)
        assigned_index = len(USER_ASSIGNMENTS) % len(NODE_POOL)
        USER_ASSIGNMENTS[user_ip] = NODE_POOL[assigned_index]
    
    assigned_node = USER_ASSIGNMENTS[user_ip]

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Cluster Control Panel</title>
      <style>
        body {{ display: flex; flex-direction: column; align-items: center; padding: 40px; font-family: sans-serif; background-color: #f0f2f5; }}
        .container {{ background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 80%; max-width: 800px; }}
        .status-card {{ background: #e7f3ff; padding: 15px; border-radius: 8px; margin-bottom: 20px; border-left: 5px solid #007bff; }}
        .row {{ display: flex; gap: 10px; margin-bottom: 15px; }}
        input[type="text"] {{ flex-grow: 1; padding: 12px; border: 2px solid #ddd; border-radius: 8px; font-size: 16px; }}
        button {{ font-size: 16px; padding: 12px 24px; cursor: pointer; border-radius: 8px; border: none; background-color: #007bff; color: white; }}
        #output {{ width: 100%; height: 350px; border: 1px solid #ccc; padding: 15px; overflow-y: auto; font-family: monospace; background: #2b2b2b; color: #a9b7c6; border-radius: 8px; }}
      </style>

      <script>
        const ASSIGNED_NODE = "{assigned_node}";

        function appendLine(line) {{
          const box = document.getElementById("output");
          box.textContent += `[${{new Date().toLocaleTimeString()}}] ${{line}}\\n`;
          box.scrollTop = box.scrollHeight;
        }}

        async function sendQuery() {{
          const prompt = document.getElementById("userPrompt").value;
          if (!prompt) return alert("Please enter a prompt.");

          appendLine(`Sending to my assigned node (${{ASSIGNED_NODE}})...`);
          
          try {{
            const res = await fetch(`/relay/${{ASSIGNED_NODE}}`, {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ prompt: prompt }})
            }});
            const data = await res.json();
            if (data.ok) appendLine(`RESPONSE: ${{data.line}}`);
            else appendLine(`ERROR: ${{data.error}}`);
          } catch (e) {{ appendLine("Connection Error: " + e); }}
        }}
      </script>
    </head>
    <body>
      <div class="container">
        <h2>LLM Cluster Interface</h2>
        
        <div class="status-card">
            <strong>Welcome!</strong> Your session is active.<br>
            <strong>Your Assigned Compute Node:</strong> <span style="color: #007bff;">{assigned_node.upper()}</span>
        </div>

        <div class="row">
          <input type="text" id="userPrompt" placeholder="What is your question?">
          <button onclick="sendQuery()">Run Query</button>
        </div>

        <div id="output"></div>
        
        <br>
        <button style="background: #6c757d; font-size: 12px;" onclick="fetch('/start_inference_all', {{method:'POST'}})">
            Admin: Boot Cluster
        </button>
      </div>
    </body>
    </html>
    """

# --- Backend Logic (Remains mostly the same but handles the prompt) ---

def ssh_relay(node: str, prompt: str) -> str:
    payload = {{
        "ip": node_interface_ip.NODES[node], 
        "model": "Qwen/Qwen2.5-1.5B-Instruct",
        "prompt": prompt,
    }}
    b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    remote_cmd = (
        f'cd "{PATH_TO_SCRIPT}" && '
        f'python3 -c "import base64, json, node_interface_ip; '
        f'd=json.loads(base64.b64decode(\'{b64}\').decode(\'utf-8\')); '
        f'print(node_interface_ip.query(**d))"'
    )

    proc = subprocess.run(["ssh", node, remote_cmd], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0: raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()

@app.post("/relay/{{node}}")
async def relay(node: str, request: Request):
    data = await request.json()
    user_prompt = data.get("prompt", "")
    async with LOCKS[node]:
        try:
            value = await run_in_pool(ssh_relay, node, user_prompt)
            return {{"ok": True, "line": value}}
        except Exception as e: return {{"ok": False, "error": str(e)}}

async def run_in_pool(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, lambda: fn(*args))

@app.post("/start_inference_all")
async def start_inference_all():
    # Helper to start nodes in parallel
    async def start_one(n: str):
        try:
            await run_in_pool(node_interface_ip.start, n)
            return True
        except: return False
    await asyncio.gather(*(start_one(n) for n in NODE_POOL))
    return {{"ok": True, "line": "All nodes received start command."}}