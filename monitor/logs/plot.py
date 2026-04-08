import json
import matplotlib.pyplot as plt
import os

# ====== PATH ======
FILE_PATH = os.path.join(os.path.dirname(__file__), "plot_data.json")

# ====== STORAGE ======
time = []
temperature = []
power = []

# ====== LOAD DATA ======
with open(FILE_PATH, "r") as f:
    try:
        # Case 1: JSON array
        data = json.load(f)
    except:
        # Case 2: line-by-line JSON
        f.seek(0)
        data = [json.loads(line) for line in f if line.strip()]

# ====== PARSE ======
for i, entry in enumerate(data):
    if "temperature" in entry and "power_watts" in entry:
        time.append(i)
        temperature.append(entry["temperature"])
        power.append(entry["power_watts"])

# ====== DEBUG ======
print(f"Loaded {len(time)} data points")

if len(time) == 0:
    print("ERROR: No valid data found in plot_data.json")
    exit()

# ====== SAVE PLOTS INSTEAD OF SHOW ======

# Temperature
plt.figure()
plt.plot(time, temperature)
plt.xlabel("Time (seconds)")
plt.ylabel("Temperature (°C)")
plt.title("Temperature vs Time")
plt.grid()
plt.savefig("temperature.png")

# Power
plt.figure()
plt.plot(time, power)
plt.xlabel("Time (seconds)")
plt.ylabel("Power (Watts)")
plt.title("Power vs Time")
plt.grid()
plt.savefig("power.png")

print("Saved: temperature.png, power.png")