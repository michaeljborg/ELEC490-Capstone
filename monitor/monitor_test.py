import subprocess
import psutil
import time
import json
import platform
from datetime import datetime


DURATION_SECONDS = 60
INTERVAL = 1
OUTPUT_FILE = "monitor_output.txt"


def get_gpu_metrics():
    try:
        result = subprocess.check_output([
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits"
        ]).decode("utf-8").strip()

        util, mem_used, mem_total, temp, power = result.split(", ")

        return {
            "gpu_util_percent": float(util),
            "gpu_mem_used_mb": float(mem_used),
            "gpu_mem_total_mb": float(mem_total),
            "gpu_temp_c": float(temp),
            "gpu_power_watts": float(power)
        }

    except Exception as e:
        return {"gpu_error": str(e)}


def collect_metrics():
    net = psutil.net_io_counters()

    return {
        "timestamp": datetime.now().isoformat(),
        "node_name": platform.node(),
        "cpu_percent": psutil.cpu_percent(),
        "ram_percent": psutil.virtual_memory().percent,
        "net_bytes_sent": net.bytes_sent,
        "net_bytes_recv": net.bytes_recv,
        **get_gpu_metrics()
    }


def main():
    print(f"Starting monitoring for {DURATION_SECONDS} seconds...")
    print(f"Saving to {OUTPUT_FILE}")

    start_time = time.time()
    results = []

    while time.time() - start_time < DURATION_SECONDS:
        metrics = collect_metrics()
        results.append(metrics)
        print(metrics)
        time.sleep(INTERVAL)

    # Proper indentation here
    with open(OUTPUT_FILE, "w") as f:
        for entry in results:
            f.write(json.dumps(entry) + "\n")

    print("Monitoring complete.")


if __name__ == "__main__":
    main()
