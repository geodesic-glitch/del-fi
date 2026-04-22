"""MeshCore mesh adapter (stub).

MeshCore (https://github.com/ripplebiz/MeshCore) is a lightweight mesh
networking protocol for LoRa radios.  This adapter provides the scaffolding;
implement the connect/_on_receive methods against the MeshCore library
to bring it online.
"""

import logging
import queue
import threading
import time

from del_fi.core.formatter import chunk_text
from del_fi.mesh.base import MeshAdapter

log = logging.getLogger("del_fi.mesh.meshcore")


class MeshCoreAdapter(MeshAdapter):
    """MeshCore radio interface (stub — connection scaffolding in place).

    Config keys (under ``meshcore:`` in config.yaml)::

        meshcore:
          port: "/dev/ttyUSB0"
          connection: "serial"
          baud_rate: 115200
    """

    protocol_name = "MeshCore"

    def __init__(self, cfg: dict, msg_queue: queue.Queue):
        super().__init__(cfg, msg_queue)
        self._mc_cfg = cfg.get("meshcore", {})
        self._connected = False
        self._should_run = True
        self._device = None
        self._lock = threading.Lock()
        self._rate_limits: dict[str, float] = {}
        self.my_node_id: str | None = None

    def connect(self) -> bool:
        """Connect to a MeshCore radio.

        TODO: Replace placeholder with real MeshCore library calls.
        """
        port = self._mc_cfg.get("port", "/dev/ttyUSB0")
        conn = self._mc_cfg.get("connection", "serial")

        try:
            log.warning(
                f"MeshCore adapter is a stub — "
                f"no real connection to {conn}:{port}"
            )
            self._connected = False
            return False
        except Exception as e:
            log.error(f"MeshCore connection failed: {e}")
            self._connected = False
            return False

    def _on_receive(self, message):
        """Handle an incoming MeshCore message.

        TODO: Parse the MeshCore message format and enqueue
        ``(sender_id, text)`` tuples onto ``self.msg_queue``.
        """

    def send_dm(self, dest_id: str, text: str) -> bool:
        """Send a direct message via MeshCore."""
        if not self._connected or not self._device:
            log.warning(f"can't send to {dest_id}: MeshCore not connected")
            return False

        max_bytes = self.cfg["max_response_bytes"]
        if len(text.encode("utf-8")) <= max_bytes:
            return self._send_one(dest_id, text)

        chunks = chunk_text(text, max_bytes)
        for i, chunk in enumerate(chunks):
            if not self._send_one(dest_id, chunk):
                return False
            if i < len(chunks) - 1:
                time.sleep(3)
        return True

    def _send_one(self, dest_id: str, text: str) -> bool:
        try:
            # TODO: self._device.send(dest_id, text)
            log.warning("MeshCore send_one is a stub — message not sent")
            return False
        except Exception as e:
            log.error(f"MeshCore send failed to {dest_id}: {e}")
            return False

    def reconnect_loop(self):
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
                self._device.close()
            except Exception:
                pass
