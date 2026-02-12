"""Abstract base class for mesh network adapters.

Any mesh protocol (Meshtastic, MeshCore, etc.) must implement this
interface so the Del-Fi oracle can send/receive messages without
caring which radio stack is underneath.
"""

import queue
from abc import ABC, abstractmethod


class MeshAdapter(ABC):
    """Contract that every mesh adapter must fulfil.

    The oracle core (router, RAG, formatter) never touches radio
    details — it just reads from ``msg_queue`` and calls ``send_dm``.

    Lifecycle
    ---------
    1. ``__init__`` — store config, create internal state
    2. ``connect``  — open the radio / start reader threads
    3. main loop    — router pulls from *msg_queue*, calls *send_dm*
    4. ``close``    — clean shutdown

    Incoming messages are placed on *msg_queue* as ``(sender_id, text)``
    tuples.  ``sender_id`` is a protocol-native string (e.g.
    ``"!a1b2c3d4"`` for Meshtastic).
    """

    def __init__(self, cfg: dict, msg_queue: queue.Queue):
        self.cfg = cfg
        self.msg_queue = msg_queue

    # --- Required methods ---

    @abstractmethod
    def connect(self) -> bool:
        """Open the connection to the mesh radio.

        Returns True on success.  Implementations should catch their
        own exceptions and return False on failure.
        """

    @abstractmethod
    def send_dm(self, dest_id: str, text: str) -> bool:
        """Send a direct (private) message to *dest_id*.

        Must handle chunking internally if the payload exceeds the
        protocol's MTU.  Returns True if the message was handed off
        to the radio successfully.
        """

    @abstractmethod
    def close(self) -> None:
        """Release radio resources.  Called once during shutdown."""

    # --- Optional overrides ---

    def reconnect_loop(self) -> None:
        """Background thread body: keep retrying ``connect``.

        The default implementation is a no-op.  Override if your
        protocol can recover from a dropped connection.
        """

    @property
    def connected(self) -> bool:
        """Whether the radio link is currently alive."""
        return False

    # --- Helpers available to all adapters ---

    @property
    def protocol_name(self) -> str:
        """Human-readable name shown in the banner and logs."""
        return self.__class__.__name__
