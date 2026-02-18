import subprocess
import requests

NODES = {
    "node1": "192.168.50.1",
    "node2": "192.168.50.2",
    "node3": "192.168.50.3",
    "node4": "192.168.50.4",
    "node5": "192.168.50.5",
    "node6": "192.168.50.6",
}

def start(hostname, model="Qwen/Qwen2.5-1.5B-Instruct"):
    tmux_session = "vllm"
    venv_path = "~/vllm-venv"
    port = 8000
    max_num_seqs = 32

    remote_cmd = f"""
    tmux kill-session -t {tmux_session} 2>/dev/null || true && \
    tmux new-session -d -s {tmux_session} '
        source {venv_path}/bin/activate && \
        vllm serve {model} \
            --host 0.0.0.0 \
            --port {port} \
            --max-num-seqs {max_num_seqs}
    '
    """

    print(f"Starting vLLM on {hostname}...")
    result = subprocess.run(
        ["ssh", hostname, remote_cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to start vLLM on {hostname}:\n{result.stderr}")

    print(f"vLLM started on {hostname} (tmux: {tmux_session})")

def wait_for_ready(node_hostname, timeout=120):
    import time
    from node_interface_ip import NODES
    
    ip = NODES[node_hostname]
    port = 8000
    url = f"http://{ip}:{port}/health"
    
    start_time = time.time()
    print(f"Waiting for vLLM on {node_hostname} to finish startup...")
    
    while time.time() - start_time < timeout:
        try:
            # A simple GET request to the health endpoint
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                print(f"vLLM on {node_hostname} is READY.")
                return True
        except requests.exceptions.ConnectionError:
            # Server hasn't opened the port yet
            pass
        
        time.sleep(5) # Check every 5 seconds
        
    raise TimeoutError(f"vLLM on {node_hostname} failed to start within {timeout}s")


def query(ip, model, prompt, timeout=60):
    port = 8000
    url = f"http://{ip}:{port}/v1/chat/completions"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "temperature": 0.7,
    }

    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
