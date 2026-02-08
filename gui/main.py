from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import subprocess
import time

app = FastAPI()

# ---- CONFIG ----
PATH_TO_SCRIPT = "/home/cluster/ELEC490-Capstone"  # directory on node2/node3 containing script.py
NODES = ["node2", "node3"]
# ----------------

EXECUTOR = ThreadPoolExecutor(max_workers=32)  # tune later


@app.get("/", response_class=HTMLResponse)
def home():
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Relay GUI</title>
      <style>
        body {{
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          height: 100vh;
          font-family: Arial, sans-serif;
          gap: 12px;
        }}
        .row {{
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          justify-content: center;
        }}
        button {{
          font-size: 18px;
          padding: 12px 18px;
          cursor: pointer;
        }}
        #output {{
          width: 75%;
          height: 320px;
          border: 1px solid #ccc;
          padding: 10px;
          overflow-y: auto;
          font-family: monospace;
          white-space: pre-wrap;
          background: #f9f9f9;
        }}
      </style>
      <script>
        function appendLine(line) {{
          const box = document.getElementById("output");
          box.textContent += line + "\\n";
          box.scrollTop = box.scrollHeight;
        }}

        async function post(path) {{
          try {{
            const res = await fetch(path, {{ method: "POST" }});
            const data = await res.json();

            if (data.ok) {{
              appendLine(data.line);
            }} else {{
              appendLine("Error: " + data.error);
            }}
          }} catch (e) {{
            appendLine("Error: " + e);
          }}
        }}
      </script>
    </head>
    <body>
      <div class="row">
        <button onclick="post('/relay/node2')">Run relay() on node2</button>
        <button onclick="post('/relay/node3')">Run relay() on node3</button>
      </div>

      <div class="row">
        <button onclick="post('/ping/node2')">Ping node2</button>
        <button onclick="post('/ping/node3')">Ping node3</button>
      </div>

      <div id="output"></div>
    </body>
    </html>
    """


def ssh_relay(node: str) -> str:
    # Runs python on the remote node and prints relay() result
    remote_cmd = f'cd "{PATH_TO_SCRIPT}" && python3 -c "import script; print(script.relay())"'
    proc = subprocess.run(
        ["ssh", node, remote_cmd],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "SSH command failed")
    return proc.stdout.strip()


def ping_node(node: str) -> str:
    # ICMP ping from node1 -> node
    start = time.time()
    proc = subprocess.run(
        ["ping", "-c", "1", "-W", "1", node],
        capture_output=True,
        text=True,
        timeout=3,
    )
    ms = int((time.time() - start) * 1000)
    if proc.returncode == 0:
        return f"Ping {node}: OK ({ms} ms)"
    return f"Ping {node}: FAIL"


async def run_in_pool(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, lambda: fn(*args))


@app.post("/relay/{node}")
async def relay(node: str):
    if node not in NODES:
        return {"ok": False, "error": f"Unknown node: {node}"}
    try:
        value = await run_in_pool(ssh_relay, node)
        return {"ok": True, "line": f"{node.capitalize()}: {value}"}
    except Exception as e:
        return {"ok": False, "error": f"{node}: {e}"}


@app.post("/ping/{node}")
async def ping(node: str):
    if node not in NODES:
        return {"ok": False, "error": f"Unknown node: {node}"}
    try:
        line = await run_in_pool(ping_node, node)
        return {"ok": True, "line": line}
    except Exception as e:
        return {"ok": False, "error": f"{node}: {e}"}
