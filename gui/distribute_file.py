#!/usr/bin/env python3
import argparse
import os
import shlex
import subprocess
from pathlib import Path
from typing import List


DEFAULT_NODES = ["node2", "node3", "node4", "node5"]


def run(cmd: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def ensure_remote_dir(node: str, dest_dir: str) -> None:
    # mkdir -p on remote
    remote_cmd = f'mkdir -p {shlex.quote(dest_dir)}'
    p = run(["ssh", node, remote_cmd], timeout=20)
    if p.returncode != 0:
        raise RuntimeError(f"[{node}] mkdir failed: {p.stderr.strip() or p.stdout.strip()}")


def scp_to_node(node: str, src_file: str, dest_path: str) -> None:
    # Copy local file -> remote path
    p = run(["scp", src_file, f"{node}:{dest_path}"], timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"[{node}] scp failed: {p.stderr.strip() or p.stdout.strip()}")


def main():
    ap = argparse.ArgumentParser(
        description="Copy a local file from node1 to multiple nodes via ssh/scp."
    )
    ap.add_argument("src", help="Source file on node1 (local path). Example: ./payload.json")
    ap.add_argument("dest_dir", help="Destination directory on remote nodes. Example: /home/cluster/ELEC490-Capstone/payloads")
    ap.add_argument("--nodes", nargs="+", default=DEFAULT_NODES, help="Nodes to copy to (default: node2 node3 node4 node5)")
    ap.add_argument("--dest-name", default=None, help="Optional new filename on remote (default: keep same name)")
    ap.add_argument("--timeout", type=int, default=60, help="SCP timeout seconds (default: 60)")
    args = ap.parse_args()

    src_path = Path(args.src).expanduser().resolve()
    if not src_path.exists() or not src_path.is_file():
        raise SystemExit(f"Source file not found: {src_path}")

    dest_dir = args.dest_dir
    dest_name = args.dest_name or src_path.name
    # remote full path (same for each node)
    remote_dest_path = dest_dir.rstrip("/") + "/" + dest_name

    print(f"Source:      {src_path}")
    print(f"Dest dir:    {dest_dir}")
    print(f"Dest name:   {dest_name}")
    print(f"Nodes:       {', '.join(args.nodes)}")
    print()

    for node in args.nodes:
        print(f"==> {node}")
        try:
            ensure_remote_dir(node, dest_dir)
            # use custom timeout passed in
            p = subprocess.run(
                ["scp", str(src_path), f"{node}:{remote_dest_path}"],
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
            if p.returncode != 0:
                raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "scp failed")
            print(f"  OK: copied to {node}:{remote_dest_path}")
        except Exception as e:
            print(f"  FAIL: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
