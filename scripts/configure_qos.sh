#!/usr/bin/env bash
# configure_qos.sh - Deterministically configure Linux-HTB QoS queues on switch s1 ports
# Root rate: 10 Mbps
# Queue 1 (High Priority): Min 6 Mbps, Max 8 Mbps
# Queue 2 (Normal Priority): Min 1 Mbps, Max 4 Mbps (Dynamic up to 8 Mbps)

set -euo pipefail

echo "=========================================================="
echo "Configuring Linux-HTB QoS on Open vSwitch switch s1"
echo "=========================================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

# Ensure s1 and its ports exist
if ! $SUDO ovs-vsctl br-exists s1; then
  echo "Error: Bridge s1 does not exist. Please start Mininet first."
  exit 1
fi

echo "Cleaning previous QoS configurations..."
$SUDO ovs-vsctl --if-exists clear Port s1-eth1 qos || true
$SUDO ovs-vsctl --if-exists clear Port s1-eth2 qos || true
$SUDO ovs-vsctl --all destroy qos || true
$SUDO ovs-vsctl --all destroy queue || true

echo "Applying HTB QoS queues to s1-eth1..."
$SUDO ovs-vsctl -- set Port s1-eth1 qos=@qos1 \
  -- --id=@qos1 create QoS type=linux-htb other-config:max-rate=10000000 queues=1=@q1_1,2=@q1_2 \
  -- --id=@q1_1 create Queue other-config:min-rate=6000000 other-config:max-rate=8000000 \
  -- --id=@q1_2 create Queue other-config:min-rate=1000000 other-config:max-rate=4000000 >/dev/null

echo "Applying HTB QoS queues to s1-eth2..."
$SUDO ovs-vsctl -- set Port s1-eth2 qos=@qos2 \
  -- --id=@qos2 create QoS type=linux-htb other-config:max-rate=10000000 queues=1=@q2_1,2=@q2_2 \
  -- --id=@q2_1 create Queue other-config:min-rate=6000000 other-config:max-rate=8000000 \
  -- --id=@q2_2 create Queue other-config:min-rate=1000000 other-config:max-rate=4000000 >/dev/null

echo "QoS configuration complete."
echo "Configured QoS objects:"
$SUDO ovs-vsctl list qos
