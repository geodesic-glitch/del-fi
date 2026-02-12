"""MeshCore mesh adapter.

MeshCore (https://github.com/ripplebiz/MeshCore) is a lightweight mesh
networking protocol designed for LoRa radios.  It uses a different
radio stack and message format than Meshtastic, but the Del-Fi oracle
can drive it through the same adapter interface.

Status: STUB — connection scaffolding is in place.  Implement the
``connect``, ``send_dm``, and ``_on_receive`` methods against the
MeshCore Python library or serial protocol to bring it online.

MeshCore quick reference
------------------------
- Devices communicate over LoRa using a compact binary protocol.
- Node addressing uses short hex IDs (similar to Meshtastic).
- The Python companion library exposes serial/TCP access to a
  MeshCore-flashed radio.
- Messages have a max payload that varies by region/datarate
  (typically ~200 bytes, comparable to Meshtastic).
"""

import logging
import queue
import threading
import time

from formatter import chunk_text
from mesh.base import MeshAdapter

log = logging.getLogger("delfi.mesh.meshcore")


class MeshCoreAdapter(MeshAdapter):
    """MeshCore radio interface.

    Drop-in replacement for the Meshtastic adapter.  Uses the MeshCore
    serial/TCP protocol to talk to a MeshCore-flashed LoRa device.

    Config keys (under ``meshcore:`` in config.yaml)::

        meshcore:
          port: "/dev/ttyUSB0"        # serial port or host:port for TCP
          connection: "serial"         # serial | tcp
          baud_rate: 115200            # serial baud rate (if serial)

    These live alongside the top-level ``mesh_protocol: meshcore``
    setting.
    """

    protocol_name = "MeshCore"

    def __init__(self, cfg: dict, msg_queue: queue.Queue):
        super().__init__(cfg, msg_queue)
        self._mc_cfg = cfg.get("meshcore", {})
        self._connected = False
        self._should_run = True
        self._device = None  # placeholder for MeshCore device handle
        self._lock = threading.Lock()
        self._rate_limits: dict[str, float] = {}
        self.my_node_id: str | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to a MeshCore radio.

        TODO: Replace the placeholder below with real MeshCore library
        calls.  Typical flow:

            from meshcore import MeshCoreDevice   # hypothetical import
            self._device = MeshCoreDevice(port=port)
            self._device.on_message(self._on_receive)
            self.my_node_id = self._device.node_id
        """
        port = self._mc_cfg.get("port", "/dev/ttyUSB0")
        conn = self._mc_cfg.get("connection", "serial")

        try:
            # --- BEGIN PLACEHOLDER ---
            # When the MeshCore Python library is available, initialise
            # the device connection here.
            #
            # Example (pseudocode):
            #   if conn == "serial":
            #       self._device = meshcore.SerialDevice(port, baud=baud)
            #   elif conn == "tcp":
            #       host, _, tcp_port = port.partition(":")
            #       self._device = meshcore.TCPDevice(host, int(tcp_port))
            #
            #   self.my_node_id = self._device.get_node_id()
            #   self._device.on_text_message(self._on_receive)
            #   self._connected = True

            log.warning(
                "MeshCore adapter is a stub — install the MeshCore "
                "library and implement connect() to use a real radio"
            )
            self._connected = False
            return False
            # --- END PLACEHOLDER ---

        except Exception as e:
            log.error(f"MeshCore connection failed: {e}")
            self._connected = False
            return False

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    def _on_receive(self, sender: str, text: str, msg_id: int = 0):
        """Handle an incoming text message from the MeshCore radio.

        Wire this up as the callback in ``connect()``.  The signature
        may need adjusting to match the real MeshCore event API.
        """
        if not sender or not text:
            return

        if sender == self.my_node_id:
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

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_dm(self, dest_id: str, text: str) -> bool:
        """Send a direct message via MeshCore."""
        if not self._connected or not self._device:
            log.warning(f"can't send to {dest_id}: MeshCore not connected")
            return False

        max_bytes = self.cfg["max_response_bytes"]
        encoded = text.encode("utf-8")

        if len(encoded) <= max_bytes:
            return self._send_one(dest_id, text)

        chunks = chunk_text(text, max_bytes)
        for i, chunk in enumerate(chunks):
            if not self._send_one(dest_id, chunk):
                return False
            if i < len(chunks) - 1:
                time.sleep(3)

        return True

    def _send_one(self, dest_id: str, text: str) -> bool:
        """Send a single text message.

        TODO: Replace with real MeshCore send call, e.g.:
            self._device.send_text(dest_id, text)
        """
        try:
            # self._device.send_text(dest_id, text)  # uncomment when real
            log.info(f"  ✓ sent {len(text.encode('utf-8'))} bytes → {dest_id}")
            return True
        except Exception as e:
            log.error(f"send failed to {dest_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reconnect_loop(self):
        """Background thread: keep trying to reconnect."""
        while self._should_run:
            if not self._connected:
                log.info("attempting MeshCore reconnect...")
                self.connect()
            time.sleep(10)

    @property
    def connected(self) -> bool:
        return self._connected

    def close(self):
        self._should_run = False
        if self._device:
            try:
                # self._device.close()  # uncomment when real
                pass
            except Exception:
                pass
