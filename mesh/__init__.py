"""Mesh adapter package — protocol-agnostic radio abstraction.

The Del-Fi oracle talks to *any* mesh network through the
:class:`MeshAdapter` interface.  This module's :func:`create_interface`
factory inspects the config and returns the right concrete adapter.

Supported protocols
-------------------
- **meshtastic** — Meshtastic LoRa radios (serial / TCP / BLE)
- **meshcore**   — MeshCore LoRa radios (stub — ready to implement)

Adding a new protocol
---------------------
1. Create ``mesh/<protocol>_adapter.py`` with a class that inherits
   from :class:`MeshAdapter`.
2. Register it in ``ADAPTERS`` below.
3. Add any protocol-specific config defaults in ``config.py``.
"""

import queue

from mesh.base import MeshAdapter
from mesh.meshtastic_adapter import MeshtasticAdapter
from mesh.meshcore_adapter import MeshCoreAdapter
from mesh.simulator import SimulatorAdapter

# Registry of protocol name → adapter class.
# To add a new protocol, drop a file in mesh/ and add it here.
ADAPTERS: dict[str, type[MeshAdapter]] = {
    "meshtastic": MeshtasticAdapter,
    "meshcore": MeshCoreAdapter,
}


def create_interface(
    cfg: dict, simulator: bool, msg_queue: queue.Queue
) -> MeshAdapter:
    """Factory: create the appropriate mesh adapter.

    Parameters
    ----------
    cfg : dict
        Full Del-Fi config (already validated).
    simulator : bool
        If True, return a :class:`SimulatorAdapter` regardless of
        ``mesh_protocol``.
    msg_queue : queue.Queue
        Incoming-message queue shared with the router.

    Returns
    -------
    MeshAdapter
        A concrete adapter ready for ``connect()``.
    """
    if simulator:
        iface = SimulatorAdapter(cfg, msg_queue)
        iface.connect()  # starts the stdin reader thread
        return iface

    protocol = cfg.get("mesh_protocol", "meshtastic")
    adapter_cls = ADAPTERS.get(protocol)
    if adapter_cls is None:
        raise ValueError(
            f"Unknown mesh_protocol '{protocol}'. "
            f"Available: {', '.join(sorted(ADAPTERS))}"
        )

    return adapter_cls(cfg, msg_queue)


__all__ = [
    "MeshAdapter",
    "MeshtasticAdapter",
    "MeshCoreAdapter",
    "SimulatorAdapter",
    "ADAPTERS",
    "create_interface",
]
