import socket
import json
import time
import subprocess
import psutil
import platform

HEADNODE_IP = "192.168.50.1"  
PORT = 5000                  
INTERVAL = 1

def collect_metrics():
    data = {
        "node_name": platform.node().split('.')[0],
        "cpu_percent": psutil.cpu_percent(),
        "ram_percent": psutil.virtual_memory().percent
    }
    
    try:
        result = subprocess.check_output([
            "nvidia-smi",
            "--query-gpu=utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits"
        ]).decode("utf-8").strip()
        
        util, temp = result.split(", ")
        data["gpu_utilization_percent"] = float(util)
        data["temperature"] = float(temp)
    except Exception:
        pass  # Fails silently if nvidia-smi errors out

    return data

if __name__ == "__main__":
    while True:
        metrics = collect_metrics()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((HEADNODE_IP, PORT))
                s.sendall(json.dumps(metrics).encode("utf-8"))
        except Exception:
            pass  # Fails silently if headnode is unreachable
            
        time.sleep(INTERVAL)