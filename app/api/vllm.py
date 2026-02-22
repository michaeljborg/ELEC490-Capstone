# api/vllm.py

from fastapi import APIRouter
import asyncio
import subprocess
from nodes import node_interface_ip

from core.config import NODE_POOL, EXECUTOR

router = APIRouter(prefix="/api/vllm")

def _start_vllm_node(node: str):
    try:
        node_interface_ip.start(node)
        node_interface_ip.wait_for_ready(node, timeout=120)
        return True, None
    except Exception as e:
        return False, str(e)

def _stop_vllm_node(node: str):
    try:
        remote_cmd = "tmux kill-session -t vllm 2>/dev/null || true"
        proc = subprocess.run(
            ["ssh", node, remote_cmd],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode == 0, None
    except Exception as e:
        return False, str(e)

@router.post("/start")
async def start_vllm_cluster():
    loop = asyncio.get_running_loop()
    results = {}
    errors = {}

    for node in NODE_POOL:
        ok, err = await loop.run_in_executor(EXECUTOR, _start_vllm_node, node)
        results[node] = ok
        if err:
            errors[node] = err

    return {"ok": True, "nodes": results, "errors": errors}

@router.post("/stop")
async def stop_vllm_cluster():
    loop = asyncio.get_running_loop()
    results = {}
    errors = {}

    for node in NODE_POOL:
        ok, err = await loop.run_in_executor(EXECUTOR, _stop_vllm_node, node)
        results[node] = ok
        if err:
            errors[node] = err

    return {"ok": True, "nodes": results, "errors": errors}