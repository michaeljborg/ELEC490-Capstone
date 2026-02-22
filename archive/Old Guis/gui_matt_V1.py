from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import subprocess
import time
import shlex
import json
import base64
import node_interface_ip

app = FastAPI()

# ---- CONFIG ----
PATH_TO_SCRIPT = "/home/cluster/ELEC490-Capstone"
NODES = ["node2", "node3", "node4", "node5"]
# ----------------

EXECUTOR = ThreadPoolExecutor(max_workers=32)
LOCKS = {node: asyncio.Lock() for node in NODES}

@app.get("/", response_class=HTMLResponse)
def home():
    # Build buttons dynamically from NODES
    relay_buttons = "\n".join(
        f"""<button onclick="sendCustomPrompt('{node}')">Query {node}</button>"""
        for node in NODES
    )

    ping_buttons = "\n".join(
        f"""<button onclick="post('/ping/{node}')">Ping {node}</button>"""
        for node in NODES
    )

    # Render a JS array for "all nodes" action
    nodes_js_array = "[" + ",".join(f"'{n}'" for n in NODES) + "]"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Cluster Control Panel</title>
      <style>
        body {{ display: flex; flex-direction: column; align-items: center; padding: 40px; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; gap: 20px; background-color: #f0f2f5; }}
        .container {{ background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 80%; max-width: 900px; }}
        .row {{ display: flex; gap: 10px; margin-bottom: 15px; flex-wrap: wrap; justify-content: center; }}
        input[type="text"] {{ flex-grow: 1; padding: 12px; border: 2px solid #ddd; border-radius: 8px; font-size: 16px; }}
        button {{ font-size: 14px; padding: 10px 20px; cursor: pointer; border-radius: 8px; border: none; background-color: #007bff; color: white; transition: background 0.2s; }}
        button:hover {{ background-color: #0056b3; }}
        #output {{ width: 100%; height: 400px; border: 1px solid #ccc; padding: 15px; overflow-y: auto; font-family: 'Courier New', monospace; white-space: pre-wrap; background: #2b2b2b; color: #a9b7c6; border-radius: 8px; }}
        h2 {{ margin-top: 0; color: #333; }}
      </style>

      <script>
        const NODES = {nodes_js_array};

        function appendLine(line) {{
          const box = document.getElementById("output");
          const timestamp = new Date().toLocaleTimeString();
          box.textContent += `[${{timestamp}}] ${{line}}\\n`;
          box.scrollTop = box.scrollHeight;
        }}

        async function post(path) {{
          try {{
            const res = await fetch(path, {{ method: "POST" }});
            const data = await res.json();
            if (data.ok) appendLine(data.line);
            else appendLine("Error: " + data.error);
          }} catch (e) {{ appendLine("Error: " + e); }}
        }}

        async function sendCustomPrompt(node) {{
          const promptInput = document.getElementById("userPrompt");
          const prompt = promptInput.value;
          
          if (!prompt) {{
            alert("Please enter a prompt first!");
            return;
          }}

          appendLine(`--- Sending prompt to ${{node}} ---`);
          
          try {{
            const response = await fetch(`/relay/${{node}}`, {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ prompt: prompt }})
            }});
            const data = await response.json();
            if (data.ok) appendLine(`${{node.toUpperCase()}} Response: ${{data.line}}`);
            else appendLine(`Error from ${{node}}: ${{data.error}}`);
          }} catch (e) {{
            appendLine("Fetch Error: " + e);
          }}
        }}
      </script>
    </head>
    <body>
      <div class="container">
        <h2>LLM Cluster Interface</h2>
        
        <div class="row">
          <input type="text" id="userPrompt" placeholder="Type your prompt here (e.g., 'What is machine learning?')">
        </div>

        <div class="row">
          <strong>Send to:</strong> {relay_buttons}
        </div>

        <hr>

        <div class="row">
          <button style="background-color: #28a745;" onclick="post('/start_inference_all')">Start VLLM on All Nodes</button>
          {ping_buttons}
        </div>

        <div id="output"></div>
      </div>
    </body>
    </html>
    """

# --- Updated Backend Logic ---

def ssh_relay(node: str, prompt: str) -> str:
    # Use the payload passed from the GUI instead of the hardcoded recipe
    payload = {
        "ip": node_interface_ip.NODES[node], 
        "model": "Qwen/Qwen2.5-1.5B-Instruct",
        "prompt": prompt,
    }

    b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    # This runs the query using your existing node_interface_ip logic on the remote node
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
        timeout=120, # Increased timeout for LLM generation
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "SSH command failed")
    return proc.stdout.strip()

@app.post("/relay/{node}")
async def relay(node: str, request: Request):
    if node not in NODES:
        return {"ok": False, "error": f"Unknown node: {node}"}
    
    data = await request.json()
    user_prompt = data.get("prompt", "")

    async with LOCKS[node]: # Ensures node handles one request at a time
        try:
            # We run the heavy SSH/Inference task in a thread pool to keep GUI responsive
            value = await run_in_pool(ssh_relay, node, user_prompt)
            return {"ok": True, "line": value}
        except Exception as e:
            return {"ok": False, "error": str(e)}

# --- Existing Utility Functions ---

async def run_in_pool(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, lambda: fn(*args))

@app.post("/ping/{node}")
async def ping(node: str):
    if node not in NODES: return {"ok": False, "error": "Unknown node"}
    try:
        start_time = time.time()
        proc = subprocess.run(["ping", "-c", "1", "-W", "1", node], capture_output=True, text=True)
        ms = int((time.time() - start_time) * 1000)
        return {"ok": True, "line": f"Ping {node}: {'OK' if proc.returncode==0 else 'FAIL'} ({ms}ms)"}
    except Exception as e: return {"ok": False, "error": str(e)}

@app.post("/start_inference_all")
async def start_inference_all():
    async def start_and_wait(n: str):
        try:
            # 1. Start the tmux session
            await run_in_pool(node_interface_ip.start, n)
            # 2. Wait for the "Application startup complete" state
            await run_in_pool(node_interface_ip.wait_for_ready, n)
            return (n, True, "Ready to Query")
        except Exception as e: 
            return (n, False, str(e))

    results = await asyncio.gather(*(start_and_wait(n) for n in NODES))
    lines = [f"{n}: {msg}" for n, ok, msg in results]
    return {"ok": True, "line": "Cluster Start Results:\\n" + "\\n".join(lines)}