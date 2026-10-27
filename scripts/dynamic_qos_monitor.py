#!/usr/bin/env python3

import csv
import os
import re
import signal
import subprocess
import time
from datetime import datetime

SWITCH = "s1"
INTERVAL = 5

NORMAL_RATE = 4_000_000
ADAPTED_RATE = 8_000_000

HIGH_THRESHOLD_MBPS = 2.5
LOW_THRESHOLD_MBPS = 0.5

HIGH_SAMPLES_REQUIRED = 2
LOW_SAMPLES_REQUIRED = 3

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
SAMPLES_FILE = os.path.join(
    RESULTS_DIR, "dynamic_qos_samples.csv"
)
EVENTS_FILE = os.path.join(
    RESULTS_DIR, "dynamic_qos_events.csv"
)

# Queue 2 UUIDs discovered dynamically from OVS configuration.
QUEUE_UUIDS = []

running = True


def stop_handler(signum, frame):
    global running
    running = False


signal.signal(signal.SIGINT, stop_handler)
signal.signal(signal.SIGTERM, stop_handler)


def run_command(args):
    cmd = list(args)
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd

    result = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=10,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n"
            f"{result.stderr.strip()}"
        )

    return result.stdout


def find_queue2_uuids():
    """
    Dynamically discover Queue 2 UUIDs from OVSDB.
    Extracts Queue 2 mappings from all configured QoS tables.
    """
    output = run_command([
        "ovs-vsctl",
        "--bare",
        "--columns=queues",
        "list",
        "qos",
    ])

    uuids = []
    for line in output.splitlines():
        # Match '2=<uuid>'
        match = re.search(r"2=([0-9a-fA-F-]+)", line)
        if match:
            uuids.append(match.group(1))

    if not uuids:
        # Fallback: find queue where min-rate is 1000000
        output = run_command([
            "ovs-vsctl",
            "--bare",
            "--columns=_uuid",
            "find",
            "queue",
            'other-config:min-rate="1000000"',
        ])
        for line in output.splitlines():
            line = line.strip()
            if line:
                uuids.append(line)

    if not uuids:
        raise RuntimeError(
            "Could not discover Queue 2 UUIDs in OVS QoS configuration. "
            "Verify that QoS and queues are configured on the switch."
        )

    return sorted(list(set(uuids)))


def read_queue2_bytes():
    output = run_command([
        "ovs-ofctl",
        "-O", "OpenFlow13",
        "queue-stats",
        SWITCH,
    ])

    total_bytes = 0
    found = False

    pattern = re.compile(
        r'port\s+"?([^"\s]+)"?\s+queue\s+2:'
        r'\s+bytes=(\d+)'
    )

    for line in output.splitlines():
        match = pattern.search(line)

        if match:
            total_bytes += int(match.group(2))
            found = True

    if not found:
        raise RuntimeError(
            "Queue 2 counters were not found. "
            "Check the switch and queue configuration."
        )

    return total_bytes


def set_queue_rate(rate, uuids=None):
    target_uuids = uuids if uuids is not None else QUEUE_UUIDS
    for queue_uuid in target_uuids:
        run_command([
            "ovs-vsctl",
            "set", "Queue", queue_uuid,
            f"other-config:max-rate={rate}",
        ])


def read_queue_rate(uuids=None):
    target_uuids = uuids if uuids is not None else QUEUE_UUIDS
    rates = []

    for queue_uuid in target_uuids:
        output = run_command([
            "ovs-vsctl",
            "get", "Queue", queue_uuid,
            "other-config:max-rate",
        ])

        value = output.strip().strip('"')

        if not value.isdigit():
            raise RuntimeError(
                f"Invalid max-rate for Queue {queue_uuid}: {value}"
            )

        rates.append(int(value))

    if len(set(rates)) != 1:
        raise RuntimeError(
            f"Queue 2 maximum rates disagree: {rates}"
        )

    if rates[0] not in (NORMAL_RATE, ADAPTED_RATE):
        raise RuntimeError(
            f"Unexpected Queue 2 maximum rate: {rates[0]}"
        )

    return rates[0]


def write_event(writer, timestamp, action, rate, reason):
    writer.writerow([
        timestamp,
        action,
        rate,
        reason,
    ])


def main():
    global QUEUE_UUIDS
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # Dynamically locate Queue 2 UUIDs from OVS
    QUEUE_UUIDS = find_queue2_uuids()
    print(f"Discovered Queue 2 UUIDs: {QUEUE_UUIDS}")

    # Read the actual configuration and initialize counters.
    current_rate = read_queue_rate()
    previous_bytes = read_queue2_bytes()
    previous_time = time.monotonic()
    high_count = 0
    low_count = 0

    print("Dynamic QoS monitor started.")
    print("Monitoring Queue 2 on switch s1.")
    print("Press Ctrl+C to stop.")
    print(
        f"Initial Queue 2 maximum: "
        f"{current_rate / 1_000_000:.0f} Mbps"
    )

    samples_exists = os.path.exists(SAMPLES_FILE)
    events_exists = os.path.exists(EVENTS_FILE)

    with open(SAMPLES_FILE, "a", newline="") as samples, \
         open(EVENTS_FILE, "a", newline="") as events:

        sample_writer = csv.writer(samples)
        event_writer = csv.writer(events)

        if not samples_exists or os.path.getsize(SAMPLES_FILE) == 0:
            sample_writer.writerow([
                "timestamp",
                "queue2_rate_mbps",
                "queue2_max_rate_mbps",
                "action",
            ])

        if not events_exists or os.path.getsize(EVENTS_FILE) == 0:
            event_writer.writerow([
                "timestamp",
                "action",
                "queue2_max_rate_mbps",
                "reason",
            ])

        samples.flush()
        events.flush()

        try:
            while running:
                time.sleep(INTERVAL)

                if not running:
                    break

                now = time.monotonic()
                current_bytes = read_queue2_bytes()

                elapsed = now - previous_time
                byte_delta = current_bytes - previous_bytes

                # Handle a counter reset safely.
                if byte_delta < 0:
                    byte_delta = 0

                rate_mbps = (
                    byte_delta * 8 / elapsed / 1_000_000
                    if elapsed > 0 else 0.0
                )

                timestamp = datetime.now().isoformat(
                    timespec="seconds"
                )

                action = "HOLD"

                if rate_mbps >= HIGH_THRESHOLD_MBPS:
                    high_count += 1
                    low_count = 0
                elif rate_mbps <= LOW_THRESHOLD_MBPS:
                    low_count += 1
                    high_count = 0
                else:
                    high_count = 0
                    low_count = 0

                if (
                    current_rate == NORMAL_RATE
                    and high_count >= HIGH_SAMPLES_REQUIRED
                ):
                    set_queue_rate(ADAPTED_RATE)
                    current_rate = ADAPTED_RATE
                    high_count = 0
                    action = "INCREASE_TO_8_MBPS"

                    write_event(
                        event_writer,
                        timestamp,
                        action,
                        8,
                        "Sustained Queue 2 traffic demand",
                    )
                    events.flush()

                    print(
                        f"[{timestamp}] Queue 2 demand "
                        f"{rate_mbps:.2f} Mbps: "
                        "maximum increased to 8 Mbps"
                    )

                elif (
                    current_rate == ADAPTED_RATE
                    and low_count >= LOW_SAMPLES_REQUIRED
                ):
                    set_queue_rate(NORMAL_RATE)
                    current_rate = NORMAL_RATE
                    low_count = 0
                    action = "RESTORE_TO_4_MBPS"

                    write_event(
                        event_writer,
                        timestamp,
                        action,
                        4,
                        "Queue 2 remained idle",
                    )
                    events.flush()

                    print(
                        f"[{timestamp}] Queue 2 demand "
                        f"{rate_mbps:.2f} Mbps: "
                        "maximum restored to 4 Mbps"
                    )

                sample_writer.writerow([
                    timestamp,
                    round(rate_mbps, 4),
                    current_rate / 1_000_000,
                    action,
                ])
                samples.flush()

                print(
                    f"[{timestamp}] Queue 2 rate="
                    f"{rate_mbps:.2f} Mbps | "
                    f"maximum={current_rate / 1_000_000:.0f} Mbps "
                    f"| {action}"
                )

                previous_bytes = current_bytes
                previous_time = now

        except Exception as exc:
            print(f"MONITOR ERROR: {exc}")
            raise

        finally:
            print("Dynamic QoS monitor stopped.")
            print(f"Samples: {SAMPLES_FILE}")
            print(f"Events:  {EVENTS_FILE}")


if __name__ == "__main__":
    main()
