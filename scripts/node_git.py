import subprocess

# Configuration
NODES = ["node2", "node3", "node4", "node5"]
BRANCH = "matt"
PROJECT_PATH = "/home/cluster/ELEC490-Capstone"

def sync_node(node):
    print(f"--- Syncing {node} ---")
    # Command to run on the remote node
    remote_cmd = f"cd {PROJECT_PATH} && git fetch origin && git checkout {BRANCH} && git pull {BRANCH}"
    
    try:
        # Run via SSH
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", node, remote_cmd],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"Successfully updated {node}")
        else:
            print(f"Failed to update {node}: {result.stderr.strip()}")
    except Exception as e:
        print(f"Could not connect to {node}: {e}")

if __name__ == "__main__":
    for node in NODES:
        sync_node(node)
    print("Cluster sync complete!")