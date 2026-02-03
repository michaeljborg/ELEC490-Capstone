from node_interface import VLLMNode, NODES
import time

# Start server on node2
node2 = VLLMNode(hostname="node2", ip=NODES["node2"])
node2.start()
# Give it a few seconds to load
time.sleep(10)

# Send a test prompt
response = node2.query("Explain what a GPU is in one sentence.")
print("\nMODEL RESPONSE:\n", response)
