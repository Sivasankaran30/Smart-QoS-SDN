import csv
import os
import time
from datetime import datetime

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib.packet import ipv4


class SmartQoSController(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    HIGH_PRIORITY_QUEUE = 1
    NORMAL_PRIORITY_QUEUE = 2

    def __init__(self, *args, **kwargs):
        super(SmartQoSController, self).__init__(*args, **kwargs)

        # Existing MAC-learning state
        self.mac_to_port = {}

        # Connected OpenFlow switches
        self.datapaths = {}

        # Flow telemetry configuration
        self.telemetry_interval = 5
        self.ewma_alpha = 0.3

        # Previous counter samples:
        # flow_key -> (byte_count, monotonic_timestamp)
        self.previous_flow_stats = {}

        # Smoothed throughput estimates
        self.ewma_throughput = {}

        # Prepare the project results directory
        project_dir = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )

        self.results_dir = os.path.join(project_dir, "results")
        os.makedirs(self.results_dir, exist_ok=True)

        self.telemetry_file = os.path.join(
            self.results_dir,
            "flow_telemetry.csv",
        )

        # Create the CSV header if the file is missing or empty.
        needs_header = (
            not os.path.exists(self.telemetry_file)
            or os.path.getsize(self.telemetry_file) == 0
        )

        if needs_header:
            with open(
                self.telemetry_file,
                "w",
                newline="",
                encoding="utf-8",
            ) as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow([
                    "timestamp",
                    "datapath_id",
                    "table_id",
                    "in_port",
                    "eth_src",
                    "eth_dst",
                    "eth_type",
                    "ip_proto",
                    "priority",
                    "packet_count",
                    "byte_count",
                    "instantaneous_mbps",
                    "ewma_mbps",
                ])

        # Start the periodic statistics monitor.
        self.monitor_thread = hub.spawn(self._monitor)

        self.logger.info("Smart QoS Controller initialized")
        self.logger.info(
            "Flow telemetry enabled: interval=%s seconds, EWMA alpha=%s",
            self.telemetry_interval,
            self.ewma_alpha,
        )

    # ---------------------------------------------------------
    # Switch connection and initial flow setup
    # ---------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Preserve the existing table-miss rule.
        match = parser.OFPMatch()

        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER,
            )
        ]

        self.add_flow(
            datapath,
            priority=0,
            match=match,
            actions=actions,
        )

        self.logger.info(
            "Switch connected: datapath ID %s",
            datapath.id,
        )

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER],
    )
    def switch_state_handler(self, ev):
        datapath = ev.datapath

        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
            self.logger.info(
                "Telemetry registered datapath %s",
                datapath.id,
            )

        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                del self.datapaths[datapath.id]

            self.logger.info(
                "Switch disconnected: datapath ID %s",
                datapath.id,
            )

    # ---------------------------------------------------------
    # Flow installation
    # ---------------------------------------------------------

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions,
            )
        ]

        flow_mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=instructions,
        )

        datapath.send_msg(flow_mod)

    # ---------------------------------------------------------
    # Existing traffic classification
    # ---------------------------------------------------------

    def classify_traffic(self, eth, pkt):
        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        if ip_pkt is not None:

            # ICMP (Control / Latency-critical) -> High Priority Queue 1
            if ip_pkt.proto == 1:
                self.logger.info(
                    "Traffic classified: ICMP -> high-priority queue %s (prio 20)",
                    self.HIGH_PRIORITY_QUEUE,
                )
                return self.HIGH_PRIORITY_QUEUE, 20, ip_pkt.proto

            # UDP (VoIP / Streaming / Real-time) -> High Priority Queue 1
            if ip_pkt.proto == 17:
                self.logger.info(
                    "Traffic classified: UDP -> high-priority queue %s (prio 20)",
                    self.HIGH_PRIORITY_QUEUE,
                )
                return self.HIGH_PRIORITY_QUEUE, 20, ip_pkt.proto

            # TCP (Bulk Data / Best-effort) -> Normal Priority Queue 2
            if ip_pkt.proto == 6:
                self.logger.info(
                    "Traffic classified: TCP -> normal-priority queue %s (prio 10)",
                    self.NORMAL_PRIORITY_QUEUE,
                )
                return self.NORMAL_PRIORITY_QUEUE, 10, ip_pkt.proto

            # Other IPv4 traffic -> Normal Priority Queue 2
            self.logger.info(
                "Traffic classified: IPv4 proto %s -> normal-priority queue %s (prio 10)",
                ip_pkt.proto,
                self.NORMAL_PRIORITY_QUEUE,
            )
            return self.NORMAL_PRIORITY_QUEUE, 10, ip_pkt.proto

        return None, 5, None

    # ---------------------------------------------------------
    # Existing MAC learning and packet handling
    # ---------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src = eth.src
        dst = eth.dst
        dpid = datapath.id

        # Learn the source MAC address.
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # Find the destination port.
        out_port = self.mac_to_port[dpid].get(
            dst,
            ofproto.OFPP_FLOOD,
        )

        actions = []

        queue_id, priority, ip_proto = self.classify_traffic(
            eth,
            pkt,
        )

        # Select a queue when the destination port is known.
        if out_port != ofproto.OFPP_FLOOD and queue_id is not None:
            actions.append(parser.OFPActionSetQueue(queue_id))

        actions.append(parser.OFPActionOutput(out_port))

        # Install specific rules after learning the destination.
        if out_port != ofproto.OFPP_FLOOD:

            if eth.ethertype == ether_types.ETH_TYPE_IP:
                match = parser.OFPMatch(
                    in_port=in_port,
                    eth_src=src,
                    eth_dst=dst,
                    eth_type=ether_types.ETH_TYPE_IP,
                    ip_proto=ip_proto,
                )

            else:
                match = parser.OFPMatch(
                    in_port=in_port,
                    eth_src=src,
                    eth_dst=dst,
                    eth_type=eth.ethertype,
                )

            self.add_flow(
                datapath,
                priority=priority,
                match=match,
                actions=actions,
            )

        # Forward the packet that triggered the Packet-In event.
        data = None

        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )

        datapath.send_msg(out)

    # ---------------------------------------------------------
    # Periodic flow and queue statistics requests
    # ---------------------------------------------------------

    def _monitor(self):
        while True:
            for datapath in list(self.datapaths.values()):

                try:
                    parser = datapath.ofproto_parser
                    ofproto = datapath.ofproto

                    # Periodic Flow Statistics Request
                    request = parser.OFPFlowStatsRequest(
                        datapath=datapath,
                    )
                    datapath.send_msg(request)

                    # Periodic Queue Statistics Request (OpenFlow 1.3)
                    q_request = parser.OFPQueueStatsRequest(
                        datapath=datapath,
                        port_no=ofproto.OFPP_ANY,
                        queue_id=ofproto.OFPQ_ALL,
                    )
                    datapath.send_msg(q_request)

                except Exception:
                    self.logger.exception(
                        "Failed to request statistics from datapath %s",
                        datapath.id,
                    )

            hub.sleep(self.telemetry_interval)

    @set_ev_cls(
        ofp_event.EventOFPQueueStatsReply,
        MAIN_DISPATCHER,
    )
    def queue_stats_reply_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id

        for stat in msg.body:
            if stat.tx_packets > 0:
                self.logger.debug(
                    "Switch %s Port %s Queue %s: tx_bytes=%s, tx_packets=%s",
                    dpid,
                    stat.port_no,
                    stat.queue_id,
                    stat.tx_bytes,
                    stat.tx_packets,
                )

    # ---------------------------------------------------------
    # Flow-statistics replies and throughput calculations
    # ---------------------------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPFlowStatsReply,
        MAIN_DISPATCHER,
    )
    def flow_stats_reply_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id

        # Use a monotonic clock for elapsed-time calculations.
        sample_time = time.monotonic()

        # One human-readable timestamp for this statistics reply.
        timestamp = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )

        rows = []

        for stat in msg.body:

            match = stat.match

            # A stable identifier for each flow entry.
            flow_key = (
                dpid,
                stat.table_id,
                stat.priority,
                str(match),
            )

            packet_count = stat.packet_count
            byte_count = stat.byte_count

            previous = self.previous_flow_stats.get(flow_key)

            instantaneous_mbps = 0.0

            if previous is not None:
                previous_bytes, previous_time = previous

                elapsed = sample_time - previous_time
                byte_delta = byte_count - previous_bytes

                # Counter decreases can occur after resets or
                # flow replacement. Do not report negative rates.
                if elapsed > 0 and byte_delta >= 0:
                    instantaneous_mbps = (
                        byte_delta * 8.0
                    ) / (elapsed * 1_000_000.0)

            # Update the baseline for the next sample.
            self.previous_flow_stats[flow_key] = (
                byte_count,
                sample_time,
            )

            # Apply EWMA smoothing to the measured rate.
            previous_ewma = self.ewma_throughput.get(flow_key)

            if previous_ewma is None:
                smoothed_mbps = instantaneous_mbps
            else:
                smoothed_mbps = (
                    self.ewma_alpha * instantaneous_mbps
                    + (1.0 - self.ewma_alpha) * previous_ewma
                )

            self.ewma_throughput[flow_key] = smoothed_mbps

            # Extract available match fields.
            in_port = match.get("in_port", "")
            eth_src = match.get("eth_src", "")
            eth_dst = match.get("eth_dst", "")
            eth_type = match.get("eth_type", "")
            ip_proto = match.get("ip_proto", "")

            rows.append([
                timestamp,
                dpid,
                stat.table_id,
                in_port,
                eth_src,
                eth_dst,
                eth_type,
                ip_proto,
                stat.priority,
                packet_count,
                byte_count,
                round(instantaneous_mbps, 6),
                round(smoothed_mbps, 6),
            ])

        # Append this reply's measurements to the CSV.
        if rows:
            try:
                with open(
                    self.telemetry_file,
                    "a",
                    newline="",
                    encoding="utf-8",
                ) as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerows(rows)

                self.logger.info(
                    "Flow telemetry recorded: datapath=%s, "
                    "flows=%s, file=%s",
                    dpid,
                    len(rows),
                    self.telemetry_file,
                )

            except OSError:
                self.logger.exception(
                    "Failed to write flow telemetry CSV"
                )
