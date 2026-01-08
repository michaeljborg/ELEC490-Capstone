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
  if ssh -o BatchMode=yes -o ConnectTimeout=5 cluster@"$NODE" "uptime" &>/dev/null; then
    echo "  SSH: OK"
    ssh cluster@"$NODE" "uptime"
  else
    echo "  SSH: FAIL"
  fi

  echo
done
