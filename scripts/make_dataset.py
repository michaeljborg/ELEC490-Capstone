import requests
import json

print("Downloading...")
r = requests.get("https://raw.githubusercontent.com/tatsu-lab/stanford_alpaca/main/alpaca_data.json")
data = [item["instruction"] for item in r.json()]

with open("benchmark_prompts.json", "w") as f:
    json.dump(data, f)
print(f"Saved {len(data)} prompts!")