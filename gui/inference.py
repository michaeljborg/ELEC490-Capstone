from node_interface_ip import start, query, NODES
import time


start(hostname="node2", model="Qwen/Qwen2.5-1.5B-Instruct")

print("Waiting for model to load...")
time.sleep(30) 

response = query(
    ip=NODES["node2"], 
    model="Qwen/Qwen2.5-1.5B-Instruct", 
    prompt="Explain what a GPU is in one sentence."
)

print("\nMODEL RESPONSE:\n", response)