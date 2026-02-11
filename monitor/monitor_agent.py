import socket
import json
import time
import subprocess
import psutil
import platform

HEADNODE_IP = "192.168.50.1"  # headnode IP
PORT = 5000
INTERVAL = 1  # seconds


def get_gpu_metrics():
    try:
        result = subprocess.check_output([
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits"
        ]).decode("utf-8").strip()

        util, mem_used, mem_total, temp, power = result.split(", ")

        return {
            "gpu_utilization_percent": float(util),
            "gpu_memory_used_mb": float(mem_used),
            "gpu_memory_total_mb": float(mem_total),
            "gpu_temperature_c": float(temp),
            "gpu_power_watts": float(power)
        }

    except Exception as e:
        return {"gpu_error": str(e)}


def collect_metrics():
    return {
        "node_name": platform.node(),
        "timestamp": time.time(),
        "cpu_percent": psutil.cpu_percent(),
        "ram_percent": psutil.virtual_memory().percent,
        "net_bytes_sent": psutil.net_io_counters().bytes_sent,
        "net_bytes_recv": psutil.net_io_counters().bytes_recv,
        **get_gpu_metrics()
    }


def send_metrics(data):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((HEADNODE_IP, PORT))
            s.sendall(json.dumps(data).encode("utf-8"))
    except Exception:
        pass  # fail silently if headnode unreachable


if __name__ == "__main__":
    while True:
        metrics = collect_metrics()
        send_metrics(metrics)
        time.sleep(INTERVAL)
