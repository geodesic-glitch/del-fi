"""Simulator mesh adapter for development without hardware.

Reads from stdin, writes to stdout.  Input format:
    message text                     (uses default sender !sim00001)
    !a1b2c3d4> message text          (specify sender ID)

Output format:
    → SENDER_ID: response text
"""

import logging
import queue
import re
import threading
import time

from mesh.base import MeshAdapter

log = logging.getLogger("delfi.mesh.simulator")


class SimulatorAdapter(MeshAdapter):
    """Fake mesh interface for development without hardware.

    Works identically regardless of which ``mesh_protocol`` is
    configured — the oracle doesn't care because it only sees the
    ``MeshAdapter`` interface.
    """

    protocol_name = "Simulator"

    def __init__(self, cfg: dict, msg_queue: queue.Queue):
        super().__init__(cfg, msg_queue)
        self._rate_limits: dict[str, float] = {}
        self._connected = True
        self._should_run = True
        self._default_sender = "!sim00001"

    def connect(self) -> bool:
        """Start the stdin reader thread."""
        thread = threading.Thread(target=self._read_loop, daemon=True)
        thread.start()
        return True

    def _read_loop(self):
        """Read lines from stdin and enqueue as messages."""
        node = self.cfg["node_name"]
        print(f"\n  📻 Del-Fi Text Chat — {node} (simulator)")
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
