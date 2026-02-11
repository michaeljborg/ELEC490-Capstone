from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import subprocess
import time

app = FastAPI()

# ---- CONFIG ----
PATH_TO_SCRIPT = "/home/cluster/ELEC490-Capstone"  # directory on node2/node3 containing script.py
NODES = ["node2", "node3", "node4", "node5"]
# ----------------

EXECUTOR = ThreadPoolExecutor(max_workers=32)

# Per-node lock => only one task runs at a time per node (queue semantics)
LOCKS = {node: asyncio.Lock() for node in NODES}


@app.get("/", response_class=HTMLResponse)
def home():
    # Build buttons dynamically from NODES
    relay_buttons = "\n".join(
        f"""<button onclick="post('/relay/{node}')">Run relay() on {node}</button>"""
        for node in NODES
    )

    ping_buttons = "\n".join(
        f"""<button onclick="post('/ping/{node}')">Ping {node}</button>"""
        for node in NODES
    )

    queue_buttons = "\n".join(
        f"""<button onclick="streamQueue('{node}', 5)">Queue 5 relays ({node})</button>"""
        for node in NODES
    )

    # Render a JS array for "all nodes" action
    nodes_js_array = "[" + ",".join(f"'{n}'" for n in NODES) + "]"

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
          width: 80%;
          height: 420px;
          border: 1px solid #ccc;
          padding: 10px;
          overflow-y: auto;
          font-family: monospace;
          white-space: pre-wrap;
          background: #f9f9f9;
        }}
      </style>

      <script>
        const NODES = {nodes_js_array};

        function appendLine(line) {{
          const box = document.getElementById("output");
          box.textContent += line + "\\n";
          box.scrollTop = box.scrollHeight;
        }}

        async function post(path) {{
          try {{
            const res = await fetch(path, {{ method: "POST" }});
            const data = await res.json();
            if (data.ok) appendLine(data.line);
            else appendLine("Error: " + data.error);
          }} catch (e) {{
            appendLine("Error: " + e);
          }}
        }}

        // Stream queue output live (Server-Sent Events)
        function streamQueue(node, n) {{
          appendLine("--- starting stream for " + node + " (n=" + n + ") ---");
          const es = new EventSource("/queue_stream/" + node + "?n=" + n);

          es.onmessage = (ev) => {{
            appendLine(ev.data);

            // Auto-close once done/aborted
            if (ev.data.indexOf("--- Finished queue on " + node + " ---") !== -1 ||
                ev.data.indexOf("--- Aborted on " + node + ":") !== -1 ||
                ev.data.indexOf("Unknown node:") !== -1 ||
                ev.data.indexOf("n must be between") !== -1) {{
              es.close();
              appendLine("--- stream closed for " + node + " ---");
            }}
          }};

          es.onerror = () => {{
            appendLine("(stream error on " + node + ")");
            es.close();
          }};
        }}

        // NEW: queue 5 relays on every node (each node runs sequentially due to server lock)
        function streamQueueAll(n=5) {{
          appendLine("=== Queueing " + n + " relays on ALL nodes: " + NODES.join(", ") + " ===");
          for (const node of NODES) {{
            streamQueue(node, n);
          }}
        }}
      </script>
    </head>

    <body>
      <div class="row">
        {relay_buttons}
      </div>

      <div class="row">
        {queue_buttons}
      </div>

      <div class="row">
        <button onclick="streamQueueAll(5)">Queue 5 relays on ALL nodes</button>
      </div>

      <div class="row">
        {ping_buttons}
      </div>

      <div id="output"></div>
    </body>
    </html>
    """


def ssh_relay(node: str) -> str:
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

    async with LOCKS[node]:
        try:
            value = await run_in_pool(ssh_relay, node)
            print(f"{node} relay returned: {value}")
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


@app.get("/queue_stream/{node}")
async def queue_stream(node: str, n: int = 5):
    def sse(text: str) -> str:
        return f"data: {text}\n\n"

    if node not in NODES:
        async def bad_node():
            yield sse(f"Unknown node: {node}")
        return StreamingResponse(bad_node(), media_type="text/event-stream")

    if n < 1 or n > 100:
        async def bad_n():
            yield sse("n must be between 1 and 100")
        return StreamingResponse(bad_n(), media_type="text/event-stream")

    async def event_gen():
        async with LOCKS[node]:
            yield sse(f"--- Queueing {n} relays on {node} ---")
            try:
                for i in range(1, n + 1):
                    value = await run_in_pool(ssh_relay, node)
                    yield sse(f"{node.capitalize()} [{i}/{n}]: {value}")
                yield sse(f"--- Finished queue on {node} ---")
            except Exception as e:
                yield sse(f"--- Aborted on {node}: {e} ---")

    return StreamingResponse(event_gen(), media_type="text/event-stream")
