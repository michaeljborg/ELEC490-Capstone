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

    raw_name = platform.node().split('.')[0]

    if "Group15Cluster-" in raw_name:
        formatted_name = "node" + raw_name.split("-")[1]
    else:
        formatted_name = raw_name
        
    data = {
            "node_name": formatted_name
        }
    
    try:
        result = subprocess.check_output([
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits"
        ]).decode("utf-8").strip()
        
        util, mem_used, mem_total, temp, power = result.split(", ")
        data["gpu_utilization_percent"] = float(util)
        data["gpu_memory_used_mb"] = float(mem_used)
        data["gpu_memory_total_mb"] = float(mem_total)
        data["temperature"] = float(temp)
        data["power_watts"] = float(power)
    except Exception:
        pass  

    return data

if __name__ == "__main__":
    print(f"Starting monitor agent. Attempting to send to {HEADNODE_IP}:{PORT}")
    while True:
        metrics = collect_metrics()
        print(f"Collected metrics for {metrics.get('node_name')}. CPU: {metrics.get('cpu_percent')}%")
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect((HEADNODE_IP, PORT))
                s.sendall(json.dumps(metrics).encode("utf-8"))
                print("Data sent successfully!")
        except Exception as e:
            print(f"Connection error: {e}")
            
        time.sleep(INTERVAL)