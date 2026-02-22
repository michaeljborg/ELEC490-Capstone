# core/dispatcher.py

import asyncio
import subprocess
import base64
import json
from nodes import node_interface_ip

from core.config import (
    JOB_QUEUE,
    AVAILABLE_NODES,
    IN_FLIGHT,
    WAITING_FOR_NODE,
    ACTIVE_SESSIONS,
    EXECUTOR,
    NODE_POOL,
)

async def broadcast_status():
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
            ACTIVE_SESSIONS.discard(ws)

def ssh_relay(node: str, prompt: str) -> str:
    payload = {
        "ip": node_interface_ip.NODES[node],
        "model": "Qwen/Qwen2.5-1.5B-Instruct",
        "prompt": prompt,
    }
    b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    remote_cmd = (
        f'cd "/home/cluster/ELEC490-Capstone/gui" && '
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
        job_id, prompt, fut = await JOB_QUEUE.get()

        WAITING_FOR_NODE += 1
        await broadcast_status()

        node = await AVAILABLE_NODES.get()

        WAITING_FOR_NODE -= 1
        IN_FLIGHT[node] += 1
        await broadcast_status()

        async def _do():
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