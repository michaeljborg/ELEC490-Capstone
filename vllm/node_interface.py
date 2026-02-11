import subprocess
import requests

#dictionary to make referancing nodes easier
NODES = {
    "node1": "192.168.50.1",
    "node2": "192.168.50.2",
    "node3": "192.168.50.3",
    "node4": "192.168.50.4",
    "node5": "192.168.50.5",
    "node6": "192.168.50.6",
}


class VLLMNode:
    def __init__(
        self,
        hostname: str,
        ip: str,
        model: str = "Qwen/Qwen2.5-1.5B-Instruct",
        port: int = 8000,
        venv_path: str = "~/vllm-venv",
        tmux_session: str = "vllm",
        max_num_seqs: int = 32,
        max_tokens: int = 200,
        temperature: float = 0.7,
    ):
        self.hostname = hostname  
        self.ip = ip               
        self.model = model
        self.port = port
        self.venv_path = venv_path
        self.tmux_session = tmux_session
        self.max_num_seqs = max_num_seqs
        self.max_tokens = max_tokens
        self.temperature = temperature

    def start(self): 
        #kills past tmux sessions on node- then opens a new session and starts vllm server
        remote_cmd = f"""
        tmux kill-session -t {self.tmux_session} 2>/dev/null || true && \
        tmux new-session -d -s {self.tmux_session} '
            source {self.venv_path}/bin/activate && \
            vllm serve {self.model} \
                --host 0.0.0.0 \
                --port {self.port} \
                --max-num-seqs {self.max_num_seqs}
        '
        """

        print(f"Starting vLLM on {self.hostname}...")
        result = subprocess.run(
            ["ssh", self.hostname, remote_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to start vLLM on {self.hostname}:\n{result.stderr}")

        print(f"vLLM started on {self.hostname} (tmux: {self.tmux_session})")

    def query(self, prompt, timeout=60):
        url = f"http://{self.ip}:{self.port}/v1/chat/completions"

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]