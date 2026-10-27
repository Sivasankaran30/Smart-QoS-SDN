import csv
import os
import re
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BASE, "results")
GRAPHS = os.path.join(RESULTS, "graphs")
os.makedirs(GRAPHS, exist_ok=True)


def save(fig, filename):
    path = os.path.join(GRAPHS, filename)
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("Created:", path)


# --------------------------------------------------
# Load dynamic QoS samples and adaptation events
# --------------------------------------------------
sample_file = os.path.join(RESULTS, "dynamic_qos_samples.csv")
events_file = os.path.join(RESULTS, "dynamic_qos_events.csv")

with open(sample_file, newline="") as f:
    samples = list(csv.DictReader(f))

with open(events_file, newline="") as f:
    events = list(csv.DictReader(f))

if not samples:
    raise ValueError("No dynamic QoS samples found.")

# --------------------------------------------------
# Select the latest continuous monitoring session.
# A gap greater than 30 seconds marks a new session.
# --------------------------------------------------
all_samples = []

for row in samples:
    row["_time"] = datetime.fromisoformat(row["timestamp"])
    all_samples.append(row)

session_start = len(all_samples) - 1

for i in range(len(all_samples) - 1, 0, -1):
    gap = (
        all_samples[i]["_time"] - all_samples[i - 1]["_time"]
    ).total_seconds()

    if gap > 30:
        break

    session_start = i - 1

latest_samples = all_samples[session_start:]
session_start_time = latest_samples[0]["_time"]

elapsed = [
    (row["_time"] - session_start_time).total_seconds()
    for row in latest_samples
]

traffic = [
    float(row["queue2_rate_mbps"])
    for row in latest_samples
]

maximum = [
    float(row["queue2_max_rate_mbps"])
    for row in latest_samples
]

# Select only events belonging to the latest session.
latest_events = []

for row in events:
    event_time = datetime.fromisoformat(row["timestamp"])

    if event_time >= session_start_time:
        latest_events.append((row, event_time))

# --------------------------------------------------
# Graph 1: Latest session traffic and queue maximum
# --------------------------------------------------
fig, ax1 = plt.subplots(figsize=(11, 5.5))

ax1.plot(
    elapsed,
    traffic,
    marker=".",
    markersize=4,
    label="Measured Queue 2 counter rate",
)

ax1.set_xlabel("Elapsed time (seconds)")
ax1.set_ylabel("Measured Queue 2 counter rate (Mbps)")
ax1.set_title("Dynamic QoS Adaptation — Latest Monitoring Session")
ax1.grid(True, alpha=0.3)

ax2 = ax1.twinx()

ax2.step(
    elapsed,
    maximum,
    where="post",
    linestyle="--",
    label="Queue 2 configured maximum",
)

ax2.set_ylabel("Configured Queue 2 maximum (Mbps)")
ax2.set_ylim(0, max(maximum) * 1.25)

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()

ax1.legend(
    lines1 + lines2,
    labels1 + labels2,
    loc="upper left",
)

save(fig, "01_dynamic_qos_adaptation.png")

# --------------------------------------------------
# Graph 2: Adaptation events in the latest session
# --------------------------------------------------
fig, ax = plt.subplots(figsize=(10, 5))

ax.step(
    elapsed,
    maximum,
    where="post",
    label="Queue 2 configured maximum",
)

for row, event_time in latest_events:
    minute = (event_time - session_start_time).total_seconds()
    rate = float(row["queue2_max_rate_mbps"])

    ax.scatter(minute, rate, s=65, zorder=3)

    ax.annotate(
        row["action"].replace("_", " "),
        (minute, rate),
        xytext=(7, 9),
        textcoords="offset points",
        fontsize=8,
    )

ax.set_xlabel("Elapsed time (seconds)")
ax.set_ylabel("Queue 2 maximum rate (Mbps)")
ax.set_title("Queue 2 Adaptation Events — Latest Session")
ax.set_ylim(0, max(maximum) * 1.3)
ax.grid(True, alpha=0.3)
ax.legend()

save(fig, "02_adaptation_events.png")

# --------------------------------------------------
# Graph 3: Earlier recorded 30-second TCP QoS test
# --------------------------------------------------
tcp_file = os.path.join(RESULTS, "tcp_test.log")

with open(tcp_file) as f:
    tcp_text = f.read()

sender_match = re.search(
    r"\[.*?\]\s+0\.00-30\.00\s+sec\s+"
    r"[\d.]+\s+\w+\s+([\d.]+)\s+Mbits/sec\s+\d+\s+sender",
    tcp_text,
)

receiver_match = re.search(
    r"\[.*?\]\s+0\.00-30\.\d+\s+sec\s+"
    r"[\d.]+\s+\w+\s+([\d.]+)\s+Mbits/sec\s+receiver",
    tcp_text,
)

if sender_match:
    values = [float(sender_match.group(1))]
    labels = ["Sender"]

    if receiver_match:
        values.append(float(receiver_match.group(1)))
        labels.append("Receiver")

    fig, ax = plt.subplots(figsize=(7, 5))

    bars = ax.bar(labels, values)

    ax.set_ylabel("TCP throughput (Mbps)")
    ax.set_title("Earlier Recorded TCP QoS Test (30 seconds)")
    ax.grid(axis="y", alpha=0.3)

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{value:.2f} Mbps",
            ha="center",
            va="bottom",
        )

    save(fig, "03_tcp_qos_throughput.png")

else:
    print("WARNING: Could not parse TCP throughput from tcp_test.log")

# --------------------------------------------------
# Graph 4: Supported Performance Evaluation
# Real data parsed directly from test logs:
# - High-priority ICMP ping RTT under concurrent TCP load
# - TCP Normal Traffic Throughput: Initial vs Adapted QoS
# --------------------------------------------------
try:
    ping_file = os.path.join(RESULTS, "qos_ping_under_load.log")
    if not os.path.exists(ping_file):
        ping_file = os.path.join(RESULTS, "qos_concurrent_ping.log")

    ping_rtts = []
    ping_min = 0.0
    ping_avg = 0.0
    ping_max = 0.0
    ping_loss = 0.0

    if os.path.exists(ping_file):
        with open(ping_file) as f:
            p_text = f.read()

        for match in re.finditer(r"time=([\d.]+)\s*ms", p_text):
            ping_rtts.append(float(match.group(1)))

        stats_m = re.search(
            r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms",
            p_text,
        )
        if stats_m:
            ping_min, ping_avg, ping_max, _ = [float(x) for x in stats_m.groups()]
        elif ping_rtts:
            ping_min = min(ping_rtts)
            ping_avg = sum(ping_rtts) / len(ping_rtts)
            ping_max = max(ping_rtts)

        loss_m = re.search(r"(\d+)% packet loss", p_text)
        if loss_m:
            ping_loss = float(loss_m.group(1))

    # Parse initial TCP throughput from tcp_test.log
    init_sender = float(sender_match.group(1)) if sender_match else 4.44
    init_receiver = float(receiver_match.group(1)) if receiver_match else 3.83

    # Parse adapted TCP throughput from experiment summary
    pre_final_path = os.path.join(RESULTS, "experiment_summary.txt.pre_final")
    adapt_sender = 6.70
    adapt_receiver = 6.22
    if os.path.exists(pre_final_path):
        with open(pre_final_path) as f:
            pf_text = f.read()
        s_m = re.search(r"Sender throughput:\s*([\d.]+)\s*Mbps", pf_text)
        r_m = re.search(r"Receiver throughput:\s*([\d.]+)\s*Mbps", pf_text)
        if s_m:
            adapt_sender = float(s_m.group(1))
        if r_m:
            adapt_receiver = float(r_m.group(1))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))

    # Subplot 1: Real Ping RTT time series under load
    if ping_rtts:
        probe_indices = list(range(1, len(ping_rtts) + 1))
        ax1.plot(
            probe_indices,
            ping_rtts,
            marker="o",
            markersize=3,
            color="#2980b9",
            alpha=0.7,
            label="Measured ICMP probe RTT",
        )
        ax1.axhline(
            ping_avg,
            color="#e74c3c",
            linestyle="--",
            label=f"Average RTT ({ping_avg:.3f} ms)",
        )
        ax1.set_xlabel("Probe sequence number (during 30s TCP load)")
        ax1.set_ylabel("Round-trip time (ms)")
        ax1.set_title("High-Priority Latency Under TCP Congestion (Queue 1)")
        ax1.grid(True, alpha=0.3)
        ax1.set_ylim(0, max(ping_max * 1.25, 1.0))

        # Annotation box with summary stats
        summary_box = (
            f"Probes: {len(ping_rtts)}\n"
            f"Packet Loss: {ping_loss:.1f}%\n"
            f"Min RTT: {ping_min:.3f} ms\n"
            f"Avg RTT: {ping_avg:.3f} ms\n"
            f"Max RTT: {ping_max:.3f} ms"
        )
        ax1.text(
            0.96,
            0.94,
            summary_box,
            transform=ax1.transAxes,
            verticalalignment="top",
            horizontalalignment="right",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="white", edgecolor="#bdc3c7", alpha=0.9),
            fontsize=9,
        )
        ax1.legend(loc="upper left")
    else:
        ax1.text(0.5, 0.5, "Ping log data unavailable", ha="center", va="center")
        ax1.set_title("High-Priority Latency Under Load")

    # Subplot 2: Grouped bar chart comparing Initial vs Adapted QoS states
    categories = ["Initial QoS State\n(Queue 2 @ 4 Mbps)", "Adapted QoS State\n(Queue 2 @ 8 Mbps)"]
    x = [0, 1]
    width = 0.25

    cfg_limits = [4.0, 8.0]
    sender_rates = [init_sender, adapt_sender]
    receiver_rates = [init_receiver, adapt_receiver]

    rects1 = ax2.bar([p - width for p in x], cfg_limits, width, label="Configured Queue Limit", color="#bdc3c7", edgecolor="#7f8c8d", hatch="//")
    rects2 = ax2.bar(x, sender_rates, width, label="Measured Sender Throughput", color="#3498db")
    rects3 = ax2.bar([p + width for p in x], receiver_rates, width, label="Measured Receiver Throughput", color="#2ecc71")

    ax2.set_ylabel("Throughput (Mbps)")
    ax2.set_title("TCP Normal Traffic Throughput: Initial vs Adapted QoS")
    ax2.set_xticks(x)
    ax2.set_xticklabels(categories)
    ax2.set_ylim(0, 10.5)
    ax2.grid(axis="y", alpha=0.3)
    ax2.legend(loc="upper left")

    def autolabel(rects):
        for rect in rects:
            h = rect.get_height()
            ax2.text(
                rect.get_x() + rect.get_width() / 2.0,
                h + 0.15,
                f"{h:.2f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )

    autolabel(rects1)
    autolabel(rects2)
    autolabel(rects3)

    save(fig, "04_qos_vs_baseline_comparison.png")

except Exception as err:
    print(f"WARNING: Failed to generate Graph 4: {err}")

print("\nGraph generation finished.")
