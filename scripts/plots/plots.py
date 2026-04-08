import os
import json
import matplotlib.pyplot as plt

BASE_DIR = "../../output/multi-node/Llama-3.2-3B"

data = []

# Load all test folders
for folder in os.listdir(BASE_DIR):
    path = os.path.join(BASE_DIR, folder)
    if not os.path.isdir(path):
        continue

    summary_path = os.path.join(path, "system_summary.json")

    if os.path.exists(summary_path):
        with open(summary_path, "r") as f:
            summary = json.load(f)
            data.append(summary)

# Sort by batch size
data = sorted(data, key=lambda x: x["batch_size"])

batch_sizes = [d["batch_size"] for d in data]
durations = [d["benchmark_duration"] for d in data]
throughput = [d["cluster_tokens_per_sec"] for d in data]
latency = [d["avg_latency"] for d in data]
ttft = [d["avg_ttft"] for d in data]

# --- SPEEDUP ---
baseline_time = durations[0]
speedup = [baseline_time / t for t in durations]

# --- EFFICIENCY ---
efficiency = [s / b for s, b in zip(speedup, batch_sizes)]

# ---------------- PLOTS ---------------- #

# 1. Completion Time
plt.figure()
plt.plot(batch_sizes, durations, marker='o')
plt.xlabel("Batch Size")
plt.ylabel("Completion Time (s)")
plt.title("Batch Size vs Completion Time")
plt.grid()
plt.savefig("completion_time.png")

# 2. Throughput
plt.figure()
plt.plot(batch_sizes, throughput, marker='o')
plt.xlabel("Batch Size")
plt.ylabel("Tokens/sec")
plt.title("Batch Size vs Throughput")
plt.grid()
plt.savefig("throughput.png")

# 3. Speedup
plt.figure()
plt.plot(batch_sizes, speedup, marker='o', label="Actual")
plt.plot(batch_sizes, batch_sizes, linestyle='--', label="Ideal")
plt.xlabel("Batch Size")
plt.ylabel("Speedup")
plt.title("Speedup vs Batch Size")
plt.legend()
plt.grid()
plt.savefig("speedup.png")

# 4. Latency
plt.figure()
plt.plot(batch_sizes, latency, marker='o')
plt.xlabel("Batch Size")
plt.ylabel("Seconds")
plt.title("Latency vs Batch Size")
plt.legend()
plt.grid()
plt.savefig("latency.png")

# 5. Efficiency
plt.figure()
plt.plot(batch_sizes, efficiency, marker='o')
plt.xlabel("Batch Size")
plt.ylabel("Efficiency (Speedup / Batch Size)")
plt.title("Parallel Efficiency")
plt.grid()
plt.savefig("efficiency.png")

plt.show()