#!/usr/bin/env python3
import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# Default nodes to hit in parallel
DEFAULT_NODES = ["node2", "node3", "node4"]


def run_remote_script(node: str, remote_script: str):
    """
    Executes a remote Python script over SSH on a single node.
    Returns (node, stdout, stderr, returncode).
    """
    try:
        proc = subprocess.run(
            ["ssh", node, "python3", remote_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        # ssh not installed on head node
        return node, "", "Error: ssh not found on this machine.\n", 127

    return node, proc.stdout, proc.stderr, proc.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Run a remote Python script on multiple nodes in parallel."
    )
    parser.add_argument(
        "nodes",
        nargs="*",
        help="List of node hostnames (default: node2..node6)",
    )
    parser.add_argument(
        "--script",
        default="test.py",
        help="Remote script path/name (default: test.py)",
    )
    args = parser.parse_args()

    nodes = args.nodes or DEFAULT_NODES
    if not nodes:
        print("No nodes specified and no defaults configured.", file=sys.stderr)
        return 1

    print(f"Running {args.script} on nodes: {', '.join(nodes)}\n")

    # Use a thread per node (or fewer, but this is fine for a small cluster)
    results = []
    with ThreadPoolExecutor(max_workers=len(nodes)) as executor:
        future_to_node = {
            executor.submit(run_remote_script, node, args.script): node
            for node in nodes
        }

        # As each node finishes, print its result immediately
        for future in as_completed(future_to_node):
            node = future_to_node[future]
            try:
                node_name, stdout, stderr, rc = future.result()
            except Exception as e:
                print(f"=== {node} ===")
                print(f"  ERROR running remote script: {e}")
                print()
                continue

            print(f"=== {node_name} (exit code {rc}) ===")

            if stdout:
                print("--- STDOUT ---")
                print(stdout, end="")

            if stderr:
                print("--- STDERR ---", file=sys.stderr)
                # print to stderr but still tag the node
                print(stderr, end="", file=sys.stderr)

            print()  # blank line between nodes
            results.append(rc)

    # Return nonzero if any node failed
    return 0 if all(rc == 0 for rc in results) else 1


if __name__ == "__main__":
    sys.exit(main())

# SCRIPT THAT IS ON THE OTHER NODES
"""
import time


def main():
    time.sleep(2)
    return "hi"


if __name__ == "__main__":
    result = main()
    print(result)
"""