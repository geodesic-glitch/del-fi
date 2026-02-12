"""Meshtastic mesh interface and simulator.

Handles radio connection, message reception, rate limiting,
duplicate detection, and outgoing message delivery.

Two implementations:
  MeshInterface  — real Meshtastic radio via serial/TCP/BLE
  SimulatorInterface — stdin/stdout for development
"""

import logging
import queue
import re
import threading
import time

from formatter import chunk_text

log = logging.getLogger("delfi.mesh")


class MeshInterface:
    """Real Meshtastic radio interface.

    Connects to a physical radio, subscribes to incoming text messages
    via the meshtastic pub/sub system, and enqueues them for the router.
    """

    def __init__(self, cfg: dict, msg_queue: queue.Queue):
        self.cfg = cfg
        self.msg_queue = msg_queue
        self.interface = None
        self.my_node_id: str | None = None
        self._seen_ids: set[int] = set()
        self._seen_max = 1000
        self._rate_limits: dict[str, float] = {}
        self._lock = threading.Lock()
        self._connected = False
        self._should_run = True

    def connect(self) -> bool:
        """Connect to the Meshtastic radio. Returns True on success."""
        try:
            conn = self.cfg["radio_connection"]
            port = self.cfg["radio_port"]

            if conn == "serial":
                from meshtastic.serial_interface import SerialMeshInterface
                self.interface = SerialMeshInterface(devPath=port)
            elif conn == "tcp":
                from meshtastic.tcp_interface import TCPMeshInterface
                host = port.split(":")[0] if ":" in port else port
                self.interface = TCPMeshInterface(hostname=host)
            elif conn == "ble":
                from meshtastic.ble_interface import BLEMeshInterface
                self.interface = BLEMeshInterface(address=port)

            # Get our own node ID so we never reply to ourselves
            node_info = self.interface.getMyNodeInfo()
            self.my_node_id = node_info.get("user", {}).get("id", None)

            # Subscribe to incoming text messages
            from pubsub import pub
            pub.subscribe(self._on_receive, "meshtastic.receive.text")

            self._connected = True
            log.info(f"radio connected via {conn} ({port})")
            return True

        except Exception as e:
            log.error(f"radio connection failed: {e}")
            self._connected = False
            return False

    def _on_receive(self, packet, interface):
        """Callback fired by meshtastic pub/sub on incoming text."""
        try:
            sender = packet.get("fromId", "")
            text = packet.get("decoded", {}).get("text", "")
            msg_id = packet.get("id", 0)
            to = packet.get("to", 0)

            if not sender or not text:
                return

            # Don't reply to ourselves
            if sender == self.my_node_id:
                return

            # Deduplicate retransmits
            with self._lock:
                if msg_id in self._seen_ids:
                    return
                self._seen_ids.add(msg_id)
                # Prevent unbounded growth
                if len(self._seen_ids) > self._seen_max:
                    self._seen_ids = set(list(self._seen_ids)[-500:])

            # Broadcasts: log but don't respond
            is_broadcast = to in (0xFFFFFFFF, 4294967295)
            if is_broadcast:
                log.info(f"← broadcast from {sender}: {text[:60]}")
                return

            # Rate limit freeform queries (commands bypass)
            is_command = text.strip().startswith("!")
            if not is_command:
                with self._lock:
                    now = time.time()
                    last = self._rate_limits.get(sender, 0)
                    if now - last < self.cfg["rate_limit_seconds"]:
                        log.debug(f"rate limited: {sender}")
                        return
                    self._rate_limits[sender] = now

            log.info(f'← query from {sender}: "{text[:80]}"')
            self.msg_queue.put((sender, text.strip()))

        except Exception as e:
            log.error(f"error handling incoming message: {e}")

    def send_dm(self, dest_id: str, text: str) -> bool:
        """Send a direct message to a node. Chunks if necessary."""
        if not self._connected or not self.interface:
            log.warning(f"can't send to {dest_id}: radio not connected")
            return False

        max_bytes = self.cfg["max_response_bytes"]
        encoded = text.encode("utf-8")

        if len(encoded) <= max_bytes:
            return self._send_one(dest_id, text)

        # Safety net: chunk messages that somehow exceed the limit
        chunks = chunk_text(text, max_bytes)
        for i, chunk in enumerate(chunks):
            if not self._send_one(dest_id, chunk):
                return False
            if i < len(chunks) - 1:
                time.sleep(3)  # inter-chunk delay to avoid flooding

        return True

    def _send_one(self, dest_id: str, text: str) -> bool:
        """Send a single text message to a destination node."""
        try:
            self.interface.sendText(text, destinationId=dest_id)
            log.info(f"  ✓ sent {len(text.encode('utf-8'))} bytes → {dest_id}")
            return True
        except Exception as e:
            log.error(f"send failed to {dest_id}: {e}")
            return False

    def reconnect_loop(self):
        """Background thread: keep trying to reconnect the radio."""
        while self._should_run:
            if not self._connected:
                log.info("attempting radio reconnect...")
                self.connect()
            time.sleep(10)

    @property
    def connected(self) -> bool:
        return self._connected

    def close(self):
        """Clean shutdown."""
        self._should_run = False
        if self.interface:
            try:
                self.interface.close()
            except Exception:
                pass


class SimulatorInterface:
    """Fake mesh interface for development without hardware.

    Reads from stdin, writes to stdout. Input format:
        message text                     (uses default sender !sim00001)
        !a1b2c3d4> message text          (specify sender ID)

    Output format:
        → SENDER_ID: response text
    """

    def __init__(self, cfg: dict, msg_queue: queue.Queue):
        self.cfg = cfg
        self.msg_queue = msg_queue
        self._rate_limits: dict[str, float] = {}
        self._connected = True
        self._should_run = True
        self._default_sender = "!sim00001"

    def start(self):
        """Start the stdin reader thread."""
        thread = threading.Thread(target=self._read_loop, daemon=True)
        thread.start()

    def _read_loop(self):
        """Read lines from stdin and enqueue as messages."""
        node = self.cfg["node_name"]
        print(f"\n  📻 Meshtastic Text Chat — {node} (simulator)")
        print(f"  ─────────────────────────────────────────────")
        print(f"  Type a message, or !nodeID> message to set sender")
        print(f"  Commands start with ! (e.g. !help, !topics)\n")

        while self._should_run:
            try:
                line = input(f"  \033[90mSend ➜\033[0m ")
                if not line.strip():
                    continue

                # Parse optional sender ID prefix: !nodeID> message
                sender = self._default_sender
                text = line.strip()
                match = re.match(r"^(![\w]+)>\s*(.+)$", line.strip())
                if match:
                    sender = match.group(1)
                    text = match.group(2)

                # Echo the sent message in chat format
                ts = time.strftime("%H:%M")
                print(f"  \033[36m{sender}\033[0m \033[90m[{ts}]\033[0m {text}")

                # Rate limit freeform queries (commands bypass)
                is_command = text.startswith("!")
                if not is_command:
                    now = time.time()
                    last = self._rate_limits.get(sender, 0)
                    if now - last < self.cfg["rate_limit_seconds"]:
                        wait = int(self.cfg["rate_limit_seconds"] - (now - last))
                        print(
                            f"  \033[33m⏳ rate limited — wait {wait}s\033[0m"
                        )
                        continue
                    self._rate_limits[sender] = now

                self.msg_queue.put((sender, text))

            except EOFError:
                break
            except KeyboardInterrupt:
                break

    def send_dm(self, dest_id: str, text: str) -> bool:
        """Print response to stdout, enforcing byte limit check."""
        max_bytes = self.cfg["max_response_bytes"]
        size = len(text.encode("utf-8"))

        if size > max_bytes:
            print(f"  \033[31m⚠ {size}B exceeds {max_bytes}B limit\033[0m")

        ts = time.strftime("%H:%M")
        node = self.cfg["node_name"]
        print(f"  \033[32m{node}\033[0m \033[90m[{ts}] ➜ {dest_id}\033[0m {text}\n")
        return True

    @property
    def connected(self) -> bool:
        return True

    def close(self):
        self._should_run = False


def create_interface(
    cfg: dict, simulator: bool, msg_queue: queue.Queue
) -> MeshInterface | SimulatorInterface:
    """Factory: create the appropriate mesh interface."""
    if simulator:
        iface = SimulatorInterface(cfg, msg_queue)
        iface.start()
        return iface
    else:
        return MeshInterface(cfg, msg_queue)
