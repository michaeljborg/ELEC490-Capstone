#!/bin/bash

NODES=(node2 node3 node4 node5 node6)

echo "=== Cluster Health Check ==="
date
echo

for NODE in "${NODES[@]}"; do
  echo ">>> $NODE"

    # Ping test
    if ping -c 1 -W 1 "$NODE" &>/dev/null; then
        echo "  Ping: OK"
    else
        echo "  Ping: FAIL"
    continue
    fi

    # SSH test
    if ssh -o ConnectTimeout=5 node$i "exit" </dev/null &>/dev/null; then
        echo "  SSH: OK"
    else
        echo "  SSH: FAIL"
    fi


  # SSH test
  if ssh -q -o BatchMode=yes -o ConnectTimeout=5 "$NODE" true; then
    echo "  SSH: OK"
  else
    echo "  SSH: FAIL"
  fi

  echo
done
