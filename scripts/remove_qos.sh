#!/usr/bin/env bash
# remove_qos.sh - Remove QoS queues from switch s1 to run the unprioritized baseline comparison

set -euo pipefail

echo "=========================================================="
echo "Removing QoS queues from switch s1 (Baseline Mode)"
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

$SUDO ovs-vsctl --if-exists clear Port s1-eth1 qos || true
$SUDO ovs-vsctl --if-exists clear Port s1-eth2 qos || true
$SUDO ovs-vsctl --all destroy qos || true
$SUDO ovs-vsctl --all destroy queue || true

echo "QoS successfully removed. Switch s1 is operating in best-effort baseline mode."
