# Smart QoS Management Using Software-Defined Networking (SDN)

An OpenFlow 1.3-based Software-Defined Networking (SDN) Quality of Service (QoS) management architecture implemented with the Ryu controller, Mininet virtual network emulator, and Open vSwitch (OVS) with Linux HTB traffic control.

---

## 1. Architecture Overview

```
       +---------------------------------------------+
       |               Ryu Controller                |
       |  - OpenFlow 1.3 Engine                      |
       |  - MAC Learning & Flow Manager              |
       |  - Multi-Protocol Traffic Classifier        |
       |  - Periodic Flow & Queue Telemetry (EWMA)   |
       +---------------------------------------------+
                              | OpenFlow 1.3 (TCP 6653)
                              v
       +---------------------------------------------+
       |             Open vSwitch (s1)               |
       |  - Flow Table (Priorities 20, 10, 5, 0)     |
       |  - Linux HTB QoS Queues (10 Mbps root)      |
       |    * Queue 1: Min 6 Mbps, Max 8 Mbps        |
       |    * Queue 2: Min 1 Mbps, Max 4/8 Mbps      |
       +---------------------------------------------+
               /                             \
      Port 1 (s1-eth1)                 Port 2 (s1-eth2)
             /                                 \
        +--------+                         +--------+
        | Host 1 |                         | Host 2 |
        |   h1   |                         |   h2   |
        +--------+                         +--------+
```

---

## 2. Traffic Classification Policy

Traffic is classified based on protocol headers by `controller/smart_qos.py`:
1. **High Priority (Queue 1, Flow Priority 20)**:
   - **ICMP (`proto=1`)**: Network control, monitoring, and latency-critical probes.
   - **UDP (`proto=17`)**: Real-time traffic, VoIP, audio/video streaming.
   - Enforces minimum guaranteed rate of 6 Mbps, maximum rate 8 Mbps.
2. **Normal Priority (Queue 2, Flow Priority 10)**:
   - **TCP (`proto=6`)**: Bulk data transfer, file downloads, web traffic.
   - Shaped to 4 Mbps baseline (min 1 Mbps), dynamically expandable to 8 Mbps.
3. **Best Effort / Control (Queue 0 / Default, Priority 5)**:
   - **ARP (`eth_type=0x0806`)**: Address resolution forwarding.
4. **Table-Miss Rule (Priority 0)**:
   - Forwards unknown packets to Ryu controller for learning.

---

## 3. Dynamic QoS Adaptation Mechanism

The dynamic QoS monitor (`scripts/dynamic_qos_monitor.py`):
1. Dynamically queries Open vSwitch for Queue 2 identifiers (no hardcoded UUIDs).
2. Continuously reads OpenFlow Queue statistics every 5 seconds.
3. **Threshold Logic**:
   - **Traffic Surge**: If Queue 2 aggregate demand exceeds **2.5 Mbps** for **2 consecutive samples**, Queue 2 maximum rate expands to **8 Mbps** (`INCREASE_TO_8_MBPS`).
   - **Traffic Idle**: If Queue 2 demand drops below **0.5 Mbps** for **3 consecutive samples**, Queue 2 rate restores to **4 Mbps** (`RESTORE_TO_4_MBPS`).
4. Logs telemetry to `results/dynamic_qos_samples.csv` and `results/dynamic_qos_events.csv`.

---

## 4. Project Directory Structure

```
smart-qos/
├── controller/
│   └── smart_qos.py             # Ryu OpenFlow 1.3 QoS controller
├── scripts/
│   ├── configure_qos.sh         # Configures OVS Linux-HTB queues on s1
│   ├── remove_qos.sh            # Removes queues for unprioritized baseline
│   ├── dynamic_qos_monitor.py   # Dynamic queue-rate adaptation monitor
│   └── generate_graphs.py       # Produces 300 DPI evaluation figures
├── results/
│   ├── flow_telemetry.csv       # Continuous flow rate telemetry (EWMA)
│   ├── dynamic_qos_samples.csv  # Queue counter rate samples
│   ├── dynamic_qos_events.csv   # Dynamic adaptation event logs
│   ├── tcp_test.log             # Recorded TCP iperf3 test logs
│   ├── experiment_summary.txt   # Detailed experiment summary and metrics
│   └── graphs/
│       ├── 01_dynamic_qos_adaptation.png
│       ├── 02_adaptation_events.png
│       ├── 03_tcp_qos_throughput.png
│       └── 04_qos_vs_baseline_comparison.png
├── backups/                     # Timestamped safety backups
└── README.md                    # Project documentation & execution guide
```

---

## 5. Step-by-Step Demonstration Guide

### Terminal 1: Start the Ryu Controller
```bash
cd ~/smart-qos
source ryu-venv/bin/activate
ryu-manager controller/smart_qos.py
```

### Terminal 2: Start Mininet Topology
```bash
sudo mn --topo single,2 --mac --switch ovsk,protocols=OpenFlow13 --controller remote,ip=127.0.0.1,port=6653
```

### Terminal 3: Configure QoS Queues on Switch
```bash
cd ~/smart-qos
sudo ./scripts/configure_qos.sh
```

### Terminal 4: Start the Dynamic QoS Monitor
```bash
cd ~/smart-qos
source ryu-venv/bin/activate
sudo ryu-venv/bin/python scripts/dynamic_qos_monitor.py
```

### In Mininet CLI (Terminal 2): Run QoS Verification

1. **Verify Connectivity**:
   ```
   mininet> pingall
   ```

2. **Start iperf3 Server on Host 2**:
   ```
   mininet> h2 iperf3 -s -D
   ```

3. **Demonstrate High-Priority Isolation Under Congestion**:
   Run high-priority ping concurrently with normal-priority TCP traffic:
   ```
   mininet> h1 ping -c 15 10.0.0.2 &
   mininet> h1 iperf3 -c 10.0.0.2 -t 15
   ```
   *Observe*: Ping latency remains steady at ~0.1 ms despite full TCP saturation!

4. **Demonstrate Dynamic Bandwidth Adaptation**:
   Run sustained TCP traffic for 30 seconds:
   ```
   mininet> h1 iperf3 -c 10.0.0.2 -t 30
   ```
   *Observe in Terminal 4*: Monitor detects sustained traffic demand >= 2.5 Mbps and increases Queue 2 max rate from 4 Mbps to 8 Mbps! When traffic finishes, it restores to 4 Mbps.

### 5. Verifying Flows and Queues
```bash
# Inspect installed OpenFlow rules (verifying priorities and queue actions)
sudo ovs-ofctl -O OpenFlow13 dump-flows s1

# Inspect queue packet/byte counters
sudo ovs-ofctl -O OpenFlow13 queue-stats s1

# Inspect kernel HTB class statistics
tc -s class show dev s1-eth1
tc -s class show dev s1-eth2
```

### 6. Generate Evaluation Graphs
```bash
cd ~/smart-qos
source ryu-venv/bin/activate
python scripts/generate_graphs.py
```
View the generated figures in `results/graphs/`.

---

## 6. Teardown and Cleanup Procedures

### Clean Up QoS Configuration
```bash
cd ~/smart-qos
sudo ./scripts/remove_qos.sh
```

### Stop Processes and Clean Mininet
1. In Terminal 4 (Monitor): Press `Ctrl+C`.
2. In Terminal 1 (Controller): Press `Ctrl+C`.
3. In Terminal 2 (Mininet): Type `exit`.
4. Clean up any lingering virtual interfaces and Open vSwitch state:
   ```bash
   sudo mn -c
   ```
