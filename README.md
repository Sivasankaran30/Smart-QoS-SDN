# Smart QoS: A Real-Time Application-Aware SDN Framework

## Project Overview
This repository contains the source code, network topologies, and evaluation metrics for the Smart QoS framework. The system leverages a Software-Defined Networking (SDN) controller (Ryu) and Open vSwitch (OVS) to dynamically mitigate bufferbloat without relying on Deep Packet Inspection (DPI).

## Current Progress (Phase 1 & 2: Review I)
We are currently at the end of Week 4. The following baseline architecture has been established:
* **Testbed Emulation:** Ubuntu Linux host running Mininet enterprise topology.
* **Traffic Slicing:** Linux Traffic Control (`tc-htb`) configured for static Priority and Best-Effort queues.
* **Baseline Benchmarking:** Static routing experiments completed using `iperf3`, successfully reproducing baseline bufferbloat and latency spikes under heavy congestion.

## Repository Structure
* `/docs` - Contains Project Review I presentation, IEEE literature survey, and mathematical models.
* `/src` - Edge classifier logic and Ryu controller scripts (Under Active Development).
* `/scripts` - Automation scripts for Mininet testbed bring-up and `iperf3` traffic generation.
